"""The two message endpoints: blocking and streaming.

These handlers are transport only — validate the upload, hand a staged file to
the pipeline, clean up. The turn itself lives in ``backend.turns``; keeping the
boundary here means the two endpoints can be compared line by line when their
behaviour is found to differ.
"""

import logging

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from backend.conversation import get_conversation_or_hydrate
from backend.turns.blocking import run_turn
from backend.turns.streaming import build_stream
from backend.uploads import stage_upload

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/conversation/{conversation_id}/message")
async def send_message(conversation_id: str, audio: UploadFile = File(...)):
    """Process a voice message through the full pipeline: STT → RAG → LLM → TTS."""
    # Validate the conversation exists, and hydrate it from the DB on a memory
    # miss. The call is for its effect on the store, not for a return value: the
    # first-substantive-turn rule (design D10) used to read the hydrated turns
    # here, but it gated the semantic answer cache, and that cache is gone.
    # Nothing consults the flag now, so nothing derives it.
    await get_conversation_or_hydrate(conversation_id)

    temp_audio = await stage_upload(conversation_id, audio)

    try:
        return await run_turn(conversation_id, temp_audio)
    finally:
        # Clean up temp audio
        if temp_audio.exists():
            temp_audio.unlink(missing_ok=True)


@router.post("/api/conversation/{conversation_id}/message/stream")
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
