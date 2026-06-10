/**
 * InterviewTTS — Frontend voice chat logic
 * Interview loop: click "Iniciar" → speak naturally (VAD) → response → auto-continue → click "Finalizar"
 */

const API_BASE = '';
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;
let isInterviewActive = false;

// VAD state
let audioContext = null;
let vadAnalyser = null;
let vadAnimationId = null;
let silenceStart = null;
const SILENCE_TIMEOUT_MS = 1500;
const RMS_THRESHOLD = 0.025;

// DOM
const btn = document.getElementById('btnInterview');
const btnLabel = document.getElementById('btnLabel');
const statusEl = document.getElementById('status');
const conversation = document.getElementById('conversation');
const playIcon = btn.querySelector('.play-icon');
const stopIcon = btn.querySelector('.stop-icon');

function init() {
    addMessage('system', 'Presiona "Iniciar entrevista" para empezar.');
    setStatus('Preparado');
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
        const data = await res.json();
        conversationId = data.conversation_id;
        addMessage('system', data.welcome_message);
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
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];

        mediaRecorder = new MediaRecorder(stream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus' : 'audio/webm',
        });

        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            stopVad();
            processRecording();
        };

        mediaRecorder.start();
        isRecording = true;
        startVad(stream);
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

    if (rms < RMS_THRESHOLD) {
        if (silenceStart === null) silenceStart = Date.now();
        else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            setStatus('Procesando...', 'processing');
            stopRecording();
            return;
        }
    } else {
        silenceStart = null;
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
    vadAnimationId = requestAnimationFrame(vadLoop);
}

function stopVad() {
    if (vadAnimationId) { cancelAnimationFrame(vadAnimationId); vadAnimationId = null; }
    if (audioContext) { audioContext.close().catch(() => {}); audioContext = null; }
    vadAnalyser = null; silenceStart = null;
}

// ─── Backend call ─────────────────────────────────────────

async function processRecording() {
    if (audioChunks.length === 0) return;
    isProcessing = true;
    btn.disabled = true;
    setStatus('Procesando...', 'processing');

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const fd = new FormData();
    fd.append('audio', blob, 'recording.webm');

    try {
        const res = await fetch(`${API_BASE}/api/conversation/${conversationId}/message`, {
            method: 'POST', body: fd,
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);

        const data = await res.json();
        addMessage('user', data.user_text);
        addMessage('candidate', data.response_text, data.audio_url);

        // Auto-restart listening when audio finishes
        const audioEl = conversation.querySelector('.message:last-child audio');
        if (audioEl && isInterviewActive) {
            setStatus('Reproduciendo...');
            audioEl.addEventListener('ended', () => {
                if (isInterviewActive) startListening();
            }, { once: true });
        } else if (isInterviewActive) {
            setTimeout(() => startListening(), 1000);
        }
    } catch (e) {
        console.error('Pipeline error:', e);
        let msg = 'Algo salió mal. Intenta de nuevo.';
        if (e.message.includes('transcribe')) msg = 'No se pudo entender el audio.';
        else if (e.message.includes('503') || e.message.includes('unavailable')) msg = 'Servicio temporalmente no disponible.';
        addMessage('error', msg);
        setStatus('Error', true);
        stopInterview();
    } finally {
        isProcessing = false;
        btn.disabled = false;
    }
}

// ─── Helpers ──────────────────────────────────────────────

function setStatus(text, className) {
    statusEl.textContent = text;
    statusEl.className = 'status' + (className ? ' ' + className : '');
}

function addMessage(type, text, audioUrl) {
    const div = document.createElement('div');
    div.className = `message ${type}`;
    div.innerHTML = `<p>${escapeHtml(text)}</p>`;
    if (audioUrl) {
        const player = document.createElement('div');
        player.className = 'audio-player';
        const audio = document.createElement('audio');
        audio.src = audioUrl; audio.controls = true; audio.autoplay = true;
        player.appendChild(audio);
        div.appendChild(player);
    }
    conversation.appendChild(div);
    conversation.scrollTop = conversation.scrollHeight;
}

function escapeHtml(text) {
    const d = document.createElement('div'); d.textContent = text; return d.innerHTML;
}

// ─── Bootstrap ────────────────────────────────────────────

btn.addEventListener('click', toggleInterview);
init();
