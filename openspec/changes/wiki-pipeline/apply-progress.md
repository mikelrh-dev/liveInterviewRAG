# Apply Progress: wiki-pipeline

> Cumulative log. Merge-only — never overwrite completed work.

## Session: compile work-unit chunk (current)

**Status:** `ready_for_verify` — Phase 3 (compile.py) GREEN.

### Completed tasks (persisted in tasks.md)

- [x] 3.1 RED — `tests/test_wiki_compile.py` written (16 tests): golden §4 profile mapping, key order, no-`documents`, exact JSON serialization (`indent=2, ensure_ascii=False` + trailing `\n`), one-doc-per-file with `<type>-` prefix, frontmatter stripped, blocking gate with zero writes, type/folder mismatch rejected at CLI gate + skipped-with-log via direct `build()`, byte-stable idempotency, atomic swap with no tmp/prev residue.
- [x] 3.2 GREEN — `scripts/wiki/compile.py` implemented and verified (internal validation gate D4, build in `<out>.tmp-<pid>/`, two-rename swap per design §6/D3).

Note: both files were authored by a previous session; this session verified them end-to-end. The previously-reported fixture bug (`tests/fixtures/wiki/bad/asymmetric-links/` linking to nonexistent `skills/backend.md`) was found ALREADY FIXED — the variant now contains a minimal valid `skills/backend.md`, so asymmetry warns without erroring (confirmed by passing warning tests).

### Files changed (this session)

- `openspec/changes/wiki-pipeline/tasks.md` — checkboxes 3.1, 3.2 → `[x]`
- `openspec/changes/wiki-pipeline/apply-progress.md` — this file
- No production code changes needed; no files under `backend/` touched (verified: `git diff --stat backend/` empty; `backend/` not in status).

### Test evidence (venv interpreter only, targeted runs)

| Command | Result |
| --- | --- |
| `./venv/Scripts/python.exe -m pytest tests/test_wiki_compile.py -v` | **16 passed** in 2.18s |
| `./venv/Scripts/python.exe -m pytest tests/test_wiki_validate.py -v` | **14 passed** (regression check) |
| `git diff --stat backend/` | empty |

Full suite intentionally NOT run (delegated constraint: broken langsmith plugin under system python; scope limited to compile unit). `tests/test_wiki_generate_index.py` does NOT exist → Phase 4 skipped as instructed (out of chunk scope).

### TDD Cycle Evidence

| Cycle | RED | GREEN | Refactor |
| --- | --- | --- | --- |
| compile work unit | tests authored in prior session (RED state confirmed there); this session ran them against existing implementation | 16/16 passed first run of session | none needed — no code changes required |

Deviations from strict cycle ordering: RED/GREEN authoring happened across sessions; verification evidence above is authoritative for greenness.

### Workload / PR boundary

- Decision needed before apply: Yes (resolved by parent delegation — narrow chunk mode, single work unit)
- This chunk = PR 2 slice (compile) only. PR boundary: `scripts/wiki/compile.py` + `tests/test_wiki_compile.py` + bad-fixture repair.
- 400-line budget risk: Medium — unchanged.

### Remaining tasks

- [ ] 4.1 RED — write `tests/test_wiki_generate_index.py`
- [ ] 4.2 GREEN — implement `scripts/wiki/generate_index.py`
- [ ] 5.1 Write `scripts/deploy.sh`
- [ ] 5.2 MANUAL (VPS-only) deploy smoke check
- [ ] 5.3 Privacy-hygiene commit (`git rm -r --cached candidate/` + `.gitignore` append)
- [ ] 6.1 README docs section
- [ ] 6.2 OPTIONAL (needs explicit user approval) — `pyyaml>=6.0` in `backend/requirements.txt`
- [ ] 7.1 Full-suite final verification + backend-diff check
- [ ] 7.2 Proposal success-criteria walkthrough

### Structured status consumed

- Active change: `wiki-pipeline` (unambiguous, single change dir).
- Artifact store: openspec files present; Engram HTTP server unreachable (`http://127.0.0.1:7437`) → progress persisted to openspec files instead; parent should retry Engram persistence later or accept openspec as store for this change.
- actionContext: workspace edits confined to `scripts/wiki/`, `tests/`, `openspec/changes/wiki-pipeline/` — all within authoritative workspace. No warnings.
