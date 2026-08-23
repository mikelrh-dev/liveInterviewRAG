# Tasks: Mobile Usability Optimization

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~183 (5 files: style.css ~100, app.js ~55, index.html ~12, avatar.js ~8, main.py ~8) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | size-exception |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | All changes | PR 1 | ~183 lines, well within 400-line budget. Single PR is correct. |

---

## Phase 1: Backend — mp4 Upload Acceptance (TDD)

- [x] 1.1 **RED**: Add `test_send_message_mp4_audio` in `tests/test_api.py` — POST to `/api/conversation/{id}/message` with `("test.m4a", b"fake-audio-data", "audio/mp4")`, assert 200 response. Test MUST fail initially (backend hardcodes `.webm` extension).
- [x] 1.2 **RED**: Add `test_send_message_stream_mp4_audio` in `tests/test_api.py` — same payload to stream endpoint, assert 200.
- [x] 1.3 **RED**: Add `test_upload_saves_with_correct_extension` in `tests/test_api.py` — mock `audio.content_type="audio/mp4"`, verify saved temp file ends in `.m4a` (not `.webm`). Covers the pure-logic extraction if implemented.
- [x] 1.4 **GREEN**: Modify `backend/main.py` lines ~388, ~515 — derive file extension from `audio.content_type` (`.m4a` for `audio/mp4`, `.webm` for `audio/webm`), pass to temp file path. All 3 new tests pass.
- [x] 1.5 **Verify**: Run `venv\Scripts\python.exe -m pytest tests/test_api.py -v` — all tests green including existing suite.

## Phase 2: Codec Chain (JS — manual verification)

- [x] 2.1 In `frontend/app.js`, add `chooseMimeType()` function that loops `['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4']` via `MediaRecorder.isTypeSupported()`, stores first match in `selectedMimeType`.
- [x] 2.2 Replace hardcoded mime type at line ~531-535 with `chooseMimeType()` call in `initAudio()`.
- [x] 2.3 At line ~723, derive Blob filename extension from `selectedMimeType` (`.webm` or `.m4a`) instead of hardcoded `.webm`.
- [x] 2.4 Add `async ensureAudioContext()` helper — calls `await audioContext.resume()` if `'suspended'`. Call before `audio.play()` (line ~673) and before `startRecording()` (line ~517).
- [ ] 2.5 **Manual verification**: Device-matrix — iPhone Safari records successfully (codec is mp4); Android Chrome records (codec is webm). See design.md device matrix.

## Phase 3: CSS Breakpoints & Viewport (manual verification)

- [x] 3.1 Add `--app-height: 100vh` on `:root` in `frontend/style.css`. Change `.app-grid` height from `100vh` to `var(--app-height)`.
- [x] 3.2 Append `/* ─── Mobile (≤768px) ─── */` section at end of `style.css` (after line 889). Add `@media (max-width: 768px)` with: `--app-height: 100dvh`, `.main-grid` single-column, `#sidebar { display: none }`.
- [x] 3.3 Add `@supports not (height: 100dvh)` fallback keeping `--app-height: 100vh`.
- [x] 3.4 In `frontend/index.html`: add `viewport-fit=cover` to meta viewport tag (line ~5). Add `defer` to Tailwind CDN script. Add `<button id="mobile-end-btn" class="mobile-only">END</button>` inside `.header-right`.
- [x] 3.5 Style `#mobile-end-btn` in mobile @media: visible only on mobile, ≥44px touch target, positioned in header. Add `#mobile-end-btn { display: none }` in desktop rules.
- [x] 3.6 Add `touch-action: manipulation` on `*` selector inside mobile @media. Add `user-select: none` scoped to `button, .sidebar-end-btn, #btn-mic`.
- [x] 3.7 Add `padding-top: env(safe-area-inset-top)` on `.header` and `padding-bottom: env(safe-area-inset-bottom)` on `.footer` inside mobile @media.
- [ ] 3.8 **Manual verification**: Device-matrix — iPhone SE (375px) shows single-column, sidebar hidden, mic button visible, no double-tap zoom, safe-area padding correct.

## Phase 4: Context Panel Overlay (manual verification)

- [x] 4.1 Inside mobile @media in `style.css`: `#context-panel { position: fixed; top: 0; right: -100%; height: var(--app-height); width: 85vw; transition: transform 0.3s ease; z-index: var(--z-context-mobile) }`.
- [x] 4.2 Add `.context-panel.open` inside mobile @media: `transform: translateX(-100%)` (slides panel to `right: 0`).
- [x] 4.3 Add `body.context-open::before` pseudo-element: `content: ''; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: calc(var(--z-context-mobile) - 1)`.
- [x] 4.4 In `frontend/app.js`, ensure context toggle handler (line ~196-203) toggles `body.context-open` class alongside existing `.context-panel.open` toggle.
- [ ] 4.5 **Manual verification**: Device-matrix — iPhone Safari context panel slides in with backdrop, backdrop click closes, z-index above Three.js canvas.

## Phase 5: Avatar & Polish (manual verification)

- [x] 5.1 In `frontend/avatar.js` or `app.js` (in `initAvatarOrb()` at line ~308): call `AvatarOrb.resize()` after init to fix initial render on mobile.
- [x] 5.2 In `frontend/app.js` `init()` (line ~165): add debounced (250ms) `window.addEventListener('resize', ...)` that measures `#avatar-wrapper` dimensions and calls `AvatarOrb.resize(w, h)`.
- [x] 5.3 In mobile @media in `style.css`: `#avatar-wrapper { max-width: min(300px, 90vw); margin: 0 auto }` to clamp avatar size on small screens.
- [x] 5.4 In `frontend/app.js`: wire `#mobile-end-btn` click to trigger the same end-session logic as the sidebar button.
- [ ] 5.5 **Manual verification**: Device-matrix — avatar resizes correctly on orientation change; END button works on mobile; desktop Chrome/Firefox pixel-identical to current.

## Phase 6: Full Verification

- [x] 6.1 Run full test suite: `venv\Scripts\python.exe -m pytest tests/ -v` — must stay green.
- [ ] 6.2 Desktop regression: verify Chrome and Firefox at ≥769px show no visual changes (pixel-identical to pre-change state).
- [ ] 6.3 Mobile end-to-end: iPhone Safari record → STT → LLM → TTS loop completes; Android Chrome same.

---

## Verification Plan

| Task | Verification Method |
|------|-------------------|
| 1.1–1.3 | pytest RED — tests must fail initially |
| 1.4 | pytest GREEN — `test_send_message_mp4_audio`, `test_send_message_stream_mp4_audio`, `test_upload_saves_with_correct_extension` |
| 1.5 | pytest full suite |
| 2.1–2.4 | Manual — device matrix (iPhone Safari, Android Chrome) |
| 3.1–3.7 | Manual — device matrix (iPhone SE 375px, desktop ≥769px) |
| 4.1–4.4 | Manual — device matrix (iPhone Safari: slide-in, backdrop, z-index) |
| 5.1–5.4 | Manual — device matrix (orientation change, END button, desktop regression) |
| 6.1 | `venv\Scripts\python.exe -m pytest tests/ -v` |
| 6.2 | Manual — desktop Chrome/Firefox visual check |
| 6.3 | Manual — mobile end-to-end voice interview loop |

## Rollback Boundary

Single PR revert (`git revert`). No database migrations, no backend logic changes, no env vars, no config changes. Desktop behavior is pixel-identical — rollback affects only the mobile path. All existing tests pass unchanged after revert.
