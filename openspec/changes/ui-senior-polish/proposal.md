# Proposal: UI Senior Polish (Mission Control)

## Intent

Audit against Vercel Web Interface Guidelines found strong visual identity but missing senior polish layers: zero keyboard focus styles, no reduced-motion support, six `transition: all` smells, single-visual-state mic button, muted text below WCAG AA (`#556688` on `#111111`), mixed voseo/ASCII-ellipsis copy. This change adds those layers **additively** — no functionality removed, no regression of the recently fixed ≤768px mobile layout. User approved Fases 1–3; Fase 4 deferred to a future change.

## Scope

### In Scope
- **P1 Foundations** (~60 CSS lines + 1 meta tag): motion tokens in `:root` (`--dur-fast/base/slow`, `--ease-out`, `--ease-spring`); global `:focus-visible` cyan ring; `prefers-reduced-motion` block disabling infinite animations (pulse-dot, pulse-red, typingBounce, audioPlay, pulse-text, pulse-sync); `color-scheme: dark` + theme-color `#131313`; intentional `-webkit-tap-highlight-color`
- **P2 Choreography** (~120 CSS lines + minimal JS hook): staggered page-load reveals (header→avatar→controls→status); all 6 `transition: all` → explicit property lists (#btn-mic, #context-toggle, #mobile-end-btn, .chunk-pill…); mic per-state visuals via `data-state` written in `setState()` — idle/listening cyan, processing amber, speaking green; fast status-text color transitions; spring-eased context drawer; hover lift on interactive buttons
- **P3 Accessibility & copy** (~40 CSS lines + HTML/JS text edits): `--text-muted` `#556688`→`~#7e90b3` (≥4.5:1); `aria-label="Cerrar panel de contexto"` on icon-only `#context-close`; `.header-title` span → real `<h1>` with identical visuals; `"..."` → `"…"` in all status strings; voseo → neutral tuteo in index.html ("Presiona", "Haz clic")

### Out of Scope
- Fase 4 signature moment: orbital-ring choreography, empty-state redesign, interview-end summary — future change
- LATENCY 12ms placeholder untouched (real wiring = backend scope creep)
- Any backend change; any functionality removal
- Any modification inside the existing `@media (max-width:768px)` block

## Capabilities

### New Capabilities
- `ui-motion-design`: motion tokens/easing, load choreography, per-state control visuals, hover/drawer motion, reduced-motion compliance
- `ui-accessibility-polish`: focus-visible ring, color-scheme/theme-color, AA text contrast, icon-button labeling, semantic heading, typography/copy standards

### Modified Capabilities
None — additive layer over the pending `ui-mission-control` capability (unarchived change `ui-mission-control-v2`). Its Animations & Accessibility requirements remain satisfied; the focus-outline scenario is finally fulfilled, not altered.

## Approach

CSS-first, one commit per phase; tokens land before consumers. JS surface limited to one `data-state` write in `setState()` plus string edits — no logic or listener changes.

## Affected Areas

| File | Impact | Description | Est. |
|------|--------|-------------|------|
| `frontend/style.css` | Major | Tokens, focus ring, reduced-motion, reveals, transition hygiene, state visuals, contrast | ~220 |
| `frontend/index.html` | Minor | theme-color meta, h1 swap, aria-label, copy register | ~10 |
| `frontend/app.js` | Minor | `data-state` hook in `setState()`, ellipsis/copy strings | ~15 |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Reduced-motion block kills desired feedback | Medium | Stop motion only; keep state colors/borders |
| `data-state` breaks `.active` consumers | Low | Attribute is additive; `.active` logic untouched |
| Muted-color shift alters subtle-label look | Medium | Visual check sidebar/context labels post-change |
| Reveals jank on slow devices | Low | `animation-fill-mode: backwards`; stagger <600ms |

## Rollback Plan

Frontend-only commits — `git revert` per phase restores prior pixels exactly. No backend/migration coupling. After any revert, confirm ≤768px rendering matches pre-change screenshots.

## Dependencies

None external; builds on completed `mobile-optimization` and `ui-mission-control-v2` layout.

## Success Criteria

- [ ] Tabbing shows a visible cyan focus ring on every interactive element
- [ ] OS reduced-motion ON stops infinite animations but preserves state colors
- [ ] Zero `transition: all` remaining in style.css
- [ ] Mic button visually distinct per idle/listening/processing/speaking via `data-state`
- [ ] `--text-muted` ≥4.5:1 contrast vs `#111111`
- [ ] `<h1>` pixel-identical to former span; `#context-close` has aria-label
- [ ] No ASCII `...` in status strings; no voseo forms in index.html
- [ ] Desktop ≥769px pixel-stable; zero changed lines inside ≤768px media block
- [ ] `python -m pytest tests/ -v` green (backend untouched)
