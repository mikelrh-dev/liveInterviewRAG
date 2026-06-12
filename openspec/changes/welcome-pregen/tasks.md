# Tasks: Pre-generate Welcome Audio at Conversation Creation

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~30 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Low

## Phase 1: Test-First (TDD)

- [x] 1.1 Add test `test_create_conversation_includes_audio_url` in `tests/test_api.py::TestConversationEndpoint` — call `POST /api/conversation`, assert 200, assert `welcome_audio_url` is str or None, assert `welcome_message` is non-empty
- [x] 1.2 Add test `test_create_conversation_tts_failure_fallback` — mock `tts_service.synthesize` to raise `RuntimeError`, confirm 200 with `welcome_audio_url: null`

## Phase 2: Backend Implementation

- [x] 2.1 In `backend/main.py::create_conversation()`, add TTS synthesis block after session creation — generate `message_id`, call `sanitize_for_tts(welcome)`, `await tts_service.synthesize()`, build `welcome_audio_url`; wrap in `try/except RuntimeError` logging a warning on failure

## Phase 3: Frontend Implementation

- [x] 3.1 In `frontend/app.js::startInterview()`, add welcome audio pre-load block after `addMessage` — `if (data.welcome_audio_url) { new Audio(...).play().catch(...) }` with console log on autoplay block

## Phase 4: Verification

- [x] 4.1 Run `python -m pytest tests/ -v` — confirm all tests pass, no regressions
- [ ] 4.2 Manual: start `uvicorn backend.main:app --port 8000`, click "Iniciar entrevista", confirm welcome audio audible or console shows "autoplay blocked"

## Phase 5: Commit & Persist

- [x] 5.1 Stage `backend/main.py`, `frontend/app.js`, `tests/test_api.py` — commit as `feat(ux): pre-generate welcome audio for immediate playback`
- [ ] 5.2 Mark change `welcome-pregen` status as `planned` via `openspec/changes/welcome-pregen/state.yaml`
