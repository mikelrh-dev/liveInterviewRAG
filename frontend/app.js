/**
 * InterviewTTS — HUD frontend
 * Voice interview loop with HUD visualizations.
 *
 * Architecture:
 * - Shared AudioContext + AnalyserNode (shared between MediaRecorder and visualizations)
 * - State machine for orb/ring: idle | listening | speaking | processing
 * - Waveform: 32 bars from FFT
 * - Typing animation: 30ms per char reveal
 * - Context panel: fetches from GET /api/conversation/{id}/context?turn=N
 */

const API_BASE = "";
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;
let isInterviewActive = false;
let isUserScrolledUp = false;

// ─── Shared Audio setup ────────────────────────────────

let audioContext = null;
let analyserNode = null;
let mediaStream = null;
// Current MediaStream → analyser source. Reused across startRecording() calls
// for the same stream; re-created (old one disconnected) when the stream changes.
let micSourceNode = null;

let selectedMimeType = "";

function chooseMimeType() {
    const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4;codecs=mp4a.40.2",
        "audio/mp4",
    ];
    for (const mime of candidates) {
        if (MediaRecorder.isTypeSupported(mime)) {
            selectedMimeType = mime;
            return;
        }
    }
    // No supported codec — leave selectedMimeType empty so callers can
    // surface a browser-compatibility error instead of a permission one.
    selectedMimeType = "";
}

async function ensureAudioContext() {
    if (audioContext && audioContext.state === "suspended") {
        await audioContext.resume();
    }
}

// TTS output analyser — drives fake-sync of talking video
let ttsAnalyser = null;
let ttsVolumeBuffer = null;
// The mic analyser's time-domain buffer, hoisted out of the animation loop for
// the same reason ttsVolumeBuffer is: it is overwritten before it is read, so
// reallocating it per frame is pure garbage.
let micTimeBuffer = null;
// The last volume handed to the orb, so an unchanged one is not restated.
let lastBlendVolume = null;

// VAD state (uses same analyser)
let vadAnimationId = null;
let silenceStart = null;
let hasSpoken = false;
// The room's own noise level, in RMS, as an exponential minimum. See
// `updateNoiseFloor` for the algorithm and `loudPeakRms` for why estimating it
// is only half the problem.
let noiseFloor = 0;
// The loudest RMS this turn has seen while it still counted as speech.
//
// A pause is a DROP, not a level, and this is what makes the difference
// decidable: a frame only counts as silence if something louder came before it.
// Without that, a candidate who talks for several seconds without pausing lets
// the noise floor climb to their own voice -- every tracker that converges to
// the signal does, that is what convergence means -- and from then on their
// voice reads as silence and the turn is cut mid-word.
//
// A running maximum with no decay, deliberately. It only ever makes the
// detector MORE willing to end a turn, never less, so the failure it could
// introduce (an early loud word pinning the bar high) costs a slightly later
// cut, not a wrong one. It is reset per recording, so a previous turn's shout
// cannot leave the next one unendable.
let loudPeakRms = 0;
// The recording cap's own state. Separate from the VAD's on purpose: the VAD
// answers "has the speaker stopped?", this answers "has this recording had
// enough?", and the second must still work in a room that never goes quiet.
let recordingCapTimer = null;
let recordedBytes = 0;
let recordingCapped = false;
const SILENCE_TIMEOUT_MS = 1200;
// The absolute floor on the cut threshold, in RMS.
//
// It is a floor and not the threshold: a fixed number cannot be both above a
// quiet room and below a loud one, and the code that had only this is what made
// a noisy room unendable. It survives as the LOWER bound on the relative
// threshold below, so a silent room behaves exactly as it always did -- its own
// noise floor collapses towards zero, the relative term never wins, and the
// threshold is this same number.
const RMS_THRESHOLD = 0.015;
// How far above the estimated noise floor counts as speech.
//
// 2x is 6 dB of signal-to-noise, which is a low bar on purpose: this only has
// to separate the room from someone talking in it, and a bar set high enough to
// be comfortable would sit inside the quietest speech in a loud room -- cutting
// candidates off because they spoke softly. Too LOW is the safe direction: the
// recording is bounded by maxRecordingMs whatever the VAD decides, so a
// threshold that lingers costs a longer answer, never a lost one.
const NOISE_FLOOR_MULTIPLE = 2;
// How fast the floor may RISE towards the signal, as a fraction of the gap per
// frame.
//
// Slow, so a word opening does not become the room. 0.02 is a ~0.8 s time
// constant at 60 fps, so a room at four times the absolute threshold is
// characterised in about a second -- fast enough that a pause inside it is seen,
// slow enough that speech is not.
const NOISE_FLOOR_RISE_RATE = 0.02;
// How fast the floor may FALL, as a fraction of the gap per frame.
//
// Four times the rise rate, and asymmetric on purpose. Coming down has to be
// quick or a door closing leaves the page deaf for the rest of the interview: a
// door is the loudest silence in a building, and the frame after it is exactly
// when the floor must already have moved. 0.08 is a ~0.2 s time constant, so a
// single pause is enough to re-characterise a room.
const NOISE_FLOOR_FALL_RATE = 0.08;

// ─── The recording ceiling ───────────────────────────────────────────────────
//
// WHY THIS EXISTS
// A recording has to end for a reason, and there are exactly two. The VAD finds
// the end of an ordinary turn -- the candidate stops talking -- and this bounds
// the ones it cannot. The second case is not hypothetical: with an absolute
// silence threshold, a room with background noise never reads as silent, the
// recorder keeps running, the blob grows to `MAX_AUDIO_SIZE`, the proxy answers
// 413, 413 is not retryable, and `stopInterview()` throws away the interview
// and the whole recording.
//
// WHERE THE NUMBER COMES FROM
// `MAX_AUDIO_DURATION` in backend/config.py, published by GET /api/config and
// applied here. One number, not two: a page-side limit that disagreed with the
// server's would cut a recording in one deployment and refuse it in the next.
//
// The constant below is the FALLBACK, and it is not decoration. `init()` fires
// the sidebar fetch without awaiting it, so a deployment that is slow, down or
// erroring still has to bound the recording -- and the first recording of a
// session is exactly the one that needs the cap. It is a copy of the server's
// default because a fallback that disagreed with the server would be a second
// limit, and `tests/test_recording_duration_limit.py` fails if the two drift.
const MAX_RECORDING_MS = 60000;
// The limit actually enforced. Starts at the fallback and is replaced by the
// server's value as soon as /api/config answers.
let maxRecordingMs = MAX_RECORDING_MS;
// Asked of MediaRecorder so `dataavailable` fires while recording instead of
// only at stop. Without it the size of a recording is unobservable until the
// moment it is already too late, which is how a blob reaches 5 MiB unnoticed.
const RECORDING_TIMESLICE_MS = 1000;

// Visualization state
let currentState = "idle"; // idle | listening | speaking | processing
let waveformBars = [];
let waveformAnimationId = null;

// DOM
const btnMic = document.getElementById("btn-mic");
const statusEl = document.getElementById("status");
const conversation = document.getElementById("conversation");
const micIcon = btnMic.querySelector(".mic-icon");
const stopIconEl = btnMic.querySelector(".stop-icon");
const orbitalRing = document.getElementById("orbital-ring");
const waveformSvg = document.getElementById("waveform");
const contextToggle = document.getElementById("context-toggle");
const contextPanel = document.getElementById("context-panel");
const contextClose = document.getElementById("context-close");
const contextContent = document.getElementById("context-content");
const audioOverlay = document.getElementById("audio-blocked-overlay");
const disclaimerOverlay = document.getElementById("disclaimer-overlay");
const disclaimerAccept = document.getElementById("disclaimer-accept");

const avatarNeutralVideo = document.getElementById("avatar-neutral-video");
const avatarTalkingVideo = document.getElementById("avatar-talking-video");

// Current candidate message
let currentCandidateDiv = null;

/**
 * What the chips in the Context panel actually are.
 *
 * `grounded` is the normal case: the RAG retrieved those passages and the model
 * wrote the answer from them. `related` is the FAQ cache hit, where the answer
 * is a fixed string from `response_cache.py` and the retrieval existed only to
 * give the panel something to draw. Both spellings are produced by
 * `backend/conversation.py` (`GROUNDED` / `RELATED`).
 *
 * `createTurnState` and `renderContext` compare against the bare strings rather
 * than reading this object, and that is not an oversight: tests/frontend lifts a
 * function out of this file by name and evaluates it in isolation, so a lifted
 * function cannot see a module-level binding. The drift that duplication could
 * cause is pinned by
 * tests/frontend/cache_hit_grounding.test.mjs, which compares these two strings
 * against the ones the server actually puts on the wire.
 *
 * See tests/frontend/cache_hit_grounding.test.mjs.
 */
const GROUNDING = {
    GROUNDED: "grounded",
    RELATED: "related",
};

// Audio queue
let audioQueue = [];
let nextChunkId = 0;
let skippedChunkIds = new Set();
let isAudioPlaying = false;
let allChunksReceived = false;
// The chunk currently making noise. Held because `tryPlayNextChunk` kept it in
// a local, which is why nothing outside could stop it: END had no way to name
// the element that was still talking.
let currentAudio = null;
// The turn's read loop, so END can cancel it. Paired with `turnAborted`, which
// says the cancellation was deliberate -- see abortTurnStream().
let turnAbortController = null;
let turnAborted = false;
// The narrator driving the status line for the turn in flight. Defaults to a
// no-op so the playback teardown can fire outside a turn without a guard.
let turnNarrator = createTurnNarrator();

// Typing animation
// ─── Sidebar data population ─────────────────────────────

let sessionStartTime = null;
// The session timer's interval. Kept so re-arming replaces the writer instead
// of adding to it, and so there is exactly one of them at a time.
let sessionTimerId = null;

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

/**
 * The header latency pill, measured for real.
 *
 * This rail used to ship the literal string "LATENCY 12ms" — a number no code
 * produced, sitting directly beside model names that are genuinely fetched from
 * GET /api/config. A reader had no way to tell instrumentation from props, and
 * for a portfolio piece that is the one thing a reviewer will actually check.
 *
 * Every figure here is timed in this file with performance.now() around a real
 * /message/stream request. Two of them, because they are two different claims:
 *
 *   TTFB — request sent -> first byte of the response body. Transport plus
 *          any queueing before the server starts answering.
 *   TTFT — request sent -> the first `token` SSE event, which is the whole
 *          pipeline: upload, Whisper STT, RAG retrieval, first LLM token.
 *
 * TTFT is the headline because it is the one a candidate actually feels, and
 * perceived responsiveness is this product's entire thesis.
 *
 * The three states are the point. "Measuring" shows no number at all, because a
 * previous turn's figure left on screen during a new turn is the same
 * fabrication in a subtler costume. Only `ttft()` being non-null may render
 * digits.
 */
function createLatencyReadout(el, now) {
    const clock = typeof now === "function" ? now : () => performance.now();

    // null means "not measured" for all three. It is never 0 and never a
    // placeholder, so a zero-length turn cannot masquerade as a fast one.
    let startMs = null;
    let ttfbMs = null;
    let ttftMs = null;

    function render() {
        if (!el) return;
        if (ttftMs !== null) {
            el.textContent = "TTFT " + (ttftMs / 1000).toFixed(2) + "s";
        } else if (startMs !== null) {
            el.textContent = "TTFT midiendo…";
        } else {
            el.textContent = "TTFT —";
        }
    }

    return {
        /** Arm the stopwatch for a new turn. Clears the previous turn's value. */
        begin() {
            startMs = clock();
            ttfbMs = null;
            ttftMs = null;
            render();
        },
        firstByte() {
            if (startMs === null || ttfbMs !== null) return;
            ttfbMs = clock() - startMs;
        },
        firstToken() {
            if (startMs === null || ttftMs !== null) return;
            ttftMs = clock() - startMs;
            render();
        },
        /**
         * The turn finished. Stop the stopwatch but KEEP the result, so a
         * completed turn's real figure stays readable.
         *
         * This also guarantees the pill always leaves the measuring state: an
         * empty transcription answers `error` + `done` with no `token` event
         * at all, and without this the pill would sit on "midiendo…" for the
         * rest of the session — a loading state that can never resolve.
         */
        settle() {
            startMs = null;
            render();
        },
        /**
         * The turn failed before producing a token — a rejected upload, a dead
         * stream. Drop the measurement: a number we never finished taking is
         * not a result, and leaving it up would date the turn.
         */
        abandon() {
            startMs = null;
            ttfbMs = null;
            ttftMs = null;
            render();
        },
        ttfb() {
            return ttfbMs;
        },
        ttft() {
            return ttftMs;
        },
    };
}

const latencyReadout = createLatencyReadout(
    document.getElementById("latency"),
);

/**
 * The SISTEMA rail, driven by GET /api/health.
 *
 * It used to read a literal "All Systems Online": a claim nothing could
 * falsify. It is now built from fields the endpoint actually returns —
 * `whisper_loaded`, `rag_chunks`, `candidate_loaded` — so the rail can show a
 * real number (how many chunks the RAG index holds) and can show which
 * subsystem is actually down.
 *
 * Two constraints shape the polling. `/api/health` sits behind the same
 * 10-requests-per-minute per-IP bucket as the conversation endpoints
 * (backend/middleware.py RateLimitMiddleware), so a chatty poll would spend
 * the interview's own budget and 429 the message POST. Hence: one request per
 * minute, an in-flight guard so a slow check cannot stack behind itself, and
 * `pause()` around the turn itself — the interview is the priority, the rail
 * is decoration.
 *
 * A failed check is information, not a crash: `check()` never rejects, so
 * init() cannot break the conversation, and it never falls back to a
 * reassuring default, because "we could not verify" and "everything is fine"
 * are different claims.
 */
