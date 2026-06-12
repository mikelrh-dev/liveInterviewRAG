# Design: Reduce VAD silence timeout from 1200 ms to 800 ms

## Architecture Context

The VAD (Voice Activity Detection) loop is entirely client-side, running in the browser's `requestAnimationFrame` cycle. The flow:

```
startRecording()
  └─→ MediaRecorder(stream) + startVad(stream)
       └─→ AudioContext
            └─→ AnalyserNode (fftSize=256)
                 └─→ vadLoop() ← requestAnimationFrame
                      ├─ calculateRms() → RMS float
                      ├─ RMS ≥ RMS_THRESHOLD (0.03) → reset silence timer
                      └─ RMS < RMS_THRESHOLD after hasSpoken
                           └─ silenceStart ?? Date.now()
                           └─ elapsed ≥ SILENCE_TIMEOUT_MS
                                └─ stopRecording()
                                     └─ processRecordingStream() → POST audio
```

VAD state is local to `frontend/app.js`: `silenceStart`, `hasSpoken`, and the constant `SILENCE_TIMEOUT_MS`. No backend involvement. No network dependency. The timing is pure `Date.now()` delta on each animation frame.

## The Change

| File | Line | Old | New |
|------|------|-----|-----|
| `frontend/app.js` | 21 | `const SILENCE_TIMEOUT_MS = 1200;` | `const SILENCE_TIMEOUT_MS = 800;` |

**One constant, one line.** No other files touched.

## Architecture Decisions

| Option | Trade-off | Decision |
|--------|-----------|----------|
| Change constant only | Fastest; no complexity; instantly reversible | **Adopted** |
| Make it env-configurable (e.g., `window.VAD_TIMEOUT`) | Enables per-session tuning but adds surface area for a borderline need | Deferred — easy to add later if users report issues |
| Adaptive timeout based on speaking cadence | Optimal responsiveness per user; but adds tracking complexity (rolling average, decay, min/max bounds) | Future — documented in Mitigation |

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| User pauses mid-thought (>800 ms) and gets cut off | **Medium in general**, **Low for interview context**. Recruiters speak in full prepared questions; natural pauses between sentences are <500 ms. Risk applies to users who think-while-speaking or have slow, deliberate cadence. | Short clip sent prematurely; conversation may feel slightly interrupted | Constant is easy to tune; revert is 1 line |
| Increased false-positive stops in noisy environments | **Low**. RMS threshold (0.03) still gates whether the timer even starts — `hasSpoken` must be `true` first. 800 ms is well above typical noise-gate chatter. | N/A — noise alone won't trigger stop | No change needed |
| Battery/CPU impact from tighter loop | **Negligible**. The loop is rAF-bound at ~60 fps already; the elapsed check is a `Date.now()` subtraction. | No real difference | None needed |

**Benefit**: 400 ms faster turn completion per utterance. For a 20-turn interview that's ~8 seconds saved, making the conversation feel measurably more responsive.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Manual — console | Verify stopRecording fires at ~800 ms silence | Add `console.log('silence ms:', Date.now() - silenceStart)` in the VAD else-if block (line 168), speak then pause, observe logged values |
| Manual — conversation | Full interview loop; no premature cut-off | Run a mock 10-turn interview, verify each turn completes naturally |

**No automated tests exist** for VAD timing (the project has no E2E/Playwright suite — config.yaml confirms `e2e: false`). The change is simple enough that manual verification with a log line is sufficient.

## Rollback

```diff
- const SILENCE_TIMEOUT_MS = 800;
+ const SILENCE_TIMEOUT_MS = 1200;
```

Revert `frontend/app.js:21`, reload the page. Done in 5 seconds.

## Future Improvement (documented)

An **adaptive silence timeout** could track the user's rolling-average pause duration across recent utterances and adjust the threshold per-turn. Implementation sketch:

```
trackedPauses[] — sliding window of last 5 silence durations
adaptiveTimeout = max(500, rollingAverage * 1.5)
```

This would handle both fast and slow speakers without a hard trade-off. Not needed now — the risk is low — but the constant at the top of `app.js` is ready for any future logic to write to it.
