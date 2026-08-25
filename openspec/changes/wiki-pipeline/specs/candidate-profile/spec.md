# Candidate Profile Specification

## Purpose

Define what the running digital twin treats as its candidate knowledge after `wiki-pipeline`: the person wiki (`wiki/`) is the single source of truth, and `candidate/` is generated, disposable output. The backend loader (`backend/services/candidate.py`) remains untouched.

## Requirements

### Requirement: Wiki as source of truth

The system SHALL treat `wiki/` as the authoritative source of candidate knowledge. The contents of `candidate/` SHALL be fully owned by `compile.py`; manual edits to `candidate/` (local or on the VPS) MUST NOT survive the next deploy.

#### Scenario: VPS-side edit is overwritten on deploy

- GIVEN a direct edit was made to a file under VPS `candidate/`
- WHEN the deploy pipeline runs to completion (validate → compile → rsync → restart)
- THEN the VPS `candidate/` matches freshly compiled wiki output exactly, and the edit is gone

### Requirement: profile.json shape stability

`compile.py` SHALL emit `profile.json` in EXACTLY the shape `backend/services/candidate.py` consumes today (keys: `name`, `title`, `summary`, `skills`, `experience`, `projects`, `stories`). The persona prompt SHALL remain unchanged.

#### Scenario: Compiled profile feeds the persona prompt

- GIVEN `wiki/profile/mikel.md` holds frontmatter and body content
- WHEN `compile.py` runs successfully
- THEN `candidate/profile.json` contains only the current consumer keys, and `get_context_string()` renders name/title/summary/skills/experience/projects/stories without any downstream change

### Requirement: Docs granularity and loader compatibility

Each wiki markdown file SHALL become its own `.md` document under `candidate/docs/`. The loader MUST accept arbitrary `.md` filenames there: absence of the legacy aggregate files (`cv.md`, `projects.md`, `skills.md`, `stories.md`) MUST NOT be an error. *(Assumption verified against `_load_markdown_docs`, which warns-only on missing legacy names — re-confirmed during design.)*

#### Scenario: Non-legacy doc filenames load at startup

- GIVEN `candidate/docs/` contains files named per wiki pages (e.g., `opinions-x.md`) and none of the four legacy aggregates
- WHEN the service starts and `CandidateProfile.load()` runs
- THEN every `.md` in `candidate/docs/` is loaded into `documents` keyed by filename, with at most a warning log

### Requirement: Reload via service restart

Behavioral updates from recompiled content SHALL reach the live service solely through the deploy's `systemctl restart interviewtts.service` step. No `backend/` module SHALL be modified by this change.

#### Scenario: Fresh content served after restart

- GIVEN a wiki edit changed an answer-bearing document
- WHEN deploy finishes including the service restart
- THEN RAG retrieval over the loaded documents reflects the new content, with zero diffs under `backend/`

## Notes

- `rag-pipeline` and `conversation-engine` are behaviorally unchanged by this specification (startup load + restart semantics preserved).
