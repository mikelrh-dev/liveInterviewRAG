# Exploration: UI Mission Control V2

## 1. Current Frontend State

### Files in scope (frontend only)

| File | Lines | Role |
|------|-------|------|
| `frontend/index.html` | 122 | Single-page HUD layout: slim header, conversation panel, hero center (500x500 avatar + orbital ring + waveform + mic), RAG context slide-in panel |
| `frontend/style.css` | 770 | Cyan HUD theme: glassmorphism panels, holographic video filter, scan lines overlay, particle background styling |
| `frontend/app.js` | 862 | State machine (idle/listening/processing/speaking), mic + VAD + MediaRecorder, SSE pipeline, waveform bars (32), context panel fetch/render, typing animation |
| `frontend/avatar.js` | 203 | Three.js scene: outer halo sphere (opacity 0.12) + 3 torus energy rings (expand on voice, additive blending). Exposes `setVolume`, `setState`, `boost`, `resize` |

### Current layout architecture (single-column stack)

```
┌─────────────────────────────────────┐
│ HEADER: "MIKEL — GEMELO DIGITAL"   │  ← 60px, slim
│        [Contexto] button            │
├─────────────────────────────────────┤
│ Conversation panel (scrollable)     │  ← 40vh, glassmorphism
│  · system messages centered         │
│  · user bubbles right-aligned       │
│  · candidate bubbles left-aligned   │
│  · avatars as initials in circles   │
│  · audio indicator bars             │
│  · typing indicator with 3 dots     │
├─────────────────────────────────────┤
│ HERO CENTER (flex column, centered) │  ← flex: 1
│  ┌─────── 500x500 ──────┐          │
│  │  Portal ring (circle) │          │
│  │  · neutral video      │          │
│  │  · talking video      │          │
│  │  · scan lines overlay │          │
│  │  · mouth glow div     │          │
│  │  · Three.js orb canvs │          │
│  └───────────────────────┘          │
│  ┌─ Orbital ring (SVG) ─┐          │  ← 220x220, absolute
│  └───────────────────────┘          │
│  ┌─ Waveform (32 bars) ─┐          │  ← 200x40
│  └───────────────────────┘          │
│      ○ Mic button (64px)            │
│  "Preparado para escuchar" (status) │
├─────────────────────────────────────┤
│ RAG Context panel (slide-in right)  │  ← 320px, fixed position
│  · header "Contexto RAG"            │
│  · chunk pills with scores          │
└─────────────────────────────────────┘
```

### Visual style (current)

- **Background**: `#0a0e1a` (deep navy), tsparticles animated dots
- **Glass panels**: `rgba(15, 23, 42, 0.6)` with `backdrop-filter: blur(12px)`
- **Accent**: Cyan `#00d4ff` throughout
- **Typography**: No external fonts loaded (uses system fallbacks for sans, JetBrains Mono declared but never loaded via `@import` or `<link>`)
- **Video effects**: `contrast(1.08) saturate(1.25) brightness(1.05)` holographic filter + scan lines overlay
- **Z-index stack**: `particles-bg: 0` → `hud-layout: 1` → videos `1` → scanlines `2` → orb canvas `2` → context-panel `100` → overlay `1000`

### Current state machine flow

```
idle → (click mic) → listening → (VAD timeout) → processing
  ↑                                                    ↓
  └─────────── (done, restart loop) ←── speaking ←─────┘
```

### What's working well

- State machine in `app.js` with clean `setState()` that coordinates ring, orb, status text, and video crossfade
- SSE pipeline with typed events (transcription, token, audio_chunk, done, interview_end, error)
- Audio queue with sequential chunk playback
- Typing animation with blinking cursor
- Context panel with expandable chunks
- Exponential backoff for network retries

### What's NOT working well (opportunities for improvement)

