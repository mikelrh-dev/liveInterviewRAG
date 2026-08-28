# Proposal: Disclaimer Overlay — "Mikel OS" honesty gate

## Intent

InterviewTTS presents a digital twin that answers as if it were Mikel. Because a visitor may mistake the twin for the real person, or trust an AI answer as fact, the app should clearly state its nature **before first use**. This both protects the project and raises perceived quality: a product that honestly names its limits (experimental, LLM can hallucinate, does not substitute a real interview) feels more trustworthy, not less.

## Problem

- A first-time visitor can start a voice interview with no indication that the responses are AI-generated.
- Untested, the twin's occasional hallucination or imprecision could be read as a claim made by Mikel himself.
- No informed-consent boundary exists between the visitor and the AI persona.

## Solution

A client-side full-screen overlay, shown on first visit, that:

1. States the twin is an **experimental prototype**.
2. States answers come from an **RAG + LLM pipeline that can hallucinate**.
3. States it **does not substitute a real interview with Mikel**.
4. Requires the visitor to click **"Entiendo, continuar"** before the interview controls unlock.
5. Remembers the acknowledgment in `localStorage` so returning visitors are not nagged, keeping the recruiter experience clean.
6. Matches the existing "Mikel OS / Mission Control" styling by reusing the existing `.overlay` pattern and CSS design tokens.

## Scope

### In Scope
- `frontend/index.html`: new overlay markup reusing `.overlay` class, plus a small persisted reminder note in the status/footer area.
- `frontend/style.css`: `.overlay--disclaimer` variant with a centered card, existing colors/fonts, accept button.
- `frontend/app.js`: on boot, check `localStorage`; show overlay and gate mic unlock if not accepted; on accept, persist, hide, and enable controls.
- OpenSpec: this proposal + spec (`openspec/changes/disclaimer-overlay/`).

### Out of Scope
- Backend changes: zero changes to FastAPI / services / tests.
- Formal legal or GDPR text: this is an informational honesty disclaimer, not a personal-data consent flow.
- Server-side acknowledgment tracking.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `frontend/index.html` | Modified (additive) | Overlay markup + optional persistent fine-print line |
| `frontend/style.css` | Modified (additive) | Disclaimer overlay variant reusing existing tokens |
| `frontend/app.js` | Modified | Boot gating + local storage acknowledgment |
| OpenSpec `changes/disclaimer-overlay/` | New | Proposal + spec |

## Approach

1. Add the overlay container to `index.html` right after the existing `audio-blocked-overlay` (same `.overlay` pattern), wrapping a `.disclaimer-card` with the copy and a `#disclaimer-accept` button.
2. Add `.overlay--disclaimer` styles in `style.css` using existing vars (`--surface-container`, `--cyan-accent`, Inter/Sora fonts, `--z-overlay` tier with a higher variant).
3. In `app.js` `init()`: read `localStorage` key `interviewtts.disclaimerAccepted`. If absent, add `.visible` state; hide mic-enable until `#disclaimer-accept` is clicked. On accept: set the key, hide, and proceed to normal startup (the existing audio-blocked flow can then take over if the browser blocks audio).

Copy (approved direction, to be finalized in implementation):
> Este es un prototipo experimental. Las respuestas las genera una IA (RAG + modelo de lenguaje) que puede cometer errores o alucinaciones. No sustituye una entrevista real con Mikel.

## Rollback Plan

Reversible with minimal risk — purely additive frontend changes:

1. Revert `app.js` gating block and the two markup/style additions.
2. Delete `openspec/changes/disclaimer-overlay/`.
3. No backend/database impact; nothing to migrate.

## Success Criteria

- [ ] On a clean browser (no stored flag), the overlay appears before the mic is enabled.
- [ ] The copy names: experimental prototype, possible LLM hallucination, and "not a substitute for a real interview".
- [ ] Clicking "Entiendo, continuar" hides the overlay and enables the controls.
- [ ] Reloading the page with the stored flag does not show the overlay (and the mic works).
- [ ] The overlay renders above the audio-blocked overlay and uses the project's existing styling (no generic-cookie-banner look).
- [ ] Zero backend changes; `python -m pytest tests/ -v` still passes.