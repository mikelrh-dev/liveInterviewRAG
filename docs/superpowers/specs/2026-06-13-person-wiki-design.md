# Person Wiki — Source of Truth for the Digital Twin

**Status:** Design proposed, awaiting user review
**Date:** 2026-06-13
**Author:** Gentle AI orchestrator + user

## Goal

Give the InterviewTTS digital twin a structured, browsable, human-editable
knowledge base that compiles deterministically into the existing RAG pipeline.
The user (Mikel) edits Markdown files in Obsidian; a compile script produces
the flat files the backend already knows how to ingest.

The backend (`backend/services/candidate.py` + `backend/services/rag.py`) is
**not modified**. All changes live in a new `wiki/` directory and a new
`scripts/wiki/` utility set.

## Context — security finding

The repository is **public**. The following files containing personal data
were already committed to git history:

- `candidate/docs/cv.md`
- `candidate/docs/projects.md`
- `candidate/docs/skills.md`
- `candidate/docs/stories.md`
- `candidate/profile.json`

`git rm --cached` removes them from future commits but **not from history**.
Public exposure means history rewriting (`git filter-repo` or BFG) is required
before pushing.

`RAGraw/` is untracked but visible. `wiki/` does not exist yet.

## Scope

### In scope (this design)

- A new `wiki/` directory with an 8-type structure (profile, project,
  experience, skills, story, opinion, decision, faq)
- 8 generic Markdown templates (placeholders, not Mikel-specific)
- A `scripts/wiki/compile.py` that walks the wiki and produces the existing
  `candidate/` flat files
- A `scripts/wiki/validate.py` that enforces frontmatter + graph symmetry
- A `scripts/wiki/generate_index.py` that regenerates `wiki/index.md`
- `.gitignore` updates for `RAGraw/`, `wiki/`, `candidate/`
- Git history cleanup for the public repo

### Out of scope (deferred)

- A web UI for editing the wiki (Obsidian is the editor)
- Hot-reload of `candidate/` while the backend runs (restart is fine for MVP)
- A search index over the wiki (Obsidian + the existing RAG cover this)
- Encryption at rest (this is a portfolio project; `.gitignore` is the boundary)
- Per-type schemas (Pydantic models for each frontmatter) — keep it as YAML
  with a single shared shape

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Wiki structure depth | 8 types, 2 levels | Each conversation type (technical, behavioral, career, etc.) maps cleanly to a type. More = templates nobody uses; fewer = LLM mixes roles |
| File naming | `kebab-case.md` for all | `MAYUSCULAS.md` from PaginaWebPracticas was for DB table names. For a person wiki, kebab-case reads better in Obsidian |
| Folder naming | lowercase plural (`projects/`, `stories/`) | Standard convention; aligns with content collection mental model |
| Frontmatter | 8 fields (subset of PaginaWebPracticas 12) | Drop `sources`/`source_hash` (overkill for self-curated content) and `depends_on`/`impacts` (only applies to code/processes, not a person) |
| Frontmatter shape | Single shared schema, `type` discriminates | 8 schemas is a maintenance burden; one shape with a `type` enum is enforceable and DRY |
| Wiki → RAG bridge | Compile script (not direct read) | Decouples wiki evolution from RAG stability; allows validation; aligns with the Karpathy "raw → wiki → compile" flow the user already has in PaginaWebPracticas |
| Compile output | Markdown files in `candidate/docs/` + synthesized `profile.json` | Backend already ingests exactly this shape; zero backend changes |
| Compile error policy | Never partially overwrite | A bad story STAR must not poison the whole RAG; failures abort before any write |
| Confidence tracking | `high` / `medium` / `low` in frontmatter | Lets the LLM know what to assert confidently vs hedge on; surfaces stale content via compile warnings |
| Validation cadence | Run on every compile, optional standalone | Catch frontmatter drift and broken links before they hit the RAG |
| Editor | Obsidian (manual install) | Free, graph view matches the Karpathy wiki intent, zero build/config |
| Version control of wiki | `.gitignore` it; history rewriting for the public repo | The wiki is personal data; the structure pattern can live in `docs/superpowers/specs/` if anyone wants to fork it |

## Architecture

### File layout