1. **Layout is fragile**: single-column with `max-width: 900px` centered. No sidebar. No system status panel. Everything is stacked vertically.
2. **No external fonts loaded**: JetBrains Mono declared in CSS but never loaded — all text falls back to system fonts
3. **Scan lines + holographic filter**: User specifically wants these removed
4. **Particles background**: Broken (was removed via defer fix, but the div and init code remain)
5. **No latency/status display**: No system health, no real-time stats, no model info visible
6. **Avatar is dominant (500x500)**: Takes up most of the viewport
7. **Conversation panel is cramped**: 40vh on a full-height layout means limited visible messages
8. **Responsive breakpoints are basic**: Only 768px and 360px media queries
9. **No text input fallback**: Only mic input — no way to type if mic fails
10. **RAG panel is a slide-in**: Hidden by default, auto-closes after 5s — user may miss it

---

## 2. Constraints and Risks

### CSS / Layout Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Z-index conflicts** | New sidebar (240px) + right panel (320px) + header + conversation panel + overlay must all coexist without overlap | Define a clear z-index layer map (see below). Keep Three.js canvas at its own layer. Context panel already uses `z-index: 100`. Overlay uses `1000`. |
| **Three.js canvas positioning** | The orb canvas is currently inside `.portal-ring` (position: relative). Moving the avatar to the left requires repositioning while keeping the canvas in sync with the portal-ring div | The `avatar.js` renderer uses `document.getElementById('orb-canvas')` — as long as the canvas element stays inside the avatar wrapper, it will render correctly. `avatar.js::resize()` must be called on layout change. |
| **Video elements + absolute positioning** | Videos use `position: absolute; inset: 0` inside `.portal-ring`. If the portal ring moves, videos follow. | Keep the same structure: portal-ring wraps videos + scanlines + canvas. Only resize the wrapper. |
| **Responsive breakpoints** | New 3-zone layout (sidebar + center + right panel) is harder to make responsive. At < 1024px the layout may need to collapse. | Plan progressive collapse: 1024px+ 3-zone, 768-1023px hide sidebar, < 768px single-column fallback (hide right panel behind toggle). |
| **Existing CSS specificity** | Current CSS uses class-based selectors. No BEM or scoping. Risk of cascading conflicts when rewriting. | Full rewrite of `style.css` is expected. No need to maintain backward compatibility with current selectors since HTML structure changes significantly. |

### Functional Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **State machine coupling** | `setState()` in `app.js` updates ring class, orb state, orb boost, status text, AND video crossfade. Any of these can break if DOM IDs/classes change. | Keep all IDs the same (`#orbital-ring`, `#btn-mic`, `#status`, `#avatar-neutral-video`, `#avatar-talking-video`, `#orb-canvas`). The Three.js init in `avatar.js` references `#orb-canvas` by ID. |
| **SSE event handler DOM references** | `addMessage()`, `showTyping()`, `hideTyping()`, `appendTypingText()` all create/modify DOM elements inside `#conversation`. If the conversation container structure changes, these break. | Keep `#conversation` as the scrollable message container. Message structure can change (avatars in bubbles, timestamps) but the parent div ID must stay the same. |
| **Context panel toggle** | `contextToggle` and `contextClose` buttons referenced by ID. These must remain or be updated. | Keep `#context-toggle` and `#context-panel` IDs. The HTML structure inside can change. Alternatively, update the button references. |
| **Overlay click handler** | Audio block overlay uses `#audio-blocked-overlay` by ID. Must keep the overlay mechanism. | Keep the overlay element and its ID. Visual style can change. |
| **tsparticles dependency** | Particles `#particles-bg` div and `initParticles()` call exist but particles library was already broken. The orchestrator doesn't mention particles in the new design. | Either remove or replace with CSS-only grid background. Removing the `#particles-bg` div and `tsparticles` script tag is safe. |

### Code Integrity Risks (DO NOT TOUCH)

- **`backend/`**: Zero changes. The orchestrator explicitly forbids backend code changes.
- **`avatar.js`**: No logic changes. Only CSS/HTML changes that reposition the avatar wrapper are allowed. The Three.js code must continue to work unchanged.
- **`app.js` state machine, SSE pipeline, audio queue, VAD, recording logic**: Must remain intact. Only DOM-related helper functions (`addMessage`, `showTyping`, etc.) may need minor structural updates to match new HTML classes.

### Performance / Asset Risks

