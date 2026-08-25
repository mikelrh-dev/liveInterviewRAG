# Proposal: wiki-pipeline

> **Change ID:** `wiki-pipeline`
> **Status:** Draft proposal
> **Depends on:** `person-wiki` (content authoring — complete; its deferred scripts are delivered here)
> **Affected specs:** `candidate-profile`, `rag-pipeline`, `conversation-engine`

---

## Intent

Make the person wiki (`wiki/`) the single source of truth for the digital twin's knowledge, and stop leaking personal data (CV, profile) through the public GitHub repo (`https://github.com/mikelrh-dev/liveInterviewRAG`).

Today there is an inversion of trust: `wiki/` holds the curated knowledge but has no tooling, while `candidate/` — generated-style data containing personal information — is what gets deployed, is still git-tracked despite being listed in `.gitignore`, and can be edited directly on the VPS with nothing reconciling it back.

This change delivers:

1. **The three deferred scripts** from `person-wiki`:
   - `scripts/wiki/compile.py` — compiles `wiki/` → `candidate/` (atomic overwrite via temp dir + `os.replace()`), per the Compile-Overwrite Contract in `wiki/CONVENCIONES.md`.
   - `scripts/wiki/validate.py` — read-only quality gate: YAML frontmatter schema check (all required fields per CONVENCIONES) + internal link checker (`related:` paths). **Blocking**: any invalid frontmatter or broken link exits non-zero and aborts compilation entirely — no partial output.
   - `scripts/wiki/generate_index.py` — regenerates `wiki/index.md` unconditionally (never hand-edited).
2. **A deploy script** (e.g., `scripts/deploy.sh`) that runs the pipeline end-to-end:
   `validate → compile → rsync candidate/ → VPS → systemctl restart interviewtts.service`.
   The wiki **always wins**: rsync overwrites VPS `candidate/` without prompting; direct edits on the VPS are never a source.
