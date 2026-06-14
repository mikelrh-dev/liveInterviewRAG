# Design: Runtime Stability — Memory leak fixes & TTS streaming resilience

## Technical Approach

A single unified background task (`periodic_cleanup`), spawned in `lifespan` startup and cancelled in shutdown, addresses all three memory leak concerns (conversations, rate-limit, audio) with one `asyncio.sleep`-driven loop. Separately, the streaming endpoint's TTS pipeline gets two try-except wraps (one around `synthesize_sentence` task creation, one around `done.result()` in the done-set loop) so a single TTS failure emits an SSE error and continues rather than hanging the stream. All four behaviors use only Python stdlib (`asyncio`, `datetime`, `logging`) — no new packages.

---

## Architecture Overview

```
[lifespan startup]
    │
    ├── cleanup_stale_audio()                    (existing, unchanged)
    │
    ├── background_task = asyncio.create_task(    [NEW — main.py:100-138]
    │       periodic_cleanup(interval_seconds) )
    │
    ├── service init, RAG ingest, LLM pre-warm   (unchanged)
    │
    └── yield  ──→  [FastAPI serving]
                        │
                        ├── POST /api/conversation                 [main.py:305-311]
                        │     └── add "last_activity_at": now       [1 line new]
                        │
                        ├── POST /api/conversation/{id}/message    [main.py:320-421]
                        │     └── set last_activity_at              [1 line new]
                        │
                        ├── POST /api/conversation/{id}/message/stream [main.py:424-604]
                        │     ├── set last_activity_at              [1 line new]
                        │     └── [TTS resilience — lines 549-570]
                        │           ├── try/except synthesize_sentence
                        │           └── try/except done.result()
                        │
                        └── [every N seconds → background_task]
                              ├── 1. _evict_stale_conversations()
                              ├── 2. _prune_rate_limit_store()
                              └── 3. cleanup_stale_audio()

[lifespan shutdown]
    │
    └── background_task.cancel()                                     [1 line new]
    └── try/except CancelledError                                     [wraps sleep]
```

Each of the three cleanup sub-steps runs inside its own try-except so one failure does not kill the other two.

---

## Background Task Design

### Function signature

```python
async def periodic_cleanup(interval_seconds: int) -> None:
```

### Lifecycle

| Phase | Action |
|-------|--------|
| `lifespan` startup (after service init, before `yield`) | `background_task = asyncio.create_task(periodic_cleanup(...))` |
| `lifespan` shutdown (after `yield`) | `background_task.cancel()` wrapped in `try/except asyncio.CancelledError` |
| Per tick | Three sub-steps, each in own try-except; then `await asyncio.sleep(interval)` |

**Sleep pattern**: `await asyncio.sleep(interval_seconds)` between ticks. If shutdown is requested mid-sleep, the `CancelledError` propagates through the sleep and the outer try-except catches it — the last tick may not fire and that is acceptable.

### Per-tick sub-steps

