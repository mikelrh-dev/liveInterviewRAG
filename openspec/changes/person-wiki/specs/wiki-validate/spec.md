# Wiki Validate Specification

## Purpose

Provide standalone frontmatter validation and graph symmetry checking for the person wiki. Enforces the eight-field frontmatter schema, verifies `type` matches the parent folder, and resolves all `related:` links. Designed as an optional pre-compile or pre-commit gate that exits with a clear code and structured error report.

## Requirements

### Requirement: Standalone Execution

The system SHALL run standalone via `python scripts/wiki/validate.py` with no prior setup, configuration, or data files required beyond `wiki/` itself.

#### Scenario: Direct invocation

- GIVEN the `wiki/` directory exists with files
- WHEN `python scripts/wiki/validate.py` is executed
- THEN validation runs and produces output (exit code + report) without requiring any other setup

### Requirement: Known-Good Validation

The system SHALL exit with code 0 and no errors when all wiki files meet the frontmatter schema.

#### Scenario: All files pass

- GIVEN all files in `wiki/` have valid frontmatter with all eight required fields, correct `type`-folder match, and resolvable `related:` links
- WHEN `validate.py` is executed
- THEN the process exits with code 0
- AND prints a success report or no output

### Requirement: Missing Frontmatter Fields

The system SHALL detect and report files with missing required frontmatter fields, exiting with code 1.

#### Scenario: Missing field reported

- GIVEN a file `wiki/stories/a-story.md` lacks the `summary_1line` field in its frontmatter
- WHEN `validate.py` is executed
- THEN the process exits with code 1
- AND the error output names the file path and the missing field name

### Requirement: Invalid Type Enforcement

The system SHALL reject files whose `type` is not one of the eight valid values (`profile`, `project`, `experience`, `skills`, `story`, `opinion`, `decision`, `faq`) or whose `type` does not match the parent folder name.

#### Scenario: Invalid type value rejected

- GIVEN a file `wiki/projects/some-project.md` has frontmatter `type: hobby`
- WHEN `validate.py` is executed
- THEN the process exits with code 1
- AND the error output identifies the file and the invalid type value

#### Scenario: Type-folder mismatch rejected

- GIVEN a file `wiki/stories/a-story.md` has frontmatter `type: project`
- WHEN `validate.py` is executed
- THEN the process exits with code 1
- AND the error output identifies the file and the mismatch

### Requirement: Link Resolution

The system SHALL verify every entry in each file's `related:` array points to an existing file in `wiki/`. Missing targets SHALL produce an error (exit 1). Missing reciprocal links SHOULD produce a warning (exit 0).

#### Scenario: Broken link causes error

- GIVEN a file `wiki/profile/mikel.md` has `related: [stories/nonexistent.md]`
- WHEN `validate.py` is executed
- THEN the process exits with code 1
- AND the error report includes the file path and the unresolvable link target

#### Scenario: Missing reciprocal linked warned

- GIVEN file `wiki/projects/tts.md` lists `related: [experience/acme.md]`
- AND `wiki/experience/acme.md` does not list `projects/tts.md` in its `related:`
- WHEN `validate.py` is executed
- THEN the process exits with code 0
- AND a warning is printed recommending the missing reciprocal link
