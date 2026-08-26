# Semantic Answer Cache Specification

## Purpose

Serve paraphrased repeats of already-answered first questions instantly by matching question embeddings against stored answers, with mandatory guardrails: first-turn-only scope, TTL expiry, FIFO row cap, kill-switch, and embedding-stability protection.

## Requirements

### Requirement: Cache Store

After a successful LLM generation on a first-substantive turn, the system SHALL store `(question_text, question_embedding float32[384], answer_text, created_at, hit_count)` in a `semantic_cache` table of the same SQLite database used by conversation persistence.

#### Scenario: First-turn answer is cached

- GIVEN a fresh conversation's first substantive question is answered successfully by the LLM
- WHEN generation completes
- THEN a semantic_cache row exists with the question text, float32[384] embedding, verbatim answer, timestamp, and hit_count=0

### Requirement: Similarity Lookup Before LLM

On first-substantive turns, before calling the LLM, the system SHALL embed the question (embedder already loaded by the RAG pipeline) and compare cosine similarity against stored rows. If the maximum similarity is ≥ 0.93, the system SHALL serve the stored answer through the SAME contract as FAQ-cache hits: verbatim answer, RAG chunks tracked for the context panel (tracked, never spoken), and the LLM never called.

#### Scenario: Paraphrased repeat served without LLM

- GIVEN a cached row whose wording is distinct enough to miss the literal FAQ cache but scores ≥ 0.93 cosine similarity against an incoming first-substantive question of the same intent
- WHEN that question arrives on a fresh conversation
- THEN the stored answer is served end-to-end in under 300ms
- AND the LLM is not invoked and RAG chunks appear in the context panel

### Requirement: Mandatory Guardrails

All guards SHALL apply: lookup/store ONLY on first substantive turns (never for turns with conversation dependency); entries expire after `SEMANTIC_CACHE_TTL_DAYS` (default 14); the table is capped at 500 rows with FIFO eviction; setting `SEMANTIC_CACHE_ENABLED=false` disables the feature entirely with zero overhead (no lookup, no store).

#### Scenario: Follow-up turn never hits cache

- GIVEN a second turn asks something textually similar to a cached question
- WHEN the turn belongs to a conversation with existing history
- THEN the cache is bypassed and the LLM generates normally

#### Scenario: Kill-switch disables entirely

- GIVEN `SEMANTIC_CACHE_ENABLED=false`
- WHEN any question is processed
- THEN no cache lookup and no cache store occur at all

#### Scenario: Expired rows unserved and FIFO cap enforced

- GIVEN a row older than SEMANTIC_CACHE_TTL_DAYS exists while the table holds 500 rows
- WHEN a lookup and then a new store execute
- THEN expired rows are never served and insertion evicts the oldest row first

### Requirement: Embedding Stability Guard

The system SHALL disable the cache automatically when sentence-transformers is unavailable — TF-IDF fallback vectors are unstable across restarts and SHALL never be stored or served. Stored rows whose embedding model/dimension does not match the active embedder SHALL be ignored during lookup.

#### Scenario: TF-IDF fallback disables cache

- GIVEN sentence-transformers failed to load and the RAG pipeline fell back to TF-IDF
- WHEN questions are processed
- THEN nothing is stored into semantic_cache and no semantic lookup occurs

#### Scenario: Model-mismatch rows ignored

- GIVEN semantic_cache contains rows produced by a different embedding model/dimension than the active embedder
- WHEN a lookup executes
- THEN mismatched rows are ignored and never served
