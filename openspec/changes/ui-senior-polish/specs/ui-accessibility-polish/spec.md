# Delta for UI Accessibility Polish

## ADDED Requirements

### Requirement: Visible Keyboard Focus Indication

Every interactive element SHALL display a `:focus-visible` indicator meeting 3:1 contrast against adjacent colors.

#### Scenario: Keyboard user sees focus ring

- GIVEN a keyboard-only user
- WHEN Tab moves focus through buttons, inputs, and links
- THEN every focused element shows the cyan focus ring meeting 3:1 contrast
- AND mouse/touch interaction does not trigger the ring

### Requirement: Muted Text Meets WCAG AA Contrast

`--text-muted` SHALL achieve a ≥4.5:1 contrast ratio against `--bg-stardust` (#111111).

#### Scenario: Contrast check passes for muted labels

- GIVEN the updated `--text-muted` value (~#7e90b3)
- WHEN measured against #111111
- THEN the contrast ratio is ≥4.5:1
- AND sidebar/context labels keep their position and size (color-only change)

### Requirement: Icon-Button Labeling and Single H1

Icon-only `#context-close` SHALL carry an `aria-label` naming its purpose, and the document SHALL contain exactly one `<h1>` (the header title) whose visual appearance is pixel-identical to the former span.

#### Scenario: Screen reader announces context-close purpose

- GIVEN a screen reader user focuses `#context-close`
- WHEN the button is announced
- THEN its accessible name conveys "close context panel" (e.g. "Cerrar panel de contexto")

#### Scenario: Single h1 hierarchy valid

- GIVEN the rendered DOM
- WHEN headings are audited
- THEN exactly one `<h1>` exists
- AND its computed styles match the former `.header-title` span rendering

### Requirement: Typographic and Copy Standards

Status/loading strings SHALL use the ellipsis character "…" instead of ASCII "...", and UI copy SHALL use consistent neutral tuteo register ("Presiona", "Haz clic") replacing voseo forms.

#### Scenario: No straight-triple-dot status strings remain

- GIVEN status/loading literals in `index.html` and `app.js`
- WHEN audited for ASCII `...`
- THEN none remain — all use "…"
- AND no voseo forms remain in `index.html`

### Requirement: Dark Color-Scheme Consistency

The document SHALL declare `color-scheme: dark` on `<html>`, and the `theme-color` meta SHALL match the page background (#131313).

#### Scenario: Browser chrome matches page background

- GIVEN the page loads in a browser supporting color-scheme and theme-color
- WHEN UA-drawn chrome and form controls render
- THEN they follow the dark scheme and theme-color blends with the page background
