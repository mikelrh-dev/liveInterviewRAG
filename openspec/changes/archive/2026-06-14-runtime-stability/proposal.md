# Proposal: Runtime Stability — Memory leak fixes & TTS streaming resilience

## Intent

The backend (`backend/main.py`, 625 lines) runs on Oracle Free Tier 24/7 and accumulates unbounded state in 3 in-memory structures: conversations (never evicted), rate-limit IP entries (never pruned), and audio files (never re-cleaned). Separately, TTS task failures in the streaming endpoint silently hang the SSE stream. These 4 stability issues are each small to fix — together they eliminate the runtime's single biggest class of production incidents.

**Why now**: The deploy survives weeks but degrades visibly (memory grows, rate-limit map bloats, audio dir fills disk). Fixing before adding any new capability.

## Scope

### In Scope

1. **Session TTL cleanup** — periodic asyncio task removes conversations inactive > N hours. `last_activity_at` updated on every `/message` and `/message/stream` call.
2. **Rate-limit store eviction** — same periodic task drops IP entries whose timestamps are all outside the rate-limit window.
3. **Periodic audio cleanup** — spawn `cleanup_stale_audio()` every N minutes (not just at startup).
4. **TTS streaming resilience** — try-except around each `tts_service.synthesize_sentence()` invocation + around `done.result()` in the event loop's done-set processing. Emits `sse_format("error", ...)` on TTS failure, continues stream.

### Out of Scope

- RAG optimization (final RAG not yet implemented)
- FastAPI `Depends()` injection refactor (over-engineering without test infra)
- `update_conversation_summary` whitespace-aware truncation (not needed)
- LLM-based intelligent summarization (cosmetic, costly)
- Any changes to `tts.py` (service layer is correct; bug is in main.py orchestration)

## Capabilities

### New Capabilities

None. All fixes are internal improvements within existing capabilities.

### Modified Capabilities

- `conversation-engine`: Session TTL (conversations expire after inactivity) and TTS error resilience (stream does not hang on TTS failure) are spec-level behavior changes. Each gets a delta spec.

## Approach

```
[lifespan startup]
    │
    ├── spawn background_cleanup() task
    │     every 15 min (configurable):
    │       1. walk conversations → remove if idle > SESSION_TTL_HOURS
    │       2. walk _rate_limit_store → drop empty-window IPs
    │       3. call cleanup_stale_audio()
    │
    ├── (existing) cleanup_stale_audio() once at startup
    │
    └── (unchanged) service init, RAG ingest, LLM pre-warm

[streaming event loop]
    before:  task = create_task(tts_service.synthesize_sentence(...))
             → fire-and-forget, exception hangs stream
    after:   task = create_task(_safe_tts_sentence(...))
             → try-except inside: logs error, emits SSE "error", task
               returns (None, None) so done.result() doesn't raise
    also:    wrap done.result() in try-except so one TTS failure
             doesn't kill the event loop iteration
```

### Config additions (`backend/config.py`)

| Key | Default | Purpose |
|-----|---------|---------|
| `SESSION_TTL_HOURS` | `2` | Hours of inactivity before conversation eviction |
| `AUDIO_CLEANUP_INTERVAL_MIN` | `30` | Minutes between periodic audio cleanup runs |

### Per-task scope

| # | What | Where | Est. lines |
|---|------|-------|-----------|
| 1 | Session TTL + `last_activity_at` | `main.py:70,305-311` + message handlers | ~20 |
| 2 | Rate-limit store eviction | `main.py:153-182` + background task | ~5 |
| 3 | Periodic audio cleanup | `main.py:100-138` + config.py | ~10 |
| 4 | TTS streaming resilience | `main.py:549-570` | ~15 |

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/main.py` | **Modified** | 4 stability fixes (TTL cleanup task, rate-limit eviction, audio re-cleanup, TTS resilience) |
| `backend/config.py` | **Modified** | 2 new env vars: `SESSION_TTL_HOURS`, `AUDIO_CLEANUP_INTERVAL_MIN` |
| `tests/test_api.py` | **Modified** | Tests for TTL behavior, rate-limit eviction, TTS error emission |
| `tests/test_conversation_memory.py` | **Modified** | Tests for `last_activity_at` timestamp updates |
| `openspec/specs/conversation-engine/spec.md` | **Modified** | Delta spec for session TTL + TTS resilience requirements |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Cleanup runs during active request, evicts a conversation mid-turn | Low | TTL check compares `last_activity_at` against a cold threshold (hours). A mid-turn request updates the field. Worst case: request fails with 404, client retries. |
| Background task exception kills the whole cleanup | Low | Each cleanup sub-step wrapped in try-except with error log. One step failing does not block others. |
| `SESSION_TTL_HOURS=0` or negative evicts everything at first tick | Low | Guard: TTL must be >= 0.1 hours (6 minutes). Log warning at startup if set too low. |
| TTS error-sse leaks internal file paths to client | Low | Error payload is `{"detail": "...", "id": N}` — no path info. Only `sentence_id` and a generic message. |

## Rollback Plan

Per-task independent revert:
1. **Session TTL**: revert `main.py` background task + `last_activity_at` lines; remove `SESSION_TTL_HOURS` from config.py. Conversations live forever again.
2. **Rate-limit eviction**: revert the 2 lines in the background task that sweep `_rate_limit_store`. IPs accumulate in memory again.
3. **Periodic audio cleanup**: revert the spawned task in `lifespan`. Audio cleanup runs once at startup only.
4. **TTS resilience**: revert the try-except wraps around `synthesize_sentence` and `done.result()`. TTS failures re-expose the hanging-stream bug.

All 4 are independent — roll back any subset without affecting the others.

## Dependencies

- **None**. Python stdlib only (`asyncio`, `datetime`, `logging`, `time`). No new third-party packages. Existing `asyncio.create_task` pattern already used in the codebase.

## Open Questions

1. **TTL default**: 2 hours is generous for an interview (most sessions < 30 min). Should we lower to 1 hour to reclaim memory faster? Risk: long pauses between questions would evict.

2. **Cleanup interval**: 30 min for audio, 15 min for session/rate-limit sweep. Should both run on the same interval (15 min) or keep separate? Combined reduces asyncio task overhead.

3. **Notification on eviction**: should evicting an active conversation send an SSE event to the frontend ("session expired"), or just silently remove it? Silent is simpler; SSE requires frontend wiring.

4. **Rate-limit eviction safety**: should we keep the last N IP entries (say 1000) even if all timestamps are stale, to avoid re-creating dict entries for returning IPs? Micro-optimization vs. purity tradeoff.

## Success Criteria

- [ ] Conversations idle longer than `SESSION_TTL_HOURS` are removed by the periodic task — verified by test asserting eviction after simulated idle time.
- [ ] `_rate_limit_store` IP count stabilizes (does not grow unboundedly) under simulated traffic from distinct IPs — verified by test.
- [ ] Audio cleanup runs every `AUDIO_CLEANUP_INTERVAL_MIN` and removes files older than 1h — verified by test with mocked `datetime`.
- [ ] TTS `synthesize_sentence` failure emits `sse_format("error", ...)` to client and does NOT abort the stream — verified by test with side_effect on TTS mock.
- [ ] Single TTS task failure in the done-set loop does not crash `event_generator()` — verified by test raising on `done.result()`.
- [ ] `last_activity_at` timestamp is updated on every `POST /api/conversation/{id}/message` and `POST /api/conversation/{id}/message/stream` — verified by existing integration tests.
- [ ] All existing `pytest` tests pass with zero regressions.
