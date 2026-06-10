"""InterviewTTS — FastAPI application with voice interview pipeline."""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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

# Services (initialized at startup)
stt_service = STTService(
    model_name=config.WHISPER_MODEL,
    device=config.WHISPER_DEVICE,
    compute_type=config.WHISPER_COMPUTE_TYPE,
)
llm_service = LLMService(
    api_key=config.OWL_API_KEY,
    api_url=config.OWL_API_URL,
    model=config.OWL_MODEL,
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

    logger.info("InterviewTTS backend started")
    yield

    # Shutdown
    await llm_service.close()
    logger.info("InterviewTTS backend stopped")


app = FastAPI(
    title="InterviewTTS",
    description="Voice-based AI interview digital twin",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow portfolio embedding
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    welcome = "Hi! I'm Mikel, a Junior DAM Developer. Feel free to ask me about my experience, projects, or skills."

    conversations[conversation_id] = {
        "id": conversation_id,
        "messages": [],
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
    }

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
            response_text = await llm_service.generate(
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
