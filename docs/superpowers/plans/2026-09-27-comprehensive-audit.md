# Comprehensive InterviewTTS Audit — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `AUDITORIA-2026-09-27.md` — a complete technical, functional, visual, and content audit of InterviewTTS, plus a remediation action plan prioritized by technical severity, without modifying product code.

**Architecture:** Seven risk-ordered audit layers (Security → Reliability → Performance/RAG → Architecture → Functional/UX → Visual → Wiki), each executed by a fresh-context reader that records findings into a single accumulating report. Severity is assigned per finding from a fixed rubric, and a final consolidation pass orders the action plan. The audit is read-and-report only: it produces one markdown file, no code changes.

**Tech Stack:** Python 3.10 (venv at `venv\Scripts\python.exe`), FastAPI, pytest, CodeGraph index (`.codegraph/`), Git, ripgrep.

---

## Ground truth established before planning

These facts are verified. Do not re-derive them; build on them.

- **Test baseline:** `250 passed, 8 failed`. The 8 failures are ALL async tests (`tests/test_tts.py` ×4, `tests/test_conversation_memory.py` ×4) failing with "async def functions are not natively supported" — a `pytest-asyncio 1.4.0` / `pytest 9.1.1` incompatibility in the venv, NOT product bugs. The plugin loads and reports `asyncio mode=auto` yet does not collect coroutine tests. This is itself an audit finding (Layer 1 / toolchain).
- **Correct interpreter:** `venv\Scripts\python.exe`. The global python lacks `pydantic` and a stray `langsmith` plugin crashes collection.
- **Run command:** `venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider` (expect 8 async failures; anything else is a new signal).
- **Backend file sizes:** `main.py` 1035, `services/rag.py` 519, `services/llm.py` 407, `services/persistence.py` 411, `services/response_cache.py` 340, `services/semantic_cache.py` 242, `services/candidate.py` 138, `services/report.py` 106, `services/stt.py` 80, `services/tts.py` 72, `config.py` 120, `prompts/candidate.py` 114.
- **Frontend file sizes:** `app.js` 1218, `style.css` 1175, `index.html` 268, `avatar.js` 218.
- **Gitignore facts:** `.env` ignored (line 21); `AUDITORIA-*.md` ignored (line 80) — the report is a local working doc by design; `/wiki/`, `/candidate/`, `RAGraw/`, `reports/`, `audio/` ignored.
- **Deps (backend/requirements.txt):** fastapi, uvicorn, faster-whisper, edge-tts, sentence-transformers, pydub, numpy, python-multipart, httpx, python-dotenv. Dev: pytest, pytest-asyncio, httpx.
- **CodeGraph:** indexed (40 files, 897 nodes, 2022 edges). Use `codegraph_explore` for symbol/flow questions before raw greps.

## Severity rubric (apply to every finding)

| Severity | Test |
|---|---|
| **CRÍTICO** | Security exposure, data loss, or production unavailability. Actively exploitable or will lose data. |
| **ALTO** | Reliability failure under load/concurrency, or silent pipeline degradation. Works in the happy path, fails in production conditions. |
| **MEDIO** | Module-boundary, maintainability, or UX-in-edge-case issue. No immediate outage; slows or degrades future work or a real user path. |
| **BAJO** | Clarity, visual consistency, or polish. No operational or significant UX impact. |

Every finding MUST cite `file:line`, state impact (observed vs. inferred — label it), and give a concrete recommendation. No finding without evidence.

---

## File Structure

- **Created:** `AUDITORIA-2026-09-27.md` — the single deliverable (gitignored by design). Accumulates sections per layer, then a final prioritized action plan.
- **Read-only:** `backend/**`, `frontend/**`, `tests/**`, `wiki/**`, `nginx/**`, `deployment/**`, `scripts/**`, `pyproject.toml`, `backend/requirements.txt`, `.env.example`, `.gitignore`. Never edited.
- **Not touched:** product code, tests, wiki content, config. The audit reports; it does not fix.

---

### Task 1: Establish and record the environment baseline

**Files:**
- Read: `pyproject.toml`, `backend/requirements.txt`, `.env.example`, `.gitignore`
- Create/append: `AUDITORIA-2026-09-27.md` (header + "Baseline verificada" section)

- [ ] **Step 1: Confirm the test baseline reproduces**

Run from repo root:
```powershell
venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
```
Expected: `250 passed, 8 failed` and the 8 failures are the async ones named in Ground truth. If the counts differ, record the actual counts in the report — do NOT force the expected numbers.

