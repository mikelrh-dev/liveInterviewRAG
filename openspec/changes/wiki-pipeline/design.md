# Design: wiki-pipeline

> **Change ID:** `wiki-pipeline`
> **Depends on:** `person-wiki` (content complete)
> **Specs:** `candidate-profile`, `wiki-pipeline`, `wiki-deployment`
> **Constraint:** Zero modifications under `backend/`

---

## 1. Executive summary

Deliver three stdlib-first scripts (`scripts/wiki/{validate,compile,generate_index}.py`) plus `scripts/deploy.sh` that turn the hand-authored wiki (`wiki/`, per `CONVENCIONES.md`) into the deployed `candidate/` directory consumed by `backend/services/candidate.py` — untouched — followed by git hygiene that stops tracking personal data. Compile emits `profile.json` in exactly today's consumer shape and one `.md` per wiki file under `candidate/docs/`; writes are atomic (temp dir → two-step rename swap) and byte-stable across runs.

**Affected service modules:** NONE modified (constraint). `backend/services/candidate.py` is the *read-only contract source*: its `_load_profile_json` (lines 32–45) and `_load_markdown_docs` (lines 75–96) define what compile must emit, and `get_context_string()` (lines 98–140) defines which `profile.json` keys are rendered.

---

## 2. Verified design inputs

### 2.1 Loader compatibility evidence (VERIFIED)

Read directly from `backend/services/candidate.py` during this design phase:

- `_load_markdown_docs` (lines 75–96): iterates `sorted(docs_dir.glob("*.md"))` over `candidate/docs/` — **arbitrary filenames**, not a fixed allow-list. Every `.md` found is loaded into `self.documents[md_file.name]`.
- Legacy aggregate names (`cv.md`, `projects.md`, `skills.md`, `stories.md`) appear only in a `missing_sections` list used for a **log line** ("missing: …") — absence is a warning, never an error. Confirms spec assumption: one-doc-per-file granularity is safe with zero loader changes.
- `_load_profile_json` (lines 32–45): reads `candidate/profile.json` wholesale via `json.load`; downstream access uses `.get(key, default)` for exactly these keys: `name`, `title`, `summary`, `skills` (list of strings joined by `,`), `experience` (list of `{company, role, period, highlights[]}`), `projects` (list of `{name, description, technologies[], highlights[]}`), `stories` (list of `{situation, task, action, result}`).
- `profile.json["documents"]` exists in today's file but is **never read** by any backend code → compile SHALL NOT emit it (spec: "only the current consumer keys").

### 2.2 PyYAML availability

`backend/services/rag.py` line 14 does `import yaml` — PyYAML is already an implicit runtime dependency of the project (not pinned in `backend/requirements.txt`). Scripts MAY use PyYAML for frontmatter parsing. A tasks-phase nicety (optional, outside the zero-backend constraint since it touches only a manifest, not code): add `pyyaml>=6.0` to `backend/requirements.txt` to make it explicit. Fallback if rejected: scripts vendor a minimal `key: value` frontmatter splitter — rejected by default because nested lists (`tags`, `related`) make hand-parsing error-prone.

---

## 3. Architecture decisions

| # | Decision | Rationale | Tradeoff accepted |
| --- | ---------- | ----------- | ------------------- |
| D1 | Compile emits `profile.json` in the exact current shape (7 keys) | Resolved proposal Q1; persona prompt unchanged; silent-breakage risk eliminated | Wiki body structure must be parsed heuristically (see §4) |
| D2 | One `.md` per wiki file (no aggregation) | Resolved proposal Q2; verified loader compatibility (§2.1) | `docs/` grows with page count; irrelevant — loader globs everything |
| D3 | Two-step rename swap for "atomicity": `candidate/ → candidate.prev/ → tmp → candidate/` | `os.replace()` cannot atomically overwrite a non-empty directory on Windows or POSIX (`ENOTEMPTY`); the two-rename dance is the closest cross-platform equivalent | Microsecond window between renames where `candidate/` doesn't exist; acceptable because the process holds both copies and failure between renames leaves `candidate.prev/` intact |
| D4 | Validation re-runs inside compile (gate duplicated, not shared as import) | Deploy calls only `deploy.sh`; compile alone must also be safe standalone. Implementation detail: compile imports validate's check function (same package, single source of truth) — the *behavior* is duplicated, not the code | None |
| D5 | Errors vs warnings split exactly along CONVENCIONES lines | Schema/frontmatter/link errors block (exit ≠ 0, zero writes); reciprocal-link asymmetry and stale low-confidence (>7 days) warn only | Gate strictness limited to CONVENCIONES-mandated rules (proposal risk mitigation) |
| D6 | Bash for deploy.sh despite Windows dev box | Target environment is Linux VPS + systemd; script is documented as run on-VPS or via SSH/WSL. Pure-Python parts stay cross-platform | Mikel must have WSL/Git-Bash or SSH to run deploys from Windows |
| D7 | Server-side retention via `candidate.prev/` rename (not tarball) | Symmetric rollback = `mv candidate.prev candidate && restart`; zero extra tooling | Only ONE prior version retained |

