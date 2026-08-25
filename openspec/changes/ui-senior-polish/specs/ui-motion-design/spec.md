# Delta for UI Motion Design

## ADDED Requirements

### Requirement: Motion Token Foundation

The system SHALL define motion tokens in `:root` — `--dur-fast`, `--dur-base`, `--dur-slow`, `--ease-out`, `--ease-spring` — and every newly added transition SHALL consume these tokens. `frontend/style.css` SHALL NOT contain any `transition: all` declaration (grep-verifiable).

#### Scenario: No transition-all remains in stylesheet

- GIVEN the repository checkout of this change
- WHEN `frontend/style.css` is searched for `transition: all`
- THEN zero matches are found
- AND previously affected selectors (#btn-mic, #context-toggle, #mobile-end-btn, .chunk-pill…) declare explicit property lists

#### Scenario: New transitions reference tokens

- GIVEN a newly added transition on an interactive element
- WHEN its declaration is inspected
- THEN durations come from `--dur-*` and easings from `--ease-*`

### Requirement: Reduced-Motion Compliance

When `prefers-reduced-motion: reduce` is active, the system SHALL disable ALL infinite/decorative animations (`pulse-dot`, `pulse-red`, `typingBounce`, `audioPlay`, `pulse-text`, `pulse-sync`), SHALL reduce transitions to ≤0.01ms, and SHALL preserve non-motion feedback: state colors, borders, and the focus ring.

#### Scenario: Reduced-motion user gets static but color-coded states

- GIVEN the OS has reduced motion enabled
- WHEN the app enters listening/processing/speaking states
- THEN no infinite animation runs
- AND state colors and borders remain fully visible

#### Scenario: Keyboard focus ring survives reduced motion

- GIVEN reduced motion is enabled
- WHEN a keyboard user tabs through interactive elements
- THEN the focus ring appears immediately with no transition delay

### Requirement: Staggered Page-Load Reveal

The system SHALL fade-up reveal header, avatar, controls, and status exactly once on page load, with a total stagger under 600ms and `animation-fill-mode: backwards`.

#### Scenario: Load reveals complete under 600ms

- GIVEN the page finishes loading
- WHEN the reveal sequence plays
- THEN all four zones reach full opacity within 600ms of first paint
- AND no zone remains hidden after the animation window (fill-mode backwards)

### Requirement: Mic Button Per-State Visuals

The mic button SHALL expose a visual state via `body`/button `data-state` written in `setState()`: `idle` (default), `listening` (cyan border + glow), `processing` (amber), `speaking` (green). Existing `.active` class behavior SHALL remain unchanged.

#### Scenario: Mic state changes visible without motion

- GIVEN reduced motion is enabled
- WHEN state changes idle → speaking
- THEN the border/glow switches to green with no animation required

#### Scenario: Data-state is additive over .active

- GIVEN existing `.active` styling and consumers
- WHEN `data-state` is introduced
- THEN `.active` selectors behave identically to before the change

### Requirement: Spring-Eased Context Drawer

The context drawer transform transition SHALL consume the spring easing token.

#### Scenario: Drawer opens with spring easing

- GIVEN the context panel is closed
- WHEN the user toggles it open or closed
- THEN its `transform` transition uses `--ease-spring`
