# Design: UI Mission Control V2

## Technical Approach

Replace single-column HUD with fixed 3-zone CSS Grid layout. Rewrite HTML/CSS (~90%), minimally modify `app.js` (~15%). Three chained PRs, each ~400 lines independently verifiable. Zero backend or `avatar.js` changes.

**Stitch note**: `stitch_generate_screen_from_text` timed out consistently (server-side capacity). Three existing "Mikel OS" design systems in Stitch (assets `6515c3ba1f3f4490b9e27cae949e3788`, `4d787d3797c64c03954a0f9213289ced`, `6b9c51d37f39466d82bf346e56677f8a`) align with our tokens: `#050b1a` bg, `#00d4ff` primary, Inter + JetBrains Mono, glassmorphism, no holographic effects. The second design system (`6515c3ba1f3f4490b9e27cae949e3788`) is the closest match — used as visual reference. Stitch project `16094787674865125631` ("Mission Control v2") created.

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Layout engine | CSS Grid (`grid-template-columns: 240px 1fr 320px`) | Fixed zones, no calc(), clean responsive via `grid-template-columns` media queries |
| Background | CSS-only grid + vignette | Replace broken tsparticles; no JS, no canvas, fast |
| Font loading | Google Fonts `<link>` in `<head>` | Currently no fonts loaded; simplest approach, preconnect for perf |
| VU meter | CSS bars driven by `analyserNode` RMS in existing rAF loop | Reuse existing `startVisualizationLoop`; add 1-2 lines to write RMS to CSS custom prop `--vu-level` |
| Status pulse | `@keyframes` with `box-shadow` animation | Pure CSS, no JS overhead |
| Number ticker | CSS `@property` + `counter` trick, or minimal rAF | Avoids library dependency; evaluated `countUp.js` but ~15 lines of vanilla is lighter |
| End-session | Call existing `stopInterview()` directly | Function already exists, just needs button wiring |
| Responsive | 3 breakpoints (1200, 1024, 768) in CSS | Matches spec; sidebar collapses progressively |

## Data Flow

```
app.js (rAF loop)
  ├── analyserNode.getByteTimeDomainData → RMS
  │     └── document.documentElement.style.setProperty('--vu-level', rms)
  │           └── CSS VU meter bars animate via calc()
  │
  ├── AvatarOrb.setVolume()  (unchanged)
  └── waveform bars          (unchanged, repositioned)

app.js SSE pipeline
  ├── addMessage() → new bubble HTML with avatar + timestamp
  ├── setState()   → orbitalRing class (unchanged), status class (unchanged)
  └── fetchContext() → renderContext() → right panel chunks

StopInterview()  ←─── End Session button click
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `frontend/index.html` | Rewrite | New grid layout, sidebar `<aside>`, header, avatar 380×380, text input, timestamps in bubbles |
| `frontend/style.css` | Rewrite | CSS vars (new palette), Grid layout, all new components, responsive, animations |
| `frontend/app.js` | Modify (~30 lines) | VU meter binding, `AvatarOrb.resize(380,380)`, bubble timestamp, end-session wiring, remove `initParticles()` |

**PR breakdown**:

| PR | Files | Lines | Scope |
|----|-------|-------|-------|
| #1 — Scaffolding | `index.html` + `style.css` partial | ~400 | CSS vars, font loading, grid bg + vignette, 3-zone grid, z-index map, HTML skeleton. Remove particles div+script, scanlines, holographic filter, dashed pseudo-rings. |
| #2 — Components | `index.html` (finish) + `style.css` (finish) + `app.js` (minimal) | ~400 | Sidebar (session, status, models, VU, end-btn), header (logo, pill, latency, Contexto), context panel (always visible + search), conversation (avatars+timestamps), avatar 380×380 + controls. Responsive breakpoints. |
| PR #3 — Polish | `style.css` + `app.js` | ~400 | VU meter RMS binding, pulse animation, ticker animation, hover glow, end-session wiring, text input fallback, visual verification. |

## Critical IDs (must survive)

| ID | Used by | Must stay |
|----|---------|-----------|
| `#orb-canvas` | `avatar.js` line 18, `init()` | Yes |
| `#btn-mic` | `app.js` line 46, `toggleInterview` | Yes |
| `#conversation` | `app.js` line 47, `addMessage` | Yes |
| `#status` | `app.js` line 48, `setStatus` | Yes |
| `#avatar-neutral-video` | `app.js` line 58, `setState` crossfade | Yes |
| `#avatar-talking-video` | `app.js` line 59, `setState` crossfade | Yes |

