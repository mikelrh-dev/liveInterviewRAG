"""The non-streaming turn: one request in, one JSON answer out.

STT → cache → RAG → LLM → TTS → persist. Nothing is sent to the browser until
the whole answer exists, which is what makes this endpoint cheap and what makes
it unusable for a 4-8 second generation. The streaming sibling in
``streaming.py`` is the same sequence with the LLM and TTS interleaved.

The two are kept apart on purpose. They differ in more than transport — the
streaming one detects farewells and closes the interview, this one does not —
and merging them is a behaviour change, not a refactor.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from backend import container
from backend.config import config
from backend.conversation import (
    build_conversation_context,
    build_turn,
    persist_turn,
    touch_activity,
)
from backend.prompts.candidate import build_system_prompt, sanitize_for_tts
from backend.turns.answer_source import LLM, resolve_answer_source
from backend.turns.errors import LLM_FAILED, STT_FAILED, TTS_FAILED

logger = logging.getLogger(__name__)


async def run_turn(conversation_id: str, temp_audio: Path) -> dict:
    """Run one full turn on a staged recording and return the JSON body.

    ``temp_audio`` is already validated and written by ``uploads.stage_upload``;
    the caller deletes it when this returns.
    """
    _t = [time.time()]  # t0

    # Step 1: STT — transcribe audio
    try:
        user_text = await asyncio.to_thread(
            container.stt_service().transcribe, temp_audio
        )
    except Exception as e:
        # Logged, not returned. FastAPI renders an HTTPException without a
        # traceback, so before this the provider's failure text was the only
        # copy that existed -- in the response body.
        logger.error("Transcription failed: %s", e, exc_info=True)
        raise HTTPException(status_code=422, detail=STT_FAILED) from e
    _t.append(time.time())

    if not user_text.strip():
        raise HTTPException(status_code=422, detail="No speech detected in audio")

    # Step 2: cache precedence (FAQ literal, then the LLM).
    source, response_text = resolve_answer_source(user_text)

    if source != LLM:
        # A cache hit is already final text, so it skips straight to TTS. The
        # two appends keep the timing indices below aligned: one for the
        # skipped RAG call, one for the skipped LLM call.
        _t.append(time.time())
        _t.append(time.time())  # LLM marker (skipped)
    else:
        # Step 2: RAG — retrieve relevant context
        context = container.rag_pipeline().get_context_string(
            user_text, top_k=config.RAG_TOP_K
        )
        _t.append(time.time())

        # Step 3: LLM — generate response as candidate
        # Build conversation context (rolling summary + recent turns) for memory
        conversation_context = build_conversation_context(
            conversation_id, recent_count=3
        )
        system_prompt = build_system_prompt(
            context, conversation_context=conversation_context
        )
        try:
            response_text = await asyncio.to_thread(
                container.llm_service().generate,
                prompt=user_text,
                context=context,
                system_prompt=system_prompt,
            )
        except RuntimeError as e:
            logger.error("Response generation failed: %s", e, exc_info=True)
            raise HTTPException(status_code=503, detail=LLM_FAILED) from e
        _t.append(time.time())

    # Step 4: TTS — synthesize audio response
    message_id = uuid.uuid4().hex
    output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
    try:
        clean_text = sanitize_for_tts(response_text)
        await container.tts_service().synthesize(clean_text, output_path=output_audio)
    except RuntimeError as e:
        logger.error("TTS synthesis failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=TTS_FAILED) from e
    _t.append(time.time())

    # Log pipeline timing
    t_stt = _t[1] - _t[0]
    t_rag = _t[2] - _t[1]
    t_llm = _t[3] - _t[2]
    t_tts = _t[4] - _t[3]
    t_total = _t[4] - _t[0]
    logger.info(
        "Pipeline: STT=%.2fs RAG=%.2fs LLM=%.2fs TTS=%.2fs TOTAL=%.2fs",
        t_stt,
        t_rag,
        t_llm,
        t_tts,
        t_total,
    )

    # Store message in conversation with turn tracking
    # Track RAG chunks for the context panel on cache hits (tracked, never
    # spoken: appending raw context here made TTS read "[Source: ...]" aloud).
    chunks_for_turn = []
    if source != LLM:
        chunks_for_turn = container.rag_pipeline().get_chunks_with_scores(
            user_text, top_k=2
        )
    audio_url = f"/audio/{conversation_id}/{message_id}.mp3"
    new_turn, new_message = build_turn(
        conversation_id, user_text, response_text, chunks_for_turn, audio_url
    )
    touch_activity(conversation_id)

    # Write-through: persist turn + message + activity atomically, then let
    # the committed n decide what memory keeps.
    await persist_turn(conversation_id, new_turn, new_message)

    return {
        "user_text": user_text,
        "response_text": response_text,
        "audio_url": audio_url,
    }