---

## 4. profile.json mapping table (authoritative)

Source: `wiki/profile/mikel.md` (frontmatter + body sections verified above) and `wiki/projects/*.md`, `wiki/stories/*.md`. Consumer: `CandidateProfile.get_context_string()`. Emit EXACTLY these top-level keys, in this order:

| JSON key | JSON type | Wiki source | Extraction rule |
| ---------- | ----------- | ------------- | ----------------- |
| `name` | string | `mikel.md` body → `## Identity` → `- **Name:** X` | Text after bold label, trimmed |
| `title` | string | `mikel.md` body → `## Identity` → `- **Role:** X` | Text after bold label |
| `summary` | string | `mikel.md` frontmatter → `summary_1line` | Verbatim (≤80 chars guaranteed by validate) |
| `skills` | string[] | `mikel.md` body → `## Top skills (summary)` bullets | Each bullet `- **Domain:** a, b, c` → flatten items after the colon, trimmed, in order |
| `experience` | object[] | `mikel.md` body → `## Career timeline (corrected)` bullets | Each bullet `- **PERIOD:** Role, Company` → `{role, company, period, highlights: []}`. Parse `**2016–2019:** Encargado, BM Supermercados` → period=`2016–2019`, split remainder on first comma |
| `projects` | object[] | every `wiki/projects/*.md` | `{name}` = filename stem; `{description}` = frontmatter `summary_1line`; `{technologies}` = frontmatter `tags`; `{highlights}` = body bullet lines (H2/H1 headings excluded), in document order |
| `stories` | object[] | every `wiki/stories/*.md` | `{situation, task, action, result}` = text under matching `## Situation` / `## Task` / `## Action` / `## Result` H2 sections (case-insensitive match, fallback: whole body as `situation` with others empty) |

Ordering rules (byte-stability, see §6): `experience` in timeline order; `projects` and `stories` sorted by filename stem ascending. Serialization: `json.dump(..., indent=2, ensure_ascii=False)` + trailing `\n`, fixed key-insertion order (no `sort_keys` — order matches table above).

Edge cases pinned:

