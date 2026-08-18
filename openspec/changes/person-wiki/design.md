# Design: Person Wiki — Structured Knowledge Base for the Digital Twin

## Technical Approach

A deterministic, file-system-based compile pipeline that transforms a human-editable Markdown wiki (`wiki/`, 8-type structure, YAML frontmatter) into the flat files (`candidate/`) that the existing backend already ingests. Three Python scripts (stdlib + PyYAML only) sit between the wiki and the RAG: `compile.py` (orchestrator), `validate.py` (pre-gate), and `generate_index.py` (navigation surface). No backend code changes — `CandidateProfile.load()` and `RAGPipeline.ingest_documents()` consume the same `candidate/` shape they always have.

The compile mapping from the proposal and specs is authoritative. The 8-type frontmatter schema, atomic write semantics, and error policies from the brainstorming design spec (`docs/superpowers/specs/2026-06-13-person-wiki-design.md`) are the primary behavioral reference.

---

## Architecture

### File Layout

```
repo root/
├── .gitignore                          [MODIFY — add RAGraw/, wiki/, candidate/]
├── RAGraw/                             [NEW, gitignored]   Raw materials (PDFs, screenshots, notes)
├── wiki/                               [NEW, gitignored]   Person wiki — Obsidian-editable knowledge base
│   ├── CONVENCIONES.md                 [NEW]   Schema docs, naming conventions, compile-overwrite contract
│   ├── index.md                        [NEW, auto-generated]   Per-type TOC with metadata tables
│   ├── templates/                      [NEW]   8 generic .md templates (profile, project, experience, skills, story, opinion, decision, faq)
│   ├── profile/mikel.md                [NEW]   Profile — source for profile.json + docs/cv.md
│   ├── projects/<kebab-case>.md        [NEW]   One file per project
│   ├── experience/<kebab-case>.md      [NEW]   One file per role/period
│   ├── skills/<domain>.md              [NEW]   One file per skill domain
│   ├── stories/<kebab-case>.md         [NEW]   One file per STAR story
│   ├── opinions/<kebab-case>.md        [NEW]   One file per strong opinion
│   ├── decisions/<kebab-case>.md       [NEW]   One file per career/technical decision
│   └── faq/<kebab-case>.md             [NEW]   One file per common interview question
├── scripts/wiki/                       [NEW, tracked]   Maintenance utilities (no personal data)
│   ├── compile.py                      [NEW, ~250 lines]   wiki/ → candidate/ compile orchestrator
│   ├── validate.py                     [NEW, ~120 lines]   Standalone frontmatter + graph symmetry checker
│   ├── generate_index.py               [NEW, ~80 lines]   Regenerates wiki/index.md
│   └── README.md                       [NEW, ~30 lines]   Developer docs for the scripts
├── candidate/                          [gitignored]   Compiled output (same shape as today)
│   ├── profile.json                    [COMPILED]   Synthesized from wiki/profile/mikel.md
│   └── docs/                           [COMPILED]   8 Markdown files (cv, projects, experience, skills, stories, opinions, decisions, faq)
└── backend/services/                   [UNCHANGED]
    ├── candidate.py                    (reads candidate/profile.json + candidate/docs/*.md)
    └── rag.py                          (chunks, embeds, retrieves)
```

### Data Flow

```
[RAGraw/]  PDFs, screenshots, raw notes
     │
     │  (manual: Mikel reads + LLM assists extraction)
     ▼
[wiki/]   Markdown + frontmatter, editable in Obsidian
     │
     │  python scripts/wiki/validate.py  (optional pre-gate)
     ▼
[compile.py]  validates frontmatter → type-folder check → related: resolution
     │         strips frontmatter → concatenates per-type → synthesizes profile.json
     │         writes to candidate.tmp/ → os.replace() → candidate/
     ▼
[candidate/]  profile.json + docs/*.md (deterministic output)
     │
     │  backend/services/candidate.py::CandidateProfile.load()  (existing, unchanged)
     ▼
[in-memory embeddings]
     │  RAGPipeline.ingest_documents()  (existing, unchanged)
     │
     ▼
[LLM prompt]  get_context_string() prepends structured + narrative context
```

### Module Boundaries

