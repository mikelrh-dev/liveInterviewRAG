# Apply progress: conversation-reports

## Status
Apply phase complete. 5 work units executed; verify returned PASS_WITH_WARNINGS; W1 (startup retention hook) and config-helper refactor (incidental quality improvement addressing 5 pre-existing lint findings on int/float env parsing) resolved before sign-off.

## TDD cycle evidence

| Work unit | RED | GREEN | Evidence |
|---|---|---|---|
| config.py additions (REPORTS_DIR, REPORT_RETENTION_DAYS) | n/a (additive) | test_config.py passes for new attrs | test_config.py: 5 passed (new + existing) |
| ReportService — generate (idempotency, format, empty-skip) | test_report_service.py: RED initially | GREEN after backend/services/report.py implementation | test_report_service.py: 17/17 passed; includes idempotency re-call test, empty-conversation skip test, format snapshot |
| ReportService — cleanup_expired (retention) | same test file (TestReportServiceCleanup class) | GREEN | cleanup sweep deletes files older than REPORT_RETENTION_DAYS; custom override respected |
| main.py hooks (farewell + eviction + sweep) | hook placement test (TestMainWiring) | GREEN | confirms generate() called AFTER messages.append and BEFORE return; never raises into SSE/cleanup |
| .gitignore reports/ | n/a | manual verification | git check-ignore reports/ -> reports/ (ignored) |

## Final suite
`./venv/Scripts/python.exe -m pytest tests/ -q` -> 219 passed, 1 failed.
The single failure is the pre-existing `tests/test_config.py::test_config_defaults` (WHISPER_COMPUTE_TYPE env-isolation bug, unrelated to this change).

## Backend integrity
`git diff --stat backend/` -> 3 files modified (.gitignore, backend/config.py, backend/main.py); 2 files created (backend/services/report.py, tests/test_report_service.py). All changes inside the agreed scope.

## Accepted deviations
- No full-SSE integration test (design §8 rationale: heavy cross-thread mocking; the no-raise guarantee + degradation contract are covered by unit tests + hook placement test)
- Pre-existing test_config_defaults failure unrelated
- `.gitignore` + `backend/config.py` host-binding `0.0.0.0` is intentional deployment topology (behind nginx same-host), documented inline with suppression comment
- Pre-existing pattern of inline `int(os.getenv(...))` replaced with `_env_int`/`_env_float` helpers as a quality improvement (named errors on invalid env values); pattern applied consistently across the Config class

## Manual items (operator)
- Task 3.3 smoke-check requires a live run with real STT/TTS to confirm reports are generated in production config; not automatable in this suite
