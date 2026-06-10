# Design: AI Mikel — Digital Twin MVP

## Technical Approach

Greenfield build of a voice-based digital twin that lets recruiters "pre-interview" a candidate via voice. The system uses RAG over candidate documents to generate context-aware responses as the candidate, with STT→LLM→TTS pipeline.

## Architecture Decisions

### Decision: Candidate Profile Storage

**Choice**: JSON files in `candidate/` directory  
**Alternatives considered**: YAML, SQLite, PostgreSQL  
**Rationale**: Single candidate, no concurrent writes, human-readable, zero dependencies. JSON is native to Python. YAML adds parsing dependency. Database is overkill for static profile data.

### Decision: RAG Implementation

**Choice**: In-memory cosine similarity with sentence-transformers  
**Alternatives considered**: Vector DB (Chroma/Pinecone), keyword search, hybrid  
**Rationale**: One candidate's documents fit in memory (~100 pages max). No vector DB overhead. Sentence-transformers provides good semantic search. Can upgrade to Chroma later if needed.

### Decision: Conversation State

**Choice**: In-memory dict with conversation ID  
**Alternatives considered**: Redis, SQLite, filesystem  
**Rationale**: MVP scope explicitly excludes session persistence. In-memory is zero-config, fastest. Conversation ID allows multiple concurrent sessions. State lost on restart is acceptable.

### Decision: Frontend Embedding

**Choice**: Standalone app with iframe embedding  
**Alternatives considered**: Web component, React wrapper, API-only  
**Rationale**: Vanilla HTML/CSS/JS is simplest. iframe allows portfolio integration without coupling. Web component adds complexity. Standalone preserves independence.

### Decision: Audio Format Handling

**Choice**: Convert browser webm/ogg to WAV via pydub  
**Alternatives considered**: Force WAV recording, use Web Audio API  
**Rationale**: Browsers record webm/ogg natively. pydub with ffmpeg handles conversion. WAV is Whisper's preferred format. Alternative: accept webm directly (Whisper supports it).

## Data Flow

```
Recruiter speaks → Browser records (webm/ogg)
    ↓
POST /api/conversation/{id}/message (audio blob)
    ↓
pydub: webm → WAV (16kHz mono)
    ↓
Faster Whisper: WAV → text
    ↓
RAG: text query → cosine similarity → top-3 chunks
    ↓
Owl API: system prompt + candidate context + user question → response text
    ↓
Edge TTS: response text → MP3 audio
    ↓
Return JSON: {text: "...", audio_url: "/audio/{id}.mp3"}
    ↓
Browser plays audio response
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/main.py` | Create | FastAPI app with conversation endpoints |
| `backend/services/stt.py` | Create | Faster Whisper wrapper |
| `backend/services/llm.py` | Create | Owl API client |
| `backend/services/tts.py` | Create | Edge TTS wrapper |
| `backend/services/rag.py` | Create | RAG retrieval logic |
| `backend/services/candidate.py` | Create | Candidate profile loader |
| `backend/prompts/candidate.py` | Create | System prompt for digital twin |
| `backend/config.py` | Create | Configuration management |
| `backend/requirements.txt` | Create | Python dependencies |
| `frontend/index.html` | Create | Main page with mic UI |
| `frontend/style.css` | Create | Styling |
| `frontend/app.js` | Create | Voice chat logic |
| `candidate/profile.json` | Create | Candidate CV and projects |
| `candidate/docs/` | Create | Project documentation files |
| `nginx/interview.conf` | Create | Nginx configuration |

## Interfaces / Contracts

### API Endpoints

```python
# Start new conversation
POST /api/conversation
Response: {"conversation_id": "uuid", "welcome_message": "..."}

# Send message in conversation
POST /api/conversation/{conversation_id}/message
Request: FormData {audio: File}
Response: {
    "user_text": "transcribed text",
    "response_text": "candidate response",
    "audio_url": "/audio/{conversation_id}/{message_id}.mp3"
}

# Health check
GET /api/health
Response: {"status": "ok", "whisper_loaded": true}
```

### Candidate Profile Schema

```json
{
  "name": "Mikel",
  "title": "Junior DAM Developer",
  "summary": "Brief professional summary",
  "skills": ["Python", "FastAPI", "JavaScript", "SQL"],
  "experience": [
    {
      "company": "...",
      "role": "...",
      "period": "...",
      "highlights": ["..."]
    }
  ],
  "projects": [
    {
      "name": "...",
      "description": "...",
      "technologies": ["..."],
      "highlights": ["..."]
    }
  ],
  "stories": [
    {
      "situation": "...",
      "task": "...",
      "action": "...",
      "result": "..."
    }
  ],
  "documents": ["path/to/cv.pdf", "path/to/project1.md"]
}
```

### RAG Document Format

```json
{
  "id": "doc-uuid",
  "source": "candidate/profile.json",
  "section": "experience",
  "content": "text content for embedding",
  "metadata": {
    "type": "experience",
    "company": "...",
    "date": "..."
  }
}
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | STT/LLM/TTS services | Mock external APIs, test input/output |
| Unit | RAG retrieval | Test with known documents, verify relevance |
| Integration | Full pipeline | Audio in → text → response → audio out |
| E2E | Browser recording | Selenium/Playwright with real mic |

## Performance Considerations

- **Whisper Loading**: Load model at startup, keep in memory (~1.4GB RAM)
- **Embedding Cache**: Cache document embeddings in memory, recompute only on profile change
- **TTS Caching**: Cache generated audio for common responses (greetings, deflects)
- **Async Processing**: Use FastAPI async for non-blocking I/O
- **Latency Target**: STT (2-3s) + RAG (0.1s) + LLM (2-3s) + TTS (1-2s) = 5-9s total

## Security

- **Rate Limiting**: 10 requests per minute per IP (simple in-memory counter)
- **Input Validation**: Validate audio format, max 30s recording
- **API Keys**: Store in environment variables, not code
- **CORS**: Restrict to portfolio domain in production
- **No Auth**: MVP scope, but structure allows adding JWT later

## Deployment Architecture

```
Oracle Free Tier ARM64
├── Nginx (port 80/443)
│   ├── / → frontend/ (static files)
│   └── /api → localhost:8000 (FastAPI)
├── FastAPI (port 8000, uvicorn)
│   ├── services/ (STT, LLM, TTS, RAG)
│   ├── candidate/ (profile.json, docs/)
│   └── audio/ (generated responses)
└── systemd service for FastAPI
```

## Migration / Rollout

No migration required — greenfield build. Deploy directly to Oracle Free Tier.

## Open Questions

- [ ] Should we accept webm directly or always convert to WAV?
- [ ] How to handle Owl API rate limits in production?
- [ ] Should audio files be cleaned up periodically?
- [ ] Portfolio integration: iframe src URL or embed code?
