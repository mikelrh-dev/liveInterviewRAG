"""InterviewTTS — FastAPI application with voice interview pipeline."""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load .env before anything else
load_dotenv()

from backend.client_ip import (  # noqa: F401  (re-exported)
    _TRUSTED_HOP_PROPERTIES,
    _is_trusted_hop,
    resolve_client_ip,
)
from backend.config import config
from backend.conversation import (  # noqa: F401  (re-exported)
    _rate_limit_store,
    build_conversation_context,
    conversations,
    get_conversation_or_hydrate,
    persist_turn,
    turn_done_payload,
    update_conversation_summary,
)
from backend.farewell import detect_farewell  # noqa: F401  (re-exported)
from backend.maintenance import (  # noqa: F401  (re-exported)
    cleanup_stale_audio,
    periodic_cleanup,
)
from backend.middleware import (  # noqa: F401  (re-exported)
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
)
from backend.prompts.candidate import build_system_prompt, sanitize_for_tts
from backend.services.candidate import CandidateProfile
from backend.services.llm import LLMService
from backend.services.persistence import PersistenceService
from backend.services.rag import RAGPipeline
from backend.services.report import ReportService
from backend.services.semantic_cache import SemanticAnswerCache
from backend.services.stt import STTService
from backend.services.tts import TTSService
from backend.sse import sse_format  # noqa: F401  (re-exported)
from backend.turns.blocking import run_turn
from backend.turns.streaming import build_stream
from backend.uploads import (  # noqa: F401  (re-exported)
    MAX_AUDIO_SIZE,
    _audio_extension,
    stage_upload,
)

logger = logging.getLogger(__name__)


# ─── Session state ──────────────────────────────────────
# conversations, _rate_limit_store, the rolling summary and the write-through
# now live in backend/conversation.py; farewell detection in
# backend/farewell.py; the periodic sweep in backend/maintenance.py. All are
# re-exported above so existing importers keep working.



# ─── Transport plumbing ──────────────────────────────────
# Client-IP resolution, the size ceiling, the SSE envelope and both middlewares
# now live in backend/client_ip.py, backend/uploads.py, backend/sse.py and
# backend/middleware.py. Re-exported above for existing importers.

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
    google_api_key=config.GOOGLE_API_KEY,
    google_model=config.GOOGLE_MODEL,
)
tts_service = TTSService(
    voice=config.TTS_VOICE,
    output_dir=config.AUDIO_DIR,
)
rag_pipeline = RAGPipeline(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    cache_dir=config.RAG_CACHE_DIR,
    embedding_model=config.EMBEDDING_MODEL,
)
candidate_profile = CandidateProfile(config.CANDIDATE_DIR, wiki_dir=config.WIKI_DIR)

report_service = ReportService(
    output_dir=config.REPORTS_DIR,
    retention_days=config.REPORT_RETENTION_DAYS,
)

# Durable store (Cap-2): write-through SQLite persistence. Failures are
# logged and swallowed inside the service — never surfaced to the pipeline.
persistence = PersistenceService(config.DB_PATH, enabled=config.PERSISTENCE_ENABLED)

# Semantic answer cache (Cap-3): reuses the RAG embedder via a provider — no
# second model load. Shares the same SQLite DB; schema is ensured lazily.
semantic_cache = SemanticAnswerCache(
    config.DB_PATH,
    lambda: rag_pipeline.embedder,
    enabled=config.SEMANTIC_CACHE_ENABLED,
    ttl_days=config.SEMANTIC_CACHE_TTL_DAYS,
    max_rows=config.SEMANTIC_CACHE_MAX_ROWS,
    threshold=config.SEMANTIC_CACHE_THRESHOLD,
)


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

    # Pre-warm LLM connection so first call is faster
    try:
        logger.info("Pre-warming LLM connection...")
        llm_service.generate(prompt="ping", context="", system_prompt="")
        logger.info("LLM connection pre-warmed")
    except Exception as e:
        logger.warning("LLM pre-warm failed (first call may be slower): %s", e)

    # Ensure audio directory exists (the /audio mount is served from it)
    ensure_audio_dir()

    # Initialize the persistent store (Cap-2): mkdir + DDL + corrupt recovery
    try:
        await asyncio.to_thread(persistence.initialize)
    except Exception as e:
        logger.warning("Persistence initialization failed at startup: %s", e)

    # Warm the semantic cache (Cap-3) and drop rows expired since last run
    try:
        await asyncio.to_thread(semantic_cache.sweep_expired)
    except Exception as e:
        logger.warning("Semantic cache startup sweep failed: %s", e)

    # Clean up stale audio files from previous runs
    cleanup_stale_audio()

    # Ensure reports directory exists and prune expired reports (30d retention)
    try:
        config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_service.cleanup_expired()
    except Exception as e:
        logger.warning("Report dir/cleanup failed at startup: %s", e)

    # Spawn periodic cleanup task
    cleanup_interval = config.AUDIO_CLEANUP_INTERVAL_MIN * 60
    cleanup_task = asyncio.create_task(
        periodic_cleanup(interval_seconds=cleanup_interval)
    )

    logger.info("InterviewTTS backend started")
    yield

    # Shutdown
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
    # Close the shared LLM HTTP client (Cap-1 keep-alive) exactly once
    llm.close_http_clients()
    logger.info("InterviewTTS backend stopped")


