# Verification Report: runtime-stability

**Commit**: 3cfe816ee886418b8e17a15d242fe89db648bb47
**Date**: 2026-06-14
**Verifier**: sdd-verify sub-agent (adversarial review)

---

## Status

**PASS WITH WARNINGS**

---

## Build & Tests

**Command**: `python -m pytest tests/ -v`
**Result**: 84 passed, 0 failed, 0 skipped, 2 warnings (DeprecationWarning for SwigPyPacked/SwigPyObject in RAG tests — pre-existing, not related to this change)
**Duration**: 30.98s

All tests pass with zero regressions. Matches apply's claim of 84/84.

---

## Spec Coverage

| # | Scenario | Spec line | Test function | File | Pass? | Notes |
|---|----------|-----------|---------------|------|-------|-------|
| 1 | Conversation evicted after TTL expiry | spec.md:14-17 | `test_conversation_eviction` | `test_conversation_memory.py:334` | ✅ PASS | Creates stale conversation (3h old, TTL=2h), asserts evicted. Also has inverse test `test_recent_conversation_not_evicted`. |
| 2 | TTL floor enforced | spec.md:19-22 | `test_session_ttl_floor_enforced` | `test_config.py:48` | ✅ PASS | SESSION_TTL_HOURS=0.05 → warning logged, effective TTL=2. Also has default and normal-value tests. |
| 3 | Stale rate-limit entry pruned | spec.md:29-31 | `test_rate_limit_pruning` | `test_conversation_memory.py:404` | ✅ PASS | IP with timestamps outside 60s window is removed. Also has inverse test `test_active_rate_limit_entry_not_pruned`. |
| 4 | Periodic audio cleanup fires | spec.md:37-40 | *(missing)* | — | ⚠️ WARNING | `periodic_cleanup` calls `cleanup_stale_audio()` per tick (visible in code at main.py:189), but no test explicitly asserts the call. Design planned a mock-call-count test but it was not implemented. Code is correct; coverage gap. |
| 5 | TTS failure emits error and continues | spec.md:47-50 | `test_tts_synthesis_error_emits_sse_and_continues` | `test_api.py:242` | ✅ PASS | First call to `synthesize_sentence` raises RuntimeError, second succeeds. Asserts error events ≥1, audio_chunk events ≥1, done events ≥1. |
| 6 | Done-set failure does not abort stream | spec.md:52-55 | `test_tts_result_exception_emits_error_and_continues` | `test_api.py:287` | ✅ PASS | All `synthesize_sentence` calls raise RuntimeError. Asserts error events ≥1 and done events ≥1. |

---

## Task Completion

All 15 checkable tasks (1.1–4.1) are marked [x]. Tasks 4.2 (manual smoke test) and 5.1–5.3 (delivery) are correctly marked [ ] as expected for the verify phase.

---

## Code Review Findings

### 3a. Session TTL (`last_activity_at` + eviction)

| Check | Result |
|-------|--------|
| `last_activity_at` updated in POST /api/conversation? | ✅ Line 354 |
| `last_activity_at` updated in POST /message? | ✅ Line 450 |
| `last_activity_at` updated in POST /message/stream? | ✅ Line 642 |
| ISO 8601 UTC comparison correct? | ✅ `datetime.utcnow().isoformat()` stored, `datetime.fromisoformat()` compared |
| Floor check enforced (not just logged)? | ✅ Config sets `raw_ttl = 2.0` when `< 0.1` |
| Task cancels cleanly in lifespan shutdown? | ✅ `cleanup_task.cancel()` + `except asyncio.CancelledError` |
| Boundary: `SESSION_TTL_HOURS=0.1` accepted? | ✅ `0.1 < 0.1` is False, passes through |

**Findings:**

- ⚠️ **WARNING**: `c.get("last_activity_at", "")` in the eviction list comprehension returns `""` for conversations lacking the field, and `datetime.fromisoformat("")` raises `ValueError`. This would abort the entire eviction try-block for that tick. Under current code, all conversations are created via `create_conversation()` which always sets `last_activity_at`, so this can't happen in normal operation. However, the pattern is fragile — any future code path that adds conversations without `last_activity_at` would silently disable all evictions. Consider a defensive guard per conversation entry.

### 3b. Rate-limit pruning

| Check | Result |
|-------|--------|
| Criterion correct (all timestamps outside window)? | ✅ Yes, filters by `now - t < 60` |
| Window matches middleware's window? | ✅ Both use 60s |
| Snapshot iteration avoids RuntimeError? | ✅ `list(_rate_limit_store.keys())` |

**Findings:** No issues found.

### 3c. Periodic audio cleanup

| Check | Result |
|-------|--------|
| Interval from config (not hardcoded)? | ✅ `config.AUDIO_CLEANUP_INTERVAL_MIN * 60` |
| Existing startup call preserved? | ✅ `cleanup_stale_audio()` still called before lifespan yield |
| Non-existent `AUDIO_DIR` handled? | ✅ `Path.rglob("*")` returns empty generator on missing dir — no crash |
| Each sub-step isolated by try-except? | ✅ Yes, cleanup_stale_audio() in its own try-except |