```
repo root/
├── RAGraw/                       [gitignored]   Raw materials (PDF, screenshots, source notes)
├── wiki/                         [gitignored]   Person wiki — editable, browsable in Obsidian
│   ├── CONVENCIONES.md
│   ├── index.md                  (auto-generated)
│   ├── templates/                (8 generic templates — also gitignored)
│   ├── profile/mikel.md
│   ├── projects/<kebab-case>.md
│   ├── experience/<kebab-case>.md
│   ├── skills/<domain>.md
│   ├── stories/<kebab-case>.md
│   ├── opinions/<kebab-case>.md
│   ├── decisions/<kebab-case>.md
│   └── faq/<kebab-case>.md
├── scripts/wiki/                 [tracked]      Maintenance utilities (no personal data)
│   ├── compile.py                (wiki/ → candidate/)
│   ├── validate.py               (frontmatter + graph symmetry)
│   ├── generate_index.py         (regenerates wiki/index.md)
│   └── README.md
├── candidate/                    [gitignored]   Compiled output — feeds the RAG AND the system prompt
│   ├── profile.json              (structured → used by CandidateProfile.get_context_string() for system prompt)
│   └── docs/                     (narrative → used by RAGPipeline.ingest_documents() for chunked retrieval)
│       ├── cv.md
│       ├── projects.md
│       ├── experience.md
│       ├── skills.md
│       ├── stories.md
│       ├── opinions.md
│       ├── decisions.md
│       └── faq.md
└── backend/                      [tracked, unchanged]
    ├── services/candidate.py     (reads candidate/ — no changes)
    └── services/rag.py           (chunks, embeds, retrieves — no changes)
```

### Data flow

```
[RAGraw/]
   PDF, screenshots, raw notes
        ↓
   (manual: Mikel reads, LLM assists extraction)
        ↓
[wiki/]   Markdown + frontmatter, editable in Obsidian
        ↓
   python scripts/wiki/compile.py
        ↓
   validates frontmatter + graph
        ↓
   strips frontmatter, pliegue experiencia/historias a JSON
        ↓
[candidate/]   profile.json + docs/*.md (deterministic output)
        ↓
   backend/services/candidate.py::CandidateProfile.load()  (existing)
        ↓
   backend/services/rag.py::RAGPipeline.ingest_documents()  (existing)
        ↓
   in-memory embeddings
        ↓
   on user query: retrieve → top-K chunks → LLM prompt
```

The only new moving part is `compile.py`. Everything downstream is unchanged.

### Compile mapping

| Source (`wiki/`) | Output (`candidate/`) | Transform |
|------------------|------------------------|-----------|
| `profile/mikel.md` (full body + frontmatter) | `profile.json` (top fields) + `docs/cv.md` (narrative) | Frontmatter → `name`, `title`, `summary`, `tags`-derived skills; body → `docs/cv.md` |
| `projects/*.md` (one file per project) | `docs/projects.md` (single file) | Concatenate; each file becomes a `## <title>` section |
| `experience/*.md` (one file per role) | `profile.json["experience"]` (array) + `docs/experience.md` | Each file → experience entry; also concatenate narrative |
| `skills/*.md` (one file per domain) | `docs/skills.md` (single file) | Concatenate; each file becomes a `## <domain>` section |
| `stories/*.md` (one file per STAR) | `profile.json["stories"]` (array) + `docs/stories.md` | Parse `## Situation/Task/Action/Result` headings; also concatenate narrative |
| `opinions/*.md` (one file per opinion) | `docs/opinions.md` (single file) | Concatenate; each file becomes a `## <opinion title>` section |
| `decisions/*.md` (one file per decision) | `docs/decisions.md` (single file) | Concatenate; each file becomes a `## <decision title>` section |
| `faq/*.md` (one file per Q) | `docs/faq.md` (single file) | Concatenate; each file becomes a `## Q: ...` section |

The `profile.json` synthesis is the only "intelligent" part of the compile —
everything else is concatenation with heading boundaries that align with the
existing chunker's regex (`r'\n(?=#{1,3}\s)'` in `_chunk_document`).

### What `profile.json` feeds

`profile.json` is consumed in **two** places by the existing backend:

1. **System prompt** — `CandidateProfile.get_context_string()` flattens the
   JSON's structured fields (`name`, `title`, `summary`, `skills`, each
   `experience` entry, each `story` entry) into a text block that is prepended
   to the LLM system prompt on every chat. This is the "always-on" identity.
