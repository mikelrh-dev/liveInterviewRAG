"""Audio upload intake: the size ceiling, validation, and temp-file staging.

``MAX_AUDIO_SIZE`` is the single source of truth for the maximum accepted
request body. It is enforced twice on purpose: ``MaxBodySizeMiddleware`` rejects
oversized uploads from the declared ``Content-Length`` *before* the body is
parsed, and ``stage_upload`` re-checks afterwards for chunked uploads that carry
no ``Content-Length`` at all.

Staging the upload here keeps both turn pipelines starting from the same
validated file instead of each re-deriving the path and the extension.
"""

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from backend.config import config

MAX_AUDIO_SIZE = 5 * 1024 * 1024  # 5MB


# ─── Audio extension mapping ────────────────────────────
_CONTENT_TYPE_EXT = {
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
}


def _audio_extension(content_type: str) -> str:
    """Derive temp-file extension from MIME content_type."""
    if not content_type:
        return ".webm"
    # Strip parameters (e.g. "audio/mp4; codecs=mp4a.40.2") and normalize case
    base_type = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(base_type, ".webm")


async def stage_upload(conversation_id: str, audio: UploadFile) -> Path:
    """Validate an uploaded recording and write it to the audio directory.

    Returns the path of the temp file. The caller owns it and must unlink it
    when the turn is over; staging does not clean up on its own because the
    streaming pipeline only finishes once the response body has been consumed.

    Raises:
        HTTPException: 422 for a non-audio, empty, or oversized body.
    """
    if not audio.content_type or not audio.content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="Invalid audio format")

    audio_bytes = await audio.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=422, detail="Empty audio file")

    # Defence in depth: the MaxBodySizeMiddleware guard already rejected
    # declared oversize bodies before parsing, so this only fires for chunked
    # uploads.
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=422, detail="Audio too long (max 30 seconds)")

    ext = _audio_extension(audio.content_type)
    temp_audio = config.AUDIO_DIR / f"input_{conversation_id}_{uuid.uuid4().hex}{ext}"
    temp_audio.write_bytes(audio_bytes)
    return temp_audio
