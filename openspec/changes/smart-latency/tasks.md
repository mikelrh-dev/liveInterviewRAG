# Tasks: Smart Latency — Keep-Alive, Persistence & Semantic Answer Cache

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~940 incl. tests (~420 excl.) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | Single change, 4 commit units = design rollback ①②③④ |
| Delivery strategy | exception-ok |
| Chain strategy | size-exception |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: size-exception
400-line budget risk: High
```

> Orchestrator override recorded: total ~940 incl. tests ACCEPTED as a single change delivered in **4 commit units** matching design's rollback plan. Each unit below is one coherent commit with its own RED tests first.

### Work Units (= commit units)

| Unit | Goal | Commit | Depends on |
|------|------|--------|------------|
| 1 | Cap-1: HTTP keep-alive singleton + lifespan close + mock retarget | ① cap1 | none |
| 2 | Cap-2 core: PersistenceService + config + hydration helper + unit tests | ② cap2-core | Unit 1 |
| 3 | Cap-2 wiring: write-through at all mutation points + integration tests | ③ cap2-wiring | Unit 2 |
| 4 | Cap-3: SemanticAnswerCache + guardrails + endpoint slotting + regression | ④ cap3 | Unit 2 (DB), independent of Unit 3 |

## Unit 1: Cap-1 Keep-Alive (RED → GREEN)

- [x] 1.1 **RED** `tests/test_llm.py`: add `test_get_client_returns_same_instance` (factory identity across calls — spec "Consecutive calls reuse one client"), `test_get_client_thread_safe_concurrent_identity`, `test_generate_uses_shared_client_timeout_60`, `test_close_http_clients_closes_once_and_safe_uninitialized` (spec "Shutdown Closing": idempotent, no-op when never created). Run: all FAIL (`_get_client` missing).
- [x] 1.2 **GREEN** `backend/services/llm.py` (~35 lines): module-level `_client=None` + `_client_lock=threading.Lock()`; `_get_client()` double-checked lazy init `httpx.Client(timeout=60.0)`; unwrap the 4 `with httpx.Client(timeout=60.0)` blocks at `llm.py:155,194,262,321` to use the shared client (streaming keeps inner `client.stream(...)` CM); add idempotent `close_http_clients()`. Keep `import httpx` untouched (design D2). Test: 1.1 cases PASS.
- [x] 1.3 `backend/main.py` (~3 lines): lifespan shutdown after `cleanup_task.cancel()` (`main.py:175`) call `llm.close_http_clients()`. Test: spec scenario "Lifespan shutdown closes client once" via 1.1 close test.
- [x] 1.4 Retarget 6 mock sites `tests/test_llm.py:35,50,64,78,88,115`: swap `patch("backend.services.llm.httpx.Client")` → `patch("backend.services.llm._get_client")` returning a plain MagicMock client; delete each site's `__enter__/__exit__` setup lines. Anti-leak rationale (design D2): patching the factory bypasses the singleton cache so the first test's mock cannot freeze into the singleton and leak into later tests.
- [x] 1.5 Verify Unit 1: `venv\Scripts\python.exe -m pytest tests/test_llm.py tests/test_api.py -q` green; error-parity scenarios (429 rate-limit text, timeout/unavailable text) still covered by retargeted existing cases.

## Unit 2: Cap-2 Persistence Core (RED → GREEN)

- [x] 2.1 **RED** create `tests/test_persistence.py` (~230 lines): `test_initialize_creates_dir_and_schema_tables`, `test_wal_fk_busy_timeout_pragmas_active`, `test_record_turn_load_roundtrip_with_summary_replay_and_chunks_json`, `test_load_unknown_cid_returns_none`, `test_evict_deletes_rows_but_keeps_report_row`, `test_record_report_upsert_and_prune_reports_retention`, `test_execute_failure_logs_warning_returns_sentinel`, `test_corrupt_db_renamed_aside_and_recreated`, `test_disabled_flag_no_ops_all_methods`, `test_hydrate_on_miss_returns_persisted_conversation`. Use `tmp_path` DBs. Run: all FAIL.
- [x] 2.2 **GREEN** create `backend/services/persistence.py` (~175 lines) per design contracts: `PersistenceService(db_path, *, enabled=True)`; `initialize()` mkdir + DDL for `conversations/turns/messages/reports/semantic_cache`; connection-per-operation via `asyncio.to_thread` callers, pragmas per connection `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` (design D3); corrupt DB (`sqlite3.DatabaseError`) → rename `{db}.corrupt-{ts}` + recreate + loud log (D7). Tests 2.1 schema/pragma/corrupt PASS.
- [x] 2.3 `record_turn(cid, turn, message)` composite single transaction: turn insert (chunks_used JSON-encoded, UNIQUE(cid,n)) + message insert + `last_activity_at` upsert (D4). Test: roundtrip case.
- [x] 2.4 `load_conversation(cid)` → hydrated dict or None: turns with `chunks_used` JSON-decoded, messages, timestamps; rolling summary recomputed by replaying `update_conversation_summary` logic over hydrated turns (D5). Test: roundtrip + unknown-cid cases.
- [x] 2.5 `evict_conversation(cid)` deletes conversations/turns/messages rows but KEEPS reports row; `record_report(cid, path)` upsert; `prune_reports(days) -> int`. Test: evict-keeps-reports + record/prune retention cases (spec "Retention and Report Survival").
- [x] 2.6 Failure policy (D7): every public method `try/except Exception` → `logger.warning` → sentinel return, never raises into request path (mirrors `report.py:51-53`); `enabled=False` gates every method to no-op. Tests: failure-tolerance + disabled-flag cases (spec "Failure Isolation").
- [x] 2.7 Hydration wiring in `backend/main.py` (~25 lines): global `persistence = PersistenceService(...)` from config; lifespan startup calls `await asyncio.to_thread(persistence.initialize)`; new async `_get_conversation_or_hydrate(cid)` — memory hit returns dict; miss → `to_thread(load_conversation)` → rebuild entry → insert into `conversations{}` → return; None → caller raises 404 as today (D5). Replace the three guards at `main.py:415,563,851` (message / stream / context endpoints). Test: `test_hydrate_on_miss_returns_persisted_conversation`.
- [x] 2.8 `backend/config.py` (~8 lines): add `_env_bool` helper; `PERSISTENCE_ENABLED` (default true), `DB_PATH` (default `data/interviewtts.db`). NOTE: `REPORT_RETENTION_DAYS` already exists at `config.py:67` — NO code edit. `.env.example` (~10 lines): document both vars + document existing `REPORT_RETENTION_DAYS`.
- [x] 2.9 Verify Unit 2: `venv\Scripts\python.exe -m pytest tests/test_persistence.py tests/ -q` green (existing suite unchanged — guard replacement is behavior-neutral pre-write-through).

## Unit 3: Cap-2 Wiring into main.py (RED → GREEN)

- [x] 3.1 **RED** extend `tests/test_api.py` (new class using existing `mock_services` fixture pattern `test_api.py:13-64` + `tmp_path` DB patched over `backend.main.persistence`): `test_create_conversation_persists_row` (spec "Conversation creation persists"), `test_message_appends_persist_turn_and_message_rows` (spec "Message append persists"), `test_restart_survival_same_id_continues_after_dict_eviction` (create conv → simulate restart by popping `conversations[cid]` → POST message to same id → 200 with hydrated prior context — spec "Restart mid-interview continues seamlessly"), `test_db_error_does_not_break_stream_answer` (patch `sqlite3.connect` to raise inside service → SSE still delivers full answer — spec "Database error does not fail the SSE turn"). Run: FAIL.
- [x] 3.2 **GREEN** write-through at ALL turn-append sites via `to_thread(persistence.record_turn, ...)`: non-stream endpoint after appends `main.py:~526-538`; stream endpoint branch A `~659-670`; stream endpoint branch B `~813-832`; farewell branch `~618`. Tests 3.1 append-persistence PASS.
- [x] 3.3 Report write-through + eviction rewiring in `backend/main.py`: `record_report(cid, path)` on farewell report generate `main.py:~625` AND inside eviction loop before delete (`:211-214`, order matters: report row written BEFORE `evict_conversation` which preserves it); eviction loop calls `to_thread(persistence.evict_conversation, cid)` after `del conversations[cid]`; `prune_reports(config.REPORT_RETENTION_DAYS)` next to `report_service.cleanup_expired()` in `periodic_cleanup` (`main.py:232`). Tests: 3.1 + Unit 2 `evict_keeps_report` scenario end-to-end (spec "Reports survive eviction", "Evicted conversation rows removed").
- [x] 3.4 Verify Unit 3: full `venv\Scripts\python.exe -m pytest tests/ -q` green including 4 new integration cases.

## Unit 4: Cap-3 Semantic Answer Cache (RED → GREEN)

- [x] 4.1 **RED** create `tests/test_semantic_cache.py` (~240 lines) with fake embedder (numpy stub, no model load): `test_store_lookup_roundtrip_hit_count_zero`, `test_hit_at_or_above_threshold_served`, `test_miss_below_threshold_returns_none` (0.92-similarity vectors), `test_ttl_expired_row_never_served`, `test_fifo_cap_500_trims_oldest`, `test_kill_switch_disables_lookup_and_store`, `test_embedder_none_tfidf_disables_cache`, `test_hit_count_increments_on_hit`, `test_model_dimension_mismatch_rows_ignored`, `test_failure_returns_sentinel_logged`. Run: FAIL.
- [x] 4.2 **GREEN** create `backend/services/semantic_cache.py` (~135 lines): `SemanticAnswerCache(db_path, embedder_provider, *, enabled, ttl_days, max_rows, threshold)`; `lookup(question)` embeds RAW question (no `expand_query`, D9), L2-normalizes, cosine = numpy dot over stacked ≤500-row matrix, hit iff ≥ threshold; `store(question, answer)` writes f32[384] blob + TTL sweep + FIFO trim; provider returning None = universal disabled signal (D8); try/except log-and-continue. Tests 4.1 PASS (spec "Cache Store", "Mandatory Guardrails", "Embedding Stability Guard").
- [x] 4.3 `backend/services/rag.py` (~4 lines): read-only property `embedder` returning `self._embedder`, `None` before init or when `_use_tfidf` (D8). Micro-test in `tests/test_rag.py`: property None pre-init / tfidf mode.
- [x] 4.4 `backend/config.py` (~7 lines): `SEMANTIC_CACHE_ENABLED=true`, `SEMANTIC_CACHE_TTL_DAYS=14`, `SEMANTIC_CACHE_MAX_ROWS=500`, `SEMANTIC_CACHE_THRESHOLD=0.93` (+ `_env_float` if absent); `.env.example`: document all four.
- [x] 4.5 **RED** integration cases in `tests/test_api.py` (`mock_services` + `patch("backend.main.semantic_cache")`, real tmp-path cache where needed): `test_first_turn_cached_second_similar_turn_bypasses_to_llm` (spec "Follow-up turn never hits cache"), `test_stream_hit_emits_single_verbatim_token_without_llm` (`llm_service.generate_stream*` NOT called), `test_nonstream_hit_tracks_chunks_for_context_panel` (chunks_used present, LLM not called), `test_miss_stores_after_successful_generation` (spec "First-turn answer is cached"). Run: FAIL.
- [x] 4.6 **GREEN** wire into BOTH endpoints (`main.py` ~40 lines): global `semantic_cache` + lifespan init wired with `embedder_provider=lambda: rag_pipeline.embedder`; slot lookup AFTER FAQ literal-cache check and BEFORE RAG, only when `is_first_substantive` = `len(conv["turns"]) == 0` evaluated post-hydration pre-generation (D10); hit contract mirrors FAQ hits exactly — non-stream: verbatim `response_text` + `chunks_for_turn = get_chunks_with_scores(user_text, top_k=2)` (`main.py:519` pattern); stream: single verbatim `token` event + single-file TTS + `audio_url`+`done` (`main.py:631-676` pattern); store-after-success only on first substantive turns; semantic TTL sweep added to `periodic_cleanup`. Tests 4.5 PASS.
- [x] 4.7 **Final gate**: full suite `venv\Scripts\python.exe -m pytest tests/ -q` green AND grep gate: zero remaining `with httpx.Client(` occurrences in `backend/services/llm.py`.

## Verification Map

| Task(s) | Pytest case / scenario | Spec scenario |
|---------|------------------------|---------------|
| 1.1–1.4 | `test_get_client_*`, `test_generate_uses_shared_client_timeout_60`, `test_close_http_clients_*` | Reuse one client · Timeouts unchanged · Shutdown closes once |
| 2.1–2.6 | `tests/test_persistence.py` 10 cases | Write-Through · Crash Safety · Retention · Failure Isolation · Embedding n/a |
| 2.7 | `test_hydrate_on_miss_returns_persisted_conversation` | Load-on-Demand Hydration |
| 3.1–3.3 | `TestPersistence*` 4 integration cases in `tests/test_api.py` | Creation persists · Append persists · Restart continues · SSE survives DB error · Reports survive eviction |
| 4.1–4.6 | `tests/test_semantic_cache.py` 10 cases + 4 API integration cases | Store · Lookup before LLM · Guardrails · TF-IDF disable · Model-mismatch ignore |
| 4.7 | Full suite green + grep gate | Success criteria (proposal) |

## Rollback

Four independent revert points = the 4 commit units above (design Migration/Rollback ①–④): revert Unit 4 commit kills Cap-3; revert Unit 3 restores in-memory-only mutations; revert Unit 2 removes persistence entirely; revert Unit 1 restores per-call clients. Runtime escape hatches without redeploy of code paths: `PERSISTENCE_ENABLED=false` (Cap-2 no-ops, in-memory dict remains source of truth), `SEMANTIC_CACHE_ENABLED=false` (Cap-3 zero overhead); deleting `data/interviewtts.db` resets persisted state (`*.db` gitignored). No migrations, no API-contract changes — rollback affects no consumers.
