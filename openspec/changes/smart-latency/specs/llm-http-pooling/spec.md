# LLM HTTP Pooling Specification

## Purpose

Eliminate per-call TCP+TLS handshake overhead (~200–500ms) on every LLM provider call by routing all four call sites through one shared, thread-safe HTTP client with identical timeouts and error behavior to the current implementation.

## Requirements

### Requirement: Shared Client Reuse

The system SHALL route all LLM provider HTTP calls — OpenRouter generate/stream and Google AI generate/stream — through one shared, thread-safe `httpx.Client`. No call site SHALL construct a new HTTP client per request. Request timeouts SHALL be identical to current behavior (60s).

#### Scenario: Consecutive calls reuse one client

- GIVEN two consecutive LLM generate calls across different requests
- WHEN both calls execute
- THEN the shared client factory returns the same client instance for both
- AND no new client object is constructed for the second call (verifiable via factory spy)

#### Scenario: Timeouts unchanged

- GIVEN any LLM provider call (generate or stream, either provider)
- WHEN the request is dispatched
- THEN the effective timeout is 60 seconds, matching pre-change behavior

### Requirement: Shutdown Closing

The application SHALL close the shared client exactly once during app shutdown (lifespan hook), without breaking in-flight calls that started before shutdown began.

#### Scenario: Lifespan shutdown closes client once

- GIVEN the app has been running with the shared client initialized
- WHEN the lifespan shutdown hook runs
- THEN the client is closed exactly once, even if no calls were made or several were made

#### Scenario: In-flight call survives shutdown start

- GIVEN an LLM streaming response is mid-flight when shutdown starts
- WHEN shutdown proceeds to close the shared client
- THEN the in-flight response completes or terminates gracefully without an unhandled exception

### Requirement: Error Behavior Parity

Error paths SHALL produce messages identical to current behavior: provider HTTP 429 responses raise the same per-provider rate-limit messages as today; timeouts and other failures raise "Response generation temporarily unavailable..." messages as today.

#### Scenario: Rate-limit message unchanged

- GIVEN a provider responds HTTP 429
- WHEN the call fails
- THEN the raised error message matches the current per-provider rate-limit text exactly

#### Scenario: Timeout/unavailable message unchanged

- GIVEN a provider times out or returns a non-429 error status
- WHEN the call fails
- THEN the raised error message matches the current "Response generation temporarily unavailable" pattern exactly
