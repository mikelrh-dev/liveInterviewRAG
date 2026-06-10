# Tasks: AI Mikel — Digital Twin MVP

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 1200-1800 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: Backend foundation → PR 2: RAG + Candidate → PR 3: API + Frontend → PR 4: Deploy |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Backend foundation: config, STT, LLM, TTS services | PR 1 | Base branch: main; tests included |
| 2 | RAG pipeline + candidate profile system | PR 2 | Depends on PR 1; base: PR 1 branch |
| 3 | FastAPI endpoints + frontend UI | PR 3 | Depends on PR 2; base: PR 2 branch |
| 4 | Deployment config + testing | PR 4 | Depends on PR 3; base: PR 3 branch |

## Phase 1: Backend Foundation (Infrastructure)

- [x] 1.0 Set up test infrastructure: install pytest, create tests/ directory, add pytest.ini, configure coverage reporting
- [x] 1.1 Create `backend/` directory structure: `services/`, `prompts/`, `__init__.py` files
- [x] 1.2 Create `backend/requirements.txt` with FastAPI, uvicorn, faster-whisper, edge-tts, sentence-transformers, pydub, numpy
- [x] 1.3 Create `backend/config.py` with environment variable loading (OWL_API_KEY, WHISPER_MODEL, TTS_VOICE)
- [x] 1.4 Create `backend/services/stt.py` — Faster Whisper wrapper: load model at startup, `transcribe(audio_path) -> str` method
- [x] 1.5 Create `backend/services/llm.py` — Owl API client: `generate(prompt, context) -> str` with rate limit error handling
- [x] 1.6 Create `backend/services/tts.py` — Edge TTS wrapper: `synthesize(text, output_path) -> str` with voice selection
- [x] 1.7 Create `backend/prompts/candidate.py` — System prompt template positioning LLM as the candidate
- [x] 1.8 Write unit tests for STT/LLM/TTS services with mocked external APIs
- [x] 1.9 Verify: Each service handles errors gracefully and returns expected types

## Phase 2: RAG Pipeline + Candidate Profile

- [x] 2.1 Create `candidate/` directory with `profile.json` (CV, projects, stories schema)
- [x] 2.2 Create `candidate/docs/` with sample Markdown files (cv.md, projects.md, skills.md, stories.md)
- [x] 2.3 Create `backend/services/candidate.py` — Load JSON + Markdown files from `candidate/` directory
- [x] 2.4 Create `backend/services/rag.py` — Document chunking (200-500 tokens with overlap)
- [x] 2.5 Implement embedding computation using sentence-transformers or TF-IDF fallback
- [x] 2.6 Implement cosine similarity retrieval: `retrieve(query, top_k=3) -> List[Chunk]`
- [x] 2.7 Add ingestion logging and empty directory warning
- [x] 2.8 Write unit tests for RAG with known documents, verify retrieval relevance
- [x] 2.9 Verify: Embedding + retrieval completes within 500ms for 50 chunks

## Phase 3: API Layer + Frontend

- [x] 3.1 Create `backend/main.py` — FastAPI app with CORS, static file serving
- [x] 3.2 Implement `POST /api/conversation` endpoint — create conversation ID, return welcome message
- [x] 3.3 Implement `POST /api/conversation/{id}/message` — accept audio FormData, orchestrate STT→RAG→LLM→TTS pipeline
- [x] 3.4 Implement `GET /api/health` — return status and model loading state
- [x] 3.5 Add conversation state management (in-memory dict with conversation IDs)
- [x] 3.6 Create `frontend/index.html` — microphone button, audio playback, conversation display
- [x] 3.7 Create `frontend/style.css` — responsive design, professional tone
- [x] 3.8 Create `frontend/app.js` — MediaRecorder API, fetch calls, audio playback logic
- [x] 3.9 Add error handling: STT failure (422), LLM failure (503), frontend retry UX
- [x] 3.10 Write integration tests: full pipeline audio in → audio out
- [x] 3.11 Verify: Total latency under 8 seconds for full pipeline

## Phase 4: Deployment + Polish

- [x] 4.1 Create `nginx/interview.conf` — reverse proxy /api to FastAPI, serve frontend static files
- [x] 4.2 Create systemd service file for FastAPI (auto-restart, logging)
- [x] 4.3 Add audio cleanup: delete temporary files after processing
- [x] 4.4 Add rate limiting: 10 requests per minute per IP (in-memory counter)
- [x] 4.5 Add input validation: audio format check, max 30s recording
- [x] 4.6 Update README with setup instructions, environment variables, demo screenshots
- [x] 4.7 Write E2E test script (manual or Playwright) for browser recording flow
- [x] 4.8 Verify: Deploy on Oracle Free Tier, accessible via Nginx, iframe embeddable

## Verification Checklist

- [x] Recruiter can ask question and receive voice response "from" candidate
- [x] Responses reference real CV/projects (not generic)
- [x] Total response latency < 8s (STT→LLM→TTS)
- [x] Deployed on Oracle Free Tier, accessible via Nginx
- [x] README with setup instructions and demo screenshots
