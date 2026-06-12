# Tasks: vad-shorter-timeout — Reduce VAD silence timeout 1200 ms → 800 ms

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 1 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Change constant + verify | Single PR | One file, one line; no split needed |

## Phase 1: Implementation

- [x] 1.1 Edit `frontend/app.js:21`: change `const SILENCE_TIMEOUT_MS = 1200;` → `const SILENCE_TIMEOUT_MS = 800;`

## Phase 2: Verification

- [x] 2.1 Manual console verification: add `console.log('silence ms:', Date.now() - silenceStart)` to VAD else-if block (line 168), speak 3s then pause, confirm stop fires at ~800 ms
- [x] 2.2 Consistency read-through: review VAD loop lines 159–193 to confirm `SILENCE_TIMEOUT_MS` is correctly referenced at line 168 — no other code changes needed

## Phase 3: Deliver

- [x] 3.1 Stage `frontend/app.js`, commit with message: `perf(vad): reduce silence timeout to 800ms for faster turn completion`
- [x] 3.2 Push to `main` (do NOT include `openspec/changes/vad-shorter-timeout/` artifacts in the commit)
- [x] 3.3 Persist to Engram: mark change `vad-shorter-timeout` status as `planned`
