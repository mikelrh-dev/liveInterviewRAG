# Tasks — conversation-reports

## Review Workload Forecast

| Field | Value |
| ------- | ------- |
| Estimated changed lines | ~250–300 (service ~90, hooks/config/gitignore ~25, tests ~140) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | pending |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low
```

Test command (all phases): `./venv/Scripts/python.exe -m pytest tests/ -v`
Zero unexpected diffs outside: `backend/main.py`, `backend/config.py`, `backend/services/report.py` (new), `.gitignore`, `tests/`.

---

## Phase 1 — Scaffold: config + gitignore

### 1.1 RED — config tests for REPORTS_DIR / REPORT_RETENTION_DAYS

- File: `tests/test_report_service.py` (new; config tests may live here).
- Tests: monkeypatched env `REPORTS_DIR` / `REPORT_RETENTION_DAYS` → re-instantiated `Config()` picks them up; defaults are `<BASE_DIR>/reports` (a `Path`) and `30`.
- Verify: run suite → new tests fail (RED), all existing tests pass.

### 1.2 GREEN — add settings in `backend/config.py`

- Append to Paths section (after `FRONTEND_DIR`, design §6):
  `self.REPORTS_DIR: Path = Path(os.getenv("REPORTS_DIR", str(self.BASE_DIR / "reports")))`
- Add retention constant:
  `self.REPORT_RETENTION_DAYS: int = int(os.getenv("REPORT_RETENTION_DAYS", "30"))`
- Verify: Phase 1.1 tests pass; full suite green.

### 1.3 `.gitignore` entry

- File: `.gitignore` — append next to existing `audio/` block (design §7):

  ```gitignore
  # Generated interview transcripts (operator-only, personal data)
  reports/
  ```

- Verify: `git status` clean after creating a stray `reports/x/y.md`; no new tracked paths.
- Rollback boundary: revert `config.py` hunk + `.gitignore` line; nothing else depends on them yet.

---

## Phase 2 — ReportService TDD (`backend/services/report.py`, new)

Work unit boundary: service module only — no `main.py` edits in this phase.

### 2.1 RED — 10 unit tests from design §8

- File: `tests/test_report_service.py`; construct `ReportService(output_dir=tmp_path, retention_days=30)` directly (no Config singleton import).
- Test list (design §8 table):
  1. `test_generate_writes_markdown_file` — returns a `Path`; file exists at `{tmp}/abc/*.md`
  2. `test_report_header_fields` — date, `Duración:` (HH:MM:SS from `last_activity_at − created_at`), correct `Turnos:` count
  3. `test_full_transcript_rendering` — 4-turn Spanish fixture rendered verbatim as `**Reclutador:** …` / `**Gemelo:** …`, accents intact (UTF-8)
  4. `test_idempotent_regeneration_noop` — second call returns `None`, content unchanged, exactly one `.md`
  5. `test_empty_conversation_skipped` — `messages: []` → `None`, nothing written (spec-binding, supersedes A3)
  6. `test_none_state_skipped` — `generate("x", None)` → `None`, no raise
  7. `test_write_failure_degrades` — forced write failure → returns `None`, warning logged via `caplog`, no exception escapes
  8. `test_cleanup_expired_deletes_old_only` — seed two `.md`, set mtimes via `os.utime`; fresh survives, expired removed, return `1`
  9. `test_cleanup_custom_days_override` — `days=0` deletes fresh reports; default uses ctor `retention_days`
  10. `test_cleanup_never_raises` — nonexistent/unreadable dir → returns `0`, no exception
- Fixture state shape (exact keys from `create_conversation`, main.py ~330): `messages` (`user_text`/`response_text`), `created_at`, `last_activity_at` as ISO strings. Do not render `turns`/`summary`.
- Verify: run → import error / all fail (RED).

### 2.2 GREEN — implement `ReportService`

- File: `backend/services/report.py` (new).
- Public interface (design §3, authoritative):
  - `__init__(self, output_dir: str | Path, retention_days: int = 30)` — plain values, matches sibling services.
  - `generate(self, conversation_id: str, conversation_state: dict | None) -> Path | None` — never raises.
    - Skip rules in order: `None` state → skip; empty `messages` → skip; any existing `(output_dir/cid)/*.md` → idempotent no-op.
    - Filename: `datetime.utcnow().strftime("%Y-%m-%dT%H%M%S")` + `.md`; `mkdir(parents=True, exist_ok=True)` only after idempotency check.
    - Render exact format from design §3 (header: Fecha/Duración/Turnos, then turn pairs separated by `---`); duration parse failure → `"desconocida"`.
    - UTF-8 explicit: `open(path, "w", encoding="utf-8")`.
  - `cleanup_expired(self, days: int | None = None) -> int` — prune `*.md` older than window by mtime (`rglob`), mirroring `cleanup_stale_audio`; default `self.retention_days`; never raises.
- All filesystem ops wrapped `try/except Exception` → `logger.warning(...)` → return `None` / partial count. Module-level `logger`. No custom exceptions.
- Verify: Phase 2.1 all 10 pass; full suite green.

### 2.3 REFACTOR — style pass on `report.py`

- Confirm house-style parity with `backend/services/tts.py` etc.: docstrings, type hints, log call style.
- Verify: full suite still green; diff confined to `backend/services/report.py`.

Rollback boundary: delete `report.py` + its test file; zero coupling elsewhere so far.

---

## Phase 3 — Wire `main.py` hooks (design §5.1–5.5)

All edits additive; no existing statement reordered except where noted.

### 3.1 RED — hook wiring tests

- File: `tests/test_report_service.py` (integration section, light mocking per design §8):
  - **Farewell flow:** build minimal `conversations[cid]` state directly in `backend.main`'s namespace; assert `detect_farewell("gracias, eso es todo")` is truthy; simulate the branch tail calling wired `report_service.generate(...)` with `config.REPORTS_DIR` monkeypatched to `tmp_path` → report exists containing the farewell exchange (proves post-append placement).
  - **Eviction ordering:** stubbed `report_service` recording call order against the §5.4 code-path shape → assert `generate(cid, ...)` called before `del conversations[cid]`.
- Note: CRITICAL placement assertion — farewell test's state must include the farewell message appended before `generate()` runs.
- Verify: RED (hooks don't exist).

### 3.2 GREEN — five additive edits in `backend/main.py`

1. **§5.1 Instantiation** (~lines 100–115): `from backend.services.report import ReportService`; `report_service = ReportService(output_dir=config.REPORTS_DIR, retention_days=config.REPORT_RETENTION_DAYS)` next to other service constructions.
2. **§5.2 Startup** in `lifespan`: `config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)` next to the `AUDIO_DIR` mkdir (~line 147); `report_service.cleanup_expired()` after `cleanup_stale_audio()` (~line 150).
3. **§5.3 Farewell branch** (~lines 577–595): insert `report_service.generate(conversation_id, conversations.get(conversation_id))` AFTER the farewell `messages.append({...})` closing paren, immediately BEFORE the branch's `return`. Use `.get()` (already-evicted session → `None` state → clean skip). Comment: must never break the SSE stream.
4. **§5.4 TTL eviction** (~lines 190–202): insert `generate` between the eviction log line and `del conversations[cid]`, wrapped in defense-in-depth try/except → warning log.
5. **§5.5 Sweep cleanup** (~line 213): `try: report_service.cleanup_expired() except Exception → logger.error("Report cleanup failed: %s", e)` alongside the existing `cleanup_stale_audio()` try/except.

- Verify: Phase 3.1 tests pass.

### 3.3 Manual smoke of both triggers

- Farewell path: run app, complete a short conversation ending in "gracias, eso es todo" → confirm exactly one `reports/{cid}/*.md` with header + farewell turn.
- Idempotency: re-trigger generation for same cid → file untouched.
- Verify: success criteria #1–#3 of proposal observable on disk.

Rollback boundary: remove the five edit regions; service + tests remain inert.

---

## Phase 4 — Verification & diff hygiene

### 4.1 Targeted suite

- `./venv/Scripts/python.exe -m pytest tests/test_report_service.py -v` → all pass.

### 4.2 Full suite + diff audit

- `./venv/Scripts/python.exe -m pytest tests/ -v` → zero failures (success criterion #6).
- `git status` / `git diff --stat`: changed files limited to `backend/main.py`, `backend/config.py`, `backend/services/report.py`, `.gitignore`, `tests/`; `reports/` absent from git output (criterion #7).
- If any unexpected diff appears outside that allowlist: stop, fix or revert before proceeding.

---

## Phase 5 — Documentation

### 5.1 README note (brief)

- File: `README.md` — one short paragraph under an operations/deployment section if one exists: interviews are persisted as Markdown transcripts under `reports/{conversation_id}/` for operator review via SSH/scp; 30-day retention (`REPORT_RETENTION_DAYS`), env-overridable location (`REPORTS_DIR`); no HTTP access. Skip if README structure makes it awkward — this is optional polish.
- Verify: docs-only diff; suite unaffected.