- **Two video elements**: Both play simultaneously (neutral visible, talking crossfades). This doubles GPU memory for video decoding. The 500x500 avatar at ~380x380 saves some pixels but still renders two video streams.
- **Three.js WebGL context**: The canvas resizes with the wrapper. If the wrapper shrinks to 380x380, the renderer must be resized via `AvatarOrb.resize(380, 380)`.
- **No WebGL fallback**: The orb falls back gracefully (hides canvas) but the CSS-only visual is less impressive. Keep this behavior.

---

## 3. Proposed Design Direction

### 3.1 Layout Blueprint

```
┌─────────────────────────────────────────────────────────────────────┐
│  HEADER: [logo] MIKEL OS    ● Online    ⏱ 1.2s    [Contexto ①]   │  ← 64px
├────────┬────────────────────────────────────────────────┬──────────┤
│        │                                                │          │
│ SIDEBAR│           MAIN AREA                           │ CONTEXT  │
│ 240px  │                                                │ PANEL    │
│        │  ┌──────────────────────────────────────┐      │ 320px    │
│ ● Sess │  │  Conversation (scrollable)           │      │          │
│   Info │  │  · User bubble (right, avatar inside) │      │ Header   │
│        │  │  · AI bubble (left, avatar inside)    │      │ "Context"│
│ ● Syst │  │  · Timestamps                         │      │          │
│   Stat │  │  · Typing indicator                   │      │ Search   │
│   ● OK │  └──────────────────────────────────────┘      │ bar      │
│        │                                                │          │
│ ● MODL │        ┌──────────────┐                        │ Chunk    │
│   TTS  │        │  Avatar      │                        │ cards    │
│   STT  │        │  380x380     │                        │ w/ score │
│   LLM  │        │  halo        │                        │          │
│        │        │  + rings     │                        │          │
│ ● VU   │        │  (left)      │                        │          │
│   ┃┃┃  │        └──────────────┘                        │          │
│        │                                                │          │
│        │           ○ Mic (bigger)                       │          │
│ [End]  │    "Escuchando..." (status)                    │          │
│        │    [✏️ Text input...]                          │          │
├────────┴────────────────────────────────────────────────┴──────────┤
│  Subtle perspective grid background                                │
└─────────────────────────────────────────────────────────────────────┘
```

### Zone dimensions

| Zone | Width | Behavior |
|------|-------|----------|
| Sidebar | 240px | Fixed, scrollable on overflow |
| Main | `calc(100% - 240px - 320px)` = flex grow | Contains conversation (top) + avatar+controls (bottom) |
| Context panel | 320px | Fixed, right-aligned, always visible |
| Header | 100% | 64px tall, fixed-top |

### Responsive collapse strategy

- **≥ 1200px**: Full 3-zone layout
- **1024–1199px**: Sidebar collapses to icon-only (40px), context panel stays
- **768–1023px**: Sidebar hidden (toggle via hamburger), context panel slide-in (as today)
- **< 768px**: Single column, sidebar + context as slide-in panels, avatar 280x280

### 3.2 Color Design Tokens

```css
:root {
    /* Background */
    --bg-deep: #050b1a;
    --bg-surface: #0a1228;
    --bg-panel: rgba(10, 18, 40, 0.85);
    --bg-glass: rgba(15, 25, 50, 0.6);

    /* Cyan palette */
    --cyan-primary: #00d4ff;
    --cyan-light: #7eeaff;
    --cyan-dim: rgba(0, 212, 255, 0.3);
    --cyan-glow: rgba(0, 212, 255, 0.12);

    /* Semantic */
    --green: #22c55e;
    --amber: #f59e0b;
    --red: #ef4444;
    --violet: #8b5cf6;        /* for AI state */

    /* Text */
    --text-primary: #e8f4ff;
    --text-secondary: #8899b4;
    --text-muted: #556688;

    /* Borders */
    --border-subtle: rgba(0, 212, 255, 0.1);
    --border-active: rgba(0, 212, 255, 0.3);

    /* Glass */
    --glass-border: rgba(0, 212, 255, 0.08);
}
```

**Differences from current**: Deep blue `#050b1a` (was `#0a0e1a`), lighter text `#e8f4ff` (was `#e2e8f0`), added amber `#f59e0b` (was `#fbbf24`). Lower contrast/saturation — no holographic filters.