| Script | CLI args | Exit codes | Inputs | Outputs | Side effects |
|--------|----------|------------|--------|---------|--------------|
| `compile.py` | None (auto-detect `wiki/` and `candidate/` relative to repo root) | 0 success, 1 validation error, 2 I/O error | `wiki/` directory tree | `candidate/profile.json`, `candidate/docs/*.md` | Writes `candidate/*` (atomic via temp+rename). Implicitly calls validate logic inline. |
| `validate.py` | None | 0 all valid, 1 any error | `wiki/` directory tree | stdout/stderr report | None (read-only) |
| `generate_index.py` | None | 0 always | `wiki/` directory tree | `wiki/index.md` | Overwrites `wiki/index.md` unconditionally |

### External Dependencies

| Dependency | Why | Status |
|------------|-----|--------|
| `pathlib` (stdlib) | Cross-platform FS walking, glob, atomic renames | Already used throughout backend |
| `yaml` (PyYAML) | Frontmatter parsing | Already installed (tested via pytest fixture usage) |
| `datetime` (stdlib) | Date comparison for stale-content warnings | No install needed |
| `json` (stdlib) | profile.json synthesis | No install needed |
| `shutil` (stdlib) | Temp directory creation | No install needed |
| `logging` (stdlib) | Structured warnings and errors | No install needed |
| `git-filter-repo` | Git history cleanup | `pip install git-filter-repo` (one-time, not a project dependency) |

---

## Component Design

### `compile.py` — The Compile Orchestrator

**Public interface**:
```python
# CLI: python scripts/wiki/compile.py
# Exit:
#   0 — success (files written or empty wiki)
#   1 — validation errors found, no writes performed
#   2 — I/O error (wiki/ missing, temp dir creation fails)
```

**Functions**:

| Function | Responsibility |
|----------|----------------|
| `main()` | Parse CLI, orchestrate: validate → compile → atomic write |
| `validate_wiki(wiki_root: Path) -> list[str]` | Run validation checks on all wiki files. Return list of error strings. If non-empty, caller aborts. |
| `compile_wiki(wiki_root: Path) -> tuple[dict, dict[str, str]]` | Walk wiki types, strip frontmatter, concatenate narratives, synthesize profile.json. Returns `(profile_dict, doc_map)` where `doc_map` is `{filename: content}`. |
| `compile_type_folder(folder: Path, type_name: str) -> list[dict]` | Process single type folder: glob `*.md`, parse each, sort alphabetically, return parsed entries. |
| `parse_md_file(path: Path) -> tuple[dict, str]` | Read file, extract YAML frontmatter (`yaml.safe_load`), return `(frontmatter_dict, body_text)`. |
| `synthesize_profile(profile_md_file: Path, entries_by_type: dict) -> dict` | Build `profile.json` dict from profile frontmatter + body sections + experience/stories entries. |
| `atomic_write(profile_dict: dict, doc_map: dict[str, str], target_dir: Path)` | Write to `candidate.tmp/`, then `os.replace()` to `candidate/`. |

**Algorithm: compile_wiki**:
```
1. Walk wiki/ for each type folder in fixed order (profile, projects, experience, skills, stories, opinions, decisions, faq)
2. For each folder:
   a. Glob *.(md|markdown)
   b. Skip if type folder is empty
   c. Sort files alphabetically
   d. For each file:
      - parse_md_file() → frontmatter, body
      - Validate frontmatter has all 8 fields; if missing, log error, skip file
      - Check type matches parent folder; if mismatch, log message, skip file
      - If related: links exist, check they resolve; if broken, log warning (continue)
      - If confidence=low and updated within 7 days → log stale-content warning
      - Strip frontmatter from body (everything between --- delimiters)
      - Build concatenated output: each file becomes "## <title>\n\n<body>"
      - For experience and stories: also build structured JSON entries
3. For profile folder (expected single file wiki/profile/mikel.md):
   - Parse frontmatter: name (from title), title, summary (from summary_1line)
   - Extract body sections: ## Identity → synthesize, ## Top skills → skills array, etc.
   - Combine with experience and stories entries from other folders
4. Return (profile_dict, {filename: concatenated_content})
```

**Frontmatter parsing**:
```python
def parse_md_file(path: Path) -> tuple[dict, str]:
    """Parse a markdown file with YAML frontmatter.
    Returns (frontmatter_dict, body_without_frontmatter).
    Raises ValueError if frontmatter is missing or unparseable.
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise ValueError("Missing frontmatter delimiters")
    # Find second ---
    _, fm_part, body = content.split("---", 2)
    frontmatter = yaml.safe_load(fm_part)
    if not isinstance(frontmatter, dict):
        raise ValueError("Frontmatter is not a YAML dict")
    return frontmatter, body.strip()
```

