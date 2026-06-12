/**
 * InterviewTTS — Frontend voice chat logic
 * Interview loop: click "Iniciar" → speak (VAD) → see text live (SSE) → hear response → auto-loop
 */

const API_BASE = '';
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;
let isInterviewActive = false;
let isUserScrolledUp = false;  // smart scroll

// VAD state
let audioContext = null;
let vadAnalyser = null;
let vadAnimationId = null;
let silenceStart = null;
let hasSpoken = false;
const SILENCE_TIMEOUT_MS = 1200;
const RMS_THRESHOLD = 0.03;

// Reusable media stream (avoids getUserMedia latency between turns)
let mediaStream = null;

// DOM
const btn = document.getElementById('btnInterview');
const btnLabel = document.getElementById('btnLabel');
const statusEl = document.getElementById('status');
const conversation = document.getElementById('conversation');
const playIcon = btn.querySelector('.play-icon');
const stopIcon = btn.querySelector('.stop-icon');

// Current candidate message (for inline updates + audio indicator)
let currentCandidateDiv = null;

function init() {
    addMessage('system', 'Presiona "Iniciar entrevista" para empezar.');
    setStatus('Preparado');

    // Smart scroll: detect if user scrolled up manually
    conversation.addEventListener('scroll', () => {
        const threshold = 50;
        isUserScrolledUp = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight > threshold;
    });
}

function scrollToBottom() {
    if (!isUserScrolledUp) {
        conversation.scrollTop = conversation.scrollHeight;
    }
}

// ─── Button actions ───────────────────────────────────────

function toggleInterview() {
    if (isInterviewActive) stopInterview();
    else startInterview();
}

async function startInterview() {
    if (isInterviewActive) return;

    try {
        const res = await fetch(`${API_BASE}/api/conversation`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        conversationId = data.conversation_id;
        addMessage('system', data.welcome_message);

        // Pre-load and play welcome audio if available
        if (data.welcome_audio_url) {
            const audio = new Audio(`${API_BASE}${data.welcome_audio_url}`);
            audio.play().catch(() => {
                // Autoplay blocked by browser — user must interact first
                console.log('Welcome audio autoplay blocked (browser policy)');
            });
        }
    } catch (e) {
        console.error('Failed to create conversation:', e);
        setStatus('Error de conexión — recarga la página', true);
        return;
    }

    isInterviewActive = true;
    btn.classList.add('active');
    playIcon.classList.add('hidden');
    stopIcon.classList.remove('hidden');
    btnLabel.textContent = 'Finalizar';
    setStatus('Escuchando...', 'listening');

    startListening();
}

function stopInterview() {
    isInterviewActive = false;
    if (isRecording) stopRecording();
    
    // Clean up media stream
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }

    btn.classList.remove('active');
    playIcon.classList.remove('hidden');
    stopIcon.classList.add('hidden');
    btnLabel.textContent = 'Iniciar entrevista';
    setStatus('Entrevista finalizada');
    addMessage('system', 'Entrevista finalizada.');
}

// ─── Recording + VAD ──────────────────────────────────────

function startListening() {
    if (!isInterviewActive || isProcessing || isRecording) return;
    startRecording();
}

async function startRecording() {
    try {
        // Reuse existing media stream if available and active
        if (!mediaStream || mediaStream.getTracks().some(t => t.readyState === 'ended')) {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }
        
        audioChunks = [];

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus' : 'audio/webm',
        });

        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            stopVad();
            processRecordingStream();
        };

        mediaRecorder.start();
        isRecording = true;
        hasSpoken = false;
        startVad(mediaStream);
        setStatus('Escuchando...', 'listening');

    } catch (e) {
        console.error('Mic denied:', e);
        setStatus('Acceso al micrófono denegado', true);
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    isRecording = false;
}

// ─── VAD ──────────────────────────────────────────────────

function calculateRms(analyser) {
    const buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / buf.length);
}

function vadLoop() {
    if (!isRecording || !vadAnalyser) return;
    const rms = calculateRms(vadAnalyser);

    if (rms >= RMS_THRESHOLD) {
        hasSpoken = true;
        silenceStart = null;
    } else if (hasSpoken) {
        if (silenceStart === null) silenceStart = Date.now();
        else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            setStatus('Procesando...', 'processing');
            stopRecording();
            return;
        }
    }

    vadAnimationId = requestAnimationFrame(vadLoop);
}

