# RAG Pipeline Specification

## Purpose

Ingest candidate documents, compute embeddings, and retrieve contextually relevant passages to feed the LLM when generating candidate responses. Enables the digital twin to answer from real CV/project data rather than hallucinating.

## Requirements

### Requirement: Document Ingestion

The system SHALL load candidate documents from the `candidate/` directory at startup. Supported formats: Markdown, plain text. Each document is chunked into passages of ~200-500 tokens with overlap.

#### Scenario: Startup ingestion

- GIVEN the `candidate/` directory contains at least one `.md` file
- WHEN the backend starts
- THEN all documents are chunked and embedded in memory
- AND ingestion logs the total chunk count

#### Scenario: Empty directory

- GIVEN the `candidate/` directory is empty or missing
- WHEN the backend starts
- THEN the system starts with an empty index
- AND logs a warning that no candidate documents were found

### Requirement: Embedding

The system SHALL compute embeddings using a lightweight sentence-transformer model (e.g., `all-MiniLM-L6-v2`). Embeddings are stored in-memory as numpy arrays. No external vector database is required for MVP.

#### Scenario: Embedding computation

- GIVEN document chunks exist from ingestion
- WHEN embeddings are computed
- THEN each chunk has a corresponding vector of fixed dimensionality
- AND total embedding time is logged

#### Scenario: Model unavailability

- GIVEN the sentence-transformer model cannot be downloaded
- WHEN the backend starts
- THEN the system SHALL fall back to TF-IDF vectors
- AND log a warning about degraded retrieval quality

### Requirement: Context Retrieval

The system SHALL accept a query string and return the top-K most relevant chunks (default K=3) ranked by cosine similarity.

#### Scenario: Relevant context found

- GIVEN the index contains candidate project chunks
- WHEN a query about "Tell me about your portfolio project" is received
- THEN the system returns 1-3 chunks with similarity scores > 0.3
- AND chunks are ordered by descending similarity

#### Scenario: No relevant context

- GIVEN a query unrelated to the candidate's data
- WHEN retrieval is performed
- THEN the system returns an empty list or scores below threshold
- AND signals the conversation engine to use fallback behavior

### Requirement: Latency Budget

Embedding + retrieval SHALL complete within 500ms on CPU for the MVP document set (<50 documents).

#### Scenario: Performance within budget

- GIVEN 50 document chunks are indexed
- WHEN a retrieval query is processed
- THEN total embedding + retrieval time is under 500ms