function createHealthStatus(el, options) {
    const opts = options || {};
    const doFetch = opts.fetch || ((url) => fetch(url));
    const schedule = opts.setTimer || ((fn, ms) => setTimeout(fn, ms));
    const unschedule = opts.clearTimer || ((id) => clearTimeout(id));
    const intervalMs = opts.intervalMs || 60000;

    let timerId = null;
    let inFlight = false;
    let paused = false;

    function paint(tone, text) {
        if (!el) return;
        const dot = el.querySelector(".dot");
        if (dot) dot.className = "dot " + tone;
        const label = el.querySelector(".status-text");
        if (label) label.textContent = text;
    }

    /**
     * Turn a health payload into what the rail says. Pure, so the classification
     * is testable without a network: anything that is not positively healthy
     * is not reported as healthy.
     */
    function describe(payload) {
        if (!payload || typeof payload !== "object") {
            return { tone: "dot-amber", text: "Sin verificar: respuesta ilegible" };
        }

        const problems = [];
        if (payload.whisper_loaded !== true) problems.push("STT sin modelo");
        if (payload.candidate_loaded !== true) problems.push("perfil no cargado");

        // The retrieval mode. Without this the rail reported a deployment that
        // had lost its embedding model as fully healthy, because every other
        // field is invariant under the TF-IDF fallback: the chunk count is the
        // same, the profile still loads, and `status` is still "ok". The page
        // said "Sistema OK · RAG 20 chunks" over a pipeline that was no longer
        // the one it claimed to be.
        //
        // An ABSENT rag_mode is not a problem: /api/health gained the field
        // later than some deployments were built, and a server that has not
        // heard of it is an older server, not a broken one. Only a value that
        // is present and is not the healthy one degrades the rail.
        const mode = payload.rag_mode;
        if (mode === "tfidf") {
            problems.push("RAG degradado: recuperación por TF-IDF, no embeddings");
        } else if (mode === "uninitialized") {
            problems.push("RAG sin inicializar");
        }

        const chunks = Number(payload.rag_chunks);
        if (!Number.isFinite(chunks) || chunks <= 0) {
            problems.push("RAG sin índice");
        }

        // The store. `PersistenceService` never raises — every method swallows
        // its exception, logs it and returns a sentinel — so an unreachable
        // database used to be invisible to every reader of this endpoint. Turns
        // that never reach the store are exactly what that policy protects, and
        // nothing else on the page could tell.
        //
        // `disabled` is a decision, not a fault: the deployment has said not to
        // write, so nothing that would have been written is missing.
        if (payload.persistence === "error") {
            problems.push("base de datos inaccesible: los turnos no se guardan");
        }

        // The server's own verdict, read last, and only to fill a gap this build
        // has no specific wording for. `/api/health` now derives `status` from
        // the fields above, and rendering green over a body it called
        // `degraded` would make the page contradict the thing it is reading.
        // A degradation the page can already name keeps its own line: the
        // specific wording is more use than "and also degraded".
        //
        // An ABSENT `status` is not a problem, for the same reason an absent
        // `rag_mode` is not: a server that has not heard of the field is an
        // older server, not a broken one. A status this build cannot read as
        // healthy -- anything that is not the string "ok" -- degrades the rail,
        // which is the rule this function already applies to every other field.
        if (payload.status !== undefined && payload.status !== "ok" && problems.length === 0) {
            const named = Array.isArray(payload.problems) ? payload.problems : [];
            problems.push(
                "el servidor se declara degradado" +
                    (named.length ? " (" + named.join(", ") + ")" : ""),
            );
        }

        if (problems.length === 0) {
            return {
                tone: "dot-green",
                text: "Sistema OK · RAG " + chunks + " chunks",
            };
        }
        return { tone: "dot-amber", text: "Degradado: " + problems.join(" · ") };
    }

    async function check() {
        // The storm guard: concurrent callers share the one in-flight request.
        if (inFlight) return null;
        inFlight = true;
        try {
            const res = await doFetch("/api/health");

            if (res.status === 429) {
                // We asked too often. That is a fact about our polling, not
                // about the server, so it must not be rendered as an outage.
                const view = { tone: "dot-amber", text: "Sin verificar (límite de peticiones)" };
                paint(view.tone, view.text);
                return view;
            }
            if (!res.ok) throw new Error("HTTP " + res.status);

            const view = describe(await res.json());
            paint(view.tone, view.text);
            return view;
        } catch (e) {
            const view = { tone: "dot-red", text: "Servidor no responde" };
            paint(view.tone, view.text);
            return view;
        } finally {
            inFlight = false;
        }
    }

    function start() {
        if (timerId === null) {
            const tick = () => {
                timerId = null;
                if (paused) {
                    // Skipped, not dropped: the next tick re-checks.
                    timerId = schedule(tick, intervalMs);
                    return;
                }
                check();
                timerId = schedule(tick, intervalMs);
            };
            timerId = schedule(tick, intervalMs);
        }
        check();
    }

    function stop() {
        if (timerId !== null) {
            unschedule(timerId);
            timerId = null;
        }
    }

    return {
        check,
        describe,
        start,
        stop,
        pause(value) {
            paused = value === true;
        },
        isPaused: () => paused,
        isChecking: () => inFlight,
    };
}

const healthStatus = createHealthStatus(
    document.getElementById("sidebar-status"),
);

/**
 * Populate the left sidebar with real data from the backend.
 * - Model names come from GET /api/config
 * - The recording duration limit comes from there too, and is applied to the
 *   page: a limit the server holds and the browser does not is not a limit.
 * - VU meter is driven by mic RMS via startVisualizationLoop
 * - Session ID and turn count update when interview starts
 */
async function populateStaticSidebar() {
    try {
        const res = await fetch(`${API_BASE}/api/config`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const cfg = await res.json();
        // Validated, not trusted. A zero, a negative or a string would either
        // disarm the cap for the rest of the session or stop every recording
        // where it stands, and both are worse than the fallback this replaces.
        // A page that obeys a bad response is a page that obeys anything.
        if (typeof cfg.max_audio_duration === "number" &&
            Number.isFinite(cfg.max_audio_duration) &&
            cfg.max_audio_duration > 0) {
            maxRecordingMs = cfg.max_audio_duration * 1000;
        }
        if (cfg.tts_voice) setText("sidebar-tts", `TTS: ${cfg.tts_voice}`);
        if (cfg.stt_model)
            setText(
                "sidebar-stt",
                `STT: ${cfg.stt_model} (${cfg.stt_device || "cpu"})`,
            );
        if (cfg.llm_model)
            setText("sidebar-llm", `LLM: ${cfg.llm_model.split("/").pop()}`);
        if (cfg.google_model)
            setText("sidebar-google", `Google: ${cfg.google_model}`);
    } catch (e) {
        console.warn("Could not load /api/config:", e);
        setText("sidebar-tts", "TTS: —");
        setText("sidebar-stt", "STT: —");
        setText("sidebar-llm", "LLM: —");
        setText("sidebar-google", "Google: —");
    }
}

/**
 * Live session timer. Starts at 00:00, ticks every second.
 * Resets whenever a new conversation is created.
 *
 * The handle is kept, and any previous writer is cleared before a new one is
 * armed. It used to be discarded, which made every call another permanent 1 Hz
 * writer: this runs on load and again from both END handlers, which each do
 * `sessionStartTime = null; startSessionTimer()` to reset the display. So every
 * END click left one more formatting the same string into the same element once
 * a second for the rest of the session -- eleven writers after ten interviews,
 * none of them stoppable.
 *
 * The leak is invisible in the UI precisely because the writers agree: they
 * compute the same value and write the same text. That is what let it survive a
 * suite that was green on every commit.
 */
function startSessionTimer() {
    const el = document.getElementById("sidebar-timer");
    if (!el) return;
    if (!sessionStartTime) sessionStartTime = Date.now();
    // Replace, never accumulate. This is the whole fix.
    if (sessionTimerId !== null) clearInterval(sessionTimerId);
    sessionTimerId = setInterval(() => {
        const s = Math.floor((Date.now() - sessionStartTime) / 1000);
        const mm = String(Math.floor(s / 60)).padStart(2, "0");
        const ss = String(s % 60).padStart(2, "0");
        el.textContent = `⏱ ${mm}:${ss}`;
    }, 1000);
}

/**
 * Update session ID and turn count when a new interview starts.
 * Called from startInterview() after the conversation is created.
 */
function updateSessionInfo(conversationId) {
    sessionStartTime = Date.now();
    const shortId = conversationId ? conversationId.slice(0, 6) : "---";
    setText("sidebar-session-id", `ID: ${shortId}`);
    setText("sidebar-turns", "Turnos: 0");
}

function updateTurnCount(turnCount) {
    setText("sidebar-turns", `Turnos: ${turnCount}`);
}

// Cached VU bar elements (lazy)
let vuBarsCache = null;
function getVuBars() {
    if (!vuBarsCache) {
        vuBarsCache = document.querySelectorAll("#sidebar-vu .vu-bar");
    }
    return vuBarsCache;
}

/**
 * Update the sidebar VU meter from current mic volume (0..1).
 * Lights up bars proportional to volume (more bars = louder).
 */
function updateVuMeter(volume) {
    const bars = getVuBars();
    if (!bars.length) return;
    const activeCount = Math.round(volume * bars.length);
    bars.forEach((bar, i) => {
        if (i < activeCount) {
            bar.classList.add("active");
        } else {
            bar.classList.remove("active");
        }
    });
}

// ─── Initialization ────────────────────────────────────

// Local storage key for the disclaimer acknowledgment.
const DISCLAIMER_KEY = "interviewtts.disclaimerAccepted";

/**
 * Start the disclaimer honesty gate. If the visitor has not previously
 * acknowledged it, show the overlay and keep the mic locked until they do.
 */
function initDisclaimer() {
    const acknowledged = (() => {
        try {
            return localStorage.getItem(DISCLAIMER_KEY) === "1";
        } catch (e) {
            // Storage may be unavailable (private mode). Treat as not acknowledged.
            return false;
        }
    })();

    if (acknowledged) {
        disclaimerOverlay.classList.add("hidden");
        return;
    }

    showDisclaimerGate();
    disclaimerAccept.addEventListener("click", () => {
        try {
            localStorage.setItem(DISCLAIMER_KEY, "1");
        } catch (e) {
            // Persist best-effort; still unlock in-session.
        }
        hideDisclaimerGate();
    });
}

/**
 * Everything in the page that the dialog stands in front of.
 *
 * Derived from the body's children rather than named, so a region added to the
 * page later is covered by the gate without anyone remembering to add it here.
 * A hard-coded list is how "the dialog is modal" quietly stops being true after
 * an unrelated markup change.
 */
function modalBackgroundRegions() {
    return Array.from(document.body.children).filter(
        (el) => el !== disclaimerOverlay,
    );
}

/**
 * Show the disclaimer gate, and make the page behind it genuinely unavailable.
 *
 * The card carries `role="dialog" aria-modal="true"`, and until now neither
 * claim was true. Nothing took focus, so a keyboard user's first Tab after load
 * landed wherever the document happened to be. Nothing trapped it, so they could
 * Tab straight out of a dialog that had promised they could not. And nothing
 * was inert, so END, Contexto, the transcript and the close button all stayed
 * in the tab order behind a modal.
 *
 * That matters more than usual here: the interview cannot be started until the
 * gate is acknowledged, so the page behind it is not merely distracting, it is
 * the part of the application the user is being asked to read first. A dialog
 * that says "modal" and is not is worse than one that says nothing, because it
 * spends the user's trust on the claim.
 *
 * Three things, and all three are needed: focus moves in, Tab cannot leave, and
 * the rest of the page is inert.
 */
function showDisclaimerGate() {
    // Not accepted yet: gate the mic and show the overlay.
    btnMic.disabled = true;
    disclaimerOverlay.classList.remove("hidden");
    for (const region of modalBackgroundRegions()) {
        region.setAttribute("inert", "");
    }
    disclaimerAccept.focus();
    trapFocusInDisclaimer();
}

/**
 * Take the gate down and give the page back.
 *
 * `inert` is removed, not just the class: a page left inert behind a dismissed
 * dialog is a page the user can see and cannot touch.
 */
function hideDisclaimerGate() {
    disclaimerOverlay.classList.add("hidden");
    for (const region of modalBackgroundRegions()) {
        region.removeAttribute("inert");
    }
    btnMic.disabled = false;
}

/**
 * Keep Tab inside the gate while it is open.
 *
 * The check is on the overlay being visible rather than on a flag, so the trap
 * cannot outlive the dialog: close the gate and the handler becomes a no-op
 * without anything having to remember to unregister it. A trap that keeps
 * running after the dialog is gone holds focus on a control the user can no
 * longer see.
 */
function trapFocusInDisclaimer() {
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Tab") return;
        if (disclaimerOverlay.classList.contains("hidden")) return;

        const focusable = Array.from(
            disclaimerOverlay.querySelectorAll(
                "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
            ),
        ).filter((el) => !el.disabled);
        if (!focusable.length) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        // Focus that is not in the dialog -- after any markup change, or after
        // the browser moved it -- comes straight back in.
        if (!disclaimerOverlay.contains(active)) {
            e.preventDefault();
            first.focus();
            return;
        }
        if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
        }
    });
}

