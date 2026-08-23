# Design: Mobile Usability Optimization

## Architecture Context

The frontend is a single-page vanilla HTML/CSS/JS app served by FastAPI static mount. The layout is a 3-zone CSS grid (`style.css:244-250`) with hardcoded 320px sidebar + 320px context panel. The backend validates uploads via `audio.content_type.startswith("audio/")` at `main.py:375,504` — already accepts any audio MIME. The z-index scale is defined at `style.css:52-60` (0–1000). The Three.js renderer lives in an IIFE at `avatar.js` exposing `window.AvatarOrb`.

Desktop behavior is the golden rule: pixel-identical at ≥769px. All mobile changes live exclusively inside `@media (max-width: 768px)` blocks or mobile-only JS paths.

## Architecture Decisions

### Decision: Single breakpoint at max-width:768px

| Option | Tradeoff |
|--------|----------|
| Two-tier (768px + 480px) | Better phone-vs-tablet tuning; doubles CSS surface area |
| **Single 768px** | Covers iPhone SE→iPad; simple; proposal targets phones only |

**Choice**: Single `@media (max-width: 768px)`. Tablet layouts are out of scope per proposal.

### Decision: @media blocks grouped at end of style.css

**Choice**: Append a single `/* ─── Mobile (≤768px) ─── */` section at end of `style.css` after line 889. All mobile overrides live there, grouped by component (grid → header → sidebar → main → context → avatar → utilities).
**Alternatives considered**: Inline @media next to each component rule (scattered, harder to audit); separate `mobile.css` file (extra HTTP request, breaks single-PR rollback).
**Rationale**: Grouped-at-end is the standard pattern for small-to-medium CSS files. Zero risk to existing rules. The empty `.context-panel.open` block at line 887-889 already signals intent for mobile.

### Decision: Desktop invariance via media-query isolation

**Choice**: Every mobile rule is inside `@media (max-width: 768px)`. Zero existing desktop rules are modified. The only exception is the new CSS custom property `--app-height` on `:root` (desktop defaults to `100vh`, mobile overrides to `100dvh`).
**Rationale**: The existing desktop rules use specific pixel values and grid templates. Adding `!important` or selector specificity wars would be fragile. Media query isolation is the only safe approach.

### Decision: Sidebar hidden on mobile; END SESSION moved to header

**Choice**: `#sidebar { display: none }` on mobile. The sidebar END SESSION button (index.html:143) is unreachable when sidebar is hidden. A small "END" button is added to `.header-right` inside a `<button id="mobile-end-btn">` element (index.html), styled only on mobile.
**Alternatives considered**: Off-canvas sidebar (slide-in from left) — adds JS toggle complexity, backdrop, z-index management for a feature recruiters don't use on phones.
**Rationale**: Sidebar shows session metadata (timer, model names, VU meter) — nice-to-have, not critical for a recruiter's 2-minute demo. END SESSION is the only actionable element and must remain accessible. A header button is the simplest path.

### Decision: Context panel as position:fixed slide-in with ::before backdrop

**Choice**: On mobile, `#context-panel` becomes `position: fixed; top: 0; right: -100%; height: 100dvh; width: 85vw; transition: transform 0.3s ease`. `.context-panel.open` sets `transform: translateX(-100%)` (slides to `right: 0`). Backdrop is a `::before` pseudo-element on the `body` when `.context-panel.open` is active — avoids adding a new DOM element.
**Alternatives considered**: New `<div id="context-backdrop">` element (cleaner for JS toggle but adds HTML for a CSS-only concern).
**Rationale**: Pseudo-element is sufficient since the backdrop is purely visual (click handling already exists at `app.js:196-203`). Z-index: panel uses `--z-context-mobile: 100` (style.css:59), above Three.js canvas (`--z-avatar: 30`, line 57) but below overlay (`--z-overlay: 1000`, line 60).

### Decision: Codec fallback with ordered candidate array

