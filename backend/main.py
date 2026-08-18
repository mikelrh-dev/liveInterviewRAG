"""InterviewTTS — FastAPI application with voice interview pipeline."""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# Load .env before anything else
load_dotenv()

from backend.config import config
from backend.services.candidate import CandidateProfile
from backend.services.llm import LLMService, SentenceBuffer
from backend.services.rag import RAGPipeline
from backend.services.response_cache import get_cached_response
from backend.services.stt import STTService
from backend.services.tts import TTSService
from backend.prompts.candidate import build_system_prompt, sanitize_for_tts

logger = logging.getLogger(__name__)

# ─── SSE format helper ────────────────────────────────────
def sse_format(event: str, data: dict) -> str:
    """Format as Server-Sent Event data line.

    Produces::
        data: {"event": "<event>", "data": <json>}\n\n
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


# ─── Farewell detection ─────────────────────────────────
import re

_FAREWELL_PATTERNS = [
    r"\bgracias\b.*\b(eso es todo|terminamos|finalizamos|nos vemos|adiós|chao)\b",
    r"\b(eso es todo|nada más|no tengo más preguntas)\b",
    r"\bno (tengo|hay) (más |ninguna )?(preguntas|dudas|cosas)\b",
    r"\bya (está|terminé|acabé|estamos)\b",
    r"\b(terminamos|finalizamos|cerramos) (la entrevista|por hoy|aquí|acá)\b",
    r"\b(gracias|muchas gracias).*(por tu tiempo|por la entrevista|ha sido un placer)\b",
    r"\bfue un placer\b",
    r"\b(adiós|chao|nos vemos|hasta luego)\b",
]


def detect_farewell(text: str) -> bool:
    """Check if the user is indicating the interview should end."""
    lower = text.lower().strip()
    for pattern in _FAREWELL_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


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

    # Pre-warm LLM connection so first call is faster
    try:
        logger.info("Pre-warming LLM connection...")
        llm_service.generate(prompt="ping", context="", system_prompt="")
        logger.info("LLM connection pre-warmed")
    except Exception as e:
        logger.warning("LLM pre-warm failed (first call may be slower): %s", e)

    # Ensure audio directory exists
    config.AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up stale audio files from previous runs
    cleanup_stale_audio()

    # Spawn periodic cleanup task
    cleanup_interval = config.AUDIO_CLEANUP_INTERVAL_MIN * 60
    cleanup_task = asyncio.create_task(periodic_cleanup(interval_seconds=cleanup_interval))

    logger.info("InterviewTTS backend started")
    yield

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
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


async def periodic_cleanup(interval_seconds: int) -> None:
    """Periodic background task: evict stale conversations, prune rate-limit store, clean audio."""
    from datetime import datetime, timedelta

    # Initial 30s delay so first cleanup doesn't fire during first request
    await asyncio.sleep(30)
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(hours=config.SESSION_TTL_HOURS)
            stale_ids = [
                cid for cid, c in conversations.items()
                if datetime.fromisoformat(c.get("last_activity_at", "")) < cutoff
            ]
            for cid in stale_ids:
                logger.debug("Evicting stale conversation: %s", cid)
                del conversations[cid]
        except Exception as e:
            logger.error("Conversation eviction failed: %s", e)
        try:
            now = time.time()
            for ip in list(_rate_limit_store.keys()):
                _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if now - t < 60]
                if not _rate_limit_store[ip]:
                    del _rate_limit_store[ip]
        except Exception as e:
            logger.error("Rate-limit pruning failed: %s", e)
        try:
            cleanup_stale_audio()
        except Exception as e:
            logger.error("Audio cleanup failed: %s", e)
        await asyncio.sleep(interval_seconds)


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


# ─── Conversation memory (rolling summary) ────────────────

MAX_SUMMARY_CHARS = 1500  # ~300 tokens for the rolling summary
MAX_TURN_TEXT_CHARS = 200  # Truncate each turn's text in the prompt


def update_conversation_summary(conversation_id: str, new_turn: dict) -> None:
    """Append a compressed entry for the new turn to the rolling summary.

    Older entries are dropped if the summary exceeds MAX_SUMMARY_CHARS.
    """
    if conversation_id not in conversations:
        return

    summary = conversations[conversation_id].get("summary", "")
    user_brief = (new_turn.get("user_text") or "")[:80]
    assist_brief = (new_turn.get("assistant_text") or "")[:120]
    new_line = f"- P: {user_brief} → R: {assist_brief}\n"

    combined = summary + new_line
    if len(combined) > MAX_SUMMARY_CHARS:
        # Drop oldest lines until it fits, keep at least the most recent
        lines = combined.split("\n")
        while len("\n".join(lines)) > MAX_SUMMARY_CHARS and len(lines) > 1:
            lines.pop(0)
        combined = "[Resumen — turnos más antiguos omitidos por longitud]\n" + "\n".join(lines)

    conversations[conversation_id]["summary"] = combined


def build_conversation_context(conversation_id: str, recent_count: int = 3) -> str:
    """Build the conversation history to inject into the system prompt.

    Combines:
    - Rolling summary of older turns (compressed)
    - Recent turns in full text (truncated to MAX_TURN_TEXT_CHARS)

    Returns empty string if no turns exist.
    """
    if conversation_id not in conversations:
        return ""

    turns = conversations[conversation_id].get("turns", [])
    if not turns:
        return ""

    summary = conversations[conversation_id].get("summary", "")
    recent = turns[-recent_count:] if len(turns) >= recent_count else turns
    older_count = len(turns) - len(recent)

    parts = []
    if summary and older_count > 0:
        parts.append(f"[Resumen de la conversación — {older_count} turnos anteriores]")
        parts.append(summary)
    if recent:
        parts.append(f"\n[Últimos {len(recent)} turnos — texto completo]")
        for turn in recent:
            user_t = (turn.get("user_text") or "")[:MAX_TURN_TEXT_CHARS]
            assist_t = (turn.get("assistant_text") or "")[:MAX_TURN_TEXT_CHARS]
            parts.append(f"- P: {user_t}")
            parts.append(f"  R: {assist_t}")

    return "\n".join(parts)


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

    _t = [time.time()]  # t0

    try:
        # Step 1: STT — transcribe audio
        try:
            user_text = stt_service.transcribe(temp_audio)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not transcribe audio: {e}")
        _t.append(time.time())

        if not user_text.strip():
            raise HTTPException(status_code=422, detail="No speech detected in audio")

        # Step 2: Response cache — skip RAG + LLM for frequent questions (instant reply)
        cached_response = get_cached_response(user_text)
        if cached_response is not None:
            logger.info("Cache hit for: %s", user_text)
            response_text = cached_response
            now = time.time()
            _t.append(now)  # RAG marker (skipped)
            _t.append(now)  # LLM marker (skipped)
        else:
            # Step 2: RAG — retrieve relevant context
            context = rag_pipeline.get_context_string(user_text, top_k=config.RAG_TOP_K)
            _t.append(time.time())

            # Step 3: LLM — generate response as candidate
            # Build conversation context (rolling summary + recent turns) for memory
            conversation_context = build_conversation_context(conversation_id, recent_count=3)
            system_prompt = build_system_prompt(context, conversation_context=conversation_context)
            try:
                response_text = await asyncio.to_thread(
                    llm_service.generate,
                    prompt=user_text,
                    context=context,
                    system_prompt=system_prompt,
                )
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=f"Response generation temporarily unavailable: {e}")
            _t.append(time.time())

        # Step 4: TTS — synthesize audio response
        message_id = uuid.uuid4().hex
        output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
        try:
            clean_text = sanitize_for_tts(response_text)
            audio_path = await tts_service.synthesize(clean_text, output_path=output_audio)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"TTS synthesis failed: {e}")
        _t.append(time.time())

        # Log pipeline timing
        t_stt = _t[1] - _t[0]
        t_rag = _t[2] - _t[1]
        t_llm = _t[3] - _t[2]
        t_tts = _t[4] - _t[3]
        t_total = _t[4] - _t[0]
        logger.info("Pipeline: STT=%.2fs RAG=%.2fs LLM=%.2fs TTS=%.2fs TOTAL=%.2fs",
                     t_stt, t_rag, t_llm, t_tts, t_total)

        # Store message in conversation with turn tracking
        turn_number = len(conversations[conversation_id].get("turns", []))
        new_turn = {
            "n": turn_number,
            "user_text": user_text,
            "assistant_text": response_text,
            "chunks_used": [],  # Non-streaming doesn't track chunks yet
        }
        conversations[conversation_id]["turns"].append(new_turn)
        # Update rolling summary for conversation memory
        update_conversation_summary(conversation_id, new_turn)
        conversations[conversation_id]["last_activity_at"] = datetime.utcnow().isoformat()
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


@app.post("/api/conversation/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, audio: UploadFile = File(...)):
    """Streaming version: STT + RAG + LLM (SSE tokens) + TTS + audio URL.

    Events:
      - transcription: {"text": "..."}
      - token: {"text": "..."}        (one per LLM chunk)
      - audio_url: {"url": "..."}
      - error: {"detail": "..."}
      - done: {}
    """
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="Invalid audio format")

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=422, detail="Empty audio file")

    MAX_AUDIO_SIZE = 5 * 1024 * 1024
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=422, detail="Audio too long (max 30 seconds)")

    temp_audio = config.AUDIO_DIR / f"input_{conversation_id}_{uuid.uuid4().hex}.webm"
    temp_audio.write_bytes(audio_bytes)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        full_response = ""
        _t_start = time.time()

        try:
            # ── Step 1: STT ──────────────────────────────────────
            user_text = await asyncio.to_thread(stt_service.transcribe, temp_audio)
            _t_stt = time.time()
            logger.info("Stream STT: %.2fs", _t_stt - _t_start)
            yield sse_format("transcription", {"text": user_text})

            if not user_text.strip():
                yield sse_format("error", {"detail": "No se detectó voz en el audio"})
                return

            # ── Farewell check ──────────────────────────────────
            if detect_farewell(user_text):
                farewell = "¡Gracias a ti! Ha sido un placer. Si tenés más preguntas en el futuro, acá estoy. ¡Éxito en tu búsqueda!"
                logger.info("Farewell detected, ending interview")
                for token in farewell.split(" "):
                    yield sse_format("token", {"text": token + " "})
                yield sse_format("interview_end", {"message": farewell})
                # Store the farewell in conversation
                conversations[conversation_id]["messages"].append({
                    "user_text": user_text,
                    "response_text": farewell,
                    "audio_url": "",
                })
                return

            # ── Step 2: Response cache — skip RAG + LLM for frequent questions ──
            cached_response = get_cached_response(user_text)
            if cached_response is not None:
                logger.info("Cache hit (streaming) for: %s", user_text)
                yield sse_format("token", {"text": cached_response})

                # Synthesize the whole cached answer as a single audio file
                message_id = uuid.uuid4().hex
                output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
                try:
                    clean_text = sanitize_for_tts(cached_response)
                    await tts_service.synthesize(clean_text, output_path=output_audio)
                except RuntimeError as e:
                    logger.error("TTS synthesis failed for cached answer: %s", e)
                    yield sse_format("error", {"detail": f"TTS synthesis failed: {e}"})
                    return

                # Store the exchange in conversation memory (same as LLM path)
                turn_number = len(conversations[conversation_id].get("turns", []))
                new_turn = {
                    "n": turn_number,
                    "user_text": user_text,
                    "assistant_text": cached_response,
                    "chunks_used": [],  # Cache hit: no RAG chunks
                }
                conversations[conversation_id]["turns"].append(new_turn)
                update_conversation_summary(conversation_id, new_turn)
                conversations[conversation_id]["last_activity_at"] = datetime.utcnow().isoformat()
                conversations[conversation_id]["messages"].append({
                    "user_text": user_text,
                    "response_text": cached_response,
                    "audio_url": f"/audio/{conversation_id}/{message_id}.mp3",
                })

                yield sse_format("audio_url", {"url": f"/audio/{conversation_id}/{message_id}.mp3"})
                yield sse_format("done", {})
                return

            # ── Step 3: RAG ──────────────────────────────────────
            context_chunks = rag_pipeline.get_chunks_with_scores(user_text, top_k=config.RAG_TOP_K)
            context = rag_pipeline.get_context_string(user_text, top_k=config.RAG_TOP_K)
            _t_rag = time.time()

            # ── Step 4: LLM streaming + sentence detection ──────
            # Build conversation context (rolling summary + recent turns) for memory
            conversation_context = build_conversation_context(conversation_id, recent_count=3)
            system_prompt = build_system_prompt(context, conversation_context=conversation_context)
            loop = asyncio.get_running_loop()
            sentence_buf = SentenceBuffer()
            tts_futures: dict[asyncio.Task, int] = {}  # task → sentence_id
            sentence_id = 0
            listening_to_llm = True

            def run_llm_stream():
                try:
                    for token in llm_service.generate_stream_with_context(
                        prompt=user_text, context=context, system_prompt=system_prompt,
                        context_chunks=context_chunks,
                    )[0]:
                        loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
                        sentences = sentence_buf.add_token(token)
                        for s in sentences:
                            loop.call_soon_threadsafe(queue.put_nowait, ("sentence", s))
                    for s in sentence_buf.flush():
                        loop.call_soon_threadsafe(queue.put_nowait, ("sentence", s))
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))

            loop.run_in_executor(None, run_llm_stream)

            # ── Step 5: Event loop — LLM tokens + TTS completions ──
            queue_task = None
            while listening_to_llm or tts_futures:
                pending = list(tts_futures.keys())
                if listening_to_llm:
                    # Reuse queue_task if it wasn't consumed
                    if queue_task is None or queue_task.done():
                        queue_task = asyncio.create_task(queue.get())
                    pending.append(queue_task)

                if not pending:
                    break

                done_set, _ = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )

                for done in done_set:
                    if listening_to_llm and done is queue_task:
                        kind, data = done.result()
                        queue_task = None  # Reset so next iteration creates a new one

                        if kind == "done":
                            listening_to_llm = False

                        elif kind == "error":
                            logger.error("LLM streaming error: %s", data)
                            yield sse_format("error", {"detail": f"Error en respuesta: {data}"})
                            return

                        elif kind == "token":
                            full_response += data
                            yield sse_format("token", {"text": data})

                        elif kind == "sentence":
                            # Sanitize before TTS (remove markdown/emoji)
                            clean_sentence = sanitize_for_tts(data)
                            if not clean_sentence:
                                continue
                            # Launch TTS for this sentence — runs in parallel with LLM
                            try:
                                task = asyncio.create_task(
                                    tts_service.synthesize_sentence(
                                        clean_sentence, sentence_id,
                                        output_dir=config.AUDIO_DIR / conversation_id,
                                    )
                                )
                                tts_futures[task] = sentence_id
                                sentence_id += 1
                            except Exception as e:
                                logger.error("TTS task creation failed for sentence %d: %s", sentence_id, e)
                                yield sse_format("error", {"detail": "TTS synthesis failed", "id": sentence_id})
                                sentence_id += 1
                    else:
                        # A TTS task completed — yield the audio chunk immediately
                        try:
                            sid, audio_path = done.result()
                            yield sse_format("audio_chunk", {
                                "id": sid,
                                "url": f"/audio/{conversation_id}/{audio_path.name}"
                            })
                        except Exception as e:
                            logger.error("TTS task %s failed: %s", done, e)
                            sid = tts_futures.get(done, -1)
                            yield sse_format("error", {"detail": "TTS synthesis failed", "id": sid})
                        del tts_futures[done]

            _t_llm = time.time()
            logger.info("Stream LLM + TTS interleaved: %.2fs total, %d sentences",
                         _t_llm - _t_rag, sentence_id)

            # Store full message and turn with chunks_used
            turn_number = len(conversations[conversation_id].get("turns", []))
            new_turn = {
                "n": turn_number,
                "user_text": user_text,
                "assistant_text": full_response,
                "chunks_used": context_chunks,
            }
            conversations[conversation_id]["turns"].append(new_turn)
            # Update rolling summary for conversation memory
            update_conversation_summary(conversation_id, new_turn)
            conversations[conversation_id]["last_activity_at"] = datetime.utcnow().isoformat()
            conversations[conversation_id]["messages"].append({
                "user_text": user_text,
                "response_text": full_response,
                "audio_url": f"/audio/{conversation_id}/",  # multiple chunks
            })

            yield sse_format("done", {})

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Stream pipeline error: %s", e, exc_info=True)
            yield sse_format("error", {"detail": str(e)})
        finally:
            if temp_audio.exists():
                temp_audio.unlink(missing_ok=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/conversation/{conversation_id}/context")
async def get_conversation_context(conversation_id: str, turn: int = 0):
    """Return the RAG chunks used for a specific conversation turn."""
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv = conversations[conversation_id]
    turns = conv.get("turns", [])
    matching_turn = next((t for t in turns if t["n"] == turn), None)

    if matching_turn is None:
        raise HTTPException(status_code=404, detail=f"Turn {turn} not found")

    return matching_turn.get("chunks_used", [])


# Serve frontend static files
if config.FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")
