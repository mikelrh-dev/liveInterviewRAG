# Mobile Responsive Layout Specification

## Purpose

Ensure the application renders correctly on mobile viewports (360–768px) while remaining pixel-identical to the current desktop layout at ≥769px. Covers CSS grid collapse, dynamic viewport height, avatar scaling, and safe-area insets.

## Requirements

### Requirement: Single-Column Layout Below Breakpoint

The system SHALL collapse the three-zone grid (`sidebar` + `main` + `context`) into a single-column layout when viewport width is ≤768px. At ≥769px the layout SHALL be pixel-identical to current desktop behavior with no visual regression.

#### Scenario: Phone portrait loads single column

- GIVEN a device with viewport width 390px
- WHEN the page loads
- THEN `.main-grid` renders as a single column (full-width main area)
- AND the sidebar is hidden
- AND the context panel area is hidden

#### Scenario: Desktop unchanged rendering

- GIVEN a device with viewport width 1440px
- WHEN the page loads
- THEN `.main-grid` renders the three-zone layout (320px sidebar + flexible main + 320px context)
- AND all elements are positioned identically to the pre-change layout

### Requirement: Dynamic Viewport Height

The system SHALL use `100dvh` for the app container height on mobile so the mic button is never hidden behind the browser address bar. A `100vh` fallback SHALL apply in browsers that do not support `dvh`. The viewport meta tag SHALL include `viewport-fit=cover`.

#### Scenario: Mic button visible on iOS Safari

- GIVEN a user opens the app on iPhone Safari (390×844 viewport with address bar)
- WHEN the page renders
- THEN the mic button is fully visible without scrolling
- AND the app container height accounts for dynamic browser chrome

#### Scenario: Fallback for browsers without dvh

- GIVEN a browser that does not support `height: 100dvh`
- WHEN the page loads inside `@media (max-width: 768px)`
- THEN `--app-height` resolves to `100vh` via the `@supports not (height: 100dvh)` fallback

### Requirement: Fluid Avatar Scaling

The system SHALL scale the Three.js avatar container fluidly using `max-width` with a `90vw` clamp so it never causes horizontal overflow down to 360px viewports. The Three.js renderer SHALL call `resize()` on initialization and on window resize events.

#### Scenario: Avatar fits on 360px viewport

- GIVEN a device with viewport width 360px
- WHEN the page loads
- THEN the avatar container width does not exceed 90% of the viewport
- AND no horizontal scrollbar appears

#### Scenario: Avatar resizes on orientation change

- GIVEN a phone in landscape mode
- WHEN the user rotates to portrait
- THEN the Three.js renderer resizes to fit the new container dimensions
- AND the avatar renders without distortion

### Requirement: Safe-Area Insets

The system SHALL apply `env(safe-area-inset-top)` and `env(safe-area-inset-bottom)` padding to the header and footer respectively when `viewport-fit=cover` is set.

#### Scenario: iPhone notch compensated

- GIVEN an iPhone with a notch (viewport-fit=cover)
- WHEN the page renders
- THEN the header content does not overlap the notch
- AND the footer content does not overlap the home indicator bar
