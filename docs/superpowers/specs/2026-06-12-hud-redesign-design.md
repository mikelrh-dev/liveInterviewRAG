# HUD Redesign — Voice-First Digital Twin

**Status:** Design approved (pending user review of this document)
**Date:** 2026-06-12
**Author:** Gentle AI orchestrator + user

## Goal

Transform the current minimal voice interview UI into a futuristic, HUD-style
interface (Iron Man / Tron aesthetic) with a 3D pulsating orb as the "digital
twin" avatar, reactive audio visualizations, and a debug panel exposing the
RAG context used for each LLM response.

The interview loop (mic → STT → LLM → TTS) remains unchanged. All changes are
additive or visual on the frontend, with one new backend endpoint for the
context display.

## Scope

### In scope (5 features)

- **A. Real-time waveform** — 32-bar SVG visualizing mic FFT in real time.
- **B. Orbital ring around mic** — SVG ring that rotates faster and changes
  opacity based on listening/processing/speaking state.
- **C. RAG context display** — Collapsible right panel showing the chunks the
  LLM used for the last response, with similarity scores.
- **D. 3D pulsating orb avatar** — Three.js sphere with a custom shader that
  pulses and glows in sync with mic input volume.
- **E. Typing animation** — LLM responses reveal letter-by-letter as the SSE
  stream arrives; audio plays in parallel (no waiting for typing to finish).

### Out of scope (deferred)

- F. Stats panel
- G. Mode switcher (formal/technical/casual)
- H. Interview progress bar
- Real RAG content ingestion (separate work stream; the display works with
  the existing empty RAG index too — will just show "no context retrieved")
- Build system / bundler — keep zero-build, all deps via CDN

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Visual style | HUD / Iron Man | Voice-first app needs constant visual feedback; sci-fi aesthetic matches "digital twin" concept |
| Color palette | Cyan `#00d4ff` on near-black `#0a0e1a` | Professional + tech-forward; less eye strain than amber for long sessions |
| Layout | Hero center (mic + orb in screen middle, conversation above) | Orb is the focal element; conversation is glassmorphism panel above |
| Avatar | 3D pulsating orb (Three.js) | Synced with audio; reinforces "digital twin" concept; only one element needs WebGL |
| 3D stack | Vanilla JS + Three.js (CDN) | Project is zero-build; CDN keeps that property; orbe is the only WebGL element |
| Particles | tsparticles (CDN, ~50KB gzipped) | Standard, well-supported, easy config |
| File structure | `avatar.js` new + modify existing files | Minimal new surface area; no restructuring |

## Architecture

### Files

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/index.html` | Modify | New HUD structure: slim header, conversation panel, hero center with orb+ring+mic, context toggle |
| `frontend/style.css` | Replace | Cyan HUD theme, glassmorphism, animations, particles config, responsive |
| `frontend/app.js` | Modify | Web Audio API analyser setup, orbe sync, waveform, ring state, typing animation, context panel |
| `frontend/avatar.js` | **New** | Three.js scene: sphere geometry, custom shader, mic volume sync, render loop |
| `backend/main.py` | Modify | New endpoint `GET /api/conversation/{id}/context?turn=N`; track `chunks_used` per turn |
| `backend/services/llm.py` | Modify | `generate_stream()` accepts and returns `chunks_used` alongside text |
| `tests/test_api.py` | Modify | New tests for context endpoint (200, 404, empty) |
| `tests/test_llm.py` | Modify | Test that chunks_used is preserved through stream |

### CDN Dependencies

```html
<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/tsparticles@2.12.0/tsparticles.bundle.min.js"></script>
```

## Layout

```
┌─────────────────────────────────────────────┐
│ HEADER: "MIKEL — GEMELO DIGITAL"  [Contexto]│  60px tall
├─────────────────────────────────────────────┤
│                                             │
│   PANEL CONVERSACIÓN (scroll)              │  40vh, glassmorphism
│   [user] Hola                               │
│   [Mikel] Bienvenido...                     │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│            ◯ ORBE 3D pulsante              │  Three.js, 160px
│           ╱│╲                                │
│          ╱ │ ╲ Anillo orbital               │  SVG, 220px diameter
│           ╲│╱                                │
│           [MIC]   ← botón al centro         │  64px, green→red
│                                             │
│           "ESCUCHANDO..."                   │  status text
│                                             │
└─────────────────────────────────────────────┘
```

RAG context panel (feature C) is a 320px wide right-side panel that slides in
from the right when the "🔍 Contexto" button is clicked.

## Visual Components

### Orb (Three.js)

- Sphere geometry, radius 0.8 in scene units (renders ~160px on screen at default camera distance)
- `MeshStandardMaterial` with `emissive: #00d4ff`, `emissiveIntensity` 0.4–1.2 (driven by mic volume)
- Custom point light at scene center, color cyan
- Camera at z=3, looking at origin
- Idle: gentle scale breathing 1.0–1.05 over 3s
- Listening: scale 1.0–1.3 mapped to mic RMS (0–0.5 range)
- Processing: emissive shifts to amber `#fbbf24`, slow rotation
- Target: 60fps; gracefully degrades to 30fps on weak hardware

