# InterviewTTS

Voice-based AI interview digital twin. Let recruiters have voice conversations with a candidate's digital twin.

## Features

- **Voice Input**: Browser microphone recording with MediaRecorder API
- **Speech-to-Text**: Faster Whisper for accurate transcription
- **RAG Pipeline**: Retrieves context from candidate documents for accurate responses
- **LLM Generation**: Google AI (primary) with OpenRouter fallback for natural, context-aware responses
- **Voice Output**: Edge TTS for professional voice synthesis
- **Clean UI**: Responsive frontend designed for professional presentation

## Quick Start

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd InterviewTTS

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r backend/requirements.txt
pip install pytest pytest-asyncio httpx  # for development
```

### Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys
# Required: OPENROUTER_API_KEY (always — fallback LLM)
# Optional: GOOGLE_API_KEY (set to use Google AI as primary LLM provider)
```

### Running

```bash
# Start the backend
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# Open in browser
# http://localhost:8000
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | API key for OpenRouter (fallback LLM provider) |
| `GOOGLE_API_KEY` | (optional) | API key for Google AI; when set, used as primary LLM provider |
| `GOOGLE_MODEL` | `gemini-3.1-flash-lite` | Google AI model name (only used if `GOOGLE_API_KEY` is set) |
| `LLM_MODEL` | `openrouter/owl-alpha` | OpenRouter model identifier |
| `LLM_TEMPERATURE` | `0.7` | LLM sampling temperature |
| `LLM_MAX_TOKENS` | `200` | Max tokens per LLM response |
| `WHISPER_MODEL` | `small` | Whisper model size (tiny/base/small/medium/large) |
| `WHISPER_DEVICE` | `cpu` | Compute device (cpu/cuda) |
| `WHISPER_COMPUTE_TYPE` | `int8` | Compute precision |
| `TTS_VOICE` | `es-ES-AlvaroNeural` | Edge TTS voice (Spanish) |
| `RAG_TOP_K` | `3` | Number of context chunks to retrieve |
| `CHUNK_SIZE` | `400` | Document chunk size in tokens |
| `CHUNK_OVERLAP` | `50` | Overlap between chunks |

## Candidate Profile

Edit files in `candidate/` to customize the digital twin:

- `candidate/profile.json` — Structured profile data (CV, projects, stories)
- `candidate/docs/*.md` — Markdown documents for RAG context

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Service health status |
| `POST` | `/api/conversation` | Create new conversation |
| `POST` | `/api/conversation/{id}/message` | Send voice message |

## Deployment

### Docker (optional)

```bash
docker compose up -d
```

### Manual (Oracle Free Tier)

1. Install system dependencies
2. Configure Nginx with `nginx/interview.conf`
3. Set up systemd service with `deployment/interviewtts.service`
4. Configure `.env` with production values

## Project Structure

```
InterviewTTS/
├── backend/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Configuration management
│   ├── services/
│   │   ├── stt.py           # Speech-to-Text (Whisper)
│   │   ├── llm.py           # LLM client (OpenRouter + Google AI)
│   │   ├── tts.py           # Text-to-Speech (Edge TTS)
│   │   ├── rag.py           # RAG pipeline
│   │   └── candidate.py     # Candidate profile loader
│   └── prompts/
│       └── candidate.py     # System prompt template
├── candidate/
│   ├── profile.json         # Candidate profile data
│   └── docs/                # Markdown documents
├── frontend/
│   ├── index.html           # Main page
│   ├── style.css            # Styling
│   └── app.js               # Voice chat logic
├── tests/                   # Unit and integration tests
├── nginx/                   # Nginx configuration
└── deployment/              # Systemd service files
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_rag.py -v
```

## License

MIT
