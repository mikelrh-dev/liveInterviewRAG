# Design: Pre-generate Welcome Audio at Conversation Creation

## Technical Approach

Extend `POST /api/conversation` to synthesize the welcome message via Edge TTS immediately after creating the conversation session. The audio file follows the existing `audio/{conversation_id}/{message_id}.mp3` convention. Failure degrades gracefully — the endpoint still returns text-only welcome.

## Architecture Context

| Layer | Current Behavior | Change |
|-------|-----------------|--------|
| `create_conversation()` | Creates session dict, returns `conversation_id` + `welcome_message`. Already `async def`. | Adds TTS synthesis step after session creation. Returns `welcome_audio_url` on success. |
| `tts_service.synthesize()` | Async Edge TTS call via `edge_tts.Communicate.save()`. Used by `send_message` endpoints. | Same service, same signature. New call site. |
| `config.AUDIO_DIR` | Resolves to `<project>/audio/`. Mounted at `/audio` via `StaticFiles`. | No change. |
| `sanitize_for_tts()` | Strips markdown/emoji before synthesis. Used in `send_message`. | Reused for welcome text. |

## Architecture Decisions

### Decision: Graceful degradation on TTS failure

**Choice**: Wrap TTS in try/except — on failure, log a warning and omit `welcome_audio_url` from the response. The endpoint still returns 200 with `welcome_message` text.

**Alternatives considered**: (a) Return 503 on TTS failure — rejected because a missing welcome audio should not block the entire conversation. (b) Retry logic — not worth complexity for a pre-warm optimization.

**Rationale**: The welcome audio is a UX enhancement, not a functional requirement. A recruiter can still read the welcome text. The conversation flow must never be blocked by a TTS transient failure.

### Decision: Use `get_audio_url()` vs inline URL construction

**Choice**: Follow the existing `send_message` pattern: construct the URL inline as `f"/audio/{conversation_id}/{message_id}.mp3"` rather than using `tts_service.get_audio_url()`, which only returns `f"/audio/{audio_path.name}"` (loses the subdirectory).

**Rationale**: Consistency with the existing code at line 314. A future refactor could fix `get_audio_url()` to support subdirectories, but that's out of scope.

## Data Flow

```
POST /api/conversation
    │
    ├─ 1. Generate conversation_id
    ├─ 2. Store session in dict
    ├─ 3. Sanitize welcome text via sanitize_for_tts()
    ├─ 4. Generate message_id → output_audio = audio/{conv_id}/{msg_id}.mp3
    ├─ 5. await tts_service.synthesize(clean_text, output_path)
    │      ┌─ Success → welcome_audio_url = /audio/{conv_id}/{msg_id}.mp3
    │      └─ Failure → log warning, welcome_audio_url = None
    └─ 6. Return { conversation_id, welcome_message, welcome_audio_url? }
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/main.py` (lines 218-234) | Modify | Add TTS synthesis + audio_url to `create_conversation()` |
| `frontend/app.js` (lines 62-85) | Modify | Pre-load and play welcome audio after conversation creation |

## Code Change Shape

### Backend (`backend/main.py`)

```python
@app.post("/api/conversation")
async def create_conversation():
    conversation_id = uuid.uuid4().hex
    welcome = "¡Hola! Soy Mikel, desarrollador junior DAM. Pregúntame sobre mi experiencia, proyectos o habilidades."

    conversations[conversation_id] = {
        "id": conversation_id,
        "messages": [],
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
    }
    logger.info("Created conversation: %s", conversation_id)

    # Pre-generate welcome audio
    welcome_audio_url = None
    try:
        message_id = uuid.uuid4().hex
        output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
        clean_welcome = sanitize_for_tts(welcome)
        await tts_service.synthesize(clean_welcome, output_path=output_audio)
        welcome_audio_url = f"/audio/{conversation_id}/{message_id}.mp3"
    except RuntimeError as e:
        logger.warning("Welcome audio pre-gen failed, continuing text-only: %s", e)

    return {
        "conversation_id": conversation_id,
        "welcome_message": welcome,
        "welcome_audio_url": welcome_audio_url,
    }
```

No imports are needed — `sanitize_for_tts` is already imported at line 30, `config` is available, and `tts_service` is a module-level global.

### Frontend (`frontend/app.js`)

```javascript
async function startInterview() {
    if (isInterviewActive) return;

    try {
        const res = await fetch(`${API_BASE}/api/conversation`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        conversationId = data.conversation_id;
        addMessage('system', data.welcome_message);

        // Pre-load and play welcome audio if available
        if (data.welcome_audio_url) {
            const audio = new Audio(`${API_BASE}${data.welcome_audio_url}`);
            audio.play().catch(() => {
                // Autoplay blocked by browser — user must interact first
                // The audio element is available so a future interaction can trigger it
                console.log('Welcome audio autoplay blocked (browser policy)');
            });
        }
    }
    // ... rest unchanged
}
```

## Autoplay Handling

Browsers block `audio.play()` without prior user gesture. The `.catch()` on the promise handles this silently — the welcome text is already visible via `addMessage`. If the recruiter has already interacted with the page (setting up the interview), autoplay may succeed. The design accepts this as a best-effort enhancement; no retry or UI element is added.

## Interfaces / Contracts

Response shape for `POST /api/conversation`:

```typescript
{
  conversation_id: string;
  welcome_message: string;        // unchanged, always present
  welcome_audio_url: string | null;  // NEW — null if TTS failed
}
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `sanitize_for_tts(welcome)` | Existing tests cover sanitization logic. Welcome text has no markdown, so no new tests needed. |
| Integration | POST response includes `welcome_audio_url` | Add a test that calls the endpoint and verifies the response shape — `conversation_id` is a hex string, `welcome_message` is non-empty, `welcome_audio_url` is a string or null. |
| Integration | TTS failure fallback | Mock `tts_service.synthesize` to raise `RuntimeError` and confirm response is 200 with `welcome_audio_url: null`. |
| Manual | Audio plays in browser | Start interview in browser, confirm the welcome message is heard. |

All existing `pytest tests/` tests should pass unchanged — the change adds a field to the response but doesn't modify any existing behavior.

## Rollback

- **Backend**: Revert the modified section of `backend/main.py` to remove TTS synthesis from `create_conversation()`.
- **Frontend**: Remove the `welcome_audio_url` handling block from `startInterview()`.
- No data migration needed — stale welcome audio files in `audio/` are cleaned up by the existing 1-hour TTL cleanup in `cleanup_stale_audio()`.

## Open Questions

None.
