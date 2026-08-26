# Proposal: Smart Latency — Keep-Alive, Persistence & Semantic Answer Cache

## Intent

Three compounding latency/durability problems on the hot path:

1. **Connection churn** — `backend/services/llm.py` creates a fresh `httpx.Client()` at every call (OpenRouter generate/stream, Google AI generate/stream): new TCP+TLS handshake (~200–500ms) per LLM call.
2. **Volatile state** — conversations live only in the in-memory `conversations{}` dict (`backend/main.py`). A restart erases interview history and kills mid-interview sessions; reports survive as files but are detached from retrievable history.
3. **Repeated full-cost answers** — the 20-entry literal FAQ cache matches near-exact phrasing only; paraphrased repeats pay full STT→RAG→LLM cost (4–8s).

Target: single-worker Oracle ARM64 VPS. SQLite (stdlib, WAL, zero deps) is deliberate — no Postgres/Redis.

## Scope

### In Scope

- **Cap 1 — HTTP keep-alive**: shared module-level lazy thread-safe `httpx.Client` reused by all 4 call sites; same timeouts; closed on app shutdown (lifespan/atexit).
- **Cap 2 — SQLite persistence** (`data/interviewtts.db`, dir auto-created; `*.db` already gitignored): write-through on conversation create / turn append / message append / report generate; load-on-demand hydration when an unknown `conversation_id` arrives; TTL eviction deletes DB rows EXCEPT reports (persist; pruned via existing `REPORT_RETENTION_DAYS`, default 30). Schema: `conversations`, `turns`, `messages`, `reports`.
- **Cap 3 — Semantic answer cache** (builds ON Cap 2's DB): post-generation store `(question_text, question_embedding f32[384], answer_text, created_at, hit_count)` into `semantic_cache`; pre-LLM lookup embeds the question (embedder already loaded in RAG pipeline), max cosine similarity ≥ 0.93 → cached answer with same contract as FAQ hits (incl. context-panel chunk tracking).
- Safety rules (spec-binding): lookup/store ONLY on first substantive turn · TTL `SEMANTIC_CACHE_TTL_DAYS=14` · 500-row FIFO cap · kill-switch `SEMANTIC_CACHE_ENABLED=true`.
- New pytest suites for persistence + semantic-cache services; llm mock adjustments.

### Out of Scope

- Frontend, prompt, model, or API contract changes
- Postgres/Redis; multi-worker deployments
- Report feature behavior beyond gaining persistence

## Capabilities

> CONTRACT for sdd-spec.

### New Capabilities

- `llm-http-pooling`: shared HTTP client lifecycle for all LLM provider calls
- `conversation-persistence`: durable conversation/turn/message/report store, restart recovery, retention
- `semantic-answer-cache`: embedding-similarity answer reuse with safety guardrails

### Modified Capabilities

- `conversation-engine`: Conversation State + Session TTL Eviction requirements gain durability semantics (history survives restarts; eviction deletes DB rows except reports)

## Approach

Three independently shippable slices in dependency order:

1. **Keep-alive** (trivial): `_get_client()` lazy singleton in llm.py; drop per-call `with` blocks; close hook in lifespan.
2. **Persistence** (foundation): new `backend/services/persistence.py` following service-module patterns; write-through at all mutation points (both message endpoints + create + eviction); hydrate-on-miss replaces 404 for known-but-unloaded ids; thread model (locked shared conn vs conn-per-op via `asyncio.to_thread`) decided in design.
3. **Semantic cache**: new `backend/services/semantic_cache.py`; hooks around the FAQ-miss path in both endpoints; disabled when TF-IDF fallback is active (no stable embedding space).

## Affected Areas

| File | Impact | Lines Est. |
|------|--------|-----------|
| `backend/services/llm.py` | Major: shared client | ~40 |
| `backend/services/persistence.py` | New | ~150 |
| `backend/services/semantic_cache.py` | New | ~120 |
| `backend/main.py` | Major: write-through, hydration, shutdown hook | ~60 |
| `backend/config.py` | Minor: DB path + cache env vars | ~10 |
| `tests/test_llm.py`, new `test_persistence.py`, `test_semantic_cache.py` | Moderate | ~300 |
| `.env.example` | Minor | ~10 |

~700 lines total → exceeds 400-line review budget; the three capabilities are natural chained-PR slices.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Wrong semantic match served | Med | 0.93 threshold + first-turn-only rule + TTL + kill-switch |
| SQLite locking Windows dev vs Linux VPS | Low | WAL mode; single writer; strategy finalized in design |
| `tests/test_llm.py` patches `llm.httpx.Client` attr (8 sites) — breaks with singleton | High | Update mocks to patch the shared-client factory |
| TF-IDF fallback produces unstable vectors that get cached | Low | Cache disabled when sentence-transformers unavailable |
| DB write failure breaks a live request | Med | Log-and-continue degradation; persistence never raises into pipeline |

## Rollback

Per-slice `git revert`. Kill-switch `SEMANTIC_CACHE_ENABLED=false` neutralizes Cap 3 without redeploy. Deleting `data/interviewtts.db` reverts state; the in-memory dict remains the runtime source of truth. No migrations, no API changes — rollback affects no consumers.

## Dependencies

None new — stdlib `sqlite3`; numpy + sentence-transformers already present.

## Success Criteria

- [ ] Paraphrased repeat question on a fresh conversation answers < 300ms end-to-end
- [ ] Restart preserves history: an existing `conversation_id` accepts new messages; past reports intact
- [ ] TTL-evicted conversations lose turns/messages rows but reports persist ≥ 30 days
- [ ] Full pytest suite green including new persistence + semantic-cache tests
- [ ] Zero new runtime dependencies

## Proposal question round

Blocked from a direct user round (executor context). Assumptions for review:

- **A1:** The launch brief's "implementation order 3→6→2" conflicts with its own dependency statements ("Cap 1 do first", "Cap 3 builds ON Cap 2"). Interpreted as dependency order 1→2→3. If "3→6→2" encodes task-phase numbering for sdd-tasks, confirm at spec/tasks time.
- **A2:** Thread-safety strategy (locked shared connection vs connection-per-operation) is deferred to design.
- **A3:** Threshold 0.93 stated as fixed value; env-configurability decided in design.
