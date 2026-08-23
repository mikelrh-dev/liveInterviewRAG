# Mobile Overlay Panels Specification

## Purpose

Provide mobile-specific overlay behavior for the context panel, ensure the sidebar END SESSION button remains accessible, enforce minimum touch targets, and suppress double-tap zoom on interactive controls.

## Requirements

### Requirement: Context Panel Slide-In Overlay

On mobile (≤768px), the context panel SHALL be a `position:fixed` overlay that slides in from the right when `.open` is toggled. The panel SHALL close via the X button AND via backdrop tap. The panel SHALL use `z-index` above the Three.js canvas (`--z-context-mobile: 100`).

#### Scenario: Context panel opens as overlay on phone

- GIVEN a recruiter opens the app on a phone (≤768px viewport)
- WHEN they tap the Context toggle button
- THEN the context panel slides in from the right as a fixed overlay
- AND the Three.js canvas remains visible behind a semi-transparent backdrop

#### Scenario: Backdrop tap closes panel

- GIVEN the context panel is open on mobile
- WHEN the user taps the backdrop area outside the panel
- THEN the context panel closes (slides back to right)

#### Scenario: X button closes panel

- GIVEN the context panel is open on mobile
- WHEN the user taps the X close button inside the panel
- THEN the context panel closes

#### Scenario: Panel z-index above canvas

- GIVEN the Three.js avatar canvas is rendered
- WHEN the context panel overlay opens
- THEN the panel renders above the canvas (z-index 100 > z-index 30)

### Requirement: Sidebar Hidden on Mobile with END SESSION Accessible

On mobile (≤768px), the sidebar SHALL be hidden (`display: none`). The END SESSION button SHALL be accessible from a header control element on mobile so the user can end the session without the sidebar.

#### Scenario: END SESSION reachable on phone

- GIVEN a user is on a phone (≤768px viewport)
- WHEN the user wants to end the session
- THEN a mobile END SESSION button is visible in the header
- AND tapping it triggers the same end-session action as the sidebar button

### Requirement: Touch Target Minimum Size

All interactive controls (context toggle, close button, header menu buttons, mic button) SHALL have an effective touch target area of at least 44×44px.

#### Scenario: Context toggle meets minimum size

- GIVEN the context panel is closed on mobile
- WHEN measuring the context toggle button's tap area
- THEN the effective touch target is ≥44×44px

#### Scenario: Close button meets minimum size

- GIVEN the context panel is open on mobile
- WHEN measuring the X close button's tap area
- THEN the effective touch target is ≥44×44px

### Requirement: Double-Tap Zoom Suppressed on Controls

The system SHALL apply `touch-action: manipulation` to suppress double-tap zoom on interactive controls. Text selection SHALL remain preserved in the conversation transcript area.

#### Scenario: Double-tap on mic does not zoom

- GIVEN the app is open on a phone
- WHEN the user double-taps the mic button rapidly
- THEN the page does not zoom in
- AND the mic recording starts/stops as expected

#### Scenario: Text selection preserved in transcript

- GIVEN a conversation transcript is displayed on mobile
- WHEN the user long-presses on transcript text
- THEN text selection is available (not disabled by touch-action)