2. **RAG chunks** — the JSON is also passed through `RAGPipeline.ingest_documents()`
   (via `CandidateProfile.documents`, which serializes the JSON as
   `filename → content` map). Each field becomes a retrievable chunk.

So `profile.json` is both a system-prompt anchor AND a RAG source. The
narrative `.md` files in `docs/` are RAG-only.

### `generate_index.py` output

`wiki/index.md` is auto-generated. It contains a markdown table per type
(`## Profiles`, `## Projects`, `## Stories`, …) with columns: title, tags,
updated, confidence, summary. Sorted by `updated` desc within each table.
Wikilinks to each file. No manual edits to `index.md` — the script owns it.

### Validation (in compile step, before any write)

1. Every file in `wiki/` has frontmatter with the 8 required fields
2. `type` in frontmatter matches the parent folder (e.g., a file in
   `wiki/stories/` MUST have `type: story`)
3. Every entry in `related:` points to an existing file in `wiki/`
4. `confidence: low` on a file with `updated` within the last 7 days → warning
5. If any validation fails: abort, do not write to `candidate/`, return
   non-zero exit code with a structured error report

### Error handling

| Case | Behavior |
|------|----------|
| `wiki/` does not exist | `compile.py` exits 1 with clear message. Backend starts with empty RAG (existing behavior) |
| File with bad frontmatter | Skip that file, log error, continue |
| `related:` link broken | Warning logged, does not fail |
| `type` mismatch with folder | Skip that file (stronger than warning — it indicates a structural mistake) |
| `candidate/` already exists | Overwrite atomically (write to temp, rename) |
| Compile failure | `candidate/` left intact, diff report printed to stderr |
| Backend starts with no `candidate/` | Log warning, RAG empty (existing behavior in `CandidateProfile._load_markdown_docs`) |

## Wiki Types & Templates

Eight types, one template each. Templates live in `wiki/templates/`.

### 1. Profile (`wiki/profile/`)

The "soul" of the twin. Single file: `wiki/profile/mikel.md`.

```markdown
---
type: profile
title: mikel
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [identity, persona]
related: []
summary_1line: One-line description of who Mikel is, in his own words
---

# Mikel — profile

## Identity
- Name
- Role / current focus
- Location
- Languages
- Links (portfolio, GitHub, LinkedIn)

## Top skills (summary)
- 3-5 things you lead with in an interview

## How the twin should talk
- Tone: direct, technical, friendly
- What to assert confidently
- What to hedge on
- Words/phrases to avoid
- Languages to answer in (by default)

## Current focus
- What you're working on / learning right now

## See also
- [[projects/interview-tts]]
- [[experience/...]]
```

### 2. Project (`wiki/projects/`)

One file per project. Title in the filename (kebab-case) and as the H1.

```markdown
---
type: project
title: project-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [project, web, ai, ...]
related: [skills/backend.md, decisions/why-python.md]
summary_1line: One-line description of what the project is
---

# Project Name

## What
One paragraph: what the project is, in plain language.

## Why
The problem it solves, or the goal behind it.

## My role
- What you specifically did
- What you didn't do
- Team size and context

## Stack
- Languages, frameworks, infra
- Why each was chosen (if non-obvious) → link to opinion/decision

## Outcomes
- Measurable results (numbers if you have them)
- What it enabled

## What I'd do differently
Honest reflection. This is what makes the answer credible in an interview.

## See also
- [[skills/backend]]
- [[decisions/why-python]]
```

### 3. Experience (`wiki/experience/`)

One file per role / period.

```markdown
---
type: experience
title: role-company-year-year
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [junior, fullstack, contract]
related: [projects/...]
summary_1line: Role title at Company, period
---

# Role — Company (YYYY-YYYY)

## Context
- Company size, industry, team
- Why you joined

## Responsibilities
- Bullet list, concrete

## Measurable achievements
- Numbers, percentages, before/after

## What this role taught you
- 1-3 takeaways, honest

## See also
- [[projects/x]]
- [[stories/y]]
```

### 4. Skills (`wiki/skills/`)

One file per domain (frontend, backend, data, ia, devops, etc.).

