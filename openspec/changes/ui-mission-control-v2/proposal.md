# Proposal: UI Mission Control V2

## Intent

Single-column HUD is fragile: avatar dominates viewport, conversation cramped at 40vh, system status invisible, RAG panel hidden. Replace with a 3-zone layout (sidebar + main + context panel) surfacing system health, live mic levels, and RAG context at all times. State machine and avatar logic stay intact.

## Scope

**In**: CSS tokens + grid bg + vignette + z-index; sidebar (240px) with session info, system status, model indicators, VU meter, end-session; header ("MIKEL OS", online pill, latency); context panel (320px) always visible with search; conversation above avatar (380×380, left) + mic + text input fallback; timestamps; Inter + JetBrains Mono; responsive (1200/1024/768); remove particles, scan lines, holographic filter, redundant pseudo-elements; 3 chained PRs.

**Out**: Backend; `avatar.js` / `app.js` state machine; holographic effects; dark/light toggle; Stitch (separate step).

## Capabilities

**New**: `ui-mission-control` — layout, sidebar, header, VU meter, responsive, visual effects.

**Modified**: None.

## Approach

1. **Stitch**: New project, one master screen using cyan tokens + `#050b1a` bg + Inter. Visual ref only.
2. **PR #1** (~400 lines): CSS vars, grid bg, vignette, z-index, layout grid, HTML skeleton.
3. **PR #2** (~400 lines): Sidebar, header, context panel, conversation, avatar (380×380), controls, text input, timestamps, responsive.
4. **PR #3** (~400 lines): Animations (pulse, ticker, hover glow, state transitions), VU meter, bubble avatars, verify IDs, backend tests.

## Affected Areas

| File | Impact |
|------|--------|
| `frontend/index.html` | Rewrite ~80% |
| `frontend/style.css` | Rewrite ~90% |
| `frontend/app.js` | Modify ~15% (DOM helpers, resize, VU binding) |

## Risks

| Risk | Mitigation |
|------|------------|
| `app.js` DOM selectors break | Keep 6 critical IDs; test per PR |
| Responsive 3-zone collapse | Progressive breakpoints in PR #2 |
| Scope 3× review budget | Chained PRs, independently verifiable |
| Three.js canvas misposition | Keep canvas in `.portal-ring`; call `AvatarOrb.resize(380,380)` |

## Rollback

Each PR revertible via `git revert`. Full revert `git checkout <original>`. Backend and avatar logic unchanged.

## Dependencies

- Stitch design (new project, one master screen) — blocks PR #1
- Inter + JetBrains Mono via Google Fonts CDN
- Verify `AvatarOrb.resize(380,380)` exists before PR #2

## Success Criteria

- [ ] 3-zone layout renders at ≥1200px: sidebar 240px + main + context 320px
- [ ] 6 critical DOM IDs survive (`#orb-canvas`, `#conversation`, `#btn-mic`, `#status`, `#avatar-neutral-video`, `#avatar-talking-video`)
- [ ] VU meter shows live RMS from `analyserNode`
- [ ] Layout collapses at 1024px, 768px, <768px
- [ ] 57 backend tests pass unchanged
- [ ] Avatar 380×380 with Three.js rings and video crossfade working
- [ ] Context panel always visible on desktop with chunk cards
- [ ] End-session button calls `stopInterview()`
