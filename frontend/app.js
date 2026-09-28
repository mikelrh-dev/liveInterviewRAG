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
let audioBlocked = false;

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

// VAD state (uses same analyser)
let vadAnimationId = null;
let silenceStart = null;
let hasSpoken = false;
const SILENCE_TIMEOUT_MS = 1200;
const RMS_THRESHOLD = 0.015;

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

// Audio queue
let audioQueue = [];
let nextChunkId = 0;
let skippedChunkIds = new Set();
let isAudioPlaying = false;
let allChunksReceived = false;
// The narrator driving the status line for the turn in flight. Defaults to a
// no-op so the playback teardown can fire outside a turn without a guard.
let turnNarrator = createTurnNarrator();

// Typing animation
const typingIntervals = [];

// ─── Sidebar data population ─────────────────────────────

let sessionStartTime = null;

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

        const chunks = Number(payload.rag_chunks);
        if (!Number.isFinite(chunks) || chunks <= 0) {
            problems.push("RAG sin índice");
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
 * - VU meter is driven by mic RMS via startVisualizationLoop
 * - Session ID and turn count update when interview starts
 */
async function populateStaticSidebar() {
    try {
        const res = await fetch(`${API_BASE}/api/config`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const cfg = await res.json();
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
 */
function startSessionTimer() {
    const el = document.getElementById("sidebar-timer");
    if (!el) return;
    if (!sessionStartTime) sessionStartTime = Date.now();
    setInterval(() => {
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

    // Not accepted yet: gate the mic and show the overlay.
    btnMic.disabled = true;
    disclaimerOverlay.classList.remove("hidden");
    disclaimerAccept.addEventListener("click", () => {
        try {
            localStorage.setItem(DISCLAIMER_KEY, "1");
        } catch (e) {
            // Persist best-effort; still unlock in-session.
        }
        disclaimerOverlay.classList.add("hidden");
        btnMic.disabled = false;
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
        audioBlocked = true;
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
        audioBlocked = false;
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
        startVisualizationLoop();
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

function startVisualizationLoop() {
    function loop() {
        waveformAnimationId = requestAnimationFrame(loop);

        // Mic RMS (shared by orb and VU meter)
        let micVolume = 0;
        if (analyserNode) {
            const timeData = new Uint8Array(analyserNode.fftSize);
            analyserNode.getByteTimeDomainData(timeData);
            let sum = 0;
            for (let i = 0; i < timeData.length; i++) {
                const v = (timeData[i] - 128) / 128;
                sum += v * v;
            }
            const rms = Math.sqrt(sum / timeData.length);
            micVolume = Math.min(1, (rms / 0.15) ** 0.7);

            // Update orb
            if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
                window.AvatarOrb.setVolume(micVolume);
            }
        }

        // TTS RMS (for fake-sync)
        let ttsVolume = 0;
        if (ttsAnalyser) {
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
            window.AvatarOrb.setBlend(ttsVolume);
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
    }

    loop();
}

// ─── State machine ─────────────────────────────────────

function setState(state) {
    document.body.dataset.state = state;
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

    // Update status text class
    statusEl.className = "hud-status";
    if (state !== "idle") {
        statusEl.classList.add(state);
    }

    // Avatar video crossfade: show talking when speaking, neutral otherwise
    if (avatarTalkingVideo) {
        if (state === "speaking") {
            avatarTalkingVideo.currentTime = 0.3;
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
    btnMic.classList.add("active");
    micIcon.classList.add("hidden");
    stopIconEl.classList.remove("hidden");
    setState("listening");

    startListening();
}

function stopInterview() {
    isInterviewActive = false;
    if (isRecording) stopRecording();

    if (mediaStream) {
        mediaStream.getTracks().forEach((t) => t.stop());
        mediaStream = null;
    }

    btnMic.classList.remove("active");
    micIcon.classList.remove("hidden");
    stopIconEl.classList.add("hidden");
    setState("idle");
    setStatus("Entrevista finalizada");
    addMessage("system", "Entrevista finalizada.");
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
            mediaStream.getTracks().forEach((t) => t.stop());
            return;
        }

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: selectedMimeType,
            audioBitsPerSecond: 128000,
        });

        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };
        mediaRecorder.onstop = () => {
            stopVad();
            processRecordingStream();
        };

        mediaRecorder.start();
        isRecording = true;
        hasSpoken = false;
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
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive")
        mediaRecorder.stop();
    isRecording = false;
}

// ─── VAD ───────────────────────────────────────────────

function startVad() {
    if (!analyserNode) return;
    silenceStart = null;
    hasSpoken = false;
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

    if (rms >= RMS_THRESHOLD) {
        hasSpoken = true;
        silenceStart = null;
    } else if (hasSpoken) {
        if (silenceStart === null) silenceStart = Date.now();
        else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            setStatus("Procesando…");
            setState("processing");
            stopRecording();
            return;
        }
    }

    vadAnimationId = requestAnimationFrame(vadLoop);
}

function stopVad() {
    if (vadAnimationId) {
        cancelAnimationFrame(vadAnimationId);
        vadAnimationId = null;
    }
    silenceStart = null;
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

    // Connect to TTS analyser for fake-sync (only if audioContext is available)
    if (audioContext && ttsAnalyser) {
        try {
            const source = audioContext.createMediaElementSource(audio);
            source.connect(ttsAnalyser);
        } catch (e) {
            // Some browsers throw if the element is already connected; ignore
        }
    }

    audio.addEventListener(
        "ended",
        () => {
            nextChunkId++;
            isAudioPlaying = false;
            removeAudioIndicator();
            tryPlayNextChunk();
            checkAllDone();
        },
        { once: true },
    );
    audio.addEventListener(
        "error",
        () => {
            console.error("Audio playback error for chunk", chunk.id);
            nextChunkId++;
            isAudioPlaying = false;
            removeAudioIndicator();
            tryPlayNextChunk();
            checkAllDone();
        },
        { once: true },
    );
    ensureAudioContext().then(() =>
        audio.play().catch((e) => {
            console.error("Audio play() failed:", e);
            nextChunkId++;
            isAudioPlaying = false;
            removeAudioIndicator();
            tryPlayNextChunk();
            checkAllDone();
        }),
    );
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
 *   commit: (data?: {n?: number, has_context?: boolean}) => number|null,
 *   last: () => number|null,
 *   contextTurn: () => number|null,
 * }}
 */
function createTurnState() {
    let lastTurnNumber = null;
    let contextTurnNumber = null;
    return {
        reset() {
            lastTurnNumber = null;
            contextTurnNumber = null;
        },
        /**
         * Record the turn the server reported as committed.
         *
         * @param {{n?: number, has_context?: boolean}} [data] `done` payload.
         * @returns {number|null} the committed turn, or null if it named none.
         */
        commit(data) {
            if (!data || !Number.isInteger(data.n) || data.n < 0) return null;
            lastTurnNumber = data.n;
            // Only a turn the server says has context is worth requesting.
            // Replacing rather than keeping the previous value stops a stale
            // turn from being requested after a context-free one.
            contextTurnNumber = data.has_context ? data.n : null;
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
    };
}

const turnState = createTurnState();

async function processRecordingStream() {
    if (audioChunks.length === 0) {
        // Nothing captured (e.g. instant stop): release the processing
        // state so body[data-state] doesn't strand the mic on amber.
        setState("idle");
        return;
    }
    isProcessing = true;
    btnMic.disabled = true;
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
            const settledTurn = turnState.last();
            if (settledTurn !== null) {
                updateTurnCount(settledTurn + 1);
                const contextTurn = turnState.contextTurn();
                if (contextTurn !== null) fetchContext(contextTurn);
            }
            // The status line is NOT written here. `done` says generation
            // finished, which is not the same as the answer having been
            // heard; announcing the mic from this hook is what made the page
            // claim the candidate could talk while it was still speaking.
            // checkAllDone() announces it, from the queue and the player.
        },
    });

    try {
        // writeOncePolicy: this POST persists a turn, so an ambiguous network
        // failure is not replayed behind the user's back. A non-OK status that
        // the policy calls transient still is retried — the server declined
        // before the pipeline ran, so no turn was written.
        const res = await fetchWithBackoff(
            `${API_BASE}/api/conversation/${conversationId}/message/stream`,
            { method: "POST", body: fd },
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
        console.error("SSE pipeline error:", e);
        addMessage("error", e.message || "Algo salió mal.");
        // Routed through the narrator for the same reason as the SSE error
        // branch: one owner of the line, and a real class name. (It passed
        // boolean `true` here, which setStatus concatenated into the class
        // attribute as the literal class "true" -- so the error styling this
        // call was reaching for never applied.)
        narrator.failed();
        // The turn produced no measurable result, so the pill must not keep
        // showing one — including the partial stopwatch.
        latencyReadout.abandon();
        // Transport-level failure (HTTP error, network drop). We do not know
        // what the server did, so the interview cannot be trusted to be in a
        // clean state; end it rather than let the candidate talk into a void.
        stopInterview();
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
        checkAllDone();
    }
}

// ─── Typing animation ──────────────────────────────────

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

async function fetchContext(turnNumber) {
    if (!conversationId || turnNumber < 0) return;

    try {
        const res = await fetch(
            `${API_BASE}/api/conversation/${conversationId}/context?turn=${turnNumber}`,
        );
        if (!res.ok) {
            // Context endpoint fails silently — hide panel, no error
            return;
        }

        const chunks = await res.json();
        renderContext(chunks);

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
    } catch (e) {
        // Silently fail — interview unaffected
        console.warn("Context fetch failed:", e.message);
    }
}

function renderContext(chunks) {
    if (!chunks || chunks.length === 0) {
        contextContent.innerHTML =
            '<p class="context-empty">No se recuperó contexto para esta respuesta</p>';
        return;
    }

    // A native <button>, not a div with role="button".
    //
    // The chip used to be `<div onclick="toggleChunk(this)">`, which gives a
    // mouse user a target and everyone else nothing: a div is not focusable,
    // cannot be reached with Tab, cannot be activated with Enter or Space, and
    // draws no focus ring. These chips are the credibility argument of the
    // whole panel -- the passages the answer was actually built from -- so
    // re-declaring `role="button"` would be the fix that looks right and is
    // not: it changes what is announced and leaves the focusability, the key
    // handling and the ring still missing. The platform provides all of it for
    // free, so the platform provides all of it.
    //
    // Every descendant is a <span> because a <button> may contain only
    // phrasing content, and the revealed passage this used to wrap in a
    // <div><p> is flow content. `display: block` in the stylesheet puts it back
    // on its own row.
    contextContent.innerHTML = chunks
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

function setStatus(text, className) {
    statusEl.textContent = text;
    statusEl.className = "hud-status" + (className ? " " + className : "");
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
