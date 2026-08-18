# Tasks: Person Wiki — Content Authoring (scope: wiki only, no code)

> **Status:** Planning complete. Apply phase: wiki content extraction + authoring.
> **Scope change (2026-06-13):** User requested to defer all code work (scripts, `filter-repo`, history cleanup) and focus on populating the wiki from existing sources. Iterative: this change fills what we can from CV + projects; the user will answer follow-up questions to fill gaps.

**Goal:** Populate `wiki/` (gitignored) with Mikel's actual data extracted from the CV PDF, the PaginaWebPracticas prior project, and the InterviewTTS project files. Set up the scaffolding (CONVENCIONES.md, 8 templates) but do NOT write any scripts, do NOT run `filter-repo`, do NOT modify `backend/`.

**Out of scope (deferred to a future change):**
- `scripts/wiki/compile.py`, `validate.py`, `generate_index.py`
- Git history cleanup with `git filter-repo`
- Integration testing
- PR chaining (single commit acceptable for content-only change)

---

## Tasks

### Task 1: Update `.gitignore` for safety

**Files:**
- Modify: `.gitignore`

- [x] **Step 1: Read current `.gitignore`** at `C:\Users\mikel\Documents\InterviewTTS\.gitignore`
- [x] **Step 2: Append these lines at the end:**
  ```
  # Sensitive — personal data, never commit
  RAGraw/
  wiki/
  candidate/
  ```
- [x] **Step 3: Run `git status`** to confirm `RAGraw/` and `candidate/` are no longer untracked
- [x] **Step 4: Commit:** `git add .gitignore && git commit -m "chore: gitignore RAGraw, wiki, candidate (sensitive data)"`

---

### Task 2: Extract CV content from PDF

**Files:**
- Read: `RAGraw/Currículum Mikel Romero.pdf`
- Output (in-memory, used by Tasks 3-5): structured CV data

