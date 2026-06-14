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

const API_BASE = '';
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
let audioBlocked = false;

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
let currentState = 'idle'; // idle | listening | speaking | processing
let waveformBars = [];
let waveformAnimationId = null;

// DOM
const btnMic = document.getElementById('btn-mic');
const statusEl = document.getElementById('status');
const conversation = document.getElementById('conversation');
const micIcon = btnMic.querySelector('.mic-icon');
const stopIconEl = btnMic.querySelector('.stop-icon');
const orbitalRing = document.getElementById('orbital-ring');
const waveformSvg = document.getElementById('waveform');
const contextToggle = document.getElementById('context-toggle');
const contextPanel = document.getElementById('context-panel');
const contextClose = document.getElementById('context-close');
const contextContent = document.getElementById('context-content');
const audioOverlay = document.getElementById('audio-blocked-overlay');
const avatarNeutralVideo = document.getElementById('avatar-neutral-video');
const avatarTalkingVideo = document.getElementById('avatar-talking-video');

// Current candidate message
let currentCandidateDiv = null;

// Audio queue
let audioQueue = [];
let nextChunkId = 0;
let isAudioPlaying = false;
let allChunksReceived = false;

// Typing animation
let typingIntervals = [];

// ─── Sidebar data population ─────────────────────────────

let sessionStartTime = null;

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

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
        if (cfg.tts_voice)   setText('sidebar-tts',    `TTS: ${cfg.tts_voice}`);
        if (cfg.stt_model)   setText('sidebar-stt',    `STT: ${cfg.stt_model} (${cfg.stt_device || 'cpu'})`);
        if (cfg.llm_model)   setText('sidebar-llm',    `LLM: ${cfg.llm_model.split('/').pop()}`);
        if (cfg.google_model) setText('sidebar-google', `Google: ${cfg.google_model}`);
    } catch (e) {
        console.warn('Could not load /api/config:', e);
        setText('sidebar-tts', 'TTS: —');
        setText('sidebar-stt', 'STT: —');
        setText('sidebar-llm', 'LLM: —');
        setText('sidebar-google', 'Google: —');
    }
}

/**
 * Live session timer. Starts at 00:00, ticks every second.
 * Resets whenever a new conversation is created.
 */
function startSessionTimer() {
    const el = document.getElementById('sidebar-timer');
    if (!el) return;
    if (!sessionStartTime) sessionStartTime = Date.now();
    setInterval(() => {
        const s = Math.floor((Date.now() - sessionStartTime) / 1000);
        const mm = String(Math.floor(s / 60)).padStart(2, '0');
        const ss = String(s % 60).padStart(2, '0');
        el.textContent = `⏱ ${mm}:${ss}`;
    }, 1000);
}

/**
 * Update session ID and turn count when a new interview starts.
 * Called from startInterview() after the conversation is created.
 */
function updateSessionInfo(conversationId) {
    sessionStartTime = Date.now();
    const shortId = conversationId ? conversationId.slice(0, 6) : '---';
    setText('sidebar-session-id', `ID: ${shortId}`);
    setText('sidebar-turns', 'Turnos: 0');
}

function updateTurnCount(turnCount) {
    setText('sidebar-turns', `Turnos: ${turnCount}`);
}

// Cached VU bar elements (lazy)
let vuBarsCache = null;
function getVuBars() {
    if (!vuBarsCache) {
        vuBarsCache = document.querySelectorAll('#sidebar-vu .vu-bar');
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
            bar.classList.add('active');
        } else {
            bar.classList.remove('active');
        }
    });
}

// ─── Initialization ────────────────────────────────────