3. **Git hygiene**:
   - `git rm -r --cached candidate/` so future commits stop publishing personal data (files remain on disk locally).
   - Add `wiki/` to `.gitignore` (currently missing).
   - Document the backup workflow: user hosts `wiki/` in a **private** GitHub repo (the hosting itself is out of scope for this change's code).

## Business problem

- **Privacy exposure:** `candidate/docs/cv.md` and profile data are tracked in a public repository even though `.gitignore` lists `candidate/` — gitignore does not untrack already-tracked files. Every commit continues to publish personal data.
- **No single source of truth:** `wiki/` was populated by `person-wiki` but has zero backup ("exists only on this disk") and no compile step, so it cannot actually feed the app yet.
- **Operational fragility:** deployment currently depends on personal data living inside the public repo. There is no reproducible path from authored content to running service.
- **Drift risk:** without an overwrite-on-deploy rule, VPS-side edits would silently diverge from the wiki.

## Target users and situations

- **Mikel (sole operator):** edits wiki content in Obsidian, runs one command to validate + deploy; interviews happen against the live twin, so content freshness matters at interview time.
- **Public repo visitors:** must never see CV/personal data going forward (past history exposure is accepted as out-of-scope).

## Scope

### In scope

- `scripts/wiki/{compile,validate,generate_index}.py` (~200–250 lines total, stdlib-first; PyYAML acceptable if already a dependency)
- `scripts/deploy.sh` (or `.py` if shell is too brittle cross-environment)
- `.gitignore`: add `wiki/`
- Git untracking command documented/executed: `git rm -r --cached candidate/`
- Tagged pre-change commit (rollback anchor), e.g. `pre/wiki-pipeline`
- Docs: README section or `docs/` note describing the edit → validate → compile → deploy loop and the private-repo backup workflow for `wiki/`
- Tests under `tests/` following strict TDD (`python -m pytest tests/ -v`)

### Out of scope (non-goals)

- **Any modification to `backend/`** — the candidate loader stays exactly as-is; post-deploy restart covers reload. (Note: the loader already supports an optional `wiki_dir`; we deliberately do not wire it.)
- **History rewrite** (`git filter-repo`) — past exposure of personal data is explicitly accepted.
- **Hosting/automating the private `wiki/` backup repo** — workflow documentation only.
- Reusing `RAGraw/Proyectos/PaginaWebPracticas/scripts/wiki/` — different project, different ontology; conventions come solely from `wiki/CONVENCIONES.md`.

## Business rules encoded by the pipeline

1. Wiki wins: `compile.py` owns `candidate/` and overwrites everything each run; manual `candidate/` edits do not survive.
2. Validation is blocking: schema violations (missing field, bad `type` vs folder, malformed dates, oversized `summary_1line`) or broken links abort with non-zero exit before anything is written or synced.
3. Type-folder matching: `type:` MUST equal parent folder name (singular type ↔ plural folder mapping per CONVENCIONES); violations are rejected by validate, skipped-with-log by compile.
4. Reciprocal `related:` links warn but do not fail (per Graph Symmetry Convention).
5. Stale-content warning: `confidence=low` files older than 7 days surface a warning during compile (non-blocking).

## Affected areas

| Area | Impact |
| --- | --- |
| `scripts/wiki/*` | New (deferred from person-wiki) |
| `scripts/deploy.sh` | New |
| `.gitignore` | Modified (+`wiki/`) |
| Git index | `candidate/` untracked (files stay on disk) |
| `candidate/` | Owned by compile.py; regenerated atomically |
| `backend/` | **None** (constraint) |
| Specs | `candidate-profile` gains the compile-overwrite provenance model; `rag-pipeline` / `conversation-engine` unchanged behaviorally (startup load + restart unchanged) |

## Risks

- **Data-loss during first compile:** overwriting hand-authored `candidate/` content that never made it into the wiki. *Mitigation:* mandatory pre-change tag + local copy of current `candidate/` (see Rollback); diff review before accepting first compile output.
- **Deploy script foot-gun:** unconditional rsync overwrite could clobber a VPS state someone forgot was divergent. *Mitigation:* accepted product decision (VPS is never edited directly); script prints a summary of what it replaced and keeps one server-side prior version (`candidate.prev/` or tarball) as a cheap safety net.
- **Validation strictness blocks deploys:** overly aggressive checks could make the gate annoying. *Mitigation:* only the CONVENCIONES-mandated rules are errors; softer conventions (link symmetry, staleness) are warnings.
- **Untracking is irreversible-ish:** once committed, removal from index is visible in history — fine, since history rewrite is out of scope anyway.
- **Windows/Linux drift:** dev on Windows (this machine), deploy target is Linux VPS. *Mitigation:* pure-Python scripts with `pathlib`; deploy script targets bash/systemd and is documented as run-on-VPS-or-via-SSH.

## Rollback plan

1. **Tag anchor:** create tag `pre/wiki-pipeline` on the last commit before any change lands. Restore point for both code and the tracked `candidate/` contents (`git show pre/wiki-pipeline:candidate/docs/cv.md > …`).
2. **Pre-untrack snapshot:** before `git rm -r --cached candidate/`, archive the working `candidate/` directory (e.g., zip outside the repo). Restoring = unzip + re-add if ever needed.
3. **Code rollback:** delete `scripts/wiki/` and `scripts/deploy.sh`; revert `.gitignore` line. No backend changes to revert (none made).
4. **VPS rollback:** redeploy last known-good `candidate/` from the local snapshot, restart `interviewtts.service`. Because deploys are atomic full-directory syncs, rollback is a symmetric operation.
5. **Compile misfire:** atomic write means `candidate/` is either fully old or fully new — no torn states; restore from the pre-compile temp/snapshot.

## Success criteria

- [x] `python scripts/wiki/validate.py` exits 0 on the real wiki and exits 1 with actionable messages on seeded-bad fixtures (tests cover both).
- [x] `python scripts/wiki/compile.py` produces a complete `candidate/` (profile.json + docs) identical across two consecutive runs (idempotent), and byte-identical replacement on re-run (atomic overwrite verified).
- [x] Invalid frontmatter or broken `related:` link causes **no** writes to `candidate/` (blocking gate proven by test).
- [x] `python scripts/wiki/generate_index.py` regenerates `wiki/index.md` covering all 8 types.
- [x] Deploy script performs (bash -n verified; MANUAL VPS smoke-check task 5.2 still open) validate → compile → rsync → restart in order and aborts at first failure.
- [x] `git ls-files candidate/` returns empty after untracking commit; `wiki/` ignored per `git status`.
- [x] Full suite green (203 passed; single pre-existing env-isolation failure in test_config_defaults unrelated to this change): `python -m pytest tests/ -v`.
- [x] Zero diffs under `backend/`.
- [ ] Estimated size respected: ~300–400 lines total (scripts + docs + gitignore + deploy).

## Proposal question round

Execution is interactive SDD mode; these questions aim to sharpen the PRD (business rules, edge cases, tradeoffs) before spec/design. Answer any subset, skip, or correct the framing — a second round is available on request. Current assumptions are stated per question.

1. **Profile.json synthesis:** CONVENCIONES says compile "synthesizes `profile.json`". Assumption: `profile/mikel.md` frontmatter+body maps into the existing `profile.json` shape consumed by `CandidateProfile.get_context_string()` (name/title/summary/skills/experience/projects/stories keys). Is that mapping authoritative, or should compile emit whatever structure emerges and we treat downstream consumers as tolerant? Wrong guess here breaks the persona prompt silently.
2. **docs/ granularity:** today the loader expects `candidate/docs/cv.md, projects.md, skills.md, stories.md`. Assumption: compile concatenates wiki files per type into those four aggregate docs (stories/opinions/decisions/faq folded sensibly). Confirm, or should each wiki file become its own doc file?
3. **Deploy auth path:** assumption is plain SSH key access to the VPS with sudo rights for `systemctl`. Any constraint (restricted user, non-standard SSH port, secrets handling) the deploy script must respect?
4. **First-deploy reconciliation:** on the very first run, VPS `candidate/` may differ from freshly compiled output. Assumption: overwrite unconditionally even on first deploy (wiki wins from day one), with the pre-change snapshot as the recovery path. Confirm?
5. **Backup repo cadence:** private-repo backup is documented-only in this change. Should the documented workflow be manual push-after-edit, or should we reserve a hook point (post-compile auto-commit) for a future change?

---

*Persisted via openspec artifact store per session preflight.*

## Resolved question round (2026-08-25)

| # | Question | Decision |
| --- | ---------- | ---------- |
| 1 | profile.json synthesis | **Mapping to current shape** — compile.py MUST emit exactly the structure `backend/services/candidate.py` consumes today (same keys); persona prompt unchanged |
| 2 | docs/ granularity | **One doc per wiki file** — each wiki markdown becomes its own `.md` under `candidate/docs/`; loader compatibility with arbitrary filenames MUST be verified in design phase |
| 3 | VPS access | Standard SSH public-key auth, sudo-capable user for `systemctl`, port 22 — deploy script may assume this |
| 4 | First-deploy reconciliation | Confirmed: overwrite unconditionally from day one (wiki wins), pre-change snapshot is the recovery path |
| 5 | Backup cadence | Manual push-after-edit workflow documented; no automation hook in this change |
