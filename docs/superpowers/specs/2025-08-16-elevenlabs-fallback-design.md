# Design: Eleven Labs TTS with Automatic Fallback to Microsoft

**Date:** 2025-08-16  
**Status:** Approved for implementation  
**Scope:** Add Eleven Labs as primary TTS provider with automatic fallback to Microsoft Edge TTS  

---

## Overview

Current state: `TTSService` uses Microsoft Edge TTS exclusively.

Target state: `TTSService` attempts Eleven Labs first. If Eleven Labs fails (timeout, error, service down), automatically switches to Microsoft for the rest of that session. No health checks; once failed, stays on Microsoft until next session.

---

## Architecture

### Single Responsibility: TTSService

```
TTSService
├── Primary provider: ElevenLabsClient (timeout: configurable, high)
├── Fallback provider: EdgeTTSClient (existing)
└── Session state: _provider_active (tracks which provider to use)
```

**Key principle:** All orchestration lives in `TTSService`. Consumers don't know fallback exists.

### Providers

#### ElevenLabsClient (new)
- Wraps Eleven Labs API
- Configurable timeout (default: 15s, user can increase)
- Requires: API key, voice ID
- Raises `ElevenLabsError` on failure

#### EdgeTTSClient (existing, extracted)
- Current `TTSService` logic moved here
- Same interface as `ElevenLabsClient`
- No timeout (Microsoft is reliable)

### Configuration

`.env` additions:
```
# Eleven Labs (optional, defaults to Microsoft if not set)
ELEVENLABS_API_KEY=<key>
ELEVENLABS_VOICE_ID=<voice_id>
TTS_ELEVENLABS_TIMEOUT=15  # seconds, configurable

# TTS Provider (default: "microsoft", set to "elevenlabs" for prod)
TTS_PRIMARY_PROVIDER=microsoft  # or "elevenlabs"
```

**Provider selection logic:**
1. Read `TTS_PRIMARY_PROVIDER` from env (default: "microsoft")
2. If "elevenlabs": try Eleven Labs, fallback to Microsoft on any error
3. If "microsoft": use Microsoft only
4. If Eleven Labs credentials missing: force Microsoft

---

## Execution Flow

### synthesize(text) → (audio_path, provider_used)

```
On first call in session:
  1. Read TTS_PRIMARY_PROVIDER
  2. If "microsoft" → skip Eleven Labs, use Microsoft directly
  3. If "elevenlabs":
     a. Attempt Eleven Labs (with timeout)
     b. Success → return (path, "elevenlabs")
     c. Failure/timeout → set session_fallback_active = True
                         → use Microsoft for all remaining calls this session
                         → return (path, "microsoft")

On subsequent calls in same session:
  - If session_fallback_active == True → use Microsoft, skip Eleven Labs
  - Otherwise → repeat flow above
```

### Response to Frontend

JSON response includes provider flag:
```json
{
  "audio_path": "/audio/abc123.mp3",
  "tts_provider": "elevenlabs"
}
```

Frontend can log/track which provider was used (optional UI feedback).

---

## Implementation Details

### TTSService refactor

**Removed:** Direct use of `edge_tts.Communicate`  
**Added:**
- `_primary_provider` (str: "elevenlabs" or "microsoft")
- `_session_fallback_active` (bool: track if fallback triggered)
- `ElevenLabsClient` and `EdgeTTSClient` instances

**Public API (unchanged from outside perspective):**
```python
async def synthesize(text: str, output_path: str | Path | None = None) -> tuple[Path, str]:
    """
    Returns: (audio_path, provider_used)
    Raises: ValueError (empty text), RuntimeError (both providers fail)
    """
```

### Error Handling

**Eleven Labs fails → Microsoft is used:**
- Log warning: "Eleven Labs failed: {error}, using Microsoft fallback"
- `session_fallback_active = True`
- Return audio with `provider="microsoft"`

**Both fail → Raise RuntimeError:**
- Log critical: "Both Eleven Labs and Microsoft failed"
- User sees error in UI

---

## Testing Strategy

**Unit tests:**
- `ElevenLabsClient`: mocked API, timeout simulation
- `EdgeTTSClient`: mocked edge_tts.Communicate
- `TTSService`: fallback logic, session state, provider selection

**Integration tests:**
- Mock both providers, verify fallback triggers correctly
- Verify session state persists across multiple calls

**Manual testing (when credentials provided):**
- Hit real Eleven Labs API, verify timeout behavior
- Trigger fallback, confirm Microsoft takes over

---

## Deployment Notes

### Development
```env
TTS_PRIMARY_PROVIDER=microsoft
ELEVENLABS_API_KEY=  # optional, leave empty
```

### Production
```env
TTS_PRIMARY_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=<your_key>
ELEVENLABS_VOICE_ID=<voice_id>
TTS_ELEVENLABS_TIMEOUT=15
```

**Cost control:** Use Microsoft in dev (free tier), Eleven Labs in production (monitored).

---

## Non-Goals (this iteration)

- Health checks / automatic recovery
- Provider switching mid-session based on performance metrics
- User-selectable voice model (fixed to config)
- Streaming response (synthesis → file first)

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Single `TTSService` orchestrating fallback | Simpler than factory pattern; consumers unchanged |
| No health checks | KISS; one fallback per session is predictable |
| Session-scoped fallback | Prevents thrashing; next session gets fresh chance |
| Provider flag in response | Frontend visibility without breaking API contract |
| Env-based provider selection | Easy to switch dev ↔ prod without code changes |