function init() {
    initWaveformBars();
    initAvatarOrb();
    populateStaticSidebar();
    startSessionTimer();

    // Smart scroll
    conversation.addEventListener('scroll', () => {
        const threshold = 50;
        isUserScrolledUp = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight > threshold;
    });

    // Sidebar End Session button
    const endBtn = document.getElementById('sidebar-end-btn');
    if (endBtn) {
        endBtn.addEventListener('click', () => {
            if (isInterviewActive) {
                stopInterview();
                setText('sidebar-session-id', 'ID: ---');
                setText('sidebar-turns', 'Turnos: 0');
                sessionStartTime = null;
                startSessionTimer();  // reset to 00:00
            }
        });
    }

    // Context panel toggle
    contextToggle.addEventListener('click', toggleContextPanel);
    contextClose.addEventListener('click', () => contextPanel.classList.remove('open'));

    // Close context panel on outside click
    document.addEventListener('click', (e) => {
        if (contextPanel.classList.contains('open') &&
            !contextPanel.contains(e.target) &&
            e.target !== contextToggle &&
            !contextToggle.contains(e.target)) {
            contextPanel.classList.remove('open');
        }
    });

    // Audio blocked overlay — resume on click
    audioOverlay.addEventListener('click', resumeAudioContext);

    // Mic button
    btnMic.addEventListener('click', toggleInterview);

    addMessage('system', 'Presioná el micrófono para empezar.');
    setStatus('Preparado');
}

/**
 * Initialize AudioContext and AnalyserNode. Handles autoplay blocking.
 */
async function initAudio() {
    if (audioContext) return;

    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        if (audioContext.state === 'suspended') {
            throw new Error('AudioContext blocked');
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
        console.warn('AudioContext init failed:', e.message);
        audioBlocked = true;
        audioOverlay.classList.remove('hidden');
    }
}

/**
 * Resume AudioContext on user interaction.
 */
async function resumeAudioContext() {
    if (audioContext && audioContext.state === 'suspended') {
        await audioContext.resume();
    }
    if (audioContext && audioContext.state === 'running') {
        audioBlocked = false;
        audioOverlay.classList.add('hidden');
    }
}

// ─── Particles ─────────────────────────────────────────

function initParticles() {
    if (typeof tsParticles === 'undefined') {
        console.warn('tsparticles not loaded — skipping particles');
        return;
    }

    tsParticles.load('particles-bg', {
        fullScreen: { enable: false },
        particles: {
            number: { value: 60, density: { enable: true, value_area: 800 } },
            color: { value: '#00d4ff' },
            opacity: { value: 0.15, random: true },
            size: { value: 2, random: true },
            move: {
                enable: true,
                speed: 0.5,
                direction: 'top',
                out_mode: 'out',
            },
            line_linked: {
                enable: true,
                distance: 100,
                color: '#00d4ff',
                opacity: 0.1,
                width: 0.5,
            },
        },
        interactivity: {
            events: {
                onhover: { enable: false },
                onclick: { enable: true, mode: 'repulse' },
            },
            modes: {
                repulse: { distance: 100, duration: 0.4 },
            },
        },
    });
}

// ─── Avatar Orb ────────────────────────────────────────

function initAvatarOrb() {
    if (typeof window.AvatarOrb === 'undefined') {
        console.warn('AvatarOrb not available');
        return;
    }

    const success = window.AvatarOrb.init();
    if (success) {
        startVisualizationLoop();
    }
}

// ─── Waveform bars ─────────────────────────────────────

function initWaveformBars() {
    const barCount = 32;
    const barWidth = 4;
    const gap = 2;

    for (let i = 0; i < barCount; i++) {
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', i * (barWidth + gap));
        rect.setAttribute('y', 20);
        rect.setAttribute('width', barWidth);
        rect.setAttribute('height', 0);
        rect.setAttribute('rx', '1');
        waveformSvg.appendChild(rect);
    }
}

