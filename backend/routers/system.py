"""Read-only endpoints: service health and the active model configuration.

Paths are spelled out in full rather than assembled from a router prefix, so
``grep '/api/'`` is a complete map of the HTTP surface.
"""

from fastapi import APIRouter

from backend import container
from backend.config import config

router = APIRouter()


@router.get("/api/health")
async def health_check():
    """Return service health status."""
    return {
        "status": "ok",
        "whisper_loaded": container.stt_service().is_loaded,
        "rag_chunks": len(container.rag_pipeline().chunks),
        "candidate_loaded": container.candidate_profile().profile_data is not None,
    }


@router.get("/api/config")
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