function init() {
    initDisclaimer();

    initWaveformBars();
    initAvatarOrb();
    populateStaticSidebar();
    startSessionTimer();
    // The SISTEMA rail is polled, not hardcoded. Rate-limit pressure is the
    // reason this is one request a minute and pausable rather than a timer
    // hammering the same per-IP bucket the interview spends from.
    healthStatus.start();

    // Smart scroll
    conversation.addEventListener("scroll", () => {
        const threshold = 50;
        isUserScrolledUp =
            conversation.scrollHeight -
                conversation.scrollTop -
                conversation.clientHeight >
            threshold;
    });

    // Sidebar End Session button
    const endBtn = document.getElementById("sidebar-end-btn");
    if (endBtn) {
        endBtn.addEventListener("click", () => {
            if (isInterviewActive) {
                stopInterview();
                setText("sidebar-session-id", "ID: ---");
                setText("sidebar-turns", "Turnos: 0");
                sessionStartTime = null;
                startSessionTimer(); // reset to 00:00
            }
        });
    }

    // Mobile END button — same logic as sidebar button
    const mobileEndBtn = document.getElementById("mobile-end-btn");
    if (mobileEndBtn) {
        mobileEndBtn.addEventListener("click", () => {
            if (isInterviewActive) {
                stopInterview();
                setText("sidebar-session-id", "ID: ---");
                setText("sidebar-turns", "Turnos: 0");
                sessionStartTime = null;
                startSessionTimer();
            }
        });
    }

    // Debounced resize for Three.js avatar
    let resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
                const wrapper = document.getElementById("avatar-wrapper");
                if (wrapper) {
                    window.AvatarOrb.resize(
                        wrapper.clientWidth,
                        wrapper.clientHeight,
                    );
                }
            }
        }, 250);
    });

    // Context panel: every path into the state goes through the controller,
    // which is the only thing that can move it.
    contextToggle.addEventListener("click", toggleContextPanel);
    contextClose.addEventListener("click", closeContextPanel);

    // Clicking away dismisses the OVERLAY. It does not dismiss the rail: on a
    // desktop the panel is a column of the page, and a click in the transcript
    // deleting it would be the same class of surprise as the auto-close was.
    document.addEventListener("click", (e) => {
        if (
            isOverlayLayout() &&
            contextPanelState.isOpen() &&
            !contextPanel.contains(e.target) &&
            e.target !== contextToggle &&
            !contextToggle.contains(e.target)
        ) {
            closeContextPanel();
        }
    });

    // Escape is the dismissal a keyboard user reaches for. On a phone the panel
    // is a full-height overlay, and the close control is a glyph in the corner.
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && contextPanelState.isOpen()) {
            closeContextPanel();
        }
    });

    // One delegated listener for the evidence chips, on the container rather
    // than on each chip: renderContext() replaces this panel's innerHTML on
    // every turn, so a per-chip listener would be discarded with its node and
    // the next turn's chips would be dead. The container survives.
    contextContent.addEventListener("click", (e) => {
        const pill = e.target.closest(".chunk-pill");
        if (pill) toggleChunk(pill);
    });

    // Audio blocked overlay — resume on click
    audioOverlay.addEventListener("click", resumeAudioContext);

    // Mic button
    btnMic.addEventListener("click", toggleInterview);

    addMessage("system", "Presiona el micrófono para empezar.");
    setStatus("Preparado");
}

/**
 * Initialize AudioContext and AnalyserNode. Handles autoplay blocking.
 */
async function initAudio() {
    if (audioContext) return;

    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (audioContext.state === "suspended") {
            throw new Error("AudioContext blocked");
        }

        analyserNode = audioContext.createAnalyser();
        analyserNode.fftSize = 64; // 32 frequency bins
        waveformBars = new Uint8Array(analyserNode.frequencyBinCount);
        // Allocated here, once, where the analyser's fftSize is known. The
        // animation loop reused it instead of building a new one every frame.
        micTimeBuffer = new Uint8Array(analyserNode.fftSize);

        // Initialize TTS analyser for fake-sync
        if (!ttsAnalyser) {
            ttsAnalyser = audioContext.createAnalyser();
            ttsAnalyser.fftSize = 256;
            ttsAnalyser.smoothingTimeConstant = 0.5;
            ttsVolumeBuffer = new Uint8Array(ttsAnalyser.fftSize);
            ttsAnalyser.connect(audioContext.destination);
        }
    } catch (e) {
        console.warn("AudioContext init failed:", e.message);
        audioOverlay.classList.remove("hidden");
    }
}

/**
 * Resume AudioContext on user interaction.
 */
async function resumeAudioContext() {
    if (audioContext && audioContext.state === "suspended") {
        await audioContext.resume();
    }
    if (audioContext && audioContext.state === "running") {
        audioOverlay.classList.add("hidden");
    }
}

// ─── Avatar Orb ────────────────────────────────────────

function initAvatarOrb() {
    if (typeof window.AvatarOrb === "undefined") {
        console.warn("AvatarOrb not available");
        return;
    }

    const success = window.AvatarOrb.init();
    if (success) {
        // Fix initial render on mobile — measure wrapper and resize
        const wrapper = document.getElementById("avatar-wrapper");
        if (wrapper) {
            window.AvatarOrb.resize(wrapper.clientWidth, wrapper.clientHeight);
        }
        // The visualization loop is NOT started here. It used to be, which armed
        // a 60fps chain on page load that then ran for the life of the page --
        // including in the idle state, where there is no audio to visualise and
        // no state to show. setState() arms it, and stops it on the way back to
        // idle.
    }
}

// ─── Waveform bars ─────────────────────────────────────

function initWaveformBars() {
    const barCount = 32;
    const barWidth = 4;
    const gap = 2;

    for (let i = 0; i < barCount; i++) {
        const rect = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "rect",
        );
        rect.setAttribute("x", i * (barWidth + gap));
        rect.setAttribute("y", 20);
        rect.setAttribute("width", barWidth);
        rect.setAttribute("height", 0);
        rect.setAttribute("rx", "1");
        waveformSvg.appendChild(rect);
    }
}

function updateWaveform() {
    if (
        !analyserNode ||
        currentState === "idle" ||
        currentState === "processing"
    ) {
        waveformSvg.classList.remove("visible");
        return;
    }

    waveformSvg.classList.add("visible");
    analyserNode.getByteFrequencyData(waveformBars);

    const rects = waveformSvg.querySelectorAll("rect");
    for (let i = 0; i < rects.length && i < waveformBars.length; i++) {
        const value = waveformBars[i];
        const height = Math.max(1, (value / 255) * 36);
        rects[i].setAttribute("height", height);
        rects[i].setAttribute("y", 20 - height / 2);
    }
}

// ─── Visualization loop ────────────────────────────────

/**
 * Begin the visualization loop, unless it is already running.
 *
 * The guard is the point. This used to declare its own recursive closure and
 * call it, so there was no handle to test before arming: calling it twice
 * produced two chains, and every frame's work was then done twice.
 *
 * `setState` is what arms and disarms it, so the loop exists exactly while a
 * turn is in flight. Before, it was armed once on load and ran for the life of
 * the page -- 60 frames a second in the idle state too, where there is no audio
 * to visualise and no state to show.
 */
function startVisualizationLoop() {
    if (waveformAnimationId !== null) return;
    waveformAnimationId = requestAnimationFrame(visualizationFrame);
}

/**
 * Stop the visualization loop.
 *
 * `waveformAnimationId` is the single record of whether a chain is running, so
 * stopping and starting cannot disagree -- which is the failure the previous
 * shape invited, where the only way to know a chain existed was to have created
 * it in this call.
 */
function stopVisualizationLoop() {
    if (waveformAnimationId === null) return;
    cancelAnimationFrame(waveformAnimationId);
    waveformAnimationId = null;
    // The next run has to state the blend again rather than assume the orb
    // already holds it.
    lastBlendVolume = null;
}

/**
 * One frame, then the next.
 *
 * A named declaration rather than a closure inside startVisualizationLoop, so
 * the re-arm is a normal function reference the guard above can reason about.
 */
function visualizationFrame() {
    // Mic RMS (shared by orb and VU meter)
    let micVolume = 0;
    if (analyserNode) {
        // The buffer is hoisted, allocated once in initAudio beside the other
        // analyser buffers. It used to be `new Uint8Array(analyserNode.fftSize)`
        // on every frame: sixty allocations a second of a buffer that is
        // overwritten from the analyser before anything reads it, so the
        // allocation bought nothing and cost a young generation each frame.
        if (!micTimeBuffer) micTimeBuffer = new Uint8Array(analyserNode.fftSize);
        analyserNode.getByteTimeDomainData(micTimeBuffer);
        let sum = 0;
        for (let i = 0; i < micTimeBuffer.length; i++) {
            const v = (micTimeBuffer[i] - 128) / 128;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / micTimeBuffer.length);
        micVolume = Math.min(1, (rms / 0.15) ** 0.7);

        // Update orb
        if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
            window.AvatarOrb.setVolume(micVolume);
        }
    }

    // TTS RMS (for fake-sync)
    let ttsVolume = 0;
    if (ttsAnalyser && ttsVolumeBuffer) {
        ttsAnalyser.getByteTimeDomainData(ttsVolumeBuffer);
        let sum = 0;
        for (let i = 0; i < ttsVolumeBuffer.length; i++) {
            const v = (ttsVolumeBuffer[i] - 128) / 128;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / ttsVolumeBuffer.length);
        ttsVolume = Math.min(1, (rms / 0.1) ** 0.7);
    }

    // Drive video crossfade from TTS volume — continuous blend, no hard cut
    if (
        window.AvatarOrb &&
        typeof window.AvatarOrb.setBlend === "function"
    ) {
        // Only when it moved. setBlend writes a CSS custom property on
        // `#portal-ring`, and every write invalidates style for that element --
        // so restating an unchanged blend sixty times a second was sixty style
        // invalidations a second to express a value that, in silence, had not
        // moved at all. Rounded before comparing, because the raw float drifts
        // in the last bits and would re-arm the write on every frame anyway.
        const blend = Math.round(ttsVolume * 1000) / 1000;
        if (blend !== lastBlendVolume) {
            lastBlendVolume = blend;
            window.AvatarOrb.setBlend(ttsVolume);
        }
    }

    // Drive talking video playback rate (new)
    if (avatarTalkingVideo && currentState === "speaking") {
        // Map TTS volume to playback rate: 0.7x (silent) to 1.6x (loud)
        avatarTalkingVideo.playbackRate = 0.7 + ttsVolume * 0.9;
    }

    // Update waveform
    updateWaveform();

    // Update sidebar VU meter from mic volume
    updateVuMeter(micVolume);

    waveformAnimationId = requestAnimationFrame(visualizationFrame);
}

// ─── State machine ─────────────────────────────────────

function setState(state) {
    document.body.dataset.state = state;
    // Read before the assignment: this is what makes the video seek happen on
    // the way INTO speaking and not on every chunk of a turn.
    const wasSpeaking = currentState === "speaking";
    currentState = state;

    // Update ring
    orbitalRing.className = "orbital-ring";
    if (state !== "idle") {
        orbitalRing.classList.add(`state-${state}`);
    }

    // Update orb state
    if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
        window.AvatarOrb.setState(state);
    }

    // Energy field boost when AI is speaking
    if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
        if (state === "speaking") {
            window.AvatarOrb.boost(1.5); // more intense
        } else {
            window.AvatarOrb.boost(1.0); // normal
        }
    }

    // The status line's classes, written by the one function that owns them.
    // This used to be `statusEl.className = "hud-status"` followed by a
    // classList.add, which is what wiped the error class setStatus() had just
    // applied -- see applyStatusClasses().
    applyStatusClasses(state, statusIsError);

    // The visualization loop exists exactly while a turn is in flight. Idle is
    // most of this page's life, and a 60fps chain with nothing to draw is a
    // battery spent for no image.
    if (state === "idle") {
        stopVisualizationLoop();
    } else {
        startVisualizationLoop();
    }

    // Avatar video crossfade: show talking when speaking, neutral otherwise
    if (avatarTalkingVideo) {
        if (state === "speaking") {
            // Only on the way in. `setState("speaking")` is called by
            // tryPlayNextChunk -- once per TTS chunk, not once per turn -- so
            // seeking every time rewound the mouth to the same 0.3s frame at the
            // start of every sentence, which is what it did.
            if (!wasSpeaking) {
                avatarTalkingVideo.currentTime = 0.3;
            }
            avatarTalkingVideo.play().catch(() => {});
            avatarTalkingVideo.classList.add("active");
        } else {
            avatarTalkingVideo.classList.remove("active");
            // After crossfade completes, pause the talking video to save CPU
            setTimeout(() => {
                if (currentState !== "speaking") {
                    avatarTalkingVideo.pause();
                }
            }, 300);
        }
    }
}

// ─── Interview toggle ──────────────────────────────────

/**
 * The mic button's accessible name.
 *
 * This was the literal string "Iniciar entrevista" for the life of the page,
 * including while the button was the control that stopped a running interview
 * -- so a screen-reader user was told the button starts an interview at the
 * exact moment it is the only control that can end one. A start/stop control
 * names the action it is about to perform, which is why this is a function of
 * the state rather than an attribute.
 *
 * The two icons swap on the same two transitions, so the name cannot drift from
 * what the button shows.
 */