1. **Session TTL eviction**: iterate `conversations` dict snapshot, compare each entry's `last_activity_at` to `utcnow - timedelta(hours=TTL)`, `del conversations[k]` for stale ones. DEBUG log each eviction.
2. **Rate-limit store pruning**: iterate `_rate_limit_store` dict snapshot, for each IP entry filter timestamps by `now - t < window` — if result list is empty, delete the key. Pure prune (no keep-N guard — proposal open question #4 defers to "pure").
3. **Audio cleanup**: call existing `cleanup_stale_audio()` (already handles file-age check, already safe).

### Decision: Not APScheduler

Justification: one loop, three sub-steps, no need for cron scheduling or job persistence. `asyncio.create_task` + `asyncio.sleep` is the idiomatic FastAPI pattern already used in the codebase (e.g., LLM pre-warm uses blocking call in thread, but the pattern exists).

---

## Sequence Diagram — TTS Streaming with Resilience

```mermaid
sequenceDiagram
    participant Client
    participant event_generator as event_generator()
    participant LLM as LLM Stream
    participant SentenceBuf as SentenceBuffer
    participant TTS as TTSService
    participant done_set as done-set loop

    Client->>event_generator: POST /message/stream
    event_generator->>event_generator: STT + RAG + LLM start
    Note over event_generator,LLM: Step 3: LLM streaming

    loop For each token from LLM
        LLM-->>SentenceBuf: token
        alt sentence complete
            SentenceBuf-->>event_generator: sentence
            event_generator->>event_generator: sanitize_for_tts()
            Note over event_generator: [NEW] try-except around create_task
            event_generator->>TTS: create_task(synthesize_sentence(...))
            TTS-->>done_set: task in tts_futures
        end
    end

    Note over done_set: await asyncio.wait(pending, FIRST_COMPLETED)

    loop For each completed task in done_set
        alt task is queue item
            done_set->>event_generator: kind, data (LLM token / done / error)
        else task is TTS completion
            Note over done_set: [NEW] try-except around done.result()
            try
                done_set->>event_generator: sid, audio_path = done.result()
                event_generator-->>Client: SSE audio_chunk {id, url}
            except Exception as e
                event_generator-->>Client: SSE error {detail, id}
                event_generator->>event_generator: log error, continue
            end
        end
    end
```

**Changed lines**: `main.py:549-570` — the sentence-processing block where `create_task` is called (line 555) and the done-set result extraction (line 565).

---

## Module & File Changes

| File | Change Type | Lines Affected | Description |
|------|-------------|----------------|-------------|
| `backend/main.py` | Modified | ~50 | `lifespan` (spawn+cancel background task), `periodic_cleanup()` function (~20 lines), `create_conversation` (add `last_activity_at`), `send_message` (update `last_activity_at`), `send_message_stream` (update `last_activity_at`), TTS try-except in event loop (lines 549-570) |
| `backend/config.py` | Modified | ~10 | `SESSION_TTL_HOURS` (int, default 2, floor 0.1), `AUDIO_CLEANUP_INTERVAL_MIN` (int, default 15), TTL floor check at startup |
| `tests/test_api.py` | Modified | ~30 | TTL eviction test (mock datetime, simulate idle), rate-limit prune test, periodic audio test (verify interval trigger), TTS error resilience test (mock side_effect) |
| `tests/test_conversation_memory.py` | Modified | ~10 | `last_activity_at` field existence test, TTL update-on-message test |
| `backend/services/tts.py` | **Unchanged** | 0 | TTS service is correct; the bug is in `main.py` orchestration (missing try-except). Explicit confirmation. |

**line‑targeted locations in main.py**:
- `main.py:100-138` — lifespan: spawn `periodic_cleanup` after service init, cancel in shutdown
- `main.py:305-311` — `create_conversation`: add `"last_activity_at"` to dict
- `main.py:390-410` — `send_message`: add `conversations[...]["last_activity_at"] = datetime.utcnow().isoformat()`
- `main.py:549-570` — `event_generator`: try-except around `create_task(tts_service.synthesize_sentence(...))` AND around `done.result()` extraction
- `main.py:576-591` — `send_message_stream`: add `conversations[...]["last_activity_at"] = datetime.utcnow().isoformat()` after storing the turn

---

## Architecture Decisions

### AD-1: Single unified background task vs separate tasks per concern

| | Details |
|---|---------|
| **Decision** | One `periodic_cleanup` task with three sub-steps, each in its own try-except |
| **Rationale** | All three cleanups run on the same 15-minute cadence. Overlapping ticks cannot happen because the single loop serializes execution. One `create_task` + one `cancel()` = minimal lifecycle management. If any sub-step needed a different interval in the future, it is a 2‑line change to add a second task. |
| **Tradeoffs** | A slow sub-step (e.g. audio cleanup walking a full disk) delays the other sub-steps by that duration. Acceptable: audio cleanup is O(files), typically <100ms. |
| **Alternatives** | Three separate tasks (pro: isolated timing, con: 3x lifecycle boilerplate, risk of concurrent cleanup on same data). Cron job (pro: no code, con: not portable, needs host config). |

### AD-2: In-process asyncio task vs external scheduler vs cron

| | Details |
|---|---------|
| **Decision** | `asyncio.create_task` in `lifespan` startup, cancelled in shutdown |
| **Rationale** | Zero dependencies. Matches existing codebase pattern (lifespan-managed resources). Works identically on Windows, Linux, macOS, and the Oracle Free Tier target. The cleanup logic is pure Python with no persistence requirement — a cron/celery/APScheduler setup would be over-engineering. |
| **Tradeoffs** | Task is tied to process lifetime — if the process restarts, cleanup state resets. Acceptable: conversations are in-memory already, they reset on restart anyway. |
| **Alternatives** | APScheduler (adds a dependency, cron syntax, job store — unnecessary for 3 sub-steps). System cron (not portable, needs file lock to prevent concurrent runs). |

### AD-3: Silent eviction vs SSE event vs log-only

| | Details |
|---|---------|
| **Decision** | Silent eviction — DEBUG log only, no SSE event to client |
| **Rationale** | TTL is 2h by default. An evicted conversation is one the user abandoned (browser closed, idle). Sending an SSE event to a disconnected client is wasted work. If the user returns after >2h, the next API call returns 404, and the frontend already handles 404 by showing "conversation not found" and offering to start a new one. No frontend changes required. |
| **Tradeoffs** | If a user keeps the page open and just pauses for 2h, the eviction is silent — the next message attempt fails with 404. Acceptable: borderline case, and 404 response is clear. |
| **Alternatives** | SSE event (needs frontend listener wiring, benefits marginal). INFO log (would clutter production logs — DEBUG is appropriate for routine cleanup). |

### AD-4: `last_activity_at` field vs derived from turns list

| | Details |
|---|---------|
| **Decision** | Explicit `last_activity_at` field (ISO UTC string) stored in the conversation dict |
| **Rationale** | O(1) read for TTL comparison vs O(n) walk of turns list. The turns list can be hundreds of entries. A single datetime field is clearer, faster, and survives any future refactoring of the turn storage format. Set on creation and updated on every `POST /api/conversation/{id}/message` and `POST /api/conversation/{id}/message/stream`. |
| **Tradeoffs** | Requires explicit updates in three places (creation, message, stream). If a future endpoint bypasses these and only writes to `messages`/`turns` directly, it will miss the update. Mitigation: grep for `.append` on conversation fields during code review. |
| **Alternatives** | Derive from last turn's timestamp (fragile: turn schema may change). Derive from `messages` list (O(n) scan). No automated field (can't implement TTL). |