function updateWaveform() {
    if (!analyserNode || currentState === 'idle' || currentState === 'processing') {
        waveformSvg.classList.remove('visible');
        return;
    }

    waveformSvg.classList.add('visible');
    analyserNode.getByteFrequencyData(waveformBars);

    const rects = waveformSvg.querySelectorAll('rect');
    for (let i = 0; i < rects.length && i < waveformBars.length; i++) {
        const value = waveformBars[i];
        const height = Math.max(1, (value / 255) * 36);
        rects[i].setAttribute('height', height);
        rects[i].setAttribute('y', 20 - height / 2);
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
            micVolume = Math.min(1, Math.pow(rms / 0.15, 0.7));

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
            ttsVolume = Math.min(1, Math.pow(rms / 0.10, 0.7));
        }

        // Drive video crossfade from TTS volume — continuous blend, no hard cut
        if (window.AvatarOrb && typeof window.AvatarOrb.setBlend === 'function') {
            window.AvatarOrb.setBlend(ttsVolume);
        }

        // Drive talking video playback rate (new)
        if (avatarTalkingVideo && currentState === 'speaking') {
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
    currentState = state;

    // Update ring
    orbitalRing.className = 'orbital-ring';
    if (state !== 'idle') {
        orbitalRing.classList.add(`state-${state}`);
    }

    // Update orb state
    if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
        window.AvatarOrb.setState(state);
    }

    // Energy field boost when AI is speaking
    if (window.AvatarOrb && window.AvatarOrb.isInitialized()) {
        if (state === 'speaking') {
            window.AvatarOrb.boost(1.5);  // more intense
        } else {
            window.AvatarOrb.boost(1.0);  // normal
        }
    }

    // Update status text class
    statusEl.className = 'hud-status';
    if (state !== 'idle') {
        statusEl.classList.add(state);
    }

    // Avatar video crossfade: show talking when speaking, neutral otherwise
    if (avatarTalkingVideo) {
        if (state === 'speaking') {
            avatarTalkingVideo.currentTime = 0.3;
            avatarTalkingVideo.play().catch(() => {});
            avatarTalkingVideo.classList.add('active');
        } else {
            avatarTalkingVideo.classList.remove('active');
            // After crossfade completes, pause the talking video to save CPU
            setTimeout(() => {
                if (currentState !== 'speaking') {
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

async function startInterview() {
    if (isInterviewActive) return;

    // Init audio on first user interaction
    await initAudio();

    try {
        const res = await fetch(`${API_BASE}/api/conversation`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        conversationId = data.conversation_id;
        updateSessionInfo(conversationId);
        addMessage('system', data.welcome_message);
    } catch (e) {
        console.error('Failed to create conversation:', e);
        setStatus('Error de conexión — recarga la página', true);
        return;
    }

    isInterviewActive = true;
    btnMic.classList.add('active');
    micIcon.classList.add('hidden');
    stopIconEl.classList.remove('hidden');
    setState('listening');

    startListening();
}

function stopInterview() {
    isInterviewActive = false;
    if (isRecording) stopRecording();

    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }

    btnMic.classList.remove('active');
    micIcon.classList.remove('hidden');
    stopIconEl.classList.add('hidden');
    setState('idle');
    setStatus('Entrevista finalizada');
    addMessage('system', 'Entrevista finalizada.');
}

// ─── Recording + VAD ───────────────────────────────────

function startListening() {
    if (!isInterviewActive || isProcessing || isRecording) return;
    startRecording();
}

async function startRecording() {
    try {
        if (!mediaStream || mediaStream.getTracks().some(t => t.readyState === 'ended')) {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }

        // Connect to analyser for visualization
        if (audioContext && analyserNode && mediaStream) {
            const source = audioContext.createMediaStreamSource(mediaStream);
            source.connect(analyserNode);
        }

        audioChunks = [];

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus' : 'audio/webm',
            audioBitsPerSecond: 128000,
        });

        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            stopVad();
            processRecordingStream();
        };

        mediaRecorder.start();
        isRecording = true;
        hasSpoken = false;
        startVad();
        setStatus('Escuchando...');
        setState('listening');

    } catch (e) {
        console.error('Mic denied:', e);
        setStatus('Acceso al micrófono denegado — revisá permisos del navegador', true);
        setState('idle');
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
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
            setStatus('Procesando...');
            setState('processing');
            stopRecording();
            return;
        }
    }

    vadAnimationId = requestAnimationFrame(vadLoop);
}

function stopVad() {
    if (vadAnimationId) { cancelAnimationFrame(vadAnimationId); vadAnimationId = null; }
    silenceStart = null;
}

// ─── Audio queue ───────────────────────────────────────

function resetAudioQueue() {
    audioQueue = [];
    nextChunkId = 0;
    isAudioPlaying = false;
    allChunksReceived = false;
}

function addAudioIndicator() {
    if (!currentCandidateDiv) return;
    const bubble = currentCandidateDiv.querySelector('.bubble');
    if (!bubble || bubble.querySelector('.audio-indicator')) return;
    const indicator = document.createElement('div');
    indicator.className = 'audio-indicator';
    for (let i = 0; i < 5; i++) {
        const bar = document.createElement('span');
        bar.className = 'bar';
        indicator.appendChild(bar);
    }
    bubble.appendChild(indicator);
}

function removeAudioIndicator() {
    if (currentCandidateDiv) {
        const indicator = currentCandidateDiv.querySelector('.audio-indicator');
        if (indicator) indicator.remove();
    }
}

function tryPlayNextChunk() {
    if (isAudioPlaying) return;

    const idx = audioQueue.findIndex(c => c.id === nextChunkId);
    if (idx === -1) return;

    const chunk = audioQueue.splice(idx, 1)[0];
    isAudioPlaying = true;
    setStatus('Reproduciendo...');
    setState('speaking');
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

    audio.addEventListener('ended', () => {
        nextChunkId++;
        isAudioPlaying = false;
        removeAudioIndicator();
        tryPlayNextChunk();
        checkAllDone();
    }, { once: true });
    audio.addEventListener('error', () => {
        console.error('Audio playback error for chunk', chunk.id);
        nextChunkId++;
        isAudioPlaying = false;
        removeAudioIndicator();
        tryPlayNextChunk();
        checkAllDone();
    }, { once: true });
    audio.play().catch(e => {
        console.error('Audio play() failed:', e);
        nextChunkId++;
        isAudioPlaying = false;
        removeAudioIndicator();
        tryPlayNextChunk();
        checkAllDone();
    });
}

function checkAllDone() {
    if (allChunksReceived && audioQueue.length === 0 && !isAudioPlaying) {
        if (isInterviewActive) startListening();
    }
}

// ─── Fetch with backoff ────────────────────────────────

/**
 * Fetch with exponential backoff.
 * Retries: 1s, 2s, 4s, 8s, max 30s.
 */
async function fetchWithBackoff(url, options, maxRetries = 5) {
    let delay = 1000;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            const res = await fetch(url, options);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res;
        } catch (e) {
            if (attempt === maxRetries) throw e;
            setStatus('Sin conexión — reintentando...', 'error');
            await new Promise(r => setTimeout(r, delay));
            delay = Math.min(delay * 2, 30000);
        }
    }
}