function micLabel(isActive) {
    return isActive ? "Detener entrevista" : "Iniciar entrevista";
}

function setMicLabel(isActive) {
    btnMic.setAttribute("aria-label", micLabel(isActive));
}

/**
 * The single writer of the mic button's presentation.
 *
 * The lit state, the two icons and the accessible name are one fact, so they
 * are written together from one decision. They used to be four class
 * manipulations repeated at each site that changed them, which is how a branch
 * could report a failure, move the state machine to idle, and leave all four
 * describing a running interview that was not running.
 *
 * The name is the part that matters most: it is the only thing that tells a
 * screen-reader user whether the control starts or ends, and it has to be true
 * at the same moment as the icons or the button is lying to half its users.
 */
function applyMicButtonState(isActive) {
    btnMic.classList.toggle("active", isActive);
    // The two icons are the same fact with opposite polarity: while an interview
    // runs the mic glyph is hidden and the stop glyph shows, and when it is not
    // running it is exactly reversed.
    micIcon.classList.toggle("hidden", isActive);
    stopIconEl.classList.toggle("hidden", !isActive);
    setMicLabel(isActive);
}

/**
 * The interview cannot run. Put the page back in a shape the user can act on.
 *
 * Both failure branches of the recorder used to report the message and call
 * `setState("idle")`, and stop there. That moved the avatar to idle and left
 * `isInterviewActive` true with the mic button still drawn and still named as
 * the control that ends a running interview. Three indicators, one of them
 * true, and a recovery that existed but was invisible: press the button, watch
 * an interview that was not running appear to end, press again to find out.
 *
 * So the interview is put back to the shape it had before it started. The one
 * control that can start an interview is then the one control that is offered,
 * and retrying is the single press its own label already promised. A user who
 * denied the microphone by accident, or whose input device was briefly busy,
 * gets one obvious way forward instead of a button that lies.
 */
function abandonInterviewStart() {
    isInterviewActive = false;
    applyMicButtonState(false);
}

function toggleInterview() {
    if (isInterviewActive) stopInterview();
    else startInterview();
}

/**
 * Clear everything a new conversation must not inherit.
 *
 * The transcript, the turn state and the sidebar counter are all per
 * interview. Left in place they describe the previous one — which is how the
 * Context sidebar ended up asking for a turn from two interviews ago.
 */
function resetInterviewView() {
    conversation.replaceChildren();
    turnState.reset();
    updateTurnCount(0);
}

async function startInterview() {
    if (isInterviewActive) return;

    // Init audio on first user interaction
    await initAudio();

    try {
        const res = await fetch(`${API_BASE}/api/conversation`, {
            method: "POST",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        conversationId = data.conversation_id;
        updateSessionInfo(conversationId);
        // A fresh conversation starts with a fresh transcript.
        resetInterviewView();
        addMessage("system", data.welcome_message);
    } catch (e) {
        console.error("Failed to create conversation:", e);
        setStatus("Error de conexión — recarga la página", true);
        return;
    }

    isInterviewActive = true;
    applyMicButtonState(true);
    setState("listening");

    startListening();
}

/**
 * End the interview. Everything that can still be running gets stopped.
 *
 * This used to flip a flag, release the microphone and reset the button, and
 * left the three things a turn is actually made of running: the audio that was
 * playing, the chunks queued behind it, and the SSE read loop. So pressing END
 * mid-answer did exactly what the user asked and nothing else -- the
 * interviewer kept talking, tokens kept streaming, and the transcript kept
 * growing behind a line that said the interview was over. The one control whose
 * whole job is to stop the thing could not stop the thing.
 *
 * `streaming.py` notes that queued audio is dropped when the session tears
 * down. On the client that was false in both directions: nothing dropped the
 * queue and nothing cancelled the stream, so the server kept producing audio
 * for a session the user had closed.
 */
function stopInterview() {
    isInterviewActive = false;

    // Audio first. The queue is the only thing that makes noise, and leaving
    // it running is the difference between END working and END being a lie.
    stopAudioPlayback();

    // Then the stream, so nothing new arrives to put back in the queue.
    abortTurnStream();

    if (isRecording) stopRecording();

    if (mediaStream) {
        mediaStream.getTracks().forEach((t) => t.stop());
        mediaStream = null;
    }

    applyMicButtonState(false);
    setState("idle");
    setStatus("Entrevista finalizada");
    addMessage("system", "Entrevista finalizada.");
}

/**
 * Make the page silent: the playing chunk stops, the queue is dropped.
 *
 * `resetAudioQueue()` is what stops the latch rather than just the noise --
 * it clears `isAudioPlaying`, so the next interview's first chunk is not
 * turned away at the `if (isAudioPlaying) return` guard by a player that is
 * still busy with a turn the user ended five minutes ago.
 */
function stopAudioPlayback() {
    if (currentAudio) {
        try {
            currentAudio.pause();
        } catch (_) {
            // Pausing an element whose source never loaded can throw; the point
            // is that we are done with it either way.
        }
        currentAudio = null;
    }
    resetAudioQueue();
    removeAudioIndicator();
}

/**
 * Cancel the turn's read loop, and mark it as deliberate.
 *
 * The flag and the abort are both needed, and they answer different questions.
 * `turnAborted` tells the read loop's catch that the stream was cut on purpose,
 * so it can unwind without writing a failure into a transcript the user just
 * closed. `abort()` is what actually stops the bytes arriving.
 *
 * A no-op when no turn is in flight, which is the common case: END is also the
 * button that stops a session that was only ever listening.
 */
function abortTurnStream() {
    turnAborted = true;
    if (turnAbortController) {
        turnAbortController.abort();
        turnAbortController = null;
    }
}

// ─── Recording + VAD ───────────────────────────────────

function startListening() {
    if (!isInterviewActive || isProcessing || isRecording) return;
    startRecording();
}

async function startRecording() {
    try {
        await ensureAudioContext();

        if (
            !mediaStream ||
            mediaStream.getTracks().some((t) => t.readyState === "ended")
        ) {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: true,
            });
        }

        // Connect to analyser for visualization.
        // Reuse the existing source for the same stream — creating and
        // connecting a new one per turn would stack sources into the
        // analyser, inflating RMS until silence is never detected.
        if (audioContext && analyserNode && mediaStream) {
            if (!micSourceNode || micSourceNode.mediaStream !== mediaStream) {
                if (micSourceNode) {
                    try {
                        micSourceNode.disconnect();
                    } catch (_) {}
                }
                micSourceNode =
                    audioContext.createMediaStreamSource(mediaStream);
                micSourceNode.connect(analyserNode);
            }
        }

        audioChunks = [];

        if (!selectedMimeType) chooseMimeType();

        if (!selectedMimeType) {
            console.error("No supported audio codec for MediaRecorder");
            setStatus(
                "Tu navegador no soporta grabación de audio compatible — usa Chrome o Safari actualizado",
                true,
            );
            setState("idle");
            // The interview is not running, so it must not be left looking as
            // though it were -- see abandonInterviewStart().
            abandonInterviewStart();
            mediaStream.getTracks().forEach((t) => t.stop());
            return;
        }

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: selectedMimeType,
            audioBitsPerSecond: 128000,
        });

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) {
                audioChunks.push(e.data);
                // Accumulated on every slice, so the running size is a number
                // rather than a surprise discovered by the server.
                recordedBytes += e.data.size;
            }
        };
        mediaRecorder.onstop = () => {
            stopVad();
            processRecordingStream();
        };

        mediaRecorder.start(RECORDING_TIMESLICE_MS);
        isRecording = true;
        hasSpoken = false;
        recordedBytes = 0;
        recordingCapped = false;
        armRecordingCap();
        startVad();
        setStatus("Escuchando…");
        setState("listening");
    } catch (e) {
        console.error("Mic denied:", e);
        setStatus(
            "Acceso al micrófono denegado — revisa permisos del navegador",
            true,
        );
        setState("idle");
        // Nothing was ever recorded, so there is no interview to end and no
        // state to keep. The page has to stop claiming one, or the recovery is
        // a double-press the candidate has to guess at.
        abandonInterviewStart();
    }
}

function stopRecording() {
    disarmRecordingCap();
    if (mediaRecorder && mediaRecorder.state !== "inactive")
        mediaRecorder.stop();
    isRecording = false;
}

// ─── The recording cap ───────────────────────────────────────────────────────
//
// The VAD decides when a turn ENDS. This decides when a recording may not
// continue, and it is the only bound that holds when the VAD cannot see a
// silence. Reaching it stops the recorder the same way a press of STOP does,
// so the audio collected so far is uploaded instead of discarded by a 413.
//
// `maxRecordingMs` rather than the constant: the limit is the server's, and
// `populateStaticSidebar` replaces this before the first answer is due. Read at
// arm time, not at module time, so a limit that arrives late is still applied
// to the recording it applies to.

function armRecordingCap() {
    disarmRecordingCap();
    const limitMs = maxRecordingMs;
    recordingCapTimer = setTimeout(() => {
        recordingCapTimer = null;
        recordingCapped = true;
        const seconds = Math.round(limitMs / 1000);
        setStatus(
            "Grabación cortada por el límite de " + seconds +
                " s — enviando lo grabado…",
            true,
        );
        setState("processing");
        stopRecording();
    }, limitMs);
}

function disarmRecordingCap() {
    if (recordingCapTimer !== null) {
        clearTimeout(recordingCapTimer);
        recordingCapTimer = null;
    }
}

// ─── VAD ───────────────────────────────────────────────

/**
 * Move the noise-floor estimate by one frame, and return the cut threshold.
 *
 * THE ALGORITHM, AND WHY THIS ONE
 * -------------------------------
 * An exponential minimum: the floor chases the signal, quickly downwards and
 * slowly upwards, so it settles at the level the room has recently been rather
 * than at the level someone is making right now.
 *
 * That asymmetry is the whole design. The direction that must be fast is the
 * one that recovers from a sudden quiet -- a door, a colleague stepping out --
 * because a floor that lags there leaves the page deaf for the rest of the
 * interview. The direction that must be slow is the one that would otherwise
 * follow a word opening, because a floor that follows speech is a floor that
 * calls speech silence.
 *
 * WHY NOT A PERCENTILE OF A RECENT WINDOW
 * ---------------------------------------
 * It was the other candidate, and it fails in a way that is hard to see. A low
 * percentile of the last N frames IS a minimum over a window, so it has the
 * same weakness as any minimum: during N frames of unbroken speech there is no
 * quiet in the window, the percentile is the speech level, and the detector
 * declares the speaker silent. The window bounds how long that lasts; it does
 * not stop it. A per-frame CAP on how far the floor may climb has the same fate
 * by a different route -- it just arrives at the speech level a few seconds
 * later instead of immediately -- which is worth knowing before choosing it,
 * because the cap is the more obvious-looking of the two.
 *
 * So the floor alone cannot be the fix, and the second half of the fix is in
 * `vadLoop`: a frame only counts as silence if the turn has been LOUDER than the
 * threshold at some point. A tracker that has run away to the signal's own
 * level therefore cannot produce a cut, because the loudest thing recorded is
 * the signal and the threshold is twice the floor. Estimating the room and
 * recognising a pause are two separate problems, and only the first one is an
 * estimation problem.
 *
 * WHY THE ABSOLUTE THRESHOLD STILL EXISTS
 * --------------------------------------
 * It is the lower bound of the pair, not a replacement. In a genuinely quiet
 * room the floor collapses towards zero, the relative term never wins, and the
 * threshold is RMS_THRESHOLD -- the same number, and the same behaviour, that
 * always worked.
 */
function updateNoiseFloor(rms) {
    const rate = rms > noiseFloor ? NOISE_FLOOR_RISE_RATE : NOISE_FLOOR_FALL_RATE;
    noiseFloor += (rms - noiseFloor) * rate;
    return Math.max(RMS_THRESHOLD, noiseFloor * NOISE_FLOOR_MULTIPLE);
}

function startVad() {
    if (!analyserNode) return;
    silenceStart = null;
    hasSpoken = false;
    // Per recording, never carried across. The room the previous turn was in is
    // not evidence about this one, and a stale floor inherited into a quieter
    // room would keep the page deaf for the length of a turn.
    noiseFloor = 0;
    loudPeakRms = 0;
    vadAnimationId = requestAnimationFrame(vadLoop);
}

function vadLoop() {
    if (!isRecording || !analyserNode) return;

    const buf = new Uint8Array(analyserNode.fftSize);
    analyserNode.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    // The floor moves on EVERY frame, including the loud ones. Restricting the
    // update to frames already judged silent is circular: in a noisy room there
    // are no such frames, which is the bug.
    const threshold = updateNoiseFloor(rms);

    if (rms >= threshold) {
        hasSpoken = true;
        if (rms > loudPeakRms) loudPeakRms = rms;
        silenceStart = null;
    } else if (hasSpoken && loudPeakRms > threshold) {
        // Below the threshold AND this turn has been louder than it, so this is
        // a pause and not merely a level the room happens to sit at.
        if (silenceStart === null) silenceStart = Date.now();
        else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            setStatus("Procesando…");
            setState("processing");
            stopRecording();
            return;
        }
    } else {
        // Neither. The floor has run away to the signal's own level, so nothing
        // can be concluded from this frame -- and in particular the silence
        // timer is cleared, because a pause has to be CONTINUOUS: a run of
        // undecidable frames in the middle of one must not be papered over by
        // the clock.
        silenceStart = null;
    }

    vadAnimationId = requestAnimationFrame(vadLoop);
}