- Missing `## Identity` fields → compile fails with actionable error (defensive; validate can't know body semantics, so compile owns body-shape checks for profile only).
- A project/story file with malformed body still compiles: only `profile/mikel.md` gets body-shape enforcement; other types degrade gracefully (empty lists).
- `documents` key deliberately absent (never read by backend).

---

## 5. Type ↔ folder mapping (8 types)

From CONVENCIONES "Type-folder matching" + "Filename patterns":

| `type:` (singular) | Folder (plural) | Output doc naming |
| -------------------- | ----------------- | ------------------- |
| `profile` | `profile/` | special: synthesizes `profile.json`, NOT a doc |
| `project` | `projects/` | `docs/project-<stem>.md` |
| `experience` | `experience/` | `docs/experience-<stem>.md` |
| `skills` | `skills/` | `docs/skills-<stem>.md` |
| `story` | `stories/` | `docs/story-<stem>.md` |
| `opinion` | `opinions/` | `docs/opinion-<stem>.md` |
| `decision` | `decisions/` | `docs/decision-<stem>.md` |
| `faq` | `faq/` | `docs/faq-<stem>.md` |

Notes:

- Doc filenames get the `<type>-` prefix to avoid stem collisions across folders (loader keys docs by bare filename). Prefixing is deterministic and keeps provenance visible.
- Excluded from scanning always: `CONVENCIONES.md`, `index.md`, `templates/` (mirrors loader `_SKIP_DIRS`/`_SKIP_FILES`, but enforced independently in scripts).
- `type:` ≠ parent folder → validate ERROR; compile skip-with-log (defense in depth; unreachable when validate gates first).

---

## 6. Script contracts

All scripts: pure stdlib + `yaml` (§2.2), `pathlib` throughout, runnable from repo root on Windows dev box. Shared helpers live in `scripts/wiki/_common.py` (frontmatter parser, type↔folder map, walk-and-skip logic) — counted within line budget.

### `scripts/wiki/validate.py`

```
Usage: python scripts/wiki/validate.py [--wiki PATH]
```

- Read-only. Scans all 8 folders + `profile/mikel.md`.
- **Errors (any → exit 1, nothing written ever):** missing required frontmatter field; `type:` not in enum or ≠ parent folder; `title` not kebab-case or ≠ filename stem; `created`/`updated` not `YYYY-MM-DD` or invalid calendar date; `confidence` not in enum; `tags` missing/empty; `related` entry not resolving to an existing `wiki/` path (paths relative to `wiki/`, e.g. `projects/interview-tts.md`; tolerate `[[...]]`-style by stripping brackets and appending `.md`); `summary_1line` > 80 chars.
- **Warnings (exit stays 0):** asymmetric `related:` links (Graph Symmetry Convention); `confidence: low` with `updated` older than 7 days.
- Output: one line per finding — `[ERROR] <relpath>: <message>` / `[WARN] <relpath>: <message>` — then `Validation failed: N errors, M warnings` or `OK: N files valid, M warnings`. Exit codes: `0` clean-or-warnings-only, `1` any error, `2` usage/IO error.

### `scripts/wiki/compile.py`

```
Usage: python scripts/wiki/compile.py [--wiki PATH] [--out PATH]
```

- Defaults: `--wiki wiki/`, `--out candidate/`.
- Step 1: run validation internally (imported from validate module). Any error → print findings, exit 1, **zero writes** (temp dir never created).
- Step 2: build complete tree in `<out>.tmp-<pid>/`: `profile.json` (per §4), `docs/*.md` (per §5, frontmatter stripped, body verbatim UTF-8).
- Step 3: swap (D3): if `<out>` exists → `os.replace(out, out.prev)`; then `os.replace(tmp, out)`; then `shutil.rmtree(out.prev)`. Print replaced-file count summary.
- Stale-content warning printed during build (non-blocking, D5).
- Exit codes: `0` success, `1` validation error, `2` IO/usage error.
- Idempotency/byte-stability guarantees: sorted iteration everywhere (`sorted(rglob)` / filename-sorted synthesis); fixed JSON key order + `indent=2, ensure_ascii=False` + `\n`; doc bodies copied byte-for-byte from source minus frontmatter. Two consecutive runs → identical bytes (proven by test).

### `scripts/wiki/generate_index.py`

```
Usage: python scripts/wiki/generate_index.py [--wiki PATH]
```

- Unconditionally regenerates `wiki/index.md`: one section per type (8 types, fixed order per §5 table), entries sorted by `updated` descending, tie-break filename asc; each line: `- [title](<folder>/<file>) — summary_1line (updated YYYY-MM-DD, confidence=X)`. Header notes "AUTO-GENERATED — do not edit".
- Exit codes: `0` success, `1` scan error. Writes ONLY `wiki/index.md`.

### `scripts/deploy.sh`

See §7 sequence diagram. Configuration via env vars with defaults at top: `VPS_HOST`, `VPS_USER` (sudo-capable), `SSH_PORT=22`, `REMOTE_DIR` (VPS path containing `candidate/`). Assumes SSH public-key auth (proposal resolved Q3).

---

## 7. Deploy pipeline (sequence diagram)

```mermaid
sequenceDiagram
    participant O as Operator (Windows)
    participant S as deploy.sh (bash, set -euo pipefail)
    participant P as Python pipeline
    participant V as VPS (Linux, systemd)

    O->>S: ./scripts/deploy.sh
    S->>P: python scripts/wiki/validate.py
    alt validation errors
        P-->>S: exit 1
        S-->>O: ABORT — no compile/rsync/restart
    end
    P-->>S: exit 0
    S->>P: python scripts/wiki/compile.py
    alt compile failure
        P-->>S: exit 1
        S-->>O: ABORT — candidate/ untouched (atomicity)
    end
    P-->>S: exit 0
    S->>V: ssh $VPS_USER@$VPS_HOST mv candidate/ candidate.prev/
    S->>V: rsync -az --delete -e "ssh -p $SSH_PORT" candidate/ $VPS_USER@$VPS_HOST:$REMOTE_DIR/candidate/
    S->>V: ssh ... "echo 'Replaced:'; ls candidate/docs/ | wc -l"
    S->>V: ssh ... "sudo systemctl restart interviewtts.service"
    alt any remote step fails
        S-->>O: ABORT (set -e) — prior state recoverable at candidate.prev/
    end
    S-->>O: DEPLOY OK (print summary + rollback hint)
```

Semantics pinned:

- **Abort-on-first-failure:** `set -euo pipefail`; every step's exit code checked implicitly; later steps never execute after a failure.
- **Wiki wins:** rsync is unconditional, no prompts, including first deploy (resolved Q4). `--delete` makes VPS `candidate/` mirror compiled output exactly.
- **Retention mechanics:** the pre-rsync `mv candidate/ candidate.prev/` keeps exactly one prior version server-side; rollback = `mv candidate.prev candidate/ && sudo systemctl restart interviewtts.service`. Next successful deploy rotates `candidate.prev/` (script removes any pre-existing `candidate.prev/` right after a successful rsync).
- **Windows-dev/Linux-target:** the three Python scripts run natively on Windows (`python scripts/wiki/…`). `deploy.sh` is bash/systemd-oriented and is meant to be executed ON the VPS checkout or via SSH from WSL/Git-Bash — documented in README. All remote commands go through `ssh`, never assume local Linux.
- **SSH assumptions (resolved Q3):** public-key auth, sudo-capable user for `systemctl`, port 22 (overridable via `SSH_PORT`). No secrets embedded in the script.

---

## 8. Git hygiene — ordered steps

Strict order; steps 1–3 land BEFORE any script work so the rollback anchor covers pre-change state:

1. `git tag pre/wiki-pipeline` on last pre-change commit (rollback anchor; `git show pre/wiki-pipeline:candidate/docs/cv.md > cv.md` restores any tracked file).
2. Snapshot working `candidate/` to a zip OUTSIDE the repo (manual step, documented; belt-and-suspenders beyond the tag).
3. Implement scripts + tests (normal feature commits).
4. Dedicated hygiene commit: `git rm -r --cached candidate/` (files remain on disk) AND append `wiki/` to `.gitignore` in the SAME commit — single reviewable privacy commit.
5. Verify: `git ls-files candidate/` → empty; `git status` shows `wiki/` ignored; `git status` shows `candidate/` files as deleted-from-index only once.

Docs deliverable (step 3 tail): README section describing edit → validate → compile → deploy loop + manual push-after-edit backup of `wiki/` to a private GitHub repo (resolved Q5: documented workflow only, no hook point built).

---

## 9. Data flow (one deploy cycle)

```
wiki/**.md ──validate──► pass/fail
      │ fail                │ pass
      ▼                     ▼
   (abort)            compile: frontmatter strip + §4 synthesis
                              │
                    candidate.tmp-<pid>/{profile.json, docs/*.md}
                              │ rename dance (D3)
                              ▼
                        candidate/ (owned, regenerated)
                              │ rsync --delete
                              ▼
                        VPS candidate/ (+ candidate.prev/)
                              │ systemctl restart
                              ▼
              CandidateProfile.load() at startup → RAG index + persona prompt
```

---

## 10. Test strategy (strict TDD)

New test files under `tests/` (existing suite untouched; `python -m pytest tests/ -v` must stay green):

| File (write FIRST, red) | Covers | Key cases |
| --- | --- | --- |
| `tests/test_wiki_validate.py` | validate.py | good fixture exits 0 & writes nothing (assert mtimes unchanged); each error class (missing field, bad type/folder, bad date, long summary, broken link) → exit 1 + named file in output; asymmetry + staleness → exit 0 with WARN lines |
| `tests/test_wiki_compile.py` | compile.py | §4 mapping assertions against golden `profile.json` values (name/title/skills flattened/experience parsed/stories STAR); one-doc-per-file with `<type>-` prefixes; blocking-gate proof (bad fixture → exit 1 AND pre-existing candidate bytes identical); idempotency (two runs → identical bytes); swap leaves no `*.tmp-*` residue |
| `tests/test_wiki_generate_index.py` | generate_index.py | all 8 types listed; `updated` desc order; deterministic regeneration (two runs byte-identical); only `index.md` touched |

Fixtures: `tests/fixtures/wiki/good/` (mini-wiki, ≥1 file per all 8 types incl. `profile/mikel.md`) and `tests/fixtures/wiki/bad/` (seeded variants of each error). Tests ALWAYS run against copies in `tmp_path` (pytest builtin) — never the real `wiki/` or `candidate/`. No mocking needed (no external services involved); no new test dependencies.

Implementation order mirrors TDD: validate → compile → generate_index → deploy.sh (smoke-checked manually on VPS; no automated shell test — out of scope for the line budget) → git hygiene → README docs.

---

## 11. Rollback (design-level restatement)

Tag anchor + local zip (§8 steps 1–2); code rollback = delete `scripts/wiki/` + `scripts/deploy.sh`, revert `.gitignore` line; VPS rollback = restore `candidate.prev/` + restart; compile misfire impossible to tear (atomic swap). Zero `backend/` surface to revert.

## 12. Line budget

~330–400 total: `_common.py` ~60, validate ~90, compile ~100, generate_index ~40, deploy.sh ~50, `.gitignore` +1, README ~30. Fits proposal estimate.