## Removals

| Element | Reason |
|---------|--------|
| `#particles-bg` div + tsparticles script | Broken, replaced by CSS grid |
| `.avatar-scanlines` | No holographic effects |
| `.avatar-video` CSS `filter` property | Remove `contrast(1.08) saturate(1.25) brightness(1.05)` |
| `.portal-ring::before` / `::after` | Dashed/orbit rings redundant with Three.js |
| `.hidden` utility | Keep and reuse |

## Z-Index Layer Map

```
0  → Grid background
1  → Vignette overlay
5  → Sidebar
10 → Header
20 → Conversation panel
30 → Avatar + controls (hero center)
90 → Context panel (fixed position)
100 → Context panel (slide-in overlay on mobile)
1000 → Audio-blocked overlay
```

## DOM Structure Plan (new)

```
<div id="audio-blocked-overlay">          ← kept
<div class="app-grid">                     ← NEW: CSS grid container
  <header class="hud-header">             ← rewritten
    [logo] MIKEL OS ● Online ⏱ 12ms [Contexto 3]
  </header>
  <aside id="sidebar">                    ← NEW
    [session info, system status, models, VU meter, End Session]
  </aside>
  <main class="main-area">
    <div id="conversation">               ← same ID, new position
      [messages with avatars + timestamps]
    </div>
    <div class="hero-center">
      <div class="avatar-wrapper">         ← 380x380
        [same internal structure: videos, canvas, no scanlines]
      </div>
      [orbital-ring, waveform, btn-mic, status, text-input]
    </div>
  </main>
  <aside id="context-panel" class="context-panel-fixed">  ← always visible
    [header, search, chunk cards]
  </aside>
</div>
<div class="vignette-overlay">            ← NEW: fixed, pointer-events: none
```

## Testing

| Layer | What | How |
|-------|------|-----|
| Visual | 3-zone layout renders | Manual check at ≥1200px; verify sidebar 240px, context 320px |
| Visual | No holographic effects | Inspect CSS: no scanlines div, no filter property on videos |
| Visual | Avatar 380×380 | Measure `.avatar-wrapper` dimensions, confirm `resize()` called |
| Integration | VU meter shows RMS | Open DevTools, inspect `--vu-level` custom property during `listening` |
| Regression | 6 critical IDs exist | `document.getElementById()` for all 6 — must return elements |
| Regression | Backend tests | `python -m pytest tests/ -v` — 57/57 must pass |
| Responsive | Collapse at breakpoints | Resize browser to 1024px, 768px, 360px; verify layout adapts |
| Functional | End Session button | Click during interview → `stopInterview()` called → state resets |

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| DOM selectors in `app.js` break | Keep 6 critical IDs (above); search `app.js` for `getElementById` / `querySelector` before renaming any class |
| Three.js canvas renders black | Keep `#orb-canvas` inside `.portal-ring` with same z-index. Call `resize(380,380)` after layout. Canvas uses `position: absolute; inset: 0` — responsive. |
| Responsive complexity | 3 breakpoints only; sidebar collapse uses simple `display: none` → hamburger; context panel falls back to existing slide-in at <768px |
| Conversation panel height calculation | Use `flex` on main area: conversation `flex: 1`, hero center `flex-shrink: 0` with fixed height based on 380px avatar |
| Scan lines removal unbalances design | Three.js halo + rings provide enough energy field; adjusted `--cyan-glow` opacity compensates |

## Open Questions

- [ ] Text input fallback: always visible (secondary) or hidden behind toggle? (Spec says hidden, shown on mic failure or toggle)
- [ ] Latency value source: currently no live latency data in frontend. Should we add a small SSE event or keep it static? (Spec says live — likely needs backend event)
- [ ] Stitch generation: retry after server capacity clears? Visual reference only, doesn't block implementation
