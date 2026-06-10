"""InterviewTTS — FastAPI application with voice interview pipeline."""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# Load .env before anything else
load_dotenv()

from backend.config import config
from backend.services.candidate import CandidateProfile
from backend.services.llm import LLMService
from backend.services.rag import RAGPipeline
from backend.services.stt import STTService
from backend.services.tts import TTSService
from backend.prompts.candidate import build_system_prompt

logger = logging.getLogger(__name__)

# In-memory conversation store
conversations: Dict[str, Dict] = {}

# Rate limiting store: {ip: [timestamps]}
_rate_limit_store: Dict[str, list] = {}

# Services (initialized at startup)
stt_service = STTService(
    model_name=config.WHISPER_MODEL,
    device=config.WHISPER_DEVICE,
    compute_type=config.WHISPER_COMPUTE_TYPE,
)
llm_service = LLMService(
    api_key=config.OPENROUTER_API_KEY,
    model=config.LLM_MODEL,
    temperature=config.LLM_TEMPERATURE,
    max_tokens=config.LLM_MAX_TOKENS,
)
tts_service = TTSService(
    voice=config.TTS_VOICE,
    output_dir=config.AUDIO_DIR,
)
rag_pipeline = RAGPipeline(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
)
candidate_profile = CandidateProfile(config.CANDIDATE_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle manager."""
    # Startup
    logger.info("Starting InterviewTTS backend...")

    # Load candidate profile
    candidate_profile.load()
    if candidate_profile.documents:
        rag_pipeline.ingest_documents(candidate_profile.documents)
        logger.info("RAG pipeline initialized with %d chunks", len(rag_pipeline.chunks))
    else:
        logger.warning("No candidate documents found — RAG will return empty results")

    # Load Whisper model
    try:
        stt_service.load_model()
    except Exception as e:
        logger.warning("Could not load Whisper model: %s (STT will fail)", e)

    # Ensure audio directory exists
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up stale audio files from previous runs
    cleanup_stale_audio()

    logger.info("InterviewTTS backend started")
    yield

    # Shutdown
    logger.info("InterviewTTS backend stopped")


def cleanup_stale_audio():
    """Clean up audio files older than 1 hour from previous runs."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for f in config.AUDIO_DIR.rglob("*"):
        if f.is_file() and f.suffix in (".mp3", ".webm", ".wav"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink(missing_ok=True)
                logger.info("Cleaned up stale audio: %s", f.name)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter: N requests per minute per IP."""

    def __init__(self, app, max_requests: int = 10, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit API endpoints
        if request.url.path.startswith("/api/"):
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            timestamps = _rate_limit_store.get(client_ip, [])

            # Remove old entries outside the window
            timestamps = [t for t in timestamps if now - t < self.window]

            if len(timestamps) >= self.max_requests:
                logger.warning("Rate limit hit for IP: %s", client_ip)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please wait before trying again."},
                )

            timestamps.append(now)
            _rate_limit_store[client_ip] = timestamps

        response = await call_next(request)
        return response


app = FastAPI(
    title="InterviewTTS",
    description="Voice-based AI interview digital twin",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting middleware
app.add_middleware(RateLimitMiddleware, max_requests=config.RATE_LIMIT_PER_MINUTE)

# CORS — restricted in production, configurable via env var
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated audio files
app.mount("/audio", StaticFiles(directory=str(config.AUDIO_DIR)), name="audio")


@app.get("/api/health")
async def health_check():
    """Return service health status."""
    return {
        "status": "ok",
        "whisper_loaded": stt_service.is_loaded,
        "rag_chunks": len(rag_pipeline.chunks),
        "candidate_loaded": candidate_profile.profile_data is not None,
    }


@app.post("/api/conversation")
async def create_conversation():
    """Create a new conversation session."""
    conversation_id = uuid.uuid4().hex
    welcome = "¡Hola! Soy Mikel, desarrollador junior DAM. Pregúntame sobre mi experiencia, proyectos o habilidades."

    conversations[conversation_id] = {
        "id": conversation_id,
        "messages": [],
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
    }
    logger.info("Created conversation: %s", conversation_id)

    return {
        "conversation_id": conversation_id,
        "welcome_message": welcome,
    }


@app.post("/api/conversation/{conversation_id}/message")
async def send_message(conversation_id: str, audio: UploadFile = File(...)):
    """Process a voice message through the full pipeline: STT → RAG → LLM → TTS."""
    # Validate conversation exists
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Validate audio file
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="Invalid audio format")

    # Save uploaded audio
    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=422, detail="Empty audio file")

    # File size check (proxy for duration — ~5MB max)
    MAX_AUDIO_SIZE = 5 * 1024 * 1024  # 5MB
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=422, detail="Audio too long (max 30 seconds)")

    temp_audio = config.AUDIO_DIR / f"input_{conversation_id}_{uuid.uuid4().hex}.webm"
    temp_audio.write_bytes(audio_bytes)

    try:
        # Step 1: STT — transcribe audio
        try:
            user_text = stt_service.transcribe(temp_audio)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not transcribe audio: {e}")

        if not user_text.strip():
            raise HTTPException(status_code=422, detail="No speech detected in audio")

        # Step 2: RAG — retrieve relevant context
        context = rag_pipeline.get_context_string(user_text, top_k=config.RAG_TOP_K)

        # Step 3: LLM — generate response as candidate
        system_prompt = build_system_prompt(context)
        try:
            response_text = await asyncio.to_thread(
                llm_service.generate,
                prompt=user_text,
                context=context,
                system_prompt=system_prompt,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"Response generation temporarily unavailable: {e}")

        # Step 4: TTS — synthesize audio response
        message_id = uuid.uuid4().hex
        output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
        try:
            audio_path = await tts_service.synthesize(response_text, output_path=output_audio)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"TTS synthesis failed: {e}")

        # Store message in conversation
        conversations[conversation_id]["messages"].append({
            "user_text": user_text,
            "response_text": response_text,
            "audio_url": f"/audio/{conversation_id}/{message_id}.mp3",
        })

        return {
            "user_text": user_text,
            "response_text": response_text,
            "audio_url": f"/audio/{conversation_id}/{message_id}.mp3",
        }

    finally:
        # Clean up temp audio
        if temp_audio.exists():
            temp_audio.unlink(missing_ok=True)


# Serve frontend static files
if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")