**Atomic write**:
```python
def atomic_write(profile_dict: dict, doc_map: dict[str, str], target_dir: Path) -> None:
    """Write to temp dir, then atomic rename to target.
    The prefix 'candidate' is used for the temp dir name.
    Uses os.replace() which is atomic on POSIX and Windows."""
    import os, tempfile, shutil, json
    
    parent = target_dir.parent
    tmp_dir = parent / f"{target_dir.name}.tmp"
    
    # Clean any previous tmp
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    
    # Write all files
    (tmp_dir / "profile.json").write_text(
        json.dumps(profile_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    docs_dir = tmp_dir / "docs"
    docs_dir.mkdir(parents=True)
    for filename, content in doc_map.items():
        (docs_dir / filename).write_text(content, encoding="utf-8")
    
    # Atomic rename: os.replace() overwrites target atomically on both POSIX and NTFS
    if target_dir.exists():
        shutil.rmtree(target_dir)
    os.replace(tmp_dir, target_dir)
```

**Error handling**:
| Scenario | Behavior |
|----------|----------|
| `wiki/` does not exist | Exit 2, print error to stderr |
| `wiki/profile/` empty (no profile file) | Exit 1 — profile is required |
| File with bad/unparseable frontmatter | Skip file, log error, continue |
| Missing optional frontmatter field | Warning logged, compile continues with default |
| `type` doesn't match parent folder | Skip file silently (log message), continue |
| Broken `related:` link | Warning logged, compile continues |
| Stale content (confidence=low, updated < 7d) | Warning logged, compile continues |
| All valid files produce no output (empty wiki) | Exit 0, no writes performed, existing `candidate/` left intact |
| I/O error during write | Exit 2, `candidate/` preserved (atomic write never ran) |
| `candidate/` already exists | Overwritten atomically (existing content lost — by design per CONVENCIONES.md) |

**Profile.json synthesis algorithm** (the complex part):

The existing `profile.json` shape (candidate/profile.json) has:
```json
{
  "name": "Mikel",
  "title": "Desarrollador Junior DAM",
  "summary": "...",
  "skills": ["Python", "FastAPI", ...],
  "experience": [{"company": "", "role": "", "period": "", "highlights": [...]}],
  "projects": [{"name": "", "description": "", "technologies": [...], "highlights": [...]}],
  "stories": [{"situation": "", "task": "", "action": "", "result": ""}],
  "documents": ["candidate/docs/cv.md", "candidate/docs/projects.md", ...]
}
```

Synthesis from `wiki/profile/mikel.md`:
1. **`name`** → `frontmatter.title` (or "Mikel" as fallback)
2. **`title`** → captured from `## Identity` section or dedicated frontmatter field
3. **`summary`** → `frontmatter.summary_1line` (short) or body's first paragraph as expanded version
4. **`skills`** → derived from `tags` + body `## Top skills` section content
5. **`experience[]`** → each file in `wiki/experience/*.md`:
   - `company` + `role` + `period` → parsed from `frontmatter.title` (convention: `role-company-year-year`) or body's `## Context` section
   - `highlights` → body paragraphs (one highlight per paragraph or bullet found after frontmatter removal)
6. **`projects[]`** → each file in `wiki/projects/*.md`:
   - `name` → `frontmatter.title`
   - `description` → body's first paragraph (before `## My role`)
   - `technologies` → parsed from `## Stack` section
   - `highlights` → bullets from `## Outcomes` and `## My role`
7. **`stories[]`** → each file in `wiki/stories/*.md`:
   - `situation` → content under `## Situation` heading
   - `task` → content under `## Task` heading
   - `action` → content under `## Action` heading
   - `result` → content under `## Result` heading
8. **`documents`** → auto-generated list of all docs written to `candidate/docs/`

**Experience and stories dual output**: Each experience file produces BOTH an entry in `profile.json["experience"]` AND a section in `candidate/docs/experience.md`. Same for stories.

### `validate.py` — Standalone Validation Gate

**Public interface**:
```python
# CLI: python scripts/wiki/validate.py
# Exit:
#   0 — all files valid
#   1 — one or more errors found (bad frontmatter, type mismatch, broken links)
# Output: structured report to stdout (human-readable list of errors/warnings)
```