function stopVad() {
    if (vadAnimationId) {
        cancelAnimationFrame(vadAnimationId);
        vadAnimationId = null;
    }
    silenceStart = null;
    noiseFloor = 0;
    loudPeakRms = 0;
}

// ─── Audio queue ───────────────────────────────────────

function resetAudioQueue() {
    audioQueue = [];
    nextChunkId = 0;
    skippedChunkIds.clear();
    isAudioPlaying = false;
    allChunksReceived = false;
}

function addAudioIndicator() {
    if (!currentCandidateDiv) return;
    const bubble = currentCandidateDiv.querySelector(".bubble");
    if (!bubble || bubble.querySelector(".audio-indicator")) return;
    const indicator = document.createElement("div");
    indicator.className = "audio-indicator";
    for (let i = 0; i < 5; i++) {
        const bar = document.createElement("span");
        bar.className = "bar";
        indicator.appendChild(bar);
    }
    bubble.appendChild(indicator);
}

function removeAudioIndicator() {
    if (currentCandidateDiv) {
        const indicator = currentCandidateDiv.querySelector(".audio-indicator");
        if (indicator) indicator.remove();
    }
}

function advancePastSkippedChunks() {
    if (isAudioPlaying) return;
    while (skippedChunkIds.has(nextChunkId)) {
nextChunkId++;
    }
}

function tryPlayNextChunk() {
    if (isAudioPlaying) return;
    advancePastSkippedChunks();

    const idx = audioQueue.findIndex((c) => c.id === nextChunkId);
    if (idx === -1) return;

    const chunk = audioQueue.splice(idx, 1)[0];
    isAudioPlaying = true;
    // The answer is audible now. This is the same stage the `audio_url`
    // branch reports, so the line does not flicker when playback starts
    // before the next chunk has finished arriving.
    turnNarrator.speaking();
    setState("speaking");
    addAudioIndicator();

    const audio = new Audio(chunk.url);
    currentAudio = audio;

    // Connect to TTS analyser for fake-sync (only if audioContext is available)
    if (audioContext && ttsAnalyser) {
        try {
            const source = audioContext.createMediaElementSource(audio);
            source.connect(ttsAnalyser);
        } catch (e) {
            // Some browsers throw if the element is already connected; ignore
        }
    }

    audio.addEventListener("ended", () => abandonCurrentChunk(), { once: true });
    audio.addEventListener(
        "error",
        () => {
            console.error("Audio playback error for chunk", chunk.id);
            abandonCurrentChunk();
        },
        { once: true },
    );

    // ONE handler for the whole chain, and that is the fix.
    //
    // This was `ensureAudioContext().then(() => audio.play().catch(...))`. The
    // inner `.catch` covered play() and nothing covered the promise the `.then`
    // was attached to, so a rejected `AudioContext.resume()` escaped unhandled.
    // It escaped at the worst possible moment: `isAudioPlaying` was already true
    // when the chain was armed, and the `ended`/`error` handlers that would have
    // cleared it never ran. So every later chunk returned at the
    // `if (isAudioPlaying) return` guard, checkAllDone() never ran, and
    // checkAllDone() is the ONLY caller of startListening(). The candidate
    // heard nothing, the mic never came back and the spinner spun forever, with
    // no message and no way out but a reload.
    //
    // `resume()` rejects routinely — the page lost the user gesture, or the tab
    // was backgrounded mid-turn. This is not a rare race, and a turn is not
    // recoverable from it by hand because the user is never told.
    ensureAudioContext()
        .then(() => audio.play())
        .catch((e) => {
            console.error("Audio playback could not start:", e);
            // To the candidate these are the same event: no sound, and nothing
            // on the page to say why. A context the browser will not run is the
            // case the audio-blocked overlay exists for, and clicking it resumes
            // -- so showing it is the difference between a recoverable turn and
            // a dead one. A one-off play() refusal with the context running is
            // not a block, and must not raise the overlay for nothing.
            if (!audioContext || audioContext.state !== "running") {
                reportAudioBlocked();
            }
            abandonCurrentChunk();
        });
}

/**
 * A chunk will not be heard. Release the player and let the turn move on.
 *
 * One function, because this bookkeeping is a latch rather than a tidy-up.
 * `isAudioPlaying` left true makes every later chunk return at the guard in
 * tryPlayNextChunk, so checkAllDone() never runs, and checkAllDone() is the
 * only caller of startListening(): the mic never comes back and the turn never
 * ends. Every path that finishes a chunk without audio -- `ended`, a decode
 * `error`, a refused `play()`, and a context that could not be resumed -- must
 * come through here, or any one of them can end the interview on its own.
 *
 * `nextChunkId++` is what moves the cursor past a chunk that will never be
 * heard, so the queue does not stall on it either.
 */
function abandonCurrentChunk() {
    nextChunkId++;
    isAudioPlaying = false;
    currentAudio = null;
    removeAudioIndicator();
    tryPlayNextChunk();
    checkAllDone();
}

/**
 * Say that the audio is blocked, through the control that can unblock it.
 *
 * There was an `audioBlocked` flag here, written in three places and read in
 * none -- a note to self that nothing could act on. It is deleted rather than
 * wired up, because the overlay it shadowed is already the state: whether the
 * audio is blocked is exactly whether `#audio-blocked-overlay` is showing, and
 * two representations of one fact is how they drift.
 *
 * Showing the overlay is the whole point: a candidate told nothing has no way to
 * recover except a reload, and the one recovery the platform offers is sitting
 * right there in the markup.
 */
function reportAudioBlocked() {
    if (audioOverlay) audioOverlay.classList.remove("hidden");
}

function checkAllDone() {
    // The one place that knows a turn is genuinely over: generation finished,
    // the queue is empty and nothing is playing. The narrator is told from
    // exactly this state, so "Escuchando…" can never arrive while the
    // candidate is still being spoken to.
    turnNarrator.audioState(audioQueue.length, isAudioPlaying);
    if (allChunksReceived && audioQueue.length === 0 && !isAudioPlaying) {
        if (isInterviewActive) startListening();
    }
}

// ─── Fetch with backoff ────────────────────────────────

/**
 * The retry policy: which failures are worth a second attempt, and how long
 * we are willing to keep trying.
 *
 * The previous helper retried *everything*, because it turned any non-OK
 * response into `throw new Error("HTTP " + status)` inside its own try block
 * and fed that to the same catch as a dropped connection. A 413 was therefore
 * indistinguishable from a flaky socket, and the loop happily re-ran upload +
 * Whisper + RAG + LLM + TTS up to six times against a request the server had
 * already refused on principle. That is how a retry mechanism turns into a
 * load amplifier.
 *
 * Retrying is only correct for failures that can plausibly resolve without the
 * request changing:
 *
 *   408 — the server gave up waiting for the body
 *   429 — the rate limiter asked us to slow down
 *   500 — unhandled server error
 *   502 / 503 / 504 — gateway or upstream unavailable
 *
 * Every other 4xx is a statement about *this* request — malformed, too large,
 * unauthorised, gone. It is surfaced with its real status and never retried.
 *
 * The bound is on total elapsed time, not on attempt count, because attempts
 * and patience are different things: the old 1s/2s/4s/8s/16s/30s ladder could
 * hold the interview for 63 seconds. Here the schedule is 1s, 2s, 4s, 8s and
 * the budget is 15s, so the worst case is five attempts and fifteen seconds.
 *
 * `replayable` is the honest part. A network-level failure is ambiguous: the
 * browser's `fetch` rejects with a bare TypeError and deliberately does not
 * say whether the request ever left the client. For a request that only reads
 * that is harmless, so it gets the full schedule. For one that WRITES it is
 * not: the request may have been received, the turn committed, and only the
 * response lost. See fetchWithBackoff for how that is handled.
 */
function createRetryPolicy(config) {
    const cfg = config || {};

    const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
    const SCHEDULE_MS = [1000, 2000, 4000, 8000];
    const MAX_TOTAL_WAIT_MS = 15000;

    function isRetryableStatus(status) {
        return RETRYABLE_STATUSES.has(status);
    }

    /**
     * Parse Retry-After, which may be delta-seconds or an HTTP-date.
     * Returns milliseconds, or null when the header is absent or unusable.
     */
    function retryAfterMs(res) {
        if (!res || !res.headers || typeof res.headers.get !== "function") {
            return null;
        }
        const raw = res.headers.get("Retry-After");
        if (raw === null || raw === undefined || raw === "") return null;

        const seconds = Number(raw);
        if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);

        const when = Date.parse(raw);
        if (Number.isFinite(when)) return Math.max(0, when - Date.now());

        return null;
    }

    /**
     * How long to wait before attempt number `attempt` (0-based), or null when
     * doing so would break the total-time budget.
     *
     * A server-sent Retry-After wins over the local ladder: it is the only
     * party that knows when it will be ready. It is still bounded — an hour
     * of backpressure is not something to hold a live interview for, so
     * exceeding the remaining budget refuses the retry and surfaces the 429
     * instead.
     */
    function delayFor(attempt, res, waitedMs) {
        const local = SCHEDULE_MS[Math.min(attempt, SCHEDULE_MS.length - 1)];
        const asked = res ? retryAfterMs(res) : null;
        const delay = asked === null ? local : Math.max(local, asked);

        if (waitedMs + delay > MAX_TOTAL_WAIT_MS) return null;
        return delay;
    }

    /** What the user is told while a retry is pending. */
    function retryMessage(status, delayMs) {
        const seconds = Math.ceil(delayMs / 1000);
        if (status === 429) {
            return "Servidor ocupado (429) — esperando " + seconds + " s…";
        }
        if (status === 503) {
            return "Servidor no disponible (503) — reintentando en " + seconds + " s…";
        }
        if (status === 502 || status === 504) {
            return "Puerta de entrada con problemas (" + status + ") — reintentando…";
        }
        if (status === 408) {
            return "El servidor tardó demasiado (408) — reintentando…";
        }
        if (status === 500) {
            return "Error del servidor (500) — reintentando…";
        }
        return "Reintentando…";
    }

    /**
     * Turn a non-retryable or exhausted response into a real Error, carrying
     * the status so the caller can tell a 413 from a 404.
     */
    async function toError(res) {
        const status = res.status;
        let detail = "";
        try {
            const body = await res.json();
            if (body && body.detail) detail = String(body.detail);
        } catch (_) {
            // Not JSON: nginx serves an HTML page for its own 413, and a
            // proxy serves HTML for 502. The status is still the honest
            // signal, so carry on without the detail.
        }

        const err = new Error(messageFor(status, detail));
        err.status = status;
        err.detail = detail;
        return err;
    }

    function messageFor(status, detail) {
        const suffix = detail ? " — " + detail : "";
        if (status === 413) {
            return "Grabación demasiado grande para el servidor (413). Reduce la " +
                "duración del audio." + suffix;
        }
        if (status === 422) {
            return "El servidor no pudo procesar el audio (422)" + suffix;
        }
        if (status === 400) {
            return "El servidor rechazó la petición (400)" + suffix;
        }
        if (status === 401 || status === 403) {
            return "Sin permiso para enviar audio (" + status + ")" + suffix;
        }
        if (status === 404) {
            return "Esta conversación ya no existe en el servidor (404). Empieza una entrevista nueva.";
        }
        if (status === 429) {
            return "Demasiadas peticiones (429). Espera un momento y reintenta.";
        }
        if (status === 500) {
            return "Error interno del servidor (500)" + suffix;
        }
        if (status === 502 || status === 503 || status === 504) {
            return "El servidor no está disponible (" + status + ")" + suffix;
        }
        if (status === 408) {
            return "El servidor tardó demasiado en responder (408). Reintenta.";
        }
        return "El servidor rechazó la petición (" + status + ")" + suffix;
    }

    return {
        isRetryableStatus,
        retryAfterMs,
        delayFor,
        retryMessage,
        toError,
        messageFor,
        isReplayable: () => cfg.replayable === true,
        maxAttempts: () => SCHEDULE_MS.length + 1,
        totalBudgetMs: () => MAX_TOTAL_WAIT_MS,
        schedule: () => [...SCHEDULE_MS],
        retryableStatuses: () => [...RETRYABLE_STATUSES],
    };
}

// The one request this app routes through the helper writes a turn, so it
// gets the write-once policy. A read would use
// createRetryPolicy({ replayable: true }) instead: nothing it can do is
// duplicated by being sent twice, so it is safe to replay silently.
const writeOncePolicy = createRetryPolicy({ replayable: false });

