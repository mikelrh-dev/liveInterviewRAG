# Proposal: AI Mikel — Digital Twin MVP

## Intent

Recruiters spend ~6s on a CV before deciding to call. A digital twin lets them "pre-interview" the candidate via voice — hear stories, not just tech stacks. This transforms a static portfolio into an interactive experience that differentiates from 95% of junior devs.

The original PLAN.md assumed an AI that interviews the user. Direction 2 inverts this: the AI responds AS the candidate using RAG over their real CV and projects. RAG is now MVP, not deferred.

## Scope

### In Scope
- RAG pipeline: ingest CV + project docs, retrieve context for responses
- Owl API integration (replaces DeepSeek V4 Flash)
- Faster Whisper STT (int8, CPU)
- Edge TTS for natural voice output
- FastAPI backend with conversation endpoint
- Vanilla HTML/CSS/JS frontend with mic input + audio playback
- Candidate profile system (CV, projects, stories)
- Nginx + Oracle Free Tier ARM64 deploy

### Out of Scope
- Voice cloning (OpenVoice — future phase)
- Real phone call integration
- Multi-language support
- Session persistence / conversation history
- Rate limiting, error handling, auth

## Capabilities

### New Capabilities
- `rag-pipeline`: Ingest candidate documents, embed, retrieve relevant context for LLM prompts
- `candidate-profile`: Structured candidate data (CV, projects, stories) that feeds RAG
- `conversation-engine`: Multi-turn conversation with context-aware responses as the candidate

### Modified Capabilities
None — this is a greenfield build. Original PLAN.md features are superseded by this proposal.

## Approach

1. **Candidate Profile**: Define a JSON/YAML schema for candidate data (CV sections, project descriptions, key stories). Load at startup.
2. **RAG**: Use a lightweight embedding approach (sentence-transformers or similar) + cosine similarity. No vector DB needed for MVP — in-memory store is fine for one candidate's docs.
3. **Conversation Flow**: Recruiter asks question → STT transcribes → RAG retrieves relevant context → Owl API generates response as candidate → Edge TTS speaks it.
4. **Prompt Engineering**: System prompt positions LLM as the candidate, instructed to answer from retrieved context and deflect gracefully when context is insufficient.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/services/rag.py` | New | RAG retrieval logic |
| `backend/services/llm.py` | Modified | Owl API instead of DeepSeek |
| `backend/services/candidate.py` | New | Candidate profile loader |
| `backend/prompts/candidate.py` | New | System prompt for digital twin |
| `backend/main.py` | Modified | New endpoints, conversation state |
| `frontend/app.js` | Modified | Conversation UI for recruiter flow |
| `candidate/` | New | CV and project documents |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Owl API latency/availability | Med | Test early; fallback to local LLM if needed |
| RAG responses sound generic | Med | Rich candidate docs + prompt tuning |
| Total latency (STT→LLM→TTS) > 8s | Med | Profile Whisper model size; cache embeddings |
| Recruiters won't interact with AI | Low | Make it optional, not required |

## Rollback Plan

Revert to original PLAN.md scope (generic interviewer, no RAG). Delete `candidate/` dir, `rag.py`, `candidate.py`. Revert `llm.py` to DeepSeek. Frontend reverts to interviewer-mode UI.

## Dependencies

- Owl API key (free tier)
- Candidate CV and project docs (user provides)
- Python packages: fastapi, uvicorn, faster-whisper, edge-tts, sentence-transformers

## Success Criteria

- [ ] Recruiter can ask a question and receive a voice response "from" the candidate
- [ ] Responses reference real CV/projects (not generic)
- [ ] Total response latency < 8s (STT→LLM→TTS)
- [ ] Deployed on Oracle Free Tier, accessible via Nginx
- [ ] README with setup instructions and demo screenshots
