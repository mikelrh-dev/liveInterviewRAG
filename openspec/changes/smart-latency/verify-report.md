# Verify Report — smart-latency

**Change**: smart-latency
**Mode**: Standard (strict TDD was used during apply; verify executed as full-suite + source-inspection quality gate)
**Date**: 2026-08-26
**Executor**: sdd-verify

---

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 25 |
| Tasks complete | 25 |
| Tasks incomplete | 0 |

tasks.md marks audited directly: Unit 1 = 5 (`1.1–1.5`), Unit 2 = 9 (`2.1–2.9`), Unit 3 = 4 (`3.1–3.4`), Unit 4 = 7 (`4.1–4.7`) — all `[x]`, consistent 25/25.

## Build & Tests Execution

**Build**: ➖ Not applicable (pure Python service; import success proven by suite collection)

**Tests**: ✅ **256 passed / 0 failed** / 0 skipped
```text
$ venv\Scripts\python.exe -m pytest tests/ -q
........................................ [100%]
256 passed, 1 warning in 127.93s (0:02:07)
```
Warning is pre-existing (starlette TestClient httpx deprecation), unrelated to this change.

Math check: baseline 221 → +35 new tests = 256. New: `test_persistence.py` 10, `test_semantic_cache.py` 10, `test_llm.py` +4 singleton, `test_api.py` +8 integration (4 persistence + 4 semantic), `test_rag.py` +3 embedder-property. Consistent with apply-progress record.

**Coverage**: ➖ Not available (no coverage tooling configured)

## Grep Gates

| Gate | Result |
|------|--------|
| `with httpx.Client(` in `backend/**` | **ZERO occurrences** ✅ |
| Only `httpx.Client(` construction | `backend/services/llm.py:32` (inside `_get_client()` factory) ✅ |
| f-string / concatenated SQL in services | **NONE** — all 24 SQL sites parameterized with `?` placeholders ✅ |
| Frontend diff in change | **ZERO frontend files** in `git status` — changeset touches only backend/, tests/, .env.example, openspec/ ✅ |
| `*.db` gitignored | `.gitignore:59` ✅ |

## Spec Compliance Matrix

### llm-http-pooling

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Shared Client Reuse | Consecutive calls reuse one client | `test_llm.py > test_get_client_returns_same_instance` + `test_get_client_thread_safe_concurrent_identity` (16 calls / 8 threads → 1 instance) | ✅ COMPLIANT |
| Shared Client Reuse | Timeouts unchanged | `test_llm.py > test_generate_uses_shared_client_timeout_60` (`assert_called_once_with(timeout=60.0)`) | ✅ COMPLIANT |
| Shutdown Closing | Lifespan closes client once | `test_llm.py > test_close_http_clients_closes_once_and_safe_uninitialized`; wiring at `main.py:208` after `cleanup_task.cancel()` | ✅ COMPLIANT |
| Shutdown Closing | In-flight call survives shutdown start | (none direct) | ⚠️ PARTIAL — see W-3 |
| Error Behavior Parity | Rate-limit message unchanged | `test_generate_rate_limit`, `test_generate_auth_error` (retargeted mocks, passing) | ✅ COMPLIANT |
| Error Behavior Parity | Timeout/unavailable message unchanged | `test_generate_timeout`, `test_generate_server_error` | ✅ COMPLIANT |

