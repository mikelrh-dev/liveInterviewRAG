# Proposal: Switch Whisper STT model from `base` to `tiny` for lower latency

## Intent

STT is the longest pipeline stage on CPU. With `WHISPER_MODEL=base` and `int8`,
transcription takes ~1-3 s/turn; the pipeline budget is 8 s and recruiters
feel the delay. Switching to `tiny` gives ~4x speedup (~0.3-0.8 s/turn).
Accuracy loss is acceptable for the target: recruiter in a quiet room,
close-mic, professional Spanish/English.

## Scope

### In Scope
- Change `WHISPER_MODEL` default `"base"` → `"tiny"` in `backend/config.py`.
- Update `.env.example` to reflect the new default and document the trade-off.
- Update `tests/test_stt.py` (`test_init_defaults`) to assert the new default.

### Out of Scope
- "Distil-Whisper tiny" — does not exist in `faster-whisper`. User rejected.
- GPU models (e.g., `large-v3` on CUDA) — requires GPU, tracked separately.
- Changing `WHISPER_COMPUTE_TYPE`, `WHISPER_DEVICE`, `beam_size`, `vad_filter`.
- Frontend.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None. The `conversation-engine` requirement "Faster Whisper (int8 model, CPU)"
holds — `tiny` is also an int8 model — and the "under 2 seconds" STT
scenario is *more* achievable with `tiny`, so no delta is required.

## Approach

Config-only change. The path `STTService.__init__` →
`WhisperModel(self.model_name, ...)` accepts any faster-whisper size, so the
swap is a one-line default flip plus test/doc updates. Override:
`WHISPER_MODEL=base` in `.env`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/config.py` | Modified | `WHISPER_MODEL` default `"base"` → `"tiny"` (line 15). |
| `.env.example` | Modified | New default; comment on speed/accuracy. |
| `tests/test_stt.py` | Modified | `test_init_defaults` asserts `model_name == "tiny"`. |
| `backend/services/stt.py` | Unchanged | Accepts any model name. |
| `backend/main.py` | Unchanged | Inherits new default via `config.WHISPER_MODEL`. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Lower accuracy on noisy audio, accents, rare jargon. | Medium | Target audio is close-mic, quiet. Trade-off documented; `WHISPER_MODEL=base` override preserved. |
| Word-level errors visible to the LLM (e.g., "API" → "a pi"). | Low | `conversation-engine` accepts "reasonable accuracy". |
| First-run download of `tiny` (~75 MB) delays startup. | Low | Same as `base`; logged on startup. |
| `test_init_defaults` fails until updated. | Certain | Updated as part of this change. |

## Rollback Plan

Revert `backend/config.py` to `"base"`, restore `.env.example`, revert the
test — three lines. For in-prod rollback without redeploy, set
`WHISPER_MODEL=base` in `.env` and restart.

## Dependencies

- `faster-whisper` (already a dependency). No new packages.

## Success Criteria

- [ ] `python -m pytest tests/ -v` passes (incl. updated `test_init_defaults`).
- [ ] Median STT/turn under 1.0 s with `tiny` (down from 1-3 s with `base`).
- [ ] `/api/health` reports `whisper_loaded: true`; no regression in `conversation-engine` spec scenarios; `.env.example` documents the trade-off.
