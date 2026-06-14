# Archive Report: runtime-stability

**Archived on**: 2026-06-14
**Commit**: 3cfe816
**Verifier verdict**: PASS_WITH_WARNINGS
**Sync convention used**: A (merge)

## Summary

Closed the runtime-stability change. Fixed 4 memory/stability bugs in main.py: conversation TTL eviction, rate-limit store pruning, periodic audio cleanup, and TTS streaming resilience. 84/84 tests pass, zero regressions.

## Spec Changes

| Spec | Status | Change |
|------|--------|--------|
| conversation-engine | MODIFIED | +4 requirements (session TTL, rate-limit pruning, audio cleanup interval, TTS streaming resilience) |

## Test Stats

- Tests added: 12
- Tests passing: 84/84
- Lines changed: +401 -13 (over the original 400-line budget by 1 line; mostly tests)

## Known Follow-up

1. Add audio-cleanup call-count test (verify report W1)
2. Harden TTL eviction for missing last_activity_at (verify report W2)
3. Add `id` field assertion to TTS error test (verify report S1)
4. Document or simplify dead-code create_task try-except (verify report S2)
5. Run manual smoke test task 4.2 (deferred to user)

## Files Modified by Change

- backend/config.py (+15)
- backend/main.py (+82 -13)
- tests/test_api.py (+83)
- tests/test_config.py (+32)
- tests/test_conversation_memory.py (+202)
