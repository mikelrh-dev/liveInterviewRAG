# Proposal: conversation-reports

## Why

Mock interview conversations currently vanish when the process restarts or a session is evicted.
The only durable artifacts are per-sentence audio chunks, which are neither reviewable nor greppable
and are themselves cleaned up aggressively. Mikel (the sole operator) has no way to look back at how
a mock interview actually went — what was asked, what was answered, and where answers were weak.

This change persists a plain Markdown report of each completed conversation so the operator can
review transcripts after the fact via SSH/scp. It is deliberately low-tech: raw transcript dump,
no LLM processing, no UI, no HTTP read endpoints.

**Explicitly NOT:** recruiter-facing, public-facing, or a product feature for end users. This is
operator tooling for a single-user system.

## Approved design decisions (from brainstorming — binding requirements)

1. **Trigger** — report generation fires on whichever happens first:
   - farewell detection (existing `detect_farewell` branch in the streaming endpoint, `main.py`),
   - session TTL eviction in the existing sweep (`periodic_cleanup`, driven by `SESSION_TTL_HOURS=2`).
2. **Content** — RAW transcript only, zero LLM calls:
   - Header metadata: date, duration (`last_activity_at − created_at`), turn count.
   - Turns rendered as `**Reclutador:** {user_text}` / `**Gemelo:** {response_text}`.
3. **Storage** — new `REPORTS_DIR` in `backend/config.py` resolving to `<project>/reports/`;
   layout `reports/{conversation_id}/{ISO-timestamp}.md` (mirrors the existing `audio/{conversation_id}/`
   pattern). Generation is idempotent — an existing report file is never regenerated or overwritten.
4. **Retention** — 30-day retention via `cleanup_expired_reports()` wired into the SAME startup +
   periodic sweep mechanism that already cleans stale audio (`cleanup_stale_audio` call sites).
5. **Architecture** — new `backend/services/report.py` exposing a `ReportService` following existing
   service module patterns (`backend/services/*.py`). Hooks are added at exactly two places in
   `main.py`: the farewell branch and the TTL eviction loop. Graceful degradation is mandatory:
   any report-write failure logs a warning and must NEVER break the streaming response or the
   cleanup loop.
6. **Out of scope** — HTTP endpoints to read reports (SSH/scp access only), LLM processing,
   UI, git tracking (`reports/` added to `.gitignore`).

## Scope

### In scope

- `backend/config.py`: add `REPORTS_DIR = BASE_DIR / "reports"` (+ optional env override, consistent
  with other path settings) and a `REPORT_RETENTION_DAYS` constant (default 30).
- New `backend/services/report.py`: `ReportService` with
  - `generate(conversation_state) -> Path | None` — renders Markdown from the in-memory
    `conversations[cid]` dict (`messages` list: `user_text` / `response_text`; plus `created_at`,
    `last_activity_at`, turn count), writes `{REPORTS_DIR}/{cid}/{timestamp}.md`;
  - idempotency guard (skip if any `.md` already exists for that `conversation_id`);
  - `cleanup_expired(days)` — delete report files older than the retention window;
  - all filesystem work wrapped defensively (log-and-return on failure).
- `backend/main.py`:
  - call `report_service.generate(...)` in the farewell branch (post-response-append, before `return`);
  - call `generate(...)` for each evicted conversation in `periodic_cleanup` before deletion;
  - call `cleanup_expired_reports()` alongside `cleanup_stale_audio()` in startup and the sweep;
  - ensure `REPORTS_DIR.mkdir(parents=True, exist_ok=True)` at startup next to `AUDIO_DIR`.
- `.gitignore`: add `reports/`.
- Tests (strict TDD, `python -m pytest tests/ -v`, venv interpreter): unit tests for `ReportService`
  (rendering, header fields, idempotency, empty-conversation edge case, write-failure degradation)
  and tests asserting the hooks fire on farewell + TTL eviction without breaking the SSE stream.

### Out of scope (non-goals)

- Any HTTP endpoint to list/read reports.
- LLM summarization, scoring, or enrichment of transcripts.
- Frontend/UI changes of any kind.
- Changing conversation-engine behavior: reporting is post-hoc, off the hot path; prompts, RAG,
  TTS, caching, and rate limiting are untouched.
- Report formats other than Markdown; compression/archiving.

## Affected capability

- New capability domain: `conversation-report`.
- `conversation-engine`: behavior unchanged (two additive post-hoc hook calls only); spec deltas
  should be scoped so the engine's existing requirements are not modified.

## Risks

| Risk | Mitigation |
| --- | --- |
| Report write failure corrupts streaming response or cleanup loop | All writes wrapped in try/except → warning log, never raise into caller paths (approved decision #5); unit-tested |
| TTL-eviction path generates report from partially-transcribed sessions | Accepted by design: raw transcript is still valuable; header shows real duration/turn count |
| Farewell fires but process crashes before flush | Idempotent regeneration on TTL sweep would cover it only if conversation survives; residual risk accepted (single operator, low stakes) |
| Disk growth if interviews are frequent | 30-day retention sweep bounds growth; reports are tiny text files |
| Concurrent farewell + eviction double-generate | Idempotency guard (skip when a report exists for the cid); worst case duplicate timestamped files, acceptable |
| Non-ASCII Spanish text in transcripts | Files written UTF-8 explicitly |

## Rollback plan

Low-risk, purely additive change. Rollback = revert the change set:

- Remove `report.py`, the two hook calls in `main.py`, the cleanup wiring, `REPORTS_DIR` config,
  and the `.gitignore` entry.
- No data migrations, no schema changes, no API surface changes — nothing else depends on reports.
- Existing `reports/` directories can be deleted manually without affecting the running system.

## Success criteria

1. A conversation ended via farewell produces exactly one `reports/{cid}/*.md` containing header
   metadata (date, duration, turn count) and every exchange as `**Reclutador:**` / `**Gemelo:**` lines.
2. A conversation evicted by the TTL sweep (never said goodbye) also produces its report.
3. Re-triggering generation for the same conversation does not rewrite or duplicate content
   (idempotent).
4. Reports older than 30 days are removed by the sweep; live conversations and their audio are
   unaffected.
5. Simulated disk/write failure during report generation logs a warning and the streaming response
   completes normally.
6. Full test suite passes: `python -m pytest tests/ -v`.
7. `git status` shows no `reports/` output (`.gitignore` effective).

## Proposal question round

Superseded: all product questions were resolved and approved during brainstorming (trigger policy,
content fidelity, storage layout, retention, architecture, and non-goals listed above). Residual
assumptions carried forward for end-of-loop review:

- **A1:** Duration is computed as `last_activity_at − created_at` (wall-clock session length),
  not cumulative speech time.
- **A2:** Turn source is the `messages` list (`user_text`/`response_text`), which includes the
  farewell exchange; the richer `turns` structure (`chunks_used`) is not rendered.
- **A3:** A TTL-evicted conversation with zero messages still yields a minimal header-only report
  rather than being skipped — cheap, and makes gaps visible. If undesired, spec can require
  skipping empty conversations.