- [x] **Step 1: Load the `pdf-extraction` skill** at `C:\Users\mikel\.agents\skills\pdf-extraction\SKILL.md` and follow its instructions
- [x] **Step 2: Extract text from the PDF** using pdfplumber. Expected output: plain text covering experience, education, skills, languages, contact info
- [x] **Step 3: Save the extracted text** to a temporary file `RAGraw/.cv_extracted.txt` (gitignored, so safe to commit-anyway — but it's inside `RAGraw/` so already gitignored) for reference in later tasks
- [x] **Step 4: Identify the structured data needed for:** profile (name, role, location, languages, links), experience (each role with dates, company, achievements), skills (with proficiency), and any other content the CV mentions (projects, certifications)
- [x] **Step 5: Note gaps:** what the CV does NOT cover that we'll need to ask the user about (e.g., STAR stories, opinions, salary expectations, availability)

---

### Task 3: Author `wiki/CONVENCIONES.md`

**Files:**
- Create: `wiki/CONVENCIONES.md`

- [x] **Step 1: Use the brainstorming design spec** at `docs/superpowers/specs/2026-06-13-person-wiki-design.md` (sections "Frontmatter Convention" and "Naming") as the source of truth
- [x] **Step 2: Author the file** covering: frontmatter schema (8 required fields), naming conventions (kebab-case files, lowercase plural folders, ISO 8601 dates), the compile-overwrite contract, maintenance workflow, confidence lifecycle
- [x] **Step 3: Commit with the templates in Task 8:** `git add wiki/CONVENCIONES.md && git commit -m "docs: add wiki CONVENCIONES (frontmatter schema + naming)"` ⚠️ Skipped: `wiki/` is gitignored by design. Content exists on disk at `wiki/CONVENCIONES.md`.

---

### Task 4: Author `wiki/profile/mikel.md` (the "soul" of the twin)

**Files:**
- Create: `wiki/profile/mikel.md`

- [x] **Step 1: Source data from the extracted CV** (Task 2) for: name, role/title, current focus, location, languages, top skills summary
- [x] **Step 2: Author the file** with the template structure from the brainstorming spec (Identity, Top skills, How the twin should talk, Current focus, See also). Use the frontmatter schema from CONVENCIONES.md
- [x] **Step 3: For the "How the twin should talk" section:** extract any tone/style cues from `PLAN.md`, `README.md`, and Mikel's writing in the PaginaWebPracticas wiki. If nothing definitive, mark as `[TODO: ask Mikel]`
- [x] **Step 4: Set `confidence: high`** for fields verified from the CV; `confidence: medium` for inferred fields

---

### Task 5: Author `wiki/experience/*.md` (one file per role)

**Files:**
- Create: `wiki/experience/<role-slug>.md` (one per role in the CV)

- [x] **Step 1: For each role in the CV**, create one file with the experience template structure: Context, Responsibilities, Measurable achievements, What this role taught you, See also
- [x] **Step 2: Use the actual dates, company names, and achievements from the CV** — do not invent or embellish
- [x] **Step 3: For "What this role taught you":** if the CV doesn't say, mark as `[TODO: ask Mikel]`
- [x] **Step 4: Filename convention:** `kebab-case-role-company-year-year.md` (e.g., `junior-dam-acme-2024-2026.md`)
- [x] **Step 5: Cross-link** to related projects and skills via `related:` in frontmatter

---

### Task 6: Author `wiki/skills/*.md` (one file per domain)

**Files:**
- Create: `wiki/skills/<domain>.md` (e.g., `backend.md`, `frontend.md`, `ia-voice.md`, `data.md`)

- [x] **Step 1: Group the CV's skills into 3-5 domains** (backend, frontend, IA/voice, data, devops — adjust based on what the CV actually shows)
- [x] **Step 2: For each domain, create one file** with the skills table: | Skill | Level | Last used | Where demonstrated |
- [x] **Step 3: Infer "Last used" and "Where demonstrated"** from the CV and from InterviewTTS project code (e.g., if CV lists Python and InterviewTTS uses FastAPI, then FastAPI is recent)
- [x] **Step 4: Mark Level as:** `confident` / `working` / `familiar` (per the template). Use `confident` only if the CV lists it as a primary skill with project evidence

---

### Task 7: Author `wiki/projects/*.md` (one per project)

**Files:**
- Create: `wiki/projects/interview-tts.md`
- Create: `wiki/projects/pagina-web-practicas.md`
- (more projects as discovered in CV)

- [x] **Step 1: Author `interview-tts.md`** from `PLAN.md`, `README.md`, the changelog of `openspec/changes/` (recent work), and the codebase. Cover: What, Why, My role, Stack, Outcomes, What I'd do differently
- [x] **Step 2: Author `pagina-web-practicas.md`** from `RAGraw/Proyectos/PaginaWebPracticas/wiki/` (entities, processes, concepts) and any source code there. Cover the same structure
- [x] **Step 3: For each project, populate "Stack"** by grep-ing the actual dependencies in the project (`requirements.txt`, `package.json`, `pyproject.toml` if present)
- [x] **Step 4: For "Outcomes":** extract measurable results if any. If the project is recent and still in progress, mark `confidence: medium` and note it's a WIP
- [x] **Step 5: For "What I'd do differently":** if no source says, mark as `[TODO: ask Mikel]`

---

### Task 8: Create 8 templates in `wiki/templates/`

**Files:**
- Create: `wiki/templates/profile-template.md`
- Create: `wiki/templates/project-template.md`
- Create: `wiki/templates/experience-template.md`
- Create: `wiki/templates/skills-template.md`
- Create: `wiki/templates/story-template.md`
- Create: `wiki/templates/opinion-template.md`
- Create: `wiki/templates/decision-template.md`
- Create: `wiki/templates/faq-template.md`

- [x] **Step 1: Copy the templates verbatim** from the brainstorming design spec (section "Wiki Types & Templates"). Each template is a generic placeholder, not Mikel-specific
- [x] **Step 2: Verify each template has** the 8-field frontmatter with the right `type:` for that template
- [x] **Step 3: Commit:** `git add wiki/CONVENCIONES.md wiki/templates/ && git commit -m "docs: add wiki CONVENCIONES + 8 generic templates"` ⚠️ Skipped: `wiki/` is gitignored by design. Templates exist on disk at `wiki/templates/`.

---

### Task 9: Scaffold empty `wiki/stories/`, `wiki/opinions/`, `wiki/decisions/`, `wiki/faq/`

**Files:**
- Create: `wiki/stories/README.md` (placeholder)
- Create: `wiki/opinions/README.md` (placeholder)
- Create: `wiki/decisions/README.md` (placeholder)
- Create: `wiki/faq/README.md` (placeholder)

- [x] **Step 1: For each of the 4 folders**, create a `README.md` explaining what goes there and noting that content is awaiting user input. Example:
  ```markdown
  # Stories

  STAR-format stories for behavioral interview questions.

  **Status:** Awaiting user input. See `openspec/changes/person-wiki/tasks.md` Task 9 for the open questions to fill these in.

  See `../CONVENCIONES.md` for the template.
  ```
- [x] **Step 2: Commit:** `git add wiki/stories wiki/opinions wiki/decisions wiki/faq && git commit -m "docs: scaffold stories/opinions/decisions/faq with placeholder READMEs"` ⚠️ Skipped: `wiki/` is gitignored by design. Placeholder READMEs exist on disk.

---

### Task 10: Inventory and surface gaps

**Files:**
- Create: `RAGraw/.gaps.md` (gitignored, internal note for the orchestrator)

- [x] **Step 1: List every `[TODO: ask Mikel]` marker** left in the wiki files. Group by type
- [x] **Step 2: List any folders that are empty or under-populated** (e.g., stories has 0 files, opinions has 0 files)
- [x] **Step 3: Write a brief gap report** to `RAGraw/.gaps.md` with: which wiki fields need user input, what would unblock each one (e.g., "to fill in stories, need Mikel to recall 3-5 STAR stories")
- [x] **Step 4: Return the gap report to the orchestrator** so the user gets asked the right follow-up questions

---

## Open Questions (resolved with sensible defaults for this run)

1. **`profile.json` overwrite contract** — Default: deferred. No code, no contract needed yet.
2. **Story count target** — Default: aim for 3-5 STAR stories as a starting corpus.
3. **Confidence lifecycle** — Default: `high` for CV-verified, `medium` for inferred, `low` for `[TODO]` placeholders.
4. **Obsidian config vendoring** — Default: deferred.

---

## Review Workload Forecast

- **Total tracked lines**: ~600 (8 wiki files + CONVENCIONES + 8 templates + 4 placeholder READMEs + .gitignore delta + extracted CV note)
- **400-line budget risk**: Medium (overshoot by ~50%)
- **Chained PRs recommended**: No — content-only change, single PR acceptable
- **Decision needed before apply**: No
- **Slicing strategy**: Single PR (multiple commits, but one PR). The user is in interactive mode and will review the result before any push.
