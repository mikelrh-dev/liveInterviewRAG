# Design: Smart Latency — Keep-Alive, Persistence & Semantic Answer Cache

## Technical Approach

Three independently revertible slices on the hot path (`main.py` STT→RAG→LLM→TTS):

1. **llm-http-pooling**: lazy thread-safe shared `httpx.Client` in `llm.py`; lifespan closes it.
2. **conversation-persistence**: new `services/persistence.py` (stdlib sqlite3, WAL, connection-per-operation) with write-through at every mutation point and hydrate-on-miss replacing the bare 404s.
3. **semantic-answer-cache**: new `services/semantic_cache.py` sitting between FAQ literal cache (`response_cache.py`) and RAG+LLM, reusing the RAG embedder; guarded by first-substantive-turn rule, TTL, FIFO cap, kill-switch.

Pipeline order after this change (both message endpoints):

```
STT ──→ FAQ literal cache ──hit──→ verbatim answer (today's contract)
              │ miss
              ▼
        semantic cache ──hit(≥thr)─→ verbatim answer + chunks_used(top_k=2), NO LLM
              │ miss (only if len(turns)==0 pre-request)
              ▼
        RAG ──→ LLM ──→ TTS ──→ append turn/message ──→ store() into semantic_cache
                                   └── write-through record_turn() → SQLite
```

## Architecture Decisions

| # | Decision | Choice | Alternatives rejected | Rationale |
|---|----------|--------|----------------------|-----------|
| 1 | Client singleton shape | Module-level `_get_client()` with `threading.Lock`, double-checked; `timeout=60.0` preserved; 4 sites (`llm.py:155,194,262,321`) drop the `with httpx.Client(...)` wrapper and use the shared client (streaming keeps its inner `client.stream(...)` CM) | Class attribute; `atexit` close | Lock-guarded lazy init is race-free for `asyncio.to_thread` callers; `atexit` double-closes under `--reload`; explicit lifespan shutdown (`main.py:174-177`, after `cleanup_task.cancel()`) calls new `llm.close_http_clients()` |
| 2 | Cap-1 test strategy | Code keeps `import httpx` untouched; tests switch to `patch("backend.services.llm._get_client")` returning a MagicMock client (no `__enter__` setup) | Keep patching `llm.httpx.Client` | Patching the factory bypasses the singleton cache entirely — otherwise the first test's mock would be frozen into the singleton and leak into later tests. **Actual churn: 6 patch sites** (`test_llm.py:35,50,64,78,88,115`) — proposal said 8; counted 6. Each edit: swap target string, delete 1–2 `__enter__/__exit__` lines. Plus 2 new tests (identity/thread-safety, timeout arg) |
| 3 | SQLite thread model | Short-lived connection per operation (`with sqlite3.connect(...) as con`), sync methods, invoked via `asyncio.to_thread`; pragmas per connection: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` | Single locked shared connection (`check_same_thread=False`) | Survives `uvicorn --reload` subprocess kills (WAL self-recovers; no stale fd holding `-wal`/`-shm`); sidesteps sqlite3 thread affinity; identical Windows/Linux semantics; open cost (µs) is noise vs LLM/TTS seconds |
| 4 | Write-through shape | One composite `record_turn(cid, turn, message)` = turn insert + message insert + `last_activity` upsert in a single transaction; called at the 3 exchange-append sites (`main.py:~526-538`, `~659-670`, `~813-832`) and farewell branch (`~618`) | Three granular calls per site | Fewer hook points, atomic, one round-trip |
| 5 | Hydrate-on-miss | New async helper `_get_conversation_or_hydrate(cid)` replacing the three `if cid not in conversations` guards (`main.py:415,563,851`): memory hit → return; else `to_thread(load_conversation)` → rebuild dict entry (turns with `chunks_used` JSON-decoded, messages, timestamps; rolling summary recomputed by replaying `update_conversation_summary` over hydrated turns) → insert into dict → return; None → caller raises 404 as today | Lazy full-table preload at startup | Startup stays fast; hydration cost paid only for resumed sessions |
| 6 | Reports table role | `reports(cid PK, path, created_at)` written through on every successful `report_service.generate` (farewell branch + eviction loop); `evict_conversation()` deletes conversations/turns/messages rows but **keeps** the reports row; `prune_reports(REPORT_RETENTION_DAYS)` runs in `periodic_cleanup` next to `report_service.cleanup_expired()` (`main.py:232`) | Store report content in DB | Files remain the report source of truth (`report.py` unchanged except none); DB row preserves cid→report linkage past eviction/restart |
| 7 | Failure policy | Every public `PersistenceService`/`SemanticAnswerCache` method: `try/except Exception` → `logger.warning`, return sentinel; **never raises into request path** (mirrors `report.py:51-53`). `initialize()` on corrupt DB (`sqlite3.DatabaseError`): rename aside `{db}.corrupt-{ts}`, recreate, log error loudly | Fail-fast; retry queues | A dead disk must degrade to today's in-memory behavior, not 500 interviews |
| 8 | Semantic cache embedder source | `RAGPipeline` gains read-only property `embedder` (returns `self._embedder`, `None` before init / when `_use_tfidf`); cache receives `embedder_provider: Callable[[], object \| None]`, wired in `main.py` as `lambda: rag_pipeline.embedder` — **no second model load** | Cache loads its own SentenceTransformer; reach into `_use_tfidf` privately | Double load costs ~1GB RAM; property keeps encapsulation; provider returning `None` is the universal "disabled" signal (covers kill-switch, TF-IDF fallback, uninitialized) |
| 9 | Similarity mechanics | Lookup embeds the **raw** question (no `expand_query` — expansion is retrieval-oriented and skews paraphrase-to-paraphrase similarity), L2-normalizes, cosine = `numpy.dot` over stacked ≤500-row matrix; hit iff max ≥ `SEMANTIC_CACHE_THRESHOLD` (0.93, env-overridable float) | Full expand_query parity with RAG | Question↔question comparison; 500×384 dot is sub-ms |
| 10 | First-substantive-turn rule | Precise definition: `len(conv.get("turns", [])) == 0` evaluated **after hydration, before generation** — turns are appended post-generation (`main.py:526,618,659,820`), so only the recruiter's opening question is ever cached/looked-up. Captured once per request as `is_first_substantive` | Turn-count>0 exclusion; per-question hashing | Cheapest correct guard against poisoning multi-turn context-dependent answers |

## Data Flow — write-through & hydration

```
POST /message(+stream)
  │ _get_conversation_or_hydrate(cid)          ← reads DB on memory miss
  ▼
