# Verify Report: wiki-pipeline

> Change ID: `wiki-pipeline` · Project: InterviewTTS · Store: `openspec` · Strict TDD: active
> Verified: 2026-08-25 · Interpreter: `./venv/Scripts/python.exe` ONLY

## Verdict: **PASS_WITH_WARNINGS**

Engineering criteria (specs, implementation, tests, git hygiene, backend contract) all pass.
Warnings are process/completeness items only; one archive blocker remains (task 7.2 reconciliation).

---

## 1. Executive Summary

The wiki-pipeline change delivers `validate.py`, `compile.py`, `generate_index.py`,
`deploy.sh`, fixtures, tests, README workflow docs, and git privacy hygiene. All three
targeted wiki suites are GREEN (40/40 re-run this session). Real-wiki validation exits 0
with warnings only. Backend contract spot-check passed against a live compile run on the
good fixture: emitted `profile.json` keys exactly match `backend/services/candidate.py`
consumption, non-legacy doc filenames are accepted by the loader's `glob("*.md")`.
Git hygiene verified live (index clean, `/wiki/` ignored, rollback tag recoverable).
Zero diffs under `backend/`. Three task checkboxes remain open: two are explicitly
accepted deviations (5.2 manual VPS smoke, 6.2 pyyaml skipped by user decision); one
(7.2) is an archive blocker pending reconciliation.

## 2. Skill Resolution

`skill_resolution: fallback-path` — no parent-injected skill paths; worked directly from
this phase contract plus repo artifacts (`openspec/config.yaml`, spec/tasks/apply-progress).

## 3. Structured Status & actionContext Findings

- Active change: `wiki-pipeline` — unambiguous (single change dir).
- Artifact store: `openspec` (`artifact_store: openspec` in config.yaml). Engram HTTP
  server unreachable (`http://127.0.0.1:7437`) during verification; openspec files used,
  which matches the configured store. No non-authoritative-store carve-out needed.
- actionContext: workspace edits confined to `scripts/wiki/`, `tests/`, `tests/fixtures/`,
  `openspec/changes/wiki-pipeline/`, `.gitignore`, `README.md`, `scripts/deploy.sh` — all
  inside the authoritative workspace. No warnings.
- Implementation ownership proven: commits `d828484`, `ccc24ff`, `bc53721` on `main`.

## 4. Spec-Coverage Table

Legend: ✅ covered by automated test · 🔷 covered by verified behavior/static evidence · ⚠️ partial (deviation noted)

### `specs/candidate-profile/spec.md`

| Requirement (SHALL) | Coverage |
| --- | --- |
| Wiki as source of truth; `candidate/` owned by compile; manual edits MUST NOT survive deploy | ✅ `tests/test_wiki_compile.py` idempotency + atomic-swap tests (full-tree rebuild, byte-stable). 🔷 `deploy.sh` replaces VPS tree wholesale (`rm -rf candidate.prev` rotation → mv → rsync). ⚠️ VPS-side behavioral proof deferred to MANUAL task 5.2 (accepted deviation). |
| `profile.json` shape stability — EXACTLY `name/title/summary/skills/experience/projects/stories`; persona prompt unchanged | ✅ `test_wiki_compile.py`: exact key-order assertion + `"documents" not in profile` + exact-JSON serialization test. 🔷 Live spot-check (this session): compiled output keys exactly `[name, title, summary, skills, experience, projects, stories]`; item shapes match loader reads (`experience`: role/company/period/highlights; `projects`: name/description/technologies/highlights; `stories`: situation/task/action/result). |
| Docs granularity + loader compatibility — one `.md` per wiki file; arbitrary names; missing legacy aggregates not an error | ✅ `test_wiki_compile.py` EXPECTED_DOCS: `<type>-<stem>.md` naming, frontmatter stripped. 🔷 Static: `backend/services/candidate.py::_load_markdown_docs` iterates `docs_dir.glob("*.md")`; missing legacy files only appear in a log message ("missing: …"), never raise. |
| Reload via service restart; NO `backend/` module modified | 🔷 `deploy.sh` step [5/5] `systemctl restart interviewtts.service`. 🔷 `git diff --stat backend/` → empty (re-verified this session, rc=0). |

### `specs/wiki-pipeline/spec.md`

