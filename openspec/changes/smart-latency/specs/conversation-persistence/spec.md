# Conversation Persistence Specification

## Purpose

Make conversations, turns, messages, and reports durable across restarts using a stdlib-only SQLite store (`data/interviewtts.db`, WAL mode, directory auto-created) with write-through persistence, load-on-demand hydration, retention-aware eviction, and strict failure isolation from the live pipeline.

## Requirements

### Requirement: Write-Through Persistence

The system SHALL persist every conversation create, turn append, message append, and report generation event to `data/interviewtts.db` as it happens (write-through). The data directory SHALL be auto-created. The database SHALL use SQLite in WAL mode. Implementation SHALL use only the Python standard library `sqlite3` — zero new third-party dependencies.

#### Scenario: Conversation creation persists

- GIVEN a recruiter creates a conversation via POST /api/conversation
- WHEN the request succeeds
- THEN a matching conversation row exists in the database immediately after the response

#### Scenario: Message append persists

- GIVEN an existing persisted conversation receives a new voice message
- WHEN the turn completes
- THEN corresponding turn and message rows are written to the database

### Requirement: Load-on-Demand Hydration

When an unknown-but-persisted `conversation_id` arrives after restart, the system SHALL hydrate that conversation from the database into memory and continue seamlessly instead of returning 404. Memory and database state SHALL remain consistent after hydration.

#### Scenario: Restart mid-interview continues seamlessly

- GIVEN a conversation with prior turns exists in the database but not in memory (post-restart)
- WHEN a new message arrives for that `conversation_id`
- THEN history is hydrated from the database and the reply accounts for prior context
- AND endpoints and response shapes are unchanged from pre-restart behavior

### Requirement: Crash Safety

A process crash during a database write SHALL leave the database readable: committed transactions remain intact and partial writes are rolled back by SQLite WAL recovery.

#### Scenario: Hard kill during write keeps DB readable

- GIVEN a write transaction is in progress
- WHEN the process is killed abruptly (kill -9) and the app restarts
- THEN the database opens successfully with previously committed rows intact and readable

### Requirement: Retention and Report Survival

TTL eviction SHALL delete evicted conversations' in-memory entries AND their conversation/turn/message database rows. Report rows SHALL SURVIVE eviction and be retained for `REPORT_RETENTION_DAYS` (default 30), pruned by the cleanup job thereafter.

#### Scenario: Report retrievable after eviction

- GIVEN a report was generated for a conversation later TTL-evicted
- WHEN the report is requested after eviction
- THEN the report content is still retrievable

#### Scenario: Evicted conversation rows removed

- GIVEN a conversation is TTL-evicted
- WHEN eviction completes
- THEN its memory entry and conversation/turn/message rows are gone
- AND its report row remains

### Requirement: Failure Isolation

Persistence failures SHALL NEVER break the live pipeline: the system SHALL log the failure and continue serving the request normally.

#### Scenario: Database error does not fail the SSE turn

- GIVEN the database raises an error on a persistence write
- WHEN a streaming message turn completes
- THEN the SSE response still delivers the full successful answer
- AND the failure is logged without surfacing an error to the client
