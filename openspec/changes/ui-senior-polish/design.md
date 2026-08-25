# Design: UI Senior Polish (Mission Control)

## Technical Approach

Additive CSS-first polish layer over the existing vanilla frontend. Motion tokens land in `:root` before consumers; one JS attribute write (`data-state`) drives state visuals; all other changes are CSS/HTML/text edits. No backend, listener, or logic changes. Affected service module: **none** (frontend-only; FastAPI serves statics untouched).

## Architecture Decisions

### D1 — Motion tokens
**Choice**: Extend existing `:root` block (style.css:5–62), appended after the Z-index map (~line 61): `--dur-fast:150ms; --dur-base:250ms; --dur-slow:400ms; --ease-out:cubic-bezier(0.16,1,0.3,1); --ease-spring:cubic-bezier(0.34,1.56,0.64,1);` plus `color-scheme: dark;`.
**Rationale**: Single token source; consumers reference vars so later tuning is one-line. Rejected: new `@layer`/file split — overkill for 978-line sheet.

### D2 — `transition: all` inventory (grep-verified, exactly 6)
| Line | Selector | Replacement |
|------|----------|-------------|
| 230 | `#context-toggle` | `border-color, color, transform` · fast/ease-out |
| 297 | `.sidebar-end-btn` | `background-color` · fast/ease-out |
| 507 | `#btn-mic` | `border-color, background-color` fast/ease-out + `transform` fast/**spring** (hover scale 1.05 pops) |
| 794 | `#context-close` | `border-color, color, transform` · fast/ease-out |
| 829 | `.chunk-pill` | `border-color, background-color, transform` · fast/ease-out |
| 962 | `#mobile-end-btn` *(inside ≤768px block)* | `background-color` · fast/ease-out |

Duration normalization: current 0.2s → `var(--dur-fast)` (150ms) for micro-feedback. Hover lift: `transform: translateY(-1px)` added on toggle/close/chunk-pill hovers; **excluded on END buttons** (destructive actions shouldn't invite interaction).

### D3 — Reduced motion
**Choice**: One universal block at EOF:
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```
**Alternatives**: targeted `animation:none` per consumer. **Rationale**: iteration-count 1 collapses infinites to final frame instantly (pulse-dot ends opacity 1/scale 1 → dot solid; pulse-red ends transparent shadow → ring gone, red border stays). Covers the 6 named consumers **plus `blink` (line 721, typing-cursor)** — a 7th infinite animation the proposal missed — and future-proofs. Colors/borders survive; only motion dies.

### D4 — Focus-visible
**Choice**: Global `*:focus-visible { outline: 2px solid var(--cyan-accent); outline-offset: 2px; }`. Verified via grep: **no `outline` or focus rule exists today** — nothing to remove; mouse clicks stay clean because `:focus-visible` ≠ `:focus`.

### D5 — Load reveals (pure CSS, zero JS)
```css
@keyframes fadeUp { from { opacity:0; transform:translateY(12px);} to { opacity:1; transform:translateY(0);} }
header          { animation: fadeUp var(--dur-slow) var(--ease-out) 0ms   backwards; }
#avatar-wrapper { animation: fadeUp var(--dur-slow) var(--ease-out) 80ms  backwards; }
#btn-mic        { animation: fadeUp var(--dur-slow) var(--ease-out) 160ms backwards; }
#status         { animation: fadeUp var(--dur-slow) var(--ease-out) 240ms backwards; }
```
No FOUC: styles apply at parse; `backwards` holds the hidden from-state during delays (last delay 240ms < 600ms budget). **Critical gotcha**: `both/forwards` would pin `transform` forever, permanently deadlocking `#btn-mic` hover scale — hence `backwards` everywhere (end state ≡ natural styles).

### D6 — Mic state visuals (`body[data-state]`)
**Hook**: first statement of `setState()` (app.js:503): `document.body.dataset.state = state;`. Body-level chosen (per recommendation) so any element can react via descendant selectors.
**Finding that shapes this**: `.active` toggles around the *whole interview* (added :579, removed :596) — **listening keeps `.active` red REC pulse**. Rules added:
```css
body[data-state="processing"] #btn-mic:disabled { border-color:var(--amber); color:var(--amber); background:rgba(245,158,11,.08); }
body[data-state="speaking"] #btn-mic.active    { border-color:var(--green); color:var(--green); background:rgba(34,197,94,.12); animation:none; box-shadow:none; }
#status { transition: color var(--dur-fast) var(--ease-out); }
```
Specificity `(1,2,1)` beats `#btn-mic.active` `(1,1,0)`. Idle/listening need **no new rule** (defaults/red pulse stand). Status text keeps existing class-based colors (:559–569).

### D7 — Drawer easing
style.css:**915** (not ~906): `transition: transform 0.3s ease` → `transition: transform var(--dur-base) var(--ease-spring);` inside existing mobile block. Desktop panel untouched (static grid column, no transform). Spring overshoot on `translateX(-100%)` = brief settle bounce; accepted aesthetic.

### D8 — Contrast fix
`--text-muted: #556688` → `#7e90b3`. Math (WCAG relative luminance): old L=0.1321 → **3.28:1 FAIL**; new L=0.2763 → (0.3263)/(0.0556) = **5.87:1 PASS** vs `#111111`. Consumers `.sidebar-label` (:275) and `.vu-bar` (:338).

### D9/D10 — HTML & copy edits
| File:Line | Edit |
|-----------|------|
| index.html: after 5 | `<meta name="theme-color" content="#131313">` |
| index.html:71 | `<span class="header-title">` → `<h1 class="header-title">` — reset `*{margin:0}` + `.header-title` font rules make pixels identical; flex-item blockification no-op |
| index.html:219 | `aria-label="Cerrar panel de contexto"` on `#context-close` |
| index.html:72 (rule) | append `-webkit-tap-highlight-color: transparent;` to `html,body` |
| index.html:57 | "Hacé click" → "Haz clic" |
| index.html:153 | "Presioná" → "Presiona" |
| app.js:288 | welcome "Presioná" → "Presiona" |
| app.js:673, 718, 783, 855, 868 | `"..."` → `"…"` (Escuchando/Procesando/Reproduciendo/reintentando/Enviando) |
| app.js:1076 | truncation literal `"..."` → `"…"` |
| app.js:648, 678 | voseo "usá"/"revisá" → "usa"/"revisa" |

## Data Flow

```
user action ──> setState(state) ──> body.dataset.state ──> CSS [data-state] rules ──> mic/status visuals
                     └──> existing class/AV/orb updates (untouched)
```

## File Changes

| File | Action | Est. lines |
|------|--------|------------|
| `frontend/style.css` | Modify | ~55 (≈47 add, 8 swap) |
| `frontend/index.html` | Modify | ~5 |
| `frontend/app.js` | Modify | ~10 |
| **Total** | | **~70 ≪ 400 budget — single PR** |

## Interfaces / Contracts

New DOM contract: `<body data-state="idle|listening|processing|speaking">`, written exclusively by `setState()`; additive — no reader today, CSS-only consumers.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Backend pytest | Regression only | `python -m pytest tests/ -v` must stay **green untouched** — zero backend lines changed; no new tests required (**explicit design decision**: all behaviors are CSS/visual or one attribute write, outside pytest's reach; JS change covered by manual matrix) |
| Manual matrix | Maps to spec scenarios | ① Tab-through → cyan ring every interactive el, no ring on click ② DevTools reduced-motion → animations frozen, colors intact ③ Reload → staggered header→avatar→mic→status reveal ④ Mic cycle → red-pulse listening / amber disabled processing / green speaking / neutral idle ⑤ Mobile drawer spring slide ⑥ Sidebar labels readable post-recolor ⑦ Desktop ≥769px screenshot-diff stable |

## Migration / Rollout

None. Rollback = `git revert` restoring style.css, index.html, app.js; zero backend/env/data coupling.

## Deviations from Proposal

1. **Two lines inside ≤768px block change** (drawer :915 — orchestrator-mandated; mobile-end-btn :962 — required for the verifiable "zero `transition:all`" criterion). Mechanical property swaps, same timing feel; verified against pre-change screenshots.
2. **Listening mic stays red** (proposal said "listening cyan"): `.active` REC pulse spans the interview; cyan would erase the recording affordance (= removal). Cyan remains on ring/status.
3. Reduced-motion covers 7th keyframe `blink`; extra voseo fixes app.js:648/678 for register consistency; hover lift excludes destructive END buttons.

## Open Questions

- None blocking. Backend `welcome_message` may contain voseo — out of scope (backend frozen), noted for a future change.
