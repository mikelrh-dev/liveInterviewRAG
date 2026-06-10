/**
 * InterviewTTS — Frontend voice chat logic
 * Handles microphone recording, VAD auto-stop, audio playback, and conversation display.
 */

const API_BASE = '';
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;

// VAD (Voice Activity Detection) state
let audioContext = null;
let vadAnalyser = null;
let vadAnimationId = null;
let silenceStart = null;
const SILENCE_TIMEOUT_MS = 1500;
const RMS_THRESHOLD = 0.025;

// DOM elements
const micButton = document.getElementById('micButton');
const status = document.getElementById('status');
const conversation = document.getElementById('conversation');
const micIcon = micButton.querySelector('.mic-icon');
const stopIcon = micButton.querySelector('.stop-icon');

/**
 * Initialize conversation on page load
 */
async function init() {
    try {
        const response = await fetch(`${API_BASE}/api/conversation`, { method: 'POST' });
        const data = await response.json();
        conversationId = data.conversation_id;
        addMessage('system', data.welcome_message);
        updateStatus('Preparado para escuchar');
    } catch (error) {
        console.error('Failed to create conversation:', error);
        updateStatus('Error de conexión — recarga la página', true);
    }
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
        // Silence detected
        if (silenceStart === null) {
            silenceStart = Date.now();
        } else if (Date.now() - silenceStart >= SILENCE_TIMEOUT_MS) {
            updateStatus('Procesando...');
            stopRecording(true); // auto-stop by VAD
            return;
        }
    } else {
        // Sound detected — reset silence timer
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
 * Toggle recording state
 */
function toggleRecording() {
    if (isProcessing) return;
    if (isRecording) {
        stopRecording(false); // manual stop
    } else {
        startRecording();
    }
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

        // Start VAD monitoring
        startVad(stream);

        // Update UI
        micButton.classList.add('recording');
        micIcon.classList.add('hidden');
        stopIcon.classList.remove('hidden');
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

    // Update UI
    micButton.classList.remove('recording');
    micIcon.classList.remove('hidden');
    stopIcon.classList.add('hidden');
}

/**
 * Process the recorded audio through the pipeline
 */
async function processRecording() {
    if (audioChunks.length === 0) return;

    isProcessing = true;
    micButton.disabled = true;
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

        updateStatus('Preparado para escuchar');

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
        updateStatus('Error — toca para reintentar', true);
    } finally {
        isProcessing = false;
        micButton.disabled = false;
    }
}

// Event listeners
micButton.addEventListener('click', toggleRecording);

// Initialize
init();
