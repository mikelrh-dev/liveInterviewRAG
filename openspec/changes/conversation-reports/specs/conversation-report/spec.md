# Conversation Report Specification

## Purpose

Persist each completed mock interview as a plain Markdown transcript under `reports/` for operator
review via SSH/scp. No HTTP endpoints, no LLM processing, no UI.

## Requirements

### Requirement: Report Generation Trigger

The system SHALL generate exactly one Markdown report per conversation, triggered by whichever
happens first: farewell detection in the streaming endpoint or TTL eviction in `periodic_cleanup`.
Both triggers SHALL call the same `ReportService` entry point. Generation is post-hoc and MUST NOT
alter conversation-engine behavior.

#### Scenario: Farewell ends the conversation

- GIVEN an active conversation with messages
- WHEN the farewell branch fires in the streaming endpoint
- THEN a report is generated after the response append and the SSE stream completes normally

#### Scenario: Session evicted by TTL sweep without farewell

- GIVEN a conversation idle past `SESSION_TTL_HOURS` that never said goodbye
- WHEN `periodic_cleanup` evicts it
- THEN a report is generated before the conversation state is deleted

### Requirement: Report Content

Reports SHALL be raw transcripts rendered from the in-memory `messages` list (`user_text` /
`response_text`) with zero LLM calls. Each report SHALL contain a header with date, duration
(`last_activity_at − created_at`), and turn count, followed by every exchange rendered as
`**Reclutador:** {user_text}` or `**Gemelo:** {response_text}`. Files SHALL be UTF-8.

#### Scenario: Full transcript rendering

- GIVEN a conversation with 4 message turns in Spanish
- WHEN the report is generated
- THEN the header shows date, wall-clock duration, and turn count of 4
- AND every exchange appears as a `**Reclutador:**` or `**Gemelo:**` line with original text intact

#### Scenario: Empty conversation is skipped

- GIVEN a TTL-evicted conversation whose `messages` list is empty
- WHEN the eviction hook invokes report generation
- THEN no report file is written for that conversation

### Requirement: Storage Layout and Idempotency

The system SHALL store reports under `REPORTS_DIR` (`<project>/reports/`, env-overridable) using
`{REPORTS_DIR}/{conversation_id}/{ISO-timestamp}.md`, mirroring the `audio/` pattern. Generation
SHALL be idempotent: if any `.md` exists for the `conversation_id`, it SHALL NOT be regenerated.

#### Scenario: Re-triggered generation is a no-op

- GIVEN a report already exists for conversation `abc123`
- WHEN generation is triggered again for `abc123`
- THEN the existing file is untouched and no duplicate content is written

### Requirement: Graceful Degradation

Report write failures SHALL log a warning and SHALL NOT propagate exceptions into the SSE streaming
response or the periodic cleanup loop.

#### Scenario: Write failure during streaming

- GIVEN the filesystem write fails during report generation in the farewell branch
- WHEN the exception is caught
- THEN a warning is logged and the SSE stream finishes normally

#### Scenario: Write failure during cleanup

- GIVEN report generation raises inside the TTL eviction loop
- WHEN the exception is caught
- THEN a warning is logged and remaining evictions and audio cleanup still execute

### Requirement: Report Retention

The system SHALL delete reports older than 30 days (`REPORT_RETENTION_DAYS`) via
`cleanup_expired_reports()`, invoked by the same startup + periodic sweep that runs
`cleanup_stale_audio()`.

#### Scenario: Expired reports pruned

- GIVEN a report file older than 30 days exists
- WHEN the cleanup sweep runs
- THEN the expired report is deleted and live conversations and audio are unaffected

### Requirement: Local-Only Access

Reports SHALL be local-to-VPS artifacts only. The system SHALL NOT expose any HTTP endpoint listing
or serving reports, and `reports/` SHALL be excluded from version control via `.gitignore`.

#### Scenario: No HTTP surface for reports

- GIVEN a report exists on disk
- WHEN any API route is called
- THEN no route returns report contents or directory listings

#### Scenario: Reports ignored by git

- GIVEN `.gitignore` contains `reports/`
- WHEN `git status` runs after reports are written
- THEN no `reports/` paths appear