**Functions**:

| Function | Responsibility |
|----------|----------------|
| `main()` | Walk wiki, collect errors + warnings, print report, exit with appropriate code |
| `validate_file(path: Path) -> tuple[list[str], list[str]]` | Validate single file: returns `(errors, warnings)` |
| `check_frontmatter(fm: dict, path: Path) -> list[str]` | Check all 8 fields exist, `type` matches parent folder |
| `check_related_links(fm: dict, wiki_root: Path) -> tuple[list[str], list[str]]` | Resolve all `related:` entries; return `(broken_errors, missing_reciprocal_warnings)` |
| `check_stale_content(fm: dict) -> list[str]` | Warn if `confidence=low` and `updated < 7 days ago` |

**Validation report format** (stdout):
```
Validating wiki/...
========================================
ERRORS (will fail):
  wiki/stories/bad-story.md: Missing field 'summary_1line'
  wiki/projects/wrong-type.md: type='story' but in 'projects/' folder
  wiki/profile/mikel.md: related: 'stories/nonexistent.md' not found

WARNINGS:
  wiki/experience/acme.md: 'related: [projects/tts.md]' missing reciprocal link in wiki/projects/tts.md
  wiki/stories/fresh-draft.md: confidence='low' and updated=2026-06-10 (3 days ago)

Result: FAILED (3 errors, 2 warnings)
```

### `generate_index.py` — Index Regenerator

**Public interface**:
```python
# CLI: python scripts/wiki/generate_index.py
# Exit: 0 always (on success or empty wiki)
```

**Functions**:

| Function | Responsibility |
|----------|----------------|
| `main()` | Walk wiki types, build per-type tables, write `wiki/index.md` |
| `build_type_table(folder: Path, type_name: str) -> str` | Build a markdown table section for one type |
| `format_wikilink(title: str) -> str` | Convert filename stem to `[[type/title]]` wikilink |

**Index format**:
```markdown
# Person Wiki Index

_Last generated: 2026-06-13T14:30:00_

## Profiles

| Title | Tags | Updated | Confidence | Summary |
|-------|------|---------|------------|---------|
| [[profile/mikel]] | identity, persona | 2026-06-13 | high | One-line... |

## Projects

| Title | Tags | Updated | Confidence | Summary |
|-------|------|---------|------------|---------|
| [[projects/interview-tts]] | project, ai, voice | 2026-06-12 | high | Voice portfolio |
| [[projects/library-app]] | project, java, spring | 2026-06-10 | high | Library CRUD |

## Stories

...
```

---

## Wiki Data Model

### 8-Type Folder Structure

| Folder | Contents | Template | Output file(s) |
|--------|----------|----------|----------------|
| `wiki/profile/` | Single `mikel.md` | `templates/profile.md` | `candidate/profile.json`, `candidate/docs/cv.md` |
| `wiki/projects/` | One `.md` per project | `templates/project.md` | `candidate/docs/projects.md` |
| `wiki/experience/` | One `.md` per role | `templates/experience.md` | `candidate/docs/experience.md` + `profile.json["experience"]` |
| `wiki/skills/` | One `.md` per domain | `templates/skills.md` | `candidate/docs/skills.md` |
| `wiki/stories/` | One `.md` per STAR story | `templates/story.md` | `candidate/docs/stories.md` + `profile.json["stories"]` |
| `wiki/opinions/` | One `.md` per opinion | `templates/opinion.md` | `candidate/docs/opinions.md` |
| `wiki/decisions/` | One `.md` per decision | `templates/decision.md` | `candidate/docs/decisions.md` |
| `wiki/faq/` | One `.md` per Q | `templates/faq.md` | `candidate/docs/faq.md` |

### Per-Type Template Structure

Templates live in `wiki/templates/`. Each is a generic `.md` file with placeholder frontmatter and H2 section headers. See the full template content in the brainstorming design spec at `docs/superpowers/specs/2026-06-13-person-wiki-design.md#wiki-types--templates`. Key sections per type:

- **profile**: `## Identity`, `## Top skills`, `## How the twin should talk`, `## Current focus`, `## See also`
- **project**: `## What`, `## Why`, `## My role`, `## Stack`, `## Outcomes`, `## What I'd do differently`, `## See also`
- **experience**: `## Context`, `## Responsibilities`, `## Measurable achievements`, `## What this role taught you`, `## See also`
- **skills**: Table (Skill | Level | Last used | Where demonstrated) followed by `## Notes`
- **story**: `## Situation`, `## Task`, `## Action`, `## Result`, `## See also`
- **opinion**: `## The claim`, `## Why I believe it`, `## When it doesn't apply`, `## See also`
- **decision**: `## Context`, `## Options I considered`, `## What I chose and why`, `## Outcome`, `## See also`
- **faq**: `## Short answer (30s)`, `## Longer version (2min)`, `## Sources`, `## See also`

### Filename Conventions

- **Files**: `kebab-case.md` (all types). The filename stem becomes the `[[wikilink]]` target and the default `title` if not overridden.
- **Folders**: lowercase plural (`projects/`, `skills/`, `stories/`, not `project/` or `skill/`).
- **Dates**: ISO 8601 (`YYYY-MM-DD`) in frontmatter `created` and `updated` fields.
- **Exception**: `wiki/profile/mikel.md` is the ONLY expected file in `wiki/profile/`.

### Frontmatter Schema (Single Shared Shape)

Every wiki file MUST start with this YAML frontmatter:

```yaml
---
type: profile | project | experience | skills | story | opinion | decision | faq
title: kebab-case-name
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high | medium | low
tags: [tag1, tag2]
related: [other-file.md, another-file.md]
summary_1line: Description in 80 chars max
---
```

**Validation rules**:

| Field | Required | Type | Validation |
|-------|----------|------|------------|
| `type` | yes | enum (8 values) | Must match parent folder name exactly |
| `title` | yes | string, kebab-case | Should equal filename stem |
| `created` | yes | string, ISO 8601 date | Parseable as `datetime.date` |
| `updated` | yes | string, ISO 8601 date | Parseable as `datetime.date` |
| `confidence` | yes | enum (high/medium/low) | One of three allowed values |
| `tags` | yes | list of strings | At least one element, lowercase hyphenated |
| `related` | yes | list of strings | Each is a relative path like `projects/tts.md` or `[]` if none |
| `summary_1line` | yes | string, ≤ 80 chars | Non-empty |

**Type-folder matching rule**: The `type` field must match the parent folder name exactly. A file at `wiki/stories/foo.md` with `type: project` is rejected by `validate.py` (exit 1) and silently skipped by `compile.py`.

**Related-link resolution rule**: Each entry in `related:` is a relative path from `wiki/` (e.g., `projects/interview-tts.md`). The file must exist at `wiki/projects/interview-tts.md`. Resolution is done by joining `wiki_root / related_entry`. Symmetry (reciprocal links) is recommended but NOT enforced by `compile.py`; `validate.py` issues a warning for missing reciprocals.

### Profile JSON Synthesis Detail

The `profile.json` generated by `compile.py` must match the schema consumed by `candidate.py`:

```json
{
  "name": "Mikel",
  "title": "Desarrollador Junior DAM",
  "summary": "Developer passionate about...",
  "skills": ["Python", "FastAPI", "JavaScript", ...],
  "experience": [
    {
      "company": "Proyectos Personales y Bootcamp",
      "role": "Desarrollador Full-Stack",
      "period": "2022 - Presente",
      "highlights": ["Construí InterviewTTS...", "..."]
    }
  ],
  "projects": [
    {
      "name": "InterviewTTS",
      "description": "Voice portfolio with AI...",
      "technologies": ["Python", "FastAPI", "Whisper", ...],
      "highlights": ["Pipeline completo STT...", "..."]
    }
  ],
  "stories": [
    {
      "situation": "Los recruiters pasan ~6 segundos...",
      "task": "Construir un gemelo digital...",
      "action": "Diseñé e implementé...",
      "result": "Creé InterviewTTS..."
    }
  ],
  "documents": [
    "candidate/docs/cv.md",
    "candidate/docs/projects.md",
    "candidate/docs/experience.md",
    "candidate/docs/skills.md",
    "candidate/docs/stories.md",
    "candidate/docs/opinions.md",
    "candidate/docs/decisions.md",
    "candidate/docs/faq.md"
  ]
}
```

Mapping from wiki files to JSON fields:

