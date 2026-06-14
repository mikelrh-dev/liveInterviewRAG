# Tasks: runtime-stability — Memory leak fixes & TTS streaming resilience

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 80-120 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | All 4 stability fixes + tests | Single PR | 4 cohesive changes, ~100 lines; no split needed |

## Phase 1: Configuration

- [x] 1.1 Add `SESSION_TTL_HOURS` (default 2, floor 0.1) and `AUDIO_CLEANUP_INTERVAL_MIN` (default 30) to `backend/config.py`. Floor check: if `< 0.1`, log warning and default to 2.
- [x] 1.2 Verify config values are importable from a test (sanity import check).

## Phase 2: Implementation

- [x] 2.1 Add `last_activity_at` (ISO 8601 UTC) to conversation dict at creation in `main.py`; update on every message handler invocation.
- [x] 2.2 Implement `async def periodic_cleanup(interval_seconds: int)` in `main.py`. Per tick: (a) evict idle conversations, (b) prune stale rate-limit IPs, (c) call `cleanup_stale_audio()`. Each sub-step wrapped in try-except.
- [x] 2.3 Spawn `periodic_cleanup` in `lifespan` startup, cancel in shutdown. Add 30-second initial delay before first tick.
- [x] 2.4 Wrap TTS `create_task` (main.py:555-560) and `done.result()` (main.py:565-570) in try-except; emit `sse_format("error", {"detail": ..., "id": sentence_id})` on failure and continue stream.

## Phase 3: Tests

Strict TDD — every test below is required.

- [x] 3.1 Test TTL floor enforcement: `SESSION_TTL_HOURS=0.05` logs warning and effective value is 2.
- [x] 3.2 Test conversation eviction: create conversation with past `last_activity_at`, run cleanup tick, assert conversation removed.
- [x] 3.3 Test rate-limit store pruning: insert stale IP timestamps, run cleanup tick, assert IP entry removed.
- [x] 3.4 Test TTS error emission: mock `synthesize_sentence` with `side_effect=RuntimeError`, assert SSE `event: error` emitted and stream continues to `done`.
- [x] 3.5 Test `done.result()` exception handling: mock `Task.result` to raise, assert error SSE emitted and event loop continues.

## Phase 4: Verification

- [x] 4.1 Run `python -m pytest tests/ -v` — confirm all tests pass with zero regressions.
- [x] 4.2 Manual smoke test — **deferred**: cannot run in automated session. Owner will run in next deploy session: start backend, open frontend, 2-min interview, set `SESSION_TTL_HOURS=0.05` in `.env`, idle 6+ min, confirm GET on old conversation_id returns 404. (84/84 unit/integration tests already pass via pytest, covering the same eviction behavior.)

## Phase 5: Deliver

- [x] 5.1 Stage `backend/main.py`, `backend/config.py`, test files. (`openspec/changes/runtime-stability/` artifacts correctly excluded from commit.)
- [x] 5.2 Commit `3cfe816`: `fix(stability): session TTL eviction, rate-limit pruning, periodic audio cleanup, TTS streaming resilience`
- [x] 5.3 Push to `origin/main` (3 commits ahead → 0; `50521eb..3cfe816`).
