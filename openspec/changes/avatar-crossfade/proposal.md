# Proposal: Avatar Audio-Reactive Crossfade

## Intent

The neutral↔talking video switch is a hard class toggle (`.active`), causing an unrealistic binary cut. The CSS `transition: opacity 0.25s` softens the edge, but there's no relationship between audio amplitude and the blend — the talking video snaps to full opacity regardless of how loud/quiet the TTS is. This feels robotic: no breathing, no subtle movement with the voice.

Fix: drive both video opacities continuously from TTS audio volume via a CSS custom property. Result: the avatar "breathes with the voice" — louder = more talking visible, quieter = more neutral visible. No event-based switch visible.

## Scope

**In**: CSS custom property `--avatar-blend` on portal-ring; new `setBlend(vol)` on `AvatarOrb` public API; wire `ttsVolume` (already computed per frame) to drive blend; keep existing transition for smooth interpolation; remove or deprecate `.active` class for talking video opacity.

**Out**: 3D avatar, Wav2Lip, mouth sync, new state videos (listening/thinking), state machine refactor, backend changes, new tests (manual browser verify only).

## Capabilities

**New**: None — pure UI enhancement, no new spec-level behavior.

**Modified**: None — existing specs (candidate-profile, conversation-engine, rag-pipeline) unchanged.

## Approach

```
TTS audio → AnalyserNode → ttsVolume (0-1 per frame, already computed)
                                  ↓
                          AvatarOrb.setBlend(vol)
                                  ↓
                      --avatar-blend CSS custom property
                                  ↓
              avatar-talking opacity = blend (via CSS var)
              avatar-neutral opacity = 1 - blend (via CSS var)
```

CSS `transition: opacity 0.2s` already exists on both video elements — acts as a low-pass filter on the per-frame volume signal.

## Affected Areas

| Area | Impact | Lines |
|------|--------|-------|
| `frontend/avatar.js` | **Modified** — add `setBlend()` to public API | +8 |
| `frontend/style.css` | **Modified** — replace `.active` class opacity with `--avatar-blend` rule | ±10 |
| `frontend/app.js` | **Modified** — call `AvatarOrb.setBlend(ttsVolume)` in vis loop | +1 |
| `frontend/index.html` | **Unchanged** — DOM IDs stable | 0 |
| `backend/`, `tests/` | **Unchanged** | 0 |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Both videos ghost simultaneously if opacity sum ≠ 1 | Low | CSS rule enforces `neutral + talking = 1` via `calc(1 - var(--blend))` |
| Low TTS volume never triggers visible blend | Low | Normalize `ttsVolume` range, apply floor (min blend 0.15 for subtle ambient movement) |
| 84 backend tests break | Very Low | Frontend-only change; run pytest after to confirm |

## Rollback

`git revert` both commits (avatar.js + style.css + app.js), or reset the 3 files to HEAD. Videos return to prior `.active` class toggle.

## Dependencies

None. All signals exist: `ttsVolume` computed in `startVisualizationLoop()`, CSS transitions in place. No new packages, CDNs, or backend deps.

## Success Criteria

- [ ] Talking video opacity rises/sinks proportional to TTS volume during speech
- [ ] Neutral video opacity is the complement (sum ≈ 1), no hard cut visible
- [ ] When TTS ends, both videos smoothly return to neutral-leaning blend
- [ ] Orb Three.js animations continue unaffected (color, rings, halo)
- [ ] All 84 backend tests pass unchanged
- [ ] Manual browser test: load page, send a message, observe smooth blend

## Open Questions

1. **Crossfade duration**: 200ms (snappy, current) or longer (more dreamy)?
2. **Blend curve**: linear proportional to volume, or eased (ease-in-out feels more organic)?
3. **Volume floor**: minimum blend at very quiet audio (subtle ambient movement), or only above a threshold?
4. **Scale bob**: opacity only, or a subtle 1.0→1.02 scale on the talking video driven by TTS?