/**
 * Fetch with bounded, classified backoff.
 *
 * The retry decision is split by *how the failure is known*, not by how it
 * feels, because those two cases have genuinely different risks:
 *
 *  - A response arrived. The request demonstrably reached the server. For
 *    /message/stream that is safe to replay even for a write, because the
 *    handler returns a StreamingResponse: the status line is committed before
 *    the generator runs, and the turn is only written near the very end of
 *    that generator. A non-200 therefore means the pipeline never started and
 *    no turn exists. Retry iff the status is transient and the budget allows.
 *
 *  - No response arrived (fetch rejected). The browser gives us a bare
 *    TypeError and, by specification, will not say whether the request ever
 *    left the client. For the message POST this is AMBIGUOUS: the server may
 *    have received it, run the whole pipeline and committed the turn, with
 *    only the response lost on the way back. The server's turn write is
 *    collision-safe, but on collision it deliberately re-derives a fresh turn
 *    number — so a duplicate POST produces a duplicate turn, not an
 *    overwrite. There is no browser-visible signal that separates "never
 *    sent" from "sent and lost", and the server has no idempotency key to
 *    deduplicate on yet, so this does not guess. It reports the ambiguity and
 *    asks for one deliberate retry.
 *
 * The trade-off, stated plainly: a genuine network blip during a turn now
 * costs the user one manual retry instead of up to five silent automatic ones.
 * That is the correct price for not writing duplicate turns, and it is a real
 * cost, not a free win. The proper fix is server-side — an idempotency key
 * the turn write can deduplicate on — and is deliberately not attempted here.
 *
 * @param {boolean} [policy.replayable] may a request be silently re-sent after
 *   an ambiguous network failure? True for reads, false for writes.
 */
async function fetchWithBackoff(url, options, policy) {
    const maxAttempts = policy.maxAttempts();
    let waitedMs = 0;
    let attempt = 0;

    for (;;) {
        let res = null;
        try {
            res = await fetch(url, options);
        } catch (e) {
            if (!policy.isReplayable()) {
                // Ambiguous, and not provably safe to replay. Say so.
                const err = new Error(
                    "Se perdió la conexión con el servidor y no se sabe si el turno " +
                        "se guardó. Pulsa el micrófono para reintentar.",
                );
                err.ambiguous = true;
                err.cause = e;
                throw err;
            }

            if (attempt >= maxAttempts - 1) throw e;
            const delay = policy.delayFor(attempt, null, waitedMs);
            if (delay === null) throw e;

            waitedMs += delay;
            attempt += 1;
            setStatus("Sin conexión — reintentando…", "error");
            await new Promise((r) => setTimeout(r, delay));
            continue;
        }

        if (res.ok) return res;

        if (!policy.isRetryableStatus(res.status) || attempt >= maxAttempts - 1) {
            throw await policy.toError(res);
        }

        const delay = policy.delayFor(attempt, res, waitedMs);
        if (delay === null) throw await policy.toError(res);

        waitedMs += delay;
        attempt += 1;
        setStatus(policy.retryMessage(res.status, delay), "error");
        await new Promise((r) => setTimeout(r, delay));
    }
}

// ─── SSE pipeline ──────────────────────────────────────

/**
 * Terminal-state owner for one interview turn.
 *
 * Contract: a turn settles EXACTLY ONCE, whichever terminal signal arrives
 * first — `done`, `error`, `interview_end`, or stream EOF. Later signals are
 * no-ops, so a `done` followed by an `error` (or an `interview_end` followed
 * by EOF) cannot tear the turn down twice.
 *
 * EOF counts as terminal on purpose: a truncated stream (client disconnect,
 * provider death) gives the frontend nothing to distinguish it from a normal
 * end, so without this the mic never restarts and the audio indicator spins
 * on forever.
 *
 * Side effects are injected as hooks so this stays testable without a DOM.
 * See tests/frontend/terminal_state.test.mjs.
 *
 * @param {{onSettle?: (reason: string) => void}} [hooks]
 * @returns {{settle: (reason: string) => boolean, isSettled: () => boolean}}
 */
function createTurnSettler(hooks) {
    let settled = false;
    return {
        isSettled: () => settled,
        /**
         * @param {string} reason Terminal signal that arrived.
         * @returns {boolean} true if this call performed the settlement.
         */
        settle(reason) {
            // Set before the callback so a re-entrant settle() from within
            // onSettle is a no-op instead of recursing.
            if (settled) return false;
            settled = true;
            if (hooks && hooks.onSettle) hooks.onSettle(reason);
            return true;
        },
    };
}

/**
 * Turn narration: the single owner of the status line for one turn.
 *
 * The defect this replaces: a candidate's turn is speak -> "Enviando audio" ->
 * 8-12 seconds -> audio, and that string was wrong for almost all of the wait,
 * because the server spends that time transcribing, retrieving and generating.
 * The dispatcher branched on all five SSE events while calling `setStatus` in
 * none of them, so the line stayed frozen at the request. Two symptoms shared
 * the root cause: the typing bubble appeared *before* the request was sent, so
 * "the AI is writing" was on screen while Whisper was still working, and the
 * settler wrote "Escuchando…" on `done` -- which says generation finished, not
 * that playback did -- claiming the mic was back while chunks were still
 * queued and playing.
 *
 * Two rules make this honest rather than merely busier:
 *
 *   1. A stage is only ever entered because something observed it. Nothing
 *      here is scheduled, timed or guessed; there is no per-stage timing to
 *      drive a progress figure, so there is no progress figure.
 *   2. Stages only move forward. `speaking` survives `complete()` for exactly
 *      as long as audio is outstanding, and the "mic is back" claim waits for
 *      the state the client already tracks -- queue empty and nothing playing.
 *
 * Rendering goes through `hooks.onStatus`, so this stays pure and testable;
 * see tests/frontend/turn_narration.test.mjs.
 */
function createTurnNarrator(hooks) {
    const STAGE_TEXT = {
        uploading: "Enviando audio…",
        transcribing: "Transcribiendo tu respuesta…",
        generating: "Redactando la respuesta…",
        speaking: "Reproduciendo la respuesta…",
        listening: "Escuchando…",
        chunkSkipped: "Se ha omitido un fragmento de audio.",
    };
    const FAILURE_TEXT = "Error del servidor.";

    let stage = null;
    let generationComplete = false;
    let audioOutstanding = false;
    let terminated = false;

    /**
     * Move to a stage and render it. Returns whether the line changed, so a
     * repeated signal for the stage already on screen costs nothing.
     */
    function move(next) {
        if (terminated || next === stage) return false;
        stage = next;
        if (next === "failed") {
            if (hooks && hooks.onStatus) hooks.onStatus(FAILURE_TEXT, "error");
        } else if (hooks && hooks.onStatus) {
            hooks.onStatus(STAGE_TEXT[next]);
        }
        return true;
    }

    return {
        /** @returns {string|null} the current stage, or null before it starts. */
        stage() {
            return stage;
        },
        /** @returns {string|null} what the line currently says. */
        text() {
            if (stage === "failed") return FAILURE_TEXT;
            return stage === null ? null : STAGE_TEXT[stage];
        },
        /** The request is in flight: the recording is being sent. */
        begin() {
            move("uploading");
        },
        /** `transcription` arrived: the answer is being understood. */
        transcribing() {
            move("transcribing");
        },
        /** The first `token` arrived: the answer is being written. */
        generating() {
            move("generating");
        },
        /** Audio is audible, or is about to be. */
        speaking() {
            audioOutstanding = true;
            move("speaking");
        },
        /** A TTS chunk failed server-side and was skipped: the turn continues. */
        chunkSkipped() {
            move("chunkSkipped");
        },
        /** A fatal error: the turn is over and nothing later may claim otherwise. */
        failed() {
            // move() first, then freeze: setting the flag first would make
            // move() reject the very transition that is being reported.
            move("failed");
            terminated = true;
        },
        /** `done`: generation finished. Playback is a separate question. */
        complete() {
            generationComplete = true;
            if (!audioOutstanding) move("listening");
        },
        /**
         * The audio queue's state, as the player already tracks it.
         *
         * @param {number} queued chunks waiting behind the current one.
         * @param {boolean} playing whether a chunk is currently playing.
         */
        audioState(queued, playing) {
            audioOutstanding = queued > 0 || playing;
            if (generationComplete && !audioOutstanding) move("listening");
        },
    };
}

/**
 * Turn bookkeeping, driven by the server rather than by the DOM.
 *
 * The turn number used to be derived by counting `.message` elements. That is
 * wrong the moment the transcript is not empty: from the second interview
 * onward the counter ran ahead of the DB and the Context sidebar asked for a
 * turn that does not exist, 404ing in silence. The `done` payload now names
 * the turn the DB committed, so this only holds what the server said — it
 * never infers a number.
 *
 * `null` means "the server named no turn": a turn whose write failed, an empty
 * transcription, a truncated stream. Nothing is counted and nothing is
 * requested in that case, which is the whole point — a wrong turn number is a
 * 404. Both `done` and `interview_end` feed this state the same way, so the
 * object is indifferent to which terminal event carried the number.
 *
 * See tests/frontend/turn_state.test.mjs.
 *
 * @returns {{
 *   reset: () => void,
 *   commit: (data?: {n?: number, has_context?: boolean, context_grounding?: string}) => number|null,
 *   last: () => number|null,
 *   contextTurn: () => number|null,
 *   contextGrounding: () => "grounded"|"related"|null,
 * }}
 */function createTurnState() {
    let lastTurnNumber = null;
    let contextTurnNumber = null;
    let contextProvenance = null;
    return {
        reset() {
            lastTurnNumber = null;
            contextTurnNumber = null;
            contextProvenance = null;
        },
        /**
         * Record the turn the server reported as committed.
         *
         * @param {{n?: number, has_context?: boolean, context_grounding?: string}} [data]
         *   `done` payload.
         * @returns {number|null} the committed turn, or null if it named none.
         */
        commit(data) {
            if (!data || !Number.isInteger(data.n) || data.n < 0) return null;
            lastTurnNumber = data.n;
            // Only a turn the server says has context is worth requesting.
            // Replacing rather than keeping the previous value stops a stale
            // turn from being requested after a context-free one.
            contextTurnNumber = data.has_context ? data.n : null;
            // The provenance of those passages, held beside the turn they belong
            // to and replaced with it. A panel drawing turn N's chips under
            // turn N-1's provenance is a claim about a different answer than the
            // one on screen.
            //
            // An absent field reads as "grounded", and that is the correct
            // default rather than a lenient one: /api/health-grade honesty cuts
            // both ways, and every answer an older server sends with chunks on it
            // is one the RAG actually wrote.
            contextProvenance = data.has_context
                ? data.context_grounding || "grounded"
                : null;
            return data.n;
        },
        /** @returns {number|null} the last committed turn, or null. */
        last() {
            return lastTurnNumber;
        },
        /**
         * The turn whose context is worth requesting, or null when the server
         * said there is none. Never a guess.
         *
         * @returns {number|null}
         */
        contextTurn() {
            return contextTurnNumber;
        },
        /**
         * What the passages for `contextTurn()` actually are: the answer the RAG
         * wrote them from, or passages that merely resemble the question.
         *
         * @returns {"grounded"|"related"|null}
         */
        contextGrounding() {
            return contextProvenance;
        },
    };
}

const turnState = createTurnState();

// The turn number the sidebar is already showing for this interview, or null.
// Cleared per turn next to `turnState.reset()`, and it is what makes the
// bookkeeping in the settle hook idempotent -- see the comment there.
let publishedTurnNumber = null;

