# Wiki Compile Specification

## Purpose

Compile the Markdown wiki directory (`wiki/`) into the flat file structure (`candidate/`) that the existing backend ingests. Transforms frontmatter and narratives into `candidate/profile.json` plus eight `candidate/docs/*.md` files using deterministic concatenation with heading boundaries. Enforces validation gates before any write to prevent partial or corrupt output.

## Requirements

### Requirement: Deterministic Compilation

The system SHALL walk all eight wiki type folders, validate each file's frontmatter, strip frontmatter from narratives, and write compiled output to `candidate/`. Per-type files SHALL be concatenated in alphabetical order by filename, each becoming a `## <title>` section derived from frontmatter.

#### Scenario: Complete wiki compile

- GIVEN a wiki with at least one file in each of the eight type folders, all with valid frontmatter
- WHEN `compile.py` is executed
- THEN `candidate/profile.json` is created with valid JSON containing `name`, `title`, `summary`, `skills` (array), `experience` (array with role/company/period/highlights), and `stories` (array with situation/task/action/result)
- AND eight `candidate/docs/*.md` files are created with per-type concatenated narratives
- AND each narrative section begins with a `## <title>` heading derived from the source file's title

#### Scenario: Concatenation order is alphabetical

- GIVEN a wiki type folder contains three files: `b-file.md`, `a-file.md`, `c-file.md`
- WHEN `compile.py` processes that folder
- THEN the output `docs/*.md` file contains sections in alphabetical filename order: `a-file.md`, `b-file.md`, `c-file.md`

### Requirement: Atomic Write Semantics

The system SHALL compile to a temporary directory and only rename to `candidate/` upon successful completion. Any validation failure SHALL abort before modifying `candidate/`.

#### Scenario: Validation failure protects candidate

- GIVEN `candidate/` contains known-good compiled data
- AND a file in `wiki/` has invalid frontmatter
- WHEN `compile.py` is executed
- THEN the existing `candidate/` content remains unchanged
- AND the system exits with a non-zero exit code

### Requirement: Frontmatter Enforcement

The system SHALL validate each file's frontmatter during compile. Files with a `type` that does not match their parent folder SHALL be silently skipped. Broken `related:` links SHALL produce a warning but not fail compilation.

#### Scenario: Type-folder mismatch skipped

- GIVEN a file `wiki/stories/leadership-story.md` has frontmatter `type: project`
- WHEN `compile.py` runs
- THEN that file is excluded from compilation
- AND the remaining valid files are compiled normally
- AND a log message identifies the skipped file and reason

#### Scenario: Broken related link warns but does not fail

- GIVEN a file `wiki/projects/tts.md` has `related: [experience/nonexistent-role.md]`
- WHEN `compile.py` runs
- THEN compilation succeeds
- AND a warning is logged with the file path and broken link

### Requirement: Stale Content Warning

The system SHALL warn when a file has `confidence: low` and `updated` is within the last 7 days.

#### Scenario: Low confidence fresh file warns

- GIVEN a file `wiki/stories/recent-story.md` has `confidence: low` and `updated` set to 3 days before the current date
- WHEN `compile.py` runs
- THEN compilation succeeds
- AND a warning is logged identifying the file as potentially stale

### Requirement: Empty Wiki Handling

The system SHALL handle an empty wiki without error, producing no output files.

#### Scenario: Empty wiki produces no-op

- GIVEN all eight wiki type folders exist but contain no `.md` files
- WHEN `compile.py` is executed
- THEN the system exits with code 0
- AND `candidate/profile.json` is not written (or is an empty skeleton)
- AND no `candidate/docs/` files are created
