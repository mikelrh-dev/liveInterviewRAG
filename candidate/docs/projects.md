# Mikel — Projects

## InterviewTTS — Voice AI Portfolio

**Technologies**: Python, FastAPI, Whisper, Edge TTS, Sentence Transformers, RAG

A voice-based AI portfolio that lets recruiters have natural voice conversations with a digital twin of the candidate. Instead of just reading a CV, recruiters can ask questions and receive spoken responses as if talking to the real person.

### Key Features
- **Voice Input**: Browser-based microphone recording with MediaRecorder API
- **Speech-to-Text**: Faster Whisper for accurate transcription of recruiter questions
- **RAG Pipeline**: Retrieves relevant context from candidate documents for accurate responses
- **LLM Generation**: Uses Owl API to generate natural, context-aware responses as the candidate
- **Voice Output**: Edge TTS synthesizes responses in a professional voice
- **Clean UI**: Responsive frontend designed for professional presentation

### Technical Highlights
- Built complete STT → RAG → LLM → TTS pipeline
- Implemented in-memory cosine similarity retrieval with sentence-transformers
- Designed for deployment on Oracle Free Tier with Nginx reverse proxy
- Professional frontend suitable for embedding in portfolio sites

### Challenges Solved
- **Latency Optimization**: Pipeline designed for under 8 seconds total response time
- **Audio Format Handling**: Browser webm/ogg converted to WAV via pydub for Whisper compatibility
- **Context Accuracy**: RAG pipeline ensures responses are grounded in real CV/project data
- **Error Graceful Degradation**: Each pipeline stage handles failures with meaningful error messages
