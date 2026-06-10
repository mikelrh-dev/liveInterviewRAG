/**
 * InterviewTTS — Frontend voice chat logic
 * Handles microphone recording, audio playback, and conversation display.
 */

const API_BASE = '';
let conversationId = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;

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
        updateStatus('Ready to listen');
    } catch (error) {
        console.error('Failed to create conversation:', error);
        updateStatus('Connection error — please refresh', true);
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
        playerDiv.innerHTML = `<audio controls src="${audioUrl}"></audio>`;
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
 * Toggle recording state
 */
function toggleRecording() {
    if (isProcessing) return;
    if (isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
}

/**
 * Start recording audio
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
            processRecording();
        };

        mediaRecorder.start();
        isRecording = true;

        // Update UI
        micButton.classList.add('recording');
        micIcon.classList.add('hidden');
        stopIcon.classList.remove('hidden');
        updateStatus('Listening...');

    } catch (error) {
        console.error('Microphone access denied:', error);
        updateStatus('Microphone access denied', true);
    }
}

/**
 * Stop recording audio
 */
function stopRecording() {
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
    updateStatus('Processing...');

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

        updateStatus('Ready to listen');

    } catch (error) {
        console.error('Pipeline error:', error);
        let message = 'Something went wrong. Please try again.';
        if (error.message.includes('transcribe')) {
            message = 'Could not understand the audio. Please try speaking more clearly.';
        } else if (error.message.includes('503') || error.message.includes('unavailable')) {
            message = 'Service temporarily unavailable. Please try again in a moment.';
        } else if (error.message.includes('422')) {
            message = 'Could not process the audio. Please try again.';
        }
        addMessage('error', message);
        updateStatus('Error — tap to retry', true);
    } finally {
        isProcessing = false;
        micButton.disabled = false;
    }
}

// Event listeners
micButton.addEventListener('click', toggleRecording);

// Initialize
init();