| JSON field | Source | Extraction |
|------------|--------|------------|
| `name` | `wiki/profile/mikel.md` frontmatter `title` | Direct value |
| `title` | `wiki/profile/mikel.md` body `## Identity` section | First line after heading that looks like a role title |
| `summary` | `wiki/profile/mikel.md` frontmatter `summary_1line` (short) + body first paragraph (expanded) | Combined |
| `skills` | `wiki/profile/mikel.md` frontmatter `tags` + `## Top skills` bullet list | Union of tags and parsed skill names from bullets |
| `experience[]` | Each `wiki/experience/*.md` | See below |
| `projects[]` | Each `wiki/projects/*.md` | See below |
| `stories[]` | Each `wiki/stories/*.md` | See below |
| `documents` | Auto-generated from `doc_map.keys()` | All files written to `candidate/docs/` |

**Experience entry building** (one file → one entry):
1. `company` → fallback: parse from `frontmatter.title` (split on `-`, extract company name after role + year pattern) OR from body `## Context` line containing "Company size, industry, team"
2. `role` → fallback: from frontmatter title or `## Context` section
3. `period` → from body `## Context` section (date range like "2022-2024") or from frontmatter created/updated dates
4. `highlights` → sentences under `## Measurable achievements` that start with a verb in past tense, bullet list items

**Story entry building**:
1. `situation` → body content under `## Situation` heading
2. `task` → body content under `## Task` heading
3. `action` → body content under `## Action` heading
4. `result` → body content under `## Result` heading

STAR heading parsing uses a simple regex: `r'^## (Situación|Situation|Tarea|Task|Acción|Action|Resultado|Result)\s*$'` to be bilingual-safe, then captures all content until the next `##` heading or end-of-file.

### `docs/experience.md` and `docs/stories.md` Generation

These docs files serve the narrative RAG alongside their structured JSON counterparts:

- **`docs/experience.md`**: starts with `# Experience\n\n`, then for each experience file in alphabetical order, appends `## <title>\n\n<body_without_frontmatter>\n\n`.
- **`docs/stories.md`**: starts with `# Stories\n\n`, then for each story file, appends `## <title>\n\n<body_without_frontmatter>\n\n`.

This ensures both the structured fields (used in the system prompt via `get_context_string()`) AND the full narrative (used by the RAG chunker) are available.

---

## Resolved Decisions

These decisions are already made. They are documented here, not re-debated.

### Decision: Empty wiki exits 0 with no writes
- **Date resolved**: 2026-06-13 (user decision)
- **Behavior**: When `wiki/` contains no `.md` files, `compile.py` exits 0 with no writes. The existing `candidate/` is left intact.
- **Rationale**: Backend already handles missing `candidate/` gracefully (logs warning, RAG empty per `CandidateProfile._load_markdown_docs`). Safer for incremental development than writing empty skeletons that would overwrite existing content.

### Decision: Type-folder mismatch asymmetry (validate fails vs compile skips)
- **Date resolved**: 2026-06-13 (spec decision)
- **Behavior**: `validate.py` fails hard (exit 1) on type-folder mismatch. `compile.py` skips the file silently with a log message.
- **Rationale**: Validate is a quality gate intended to catch structural mistakes before they matter — fail loud. Compile is the last-mile production path — you want partial output if the rest of the wiki is valid, even if one file is misplaced.

### Decision: Single shared frontmatter schema with `type` discriminator
- **Date resolved**: 2026-06-13 (spec decision)
- **Behavior**: One frontmatter shape (8 fields), `type` enum discriminates behavior. Not 8 Pydantic schemas.
- **Rationale**: Lower maintenance burden, single source of truth for frontmatter validation. The `type` enum is sufficient to route behavior in the compile script.

### Decision: Stdlib + PyYAML only
- **Date resolved**: 2026-06-13 (proposal decision)
- **Behavior**: No new third-party packages. Python stdlib (`pathlib`, `json`, `datetime`, `shutil`, `logging`) + PyYAML (already installed).
- **Rationale**: The scripts are ~450 lines total. A dependency like click/typer for CLI, pydantic for schemas, or frontmatter-specific libraries adds maintenance cost without proportional benefit.

### Decision: Git history cleanup with `git filter-repo`
- **Date resolved**: 2026-06-13 (spec decision)
- **Behavior**: Run `git filter-repo --path candidate/ --invert-paths` before any public push.
- **Rationale**: Personal data (stories, CV, project details) is already committed to git history. `git rm --cached` only removes it from HEAD, not from history. This is a public repo — history rewriting is mandatory before pushing.