FAQ? ──no──► semantic.lookup(q)? ──no──► RAG+LLM
  │yes           │yes                      │ success & first substantive turn
  ▼              ▼                         ▼
record_turn ◄── record_turn            record_turn + semantic.store(q, answer)

periodic_cleanup: evict_conversation(cid) [keeps reports row]
                  · prune_reports(REPORT_RETENTION_DAYS)
                  · semantic cache TTL sweep
```

## File Changes

| File | Action | Description | Est. Lines |
|------|--------|-------------|-----------|
| `backend/services/persistence.py` | Create | `PersistenceService(db_path)`: DDL (conversations, turns, messages, reports, semantic_cache), WAL/FK/busy_timeout pragmas, corrupt-rename recovery, `record_turn`, `load_conversation`, `evict_conversation`, `record_report`, `prune_reports`, `_enabled` gate | ~175 |
| `backend/services/semantic_cache.py` | Create | `SemanticAnswerCache(db_path, embedder_provider)`: `lookup`, `store`, TTL sweep, FIFO cap, guard chain | ~135 |
| `backend/services/llm.py` | Modify | `_get_client()` + `close_http_clients()`; unwrap 4 `with` blocks | ~35 |
| `backend/main.py` | Modify | Global `persistence` + `semantic_cache`; lifespan init/close; `_get_conversation_or_hydrate`; 4×`record_turn`; 2×`record_report`; eviction + prune hooks; semantic-cache branches in both endpoints | ~75 |
| `backend/config.py` | Modify | `_env_bool`; `PERSISTENCE_ENABLED`, `DB_PATH`, `SEMANTIC_CACHE_ENABLED/TTL_DAYS/MAX_ROWS/THRESHOLD`. **`REPORT_RETENTION_DAYS` already exists (line 67)** — no edit | ~15 |
| `backend/services/rag.py` | Modify | `embedder` property | ~4 |
| `.env.example` | Modify | New vars + document `REPORT_RETENTION_DAYS` | ~10 |
| `tests/test_llm.py` | Modify | 6 patch-site retargets + 2 singleton tests | ~±20 |
| `tests/test_persistence.py` | Create | See testing strategy | ~230 |
| `tests/test_semantic_cache.py` | Create | See testing strategy | ~240 |
| **TOTAL** | | | **≈ 940** |

> **400-line review budget risk: HIGH** (~940 incl. tests, ~420 excl.). Plain-text guard record: `Decision needed before apply: Yes` · `Chained PRs recommended: Yes` · `400-line budget risk: High`.

## Interfaces / Contracts

```python
class PersistenceService:
    def __init__(self, db_path: Path, *, enabled: bool = True) -> None: ...
    def initialize(self) -> None                       # mkdir + DDL; never raises
    def record_turn(self, cid: str, turn: dict, message: dict) -> None
    def load_conversation(self, cid: str) -> dict | None   # hydrated dict or None
    def evict_conversation(self, cid: str) -> None         # keeps reports row
    def record_report(self, cid: str, path: str) -> None   # upsert
    def prune_reports(self, days: int) -> int

