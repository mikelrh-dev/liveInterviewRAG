# ui-mission-control Specification

## Purpose

Layout redesign for interview console: sidebar + main + context panel. Replaces single-column HUD with persistent status, VU meter, always-visible RAG context, 380px avatar. Backend and avatar logic unchanged.

## Requirements

### Requirement: Layout and Visual Foundation

System SHALL render three fixed zones at ≥1200px: sidebar 240px, main flex-grow, context panel 320px. SHALL collapse sidebar: icon-only (40px) at 1024–1199px, hamburger at 768–1023px, single-column below 768px. SHALL load Inter + JetBrains Mono via `<link>`. SHALL apply CSS vars: cyan `#00d4ff` + `#7eeaff`, deep bg `#050b1a`, glass panels, grid + vignette. SHALL NOT use particles, scan lines, or holographic filters.

#### Scenario: Desktop renders three zones

- GIVEN viewport ≥1200px
- WHEN page renders
- THEN sidebar 240px left, main flex-grow, context panel 320px right

#### Scenario: Sidebar collapses at 1024px

- GIVEN viewport 1024–1199px
- WHEN page renders
- THEN sidebar icon-only (40px), context panel full width

#### Scenario: No holographic effects

- GIVEN avatar and background elements render
- WHEN inspected
- THEN no scan lines, holographic filter, or dashed rings SHALL exist

### Requirement: Header and Sidebar Dashboard

System SHALL display 64px header: logo, "MIKEL OS", online pill with pulsing dot, latency ms, Contexto button with unread badge. Sidebar SHALL show session info (short ID, mm:ss, turn count), system status with pulsing green dot, model rows (TTS/STT/LLM), VU meter from `analyserNode` RMS, and "End Session" button calling `stopInterview()`.

#### Scenario: Header shows indicators

- GIVEN page loaded and interview active
- WHEN header renders
- THEN "MIKEL OS", ● Online pill with pulse, latency, Contexto button SHALL be visible

#### Scenario: VU meter shows mic RMS

- GIVEN system in `listening` state
- WHEN `analyserNode` emits RMS values
- THEN VU meter SHALL render real-time animated bars

#### Scenario: End Session stops interview

- GIVEN interview running
- WHEN user clicks "End Session"
- THEN system SHALL call `stopInterview()` and transition to idle

### Requirement: Avatar and Conversation

System SHALL render avatar 380×380, left of main area, preserving video crossfade, Three.js halo + rings, and mouth glow. SHALL call `AvatarOrb.resize(380, 380)` on load. Conversation panel SHALL render above avatar with asymmetric bubbles (user right, AI left), avatar images inside, timestamps, existing fade-in, typing indicator.

#### Scenario: Avatar 380px with all layers

- GIVEN page loaded, interview idle
- WHEN avatar renders
- THEN 380×380 with neutral video, Three.js halo + rings, no scan lines

#### Scenario: Bubbles with avatars and timestamps

- GIVEN conversation has messages
- WHEN panel renders
- THEN each bubble SHALL have asymmetric corners, avatar image inside, timestamp

### Requirement: Controls and Context Panel

System SHALL display 80px mic button with cyan glow, status text, hidden text input on mic failure or toggle. Context panel SHALL be always visible at 320px right (≥768px), with header, search filter, chunk cards with scores. Below 768px SHALL revert to slide-in overlay.

#### Scenario: Mic button 80px with glow

- GIVEN page loaded
- WHEN controls render
- THEN mic button SHALL be 80px, SHALL show cyan glow when clicked

#### Scenario: Context panel visible on desktop

- GIVEN RAG chunks exist and viewport ≥768px
- WHEN page renders
- THEN context panel visible right with chunk cards and scores

#### Scenario: Search filters chunks

- GIVEN multiple chunk cards visible
- WHEN user types in search filter
- THEN only matching cards SHALL remain visible

### Requirement: Animations and Accessibility

System SHALL animate state transitions at 0.3s ease. Online dot SHALL pulse at 2s. Interactive elements SHALL show cyan hover glow. Counters SHOULD animate on change. System SHALL preserve IDs: `#orb-canvas`, `#btn-mic`, `#conversation`, `#status`, `#avatar-neutral-video`, `#avatar-talking-video`. SHALL support keyboard nav with visible focus outlines. Panels SHOULD have `aria-label`.

#### Scenario: Critical IDs survive

- GIVEN HTML rewritten with new layout
- WHEN DOM inspected
- THEN all specified IDs SHALL exist and function

#### Scenario: Keyboard nav with focus outlines

- GIVEN page loaded
- WHEN user presses Tab repeatedly
- THEN focus cycles through interactive elements in logical order with visible outlines