---

## Open Questions

These 4 questions from the proposal are NOT yet resolved. They are surfaced here for user review at design time. They do NOT block the design — `sdd-tasks` can plan around them with sensible defaults, and the user can resolve them at apply time.

1. **`profile.json` overwrite contract**: if Mikel edits `candidate/profile.json` directly (legacy habit), the next `compile.py` will overwrite it without warning. Document this in `wiki/CONVENCIONES.md`. Acceptable?
   - **Default for planning**: yes, document in CONVENCIONES.md.

2. **Story count target**: 5–10 STAR stories as a healthy starting corpus. Right target?
   - **Default for planning**: aim for 8 as medium target.

3. **Confidence lifecycle**: when does `medium` become `high`? Default rule: after a real interview with positive feedback on that content. Codify in `CONVENCIONES.md`. Agreed?
   - **Default for planning**: yes, codify in CONVENCIONES.md.

4. **Obsidian config vendoring**: vendor `.obsidian/` config (graph colors, theme, hotkeys) for consistency? Deferred.
   - **Default for planning**: skip for MVP. Noted in open questions.

---

## Testing Strategy

### Unit Tests (pytest, new files in `tests/scripts/wiki/`)

| Test file | What it covers | Approach |
|-----------|----------------|----------|
| `tests/scripts/wiki/test_compile.py` | compile.py frontmatter parsing, concatenation, type-folder skipping, atomic write, empty wiki, stale warning, broken link warning | Fixture wiki dirs (tmp_path) with known files |
| `tests/scripts/wiki/test_validate.py` | validate.py exit codes, error reporting format, each validation rule independently | Fixture wiki dirs with intentional errors |
| `tests/scripts/wiki/test_generate_index.py` | Index generation, table format, recency sorting, empty wiki, wikilink format | Fixture wiki dirs |

### Test Fixtures

All fixtures use `pytest`'s `tmp_path` to create ephemeral wiki directories:

```
tests/scripts/wiki/fixtures/
├── conftest.py              # Shared fixtures (sample_wiki, wiki_with_errors, empty_wiki)
├── sample_wiki/             # Full valid wiki with 3-5 files across 3+ types
│   ├── profile/mikel.md
│   ├── projects/test-proj.md
│   ├── stories/test-story.md
│   └── opinions/test-opinion.md
└── broken_fixtures/         # Individual bad files for negative tests
    ├── missing_field.md
    ├── type_mismatch.md
    ├── broken_related.md
    ├── stale_content.md
    └── no_frontmatter.md
```

### Integration Test

```
1. Create a full wiki fixture (5 files across 4 types)
2. Run compile.py → assert candidate/profile.json + docs/*.md created
3. Load candidate/ via CandidateProfile → assert profile data matches source
4. Load docs/ via RAGPipeline.ingest_documents() → assert chunks created
5. Verify: query RAG for wiki-specific content → assert top-K contains expected chunk
6. Verify: query RAG for nonexistent content → assert graceful empty response
```

### Manual Test (Mikel, periodically)

- Open `wiki/` in Obsidian → graph view shows clusters by type
- Click `[[wikilink]]` → it resolves to the target file
- Edit a file → run compile → restart backend → ask twin about edit → response reflects new content

---

## Rollout Plan

| Phase | Description | Duration estimate | Chained PR candidate? |
|-------|-------------|-------------------|----------------------|
| **1. Git history cleanup** | `git filter-repo --path candidate/ --invert-paths`. Verify with `git log --all --full-history -- candidate/`. If remote exists, force-push. | 30 min | No — infrastructure, no code |
| **2. .gitignore + untrack** | Add `RAGraw/`, `wiki/`, `candidate/` to `.gitignore`. `git rm --cached -r candidate/` (keep locally). Verify with `git status`. | 10 min | No — infrastructure |
| **3. Wiki scaffold** | Create 8 wiki type folders. Create 8 templates in `wiki/templates/`. Create `wiki/CONVENCIONES.md`. | 30 min | PR #1 (~200 lines: 8 templates + CONVENCIONES) |
| **4. validate.py** | Standalone frontmatter + graph symmetry validator. Tests for each rule. | 1-2 hrs | PR #2 (~160 lines: validate.py + tests) |
| **5. generate_index.py** | Index regenerator. Tests for format, sorting, empty wiki. | 45 min | Part of PR #2 or separate PR #3 (~100 lines) |
| **6. compile.py** | Full compile orchestrator — frontmatter parsing, concatenation, atomic write, profile.json synthesis. Tests for all scenarios. | 2-3 hrs | PR #4 (~350 lines: compile.py + compile tests) |
| **7. First real content** | Extract profile from existing `candidate/profile.json` into `wiki/profile/mikel.md`. Extract 1-2 stories from `candidate/docs/stories.md`. | 1 hr | Manual, not code |
| **8. First compile + smoke test** | Run compile → start backend → query RAG → assert results. Fix any issues. | 1 hr | Manual verification |
| **9. Content iteration** | Add remaining types (projects, skills, opinions, decisions, faq). Re-compile after each batch. Re-test. | Ongoing | Manual |