### 3.3 Spacing / Typography

```css
:root {
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;

    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 16px;
    --space-lg: 24px;
    --space-xl: 32px;

    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 16px;
    --radius-full: 50%;

    --header-h: 64px;
    --sidebar-w: 240px;
    --context-w: 320px;
    --avatar-size: 380px;
}
```

**Note**: Inter and JetBrains Mono must be loaded via `<link>` in the HTML head (Google Fonts or self-hosted). Currently NO external fonts are loaded.

### 3.4 Component Breakdown

#### REMOVED (delete from HTML + CSS)

| Component | Reason |
|-----------|--------|
| `#particles-bg` div | broken, not in new design, replaced by CSS grid |
| `tsparticles` script tag | broken dependency, not needed |
| `.avatar-scanlines` div | no holographic effects |
| `.avatar-video` CSS `filter` property | remove `contrast(1.08) saturate(1.25) brightness(1.05)` |
| `.portal-ring::before` / `::after` pseudo-elements | dashed/orbit rings are redundant with Three.js rings |
| Current `.hud-layout` max-width centering | replaced by grid layout |

#### ADDED (new HTML + CSS)

| Component | Description |
|-----------|-------------|
| **Sidebar** `<aside id="sidebar">` | 240px fixed panel on left |
| Sidebar: session info section | Conversation ID, duration, turn count |
| Sidebar: system status | Pulsing green dot + "All Systems Online" |
| Sidebar: model info | Three rows: TTS model, STT model, LLM model with status dots |
| Sidebar: VU meter | CSS-only animated bars showing mic volume level (driven by existing `analyserNode` RMS) |
| Sidebar: end-session button | Red outlined button, calls `stopInterview()` |
| **Header improvements** | Logo + "MIKEL OS" title, green online pill, live latency stat, Contexto button with notification badge |
| **Grid background** | CSS-only perspective grid overlay (cyberpunk floor effect) |
| **Vignette overlay** | CSS `radial-gradient` darkening at viewport edges |
| **Text input fallback** | Input field below mic, hidden by default, shown when mic fails or via toggle |
| **Timestamp spans** | Small timestamps on conversation bubbles |
| **Right context panel (always visible)** | Current slide-in becomes fixed right panel with better visual treatment |
| **Context search bar** | Simple filter input at top of context panel |
| **Number ticker animation** | Animated counter for latency/status numbers |
| **Status pulse animation** | Breathing pulse on the online status dot |
| **Hover glow effects** | Subtle cyan glow on hover for interactive elements |

#### MODIFIED (existing elements with changes)

| Component | Changes |
|-----------|---------|
| **Header** | New logo area, title changes to "MIKEL OS", add online pill, add latency stat, keep Contexto button with badge |
| **Avatar wrapper** | Resize from 500x500 → 380x380. Reposition to left side of main area (float left or grid column). Keep all internal structure (videos, canvas, scanlines removed, glow kept). |
| **Conversation panel** | Move to top of main area. Add avatar images inside bubbles (use existing user/candidate avatars but positioned inside asymmetric corner bubbles). Add timestamps. |
| **Mic button** | Increase size from 64px → 80px. Subtle glow ring when active. |
| **Status text** | Below mic, same behavior but updated styling. |
| **Orbital ring** | Keep SVG ring, resize proportionally to match 380x380 avatar. |
| **Waveform** | Keep SVG waveform, reposition below avatar. |
| **Context panel** | Change from fixed slide-in to a positioned panel in the right zone. Always visible (no slide). Better chunk styling. Add search filter. |
| **.hud-status** | Same class names, update visual styling. |

#### UNCHANGED (logic stays identical)

| File | Reason |
|------|--------|
| `avatar.js` | No logic changes — Three.js continues working. Only CSS container size changes. Must call `AvatarOrb.resize(380, 380)` after layout loads. |
| `app.js` state machine | `setState()`, VAD, recording, SSE pipeline, audio queue — all unchanged |
| `app.js` DOM helpers | `addMessage()`, `showTyping()`, `hideTyping()`, `appendTypingText()`, `fetchContext()`, `renderContext()` — keep signatures, update internal DOM structure as needed |

