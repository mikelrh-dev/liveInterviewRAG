# Proposal: Person Wiki — Structured Knowledge Base for the Digital Twin

## Intent

The candidate data that powers the digital twin (`candidate/profile.json` + `candidate/docs/*.md`) is currently a flat, manually-edited directory with no schema enforcement, no browsing surface, and no provenance tracking. Adding a new story, updating a skill, or fixing a CV section requires editing monolithic files blind — the twin is only as good as the last manual edit.

The **person wiki** solves this by introducing a browsable, type-structured Markdown knowledge base that compiles deterministically into the existing `candidate/` shape. The user edits individual files in Obsidian; a compile script validates frontmatter, checks graph symmetry, and produces the flat files the backend already ingests. No backend code changes.

**Why now**: The MVP proved the twin works. The bottleneck is now *knowledge quality* — the wiki gives us a scalable editing surface, validation gates, and a clear separation between raw source and compiled artifact.

## Scope

### In Scope

- **Wiki directory** (`wiki/`, gitignored): 8-type folder structure (profile, projects, experience, skills, stories, opinions, decisions, faq) with 8 generic Markdown templates.
- **Wiki index** (`wiki/index.md`): auto-generated table of contents with title, tags, updated, confidence, and summary per file, sorted by recency.
- **Compile script** (`scripts/wiki/compile.py`): walks `wiki/`, validates frontmatter, concatenates per-type files into `candidate/docs/*.md`, and synthesizes `candidate/profile.json`. Atomic writes — never partially overwrites.
- **Validate script** (`scripts/wiki/validate.py`): standalone frontmatter + graph symmetry checker. Optional pre-compile gate.
- **Generate-index script** (`scripts/wiki/generate_index.py`): regenerates `wiki/index.md`.
- **`.gitignore` updates**: add `wiki/`, `candidate/`, `RAGraw/` to prevent future personal-data leaks.
- **Git history cleanup**: `git filter-repo` to scrub `candidate/` from all history (public repo, personal data already committed).
- **`wiki/CONVENCIONES.md`**: documents the frontmatter schema, field rules, naming conventions, and the compile-overwrite contract.

### Out of Scope

- **Backend modifications**: zero changes to `backend/services/`, `backend/main.py`, or any test file. The backend is a downstream consumer that reads `candidate/`.
- **Hot-reload**: the backend must be restarted after `compile.py` runs. Handled manually for MVP.
- **Web UI for editing**: Obsidian is the editor. No admin panel, no rich-text editor.
- **Per-type Pydantic schemas**: a single YAML frontmatter shape with `type` discriminator is sufficient.
- **Encryption at rest**: not warranted for a portfolio project; `.gitignore` is the boundary.
- **Obsidian config vendoring**: `.obsidian/` settings (graph colors, theme) deferred until the user requests it.

## Capabilities

### New Capabilities
- `wiki-compile`: Deterministic compilation from `wiki/` (Markdown + frontmatter) to `candidate/` (flat files the backend ingests). Handles concatenation with heading boundaries, `profile.json` synthesis, and atomic overwrite.
- `wiki-validate`: Frontmatter enforcement (8 required fields, `type` matches parent folder), `related:` link resolution, stale-content warnings.
- `wiki-index`: Auto-generated `wiki/index.md` with per-type tables sorted by `updated` desc, wikilinks to each file.

### Modified Capabilities
None. The `candidate-profile` and `rag-pipeline` specs are unaffected — they consume the same `candidate/` shape. The `conversation-engine` spec is unaffected.

## Approach

```
[RAGraw/]  ──manual──▶  [wiki/]  ──compile.py──▶  [candidate/]  ──existing RAG──▶  [twin responds]
                           │                            │
                           │ (Obsidian)                  │ (gitignored, compiled)
                           ▼                            ▼
                    templates/                      profile.json
                    profile/                        docs/{8 types}.md
                    projects/
                    experience/
                    skills/
                    stories/
                    opinions/
                    decisions/
                    faq/
```

The only new moving part is `compile.py`. Everything downstream is unchanged:

1. **Source materials** live in `RAGraw/` (PDFs, screenshots, raw notes).
2. **Wiki files** are authored in Obsidian under `wiki/`, one file per atomic unit (one project, one story, one skill domain), with YAML frontmatter enforcing 8 fields.
3. **`compile.py`** validates frontmatter, checks `type` ↔ folder match, resolves `related:` links, strips frontmatter from narrative, concatenates per-type, and synthesizes `profile.json`.
4. **`candidate/`** receives the output — identical shape to what the backend already loads.
5. **Backend** picks it up at next restart via `CandidateProfile.load()` and `RAGPipeline.ingest_documents()`.

### Compile mapping summary

| Source (`wiki/`) | Output (`candidate/`) | Transform |
|---|---|---|
| `profile/mikel.md` | `profile.json` + `docs/cv.md` | Frontmatter → JSON fields; body → narrative |
| `projects/*.md` | `docs/projects.md` | Concatenate, each file → `## <title>` section |
| `experience/*.md` | `profile.json["experience"]` + `docs/experience.md` | Structured + narrative |
| `skills/*.md` | `docs/skills.md` | Concatenate, each file → `## <domain>` section |
| `stories/*.md` | `profile.json["stories"]` + `docs/stories.md` | Parse STAR headings + narrative |
| `opinions/*.md` | `docs/opinions.md` | Concatenate |
| `decisions/*.md` | `docs/decisions.md` | Concatenate |
| `faq/*.md` | `docs/faq.md` | Concatenate |