async function processRecordingStream() {
    if (audioChunks.length === 0) {
        // Nothing captured (e.g. instant stop): release the processing
        // state so body[data-state] doesn't strand the mic on amber.
        setState("idle");
        return;
    }
    isProcessing = true;
    btnMic.disabled = true;
    // A turn starts knowing nothing about being cancelled. The controller is
    // what END aborts, and it has to exist before the request leaves or there
    // is a window in which the user can end an interview whose stream is
    // already on the wire with nothing to cancel it.
    turnAborted = false;
    turnAbortController = new AbortController();
    // One narrator per turn, next to the one settler below: the status line
    // has a single owner, so a stage cannot be reported by two call sites
    // that drift apart.
    const narrator = createTurnNarrator({
        onStatus: (text, className) => setStatus(text, className),
    });
    turnNarrator = narrator;
    // The only claim this file can make honestly before the server answers:
    // the recording is on its way. Everything after that is reported by
    // whoever actually observed it.
    narrator.begin();
    setState("processing");
    resetAudioQueue();
    turnState.reset();
    publishedTurnNumber = null;
    currentCandidateDiv = null;
    // Arm the stopwatch before the request leaves, and clear any previous
    // turn's figure: the pill must never show turn N-1's latency as if it
    // were turn N's.
    latencyReadout.begin();
    // The interview owns the rate-limit budget; the health rail stands down
    // for the duration of the turn.
    healthStatus.pause(true);

    const blob = new Blob(audioChunks, {
        type: selectedMimeType || "audio/webm",
    });
    const ext = (selectedMimeType || "").includes("mp4") ? ".m4a" : ".webm";
    const fd = new FormData();
    fd.append("audio", blob, `recording${ext}`);

    // One settler per turn: the single owner of terminal bookkeeping, so
    // `done`, `error`, `interview_end` and EOF cannot each tear down the turn
    // independently.
    const turn = createTurnSettler({
        onSettle(reason) {
            // A terminal event means the answer is complete, whatever ended
            // it. This is what unblocks the mic: checkAllDone() restarts
            // listening once the audio queue has drained.
            allChunksReceived = true;
            hideTyping();
            removeAudioIndicator();
            // The text is final, so let it be heard. Driven from here rather
            // than from a branch, because `done`, `interview_end`, `error` and
            // EOF all reach the end of a turn and every one of them must
            // finish the answer -- a terminal path that settles in silence
            // leaves a blind user waiting on a reply that already arrived.
            finalizeAnswer(currentCandidateDiv);
            // The status line is NOT written here. `done` says generation
            // finished, which is not the same as the answer having been
            // heard; announcing the mic from this hook is what made the page
            // claim the candidate could talk while it was still speaking.
            // checkAllDone() announces it, from the queue and the player.
            //
            // The turn-number bookkeeping, in one place and guarded so it moves
            // the counter exactly once. It lives on the settler -- as a property
            // rather than as work this hook performs -- because a fatal `error`
            // settles the turn BEFORE its `done` is read. The server emits the
            // two back to back with no await between them, so one network read
            // usually carries both, and settling on the error runs this with no
            // turn to report; the number arrives one line later. Publishing from
            // here alone is what made the counter skip a turn the candidate had
            // just heard, because the write it wanted to count did happen.
            turn.publishTurn = () => {
                const settledTurn = turnState.last();
                if (settledTurn === null || settledTurn === publishedTurnNumber) {
                    return;
                }
                publishedTurnNumber = settledTurn;
                updateTurnCount(settledTurn + 1);
                const contextTurn = turnState.contextTurn();
                // The provenance rides along with the turn, so the panel draws
                // this turn's chips under this turn's claim.
                if (contextTurn !== null) {
                    fetchContext(contextTurn, turnState.contextGrounding());
                }
            };
            turn.publishTurn();
        },
    });

    try {
        // writeOncePolicy: this POST persists a turn, so an ambiguous network
        // failure is not replayed behind the user's back. A non-OK status that
        // the policy calls transient still is retried — the server declined
        // before the pipeline ran, so no turn was written.
        const res = await fetchWithBackoff(
            `${API_BASE}/api/conversation/${conversationId}/message/stream`,
            { method: "POST", body: fd, signal: turnAbortController.signal },
            writeOncePolicy,
        );

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            latencyReadout.firstByte();

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith("data: ")) continue;

                let event;
                try {
                    event = JSON.parse(trimmed.slice(6));
                } catch (_) {
                    continue;
                }

                const type = event.event || "";

                if (type === "transcription") {
                    // Whisper finished: the wait is no longer an upload.
                    narrator.transcribing();
                    addMessage("user", event.data.text);
                } else if (type === "token") {
                    // First LLM token of this turn: the number the candidate
                    // actually perceives, measured rather than asserted.
                    latencyReadout.firstToken();
                    // The answer really is being written now. Showing the
                    // typing bubble any earlier -- it used to go up before
                    // the request was even sent -- claims work that has not
                    // started yet.
                    narrator.generating();
                    showTyping();
                    if (!currentCandidateDiv) {
                        currentCandidateDiv = addMessage("candidate", "");
                        hideTyping();
                    }
                    // Typing animation: append character by character
                    appendTypingText(currentCandidateDiv, event.data.text);
                    scrollToBottom();
                } else if (type === "audio_url") {
                    // The answer is audible from here on.
                    narrator.speaking();
                    // Canonical audio event. Two shapes arrive under this name:
                    // the incremental per-sentence stream carries an explicit
                    // `id`, while the single-file cached/farewell answer does
                    // not. Fall back to the queue cursor so the playback
                    // ordering contract in tryPlayNextChunk() still holds.
                    const id = Number.isFinite(event.data.id)
                        ? event.data.id
                        : nextChunkId;
                    audioQueue.push({ id, url: event.data.url });
                    tryPlayNextChunk();
                } else if (type === "done") {
                    // Before settling: `done` is the only event that names the
                    // committed turn, and the settler reads it on the way out.
                    turnState.commit(event.data);
                    // `incomplete` means the model stopped generating and the
                    // server kept what had already been said. The answer is on
                    // screen and the candidate heard it, so nothing is being
                    // retracted -- but without a mark on the transcript the
                    // half-answer reads as the whole one. Marked here rather
                    // than in the `error` branch because the error bubble
                    // describes the failure while this one labels the answer
                    // beside it, and because `done` is the event that says
                    // which turn is being closed.
                    if (event.data && event.data.incomplete) {
                        markAnswerIncomplete(currentCandidateDiv);
                    }
                    // The `error` that preceded this frame -- and it usually
                    // does, in the same network read -- already settled the turn,
                    // so the settle hook ran with no turn to report. The number
                    // this commit just recorded is the one the counter was
                    // waiting for; publishing it here is what keeps a turn the
                    // candidate heard from being left out of the count.
                    if (turn.publishTurn) turn.publishTurn();
                    // Generation is complete. If audio is still outstanding
                    // this keeps the line on "speaking"; if there was nothing
                    // to play, the turn is genuinely over.
                    narrator.complete();
                    turn.settle("done");
                    if (audioQueue.length === 0 && !isAudioPlaying) {
                        if (isInterviewActive) startListening();
                    }
                } else if (type === "interview_end") {
                    // Terminal event for the farewell path. The payload is
                    // `done`'s payload — same builder, from the turn the DB
                    // committed — so the number is already here when the
                    // settler reads it on the way out. Commit first, then
                    // settle, exactly as the `done` branch does: that is what
                    // lets both terminal events share one counter call site
                    // in the onSettle hook instead of two copies of the same
                    // arithmetic, which is how they drifted apart once
                    // already. A farewell carries no RAG chunks, so
                    // has_context is false and nothing is requested.
                    turnState.commit(event.data);
                    turn.settle("interview_end");
                    stopInterview();
                } else if (type === "error") {
                    const chunkId = event.data ? event.data.id : undefined;
                    if (
                        typeof chunkId === "number" &&
                        Number.isFinite(chunkId)
                    ) {
                        // Recoverable per-chunk TTS failure: skip past that
                        // chunk instead of aborting the whole stream. The turn
                        // is NOT settled here — more audio and a later `done`
                        // are still expected.
                        // Say so, though: the candidate just heard a gap, and
                        // silence is not an explanation. The turn continues,
                        // so this is a report and not a state change -- the
                        // next audio event puts the line back.
                        narrator.chunkSkipped();
                        console.warn(
                            `TTS chunk ${chunkId} failed server-side — skipping:`,
                            event.data.detail || "TTS synthesis failed",
                        );
                        skippedChunkIds.add(chunkId);
                        advancePastSkippedChunks();
                        tryPlayNextChunk();
                    } else {
                        // Fatal server-side error. Surface it and settle the
                        // turn so the mic comes back and the candidate can
                        // retry or end the interview. This used to throw out
                        // of the read loop into the catch below, which called
                        // stopInterview() — one failed sentence ended the
                        // whole session.
                        const detail =
                            (event.data && event.data.detail) || "Error del servidor";
                        addMessage("error", detail);
                        narrator.failed();
                        turn.settle("error");
                    }
                }
            }
        }

        // Stream EOF. A well-formed stream already settled on `done` or
        // `interview_end`; a truncated one has not, and settling here is what
        // keeps the mic from being stranded.
        turn.settle("eof");
    } catch (e) {
        // The turn was ended on purpose. The abort surfaces here as a rejected
        // read, and reporting it would put the last line of a finished
        // interview into its own transcript: an error the user did not cause,
        // on a session they were told was over. `finally` still runs, so the
        // processing state is released either way.
        if (turnAborted) {
            console.info("SSE stream cancelled: the interview was ended.");
            return;
        }
        console.error("SSE pipeline error:", e);
        addMessage("error", e.message || "Algo salió mal.");
        // Routed through the narrator for the same reason as the SSE error
        // branch: one owner of the line, and a real class name. (It passed
        // boolean `true` here, which setStatus concatenated into the class
        // attribute as the literal class "true" -- so the error styling this
        // call was reaching for never applied. setStatus now normalises the
        // boolean, and applyStatusClasses() is the only writer of the list, so
        // the three remaining `true` call sites cannot drift the same way.)
        narrator.failed();
        // The turn produced no measurable result, so the pill must not keep
        // showing one — including the partial stopwatch.
        latencyReadout.abandon();
        // Transport-level failure (HTTP error, network drop). We do not know
        // what the server did, so the interview cannot be trusted to be in a
        // clean state; end it rather than let the candidate talk into a void.
        //
        // A 413 IS THE EXCEPTION, and it used not to be. The server refused the
        // body on SIZE -- it never ran the pipeline and wrote no turn -- so the
        // conversation on disk is intact and ending the session destroys turns
        // the candidate already completed. The cap in `armRecordingCap` is the
        // real fix, but it cannot be the only one: a browser can encode faster
        // than the budget assumes, and nginx has its own ceiling above ours.
        // So the recovery is here too: say what happened, keep the interview,
        // and let the `finally` hand the mic back.
        if (e.status === 413) {
            setStatus(
                "La grabación era demasiado grande para el servidor (413) — " +
                    "el audio se ha cortado. La entrevista continúa.",
                true,
            );
        } else {
            stopInterview();
        }
    } finally {
        // Backstop: settles if neither a terminal event nor a clean EOF was
        // reached (e.g. the catch above took over).
        turn.settle("eof");
        // Always leaves the measuring state, and keeps a real measurement if
        // one was taken. Runs after the catch, so a failed turn stays blank.
        latencyReadout.settle();
        healthStatus.pause(false);
        isProcessing = false;
        btnMic.disabled = false;
        // The controller has done its job either way, and holding it would abort
        // the NEXT turn's stream the moment END was pressed again.
        turnAbortController = null;
        checkAllDone();
    }
}

// ─── Typing animation ──────────────────────────────────

/**
 * Say, on the answer itself, that the model stopped generating.
 *
 * The error bubble already reports that the response could not be generated,
 * but it describes the failure rather than the answer next to it -- and the
 * answer next to it is what a recruiter reads. A truncated reply presented as
 * a finished one is the dishonesty this page is built to avoid, so the mark
 * lives inside the answer bubble.
 *
 * Inside the bubble rather than after it: `#conversation` is a polite live
 * region, so a note appended here is announced once the bubble is unmuted by
 * finalizeAnswer(). A separate message beside it would also land in the
 * transcript as though it were something the recruiter said.
 *
 * Idempotent. `done` is the only caller and fires once per turn, but a label
 * that could appear twice would read as a rendering fault, and the guard costs
 * one querySelector.
 */
function markAnswerIncomplete(messageDiv) {
    if (!messageDiv) return;

    const bubble = messageDiv.querySelector(".bubble");
    if (!bubble || bubble.querySelector(".answer-incomplete")) return;

    const note = document.createElement("p");
    note.className = "answer-incomplete";
    note.textContent =
        "Respuesta incompleta: el modelo dejó de generar a mitad del turno.";
    bubble.appendChild(note);
}

/**
 * Say a finished answer, exactly once.
 *
 * `#conversation` is a polite live region, which is what makes an arriving
 * answer audible at all. But the answer arrives by being appended to as the LLM
 * streams, and a live region announces every one of those appends -- a
 * five-sentence reply read out as a growing prefix five to ten times. So the
 * candidate bubble is muted with `aria-live="off"` in addMessage(), and this
 * unmutes it when the turn is over and the text is final.
 *
 * The order is the whole trick. A live region announces on a *mutation*, and a
 * mutation made while the region is still muted is a mutation nobody hears, so
 * the attribute has to be lifted before the text is re-committed. Re-committing
 * text that has not changed is exactly that mutation: setting `textContent`
 * replaces the text node, and replacing the node is what the region reports.
 *
 * The cursor is removed explicitly rather than left to that reassignment to
 * sweep up incidentally. It is appended per token and was never removed, so it
 * blinked forever at the end of every answer; the answer being final is the
 * only moment that is correct. Stated, it survives a refactor that changes how
 * the text is written; left implicit, it does not.
 */
function finalizeAnswer(messageDiv) {
    if (!messageDiv) return;

    const bubble = messageDiv.querySelector(".bubble");
    if (!bubble) return;

    const p = bubble.querySelector("p");
    if (!p) return;

    bubble.removeAttribute("aria-live");

    const cursor = p.querySelector(".typing-cursor");
    if (cursor) cursor.remove();

    p.textContent = p.textContent;
}

function appendTypingText(messageDiv, text) {
    const bubble = messageDiv.querySelector(".bubble");
    if (!bubble) return;

    let p = bubble.querySelector("p");
    if (!p) {
        p = document.createElement("p");
        bubble.appendChild(p);
    }

    // Remove existing cursor if any
    const existingCursor = p.querySelector(".typing-cursor");
    if (existingCursor) existingCursor.remove();

    // Append text
    p.textContent += text;

    // Add blinking cursor
    const cursor = document.createElement("span");
    cursor.className = "typing-cursor";
    p.appendChild(cursor);
}

// ─── Context panel ─────────────────────────────────────

/**
 * Which layout the panel is in right now.
 *
 * One breakpoint, read in one place. Above 768px the panel is a column of the
 * grid; at or below it, it is a fixed overlay covering the whole screen. The
 * stylesheet draws that line in its own `@media` block, so this reads the same
 * query -- otherwise the script and the cascade can disagree about where the
 * panel is, and "does an outside click dismiss it" has no honest answer.
 */
function isOverlayLayout() {
    return window.matchMedia("(max-width: 768px)").matches;
}

/**
 * The panel's open/closed state, with one owner.
 *
 * This used to be four `classList.remove("open")` call sites -- the toggle, the
 * close button, the outside click and a timer -- with nothing holding the state,
 * so the sites could disagree and a pending timer could slam a panel the user
 * had opened by hand. Here the state lives in one boolean and only `open`,
 * `close` and `toggle` may move it.
 *
 * Every transition is idempotent: a redundant call reports `false` and writes
 * nothing, so a document-level click handler firing on every click cannot
 * re-apply the DOM state forty times a second.
 *
 * `initialOpen` is a parameter, not a constant, because the honest answer
 * differs by layout -- the rail is visible on a desktop, the overlay is not.
 *
 * Rendering goes through `hooks.onChange`, so this stays pure and testable;
 * see tests/frontend/context_panel.test.mjs.
 */
