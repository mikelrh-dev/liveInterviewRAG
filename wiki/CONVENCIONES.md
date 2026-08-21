# CONVENCIONES — Person Wiki

> Schema, naming, and workflow conventions for the InterviewTTS digital twin knowledge base.
> Inspired by the PaginaWebPracticas wiki convention system, adapted for a person-centric ontology.

---

## Frontmatter Schema

Every file in `wiki/` (except `CONVENCIONES.md`, `index.md`, and `templates/`) MUST start with this YAML frontmatter:

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

| Field | Required | Validation |
|-------|----------|------------|
| `type` | yes | Must match parent folder name exactly (profile, project, experience, skills, story, opinion, decision, faq) |
| `title` | yes | kebab-case; should equal filename stem |
| `created` | yes | ISO 8601 date (`YYYY-MM-DD`) |
| `updated` | yes | ISO 8601 date; update whenever content changes |
| `confidence` | yes | `high` = CV-verified or interview-tested, `medium` = inferred/reviewed, `low` = draft/placeholder |
| `tags` | yes | List of strings; at least one; lowercase hyphenated |
| `related` | yes | Relative paths from `wiki/` (e.g., `projects/interview-tts.md`). Empty array `[]` if none. Symmetry recommended but not enforced |
| `summary_1line` | yes | ≤ 80 characters. Used by the LLM to triage relevance |

### Type-folder matching

The `type` field MUST match the parent folder name exactly. A file at `wiki/stories/foo.md` with `type: project` is invalid — `scripts/wiki/validate.py` will reject it (exit 1), and `scripts/wiki/compile.py` will skip it silently with a log message.

---

## Naming Conventions

- **Files**: `kebab-case.md` for all types (e.g., `interview-tts.md`, `junior-dam-mercadona-2019-2024.md`)
- **Folders**: lowercase plural (`projects/`, `skills/`, `stories/`, not `project/` or `skill/`)
- **Dates**: ISO 8601 (`YYYY-MM-DD`) in frontmatter `created` and `updated` fields
- **Exception**: `wiki/profile/mikel.md` is the ONLY expected file in `wiki/profile/`

### Filename patterns by type

| Type | Pattern | Example |
|------|---------|---------|
| profile | `mikel.md` (single file) | `profile/mikel.md` |
| project | `<project-slug>.md` | `projects/interview-tts.md` |
| experience | `<role-company-startyear-endyear>.md` | `experience/gerente-mercadona-2019-2024.md` |
| skills | `<domain>.md` | `skills/backend.md` |
| story | `<story-slug>.md` | `stories/conflicto-equipo.md` |
| opinion | `<opinion-slug>.md` | `opinions/por-que-python.md` |
| decision | `<decision-slug>.md` | `decisions/elegir-dam.md` |
| faq | `<faq-slug>.md` | `faq/presentacion-30s.md` |

---

## Compile-Overwrite Contract

> **IMPORTANT**: `scripts/wiki/compile.py` OWNS `candidate/`. It writes atomically (temp dir → `os.replace()`) and overwrites everything in `candidate/` on every run.

- The wiki (`wiki/`) is the **source of truth**. Edits to `candidate/` are overwritten on the next compile.
- If you need to preserve something in `candidate/`, add it to the wiki and recompile.
- `compile.py` strips frontmatter, concatenates by type, and synthesizes `profile.json`. No manual edits survive.
- `validate.py` is a read-only quality gate. Run it before compile if you've done bulk edits.
- `generate_index.py` regenerates `wiki/index.md` unconditionally. Do not edit `wiki/index.md` by hand.

---

## Maintenance Workflow

```
1. Edit wiki file in Obsidian (or any text editor)
       ↓
2. [Optional] python scripts/wiki/validate.py  (quality gate)
       ↓
3. python scripts/wiki/compile.py              (wiki/ → candidate/)
       ↓
4. Restart backend (uvicorn —reload catches changes)
       ↓
5. Test in browser — ask the twin about the new content
```

### When to update `updated`

Always — even for typos. The `updated` field drives the stale-content warning in compile (`confidence=low` + `updated` within 7 days → warning).

### When to add a new file

When existing files can't represent the content cleanly. Don't over-fragment — a project with 5 bullets doesn't need 5 files. One file per project, one file per skill domain, one file per STAR story.

---

## Confidence Lifecycle

| Level | Meaning | When to use | How to promote |
|-------|---------|-------------|----------------|
| `high` | Verified, interview-tested | Data directly from CV or used in a real interview successfully | Already at max |
| `medium` | Reviewed but not tested | Inferred from codebase, projects, or user review | After a real interview with positive feedback on that content |
| `low` | Draft / placeholder | `[TODO: ask Mikel]` markers, AI-inferred content awaiting user confirmation | After user reviews and approves |

**Rule**: A story with `confidence=low` that hasn't been updated in 7+ days is flagged as stale content during compile.

---

## Graph Symmetry Convention

`related:` entries SHOULD be reciprocal. If `wiki/projects/interview-tts.md` lists `[skills/backend.md]`, then `wiki/skills/backend.md` should also list `[projects/interview-tts.md]`. This is NOT enforced — `validate.py` issues a warning for missing reciprocals, but `compile.py` does not fail.

---

## File System Layout

```
wiki/
├── CONVENCIONES.md          ← This file
├── index.md                 ← Auto-generated by generate_index.py
├── templates/               ← 8 generic templates (profile, project, experience, skills, story, opinion, decision, faq)
├── profile/                 ← Single mikel.md
├── projects/                ← One .md per project
├── experience/              ← One .md per role/period
├── skills/                  ← One .md per domain
├── stories/                 ← One .md per STAR story
├── opinions/                ← One .md per strong opinion
├── decisions/               ← One .md per career/technical decision
└── faq/                     ← One .md per common interview question
```