---

## Error Handling & Edge Cases

| Scenario | Behavior |
|----------|----------|
| Cleanup runs while a message is being processed | Race condition impossible — the `last_activity_at` update happens BEFORE the turn is stored. The TTL threshold is 2h. A message in flight updates `last_activity_at` to now, so the current tick will skip it. Worst case: tick reads `last_activity_at` before the update completes, sees a stale value, evicts it. The request's next line throws KeyError → 404. Client retries with a new conversation. |
| Background task raises an unhandled exception | Caught by the outer `try/except Exception` in the tick loop, logged as error, task continues to next tick. The task is never silently killed. |
| `SESSION_TTL_HOURS` env var set to 0 or negative | Rejected at startup: `config.py` validates floor ≥ 0.1 (6 minutes). If below floor, logs WARNING and defaults to 2. |
| Cleanup interval shorter than work duration | Not possible — `asyncio.sleep` follows the work. The loop is sequential: `work → sleep → work`. Overlapping ticks cannot occur because there is only one task. |
| All conversations evicted in one tick | Empty loop on next tick. Task continues normally. `conversations` is just an empty dict. `build_conversation_context` already handles missing conversations gracefully. |
| TTS `synthesize_sentence` raises | Caught by try-except. Logs error, yields `sse_format("error", {"detail": "...", "id": sentence_id})`. The task returns a sentinel so `done.result()` does not raise. Stream continues with remaining sentences. |
| `done.result()` raises | Caught by try-except around the extraction. Logs error, yields `sse_format("error", ...)`, deletes the failed task from `tts_futures`, continues the event loop. |