| Requirement (SHALL) | Coverage |
| --- | --- |
| Blocking validation gate — read-only, schema errors, broken links, actionable messages, zero writes on error | ✅ `tests/test_wiki_validate.py` (14 tests): every seeded error class exits 1 naming offending file; good fixture exits 0 with mtimes unchanged; IO/missing-path exits 2. 🔷 Real-wiki run: `OK: 33 files valid, 61 warnings`, exit 0. |
| Warnings non-blocking — asymmetric links + stale low-confidence warn only | ✅ warning-only fixture variants assert exit 0 WITH `[WARN]` present. |
| Atomic idempotent compilation — temp build + swap, byte-stable, zero writes on invalid input, mismatch skip-with-log | ✅ `tests/test_wiki_compile.py` (16 tests): blocking gate (bad fixture → exit 1 AND pre-existing bytes identical), byte-stable double run, no `.tmp-*`/prev residue, type/folder-mismatch skip-with-log via direct `build()`. |
| Index regeneration — unconditional, all 8 types, sorted `updated` desc, never hand-edited | ✅ `tests/test_wiki_generate_index.py` (10 tests): fixed 8-type order, sort/tie-break, line format, AUTO-GENERATED header, byte-identical reruns, only `wiki/index.md` modified. 🔷 README documents "never hand-edit". |

### `specs/wiki-deployment/spec.md`

| Requirement (SHALL) | Coverage |
| --- | --- |
| Ordered deploy pipeline — validate → compile → rsync → restart; abort at first failure | 🔷 Static: `set -euo pipefail`, numbered strict-order steps, explicit ABORT guard after compile; `bash -n scripts/deploy.sh` passes. ⚠️ Behavioral abort-on-failure smoke is MANUAL task 5.2 (open, accepted deviation). |
| Unconditional overwrite + server-side retention (`candidate.prev`) + replaced-summary | 🔷 Static inspection of `deploy.sh` steps 3–5 (rotate stale backup → retain live tree as `candidate.prev` → rsync → summary → restart). ⚠️ VPS retention/rollback behavior proven only in task 5.2 (open). |
| Git privacy hygiene — untrack `candidate/`, ignore `wiki/`, tag `pre/wiki-pipeline` exists | ✅ Verified LIVE this session: `git ls-files candidate/` → empty; `.gitignore:72` contains `/wiki/`; tag `pre/wiki-pipeline` → `16e6b5c`; `git show pre/wiki-pipeline:candidate/docs/cv.md` recoverable; atomic untracking commit `ccc24ff` touches `.gitignore` + index deletions once. |
| Documented backup workflow — edit→validate→compile→deploy loop + manual private-repo push; no automation hook | 🔷 `README.md` L267–303: full loop commands, AUTO-GENERATED index notice, "Backing up `wiki/` (manual, private repo)" section, rollback anchors (tag, zip snapshot, `candidate.prev`). No automation hook built. |

**No SHALL requirement is without coverage.** Two requirements carry a documented
partial-behavioral gap traced solely to accepted-deviation task 5.2.

## 5. Task Completion Status

Authoritative source: `openspec/changes/wiki-pipeline/tasks.md`.

**Exact unchecked implementation-task lines remaining:**

```
- [ ] 5.2 MANUAL (VPS-only) — smoke-check deploy end-to-end against a staging/real VPS: ...
- [ ] 6.2 **OPTIONAL / requires explicit user approval:** append `pyyaml>=6.0` ...
- [ ] 7.2 Walk the proposal success-criteria checklist item by item ... and tick each in `proposal.md`.
```

Disposition:

| Line | Disposition |
| --- | --- |
| 5.2 | **Accepted deviation** (per parent delegation). Deploy ordering/abort verified statically (`bash -n` + script inspection) only. Listed as such in §8. |
| 6.2 | **Accepted deviation** — skipped by explicit user decision (OPTIONAL task, approval not granted; PyYAML available transitively). Not a defect. |
| 7.2 | **Archive blocker.** Substance substantially performed — `proposal.md` Success-criteria section shows 8 of 9 items ticked — but the walkthrough is incomplete: the final item *"Estimated size respected"* is unticked, and the `tasks.md` 7.2 checkbox was never reconciled. Cannot be recorded as an accepted deviation because the delegation did not approve it. |

Stale-checkbox observation (INFO): `apply-progress.md` "Remaining tasks" still lists
4.1–7.1 as unchecked although `tasks.md` marks them `[x]`; apply-progress is merge-only,
so reconcile or annotate at archive time.

## 6. Verification Commands (exact)

This session (venv interpreter only):