---

## Line Budget Forecast (Critical for PR Review)

### Estimated lines per file

| File | Estimate | Type |
|------|----------|------|
| `scripts/wiki/compile.py` | ~250 | New (tracked) |
| `scripts/wiki/validate.py` | ~120 | New (tracked) |
| `scripts/wiki/generate_index.py` | ~80 | New (tracked) |
| `scripts/wiki/README.md` | ~30 | New (tracked) |
| `wiki/templates/*.md` (8 files) | ~160 (20 each) | New (gitignored — excluded from PR count) |
| `wiki/CONVENCIONES.md` | ~40 | New (gitignored — excluded) |
| `.gitignore` | ~3 (additions) | Modified (tracked) |
| `tests/scripts/wiki/test_compile.py` | ~100 | New (tracked) |
| `tests/scripts/wiki/test_validate.py` | ~80 | New (tracked) |
| `tests/scripts/wiki/test_generate_index.py` | ~50 | New (tracked) |
| `tests/scripts/wiki/fixtures/conftest.py` | ~60 | New (tracked) |
| `tests/scripts/wiki/fixtures/broken_fixtures/*.md` | ~30 | New (tracked) |

**Tracked line estimate**: ~820 lines (scripts + tests + .gitignore)
**Gitignored files**: ~200 lines (wiki templates, CONVENCIONES.md)
**Total**: ~1,020 lines

The **400-line review budget** is exceeded by ~2.5x.

### Recommended Slicing Strategy

| Chained PR | Contents | Estimated tracked lines | Rationale |
|------------|----------|------------------------|-----------|
| **PR #1: Infrastructure + Scaffold** | .gitignore update, `scripts/wiki/README.md`, `wiki/templates/*`, `wiki/CONVENCIONES.md`, `tests/scripts/wiki/fixtures/conftest.py` + broken fixtures | ~130 | Establishes the convention layer first. Templates and CONVENCIONES are the spec — code follows. |
| **PR #2: Validation + Index** | `validate.py`, `generate_index.py`, their tests | ~250 | Self-contained tools that can be tested independently. No compile dependency. |
| **PR #3: Compile Engine** | `compile.py` + `test_compile.py` | ~350 | The core deliverable. Depends on the frontmatter schema from PR #1. Split into own PR because it's the largest and riskiest chunk. |
| **PR #4: Integration test** | Integration test, backend smoke test fixture | ~90 | Final verification that the pipeline works end-to-end. |

**Decision needed before apply**: Yes — user must confirm this slicing aligns with their review preferences.
**Chained PRs recommended**: Yes — 4 PRs across ~820 tracked lines.
**400-line budget risk**: High — 2x overshoot.

---

## Backend Compatibility Note

Zero changes to `backend/`. The existing `CandidateProfile.load()` reads `candidate/profile.json` and `candidate/docs/*.md` — the compile scripts produce exactly this shape. The existing `RAGPipeline.ingest_documents()` chunks by `r'\n(?=#{1,3}\s)'` — the compile concatenation with `## <title>` boundaries was designed to align with this regex.

The one subtle change: `candidate/docs/` grows from 4 files (cv, projects, skills, stories) to 8 files (+ experience, opinions, decisions, faq). The existing `RAGPipeline.ingest_documents()` already handles arbitrary filenames (it iterates `glob("*.md")`). `CandidateProfile._load_markdown_docs()` only tracks the original 4 as "expected" in its `missing_sections` list — this is informational only and does not affect functionality. No backend code change needed.