// ─── SSE pipeline ──────────────────────────────────────

async function processRecordingStream() {
    if (audioChunks.length === 0) return;
    isProcessing = true;
    btnMic.disabled = true;
    setStatus('Enviando audio...');
    setState('processing');
    resetAudioQueue();
    currentCandidateDiv = null;
    showTyping();

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const fd = new FormData();
    fd.append('audio', blob, 'recording.webm');

    let fullText = '';
    let lastTurnNumber = -1;

    try {
        const res = await fetchWithBackoff(
            `${API_BASE}/api/conversation/${conversationId}/message/stream`,
            { method: 'POST', body: fd },
        );

        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try { detail = (await res.json()).detail || detail; } catch (_) {}
            throw new Error(detail);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith('data: ')) continue;

                let event;
                try { event = JSON.parse(trimmed.slice(6)); } catch (_) { continue; }

                const type = event.event || '';

                if (type === 'transcription') {
                    addMessage('user', event.data.text);
                } else if (type === 'token') {
                    fullText += event.data.text;
                    if (!currentCandidateDiv) {
                        currentCandidateDiv = addMessage('candidate', '');
                        hideTyping();
                    }
                    // Typing animation: append character by character
                    appendTypingText(currentCandidateDiv, event.data.text);
                    scrollToBottom();
                } else if (type === 'audio_chunk') {
                    audioQueue.push({ id: event.data.id, url: event.data.url });
                    tryPlayNextChunk();
                } else if (type === 'done') {
                    allChunksReceived = true;
                    // Update sidebar turn count
                    updateTurnCount(Math.max(0, getCurrentTurnNumber() + 1));
                    // Fetch context for this turn
                    lastTurnNumber = getCurrentTurnNumber();
                    if (lastTurnNumber >= 0) {
                        fetchContext(lastTurnNumber);
                    }
                    if (audioQueue.length === 0 && !isAudioPlaying) {
                        if (isInterviewActive) startListening();
                    }
                } else if (type === 'interview_end') {
                    if (currentCandidateDiv) {
                        const indicator = currentCandidateDiv.querySelector('.audio-indicator');
                        if (indicator) indicator.remove();
                    }
                    stopInterview();
                } else if (type === 'error') {
                    throw new Error(event.data.detail || 'Error del servidor');
                }
            }
        }
    } catch (e) {
        console.error('SSE pipeline error:', e);
        hideTyping();
        addMessage('error', e.message || 'Algo salió mal.');
        setStatus('Error', true);
        stopInterview();
    } finally {
        isProcessing = false;
        btnMic.disabled = false;
        checkAllDone();
    }
}

