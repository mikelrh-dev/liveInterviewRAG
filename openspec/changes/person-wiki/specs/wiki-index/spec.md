# Wiki Index Specification

## Purpose

Generate `wiki/index.md`, an auto-generated table of contents for the person wiki. Produces one markdown table per wiki type with file metadata (title, tags, updated, confidence, summary) sorted by recency. The index is the primary browsing surface in Obsidian and is regenerated unconditionally — manual edits are overwritten.

## Requirements

### Requirement: Index Generation

The system SHALL produce `wiki/index.md` with one markdown table per wiki type containing rows for each file. Each row SHALL include the file title as a wikilink, tags, updated date, confidence level, and summary.

#### Scenario: Full index produced

- GIVEN `wiki/` contains files across multiple type folders, each with valid frontmatter
- WHEN `generate_index.py` is executed
- THEN `wiki/index.md` is created with one `## <Type>` table section per type that has files
- AND each table has columns: title, tags, updated, confidence, summary
- AND each title cell is a wikilink pointing to the source file (e.g., `[[projects/interview-tts]]`)

#### Scenario: Records sorted by recency

- GIVEN three files in `wiki/stories/` with updated dates `2026-06-10`, `2026-06-01`, `2026-05-20`
- WHEN `generate_index.py` is executed
- THEN the stories table lists them in descending updated order (most recent first)

### Requirement: Empty Wiki Handling

The system SHALL handle an empty wiki gracefully, producing an index with empty tables or placeholder notices.

#### Scenario: Empty wiki yields clean index

- GIVEN all eight wiki type folders exist but contain no `.md` files
- WHEN `generate_index.py` is executed
- THEN `wiki/index.md` is created with empty table sections or a "no files" note per type
- AND the process exits with code 0

### Requirement: Idempotent Output

The system SHALL produce byte-identical output across repeated runs on the same wiki content.

#### Scenario: Repeated runs produce same output

- GIVEN the wiki content has not changed
- WHEN `generate_index.py` is executed twice
- THEN the second `wiki/index.md` is byte-identical to the first

### Requirement: Manual Edit Protection

The system SHALL overwrite `wiki/index.md` unconditionally. Manual edits to `wiki/index.md` SHALL NOT be preserved across a subsequent `generate_index.py` run.

#### Scenario: Manual edits overwritten

- GIVEN `wiki/index.md` has been manually edited with custom content
- WHEN `generate_index.py` is executed
- THEN the manual content is fully replaced by auto-generated output
- AND the process does not warn or prompt for confirmation