**Findings:** No issues found with the implementation.

### 3d. TTS streaming resilience

| Check | Result |
|-------|--------|
| `create_task` try-except catches sync errors? | ✅ Catches `Exception` (though async `synthesize_sentence` errors happen at `done.result()` not `create_task`) |
| `done.result()` try-except catches errors? | ✅ |
| Error SSE includes `id` (sentence_id)? | ✅ Both paths include `id` |
| Error SSE includes `detail`? | ✅ Both paths include `"detail": "TTS synthesis failed"` |
| Error SSE leaks paths/stack traces? | ✅ No — only `{"detail": "TTS synthesis failed", "id": N}` is emitted |
| Sentence ID extracted correctly from `tts_futures`? | ✅ `tts_futures.get(done, -1)` |
| `done` event still emitted after TTS failures? | ✅ Loop continues while `tts_futures` is non-empty, exits correctly |
| LLM stream ends but TTS tasks remain? | ✅ Loop condition `while listening_to_llm or tts_futures:` handles this |

**Findings:**

- 💡 **SUGGESTION**: The first try-except (around `asyncio.create_task` at main.py:600-612) is effectively dead code for async exceptions. `create_task` only schedules the coroutine — actual `synthesize_sentence` errors surface at `done.result()`, which is caught by the second try-except (line 615-624). This first wrap only catches synchronous errors (e.g., `TypeError`, closed event loop), which never occur in practice. Consider adding a comment explaining this, or removing the outer try-except if it adds confusion. Functionally harmless.

- 💡 **SUGGESTION**: The TTS error test (`test_tts_synthesis_error_emits_sse_and_continues`) checks `"detail" in error_events[0].get("data", {})` but does NOT verify the `id` field. The spec requires both `detail` and `id`. The code emits both, but the test should assert `"id"` presence for full spec coverage.

---

## Edge Cases

| Edge Case | Result | Finding |
|-----------|--------|---------|
| `last_activity_at` missing from old conversation | ⚠️ WARNING | `c.get("last_activity_at", "")` → `fromisoformat("")` raises `ValueError`, aborting entire eviction tick. Not reachable under current code paths, but fragile. |
| `SESSION_TTL_HOURS` exactly 0.1 (boundary) | ✅ PASS | `0.1 < 0.1` is `False`, value accepted. Floor check correct. |
| Periodic cleanup cancelled mid-sub-step | ✅ PASS | `CancelledError` inherits from `BaseException`, NOT `Exception` — propagates through `except Exception` correctly. Lifespan shutdown catches it. |
| LLM stream ends with pending TTS tasks | ✅ PASS | Loop condition `while listening_to_llm or tts_futures:` keeps running until all TTS tasks complete. |

---

## Out-of-Scope Compliance

The diff (commit 3cfe816) touched exactly 5 files:

| File | Change | In Scope? |
|------|--------|-----------|
| `backend/config.py` | +15 lines | ✅ In scope (Affected Areas: config.py) |
| `backend/main.py` | +55 / -13 lines | ✅ In scope (Affected Areas: main.py) |
| `tests/test_api.py` | +83 lines | ✅ In scope (Affected Areas: test_api.py) |
| `tests/test_config.py` | +32 lines | ✅ In scope (tests for new config) |
| `tests/test_conversation_memory.py` | +202 lines | ✅ In scope (Affected Areas: test_conversation_memory.py) |

**Not touched** (all confirmed):
- `backend/services/tts.py` — ✅ Explicitly confirmed out of scope (design.md line 136)
- `backend/services/llm.py`, `rag.py`, `stt.py`, `candidate.py` — ✅ Not changed
- `frontend/` — ✅ No frontend changes
- Any other unexpected files — ✅ Not changed

**Verdict**: ✅ Fully compliant with out-of-scope boundaries.

---

## Final Verdict

**VERIFIED WITH WARNINGS — archive acceptable, address warnings in follow-up**

All 6 spec scenarios have correct implementations. 5 of 6 have explicit covering tests. No critical bugs, regressions, or spec violations found. 2 warnings and 2 suggestions identified for follow-up.

---

## Suggested Follow-up Work

| Priority | Issue | Type | Description |
|----------|-------|------|-------------|
| 1 | Add audio-cleanup call-count test | WARNING | Mock `cleanup_stale_audio` in `periodic_cleanup` test and assert it's called each tick (design.md:209 planned this but it was not implemented). |
| 2 | Harden TTL eviction for missing `last_activity_at` | WARNING | Add per-conversation `try/except ValueError` or skip entries without the field instead of failing the entire eviction block. |
| 3 | Add `id` field assertion to TTS error test | SUGGESTION | In `test_tts_synthesis_error_emits_sse_and_continues`, also assert `"id" in error_events[0]["data"]`. |
| 4 | Document or remove dead-code create_task try-except | SUGGESTION | The `try/except` around `asyncio.create_task` (main.py:600) only catches sync errors; async `synthesize_sentence` errors are caught by `done.result()`. Add a comment or simplify. |