```markdown
---
type: skills
title: domain-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [skill-domain]
related: [projects/...]
summary_1line: Domain summary (frontend, backend, IA, etc.)
---

# Domain name

| Skill | Level | Last used | Where demonstrated |
|-------|-------|-----------|--------------------|
| FastAPI | confident | 2026-06 | [[projects/interview-tts]] |
| React | working | 2025-09 | [[projects/pagina-web-practicas]] |
| Postgres | familiar | 2025-12 | [[experience/...]] |

## Notes
- Anything else worth saying about this domain
- Gaps you're actively working on
```

### 5. Story (`wiki/stories/`) — STAR format

One file per story. These are the **hardest** to write and the **highest
leverage** for behavioral questions.

```markdown
---
type: story
title: story-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: medium
tags: [conflict, leadership, ownership, failure, learning, ...]
related: [experience/...]
summary_1line: One-line setup: situation + outcome
---

# Story title

## Situation
- Where, when, who was involved
- What the stakes were

## Task
- What you specifically had to do

## Action
- What you actually did, step by step
- What you decided and why
- Be honest about what you didn't know at the time

## Result
- Quantified outcome if possible
- What the team / company / user got
- What you learned

## See also
- [[experience/...]]
- [[skills/...]]
```

### 6. Opinion (`wiki/opinions/`)

One file per strong opinion. Optional but they make the twin sound like a
person, not a brochure.

```markdown
---
type: opinion
title: opinion-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: medium
tags: [architecture, process, tooling, ...]
related: [decisions/...]
summary_1line: One-line statement of the opinion
---

# Opinion title

## The claim
One sentence: what you believe.

## Why I believe it
- Reasoning, evidence, experience
- 1-3 paragraphs max

## When it doesn't apply
- The opposite cases, with reasoning
- Be honest about the limits of your view

## See also
- [[decisions/...]]
```

### 7. Decision (`wiki/decisions/`)

One file per significant decision (career or technical).

```markdown
---
type: decision
title: decision-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [career, technical, ...]
related: [experience/..., projects/...]
summary_1line: One-line: what was decided and why
---

# Decision title

## Context
- When, what state of things
- What triggered the decision

## Options I considered
- Option A: pros, cons
- Option B: pros, cons
- Option C (if any): pros, cons

## What I chose and why
- The deciding factors
- The tradeoffs I accepted

## Outcome
- What actually happened
- Would I choose the same again?

## See also
- [[opinions/...]]
- [[experience/...]]
```

### 8. FAQ (`wiki/faq/`)

One file per common interview question. Pre-cooked answers are
disproportionately useful for predictable questions.

```markdown
---
type: faq
title: faq-slug
created: YYYY-MM-DD
updated: YYYY-MM-DD
confidence: high
tags: [intro, weakness, salary, ...]
related: [profile/mikel, stories/...]
summary_1line: The question this entry answers
---

# Question

## Short answer (30s)
The elevator-pitch version. What the LLM should reach for first.

## Longer version (2min)
- Context
- Reasoning
- What you want them to remember

## Sources
- [[profile/mikel]]
- [[stories/conflicto-xyz]]

## See also
- [[faq/...]]
```

## Frontmatter Convention

Every file in `wiki/` (except `CONVENCIONES.md`, `index.md`, and anything in
`templates/`) MUST start with this frontmatter:

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

### Field rules

| Field | Required | Notes |
|-------|----------|-------|
| `type` | yes | Must match parent folder |
| `title` | yes | Should equal filename stem |
| `created` | yes | ISO 8601 date |
| `updated` | yes | ISO 8601 date; update whenever content changes |
| `confidence` | yes | `high` = verified, `medium` = reviewed, `low` = draft |
| `tags` | yes | At least one; lowercase, hyphenated |
| `related` | yes | Empty array `[]` if none. Symmetry is recommended (if A lists B, B should list A) but not enforced |
| `summary_1line` | yes | ≤ 80 chars. Used by the LLM to triage which chunks are relevant |

### Naming

- Files: `kebab-case.md` for all types
- Folders: lowercase plural
- Dates: ISO 8601 (`YYYY-MM-DD`)

## Maintenance Workflow

```
1. Mikel edits a file in wiki/          (in Obsidian)
        ↓
2. [Optional] python scripts/wiki/validate.py
        ↓
3. python scripts/wiki/compile.py
        ↓
4. Restart backend (or hot-reload in future)
        ↓
5. Test in browser
```

### When to update `updated`