function createContextPanel(hooks, initialOpen) {
    let isOpen = Boolean(initialOpen);

    function apply(next) {
        if (next === isOpen) return false;
        isOpen = next;
        if (hooks && hooks.onChange) hooks.onChange(isOpen);
        return true;
    }

    // Published at construction so the classes and aria-expanded are correct
    // before anyone has clicked anything.
    if (hooks && hooks.onChange) hooks.onChange(isOpen);

    return {
        /** @returns {boolean} whether the panel is showing. */
        isOpen() {
            return isOpen;
        },
        /** @returns {boolean} whether this call changed anything. */
        open() {
            return apply(true);
        },
        /** @returns {boolean} whether this call changed anything. */
        close() {
            return apply(false);
        },
        /** @returns {boolean} whether this call changed anything. */
        toggle() {
            return apply(!isOpen);
        },
    };
}

/**
 * The single writer of the panel's DOM state.
 *
 * The class, the body class and `aria-expanded` move together, in one place, on
 * purpose: `aria-expanded` is the only thing that tells a screen-reader user
 * whether the panel is showing, and an attribute written anywhere other than
 * beside the class it describes is one edit away from lying.
 */
function applyContextPanelState(isOpen) {
    contextPanel.classList.toggle("open", isOpen);
    document.body.classList.toggle("context-open", isOpen);
    contextToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
}

const contextPanelState = createContextPanel(
    { onChange: applyContextPanelState },
    !isOverlayLayout(),
);

function toggleContextPanel() {
    // Focus deliberately stays on the toggle. This is the ARIA disclosure
    // pattern: the panel is simply the next thing in the tab order, and moving
    // focus out from under the control the user just pressed makes a two-click
    // round trip out of it.
    contextPanelState.toggle();
}

function closeContextPanel() {
    // Hand focus back before the panel leaves, or a keyboard user who just
    // dismissed the overlay is dropped at the top of the document with nothing
    // to say where they went.
    if (contextPanel.contains(document.activeElement)) {
        contextToggle.focus();
    }
    contextPanelState.close();
}

async function fetchContext(turnNumber, grounding) {
    if (!conversationId || turnNumber < 0) return;
    // Not for a turn the user ended. Fetching would be a request the candidate
    // did not ask for, into a rail they just dismissed, to fill a panel they no
    // longer care about. Checked here rather than at the call site because this
    // is the same kind of precondition as the two above it: is there a turn to
    // ask about, and should we be asking?
    if (turnAborted) return;

    let chunks;
    try {
        const res = await fetch(
            `${API_BASE}/api/conversation/${conversationId}/context?turn=${turnNumber}`,
        );
        if (!res.ok) {
            // Say what is true. The comment this replaces said "hide panel" and
            // the code hid nothing: it returned, leaving `#context-content` with
            // whatever `renderContext` wrote for the PREVIOUS turn. So a 404 --
            // which is what a turn number the server does not have produces --
            // left turn N-1's passages on screen inside a panel that now belongs
            // to turn N.
            //
            // That is the worst failure this panel is capable of. The passages
            // look authoritative, they are attributed to the current answer, and
            // they are wrong: a recruiter reads them as this answer's provenance.
            // Nothing on the page says otherwise.
            renderContextUnavailable();
            return;
        }

        chunks = await res.json();
    } catch (e) {
        // The other way to fail, and the likelier one. It warned and left the
        // panel exactly as it was, which is the same stale attribution.
        console.warn("Context fetch failed:", e.message);
        renderContextUnavailable();
        return;
    }

    renderContext(chunks, grounding);

    // Deliberately no timer here.
    //
    // This used to `setTimeout(..., 5000)` and then remove the `open` class,
    // which made the panel a toast: a recruiter could not re-read turn 2's
    // evidence once turn 3 landed, and the pending handle was never stored,
    // so it could also slam a panel the user had opened by hand. No interval
    // is long enough to stop that -- the failure is not the length, it is
    // the panel moving without the user. Evidence is replaced in place in a
    // rail that stays exactly where they put it; on a phone the overlay is
    // opened by the user, when they want it.
}

/**
 * The evidence for this turn could not be retrieved.
 *
 * Deliberately worded differently from `renderContext`'s empty case, because
 * they are different facts. An empty list is the retriever's answer: it looked
 * and found nothing. A failed request means we never found out. They share the
 * `context-empty` class so the panel reads as empty either way, but a reader
 * must not be able to conclude "nothing was retrieved" from a request that was
 * never served.
 *
 * It also never leaves the previous turn's passages in place. Clearing the panel
 * is the load-bearing half; the wording is what keeps the emptiness honest.
 */
function renderContextUnavailable() {
    contextContent.innerHTML =
        '<p class="context-empty context-unavailable">No se pudo recuperar el contexto de esta respuesta</p>';
}

/**
 * Draw the evidence for a turn, saying which of the two things it is.
 *
 * `grounding` comes from the `done` payload and is the difference between
 * "the model wrote the answer from these" and "these resemble the question".
 * A FAQ cache hit is the second: the answer is a fixed string from
 * `response_cache.py` and the retrieval existed only to fill this panel. The
 * two used to be presented identically, and the surrounding comment claimed
 * they were always the first, which for roughly 18 of the most common
 * interview questions was a false provenance claim in the direction a
 * recruiter acts on.
 *
 * The passages are shown either way. Hiding them on a cache hit was the other
 * option and it is worse: `has_context` drives whether the panel REFRESHES, so
 * a cache hit that suppressed the request would leave the previous turn's
 * passages standing inside a panel that now belongs to this one.
 *
 * An unrecognised `grounding` value is treated as NOT grounded. This function
 * decides what the page says, and a value it cannot read must not become the
 * stronger claim.
 */
function renderContext(chunks, grounding) {
    if (!chunks || chunks.length === 0) {
        contextContent.innerHTML =
            '<p class="context-empty">No se recuperó contexto para esta respuesta</p>';
        return;
    }

    // The one line that makes the panel honest, and the reason the word
    // "source" is not enough on its own elsewhere in this file: a chip labelled
    // "Fuente:" is a claim of provenance, which is exactly what is missing here.
    //
    // Compared against the bare string, not GROUNDING.GROUNDED: see the note on
    // that constant. The ABSENT case is grounded and only the absent case is:
    // /api/health-grade honesty cuts both ways, and an older server sends no
    // field on turns the RAG really did write. A value that IS present and is
    // not the one this build knows falls on the honest side, because this
    // function decides what the page says.
    const related = grounding !== undefined && grounding !== "grounded";
    const note = related
        ? '<p class="context-empty context-related">Estas passagens se ' +
          "relacionan con tu pregunta, pero esta respuesta viene de la " +
          "caché de preguntas frecuentes: no se construyó a partir de " +
          "ellas.</p>"
        : "";

    // A native <button>, not a div with role="button".
    //
    // The chip used to be `<div onclick="toggleChunk(this)">`, which gives a
    // mouse user a target and everyone else nothing: a div is not focusable,
    // cannot be reached with Tab, cannot be activated with Enter or Space, and
    // draws no focus ring. These chips are the evidence the whole panel exists
    // to show, so re-declaring `role="button"` would be the fix that looks
    // right and is not: it changes what is announced and leaves the
    // focusability, the key handling and the ring still missing. The platform
    // provides all of it for free, so the platform provides all of it.
    //
    // Every descendant is a <span> because a <button> may contain only
    // phrasing content, and the revealed passage this used to wrap in a
    // <div><p> is flow content. `display: block` in the stylesheet puts it back
    // on its own row.
    contextContent.innerHTML =
        note +
        chunks
            .map(
                (chunk, i) => `
        <button type="button" class="chunk-pill" data-index="${i}"
                aria-expanded="false" aria-controls="chunk-detail-${i}">
            <span class="chunk-score">${chunk.score.toFixed(2)}</span>
            <span class="chunk-preview">${escapeHtml(chunk.text.substring(0, 100))}${chunk.text.length > 100 ? "…" : ""}</span>
            <span class="chunk-full" id="chunk-detail-${i}">
                <span>${escapeHtml(chunk.text)}</span>
                <span class="chunk-source">Fuente: ${escapeHtml(chunk.source)}</span>
            </span>
        </button>
    `,
            )
            .join("");
}

/**
 * Expand or collapse one evidence chip.
 *
 * The class drives the visual and `aria-expanded` drives the announcement, so
 * both are written from the same decision on every call: a control that flips
 * only one of them lies to half its users.
 */
function toggleChunk(el) {
    const expanded = !el.classList.contains("expanded");
    el.classList.toggle("expanded", expanded);
    el.setAttribute("aria-expanded", expanded ? "true" : "false");
}

// ─── Helpers ───────────────────────────────────────────

// ─── Status line ─────────────────────────────────────────

/**
 * Is the last thing written to the status line a failure?
 *
 * Module state, not a parameter, because the two writers of the class list run
 * at different times: the failure paths call `setStatus(msg, true)` and then
 * `setState(...)` on the next line, and the state change must not forget that
 * the message on screen is an error.
 */
let statusIsError = false;

/**
 * The single writer of #status's class list.
 *
 * This attribute used to have two owners. `setStatus` wrote
 * `"hud-status" + (className ? " " + className : "")`, which for a boolean
 * `true` produced the class `true` -- a JavaScript value that had leaked into a
 * class attribute and that no stylesheet has a rule for. `setState` then ran
 * `statusEl.className = "hud-status"` on the very next line and threw away
 * whatever had been written, so even a correctly spelled error class would have
 * lasted zero frames. Both faults are the same fault: two functions writing
 * one attribute without knowing about each other.
 *
 * So there is one function, and it is handed both facts -- the state the
 * machine is in, and whether the line is currently reporting a failure -- and
 * derives the whole list. `hud-status` is the base, the state is added when it
 * is not idle, and `error` is added when the message is a failure. There is
 * nothing for a caller to get wrong, because no caller names a class any more.
 */
function applyStatusClasses(state, isError) {
    const classes = ["hud-status"];
    if (state && state !== "idle") classes.push(state);
    if (isError) classes.push("error");
    statusEl.className = classes.join(" ");
}

/**
 * Write the status line.
 *
 * `className` is the failure flag, and it accepts two spellings: `true` and
 * `"error"`. Both mean the same thing and are normalised here, once, so no call
 * site has to know which one the stylesheet has a rule for. The `true` spelling
 * is what three call sites pass; `"error"` is what the turn narrator passes.
 * A new status with no flag clears a previous error, because a stale error is
 * its own kind of lie.
 */
function setStatus(text, className) {
    statusEl.textContent = text;
    statusIsError = className === true || className === "error";
    applyStatusClasses(currentState, statusIsError);
}

function scrollToBottom() {
    if (!isUserScrolledUp) {
        conversation.scrollTop = conversation.scrollHeight;
    }
}

function showTyping() {
    if (document.querySelector(".typing-indicator")) return;
    const div = document.createElement("div");
    div.className = "typing-indicator";
    // #conversation is a polite live region, so everything appended into it is
    // announced. This one is three empty dots in a decorative avatar: it has no
    // text to read, and it says nothing #status has not already said one line
    // above. Left live, it adds an announcement with no content to every turn.
    //
    // `aria-hidden="true"` removes the element from the accessibility tree
    // ENTIRELY -- not from the live region while remaining visible to a screen
    // reader, which is what this comment used to claim. It is still the right
    // tool: the alternative is an announcement with nothing in it. The cost is
    // that the element is also absent from the accessibility tree for anyone
    // exploring it, which costs nothing for a decorative ellipsis that says
    // nothing. Anything that puts real text in here has to be reconsidered --
    // see tests/frontend/announcements.test.mjs.
    div.setAttribute("aria-hidden", "true");
    div.innerHTML = `
        <div class="avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
            </svg>
        </div>
        <div class="typing-bubble">
            <span class="dot"></span>
            <span class="dot"></span>
            <span class="dot"></span>
        </div>`;
    conversation.appendChild(div);
    scrollToBottom();
}

function hideTyping() {
    const el = document.querySelector(".typing-indicator");
    if (el) el.remove();
}

function addMessage(type, text) {
    const div = document.createElement("div");
    div.className = `message ${type}`;

    if (type === "user" || type === "candidate") {
        const avatar = document.createElement("div");
        avatar.className = `avatar ${type}-avatar`;
        avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
            <circle cx="12" cy="7" r="4"/>
        </svg>`;

        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.innerHTML = `<p>${escapeHtml(text || "")}</p>`;

        if (type === "candidate") {
            // The only message that streams. `#conversation` is a polite live
            // region, so without this the answer is announced once per LLM
            // token; finalizeAnswer() unmutes it when the turn is over. Every
            // other message type is written in one go and is announced as
            // written -- including the user's own question, which they need
            // echoed back.
            bubble.setAttribute("aria-live", "off");
            div.appendChild(avatar);
            div.appendChild(bubble);
        } else {
            div.appendChild(bubble);
            div.appendChild(avatar);
        }
    } else {
        div.innerHTML = `<p>${escapeHtml(text || "")}</p>`;
    }

    conversation.appendChild(div);
    scrollToBottom();
    return div;
}

function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

// ─── Bootstrap ─────────────────────────────────────────

init();