The `profile.json` synthesis is the only "intelligent" part — everything else is concatenation with heading boundaries that align with the existing chunker's regex (`r'\n(?=#{1,3}\s)'`).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `wiki/` | **New** | 8-type folder structure, 8 templates, `index.md` (auto-generated), `CONVENCIONES.md`. All gitignored. |
| `wiki/templates/` | **New** | 8 generic Markdown templates (profile, project, experience, skills, story, opinion, decision, faq) with example frontmatter. |
| `scripts/wiki/compile.py` | **New** | ≈250 lines. Walks `wiki/`, validates, compiles to `candidate/`. |
| `scripts/wiki/validate.py` | **New** | ≈120 lines. Frontmatter + graph symmetry checker. |
| `scripts/wiki/generate_index.py` | **New** | ≈80 lines. Regenerates `wiki/index.md`. |
| `scripts/wiki/README.md` | **New** | Developer docs for the scripts. |
| `.gitignore` | **Modified** | Add `RAGraw/`, `wiki/`, `candidate/`. |
| `candidate/` | **Tracked → gitignored** | Still exists locally, compiled output replaces manual files. |
| `backend/services/` | **Unchanged** | Downstream consumer only. |
| `tests/` | **Unchanged** | No backend test changes needed. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **Personal data already in git history** | Certain | `git filter-repo` before any public push. Critical: if the repo has existing clones, the data is unreachable. |
| **Compile produces invalid `candidate/` output** | Low | Validate step runs before every write; atomic temp+rename prevents partial state. |
| **Frontmatter drift** (fields added/removed without updating schema) | Medium | `validate.py` enforces the 8-field structure; a skipped file is logged explicitly. |
| **User edits `candidate/profile.json` directly (legacy habit)** | Medium | `CONVENCIONES.md` documents the overwrite contract. Next `compile.py` replaces it. |
| **Obsidian graph breaks after rename** | Low | `validate.py` checks all `related:` links resolve. |
| **`.gitignore` misses a path** | Low | Reviewed at proposal time; `git status` double-check after scaffold. |

## Rollback Plan

1. **Cleanest**: revert `.gitignore` to remove `RAGraw/`, `wiki/`, `candidate/` entries. Restore `candidate/` tracking. Delete `scripts/wiki/` and `wiki/` directories. Restore `candidate/` from last known-good state (it's still there unless deleted).
2. **If `filter-repo` already ran**: the repo history is rewritten. Other collaborators need to re-clone. That's fine — this is a solo project and the cleanup is mandatory before going public anyway.
3. **If only scripts exist, no wiki content yet**: delete `scripts/wiki/`, revert `.gitignore`, done.

## Dependencies

- **Python stdlib only** for all 3 scripts (`pathlib`, `yaml` via PyYAML, `datetime`, `json`, `shutil`). No new third-party packages.
- **PyYAML** — already available (used in existing tests).
- **`git-filter-repo`** — `pip install git-filter-repo` (one-time, not a project dependency).
- **Obsidian** — user installs manually (free). Not a code dependency.
- **No backend dependency changes**: `requirements.txt` unaffected.

## Open Questions (from design spec)

These are surfaced for review at proposal time. They'll be resolved before design:

1. **`profile.json` shape on conflict**: if Mikel edits `candidate/profile.json` directly (legacy), the next `compile.py` will overwrite it. The spec proposes documenting this in `CONVENCIONES.md` rather than adding a diff-check. Acceptable?

2. **Story count target**: the spec suggests 5–10 STAR stories as a healthy starting corpus. Is this the right initial target, or should we aim higher/lower?

3. **Confidence lifecycle**: when does `medium` become `high`? Rule of thumb from the spec: after a real interview with positive feedback on that story. Should be codified in `CONVENCIONES.md`. Agreed?

4. **Obsidian config vendoring**: should we vendor an `.obsidian/` config (graph colors, theme, hotkeys) for consistency across machines? The spec defers this — OK to skip for now?

## Success Criteria

- [ ] `wiki/` exists with 8-type folder structure and all 8 templates in `wiki/templates/`.
- [ ] `scripts/wiki/compile.py` walks `wiki/`, validates frontmatter, and produces valid `candidate/profile.json` + all 8 `docs/*.md` files.
- [ ] `scripts/wiki/validate.py` exits 0 on a known-good wiki, exits 1 with a structured error report on a wiki with bad frontmatter.
- [ ] `scripts/wiki/generate_index.py` produces a `wiki/index.md` with per-type tables, wikilinks, and recency sorting.
- [ ] After `compile.py` run and backend restart, a RAG query for wiki content returns relevant chunks in the top-K.
- [ ] `git log --all --full-history -- candidate/` returns nothing after `filter-repo` cleanup.
- [ ] `.gitignore` prevents `RAGraw/`, `wiki/`, `candidate/` from being tracked (verified with `git status` after scaffold).
- [ ] Backend starts and responds normally with the newly compiled `candidate/` — zero regression on existing `pytest` tests.