Whenever content changes — even a typo. This drives the "stale content"
warning in compile.

### When to add a new file

When the existing files can't represent the new content cleanly. Don't
over-fragment; a project with 5 bullet points doesn't need 5 files.

### When to run validate.py standalone

- Before any commit (well, none — wiki is gitignored — but before any
  `compile.py` if you've done a lot of editing)
- After bulk edits or renames
- When `compile.py` reports validation warnings you don't understand

## Security & Gitignore

### `.gitignore` additions

```gitignore
# Sensitive — personal data, never commit
RAGraw/
wiki/
candidate/
```

### Git history cleanup (public repo)

Required because personal data is already in history:

```bash
# Recommended: git filter-repo
pip install git-filter-repo
git filter-repo --path candidate/ --invert-paths

# Verify history is clean
git log --all --full-history -- candidate/
# (should return nothing)

# Force-push (if remote exists)
git remote add origin <your-public-url>  # if not yet
git push -u origin main --force
```

**Do this BEFORE the next public push.** If the repo is already public and
has been cloned by others, the data is out — `filter-repo` only protects
future clones. Consider the data exposed and rotate anything that depends
on it (e.g., if `stories.md` mentions a project name used as a password
elsewhere — unlikely but worth a thought).

### `candidate/` after cleanup

After `git filter-repo`, `candidate/` exists locally but is not tracked.
Mikel's local `candidate/` files (the current 4 `.md` + `profile.json`) get
replaced by the first `compile.py` run.

## Testing

### Unit (must pass)

- `compile.py` against a fixture wiki (3 files: one project, one story, one
  opinion) → assert `candidate/docs/projects.md` contains both project
  headings, `candidate/docs/stories.md` contains the STAR sections, and
  `candidate/profile.json` has the expected fields
- `compile.py` against a wiki with one file missing `related:` → assert
  warning logged, compile still succeeds
- `compile.py` against a wiki with one file where `type` doesn't match
  folder → assert that file is skipped, others compile
- `compile.py` against a wiki with broken `related:` link → assert warning
- `validate.py` against a known-good wiki → exits 0
- `validate.py` against a wiki with bad frontmatter → exits 1, error report
  identifies the file

### Integration (must pass)

- Compile the full wiki (when populated) → start backend → query
  "¿Qué stack usaste en InterviewTTS?" → assert the project chunk for
  `interview-tts.md` is in the top-K
- Compile a wiki with no files (empty `wiki/`) → start backend → query
  anything → assert RAG returns empty (existing graceful behavior)

### Manual (Mikel, periodically)

- Open `wiki/` in Obsidian → graph view shows clusters by type
- Click a `[[link]]` → it resolves
- Edit a file, run compile, restart backend, ask the twin a question about
  that edit → answer reflects the new content

## Rollout Plan

1. **History cleanup** — `git filter-repo` to scrub `candidate/` from history
2. **Gitignore update** — add `RAGraw/`, `wiki/`, `candidate/`
3. **Scaffold** — create empty `wiki/` with `CONVENCIONES.md`, 8 templates in
   `wiki/templates/`, and the 8 folder stubs (empty)
4. **Scripts** — `scripts/wiki/compile.py`, `validate.py`,
   `generate_index.py`, `README.md`
5. **First content** — extract `profile/mikel.md` from the PDF in `RAGraw/`
6. **First compile** — run compile, start backend, smoke test
7. **Iterate** — add projects, skills, stories one at a time. Re-compile +
   re-test after each batch
8. **Document** — update `PLAN.md` with a "Knowledge base" section pointing
   to this design doc

## Open Questions

- **Profile.json shape on conflict**: if Mikel edits `candidate/profile.json`
  directly (legacy), the next `compile.py` will overwrite it. Document this
  clearly in `wiki/CONVENCIONES.md` so it's not a surprise.
- **Story count target**: 5-10 STAR stories is a healthy starting corpus for
  a junior. More is better but quality > quantity. Track as a manual goal.
- **Confidence lifecycle**: when does `medium` become `high`? Rule of thumb:
  if the story has been used in a real interview and the feedback was
  positive, bump to `high`. Codify in `CONVENCIONES.md`.
- **Obsidian config**: should we vendor an `.obsidian/` config (graph
  settings, color scheme) so the wiki looks consistent across machines?
  Defer until Mikel asks.