| Command | Result |
| --- | --- |
| `./venv/Scripts/python.exe -m pytest tests/test_wiki_validate.py tests/test_wiki_compile.py tests/test_wiki_generate_index.py -v` | **40 passed** in 4.94s (14 + 16 + 10) — GREEN reconfirmed |
| `./venv/Scripts/python.exe scripts/wiki/validate.py --wiki wiki` | exit 0; `OK: 33 files valid, 61 warnings` (all `[WARN]` link-asymmetry, non-blocking) |
| `./venv/Scripts/python.exe scripts/wiki/compile.py --wiki <tmp copy of good fixture> --out <tmp>/candidate` | exit 0; `Replaced: 8 files`; no tmp residue |
| profile.json introspection on that output | top-level keys exactly `[name,title,summary,skills,experience,projects,stories]`; item subkeys match loader reads exactly |
| `bash -n scripts/deploy.sh` | pass |
| `git ls-files candidate/` | empty (exit 0) |
| `git show pre/wiki-pipeline:candidate/docs/cv.md` | content recovered |
| `git diff --stat backend/` | empty |
| `grep wiki .gitignore` | line 72 `/wiki/` present |

Prior-session confirmed findings carried forward (not redone):
40/40 targeted pass; real-wiki validate exit 0 warnings-only; full suite 203 passed with a
single pre-existing env-isolation failure in `test_config_defaults` (unrelated to this
change; predates it).

## 7. Strict TDD Compliance

- `apply-progress.md` contains a **TDD Cycle Evidence** table ✅.
- Reported test files exist and match codebase: `tests/test_wiki_validate.py` (143 lines),
  `tests/test_wiki_compile.py` (245), `tests/test_wiki_generate_index.py` (192) ✅.
- Relevant tests re-run this session: **GREEN confirmed** ✅.
- Cycle-ordering caveat: compile-cycle RED/GREEN authored across sessions; apply-progress
  discloses this and designates greenness evidence as authoritative. Acceptable with note.

## 8. Findings (ranked)

1. **[CRITICAL — archive blocker]** Task **7.2** unchecked; proposal success-criteria
   walkthrough 8/9 ticked with *"Estimated size respected"* left unticked and the
   checkbox unreconciled. Fix: complete/annotate the size judgment and tick 7.2 (or
   record an explicit archive exception).
2. **[WARNING — accepted deviation]** Task **5.2** MANUAL VPS smoke open: abort-on-first-
   failure, `candidate.prev` retention, and post-restart freshness are verified statically
   and by unit tests, not behaviorally on a VPS. Must ship before first production deploy.
3. **[WARNING]** Review-workload size: actual changed surface exceeds the ~400-line budget
   (scripts/wiki ≈ 636 lines + tests ≈ 580 + docs/deploy/gitignore). This was pre-flagged
   as Medium risk in the Review Workload Forecast ("tests push total above it") and is the
   likely reason the size criterion is unticked — consistent with the forecast, not scope
   creep, but should be stated when ticking 7.2.
4. **[WARNING — low]** Chain strategy was recorded as `pending` and chained PRs were
   recommended; work landed instead as sequential commits on `main` under narrow-chunk
   delegation (PR-boundary slices respected per commit: d828484 feature+tests, ccc24ff
   hygiene, bc53721 docs/evidence). Boundary honored in substance; strategy field never
   closed out.
5. **[INFO — accepted deviation]** Task **6.2** pyyaml manifest declaration skipped by
   explicit user decision; runtime unaffected (transitive dependency).
6. **[INFO]** Stale "Remaining tasks" block in `apply-progress.md` (see §5).
7. **[INFO — out of scope]** Pre-existing `test_config_defaults` env-isolation failure is
   unrelated to this change (no `backend/` diffs; failure predates change).

## 9. Assertion-Quality Audit (strict TDD)

Reviewed assertions across the three wiki suites: golden-value equality on profile fields,
exact key-order list, exact JSON serialization string comparison, returncode checks with
stdout/stderr context, mtime-immutability checks for read-only guarantees, byte-comparison
for idempotency, residue checks for atomicity. No tautologies, ghost loops, type-only-only
assertions, or smoke-only tests found. Quality: **good**.

## 10. Exact Blockers

1. Reconcile task **7.2** (complete the size-respected judgment in `proposal.md`, tick
   checklist item, tick `tasks.md` 7.2) — or record a signed-off archive exception.
   Until then, **archive is NOT ready** and this report must not be treated as a clean PASS.

Non-blocking reminders before first production deploy: perform task 5.2 VPS smoke;
optionally close the `Chain strategy: pending` field.

---

*Persisted to openspec artifact store per `openspec/config.yaml` (`artifact_store: openspec`).
Engram HTTP server unreachable during this session; openspec files are the configured
authoritative store for this change.*
