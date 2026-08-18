# Tasks: UI Mission Control V2

## Review Workload Forecast

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium

| Unit | Goal | PR | Base |
|------|------|----|------|
| 1 | CSS scaffolding + HTML skeleton | #1 | main |
| 2 | Components (sidebar, header, avatar, context, controls) | #2 | main |
| 3 | Polish (animations, data wiring, end-session) | #3 | main |

## PR #1 — Scaffolding (~400 lines)

- [x] 1.1 Add Tailwind CDN + Google Fonts (Sora, Inter, JetBrains Mono) to `<head>` with inline `tailwind.config` for Orbital Precision tokens
- [x] 1.2 Add CSS vars: palette, typography, spacing, z-index map; replace old `:root` vars
- [x] 1.3 Build 3-zone CSS Grid: `grid-template-columns: 320px 1fr 320px` (Stitch HTML uses w-80 = 320px for both sidebars, not 240px)
- [x] 1.4 Add CSS-only grid bg (`linear-gradient`+`perspective`) + vignette overlay
- [x] 1.5 Write HTML skeleton: `.app-grid`, `<header>`, `<aside id="sidebar">`, `<main>` with `#conversation`+`.hero-center`, `<aside id="context-panel">`
- [x] 1.6 Build top nav: logo, "MIKEL OS", `#status` pill, latency span, Contexto button
- [x] 1.7 Remove `#particles-bg`, tsparticles script, `.avatar-scanlines`, video filter property, `.portal-ring::before/::after`, `.hud-layout` max-width
- [x] 1.8 Prune CSS: keep `.hidden`, `.overlay`, `.message`, `.bubble`, `.typing-*`, `.chunk-pill`, `.context-panel`; delete `.hud-layout`, `.hud-header`, `.hud-btn`, `.hud-status`

**Files**: `frontend/index.html`, `frontend/style.css`
**Verify**: 3 empty zones visible, fonts load, no console errors, 6 critical IDs exist in DOM

## PR #2 — Components (~400 lines)

- [ ] 2.1 Sidebar: session info (id, timer placeholder, turn count), sync-pulse status dot, model rows (TTS/STT/LLM), VU meter placeholder, end-session button
- [ ] 2.2 Avatar zone 380×380: TWO `<video>` elements (`#avatar-neutral-video` and `#avatar-talking-video` — the actual animated avatar, not a static image) inside the portal-ring, plus `#orb-canvas` (Three.js halo + rings). Crosshair corners, dashed target ring, light scanning line. The `frontend/assets/avatar-stitch.png` stays in assets/ as legacy fallback but is NOT loaded into the new HTML.
- [ ] 2.3 Conversation: scrollable `#conversation`, asymmetric bubbles with avatars + timestamp spans, typing indicator, fadeIn keyframe
- [ ] 2.4 Context panel: fixed 320px, header, search `<input>`, `#context-content` for chunk cards with scores, close button. Latency stat in top nav is STATIC placeholder text "12ms" (per user decision Q2=A — no live SSE event for now)
- [ ] 2.5 Controls: `#btn-mic` 80px with cyan accent ring, `#status` text below, ALWAYS-VISIBLE text input fallback (per user decision Q1=A — not hidden, shown always so users can type if they prefer)
- [ ] 2.6 Responsive: ≥1200px full grid, 1024-1199px sidebar→40px icons, 768-1023px sidebar hidden+hamburger, <768px single-column
- [ ] 2.7 Add `--cyan-accent: #00d4ff` CSS vars scoped to avatar zone only
- [ ] 2.8 Call `AvatarOrb.resize(380,380)` after DOM ready in `app.js` `init()`
- [ ] 2.9 Remove `initParticles()` call from `app.js`
- [ ] 2.10 Add decorative data readouts (lat/lng, sys temp, uplink) near avatar

**Files**: `frontend/index.html`, `frontend/style.css`, `frontend/app.js`
**Verify**: All components render, videos + Three.js halo/rings work, mic visible, conversation scrollable, context searchable, 6 IDs preserved

## PR #3 — Polish (~400 lines)

- [ ] 3.1 Wire VU meter: set `--vu-level` CSS custom property in rAF loop, animate sidebar bars via CSS `calc()`
- [ ] 3.2 Wire session timer: `setInterval` in `app.js` updates mm:ss display
- [ ] 3.3 Wire end-session button: click handler calls `stopInterview()` with optional confirm
- [ ] 3.4 Keep `setStatus()` working with new header `#status` DOM structure
- [ ] 3.5 Hover glow effects: cyan accent on avatar zone, white on sidebar/nav interactive elements, `transition: all 0.2s`
- [ ] 3.6 Add `pulse-sync` keyframe, verify on system status dot
- [ ] 3.7 Wire text input fallback: form submit sends text to backend (new endpoint or existing message endpoint) and treats it like a transcripted message. Visible always (per Q1=A).
- [ ] 3.8 Update `addMessage()` in `app.js` for timestamps + new bubble HTML structure
- [ ] 3.9 Verify `setState()` crossfade works in all 4 states (idle/listening/speaking/processing)
- [ ] 3.10 Visual compare to Stitch screenshot; confirm 57/57 backend tests pass

**Files**: `frontend/style.css`, `frontend/app.js`
**Verify**: VU meter pulses, timer counts up, end-session stops interview, text input appears on mic error, animations smooth, no console errors, tests pass

## Acceptance Criteria (full change)

- [ ] 3-zone layout at ≥1200px with correct dimensions
- [ ] 6 critical DOM IDs survive and function
- [ ] VU meter shows live RMS from `analyserNode`
- [ ] Responsive at 1024px, 768px, <768px
- [ ] 57/57 backend tests pass unchanged
- [ ] Avatar 380×380 with Three.js halo+rings and video crossfade
- [ ] Context panel always visible on desktop with chunk cards
- [ ] End-session button calls `stopInterview()`
- [ ] No scan lines, holographic filter, or particles present

## Open Questions for Orchestrator

1. **Text input**: **A — always visible** (user decision)
2. **Latency**: **A — static placeholder** (user decision)
3. **Chain strategy**: **A — `stacked-to-main`** (user decision)
