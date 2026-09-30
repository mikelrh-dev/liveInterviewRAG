"""InterviewTTS — application composition root.

This module builds the object graph and nothing else. It constructs every
service once, assembles the FastAPI application, and starts the background
sweep. The behaviour lives in focused modules beside it:

    backend.routers.*      the HTTP surface, one module per resource
    backend.turns.*        one request in, one answer out
    backend.conversation   in-process session state and write-through
    backend.maintenance    the periodic sweep
    backend.uploads        audio intake and the single size ceiling
    backend.middleware     guards that must run before a route
    backend.client_ip      client identity behind a reverse proxy
    backend.sse            the SSE wire format
    backend.farewell       end-of-interview detection

The service objects defined below are the process-wide singletons, and those
module-level names are the canonical handle on them. Everything else reaches
them through ``backend.container``, which reads them off this module on each
call: binding an instance at import time would freeze it, and the singletons are
swappable at runtime (the test suite replaces them with doubles).

This module also re-exports the names the rest of the backend, and the test
suite, import from here — ``conversations``, ``periodic_cleanup``,
``send_message`` and friends now live in the modules above but keep working
from here so no caller has to know the move happened.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything else
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.client_ip import resolve_client_ip  # noqa: F401  (re-exported)
from backend.config import config
from backend.conversation import (  # noqa: F401  (re-exported)
    _rate_limit_store,
    build_conversation_context,
    conversations,
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
from backend.routers import conversations as conversation_routes
from backend.routers import system as system_routes
from backend.routers import turns as turn_routes
from backend.routers.turns import send_message, send_message_stream  # noqa: F401
from backend.services.candidate import CandidateProfile
from backend.services.llm import LLMService
from backend.services.persistence import PersistenceService
from backend.services.rag import RAGPipeline
from backend.services.report import ReportService
from backend.services.stt import STTService
from backend.services.tts import TTSService
from backend.uploads import MAX_AUDIO_SIZE  # noqa: F401  (re-exported)

logger = logging.getLogger(__name__)

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

# NOTE: TTSService.__init__ also creates its output directory. That is a side
# effect of a service, not a statement of intent, so the mount below does not
# rely on it -- see ensure_audio_dir().
tts_service = TTSService(
    voice=config.TTS_VOICE,
    output_dir=config.AUDIO_DIR,
)

# CHUNK_SIZE=400 / CHUNK_OVERLAP=50 are unreachable with the current corpus, so
# RAGPipeline._chunk_document never takes its splitting branch.
#
# Measured on the 37 wiki pages CandidateProfile loads, chunked by
# _chunk_document at 400/50: 124 chunks, median 54 words, p95 131, longest 266,
# and ZERO chunks reach 400 words. The distribution is NOT bottom-heavy -- 0.0%
# are under 15 words, and 13.7% are under 30 -- so the previous version of this
# comment, which claimed 37.4% under 15 and concluded that a ceiling which
# actually bites "would need to be far lower", had the shape of the corpus
# backwards. The conclusion that survives is the one above, and it survives for
# the opposite reason: not because the chunks cluster at the short end, but
# because the longest is 266 words, 134 short of the ceiling.
# CHUNK_OVERLAP is inert for the same reason.
#
# RE-MEASURED 2026-09-29, one chunk down: 125 -> 124, and the short tail has
# gone from 0.8% to 0.0%. Both come from the same change -- a heading section
# with no body under it is no longer emitted (see `is_bare_heading`). The
# `## Alternativas consideradas` section of
# `decisions/fraud-detector-3-layer-architecture.md` was the corpus's ONLY
# chunk under 15 words. It was also the top-1 context for 3 of the 49 labelled
# questions under the multilingual embedder, so removing it costs the
# distribution one chunk and the reader one restated heading.
#
# The corpus is wiki/, which .gitignore excludes and which is therefore absent
# from a clean clone -- a reader elsewhere cannot reproduce these figures and
# should not be left to assume they are universal.
# tests/test_chunk_size_comment.py re-derives every number above and fails when
# the prose and the measurement disagree; it skips where wiki/ is absent, for
# the same reason. Whether 400 is the RIGHT size rather than merely an inert one
# is a separate question, answered by measurement in
# tests/test_rag_chunk_size_sweep.py -- whose own figures are fixture-derived,
# because a clean clone has no real corpus to sweep.
#
# Left as-is because rag.py is out of scope for this change; the constants are
# consumed here, so this is the place the measurement belongs until then.
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

# The semantic answer cache (Cap-3) used to be constructed here, sharing this
# module's DB and reusing the RAG embedder. It is gone: measured with the real
# all-MiniLM-L6-v2 that this pipeline shipped at the time, the shipped threshold
# could not be reached by any paraphrase and lowering it to a
# zero-false-positive floor still served ~7% of them, because an English-only
# embedder cannot separate a Spanish paraphrase from a different Spanish
# question. The corpus is Spanish, so that embedder is no longer the default
# (see config.py: EMBEDDING_MODEL); this cache stays removed regardless, and
# re-measuring it against the multilingual embedder is a separate piece of work
# that has NOT been done — a cache is not reinstated on the strength of a
# different vector space making the old numbers look obsolete.
# It also retained the recruiter's raw question for
# 14 days while the transcript expires in 2. The measurement is kept, and kept
# honest, in tests/test_rag.py::TestSemanticAnswerCacheWasNotViable. The FAQ
# literal cache (services/response_cache.py) is unaffected and still works.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle manager."""
    # Startup
    logger.info("Starting InterviewTTS backend...")

    # Load candidate profile
    candidate_profile.load()
    if candidate_profile.documents:
        rag_pipeline.ingest_documents(candidate_profile.documents)
        # The mode rides on the startup line because a chunk count cannot
        # distinguish the two. This one read as "RAG pipeline initialized with
        # 20 chunks" whether the embedder had loaded or the pipeline had fallen
        # back to TF-IDF, so the one fact an operator needs at boot was the one
        # fact the log did not carry.
        logger.info(
            "RAG pipeline initialized with %d chunks (mode: %s)",
            len(rag_pipeline.chunks),
            rag_pipeline.mode,
        )
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
    # Close the shared LLM HTTP client (Cap-1 keep-alive) exactly once.
    # This is a module-level function in services/llm.py, not a method on the
    # service object: only LLMService (the class) is imported here, so
    # referencing `llm` raised NameError and the clients never closed.
    from backend.services.llm import close_http_clients

    close_http_clients()
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

# CORS — restricted in production, configurable via env var.
#
# The origins come from config, which parses and strips them. This used to be
# os.getenv("CORS_ORIGINS").split(",") here, which meant the padding around each
# comma-separated value survived into CORSMiddleware: an origin declared as
# " https://site " never matches the Origin header a browser sends, so the
# request fails CORS and the failure looks like a policy problem rather than a
# stray space. Routing it through Config also means the value can be defaulted,
# validated and constructed in a test without mutating the environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
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

# Endpoints. The frontend mount below claims "/", so it has to come last.
app.include_router(system_routes.router)
app.include_router(conversation_routes.router)
app.include_router(turn_routes.router)

# Serve frontend static files
if config.FRONTEND_DIR.exists():
    app.mount(
        "/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend"
    )