### conversation-persistence

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Write-Through | Conversation creation persists | `test_api.py > test_create_conversation_persists_row` (row readable immediately after POST) | ✅ COMPLIANT |
| Write-Through | Message append persists | `test_api.py > test_message_appends_persist_turn_and_message_rows` | ✅ COMPLIANT |
| Load-on-Demand Hydration | Restart mid-interview continues seamlessly | `test_api.py > test_restart_survival_same_id_continues_after_dict_eviction` + `test_persistence.py > test_hydrate_on_miss_returns_persisted_conversation` | ✅ COMPLIANT |
| Crash Safety | Hard kill keeps DB readable | WAL pragma active (`test_wal_fk_busy_timeout_pragmas_active`: wal/fk=1/busy=5000) + corrupt-rename recovery tested | ⚠️ PARTIAL — see W-2 |
| Retention & Report Survival | Report retrievable after eviction | `test_evict_deletes_rows_but_keeps_report_row` + `test_record_report_upsert_and_prune_reports_retention` + `test_report_service.py > TestEvictionOrderingHook.test_generate_called_before_delete` | ✅ COMPLIANT |
| Retention & Report Survival | Evicted conversation rows removed | Service level: cascade delete asserted via raw counts; loop level: `test_conversation_eviction` runs real `periodic_cleanup` | ✅ COMPLIANT (composed coverage — see S-1) |
| Failure Isolation | DB error does not fail the SSE turn | `test_api.py > test_db_error_does_not_break_stream_answer` (`_connect` patched to raise → full token text delivered, `done` emitted, zero error events, warning logged) | ✅ COMPLIANT |

### semantic-answer-cache

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Cache Store | First-turn answer is cached | `test_api.py > test_miss_stores_after_successful_generation` (both endpoints) + `test_store_lookup_roundtrip_hit_count_zero` (hit_count=0 verified in DB) | ✅ COMPLIANT |
| Similarity Lookup Before LLM | Paraphrased repeat served without LLM | Functional: `test_nonstream_hit_tracks_chunks_for_context_panel`, `test_stream_hit_emits_single_verbatim_token_without_llm`, threshold boundary tests (0.95 hit / 0.92 miss) | ⚠️ PARTIAL — functional clauses pass; `<300ms` wall-clock clause unmeasurable in suite (see W-4) |
| Mandatory Guardrails | Follow-up turn never hits cache | `test_first_turn_cached_second_similar_turn_bypasses_to_llm` (`lookup.call_count == 1` — second turn never consults) | ✅ COMPLIANT |
| Mandatory Guardrails | Kill-switch disables entirely | `test_kill_switch_disables_lookup_and_stool→store` (spy provider proves embedder provider NEVER consulted → true zero overhead) | ✅ COMPLIANT |
| Mandatory Guardrails | Expired rows unserved + FIFO cap | `test_ttl_expired_row_never_served` + `test_fifo_cap_500_trims_oldest` (505 inserted → exactly 500 newest kept, oldest 5 gone) | ✅ COMPLIANT |
| Embedding Stability Guard | TF-IDF fallback disables cache | `test_embedder_none_tfidf_disables_cache` + `rag.py:158–167` property returns `None` when `_use_tfidf` or uninitialized (`TestEmbedderProperty`, 3 cases) | ✅ COMPLIANT |
| Embedding Stability Guard | Model-mismatch rows ignored | `test_model_dimension_mismatch_rows_ignored` (8-dim rows invisible to 4-dim embedder) | ✅ COMPLIANT |

### conversation-engine (MODIFIED deltas)

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Conversation State | Persisted history survives restart | `test_restart_survival_same_id_continues_after_dict_eviction`; API contracts unchanged (same endpoints/shapes — hydration is transparent) | ✅ COMPLIANT |
| Conversation State | Multi-turn with history / stateless | Pre-existing `test_conversation_memory.py` suite (passing) | ✅ COMPLIANT |
| Session TTL Eviction | Conversation evicted after TTL expiry | `test_conversation_memory.py > test_conversation_eviction` (memory) + `test_evict_deletes_rows_but_keeps_report_row` (DB rows); silent DEBUG-only at `main.py:240` | ✅ COMPLIANT |
| Session TTL Eviction | TTL floor enforced | `test_config.py > test_session_ttl_floor_enforced` (0.05 → warns, defaults 2) | ✅ COMPLIANT |
| Session TTL Eviction | Reports survive eviction | `test_evict_deletes_rows_but_keeps_report_row` (`reports == 1` after evict) | ✅ COMPLIANT |

**Compliance summary**: 21/24 scenarios fully compliant · 3 partial (environment-bound clauses) · 0 failing · 0 untested

## Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| Singleton client, 4 call sites | ✅ Implemented | `llm.py:189,228,296,355` all use `_get_client()`; streaming keeps inner `client.stream()` CM (:229, :360) |
| Double-checked lock correct | ✅ Verified | Outside-lock read + inside-lock re-check (`llm.py:29–33`); safe under CPython GIL (construction completes before publication). Close path takes the same lock and resets to `None` so a closed client is never handed back (:43–46) |
| Schema + pragmas | ✅ Implemented | `persistence.py:23–59` DDL matches design schema; WAL/FK/busy_timeout per connection (:108–110); pragma failure closes raw con first — Windows handle-leak fix enabling corrupt-rename |
| Connection-per-op thread safety | ✅ Verified | Every method opens/closes its own connection within one `to_thread` worker; no shared connection object; only shared state is the benign `_schema_ready` flag (idempotent `CREATE IF NOT EXISTS`) |
| Write-through completeness | ✅ Implemented | create `main.py:486`; non-stream append :648; stream LLM append :987; farewell :793; cache-hit path via shared `emit_cached_answer` closure :760 — ALL mutation points wired |
| Farewell + report caller | ✅ Implemented | `record_report` at farewell :806–809 and in eviction loop :246–248 (BEFORE `evict_conversation` :254 — order preserved per design D6); `prune_reports` next to `cleanup_expired` :278 |
| Hydration replaces 404s | ✅ Implemented | `_get_conversation_or_hydrate` (`main.py:381–403`) replaces the 3 former guards: message :500, stream :679, context :1011; unknown-and-unpersisted still 404s |
| Summary replay fidelity | ✅ Verified byte-identical | `persistence._replay_summary` mirrors `main.update_conversation_summary` algorithm exactly (80/120-char briefs, 1500-cap oldest-line drop, same header string); **runtime proof**: `test_record_turn_load_roundtrip…` replays live updater over same turns and asserts equality (`test_persistence.py:131–148`) |
| Semantic slots (BOTH endpoints) | ✅ Implemented | Non-stream: FAQ :544 → semantic :554–555 → RAG :563. Stream: FAQ :813 → semantic :822–824 → RAG :831. Hit contract via `emit_cached_answer`: single verbatim token, single-file TTS, chunks top_k=2, audio_url+done — zero LLM |
| Threshold/TTL/FIFO/kill-switch/tfidf in CODE | ✅ Implemented | threshold compare `semantic_cache.py:153`; TTL filter in lookup SQL :122–124 + sweeps :191–194/:209; FIFO `ORDER BY id DESC LIMIT ?` :195–203; enabled gate FIRST in lookup/store :106/:163 before any work; tfidf/uninit → provider None → disabled :110/:167 |
| Store only on success | ✅ Implemented | Non-stream: store at :652 reached only after TTS success (TTS failure raises 503 at :601); stream: store at :991 reached only after clean generation loop; hit branches return early without storing |
| PERSISTENCE_ENABLED escape hatch | ✅ Functional | `config.py:80` default true; gates every public method (`test_disabled_flag_no_ops_all_methods` proves no disk touch) |
| Config surface | ✅ Implemented | `config.py:80–91`: PERSISTENCE_ENABLED, DB_PATH, SEMANTIC_CACHE_ENABLED/TTL_DAYS(14)/MAX_ROWS(500)/THRESHOLD(0.93); `.env.example` documents all + REPORT_RETENTION_DAYS |
| Security sanity | ✅ Clean | All SQL parameterized (24/24); DB at `BASE_DIR/data/` — app mounts only `/audio` and `/` (frontend); `data/` never web-servable; `*.db` gitignored |

## Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1 Client singleton shape | ✅ Yes | Exact implementation incl. lifespan close placement |
| D2 Mock strategy (patch factory) | ✅ Yes | 6 retargets at `test_llm.py:44,59,73,87,97,121` (design corrected proposal's "8") |
| D3 Connection-per-op + pragmas | ✅ Yes | Plus the Windows pragma-failure handle fix (documented deviation, improves corrupt-rename) |
| D4 Composite record_turn | ✅ Yes | Single transaction: conversation upsert + turn + message |
| D5 Hydrate-on-miss helper | ✅ Yes | 3 guards replaced; summary replayed |
| D6 Reports table role | ✅ Yes | Row written BEFORE evict; prune wired into cleanup |
| D7 Log-and-continue failure policy | ✅ Yes | Every public method try/except → warning → sentinel; corrupt DB quarantined `{db}.corrupt-{ts}` |
| D8 Embedder provider, no 2nd model | ✅ Yes | `rag.embedder` property + `lambda: rag_pipeline.embedder` at `main.py:133` |
| D9 Raw-question embedding, numpy dot | ✅ Yes | No expand_query; stacked matrix dot product |
| D10 First-substantive-turn rule | ✅ Yes | Post-hydration pre-generation capture in both endpoints (:504, :714); turns appended post-generation so only opening question caches |

**Documented deviation from interface list**: `PersistenceService.record_conversation()` added beyond design's method list — required by spec scenario "Conversation creation persists"; recorded in apply-progress and coherent with specs. Accepted.

## Issues Found

**CRITICAL**: None.

**WARNING**:
- **W-1 — Stale DB-only rows are never swept after a restart.** `periodic_cleanup` iterates only in-memory `conversations.items()` (`main.py:234–238`). A persisted conversation abandoned across a restart keeps its conversation/turn/message rows forever — only reports have a prune job. No written SHALL is violated (eviction is defined over in-memory entries, and hydrated stale sessions DO get evicted correctly), but row growth is unbounded on the long-lived VPS. Recommend a future task: DB-side sweep of `conversations.last_activity_at < cutoff` in `periodic_cleanup`.
- **W-2 — Crash Safety scenario partially evidenced.** No literal `kill -9` drill exists (hard to automate portably). Coverage is indirect: WAL pragma verified active at runtime + corrupt-file quarantine/rebuild tested. SQLite's WAL recovery guarantee applies once `journal_mode=WAL` holds, so risk is low; noting per evidence discipline.
- **W-3 — "In-flight call survives shutdown start" has no runtime test.** Structurally satisfied: every LLM call site wraps failures into `RuntimeError` → surfaced as SSE error event (graceful termination, no unhandled exception), and close idempotency/no-op is tested. Mid-shutdown stream race itself is untested.
- **W-4 — Sub-300ms latency clause untested.** The "Paraphrased repeat served without LLM" scenario's functional clauses (verbatim answer, zero LLM, chunks tracked) all pass; the wall-clock bound requires real embedder+TTS timing and belongs in a manual/staging acceptance check (also listed as proposal Success Criterion #1).

**SUGGESTION**:
- **S-1 — Eviction-loop tests use the global persistence instance** (`test_conversation_memory.py:334`, `test_report_service.py:245` run against the real `data/interviewtts.db` instead of a tmp_path store like `TestPersistenceIntegration` does). Harmless (gitignored, no assertions read it) but patching would make them hermetic. Composed coverage note: loop-level tests assert memory eviction; DB-row deletion is asserted at service level; the wiring lines are statically verified.
- **S-2 — `datetime.utcnow()` is deprecated (Python ≥3.12)** across both new services and main.py. Cosmetic today; consider timezone-aware `datetime.now(timezone.utc)` in a cleanup pass.

## Verdict

**PASS WITH WARNINGS**

Full suite green (256/0), 25/25 tasks complete, 21/24 spec scenarios fully compliant with runtime evidence and 3 partials that are environment-bound (kill-drill, mid-shutdown race, wall-clock latency) rather than functional gaps. All four capabilities implemented per design D1–D10; API contracts unchanged; frontend untouched; grep and parameterization gates clean. The single substantive operational finding (W-1, stale DB-only rows) warrants a small follow-up task but violates no written requirement.

### Recommended next step
`sdd-archive` (sync deltas into main specs). Optionally log W-1 as a follow-up change proposal first.