function startVad(stream) {
    audioContext = new AudioContext();
    const src = audioContext.createMediaStreamSource(stream);
    vadAnalyser = audioContext.createAnalyser();
    vadAnalyser.fftSize = 256;
    src.connect(vadAnalyser);
    silenceStart = null;
    hasSpoken = false;
    vadAnimationId = requestAnimationFrame(vadLoop);
}

function stopVad() {
    if (vadAnimationId) { cancelAnimationFrame(vadAnimationId); vadAnimationId = null; }
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    vadAnalyser = null; silenceStart = null;
}

// ─── Audio chunk queue (sequential playback) ────────────

let audioQueue = [];        // {id, url}[]
let nextChunkId = 0;        // expected ID for next chunk
let isAudioPlaying = false;
let allChunksReceived = false;

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
    if (idx === -1) return; // next chunk not available yet

    const chunk = audioQueue.splice(idx, 1)[0];
    isAudioPlaying = true;
    setStatus('Reproduciendo...');
    addAudioIndicator();

    const audio = new Audio(chunk.url);
    audio.addEventListener('ended', () => {
        nextChunkId++;
        isAudioPlaying = false;
        tryPlayNextChunk();
        checkAllDone();
    }, { once: true });
    audio.addEventListener('error', () => {
        console.error('Audio playback error for chunk', chunk.id);
        nextChunkId++;
        isAudioPlaying = false;
        tryPlayNextChunk();
        checkAllDone();
    }, { once: true });
    audio.play().catch(e => {
        console.error('Audio play() failed:', e);
        nextChunkId++;
        isAudioPlaying = false;
        tryPlayNextChunk();
        checkAllDone();
    });
}

function checkAllDone() {
    if (allChunksReceived && audioQueue.length === 0 && !isAudioPlaying) {
        removeAudioIndicator();
        if (isInterviewActive) startListening();
    }
}

// ─── SSE streaming ───────────────────────────────────────

async function processRecordingStream() {
    if (audioChunks.length === 0) return;
    isProcessing = true;
    btn.disabled = true;
    setStatus('Enviando audio...', 'processing');
    resetAudioQueue();
    currentCandidateDiv = null;
    showTyping();

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const fd = new FormData();
    fd.append('audio', blob, 'recording.webm');

    let fullText = '';

    try {
        const res = await fetch(`${API_BASE}/api/conversation/${conversationId}/message/stream`, {
            method: 'POST', body: fd,
        });

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
                    const p = currentCandidateDiv.querySelector('.bubble p');
                    if (p) p.textContent = fullText;
                    scrollToBottom();
                } else if (type === 'audio_chunk') {
                    audioQueue.push({ id: event.data.id, url: event.data.url });
                    tryPlayNextChunk();
                } else if (type === 'done') {
                    allChunksReceived = true;
                    // If no audio was received at all, restart directly
                    if (audioQueue.length === 0 && !isAudioPlaying) {
                        if (isInterviewActive) startListening();
                    }
                } else if (type === 'interview_end') {
                    // Farewell detected — don't restart listening
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
        btn.disabled = false;
        checkAllDone();
    }
}

// ─── Helpers ──────────────────────────────────────────────

function setStatus(text, className) {
    statusEl.textContent = text;
    statusEl.className = 'status' + (className ? ' ' + className : '');
}

function showTyping() {
    if (document.querySelector('.typing-indicator')) return;
    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = `
        <div class="avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
        // Avatar
        const avatar = document.createElement('div');
        avatar.className = `avatar ${type}-avatar`;
        if (type === 'candidate') {
            avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
            </svg>`;
        } else {
            avatar.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
            </svg>`;
        }

        // Bubble
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = `<p>${escapeHtml(text || '')}</p>`;

        // Order: avatar + bubble for candidate, bubble + avatar for user
        if (type === 'candidate') {
            div.appendChild(avatar);
            div.appendChild(bubble);
        } else {
            div.appendChild(bubble);
            div.appendChild(avatar);
        }
    } else {
        // System or error — simple text
        div.innerHTML = `<p>${escapeHtml(text || '')}</p>`;
    }

    conversation.appendChild(div);
    scrollToBottom();
    return div;
}

function escapeHtml(text) {
    const d = document.createElement('div'); d.textContent = text; return d.innerHTML;
}

// ─── Bootstrap ────────────────────────────────────────────

btn.addEventListener('click', toggleInterview);
init();