- [ ] **Step 2: Record the pytest-asyncio/pytest 9 toolchain break as a finding**

Add to the report under Layer 1 (toolchain integrity) as **ALTO** severity: 8 async tests cannot execute on the installed toolchain, so TTS and conversation-memory async behavior is currently unverified by CI. Evidence: plugin loads, reports `asyncio mode=auto`, yet coroutine tests are not collected. Recommendation: pin compatible versions (e.g. pytest 8.x, or pytest-asyncio latest supporting pytest 9) and re-run.

- [ ] **Step 3: Write the report header and baseline section**

Open `AUDITORIA-2026-09-27.md` with: title, date (2026-09-27), scope statement, the severity rubric, and the verified baseline (test counts, interpreter, codegraph status, file-size table from Ground truth). Keep it factual.

- [ ] **Step 4: Commit the baseline report section**

```powershell
git add -f AUDITORIA-2026-09-27.md
git commit -m "docs(audit): record verified baseline and toolchain finding"
```
Note: `AUDITORIA-*.md` is gitignored, so `-f` is required to track it. If you prefer it untracked, drop the commit and skip Step 4 for every task.

---

### Task 2: Layer 1 — Security and data integrity (CRÍTICO)

**Files (read):** `backend/main.py`, `backend/config.py`, `backend/services/persistence.py`, `backend/services/semantic_cache.py`, `backend/services/response_cache.py`, `backend/services/report.py`, `nginx/interview.conf`, `deployment/interviewtts.service`, `.env.example`, `.gitignore`
**Skill to load first:** `security-and-hardening`

- [ ] **Step 1: Use CodeGraph to map the input → persistence path**

Query: `send_message _audio_extension persistence record_conversation semantic_cache report` to see the full path from HTTP upload to disk and SQLite. This is faster than reading main.py linearly.

- [ ] **Step 2: Audit secret handling**

Check: does `.env.example` contain real-looking values? Does `/api/config` (main.py) leak any secret? Is any API key read into a response? Record any secret that could reach the client as CRÍTICO.

- [ ] **Step 3: Audit the audio upload path for injection/traversal/DoS**

In `send_message` (main.py ~505-533): verify how `ext` is derived from `content_type` (`_audio_extension`), whether a crafted `content_type` can write an arbitrary extension or path, whether `MAX_AUDIO_SIZE` is checked before or after reading the full body, and whether the temp filename (`input_{conversation_id}_{uuid4}{ext}`) is safe. Check the `StaticFiles` mount at `/audio` (main.py ~355) — can a path escape `AUDIO_DIR`? Any CRÍTICO finding here is a strong candidate for the top of the report.

- [ ] **Step 4: Audit the SQLite layer for injection and data loss**

In `services/persistence.py`: grep for f-string/`.format`/`+` SQL construction vs. parameterized `?`/named binds. Any unparameterized interpolation of user-controlled values (conversation_id, turn text) is CRÍTICO. Also check: are deletes/prunes (TTL) guarded? Is there a transaction boundary? What happens on concurrent writes to the same DB from `asyncio.to_thread`?

- [ ] **Step 5: Audit rate limiting, CORS, headers, TLS**

Find the rate limiter in main.py: is it per-IP, does it have a bounded structure, can it be evaded or DoS'd? Check CORS config (is it wildcard? does it allow credentials?). Check `nginx/interview.conf` for TLS, HSTS, and whether `/audio` is served directly.

- [ ] **Step 6: Audit dependencies**

Cross-check `backend/requirements.txt` versions against known issues. Note anything with a minimum-version floor too permissive to guarantee a patched release (e.g. `fastapi>=0.104.0` with no upper bound). Mark unverifiable-without-network items as "inferred."

- [ ] **Step 7: Write Layer 1 findings into the report**

For each finding: title, severity, `file:line` evidence, impact (observed/inferred), recommendation. Order CRÍTICO first. If any CRÍTICO exists, note it prominently at the top of the report.

---

### Task 3: Layer 2 — Reliability and concurrency (ALTO)

**Files (read):** `backend/main.py` (SSE streaming path), `backend/services/llm.py`, `backend/services/tts.py`, `backend/services/stt.py`, `tests/test_api.py`, `tests/test_llm.py`
**Skill to load first:** `code-review-and-quality`, then `debugging-and-error-recovery` for root causes

- [ ] **Step 1: Map the async pipeline with CodeGraph**

