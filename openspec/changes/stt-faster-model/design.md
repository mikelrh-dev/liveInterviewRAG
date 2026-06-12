# Design: Switch Whisper STT model from `base` to `tiny`

## Architecture Context

The STT service lives at `backend/services/stt.py:STTService`. At
`backend/main.py:76` it is instantiated with `model_name=config.WHISPER_MODEL`,
so the config default at `backend/config.py:15` is the single source of truth
for the runtime model. `STTService.__init__` is a thin wrapper that forwards
`model_name` to `faster_whisper.WhisperModel(...)` at line 25 — it accepts any
valid faster-whisper size string, so the swap requires no service-layer code
change.

Pipeline position: `mic audio → STT (this change) → RAG → LLM → TTS → speaker`.
Per-turn latency is logged at `backend/main.py:307` as
`Pipeline: STT=%.2fs RAG=... LLM=... TTS=... TOTAL=...`. That is the metric
that moves.

## Architecture Decisions

### Decision: Flip the default value only; do not introduce a feature flag

**Choice**: Change the `os.getenv(..., "base")` default to `"tiny"` and let
operators override via `WHISPER_MODEL=base` in `.env` if they need higher
accuracy.
**Alternatives considered**: Env-driven switch (`WHISPER_MODEL=tiny|base` with
runtime toggle), per-request model override, A/B router.
**Rationale**: The override path already exists and is documented. Runtime
toggles add complexity (model reload, memory) for a benefit no stakeholder
asked for. The change is "make the faster thing the default, keep the slower
thing one env var away."

### Decision: Align the `STTService` class default with the config default

**Choice**: Also change `STTService.__init__` default from `"base"` to
`"tiny"` at `backend/services/stt.py:12`.
**Alternatives considered**: Leave the class default at `"base"`; rewrite
`test_init_defaults` to patch `config.WHISPER_MODEL`; delete the default
argument and require an explicit `model_name`.
**Rationale**: The proposal marks `stt.py` as Unchanged, but
`tests/test_stt.py:16` calls `STTService()` with no args, so the existing
assertion targets the **class default**, not the config default. The minimum
honest change is to keep both defaults in sync. This is a design catch —
the proposal underspecifies the file list.

## File Changes

| File | Action | Why |
|------|--------|-----|
| `backend/config.py` | Modify line 15 | Flip default `"base"` → `"tiny"`. |
| `.env.example` | Modify line 11 | New default + trade-off comment. |
| `tests/test_stt.py` | Modify line 17 | Assert new default `"tiny"`. |
| `backend/services/stt.py` | Modify line 12 | Sync class default to `"tiny"` (design catch — see Decisions). |
| `backend/main.py` | Unchanged | Inherits new default via `config.WHISPER_MODEL` at line 77. |

### Literal edits

**`backend/config.py:15`**
```diff
-self.WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
+self.WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "tiny")
```

**`backend/services/stt.py:12`**
```diff
-def __init__(self, model_name: str = "base", device: str = "cpu", compute_type: str = "int8"):
+def __init__(self, model_name: str = "tiny", device: str = "cpu", compute_type: str = "int8"):
```

**`.env.example:11-13`** — update default and add a comment line above it:
```diff
 # Whisper settings
-WHISPER_MODEL=base
+# Default `tiny` for ~4x faster CPU transcription; set to `base` for higher accuracy.
+WHISPER_MODEL=tiny
 WHISPER_DEVICE=cpu
 WHISPER_COMPUTE_TYPE=int8
```

**`tests/test_stt.py:17`**
```diff
-assert svc.model_name == "base"
+assert svc.model_name == "tiny"
```

## Testing Strategy

This project runs with `strict_tdd: true`. The test update is the **red**
step: change line 17 to assert `"tiny"` first, confirm it fails against the
old default, then apply the three production edits to make it **green**.
No refactor needed for a 3-line change.

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `tests/test_stt.py::TestSTTService::test_init_defaults` asserts new default. | `python -m pytest tests/test_stt.py -v` |
| Smoke | Full suite stays green. | `python -m pytest tests/ -v` |
| Manual | Median STT/turn < 1.0 s with `tiny`. | See "Manual Verification". |

## Manual Verification

1. Ensure `INFO` logging is enabled for the backend (uvicorn default).
2. Restart the server so `STTService.load_model()` pulls `tiny` (~75 MB,
   one-time).
3. Record a 5–10 s test clip via the existing UI.
4. Inspect the log line at `backend/main.py:307`:
   `Pipeline: STT=0.4s RAG=0.1s LLM=2.1s TTS=1.2s TOTAL=3.8s`.
5. Pass criterion: `STT` < 1.0 s median across 5 turns. Compare to a
   `WHISPER_MODEL=base` run for the delta.

## Rollback

Flip `backend/config.py:15` back to `"base"` (or set `WHISPER_MODEL=base` in
`.env` and restart). No data migration, no schema change.

## Risk Callouts

- Lower accuracy on noisy audio / accents / rare jargon — **Medium**. Target audio is close-mic, quiet; override preserved.
- Word-level errors leak to the LLM — **Low**. `conversation-engine` accepts "reasonable accuracy".
- First-run `tiny` download (~75 MB) delays startup — **Low**. Same as `base`; logged on load.
- `test_init_defaults` fails until updated — **Certain**. Updated in this change.