**Choice**: Replace `app.js:531-535` with a `chooseMimeType()` function that loops `['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4']` and picks the first supported via `MediaRecorder.isTypeSupported()`. Store result in `selectedMimeType`. Blob type at `app.js:723` uses this. Filename extension derived from mime (`.webm` or `.m4a`).
**Alternatives considered**: Detect platform via `navigator.userAgent` then branch — fragile, fails on future browsers.
**Rationale**: Feature detection is the only correct approach. Webm tried first (desktop Chrome, Firefox, Android Chrome all support it). Mp4 is iOS Safari's path. Fallback to bare `audio/webm` as last resort (some browsers need it without codec param).

### Decision: Backend accepts mp4 without content-type allowlist change

**Choice**: The backend validation at `main.py:375,504` already accepts `audio/*` content types. The temp file is always saved as `.webm` extension (`main.py:388,515`). For mp4 uploads, change the extension to match the actual container (`.m4a` or `.mp4`) based on `audio.content_type`. Whisper via ffmpeg handles both containers transparently — no STT change needed.
**Rationale**: The current hardcoded `.webm` extension is cosmetically wrong for mp4 blobs but ffmpeg ignores file extensions. The minimal fix is deriving extension from mime. No backend logic change, no allowlist change.

### Decision: --app-height CSS custom property with 100dvh + fallback

**Choice**: On `:root` add `--app-height: 100vh`. Inside `@media (max-width: 768px)`, override with `--app-height: 100dvh`. Add `@supports not (height: 100dvh)` fallback keeping `100vh`. `.app-grid` uses `height: var(--app-height)` instead of `height: 100vh`. `index.html:5` adds `viewport-fit=cover`. Header and footer get `padding-top: env(safe-area-inset-top)` / `padding-bottom: env(safe-area-inset-bottom)`.
**Rationale**: `dvh` accounts for mobile browser chrome. The `@supports` fallback handles browsers without dvh. env(safe-area-inset-*) handles iPhone notch/home-indicator.

### Decision: Three.js resize owned by avatar.js, triggered from app.js

**Choice**: `avatar.js` already exposes `AvatarOrb.resize(w, h)` (line 199). `app.js` calls `AvatarOrb.resize()` in `initAvatarOrb()` after init (line 308), and adds a debounced `window.addEventListener('resize', ...)` in `init()` (line 165) that measures `#avatar-wrapper` and calls `AvatarOrb.resize()`.
**Rationale**: `avatar.js` owns the renderer and camera. `app.js` owns lifecycle and DOM measurement. Debounce at 250ms avoids thrashing during orientation change.

### Decision: AudioContext resume guard before first play()

**Choice**: Add an `async ensureAudioContext()` helper in `app.js` that calls `await audioContext.resume()` if state is `'suspended'`, idempotent. Called before `audio.play()` at `app.js:673` and before `startRecording()` at `app.js:517` (inside `initAudio`).
**Rationale**: iOS Safari suspends AudioContext until user gesture. The existing `resumeAudioContext()` at line 249 is click-triggered but `tryPlayNextChunk()` plays programmatically after SSE response. The guard ensures resume happened.

### Decision: touch-action:manipulation on *, not interactive-only

**Choice**: `touch-action: manipulation` on `*` selector inside `@media (max-width: 768px)`.
**Alternatives considered**: Scope to `button, a, [role="button"]` only — misses tap-to-zoom on conversation text, avatar area.
**Rationale**: The entire interview is a single-purpose voice tool. Double-tap zoom on any element is undesirable. Global `manipulation` is simpler and covers all interactive surfaces. `user-select: none` scoped to `button, .sidebar-end-btn, #btn-mic` to prevent accidental text selection on controls only.

## Data Flow