Query: `message_stream event_generator llm stream tts synthesize` to get the streaming call path and see every `await` and any synchronous calls that are not wrapped in `asyncio.to_thread`.

- [ ] **Step 2: Audit event-loop blocking**

Find every CPU-bound or blocking call in the request/stream path (Whisper transcribe, embedding, cosine similarity, file I/O, sqlite calls). Confirm each is offloaded via `asyncio.to_thread` (or is genuinely non-blocking). Any blocking call in an `async def` on the hot path is ALTO. Cross-check against the "unblock event loop" fix in commit `977d65b` to see what was fixed and what analogous calls remain.

- [ ] **Step 3: Audit SSE correctness and client cancellation**

In the `event_generator` (~main.py 900-1013): what happens if the client disconnects mid-stream? Are partial tokens/audio handled? Is there a timeout? Does an exception in TTS after tokens started produce a coherent end-of-stream or a truncated silent response?

- [ ] **Step 4: Audit the single-conversation assumption under real concurrency**

The README/RUNBOOK assume one conversation at a time. Test the actual behavior: two concurrent `send_message` calls to different conversation ids. Do they contend on Whisper/embedder (shared, non-reentrant)? On the rate limiter? On the audio dir? Note anything that would corrupt state or crash. This is an ALTO finding if concurrent use is possible (it is — rate limit is per-IP, 10/min, not exclusive).

- [ ] **Step 5: Audit graceful degradation and resource cleanup**

For each of LLM (Google→OpenRouter), TTS (Pocket→Edge), STT (Whisper): what happens on provider failure/timeout? Is there a retry with backoff, or a hard failure? Are temp audio files deleted on both success and failure paths (try/finally)? Does the event loop unblock correctly? Check the periodic-cleanup task and whether it can leak or double-run.

- [ ] **Step 6: Write Layer 2 findings into the report**

Same finding format. Every concurrency/async claim must cite the specific `await`/blocking call. If a claim is an inference (e.g. "these two share a non-reentrant model"), label it inferred and state the reasoning.

---

### Task 4: Layer 3 — Performance and RAG quality (ALTO / MEDIO)

**Files (read):** `backend/services/rag.py`, `backend/services/semantic_cache.py`, `backend/services/response_cache.py`, `backend/services/candidate.py`, `tests/test_rag.py`, `tests/test_semantic_cache.py`, `tests/test_response_cache.py`
**Skills to load first:** `performance-optimization`; for retrieval-quality reasoning, `exploratory-data-analysis` and `scikit-learn` (similarity/threshold analysis)

- [ ] **Step 1: Map the RAG path with CodeGraph**

Query: `RAGPipeline ingest_documents get_chunks_with_scores embed score similarity chunk` to see chunking, embedding, persistence, and scoring.

- [ ] **Step 2: Break down the latency budget by stage**

From the code, identify the stages: STT → RAG retrieve → LLM first token → LLM complete → TTS. For each, note whether timing instrumentation exists and what the dominant cost is expected to be on 4-core CPU. Compare against the README's "8-12s" claim. If instrumentation is absent, mark the budget as unverified and recommend adding per-stage timing (MEDIO).

- [ ] **Step 3: Audit retrieval correctness and cost**

