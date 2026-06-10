# Conversation Engine Specification

## Purpose

Orchestrate the full pipeline: STT → RAG → LLM → TTS. Manage conversation state so the AI responds AS the candidate with context-aware, voice-based answers. This is the core user-facing capability.

## Requirements

### Requirement: Pipeline Orchestration

The system SHALL process each user turn through the sequence: (1) STT transcribes audio to text, (2) RAG retrieves relevant context, (3) LLM generates response as candidate, (4) TTS converts response to audio.

#### Scenario: Full pipeline success

- GIVEN a recruiter sends a voice message asking about the candidate's experience
- WHEN the pipeline completes
- THEN the response is an audio file speaking as the candidate
- AND the response references real CV/project data from RAG

#### Scenario: Pipeline latency target

- GIVEN any user voice input
- WHEN the full pipeline executes
- THEN total latency (receive audio → return audio) is under 8 seconds

### Requirement: System Prompt

The system SHALL use a system prompt that positions the LLM as the candidate. The prompt instructs the model to answer from retrieved context, use first-person perspective, and deflect gracefully when context is insufficient.

#### Scenario: Context-aware response

- GIVEN RAG returns chunks about the candidate's React experience
- WHEN the LLM generates a response
- THEN the response uses first-person language ("I built...")
- AND references specific details from retrieved chunks

#### Scenario: Insufficient context

- GIVEN RAG returns no relevant chunks for a question
- WHEN the LLM generates a response
- THEN the response deflects honestly ("I haven't worked with that yet, but...")
- AND does NOT fabricate credentials

### Requirement: Conversation State (MAY)

The system MAY maintain per-session conversation history to enable multi-turn coherence. This is optional for MVP — each turn can be stateless.

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

### Requirement: STT Integration

The system SHALL use Faster Whisper (int8 model, CPU) for speech-to-text. Accepts audio input (webm/ogg/wav) and returns transcribed text.

#### Scenario: Audio transcription

- GIVEN a 5-second voice input from the recruiter
- WHEN STT processes the audio
- THEN transcription text is returned with reasonable accuracy
- AND processing time is under 2 seconds

#### Scenario: Silence or noise

- GIVEN an audio file with no speech content
- WHEN STT processes the audio
- THEN the system returns an empty or minimal transcription
- AND signals the conversation engine to prompt the user to retry

### Requirement: TTS Output

The system SHALL use Edge TTS to generate natural-sounding voice output. The voice SHOULD be selected to match a professional tone suitable for a candidate profile.

#### Scenario: Voice synthesis

- GIVEN an LLM-generated response text
- WHEN TTS processes the text
- THEN an audio file is returned in a web-compatible format (mp3/ogg)
- AND the voice is clear and natural-sounding

#### Scenario: Long response handling

- GIVEN an LLM response exceeding 500 words
- WHEN TTS processes the text
- THEN the full response is synthesized without truncation
- AND audio generation completes within the latency budget

### Requirement: Error Handling

The system SHALL return meaningful error states when any pipeline stage fails. The frontend SHALL display a user-friendly message and allow retry.

#### Scenario: STT failure

- GIVEN audio input that cannot be processed
- WHEN STT throws an error
- THEN the API returns HTTP 422 with a message "Could not transcribe audio"
- AND the frontend displays the error and resets the mic state

#### Scenario: LLM failure

- GIVEN the Owl API is unreachable
- WHEN the LLM stage fails
- THEN the API returns HTTP 503 with a message "Response generation temporarily unavailable"
- AND the frontend allows the user to retry after a delay
