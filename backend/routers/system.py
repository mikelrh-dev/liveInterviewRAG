"""Read-only endpoints: service health and the active model configuration.

Paths are spelled out in full rather than assembled from a router prefix, so
``grep '/api/'`` is a complete map of the HTTP surface.
"""

import logging

from fastapi import APIRouter

from backend import container
from backend.config import config

logger = logging.getLogger(__name__)

router = APIRouter()

#: The retrieval mode the service is supposed to be in. Anything else is a
#: degradation: ``tfidf`` is the fallback, and ``uninitialized`` means no model
#: has been loaded at all. Exported so the rule is stated once.
EXPECTED_RAG_MODE = "embeddings"

#: The store states that are not a fault. ``disabled`` is a decision the
#: deployment made, not something that went wrong.
_HEALTHY_STORE_STATES = frozenset({"ok", "disabled"})


def _persistence_state() -> str:
    """Ask the store whether it is reachable, and refuse to guess.

    Two failures this has to survive, and they are different in kind:

    * A store whose ``health()`` raises. A ``GET`` that a browser and an
      external monitor both poll must not 500, because a connection error is a
      worse signal than an honest one -- it is not a fact about the service.
    * A store that answers with something this build does not recognise. Same
      reasoning as ``conversation.store_is_configured``, and the two errors are
      not symmetric: reading an unfamiliar answer as "ok" paints a green rail
      over a store nobody has heard from, while reading it as "error" costs one
      amber rail until the store learns to speak for itself.
    """
    try:
        state = str(container.persistence().health())
    except Exception as e:
        # The reason stays in the store's own log; this body is public.
        logger.error("Persistence health probe raised: %s", e)
        return "error"
    return state if state in _HEALTHY_STORE_STATES else "error"


@router.get("/api/health")
async def health_check():
    """Return service health, and a status DERIVED from the rest of the body.

    ``status`` used to be the literal ``"ok"``. Nothing computed it, so nothing
    could contradict it, and the three things that matter most are all silent
    from the outside:

    * ``rag_mode`` used to be absent, and everything else here is invariant
      under the TF-IDF fallback -- the chunk count is the same, the profile
      still loads, and Whisper still has its model. So a deployment that had
      lost its embedding model answered ``status: "ok"`` and the status rail
      painted a green dot over a retrieval pipeline that was no longer the one
      it claimed to be.
    * A zero chunk count is a real answer to "can this service retrieve
      anything", and nothing read it.
    * The store's failure policy is that no method ever raises, so an
      unreachable database produced a log line and no other visible fact at
      all.

    So ``status`` is now the conjunction of the signals this service already
    knows how to report, and ``problems`` names which of them failed. The HTTP
    code is unchanged on purpose: a degraded body is not an outage, the page
    that polls this is served by the same process, and every other field in
    the payload is still true.
    """
    whisper_loaded = container.stt_service().is_loaded
    rag_chunks = len(container.rag_pipeline().chunks)
    rag_mode = container.rag_pipeline().mode
    candidate_loaded = container.candidate_profile().profile_data is not None
    persistence = _persistence_state()

    problems = []
    if not whisper_loaded:
        problems.append("whisper_loaded")
    if rag_chunks == 0:
        problems.append("rag_chunks")
    if rag_mode != EXPECTED_RAG_MODE:
        problems.append("rag_mode")
    if not candidate_loaded:
        problems.append("candidate_loaded")
    if persistence not in _HEALTHY_STORE_STATES:
        problems.append("persistence")

    return {
        "status": "ok" if not problems else "degraded",
        "problems": problems,
        "whisper_loaded": whisper_loaded,
        "rag_chunks": rag_chunks,
        "rag_mode": rag_mode,
        "candidate_loaded": candidate_loaded,
        "persistence": persistence,
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