### Orbital ring (SVG)

- Circular stroke 220px diameter
- `stroke-dasharray` animated via CSS keyframes
- States:
  - Idle: opacity 30%, no rotation
  - Listening: opacity 100%, rotates 360° clockwise in 3s
  - Speaking: rotates 360° counter-clockwise in 4s
  - Processing: `stroke-dashoffset` scanning animation

### Waveform

- SVG `<rect>` x32 bars, 200x40px viewbox
- Each bar height mapped to FFT bin (0–255 range, normalized)
- Updated on `requestAnimationFrame` from `AnalyserNode.getByteFrequencyData()`
- Visible only when state is `listening` or `speaking`
- Color: `url(#wave-gradient)` cyan→transparent

### Particles (tsparticles)

- 60 particles, color `#00d4ff`, opacity 0.15
- Drift upward, ~10px/s
- Connect lines when distance < 100px, stroke 0.5px cyan @ 0.1 opacity
- Click ripple: particles emit outward from click point

### Typing animation (feature E)

- 30ms per character reveal
- Blinking cursor at end (`▌` with CSS animation)
- Audio plays in parallel — typing is purely visual
- Long messages (>200 chars): typing completes before user can re-speak

### RAG context panel (feature C)

- Toggle button in header: "🔍 Contexto"
- Panel slides in from right, 320px wide, glassmorphism
- Content per chunk: pill with `[score] [first 100 chars of text]`
- Click pill: expands to full chunk text + source filename
- Auto-closes 5s after opening, or on outside click

## Data Flow

### Frontend audio (parallel pipelines)

```
getUserMedia({ audio: true })
   │
   ├──→ MediaRecorder → audio chunks → POST /api/conversation/{id}/message
   │                                                          │
   │                                                          ▼
   │                                                    STT (Whisper tiny)
   │                                                          │
   │                                                          ▼ texto
   │                                                    POST /api/chat
   │                                                          │
   │                                                          ▼ SSE stream
   │                                                    text chunks → typing
   │                                                          │
   │                                                          ▼ end event
   │                                                    GET /api/.../context
   │                                                          │
   │                                                          ▼ chunks
   │                                                    context panel render
   │
   └──→ AudioContext.createMediaStreamSource(stream)
              │
              └──→ AnalyserNode (fftSize=64)
                         │
                         ├──→ avatar.js → orbe scale + emissive
                         ├──→ app.js → waveform bars (32)
                         └──→ app.js → ring rotation speed
```

The same `mediaStream` is shared between MediaRecorder and Analyser — no
duplicate permission prompts, no latency from re-acquiring.

### Backend changes

**Conversation state extension:**

