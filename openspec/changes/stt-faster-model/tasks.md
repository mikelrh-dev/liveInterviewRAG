# Tasks: Switch Whisper STT model from `base` to `tiny`

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~5 (3 prod + 1 test + 1 doc comment) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single commit on `main` |
| Delivery strategy | ask-always (resolved: single commit, no chain) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: stacked-to-main
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Default Whisper model `base` → `tiny` with passing test | Single commit on `main` | Includes test, config, service default, .env example |

## Phase 1: TDD — RED

- [x] 1.1 Edit `tests/test_stt.py` line 17: `assert svc.model_name == "base"` → `assert svc.model_name == "tiny"`.
- [x] 1.2 Verify RED: `python -m pytest tests/test_stt.py::TestSTTService::test_init_defaults -v` must FAIL (current prod still defaults to `"base"`).

## Phase 2: Production GREEN

- [x] 2.1 Edit `backend/config.py` line 15: default `"base"` → `"tiny"` in `os.getenv("WHISPER_MODEL", ...)`.
- [x] 2.2 Edit `backend/services/stt.py` line 12: class default `model_name: str = "base"` → `= "tiny"` (sync with config; design catch).
- [x] 2.3 Edit `.env.example` lines 11-13: add trade-off comment line above and flip `WHISPER_MODEL=base` → `WHISPER_MODEL=tiny`.

## Phase 3: Automated Verification

- [x] 3.1 Run `python -m pytest tests/test_stt.py -v`; expect `test_init_defaults` now PASSES.
- [x] 3.2 Run `python -m pytest tests/ -v`; expect all green, no regressions.

## Phase 4: Manual Latency Check (optional)

- [ ] 4.1 Start server: `uvicorn backend.main:app --port 8000`; record 5-10s clip via UI.
- [ ] 4.2 Inspect `Pipeline: STT=...` log line; pass = STT < 1.0s median across 5 turns. Skip if deploying and observing in prod.

## Phase 5: Commit & Persist

- [x] 5.1 Stage only the 4 implementation files (`tests/test_stt.py`, `backend/config.py`, `backend/services/stt.py`, `.env.example`); commit with `perf(stt): default to whisper tiny for ~4x faster cpu transcription`. Push to `main`.
- [x] 5.2 Leave `openspec/changes/stt-faster-model/` artifacts out of the implementation commit; optionally commit them separately as `docs(specs): add stt-faster-model change artifacts` or leave to user.
- [ ] 5.3 Update Engram: mark change `stt-faster-model` status as `planned` so orchestrator can locate it on apply.

## Notes

- Order is strict: 1.1 → 1.2 (RED) → 2.x (GREEN) → 3.x (verify) → 4.x (optional) → 5.x (commit).
- Do NOT modify any code during this planning phase — tasks.md is the only artifact produced.
- `.env.example` adds 1 comment line (net +1 line); test/config/service each net 0 line-count, value change only.
- Rollback: revert commit OR set `WHISPER_MODEL=base` in `.env` and restart.
