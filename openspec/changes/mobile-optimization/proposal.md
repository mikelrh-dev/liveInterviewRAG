# Proposal: Mobile Usability Optimization

## Intent

Recruiters WILL open the demo link from their phone. Today the app is unusable on mobile: hardcoded 320px+1fr+320px grid overflows screens, iOS Safari fails to record audio (webm-only codec), the 100vh mic button hides behind the address bar, and there are zero @media rules. This change makes the voice interview flow work end-to-end on iPhone Safari ≥15 and modern Android Chrome.

## Scope

### In Scope
- Responsive layout: @media breakpoints collapse 3-zone grid to single-column on mobile
- iOS audio fix: MediaRecorder codec fallback (mp4) + correct error messaging + backend mp4 acceptance
- Viewport fix: 100dvh with 100vh fallback + viewport-fit=cover + safe-area-inset padding
- Touch fix: touch-action: manipulation to prevent double-tap zoom
- Context panel: position:fixed slide-in overlay with backdrop on mobile
- Avatar: max-width+90vw clamp, Three.js resize() called on init + window resize
- Touch targets: ensure ≥44px on toggle/close buttons
- Tailwind CDN: add defer attribute to blocking script

### Out of Scope
- PWA / installable / offline mode
- Tablet-specific layouts
- Deep CDN re-architecture (beyond defer)
- Backend pipeline logic changes (audio processing unchanged)
- Desktop behavior changes (golden rule: pixel-identical)

## Capabilities

### New Capabilities
- `mobile-responsive-layout`: CSS @media breakpoints, grid collapse, viewport fixes
- `mobile-audio-recording`: iOS Safari MediaRecorder codec fallback chain
- `mobile-overlay-panels`: Slide-in context panel with backdrop on mobile

### Modified Capabilities
None — existing specs (candidate-profile, conversation-engine, rag-pipeline) are unaffected.

## Approach

Single PR, ~190-250 changed lines. Changes organized by phase:

1. **Layout breakpoint** (style.css): @media (max-width: 768px) collapses `.main-grid` to single-column, adjusts sidebar/context to overlay-ready state
2. **iOS codec** (app.js): try audio/mp4 fallback chain in MediaRecorder constructor; fix error message; accept mp4 in backend upload validation
3. **Viewport + touch** (index.html, style.css): add viewport-fit=cover to meta, 100dvh with fallback, safe-area-inset padding, touch-action: manipulation
4. **Overlays** (style.css, app.js): context panel position:fixed slide-in + backdrop in mobile breakpoint; toggle/close buttons sized ≥44px
5. **Polish** (avatar.js, style.css): avatar max-width clamp, Three.js resize on init + resize listener, user-select:none on buttons

## Affected Areas

| File | Impact | Lines Est. |
|------|--------|-----------|
| `frontend/style.css` | Major: @media rules, viewport, overlays, touch | ~120 |
| `frontend/app.js` | Moderate: codec fallback, error fix, context panel toggle | ~50 |
| `frontend/index.html` | Minor: defer on Tailwind, viewport meta | ~10 |
| `frontend/avatar.js` | Minor: resize on init + listener | ~20 |
| `backend/main.py` | Minor: accept mp4 content-type on upload | ~5-10 |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| dvh unsupported in older browsers | Low | 100vh fallback via `@supports not (height: 100dvh)` |
| Codec chain regression on desktop Chrome | Low | webm tried first; mp4 is fallback only |
| Backend mp4 acceptance breaks validation | Low | Extend existing content-type allowlist, no logic change |
| Overlay z-index conflicts with Three.js canvas | Medium | Test z-index layering; use existing `--z-context-mobile: 100` |

## Rollback

Single PR revert via `git revert`. No database migrations, no backend logic changes. Desktop behavior untouched — rollback affects only mobile path.

## Dependencies

- None — all changes are CSS/JS/HTML frontend + minor backend content-type tweak

## Success Criteria

- [ ] Voice interview completes end-to-end on iPhone Safari (record → STT → LLM → TTS playback)
- [ ] Voice interview completes end-to-end on Android Chrome
- [ ] Desktop behavior pixel-identical (zero visual regression at ≥769px)
- [ ] Mic button visible and tappable (not hidden behind address bar)
- [ ] No double-tap zoom during interview
- [ ] Context panel slides in/out as overlay on mobile
- [ ] All existing tests pass unchanged
- [ ] Total changes ≤ 400 lines in single PR
