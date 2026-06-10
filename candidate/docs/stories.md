# Mikel — Stories

## The InterviewTTS Origin Story

### Situation
I noticed that recruiters spend 15-20 minutes on initial screening calls, often asking the same questions about experience, projects, and skills. Meanwhile, candidates have to repeat their stories multiple times. I thought: what if I could build a voice-based digital twin that handles the first round of questions naturally?

### Task
Create a working MVP of a voice-based AI portfolio — a system where recruiters can have actual voice conversations with a digital version of me. The responses needed to be grounded in my real CV and projects, not generic AI answers.

### Action
I designed and built InterviewTTS from scratch:
1. **STT Pipeline**: Used Faster Whisper for accurate transcription of recruiter voice input
2. **RAG System**: Built an in-memory retrieval pipeline that searches my real CV, project docs, and skills for relevant context
3. **LLM Integration**: Connected to Owl API to generate natural responses as me, using retrieved context
4. **TTS Output**: Integrated Edge TTS to deliver spoken responses in a professional voice
5. **Clean Frontend**: Built a responsive UI with microphone recording and audio playback

### Result
Created a working MVP that demonstrates practical AI engineering skills. The system processes voice input through a full STT → RAG → LLM → TTS pipeline. It showcases not just technical ability, but the ability to identify a real problem and build a complete solution.

## Learning Through Building

### Situation
When starting with FastAPI and async Python, I realized that documentation alone wasn't enough — I needed to build something real to truly understand the concepts.

### Task
Deepen my understanding of modern Python backend development by building a production-quality application.

### Action
Built InterviewTTS as a learning vehicle:
- Implemented proper async service patterns with httpx
- Designed clean service boundaries (STT, LLM, TTS, RAG as independent services)
- Used environment-based configuration for different deployment contexts
- Wrote comprehensive tests with mocked external APIs

### Result
Gained practical experience with FastAPI, async programming, and clean architecture — skills directly applicable to professional development work.