### 3.5 Visual Effects (subtle, performant)

1. **Grid background**: CSS-only using `background-image: linear-gradient` + `perspective`. No canvas, no JS.
2. **Vignette**: `box-shadow: inset 0 0 150px rgba(0,0,0,0.5)` on a fixed overlay div.
3. **Status pulse**: `@keyframes pulse-dot` animation on the green status dot.
4. **Number ticker**: CSS `@property` animation or JS `requestAnimationFrame` counter driven from a start/end value.
5. **Hover glow**: `box-shadow: 0 0 20px var(--cyan-glow)` on hover for interactive elements.
6. **State transitions**: Smooth color/opacity transitions on status text, ring, and mic button.
7. **Conversation fade-in**: Keep existing `fadeIn` keyframe for messages.

### 3.6 Z-Index Layer Map

```
z-index: 0     → Grid background
z-index: 1     → Vignette overlay
z-index: 2     → Particles (if kept) / HUD layout base
z-index: 5     → Sidebar
z-index: 10    → Header
z-index: 90    → Right context panel
z-index: 100   → Context panel (slide-in fallback on mobile)
z-index: 1000  → Audio-blocked overlay
```

### 3.7 Stitch Generation Strategy

| Aspect | Decision |
|--------|----------|
| **Project** | Use existing `ProyectoGemelo` (id: `14471447934404768768`) — already has avatar screens. Or create a new project for the full layout. |
| **Approach** | Generate one master screen showing the full 3-zone layout. Then generate variants for each zone. |
| **Model** | `GEMINI_3_1_PRO` (latest, best quality) — avoid `GEMINI_3_FLASH` for design quality. |
| **Device type** | `DESKTOP` — this is primarily a desktop app. Responsive adaptations are secondary. |
| **Design system** | Create a new design system first with the color tokens and typography above, then generate screens using it. |
| **Prompts** | See section 5 (Open Questions) for prompt details — user may want to refine the visual direction. |

---

## 4. Scope Estimate

### Files affected

| File | Action | Estimated changes |
|------|--------|-------------------|
| `frontend/index.html` | **Rewrite** (~80% new structure) | ~200 lines (from 122) — sidebar, new header, grid background, text input, timestamps |
| `frontend/style.css` | **Rewrite** (~90% new or modified) | ~900–1100 lines (from 770) — new layout, new components, responsive breakpoints |
| `frontend/app.js` | **Modify** (~15% changes) | ~100 lines changed of 862 — minor DOM updates for new classes, resize call, VU meter data binding, timestamps |
| `frontend/avatar.js` | **No change** | 0 lines — but must verify `resize()` is called with new 380x380 dimensions |
| `frontend/` assets | **No change** | Video files remain, favicon remains |

### Total estimated scope

- **HTML lines**: ~200 (was 122)
- **CSS lines**: ~1000 (was 770)
- **JS changes**: ~100 lines modified (of 862 total)
- **Total changed lines**: ~1150–1300

### 400-line review budget assessment

This change will EXCEED the 400-line review budget by a significant margin (~3x). Options:

1. **Single PR with size exception** — simplest, but hard to review
2. **Two chained PRs**: PR #1 = HTML + CSS (layout redesign), PR #2 = JS modifications + polish
3. **Three chained PRs**: PR #1 = CSS tokens + grid + layout scaffolding, PR #2 = component styling + responsive, PR #3 = JS integration + VU meter + polish

**Recommendation**: 3 chained PRs. Each slice is ~400 lines and independently verifiable (visual check).

---

## 5. Open Questions for the User

These need answers before moving to the Proposal phase:

### Design Direction

1. **"MIKEL OS" branding**: The current title is "MIKEL — GEMELO DIGITAL". The proposal suggests "MIKEL OS" — do you confirm this new name?
   - If yes: should the logo be text-only or should I create a simple SVG logo mark?
   - What about the subtitle under "MIKEL OS"? (e.g., "Digital Twin Interface", "Voice Interview System", or nothing?)

