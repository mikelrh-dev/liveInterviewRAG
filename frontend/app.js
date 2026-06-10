/**
 * InterviewTTS — Frontend voice chat logic
 * Continuous interview loop: VAD auto-stop + auto-restart listening after each response.
 */

const API_BASE = '';
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;
let isInterviewActive = false;
let isWaitingForRestart = false;

// VAD (Voice Activity Detection) state
let audioContext = null;
let vadAnalyser = null;
let vadAnimationId = null;
let silenceStart = null;
const SILENCE_TIMEOUT_MS = 1500;
const RMS_THRESHOLD = 0.025;

// DOM elements
const btnInterview = document.getElementById('btnInterview');
const btnLabel = document.getElementById('btnLabel');
const status = document.getElementById('status');
const conversation = document.getElementById('conversation');
const playIcon = btnInterview.querySelector('.play-icon');
const stopIcon = btnInterview.querySelector('.stop-icon');

/**
 * Initialize on page load
 */
async function init() {
    addMessage('system', 'Presiona "Iniciar entrevista" para empezar la entrevista por voz con Mikel.');
    updateStatus('Preparado para escuchar');
}

/**
 * Toggle interview on/off
 */
function toggleInterview() {
    if (isInterviewActive) {
        endInterview();
    } else {
        startInterview();
    }
}

/**
 * Start interview — creates conversation and begins listening
 */
async function startInterview() {
    if (isInterviewActive) return;

    try {
        const response = await fetch(`${API_BASE}/api/conversation`, { method: 'POST' });
        const data = await response.json();
        conversationId = data.conversation_id;
    } catch (error) {
        console.error('Failed to create conversation:', error);
        updateStatus('Error de conexión — recarga la página', true);
        return;
    }

    isInterviewActive = true;
    btnInterview.classList.remove('active');
    btnInterview.classList.add('listening');
    playIcon.classList.add('hidden');
    stopIcon.classList.remove('hidden');
    btnLabel.textContent = 'Finalizar';
    addMessage('system', 'Entrevista iniciada. Habla cuando quieras.');

    startListening();
}

/**
 * End interview — stops recording and resets state
 */
function endInterview() {
    isInterviewActive = false;
    isWaitingForRestart = false;

    if (isRecording) {
        stopRecording();
    }

    btnInterview.classList.remove('active', 'listening');
    playIcon.classList.remove('hidden');
    stopIcon.classList.add('hidden');
    btnLabel.textContent = 'Iniciar entrevista';
    updateStatus('Entrevista finalizada');
    addMessage('system', 'Entrevista finalizada. Presiona "Iniciar entrevista" para una nueva.');
}

/**
 * Start listening — VAD-enabled recording
 */
function startListening() {
    if (!isInterviewActive || isProcessing || isRecording) return;
    isWaitingForRestart = false;
    startRecording();
}

/**
 * Add a message to the conversation display
 */
function addMessage(type, text, audioUrl = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    messageDiv.innerHTML = `<p>${escapeHtml(text)}</p>`;

    if (audioUrl) {
        const playerDiv = document.createElement('div');
        playerDiv.className = 'audio-player';
        const audioEl = document.createElement('audio');
        audioEl.src = audioUrl;
        audioEl.controls = true;
        audioEl.autoplay = true;
        playerDiv.appendChild(audioEl);
        messageDiv.appendChild(playerDiv);
    }

    conversation.appendChild(messageDiv);
    conversation.scrollTop = conversation.scrollHeight;
}

/**
 * Update the status text
 */
function updateStatus(text, isError = false) {
    status.textContent = text;
    status.className = `status ${isError ? 'error' : ''}`;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * RMS (volume) from AnalyserNode — 0 = silence, 1 = max
 */
function calculateRms(analyser) {
    const buffer = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buffer);
    let sum = 0;
    for (let i = 0; i < buffer.length; i++) {
        const value = (buffer[i] - 128) / 128;
        sum += value * value;
    }
    return Math.sqrt(sum / buffer.length);
}

