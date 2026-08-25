# Wiki Pipeline Specification

## Purpose

Define the local tooling that turns authored wiki content (`wiki/`, per `wiki/CONVENCIONES.md`) into deployable `candidate/` output: a blocking validation gate, an atomic idempotent compiler, and an index generator.

## Requirements

### Requirement: Blocking validation gate

`validate.py` SHALL be read-only and SHALL enforce the CONVENCIONES frontmatter schema as errors: missing required fields, `type:` not matching the parent folder (singular type ↔ plural folder mapping), malformed dates, and oversized `summary_1line`. Any broken `related:` link SHALL also be an error. On any error the script SHALL exit non-zero with actionable messages and MUST NOT write anything.

#### Scenario: Broken related link aborts

- GIVEN a wiki file whose `related:` points to a nonexistent path
- WHEN `python scripts/wiki/validate.py` runs
- THEN it exits non-zero naming the offending file and link, and no file anywhere is modified

#### Scenario: Valid wiki passes clean

- GIVEN a wiki conforming to `wiki/CONVENCIONES.md`
- WHEN validate runs
- THEN it exits 0 having written nothing

### Requirement: Warnings are non-blocking

Reciprocal-link asymmetry (`A relates to B` but not vice versa) and stale low-confidence files (`confidence: low` older than 7 days) SHALL produce warnings only; they MUST NOT cause a non-zero exit.

#### Scenario: Asymmetric links still pass

- GIVEN two files where only one declares the reciprocal `related:` entry
- WHEN validate or compile runs
- THEN a warning is printed and the pipeline continues successfully

### Requirement: Atomic idempotent compilation

`compile.py` SHALL build the full new `candidate/` in a temporary directory and swap it in via `os.replace()`-style atomic replacement. Output SHALL be byte-stable across consecutive runs. If validation fails, compile SHALL emit nothing — `candidate/` retains its previous complete state. Type/folder mismatches are skipped-with-log by compile (they fail validate first).

#### Scenario: Failed validation leaves candidate untouched

- GIVEN invalid frontmatter exists somewhere in `wiki/`
- WHEN `python scripts/wiki/compile.py` runs
- THEN it exits non-zero and every pre-existing file under `candidate/` is byte-identical to before

#### Scenario: Idempotent re-run

- GIVEN one successful compile has produced `candidate/`
- WHEN compile runs again immediately
- THEN all outputs are byte-identical to the prior run

#### Scenario: One doc per wiki file plus profile

- GIVEN a valid wiki containing files across the 8 types
- WHEN compile succeeds
- THEN `candidate/profile.json` exists in the exact current consumer shape, and each wiki markdown appears as its own `.md` under `candidate/docs/`

### Requirement: Index regeneration

`generate_index.py` SHALL regenerate `wiki/index.md` unconditionally on each run. The index SHALL cover entries of all 8 types, sorted by `updated` descending. `wiki/index.md` MUST NOT be hand-edited.

#### Scenario: Regenerated index covers all types

- GIVEN a wiki with at least one file of each of the 8 types
- WHEN `python scripts/wiki/generate_index.py` runs
- THEN `wiki/index.md` lists every entry, newest-updated first, grouped across all 8 types