```python
conversations[conv_id] = {
    "id": conv_id,
    "messages": [...],          # existing
    "turns": [                  # new
        {
            "n": 0,
            "user_text": "...",
            "assistant_text": "...",
            "chunks_used": [
                {"text": "...", "score": 0.82, "source": "cv.md"},
                ...
            ]
        }
    ],
    "created_at": "..."
}
```

**LLM service change:** `generate_stream()` already takes `context_chunks` as
input. New behavior: also return the same chunks so the endpoint can store
them. Two options:

- **Option X (chosen):** `generate_stream()` returns `(text_chunks, used_chunks)`.
  Endpoint appends to conversation state on stream completion.
- Option Y: pass a callback that the endpoint registers to receive chunks.

**New endpoint:**

```
GET /api/conversation/{conv_id}/context?turn={n}
→ 200 [{text, score, source}, ...]
→ 404 if conversation or turn not found
```

## Error Handling

| Failure | Behavior |
|---------|----------|
| Mic permission denied | Status: "Acceso al micrófono denegado — revisá permisos del navegador"; no orb renders |
| Three.js fails to load | Fallback to CSS radial-gradient orb (animated CSS keyframes); interview continues |
| WebGL not supported | Same CSS fallback; no particles; conversation still works |
| AudioContext blocked (autoplay) | Banner overlay: "Click anywhere to enable audio"; resumes on first click |
| STT fails | Status: "No te entendí — repetí por favor"; same recording, no new question sent |
| LLM fails on Google AI | Fallback to OpenRouter (existing in commit 4657ff8) |
| LLM fails on both providers | Status: "Error generando respuesta"; user can retry |
| TTS fails | Text appears with typing, no audio; interview continues |
| Network error | Status: "Sin conexión — reintentando..." with exponential backoff (1s, 2s, 4s, 8s, max 30s) |
| Context endpoint fails | Panel hidden; no error shown; interview unaffected |

**Design principle:** no HUD feature can break the interview. If every visual
element fails, the original text-only chat must still work.

## Testing

| Layer | Method | Coverage |
|-------|--------|----------|
| Backend (existing) | `pytest tests/ -v` | All 50 existing tests must keep passing; no regression |
| Backend (new) | `pytest tests/test_api.py tests/test_llm.py -v` | 4–5 new tests: context endpoint 200/404/empty; chunks preservation through stream |
| Visual | Manual + screenshots | "Wow" factor; responsive at 360px, 768px, 1280px; motion is smooth |
| Audio reactivity | Manual with Chrome DevTools Audio panel | Orb scales visibly with voice; waveform bars move; ring rotates |
| E2E (optional) | Playwright | Start interview → orb renders → send message → typing animation → audio plays |
| Performance | Chrome DevTools Performance tab | 60fps target with orb + waveform + 60 particles; <5% CPU on M-class hardware |

**Out of automated scope:** "looks beautiful". That is a manual review item.

## Success Criteria

- [ ] All 50 existing tests pass; 4–5 new tests pass
- [ ] HUD loads with no console errors in Chrome/Edge/Firefox
- [ ] Orb, ring, waveform, and particles all render and are animated
- [ ] Orb pulses visibly with voice input (verified manually)
- [ ] Typing animation matches SSE arrival rate; audio plays in parallel
- [ ] RAG context panel shows chunks when RAG index has documents
- [ ] All error scenarios from the table above produce graceful UX, not crashes
- [ ] Performance: 60fps maintained during 5-minute interview simulation
- [ ] No new build step; project remains zero-build
- [ ] Code review: no new dependency added to package.json; only CDN `<script>` tags

## Rollout

Single PR. No feature flag needed (the visual layer is additive; if it fails
to load, the interview still works). Rollback = revert the PR.

## Open Questions

None at design time. All major decisions captured above.

## Future Work (not in this change)

- Real RAG content ingestion (separate task: gather docs, ingest, test retrieval quality)
- Stats panel (F)
- Mode switcher (G)
- Interview progress bar (H)
- Mobile-specific layout tuning beyond basic responsive
- PWA / offline support