2. **Avatar position**: The design puts the avatar on the LEFT side of the main area, with the conversation panel ABOVE it. Two alternatives:
   - **Option A (proposed)**: Conversation on top (flex-grow), avatar+controls below (fixed height)
   - **Option B**: Split screen — conversation on the left 60%, avatar on the right 40% (similar to current but with sidebar)
   - Which do you prefer?

3. **Text input fallback**: Should we add a visible text input below the mic (always visible but secondary), or hide it behind a toggle button? (The current design has no text input at all.)

4. **Context panel visibility**: The current design auto-hides the context panel. The proposal makes it always visible on the right. However, on smaller screens this won't fit. Preference:
   - **Option A**: Always visible on desktop (≥1200px), toggleable on smaller screens (current behavior)
   - **Option B**: Always hidden behind toggle (current behavior) but with improved visual treatment when open

### Visual Style Refinements

5. **Grid background style**: The proposal says "subtle perspective grid (cyberpunk floor)". Three directions:
   - **Option A**: Very subtle blue grid lines on dark background, low opacity
   - **Option B**: More visible grid with slight perspective distortion (true 3D floor effect)
   - **Option C**: No grid, just vignette darkening at edges (simpler, cleaner)

6. **Color accent**: The palette uses cyan as primary. Should we:
   - Keep pure cyan `#00d4ff` (current)
   - Shift slightly toward teal `#0ea5e9` (more blue, calmer)
   - Use the proposed `#00d4ff` with `#7eeaff` light accent?

7. **Avatar resize**: The proposal shrinks the avatar from 500×500 to 380×380. Does this feel right, or would you prefer:
   - Larger (440×440)? (more presence, less space for conversation)
   - Smaller (320×320)? (more space for conversation, less presence)
   - Keep 500×500 but with the sidebar?

### Stitch / Visual Generation

8. **Stitch project**: Shall we use the existing `ProyectoGemelo` project (already has avatar screens) or create a fresh project for the new layout?

9. **Stitch approach**: The proposal suggests generating one master screen first, then variants. Would you like me to:
   - Generate the full layout in one screen first so you can see the composition before refining?
   - Or start with individual components (sidebar, header, conversation) and compose them later?

### Scope / Delivery

10. **Delivery strategy**: This change exceeds the 400-line review budget. The proposal recommends 3 chained PRs (CSS scaffolding → components + responsive → JS polish). Does this approach work for you, or do you prefer fewer/larger PRs?

---

## 6. Risks Summary

| Risk Level | Risk | Mitigation |
|------------|------|------------|
| **Low** | z-index conflicts with new layout layers | Pre-defined z-index map (section 3.6) |
| **Low** | Three.js canvas fails to resize | Call `AvatarOrb.resize(380, 380)` on load; add resize observer as fallback |
| **Low** | Video elements lose positioning | Keep `position: absolute; inset: 0` inside portal-ring |
| **Medium** | `app.js` DOM selectors break with new HTML structure | Keep all existing IDs intact (`#orb-canvas`, `#conversation`, `#btn-mic`, `#status`, etc.) |
| **Medium** | Responsive collapse layout is complex | Progressive breakpoints at 1200/1024/768; test each breakpoint |
| **Medium** | Scan lines removal affects visual balance | The Three.js halo + rings provide enough energy field effect without scan lines |
| **High** | Scope exceeds 400-line budget (3x) | Use chained PRs; each slice is independently verifiable |
| **Very Low** | Backend test breakage | Frontend-only changes cannot affect backend tests |

---

## 7. Ready for Proposal

**Ready: Yes**, with the caveat that the open questions in section 5 should be answered first. The orchestrator should present these to the user before launching `sdd-propose`.

### Key findings to communicate to the orchestrator

1. This is primarily a cosmetic/structural redesign — no new backend features needed
2. The existing state machine and SSE pipeline are solid and only need minor DOM adjustments
3. The Three.js avatar code needs zero logic changes — only a resize call
4. Font loading (Inter + JetBrains Mono) was missing entirely and must be added
5. The scope is ~3x the 400-line review budget — chained PRs are strongly recommended
6. Six critical IDs must remain unchanged: `#orb-canvas`, `#conversation`, `#btn-mic`, `#status`, `#avatar-neutral-video`, `#avatar-talking-video`