class SemanticAnswerCache:
    def __init__(self, db_path: Path, embedder_provider: Callable[[], object | None],
                 *, enabled: bool, ttl_days: int, max_rows: int, threshold: float)
    def lookup(self, question: str) -> str | None
    def store(self, question: str, answer: str) -> None
```

Schema: `conversations(id PK, summary, created_at, last_activity_at)` · `turns(id PK, conversation_id FK CASCADE, n, user_text, assistant_text, chunks_used TEXT/*JSON*/, UNIQUE(cid,n))` · `messages(id PK, conversation_id FK CASCADE, user_text, response_text, audio_url)` · `reports(conversation_id PK, path, created_at)` · `semantic_cache(id PK, question, embedding BLOB f32[384], answer, hit_count, created_at)`.

Semantic-hit contract mirrors FAQ hits exactly: non-stream → `response_text` + `chunks_for_turn = get_chunks_with_scores(user_text, top_k=2)` (`main.py:519` pattern); stream → single verbatim `token` event, single-file TTS, `audio_url`+`done` (`main.py:631-676` pattern). No LLM call.

## Testing Strategy (strict TDD per config.testing.strict_tdd)

| Layer | Suite | Cases |
|-------|-------|-------|
| Unit | `test_persistence.py` (~10) | schema init; WAL pragma set; record/load roundtrip incl. summary replay + chunks JSON; evict keeps reports row; record/prune reports retention; failure-tolerance (patched execute raises → sentinel); corrupt-DB rename+recreate; disabled flag no-ops |
| Unit | `test_semantic_cache.py` (~10) | store/lookup roundtrip; miss at 0.92-similarity vectors; hit at ≥threshold; TTL expiry ignored; FIFO cap trims oldest; kill-switch off → None; embedder `None` (tfidf) → disabled; hit_count increment |
| Integration | `test_api.py`-style `mock_services` fixtures (`test_api.py:13-64` pattern) + `patch("backend.main.semantic_cache")` | first-turn-only guard (second turn never caches); stream hit yields verbatim token + `llm_service.generate_stream*` NOT called; miss stores after generation; hydrate-on-miss accepts message for unknown-but-persisted cid |
| Unit | `test_llm.py` | 6 retargeted mocks + singleton identity/timeout tests |

Run: `python -m pytest tests/ -v`.

## Migration / Rollout

No data migration (fresh DB; `*.db` already gitignored, `.gitignore:59`). Four independent revert units: ① cap1 (llm.py+test_llm+lifespan close) ② cap2 core (persistence.py+config+.env.example) ③ cap2 wiring (main.py) ④ cap3 (semantic_cache+hooks+tests). Runtime escape hatches: `PERSISTENCE_ENABLED=false`, `SEMANTIC_CACHE_ENABLED=false`; deleting `data/interviewtts.db` resets state.

## Open Questions

None blocking. Noted deviations: proposal's "8 mock sites" is actually **6**; `REPORT_RETENTION_DAYS` needed **no addition** (already `config.py:67`); total estimate ~940 exceeds the brief's ~600–750 due to composite `record_turn`, hydration helper, and fuller test suites — flagged above for the orchestrator guard record.