---

## Testing Strategy

### Unit tests

| What | Approach | Mock pattern |
|------|----------|-------------|
| `periodic_cleanup` — TTL eviction | Inject conversations with known `last_activity_at`, run one tick, assert stale ones removed | `mock asyncio.sleep` (make it a no-op), `mock datetime.utcnow` for time control |
| `periodic_cleanup` — rate-limit prune | Inject `_rate_limit_store` entries with all-stale timestamps, run tick, assert IP removed | `mock asyncio.sleep`, `mock time.time` |
| `periodic_cleanup` — audio cleanup | Assert `cleanup_stale_audio` is called each tick | Mock the function itself, assert call count |
| TTL floor enforcement | Set `SESSION_TTL_HOURS=0.05`, start app, assert warning logged and effective TTL is 2 | Config object verify-after-init |
| `last_activity_at` field | Create conversation, send message, assert field is set and is recent ISO datetime | Direct dict inspection |

### Integration tests

| What | Approach |
|------|----------|
| End-to-end TTL eviction through lifespan | `TestClient` + `mock.patch("backend.main.asyncio.sleep")` to trigger ticks synchronously. Add conversation with past `last_activity_at`, wait for tick, assert 404 on next message. |
| TTS error emission through streaming | Mock `TTSService.synthesize_sentence` with `side_effect=RuntimeError("TTS failed")`. POST to `/message/stream`, assert SSE stream contains `event: error` and continues to emit `event: done`. |
| Rate-limit prune through middleware | Fill `_rate_limit_store` with stale entries, trigger cleanup, assert next request from that IP is NOT rate-limited (entry was reset). |

### Mock patterns

- `mock asyncio.sleep` → `await asyncio.sleep(0)` or `return None` — makes cleanup loop synchronous in tests
- `mock datetime.utcnow` → `return datetime(2024, 1, 1, 12, 0, 0)` — absolute time control for TTL assertions
- `mock TTSService.synthesize_sentence` with `side_effect=RuntimeError` — triggers error path without real TTS

### Existing test conventions to follow

- `tests/test_api.py` uses `TestClient` from `fastapi.testclient`, patches services via `unittest.mock.patch`, and uses class-based test organization (`class TestMessageEndpoint`). Follow the same pattern.
- `tests/test_conversation_memory.py` directly imports `conversations` dict and manipulates it in tests. For `last_activity_at` tests, follow this direct-manipulation pattern.

---

## Rollback Considerations

| Behavior | Revert surface | Independent? |
|----------|----------------|--------------|
| Session TTL eviction | Remove `periodic_cleanup` spawn + cancel in lifespan, remove `last_activity_at` lines from 3 endpoints, remove `SESSION_TTL_HOURS` from config.py | ✅ Yes |
| Rate-limit store pruning | Remove the 2 lines in `periodic_cleanup` that sweep `_rate_limit_store` | ✅ Yes |
| Periodic audio cleanup | Remove the `cleanup_stale_audio()` call from the task (keep the startup-only call) | ✅ Yes |
| TTS streaming resilience | Remove the two try-except wraps in `event_generator` | ✅ Yes |

**Hybrid state consistency**: Because the four behaviors operate on independent data structures (conversations dict, `_rate_limit_store` dict, audio filesystem, and in-flight TTS tasks), any subset can be reverted without leaving the system in an inconsistent state. The background task itself becomes a no-op shell if all three cleanup sub-steps are removed, but it still spawns and sleeps — negligible overhead (<1µs per tick).
