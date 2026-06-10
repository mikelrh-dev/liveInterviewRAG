# Candidate Profile Specification

## Purpose

Define and load a structured representation of the candidate's professional identity — CV sections, project descriptions, key stories, and skills. This data feeds the RAG pipeline and shapes the LLM's persona as the digital twin.

## Requirements

### Requirement: Profile Schema

The system SHALL define candidate data as Markdown files in the `candidate/` directory. Each file represents a domain section (e.g., `cv.md`, `projects.md`, `skills.md`, `stories.md`).

#### Scenario: Schema loading

- GIVEN `candidate/cv.md`, `candidate/projects.md`, `candidate/skills.md` exist
- WHEN the backend starts
- THEN each file is loaded as a document for RAG ingestion
- AND file names are logged for verification

#### Scenario: Missing optional sections

- GIVEN `candidate/stories.md` does not exist
- WHEN the backend starts
- THEN the system starts without stories context
- AND logs which sections were found vs missing

### Requirement: Document Structure

Each candidate Markdown file SHOULD use structured headings (`#`, `##`) to delineate sections. The system SHALL treat each heading-level section as a logical chunk boundary during ingestion.

#### Scenario: Well-structured document

- GIVEN `candidate/cv.md` has `## Experience` and `## Education` sections
- WHEN chunks are created
- THEN each section produces at least one chunk preserving section context

#### Scenario: Flat document

- GIVEN a document with no sub-headings
- WHEN chunks are created
- THEN the document is split by token count with overlap

### Requirement: Content Quality

Candidate documents SHOULD contain concrete details: project names, technologies used, measurable outcomes, specific stories. Generic placeholder content degrades retrieval quality.

#### Scenario: Rich content retrieval

- GIVEN `candidate/projects.md` describes "InterviewTTS — voice AI portfolio" with tech stack details
- WHEN a query asks "What technologies did you use in your portfolio project?"
- THEN retrieved chunks include specific technology names and context

#### Scenario: Sparse content retrieval

- GIVEN a document contains only bullet-point skill lists with no context
- WHEN a query asks for a detailed explanation
- THEN retrieved chunks lack depth and the conversation engine signals fallback

### Requirement: Hot Reload (MAY)

The system MAY support reloading candidate documents without restarting. This is optional for MVP but supports rapid iteration.

#### Scenario: Document update detected

- GIVEN a candidate file is modified while the server is running
- WHEN reload is triggered (manual endpoint or file watcher)
- THEN embeddings are recomputed for changed chunks only
- AND retrieval uses the updated index