/**
 * Get current turn number from conversation state.
 */
function getCurrentTurnNumber() {
    // Count messages to estimate turn number (user+candidate pairs)
    const messages = conversation.querySelectorAll('.message.user, .message.candidate');
    // Each pair = 1 turn, so divide by 2 and subtract 1 (0-indexed)
    return Math.floor(messages.length / 2) - 1;
}

// ─── Typing animation ──────────────────────────────────

function appendTypingText(messageDiv, text) {
    const bubble = messageDiv.querySelector('.bubble');
    if (!bubble) return;

    let p = bubble.querySelector('p');
    if (!p) {
        p = document.createElement('p');
        bubble.appendChild(p);
    }

    // Remove existing cursor if any
    const existingCursor = p.querySelector('.typing-cursor');
    if (existingCursor) existingCursor.remove();

    // Append text
    p.textContent += text;

    // Add blinking cursor
    const cursor = document.createElement('span');
    cursor.className = 'typing-cursor';
    p.appendChild(cursor);
}

// ─── Context panel ─────────────────────────────────────

function toggleContextPanel() {
    contextPanel.classList.toggle('open');
}

async function fetchContext(turnNumber) {
    if (!conversationId || turnNumber < 0) return;

    try {
        const res = await fetch(`${API_BASE}/api/conversation/${conversationId}/context?turn=${turnNumber}`);
        if (!res.ok) {
            // Context endpoint fails silently — hide panel, no error
            return;
        }

        const chunks = await res.json();
        renderContext(chunks);

        // Auto-close after 5s
        setTimeout(() => {
            contextPanel.classList.remove('open');
        }, 5000);
    } catch (e) {
        // Silently fail — interview unaffected
        console.warn('Context fetch failed:', e.message);
    }
}

function renderContext(chunks) {
    if (!chunks || chunks.length === 0) {
        contextContent.innerHTML = '<p class="context-empty">No se recuperó contexto para esta respuesta</p>';
        return;
    }

    contextContent.innerHTML = chunks.map((chunk, i) => `
        <div class="chunk-pill" data-index="${i}" onclick="toggleChunk(this)">
            <span class="chunk-score">${chunk.score.toFixed(2)}</span>
            <span class="chunk-preview">${escapeHtml(chunk.text.substring(0, 100))}${chunk.text.length > 100 ? '...' : ''}</span>
            <div class="chunk-full">
                <p>${escapeHtml(chunk.text)}</p>
                <p class="chunk-source">Fuente: ${escapeHtml(chunk.source)}</p>
            </div>
        </div>
    `).join('');
}

function toggleChunk(el) {
    el.classList.toggle('expanded');
}
// Make it global for onclick
window.toggleChunk = toggleChunk;

// ─── Helpers ───────────────────────────────────────────

function setStatus(text, className) {
    statusEl.textContent = text;
    statusEl.className = 'hud-status' + (className ? ' ' + className : '');
}

function scrollToBottom() {
    if (!isUserScrolledUp) {
        conversation.scrollTop = conversation.scrollHeight;
    }
}

function showTyping() {
    if (document.querySelector('.typing-indicator')) return;
    const div = document.createElement('div');
    div.className = 'typing-indicator';
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
    const el = document.querySelector('.typing-indicator');
    if (el) el.remove();
}

function addMessage(type, text) {
    const div = document.createElement('div');
    div.className = `message ${type}`;

    if (type === 'user' || type === 'candidate') {
        const avatar = document.createElement('div');
        avatar.className = `avatar ${type}-avatar`;
        avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
            <circle cx="12" cy="7" r="4"/>
        </svg>`;

        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = `<p>${escapeHtml(text || '')}</p>`;

        if (type === 'candidate') {
            div.appendChild(avatar);
            div.appendChild(bubble);
        } else {
            div.appendChild(bubble);
            div.appendChild(avatar);
        }
    } else {
        div.innerHTML = `<p>${escapeHtml(text || '')}</p>`;
    }

    conversation.appendChild(div);
    scrollToBottom();
    return div;
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

// ─── Bootstrap ─────────────────────────────────────────

init();
