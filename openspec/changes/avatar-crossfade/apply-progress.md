# Apply Progress: avatar-crossfade

**Date**: 2026-06-14
**Mode**: Standard (UI change, manual browser verification)
**Apply state**: Ready

## Tasks

- [x] **T1. avatar.js** — Add `setBlend(vol)` function and export in public API
- [x] **T2. style.css** — Replace `.active` class-based opacity with `--avatar-blend` CSS custom property on both video layers
- [x] **T3. app.js** — Call `AvatarOrb.setBlend(ttsVolume)` in visualization loop after TTS volume computation

## Files Changed

| File | Action | Lines Changed | What Was Done |
|------|--------|---------------|---------------|
| `frontend/avatar.js` | Modified | +15 | Added `setBlend()` function (lines 150-162) and wired into public API |
| `frontend/style.css` | Modified | +8 / -2 | Replaced `#avatar-talking-video` opacity from `0` to `var(--avatar-blend, 0.15)`; changed `.active` to same; added `#avatar-neutral-video` with `calc(1 - var(--avatar-blend, 0.15))` |
| `frontend/app.js` | Modified | +5 | Added `AvatarOrb.setBlend(ttsVolume)` call in vis loop (lines 388-391) |
| `frontend/index.html` | Unchanged | 0 | No DOM changes needed |
| `tests/`, `backend/` | Unchanged | 0 | Frontend-only change |

## Pytest Result

**84 passed, 0 failed** — no regression.

## Deviations from Instructions

1. **Did NOT wire `setBlend` into `setVolume`** as the orchestrator suggested. `setVolume(micVolume)` is called per frame with **mic** volume, not TTS volume. Wiring it would drive the video blend from mic level (user's voice) instead of TTS level (AI's voice). Instead, `setBlend` is standalone and called explicitly with `ttsVolume` — cleaner separation of concerns.

## Browser-Test Verification (pending — user to verify)

- [ ] Talking video opacity rises/sinks proportional to TTS volume during speech
- [ ] Neutral video opacity is the complement (sum ≈ 1), no hard cut visible
- [ ] When TTS ends, both videos smoothly return to neutral-leaning blend
- [ ] Orb Three.js animations continue unaffected (color, rings, halo)
- [ ] No ghosting or flicker at transition boundaries

## Risks / Notes

- **CSS specificity**: `#avatar-talking-video.active` (1-ID, 1-class) equals `#avatar-talking-video` (1-ID, 0-class) plus `.active` has higher weight. The `.active` rule now explicitly uses `var(--avatar-blend, 0.15)` so both rules agree — `.active` adds no override.
- **Transition smoothing**: The 250ms `transition: opacity` on both video elements smooths the per-frame (60fps) `--avatar-blend` updates, acting as low-pass filter.
- **Floor at 0.15**: Even at zero audio, neutral video shows at 85% opacity and talking at 15% — subtle ambient presence.
- **Revert**: Remove the 3 new CSS rules, restore original `opacity: 0` and `.active { opacity: 1 }`. Remove `setBlend` from avatar.js and app.js. No side effects.
