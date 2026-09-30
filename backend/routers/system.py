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
    """Return service health status.

    ``rag_mode`` is the field that makes a degraded pipeline visible. It used to
    be absent, and everything else here is invariant under the TF-IDF fallback:
    the chunk count is the same, the profile still loads, and Whisper still has
    its model. So a deployment that had lost its embedding model answered
    ``status: "ok"`` and the status rail painted a green dot over a retrieval
    pipeline that was no longer the one it claimed to be.
    """
    return {
        "status": "ok",
        "whisper_loaded": container.stt_service().is_loaded,
        "rag_chunks": len(container.rag_pipeline().chunks),
        "rag_mode": container.rag_pipeline().mode,
        "candidate_loaded": container.candidate_profile().profile_data is not None,
    }


@router.get("/api/config")
async def get_config():
    """Return active model configuration for the sidebar UI.

    ``max_audio_duration`` is the one field here that is not a model name, and
    it earns its place: the page has to bound a recording before it is uploaded,
    and a limit that only exists on the server is a limit the server learns
    about too late. The page already fetches this endpoint on load, so publishing
    the value costs one request that was being made anyway -- and a limit
    published in two places is a limit that will eventually disagree with
    itself.
    """
    return {
        "tts_voice": config.TTS_VOICE,
        "stt_model": config.WHISPER_MODEL,
        "stt_device": config.WHISPER_DEVICE,
        "llm_model": config.LLM_MODEL,
        "google_model": config.GOOGLE_MODEL,
        "rag_top_k": config.RAG_TOP_K,
        "max_tokens": config.LLM_MAX_TOKENS,
        "max_audio_duration": config.MAX_AUDIO_DURATION,
    }
