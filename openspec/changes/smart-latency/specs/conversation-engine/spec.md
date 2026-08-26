# Delta for Conversation Engine

## MODIFIED Requirements

### Requirement: Conversation State (MAY)

The system MAY maintain per-session conversation history to enable multi-turn coherence. When maintained, history SHALL gain optional durable backing: all conversation state operations keep identical API contracts (same endpoints, same response shapes), while persisted history survives restarts through load-on-demand hydration.
(Previously: history was in-memory only; a restart erased every session.)

#### Scenario: Multi-turn with history

- GIVEN conversation history contains previous Q&A about projects
- WHEN the user asks "Tell me more about that"
- THEN the LLM resolves "that" using conversation context
- AND provides a coherent follow-up

#### Scenario: Stateless mode

- GIVEN conversation history is not maintained
- WHEN a follow-up question references earlier context
- THEN the LLM responds based on the current query alone
- AND the response remains plausible but may lack continuity

#### Scenario: Persisted history survives restart

- GIVEN a conversation was persisted before an application restart
- WHEN the same `conversation_id` sends a new message after restart
- THEN the conversation continues seamlessly with hydrated history
- AND endpoint paths and response shapes are unchanged

### Requirement: Session TTL Eviction

The system SHALL track `last_activity_at` (UTC) on every successful write to a conversation (POST `/api/conversation`, `POST /api/conversation/{id}/message`, `POST /api/conversation/{id}/message/stream`). The system SHALL evict conversations idle longer than `SESSION_TTL_HOURS` (default 2) via a periodic background task every 15 minutes. Eviction SHALL be silent — no SSE event to clients, DEBUG log only. If `SESSION_TTL_HOURS < 0.1` at startup, the system SHALL log a warning and default to 2. Eviction SHALL additionally delete the evicted conversation's rows from the persistent store (conversation, turns, messages); report rows SHALL be retained and pruned only by the report retention job.
(Previously: eviction removed the in-memory entry only; no database rows existed.)

#### Scenario: Conversation evicted after TTL expiry

- GIVEN a conversation has `last_activity_at` older than `SESSION_TTL_HOURS`
- WHEN the periodic cleanup task runs
- THEN the conversation is removed from the in-memory store
- AND its conversation, turns, and messages rows are deleted from the database
- AND no SSE event is emitted

#### Scenario: TTL floor enforced

- GIVEN the environment sets `SESSION_TTL_HOURS=0.05`
- WHEN the system starts
- THEN a warning is logged and the effective TTL defaults to 2

#### Scenario: Reports survive eviction

- GIVEN a TTL-evicted conversation had a generated report
- WHEN eviction deletes its conversation, turns, and messages rows
- THEN the report row remains retrievable within REPORT_RETENTION_DAYS