```
Mobile Browser                         Backend
─────────────                          ───────
getUserMedia()
  ↓
chooseMimeType()
  → audio/webm;codecs=opus (preferred)
  → audio/mp4;codecs=mp4a.40.2 (iOS fallback)
  ↓
MediaRecorder(blob)
  ↓
POST /api/conversation/{id}/message/stream
  content-type: audio/webm OR audio/mp4
  filename: recording.webm OR recording.m4a
  ↓                              validate: audio/* (no change)
  ↓                              save as input_{id}_{uuid}.{ext}
  ↓                              STT → ffmpeg handles both containers
  ↓                              RAG → LLM → TTS
  ↓
SSE stream ← audio_chunk events
  ↓
new Audio(url).play()
  ensureAudioContext() before play()
```

## File Changes

| File | Lines Est. | Description |
|------|-----------|-------------|
| `frontend/style.css` | ~100 | Mobile @media block: grid collapse, sidebar hide, context slide-in, viewport/touch, avatar clamp |
| `frontend/app.js` | ~55 | chooseMimeType(), codec-aware Blob/Filename, AudioContext guard, mobile end-btn, resize listener |
| `frontend/index.html` | ~12 | viewport-fit=cover meta, defer on Tailwind, mobile-end-btn element |
| `frontend/avatar.js` | ~8 | Resize call after init (already exposed; just call from app.js init) |
| `backend/main.py` | ~8 | Derive file extension from content_type in temp_audio path (lines 388, 515) |
| **Total** | **~183** | Within ≤250 budget |

## Testing Strategy

`strict_tdd: true` per config. Backend changes are unit-testable. Frontend JS is not covered by pytest.

| Layer | What | Approach |
|-------|------|----------|
| **Unit (pytest)** | mp4 upload accepted by both endpoints | `tests/test_api.py`: add `test_send_message_mp4_audio` — POST with `("test.m4a", b"fake", "audio/mp4")`, assert 200. Add `test_send_message_stream_mp4_audio` — same for stream endpoint. |
| **Unit (pytest)** | File extension derivation | `tests/test_api.py`: add `test_upload_saves_with_correct_extension` — mock `audio.content_type="audio/mp4"`, verify temp file ends in `.m4a`. |
| **Manual device matrix** | Codec selection, layout, touch, overlays | See checklist below. JS logic is not unit-testable without a DOM runtime; extraction of `chooseMimeType()` as a pure function is recommended but not required for this PR. |

### Manual Device Matrix (verification artifact)

| Device | Browser | Test |
|--------|---------|------|
| iPhone 12+ | Safari ≥15 | Record → STT → TTS loop works; mic button visible; no zoom; context slides in |
| iPhone SE | Safari ≥15 | Same as above on 375px screen |
| Android Pixel | Chrome | Record → STT → TTS loop works; codec is webm |
| Desktop Chrome | Chrome | Pixel-identical to current; no visual regression |
| Desktop Firefox | Firefox | Pixel-identical; codec is webm |

**Recommendation against extracting `chooseMimeType()` for unit testing**: The function is a pure `isTypeSupported` check loop — 6 lines. Wrapping it in a module export + test harness adds more code than the function itself. The manual device matrix provides stronger coverage (it proves the browser actually supports the codec). Extract only if the fallback chain grows beyond 4 candidates.

## Migration / Rollout

No migration required. No data schema changes. No feature flags. Single PR, single deploy. Desktop behavior is pixel-identical — the change is invisible to desktop users.

## Rollback

Revert the single PR. Changes affect only 5 files:
- `frontend/style.css` — remove mobile @media block
- `frontend/app.js` — remove chooseMimeType(), AudioContext guard, resize listener, mobile-end-btn handler
- `frontend/index.html` — remove viewport-fit=cover, defer, mobile-end-btn
- `frontend/avatar.js` — no changes (resize is called from app.js)
- `backend/main.py` — remove extension derivation

All existing tests pass unchanged. No database, no env vars, no config changes.

## Open Questions

- [ ] None. All decisions grounded in code and proposal constraints.