In `rag.py`: chunk size/overlap, how top-k is selected, whether the similarity function is correct (cosine on normalized vs. raw embeddings — a real bug class), and whether the persisted embedding cache is invalidated correctly on document change. Note the cost: is the embedder re-run per request or reused? Is the whole chunk set scored per query (fine for small doc set, note if it wouldn't scale).

- [ ] **Step 4: Analyze the two cache tiers**

- `response_cache.py` (FAQ exact/normalized match): what are the hit conditions, and is the "20 common questions" set actually reachable given real recruiter phrasing?
- `semantic_cache.py` (embedding similarity threshold): what threshold, what's the hit rate, what happens on a false hit (serving a semantically-wrong cached answer is worse than a miss — CRÍTICO/ALTO if unguarded). The threshold constant is the key risk; a too-low threshold yields confidently wrong answers.
Use the tests to understand intended behavior, and reason about the failure mode where a cache hit returns a wrong-but-similar answer.

- [ ] **Step 5: Assess memory footprint**

Estimate steady-state RAM: Whisper model + embedder + shared httpx client + chunk embeddings + in-memory conversations dict. Compare against the 24 GB budget and the "single-conversation" claim. Flag unbounded growth (conversations dict, temp files) as ALTO.

- [ ] **Step 6: Write Layer 3 findings into the report**

Format the latency table, the cache-risk analysis, and the memory estimate. Mark measured vs. estimated clearly.

---

### Task 5: Layer 4 — Architecture and code quality (MEDIO)

**Files (read):** `backend/main.py` (full structure), all `backend/services/*.py`, `backend/config.py`
**Skill to load first:** `code-simplification`, then `code-review-and-quality`

- [ ] **Step 1: Map responsibilities in main.py**

`main.py` is 1035 lines. Use CodeGraph to list its top-level symbols and what each touches. Categorize: HTTP routing, request validation, pipeline orchestration, conversation memory, SSE, static serving, lifecycle. Quantify: how many concerns live in one file, and which service calls are made directly from route handlers vs. through a layer.

- [ ] **Step 2: Identify god objects and coupling**

`main.py` (1035) and `frontend/app.js` (1218) are the two largest units. For each, list the distinct responsibilities and the coupling (does main.py reach into service internals? does it hold business rules that belong in a service?). Propose a target split (e.g. routers per domain, an orchestrator service) as a recommendation — do not implement it.

- [ ] **Step 3: Find duplication and dead code**

Look for: repeated conversation-memory logic, repeated RAG/clean-up patterns, helper functions defined but never called (grep each def), unreachable branches. `grep` for definitions vs. call sites on suspicious helpers.

- [ ] **Step 4: Audit config management**

In `config.py`: are settings read from env with validation? Are defaults sane? Is any config duplicated between `.env`, `config.py`, and `nginx`/systemd? Is anything secret-adjacent logged?

- [ ] **Step 5: Write Layer 4 findings into the report**

Include a proposed module decomposition as a table (current responsibility → target module). Keep it as a recommendation.

---

### Task 6: Layer 5 — Functional and UX (MEDIO)

**Files (read):** `frontend/app.js`, `frontend/index.html`, `backend/main.py` (conversation logic), `backend/prompts/candidate.py`, `tests/test_api.py`
**Skill to load first:** `frontend-ui-engineering`, then `web-design-guidelines` for a11y/UX

- [ ] **Step 1: Trace the recruiter journey through app.js**

Map the user flow: load → create conversation → record → send → stream → play audio → repeat → end. For each state, identify what the user sees (loading, error, disabled controls, transcript display). Note every point where the user can get stuck or unclear feedback.

- [ ] **Step 2: Audit error/empty/edge states in the UI**

When STT fails, LLM fails, or TTS fails, what does the user see? Is there a retry? Silent failure? Check the streaming error handling in `app.js`. Missing user-facing error feedback is a MEDIUM functional finding.

- [ ] **Step 3: Audit conversation-logic edge cases**

In `main.py` conversation handling: farewell detection, VAD timeout, the first-substantive-turn rule, hydration from DB, TTL eviction. Are there states where the conversation desyncs (memory vs. DB)? Reference the relevant tests and note which edge cases are covered vs. untested.

- [ ] **Step 4: Audit accessibility and responsive behavior**

In `index.html`/`style.css`: keyboard operability (can a keyboard-only user record/play?), focus management, ARIA on the status/streaming region, contrast, `prefers-reduced-motion`, and behavior on small screens. Cite specific elements/lines. This feeds Layer 6 too.

- [ ] **Step 5: Write Layer 5 findings into the report**

Format as a user-journey table: stage → expected → actual → severity.

---

### Task 7: Layer 6 — Visual polish (BAJO)

**Files (read):** `frontend/style.css`, `frontend/index.html`, `frontend/avatar.js`, `frontend/app.js` (UI-affecting parts)
**Skills to load first:** `design-taste-frontend`, then `motion-design` for the animation/choreography review

- [ ] **Step 1: Audit design-token consistency**

In `style.css`: are spacing/color/type/radius values tokenized (CSS custom properties) or hard-coded inconsistently? List repeated magic values. Note the motion-token layer from commit `fe113a8` and whether it's applied consistently.

- [ ] **Step 2: Audit motion and accessibility of animation**

Check animation durations/easings for consistency; verify `prefers-reduced-motion` is honored for all non-essential motion. Avatar crossfade and streaming indicator animation are the focus.

- [ ] **Step 3: Audit the avatar and disclaimer overlay**

In `avatar.js` and the overlay added in commit `3f47755`: is the audio-reactive sync robust (state cleanup on stop/error)? Is the experimental disclaimer clear and dismissible, and does it not obscure the primary CTA? Visual findings are BAJO unless they block usability (then they escalate to MEDIO).

- [ ] **Step 4: Write Layer 6 findings into the report**

Group by: tokens, motion, avatar, overlay. All BAJO/MEDIO.

---

### Task 8: Layer 7 — Wiki content audit (MEDIO)

**Files (read):** `AUDITORIA-2026-08-28.md`, `wiki/**` (all `.md`), `scripts/wiki/validate.py`, `scripts/wiki/compile.py`, `backend/services/rag.py` (chunking — does it strip TODO markers?)
**Skill to load first:** `systemic-issue-triage`

- [ ] **Step 1: Re-run the wiki validator for a current signal**

```powershell
venv\Scripts\python.exe scripts/wiki/validate.py --wiki wiki/
```
Record the current error/warning counts. This is the authoritative current state, independent of the August report.

- [ ] **Step 2: Reconcile against the August audit**

For each of the 22 TODO markers and 14 `confidence: medium` pages from `AUDITORIA-2026-08-28.md`: resolved, still open, or changed. Produce a status table. The August report is a local working doc (gitignored) — it should still be on disk to diff against; if it's missing, re-derive from the report's tables quoted in git history or note that reconciliation is partial.

- [ ] **Step 3: Check for new content drift**

Scan `wiki/` for any `[TODO` markers, and any page with `confidence: medium`/`low`, added or edited since 2026-08-28. The working tree has uncommitted changes to 12 `wiki/` files — inspect those specifically, since they may be in-progress resolutions.

- [ ] **Step 4: Verify the RAG does not serve TODO markers as data**

In `rag.py`, confirm whether the chunker filters `[TODO`/placeholder text before embedding. If not, that is a real functional risk (the LLM may quote a TODO verbatim or hallucinate the missing fact) — cite the chunking function. Cross-reference the August plan's P5 item.

- [ ] **Step 5: Write Layer 7 findings into the report**

Include the validator counts, the reconciliation table, and the TODO-filtering verdict.

---

### Task 9: Consolidate, prioritize, and finalize the report

**Files:** `AUDITORIA-2026-09-27.md` (final pass)
**Skill to load first:** `code-review-and-quality` (to sanity-check severity assignment)

- [ ] **Step 1: Normalize every finding to the rubric**

Go through all findings: each has severity, `file:line` evidence, impact labeled observed/inferred, and a recommendation. Re-assign any severity that drifted. Remove any finding without evidence.

- [ ] **Step 2: Build the prioritized action plan**

A single table ordered by severity (CRÍTICO → BAJO), columns: #, action, files, depends-on, effort (S/M/L). Group CRÍTICO items as "do first, before any deploy." For the toolchain break, the first action is fixing the test toolchain (it gates verifying every other fix).

- [ ] **Step 3: Write the executive summary at the top**

3-5 bullets: the single most important risk, the count per severity, and the recommended first action. This is what the user reads first.

- [ ] **Step 4: Final read-through**

Confirm: no placeholder text, no contradictions between layers, every claim cited, the plan is consistent with the findings. Confirm the report is the only file created (no product code touched).

- [ ] **Step 5: Commit the final report**

```powershell
git add -f AUDITORIA-2026-09-27.md
git commit -m "docs(audit): complete comprehensive audit with prioritized action plan"
```

---

## Self-review checklist

- [ ] Every layer (1-7) has findings or an explicit "no findings, here's why" — no layer silently skipped.
- [ ] Every finding has `file:line` evidence and a severity from the rubric.
- [ ] The 8 async test failures are reported as a toolchain finding, not hidden or "fixed" in passing.
- [ ] The action plan's first item unblocks verification (test toolchain) and CRÍTICO items are listed before MEDIO/BAJO.
- [ ] Only `AUDITORIA-2026-09-27.md` was created/modified; no product code, test, or wiki file was changed.
- [ ] Report is self-consistent: summary counts match the per-layer findings.

## Notes for the executor

- Use `codegraph_explore` before grepping — the index is built and the call paths are already mapped.
- This is Windows/PowerShell: invoke the venv python as `venv\Scripts\python.exe`, not `source activate`.
- Prefer fresh-context readers per layer (via subagent-driven-development) so findings in one layer don't bias another.
- If a layer surfaces a CRÍTICO that clearly invalidates later layers' assumptions, pause and surface it to the user before continuing (per the design's execution rule).
- Mark anything you could not verify without network/credentials as "inferred" rather than asserting it.