app = FastAPI(
    title="InterviewTTS",
    description="Voice-based AI interview digital twin",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting middleware
app.add_middleware(RateLimitMiddleware, max_requests=config.RATE_LIMIT_PER_MINUTE)

# Oversized-body guard. Registered after the rate limiter so it sits *inside*
# CORS (add_middleware inserts outermost-first) and still runs before any
# route parses a body.
app.add_middleware(MaxBodySizeMiddleware, max_size=MAX_AUDIO_SIZE)

# CORS — restricted in production, configurable via env var
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Audio directory bootstrap ───────────────────────────
def ensure_audio_dir() -> Path:
    """Create the audio directory and return it.

    ``app.mount`` below runs at import time and ``StaticFiles`` refuses to mount
    a directory that does not exist. That directory used to be created only as a
    side effect of ``TTSService.__init__``, so the mount silently depended on a
    service constructor running earlier in this module: make the service lazy, or
    reorder two statements, and startup dies here with an error about a
    directory nobody asked for. Declaring the prerequisite at the line that needs
    it is the only ordering that cannot rot.

    Idempotent, and called again from the lifespan so a directory deleted while
    the process runs is restored.
    """
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return config.AUDIO_DIR


# Serve generated audio files
AUDIO_DIR = ensure_audio_dir()
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


@app.get("/api/health")
async def health_check():
    """Return service health status."""
    return {
        "status": "ok",
        "whisper_loaded": stt_service.is_loaded,
        "rag_chunks": len(rag_pipeline.chunks),
        "candidate_loaded": candidate_profile.profile_data is not None,
    }


@app.get("/api/config")
async def get_config():
    """Return active model configuration for the sidebar UI."""
    return {
        "tts_voice": config.TTS_VOICE,
        "stt_model": config.WHISPER_MODEL,
        "stt_device": config.WHISPER_DEVICE,
        "llm_model": config.LLM_MODEL,
        "google_model": config.GOOGLE_MODEL,
        "rag_top_k": config.RAG_TOP_K,
        "max_tokens": config.LLM_MAX_TOKENS,
    }


# ─── Conversation memory ────────────────────────────────────
# Moved to backend/conversation.py: the conversations and _rate_limit_store
# dicts, the rolling summary, DB hydration, build_conversation_context and
# the write-through persist_turn. Re-exported above for existing importers.


@app.post("/api/conversation")
async def create_conversation():
    """Create a new conversation session."""
    conversation_id = uuid.uuid4().hex
    welcome = "¡Hola! Soy Mikel, desarrollador junior DAM. Pregúntame sobre mi experiencia, proyectos o habilidades."

    now_iso = datetime.utcnow().isoformat()
    conversations[conversation_id] = {
        "id": conversation_id,
        "messages": [],
        "turns": [],
        "summary": "",  # Rolling summary of older turns (for memory beyond recent_count)
        "created_at": now_iso,
        "last_activity_at": now_iso,
    }
    logger.info("Created conversation: %s", conversation_id)

    # Write-through: persist the creation immediately (spec: Conversation
    # creation persists). Failures are swallowed inside the service.
    await asyncio.to_thread(
        persistence.record_conversation, conversation_id, "", now_iso, now_iso
    )

    return {
        "conversation_id": conversation_id,
        "welcome_message": welcome,
    }


@app.post("/api/conversation/{conversation_id}/message")
async def send_message(conversation_id: str, audio: UploadFile = File(...)):
    """Process a voice message through the full pipeline: STT → RAG → LLM → TTS."""
    # Validate conversation exists (hydrates from DB on memory miss)
    conversation = await get_conversation_or_hydrate(conversation_id)
    # First-substantive-turn rule (design D10): evaluated post-hydration,
    # pre-generation. Turns are appended post-generation, so only the
    # recruiter's opening question is ever looked up or stored.
    is_first_substantive = len(conversation.get("turns", [])) == 0

    temp_audio = await stage_upload(conversation_id, audio)

    try:
        return await run_turn(conversation_id, temp_audio, is_first_substantive)
    finally:
        # Clean up temp audio
        if temp_audio.exists():
            temp_audio.unlink(missing_ok=True)


@app.post("/api/conversation/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, audio: UploadFile = File(...)):
    """Streaming version: STT + RAG + LLM (SSE tokens) + TTS + audio URL.

    See ``backend.turns.streaming`` for the event contract, which is documented
    there next to the code that emits it.
    """
    # Validate conversation exists (hydrates from DB on memory miss)
    await get_conversation_or_hydrate(conversation_id)

    temp_audio = await stage_upload(conversation_id, audio)

    # The generator, not this function, owns the temp file: it is only finished
    # once the response body has been read, which is after this returns.
    return StreamingResponse(
        build_stream(conversation_id, temp_audio), media_type="text/event-stream"
    )


@app.get("/api/conversation/{conversation_id}/context")
async def get_conversation_context(conversation_id: str, turn: int = 0):
    """Return the RAG chunks used for a specific conversation turn."""
    await get_conversation_or_hydrate(conversation_id)

    conv = conversations[conversation_id]
    turns = conv.get("turns", [])
    matching_turn = next((t for t in turns if t["n"] == turn), None)

    if matching_turn is None:
        raise HTTPException(status_code=404, detail=f"Turn {turn} not found")

    return matching_turn.get("chunks_used", [])


# Serve frontend static files
if config.FRONTEND_DIR.exists():
    app.mount(
        "/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend"
    )
