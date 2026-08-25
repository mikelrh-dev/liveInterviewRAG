# Tasks: UI Senior Polish (Mission Control)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Total estimated changed lines | ~70 (~55 style.css, ~5 index.html, ~10 app.js) |
| >400-line budget risk | No |
| Chained PRs recommended | No |
| Decision needed before apply | No |
| Delivery strategy | single-pr |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low
```

**Rollback boundary**: frontend-only, one commit per phase (A→B→C) — each phase is an independent `git revert` point restoring prior pixels exactly; no backend/env coupling. After any revert, confirm ≤768px rendering matches pre-change screenshots.

## Phase A: Foundations (tokens land first — B/C consume them)

- [x] A.1 `frontend/style.css` — extend `:root` after Z-index map (~:61): `--dur-fast:150ms; --dur-base:250ms; --dur-slow:400ms; --ease-out:cubic-bezier(0.16,1,0.3,1); --ease-spring:cubic-bezier(0.34,1.56,0.64,1);` + `color-scheme: dark;` (~7 ln)
- [x] A.2 `frontend/style.css`:72 — append `-webkit-tap-highlight-color: transparent;` to the `html, body` rule (~1 ln)
- [x] A.3 `frontend/style.css` — global rule `*:focus-visible { outline: 2px solid var(--cyan-accent); outline-offset: 2px; }` (~4 ln)
- [x] A.4 `frontend/style.css` EOF — universal `@media (prefers-reduced-motion: reduce)` block (`animation-duration:.01ms`, `animation-iteration-count:1`, `transition-duration:.01ms`, `scroll-behavior:auto` — all `!important`), covering all 7 infinite keyframes incl. `blink` (:725) (~9 ln)
- [x] A.5 `frontend/index.html` — `<meta name="theme-color" content="#131313">` after viewport meta (:5) (~1 ln)

## Phase B: Choreography (consumes Phase A tokens)

- [x] B.1 `frontend/style.css` — replace all 6 `transition:all` with explicit lists + token timing: :230 `#context-toggle` (border-color,color,transform), :297 `.sidebar-end-btn` (background-color), :507 `#btn-mic` (border-color,background-color fast/ease-out **+ transform fast/spring** for hover scale 1.05), :794 `#context-close` (border-color,color,transform), :829 `.chunk-pill` (border-color,background-color,transform), :962 `#mobile-end-btn` (background-color; inside ≤768px block); normalize 0.2s → `var(--dur-fast)` (~12 ln)
- [x] B.2 `frontend/style.css` — hover lift `translateY(-1px)` on #context-toggle / #context-close / .chunk-pill only; **exclude destructive END buttons** (.sidebar-end-btn, #mobile-end-btn) (~4 ln)
- [x] B.3 `frontend/style.css` — `@keyframes fadeUp` + staggered reveals: header 0ms, #avatar-wrapper 80ms, #btn-mic 160ms, #status 240ms, all `animation-fill-mode: backwards` (never both/forwards — pins transform and deadlocks mic hover scale) (~9 ln)
- [x] B.4 `frontend/app.js`:503 — first statement of `setState()`: `document.body.dataset.state = state;` (~1 ln)
- [x] B.5 `frontend/style.css` — state rules: `body[data-state="processing"] #btn-mic:disabled` amber; `body[data-state="speaking"] #btn-mic.active` green with `animation:none`; listening keeps `.active` red REC pulse (no new rule); add `#status { transition: color var(--dur-fast) var(--ease-out); }` (~6 ln)
- [x] B.6 `frontend/style.css`:915 — drawer `transition: transform 0.3s ease` → `transform var(--dur-base) var(--ease-spring)` (~1 ln)

## Phase C: Accessibility & Copy + Final Regression

- [x] C.1 `frontend/style.css`:32 — `--text-muted: #556688` → `#7e90b3` (5.87:1 vs #111111 ≥ AA) (~1 ln)
- [x] C.2 `frontend/index.html`:219 — `aria-label="Cerrar panel de contexto"` on icon-only `#context-close` (~1 ln)
- [x] C.3 `frontend/index.html`:71 — `<span class="header-title">` → `<h1 class="header-title">`; visuals identical via reset margin + existing font rules (~1 ln)
- [x] C.4 `frontend/app.js` — `"..."` → `"…"` in status strings :673, :718, :783, :855, :868 and truncation literal :1076 (~6 ln)
- [x] C.5 copy tuteo — `index.html`:57 "Hacé click"→"Haz clic", :153 "Presioná"→"Presiona"; `app.js`:288 "Presioná"→"Presiona", :648 "usá"→"usa", :678 "revisá"→"revisa" (~5 ln)
- [x] C.6 Final regression — run `venv\Scripts\python.exe -m pytest tests/ -q` MUST be green (backend untouched; **no new pytest tests — explicit design decision**); confirm only edits inside ≤768px media block are the two mandated swaps (:915, :962)

> **C.6 result**: final run `1 failed, 220 passed` — delta vs locked baseline (`1 failed / 220 passed`, captured before any edit) is ZERO.
> Both failures are pre-existing backend test-infra issues, out of scope by the zero-backend-changes constraint:
> ① `test_config.py::test_config_defaults` — deterministic in all 5 full-suite runs (clean tree included): operator `.env` sets `WHISPER_COMPUTE_TYPE=float16`; `load_dotenv()` from `backend.main` (imported earlier by test_api) pollutes `os.environ`; test only delenv's `WHISPER_MODEL`.
> ② `test_report_service.py::TestReportServiceCleanup::test_cleanup_custom_days_override` — nondeterministic clock race: `cleanup_expired(days=0)` compares fresh-file mtime vs `time.time()` cutoff; flipped pass/fail across runs of the IDENTICAL tree (failed in run 3–4, passed in runs 1–2 and 5–6). No test reads `frontend/*` (grep-verified); no causal path exists.

## Verification Map

Manual matrix (design's 7-point checklist) → task: ① focus ring → A.3 · ② reduced-motion frozen/colors intact → A.4 · ③ staggered reveal header→avatar→mic→status <600ms → B.3 · ④ mic cycle idle/listening-red/processing-amber-disabled/speaking-green → B.4+B.5 · ⑤ drawer spring slide → B.6 · ⑥ sidebar labels readable post-recolor → C.1 · ⑦ desktop ≥769px screenshot-diff stable → C.6
Grep/pytest gates: zero `transition:\s*all` in style.css → B.1 · full suite green → C.6
