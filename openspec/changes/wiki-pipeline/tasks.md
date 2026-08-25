# Tasks: wiki-pipeline

> Change ID: `wiki-pipeline` · Project: InterviewTTS · Strict TDD active (`python -m pytest tests/ -v`)
> Constraint: ZERO modifications under `backend/` (except explicitly-marked OPTIONAL task 6.2)

## Review Workload Forecast

| Field | Value |
| ------- | ------- |
| Estimated changed lines | ~650–800 total (scripts ~340 per design §12 + tests ~250–320 + docs/gitignore ~40) |
| 400-line budget risk | Medium |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (scaffold + fixtures + validate) → PR 2 (compile + generate_index) → PR 3 (deploy.sh + git hygiene + docs) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: Medium
```

Note: production-code surface fits the ~400 budget (design §12); tests push total above it. Each work unit below has a clean start (fixtures/tests written), finish (pytest green), verification (`python -m pytest tests/ -v`), and rollback boundary (delete new files only; no shared-state edits across units except `_common.py`, delivered inside PR 1).

---

## Phase 1 — Scaffold & Safety (BEFORE any script work)

- [x] 1.1 Create rollback anchor tag on the last pre-change commit: `git tag pre/wiki-pipeline`; verify recoverability with `git show pre/wiki-pipeline:candidate/docs/cv.md`.
- [x] 1.2 Snapshot working `candidate/` directory to a timestamped zip OUTSIDE the repo (e.g., `%USERPROFILE%\backups\candidate-pre-wiki-pipeline.zip`) — belt-and-suspenders beyond the tag; record snapshot path in this task's notes.
- [x] 1.3 Create good mini-wiki fixture `tests/fixtures/wiki/good/`: ≥1 valid `.md` per ALL 8 types (`profile/mikel.md`, `projects/*.md`, `experience/*.md`, `skills/*.md`, `stories/*.md`, `opinions/*.md`, `decisions/*.md`, `faq/*.md`) with CONVENCIONES-conformant frontmatter (`type`, `title` kebab-case = stem, `created`/`updated` YYYY-MM-DD, `confidence`, non-empty `tags`, resolving `related:`, `summary_1line` ≤80 chars) plus body sections required by design §4 mapping (`## Identity`, `## Top skills (summary)`, `## Career timeline (corrected)`, story `## Situation/Task/Action/Result`). Include a `CONVENCIONES.md` + `templates/` to prove skip logic.
- [x] 1.4 Create bad-wiki fixtures under `tests/fixtures/wiki/bad/` as seeded variants: `missing-field/` (required frontmatter field absent), `type-folder-mismatch/` (`type:` ≠ parent folder), `broken-link/` (`related:` → nonexistent path), `bad-date/` (non-YYYY-MM-DD and/or invalid calendar date), `long-summary/` (`summary_1line` >80 chars), plus warning-only variants `asymmetric-links/` and `stale-low-confidence/` (`confidence: low`, `updated` >7 days old).

## Phase 2 — validate.py (strict TDD: RED → GREEN)

- [x] 2.1 RED — write `tests/test_wiki_validate.py`: good fixture copied into `tmp_path` → exit 0 AND nothing written (assert all mtimes unchanged); each error-class fixture → exit 1 with `[ERROR] <relpath>: …` naming the offending file; asymmetric-links + stale-low-confidence → exit 0 WITH `[WARN]` lines present; missing `--wiki` path / IO failure → exit 2; final summary line format (`Validation failed: N errors, M warnings` / `OK: N files valid, M warnings`). Run suite, confirm new tests FAIL.
- [x] 2.2 GREEN — implement `scripts/wiki/_common.py`: PyYAML frontmatter parser, singular-type ↔ plural-folder map (8 entries per design §5), recursive walk-and-skip (always exclude `CONVENCIONES.md`, `index.md`, `templates/`).
- [x] 2.3 GREEN — implement `scripts/wiki/validate.py` (read-only, `--wiki PATH`): all CONVENCIONES errors from design §6 (missing field, type enum/mismatch, kebab-case title ≠ stem, malformed dates, confidence enum, empty tags, unresolvable `related:` incl. `[[...]]` stripping, summary >80 chars); warnings for link asymmetry + stale low-confidence; exit codes 0/1/2. Run `python -m pytest tests/ -v` → green, existing 8 test files untouched.

## Phase 3 — compile.py (strict TDD: RED → GREEN)

- [x] 3.1 RED — write `tests/test_wiki_compile.py`: golden-value assertions on emitted `profile.json` against design §4 mapping table (name/title from `## Identity` bold labels; `summary` verbatim from `summary_1line`; skills flattened from bullets in order; experience parsed `**PERIOD:** Role, Company`; projects keyed by filename stem with `tags`→technologies and body bullets→highlights; stories STAR from H2 sections; key order exactly name/title/summary/skills/experience/projects/stories; NO `documents` key; `indent=2, ensure_ascii=False` + trailing newline); one-doc-per-file output named `<type>-<stem>.md` with frontmatter stripped; blocking-gate proof (bad fixture → exit 1 AND every pre-existing `candidate/` byte identical); idempotency (two consecutive runs → byte-identical outputs); atomic swap leaves no `<out>.tmp-*` residue and produces `<out.prev>` cleanup; type/folder mismatch skipped-with-log when reached directly. Confirm FAIL.
- [x] 3.2 GREEN — implement `scripts/wiki/compile.py` (`--wiki PATH --out PATH`): step 1 internal validation via imported validate check function (D4) with zero writes on error (temp dir never created); step 2 build full tree in `<out>.tmp-<pid>/` per §4/§5 with sorted iteration everywhere; step 3 two-step rename swap (`out → out.prev → tmp → out → rmtree(prev)`); stale-content warning printed; print replaced-file summary; exit codes 0/1/2. Run `python -m pytest tests/ -v` → green.

## Phase 4 — generate_index.py (strict TDD: RED → GREEN)

- [x] 4.1 RED — write `tests/test_wiki_generate_index.py`: index lists entries of ALL 8 types in fixed order per design §5; entries sorted by `updated` descending (tie-break filename ascending); line format `- [title](<folder>/<file>) — summary_1line (updated YYYY-MM-DD, confidence=X)`; header contains AUTO-GENERATED notice; two runs → byte-identical `wiki/index.md`; ONLY `index.md` modified (all other fixture files' mtimes unchanged). Confirm FAIL.
- [x] 4.2 GREEN — implement `scripts/wiki/generate_index.py` (`--wiki PATH`): unconditional regeneration of `wiki/index.md` using `_common` walk/skip/map helpers; writes ONLY `wiki/index.md`; exit codes 0/1. Run `python -m pytest tests/ -v` → green.

## Phase 5 — deploy.sh + Git hygiene

- [x] 5.1 Write `scripts/deploy.sh` per design §7 sequence diagram: `set -euo pipefail`; env-var config block (`VPS_HOST`, `VPS_USER`, `SSH_PORT=22`, `REMOTE_DIR` with defaults, no secrets); strict order validate → compile → `ssh mv candidate/ candidate.prev/` → `rsync -az --delete` → replaced-content summary → `sudo systemctl restart interviewtts.service` → rotate old `candidate.prev/` after success → DEPLOY OK message with rollback hint. Verify locally: `bash -n scripts/deploy.sh` passes.
- [ ] 5.2 MANUAL (VPS-only) — smoke-check deploy end-to-end against a staging/real VPS: confirm abort-on-first-failure (temporarily seed an invalid wiki file, watch it stop before rsync), confirm `candidate.prev/` retention and rollback (`mv candidate.prev candidate/ && sudo systemctl restart interviewtts.service`), then restore valid state and complete one clean deploy.
- [x] 5.3 Dedicated privacy-hygiene commit containing BOTH changes atomically: `git rm -r --cached candidate/` (files remain on disk) AND append `wiki/` to `.gitignore`. Verify: `git ls-files candidate/` returns empty; `git status` shows `wiki/` ignored; `candidate/` appears deleted-from-index only once.

## Phase 6 — Documentation

- [x] 6.1 Add README section documenting: the edit → `python scripts/wiki/validate.py` → `python scripts/wiki/compile.py` → `./scripts/deploy.sh` loop; that `wiki/index.md` is auto-generated (never hand-edit); the manual push-after-edit backup of `wiki/` to a PRIVATE GitHub repo (workflow documentation only, no automation hook); note that `deploy.sh` targets bash/systemd and runs on-VPS or via SSH/WSL from Windows; rollback hints (tag anchor, local zip, `candidate.prev/`).

## Phase 7 — Final verification

- [x] 7.1 Run full suite `python -m pytest tests/ -v` → all green (new 3 test files + existing 8 untouched); confirm zero diffs under `backend/` (`git diff --stat backend/` empty).
- [x] 7.2 Walk the proposal success-criteria checklist item by item (validate exit codes on real + bad wikis; compile idempotency/atomicity; zero-writes-on-invalid; 8-type index; deploy ordering; untracking verified; suite green; backend untouched; size respected) and tick each in `proposal.md`.

## OPTIONAL — pending user approval (touches `backend/` manifest)

- [ ] 6.2 **OPTIONAL / requires explicit user approval:** append `pyyaml>=6.0` to `backend/requirements.txt` (manifest-only declaration; `backend/services/rag.py` already imports `yaml`, making it a de facto dependency). SKIP by default if approval not granted — scripts still work since PyYAML is installed transitively today.