/**
 * VAD loop — runs on each animation frame while recording
 */
function vadLoop() {
    if (!isRecording || !vadAnalyser) return;

    const rms = calculateRms(vadAnalyser);

    if (rms < RMS_THRESHOLD) {
        if (silenceStart === null) {
            silenceStart = Date.now();
        } else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            updateStatus('Procesando...');
            stopRecording(true);
            return;
        }
    } else {
        silenceStart = null;
    }

    vadAnimationId = requestAnimationFrame(vadLoop);
}

/**
 * Start VAD monitoring from a media stream
 */
function startVad(stream) {
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    vadAnalyser = audioContext.createAnalyser();
    vadAnalyser.fftSize = 256;
    source.connect(vadAnalyser);
    silenceStart = null;
    vadAnimationId = requestAnimationFrame(vadLoop);
}

/**
 * Stop VAD monitoring and clean up
 */
function stopVad() {
    if (vadAnimationId) {
        cancelAnimationFrame(vadAnimationId);
        vadAnimationId = null;
    }
    if (audioContext) {
        audioContext.close().catch(() => {});
        audioContext = null;
    }
    vadAnalyser = null;
    silenceStart = null;
}

/**
 * Start recording audio with VAD
 */
async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];

        mediaRecorder = new MediaRecorder(stream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : 'audio/webm',
        });

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(track => track.stop());
            stopVad();
            processRecording();
        };

        mediaRecorder.start();
        isRecording = true;

        btnInterview.classList.remove('active');
        btnInterview.classList.add('listening');
        updateStatus('Escuchando...');

    } catch (error) {
        console.error('Microphone access denied:', error);
        updateStatus('Acceso al micrófono denegado', true);
    }
}

/**
 * Stop recording audio
 * @param {boolean} [auto=false] — true if VAD triggered the stop
 */
function stopRecording(auto = false) {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    isRecording = false;
    btnInterview.classList.remove('listening');
}

/**
 * Process the recorded audio through the pipeline
 */
async function processRecording() {
    if (audioChunks.length === 0) {
        if (isInterviewActive) startListening();
        return;
    }

    isProcessing = true;
    btnInterview.disabled = true;
    updateStatus('Procesando...');

    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');

    try {
        const response = await fetch(
            `${API_BASE}/api/conversation/${conversationId}/message`,
            {
                method: 'POST',
                body: formData,
            }
        );

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || `HTTP ${response.status}`);
        }

        const data = await response.json();

        // Display user's transcribed text
        addMessage('user', data.user_text);

        // Display candidate's response with audio
        addMessage('candidate', data.response_text, data.audio_url);

        updateStatus('Respuesta recibida');

        // Wait for the audio to finish playing, then auto-restart listening
        const lastAudioEl = conversation.querySelector('.message:last-child audio');
        if (lastAudioEl) {
            isWaitingForRestart = true;
            lastAudioEl.addEventListener('ended', () => {
                isWaitingForRestart = false;
                if (isInterviewActive) {
                    updateStatus('Escuchando...');
                    startListening();
                }
            }, { once: true });
        } else {
            setTimeout(() => {
                if (isInterviewActive) startListening();
            }, 1000);
        }

    } catch (error) {
        console.error('Pipeline error:', error);
        let message = 'Algo salió mal. Intenta de nuevo.';
        if (error.message.includes('transcribe')) {
            message = 'No se pudo entender el audio. Habla más claro, por favor.';
        } else if (error.message.includes('503') || error.message.includes('unavailable')) {
            message = 'Servicio temporalmente no disponible. Intenta de nuevo en un momento.';
        } else if (error.message.includes('422')) {
            message = 'No se pudo procesar el audio. Intenta de nuevo.';
        }
        addMessage('error', message);
        updateStatus('Error — finaliza y vuelve a intentar', true);
    } finally {
        isProcessing = false;
        btnInterview.disabled = false;
    }
}

// Event listeners
btnInterview.addEventListener('click', toggleInterview);

// Initialize
init();
