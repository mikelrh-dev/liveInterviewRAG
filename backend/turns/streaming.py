"""The streaming turn: SSE events, interleaved LLM and TTS, one terminal event.

The event contract below is the whole public surface of this module and is kept
in lockstep with the dispatcher in ``frontend/app.js``.
``tests/test_sse_contract.py`` enforces that every name emitted here has a
frontend branch, because an unhandled event is dropped silently rather than
reported.

    - transcription:  {"text": "..."}                 always first
    - token:          {"text": "..."}                 one per LLM chunk
    - audio_url:      {"url": "..."}                  cached/farewell
                      {"id": int, "url": "..."}        per-sentence stream
    - error:          {"detail": "..."}               fatal, non-recoverable
                      {"detail": "...", "id": int}    recoverable, per-chunk
    - done:           {"n": int, "has_context": bool} terminal (normal turn)
                      {}                              terminal (nothing stored)
    - interview_end:  {"message": "..."}              terminal (farewell)

Exactly one terminal event is emitted per stream. ``audio_url`` is the single
canonical audio event name; the optional ``id`` is the frontend playback cursor
and is absent when the answer is one whole file.

``done`` is the only event that names a turn, and the name is the one the DB
committed — the frontend must not count transcript elements to find it. An empty
``done`` payload means no turn was stored (a failed write, an empty
transcription, an LLM that died mid-stream), so the client asks the Context
panel about nothing. ``interview_end`` names no turn: the farewell is written
after the terminal event on purpose, so the goodbye never queues behind a slow
disk.

This module is where the two endpoints still diverge: only the streaming path
detects a farewell and ends the interview. That is a known gap, not an
oversight, and unifying it is follow-up work rather than something to smuggle
into a refactor.
"""

import asyncio
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException

from backend import container
from backend.config import config
from backend.conversation import (
    build_conversation_context,
    build_turn,
    conversations,
    persist_turn,
    touch_activity,
    turn_done_payload,
)
from backend.farewell import FAREWELL_TEXT, detect_farewell
from backend.prompts.candidate import build_system_prompt, sanitize_for_tts
from backend.services.llm import SentenceBuffer
from backend.sse import sse_format
from backend.turns.answer_source import LLM, resolve_answer_source
from backend.turns.errors import (
    FAREWELL_TTS_FAILED,
    LLM_FAILED,
    TTS_CHUNK_FAILED,
    TTS_FAILED,
    UNEXPECTED_ERROR,
)

logger = logging.getLogger(__name__)


def build_stream(
    conversation_id: str, temp_audio: Path
) -> AsyncIterator[str]:
    """Return the SSE generator for one turn on a staged recording.

    ``temp_audio`` is already validated and written by ``uploads.stage_upload``.
    It is unlinked in the generator's ``finally``, which runs when the response
    body finishes being consumed — not when this function returns, which is why
    staging hands cleanup back to the caller instead of doing it itself.
    """

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        full_response = ""
        _t_start = time.time()
        # Terminal-event contract: exactly one `done` or `interview_end` per
        # stream. Set at each terminal yield and asserted in the finally block,
        # so a new early return cannot silently truncate the stream.
        terminal_emitted = False

        try:
            # ── Step 1: STT ──────────────────────────────────────
            user_text = await asyncio.to_thread(
                container.stt_service().transcribe, temp_audio
            )
            _t_stt = time.time()
            logger.info("Stream STT: %.2fs", _t_stt - _t_start)
            yield sse_format("transcription", {"text": user_text})

            if not user_text.strip():
                # A silent recording is a failed turn, not a failed interview:
                # report it and terminate so the frontend can hand the mic
                # back. Without a terminal event the stream just stops, which
                # the browser cannot distinguish from a network drop.
                yield sse_format("error", {"detail": "No se detectó voz en el audio"})
                yield sse_format("done", {})
                terminal_emitted = True
                return

            # First-substantive-turn rule (design D10): evaluated post-hydration,
            # pre-generation. Only the recruiter's opening question is ever
            # looked up or stored in the semantic cache.
            is_first_substantive = len(
                conversations[conversation_id].get("turns", [])
            ) == 0

            async def emit_cached_answer(response_text: str):
                """Shared FAQ/semantic hit contract (verbatim token, single-file
                TTS, chunks tracked for the panel, memory + DB write-through,
                audio_url + done). On TTS failure it reports the error, stores
                nothing, and still terminates the stream with `done`.
                """
                nonlocal terminal_emitted
                yield sse_format("token", {"text": response_text})

                # Synthesize the answer as a single audio file
                message_id = uuid.uuid4().hex
                output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
                try:
                    clean_text = sanitize_for_tts(response_text)
                    await container.tts_service().synthesize(
                        clean_text, output_path=output_audio
                    )
                except Exception as e:
                    logger.error(
                        "TTS synthesis failed for cached answer: %s",
                        e,
                        exc_info=True,
                    )
                    yield sse_format("error", {"detail": TTS_FAILED})
                    yield sse_format("done", {})
                    terminal_emitted = True
                    return

                # Retrieve chunks for context tracking (same as LLM path)
                context_chunks = container.rag_pipeline().get_chunks_with_scores(
                    user_text, top_k=2
                )

                # Store the exchange in conversation memory
                audio_url = f"/audio/{conversation_id}/{message_id}.mp3"
                new_turn, new_message = build_turn(
                    conversation_id, user_text, response_text, context_chunks, audio_url
                )
                touch_activity(conversation_id)

                # Write-through: persist the cache-hit exchange atomically,
                # then let the committed n decide what memory keeps.
                committed = await persist_turn(
                    conversation_id, new_turn, new_message
                )

                yield sse_format("audio_url", {"url": audio_url})
                yield sse_format("done", turn_done_payload(committed))
                terminal_emitted = True

            # ── Farewell check ──────────────────────────────────
            if detect_farewell(user_text):
                farewell = FAREWELL_TEXT
                logger.info("Farewell detected, ending interview")
                for token in farewell.split(" "):
                    yield sse_format("token", {"text": token + " "})

                # Speak the farewell. Ending the interview in silence is the
                # one failure the candidate cannot forgive, so the goodbye
                # goes through TTS on the same pattern as every other call
                # site. A failure degrades to a reported but silent goodbye
                # rather than a stream that never terminates.
                message_id = uuid.uuid4().hex
                output_audio = config.AUDIO_DIR / f"{conversation_id}/{message_id}.mp3"
                audio_url = ""
                try:
                    clean_farewell = sanitize_for_tts(farewell)
                    await container.tts_service().synthesize(
                        clean_farewell, output_path=output_audio
                    )
                    audio_url = f"/audio/{conversation_id}/{message_id}.mp3"
                except Exception as e:
                    logger.error(
                        "Farewell TTS synthesis failed: %s", e, exc_info=True
                    )
                    yield sse_format("error", {"detail": FAREWELL_TTS_FAILED})

                # Queue the audio before the terminal event: once
                # interview_end tears the session down, queued audio is dropped.
                if audio_url:
                    yield sse_format("audio_url", {"url": audio_url})

                yield sse_format("interview_end", {"message": farewell})
                terminal_emitted = True
                # Store the farewell in conversation (messages + turns stay in
                # sync so build_conversation_context sees the full history).
                # audio_url is empty when TTS failed, so a later read of the
                # transcript never points at a file that was never written.
                farewell_turn, farewell_message = build_turn(
                    conversation_id, user_text, farewell, [], audio_url
                )
                # Write-through: persist the closing exchange atomically, then
                # let the committed n decide what memory keeps. Runs after
                # interview_end on purpose — the goodbye must not queue behind
                # a slow disk. Memory is reconciled before the report is built,
                # so the report describes exactly what survived the write.
                await persist_turn(conversation_id, farewell_turn, farewell_message)
                # Post-hoc report — must never break the SSE stream.
                # to_thread keeps the event loop free during the file write.
                report_path = await asyncio.to_thread(
                    container.report_service().generate,
                    conversation_id,
                    conversations.get(conversation_id),
                )
                if report_path is not None:
                    await asyncio.to_thread(
                        container.persistence().record_report,
                        conversation_id,
                        str(report_path),
                    )
                return

            # ── Step 2: cache precedence (FAQ literal, then semantic) ──
            source, cached_text = resolve_answer_source(
                user_text,
                is_first_substantive=is_first_substantive,
                path_label=" (streaming)",
            )
            if source != LLM:
                async for event in emit_cached_answer(cached_text):
                    yield event
                return

            # ── Step 3: RAG ──────────────────────────────────────
            context_chunks = container.rag_pipeline().get_chunks_with_scores(
                user_text, top_k=config.RAG_TOP_K
            )
            context = container.rag_pipeline().get_context_string(
                user_text, top_k=config.RAG_TOP_K
            )
            _t_rag = time.time()

            # ── Step 4: LLM streaming + sentence detection ──────
            # Build conversation context (rolling summary + recent turns) for memory
            conversation_context = build_conversation_context(
                conversation_id, recent_count=3
            )
            system_prompt = build_system_prompt(
                context, conversation_context=conversation_context
            )
            loop = asyncio.get_running_loop()
            sentence_buf = SentenceBuffer()
            tts_futures: dict[asyncio.Task, int] = {}  # task → sentence_id
            sentence_id = 0
            listening_to_llm = True

            def run_llm_stream():
                try:
                    for token in container.llm_service().generate_stream_with_context(
                        prompt=user_text,
                        context=context,
                        system_prompt=system_prompt,
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
                    # Logged here, on the thread that still has the exception
                    # object, because the queue carries a string and the
                    # traceback would otherwise be lost at the thread boundary.
                    logger.error("LLM streaming error: %s", e, exc_info=True)
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
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for done in done_set:
                    if listening_to_llm and done is queue_task:
                        kind, data = done.result()
                        queue_task = None  # Reset so next iteration creates a new one

                        if kind == "done":
                            listening_to_llm = False

                        elif kind == "error":
                            # `data` is the provider's failure text. It reached
                            # this point through an executor thread, where the
                            # traceback is already gone, so it stays in the log
                            # (logged in full at the raise site) and never in
                            # the payload.
                            logger.error("LLM streaming error: %s", data)
                            yield sse_format("error", {"detail": LLM_FAILED})
                            # Terminate: a provider that dies mid-generation
                            # leaves nothing to stream, and the frontend needs
                            # a terminal event to hand the mic back.
                            yield sse_format("done", {})
                            terminal_emitted = True
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
                                    container.tts_service().synthesize_sentence(
                                        clean_sentence,
                                        sentence_id,
                                        output_dir=config.AUDIO_DIR / conversation_id,
                                    )
                                )
                                tts_futures[task] = sentence_id
                                sentence_id += 1
                            except Exception as e:
                                logger.error(
                                    "TTS task creation failed for sentence %d: %s",
                                    sentence_id,
                                    e,
                                    exc_info=True,
                                )
                                yield sse_format(
                                    "error",
                                    {
                                        "detail": TTS_CHUNK_FAILED,
                                        "id": sentence_id,
                                    },
                                )
                                sentence_id += 1
                    else:
                        # A TTS task completed — yield the audio chunk immediately
                        try:
                            sid, audio_path = done.result()
                            yield sse_format(
                                "audio_url",
                                {
                                    "id": sid,
                                    "url": f"/audio/{conversation_id}/{audio_path.name}",
                                },
                            )
                        except Exception as e:
                            logger.error("TTS task %s failed: %s", done, e, exc_info=True)
                            sid = tts_futures.get(done, -1)
                            yield sse_format(
                                "error",
                                {"detail": TTS_CHUNK_FAILED, "id": sid},
                            )
                        del tts_futures[done]

            _t_llm = time.time()
            logger.info(
                "Stream LLM + TTS interleaved: %.2fs total, %d sentences",
                _t_llm - _t_rag,
                sentence_id,
            )

            # Store full message and turn with chunks_used. A streamed answer
            # is many per-sentence files, so the message names the directory
            # rather than one file.
            new_turn, new_message = build_turn(
                conversation_id,
                user_text,
                full_response,
                context_chunks,
                f"/audio/{conversation_id}/",  # multiple chunks
            )
            touch_activity(conversation_id)

            # Write-through: persist turn + message + activity atomically, then
            # let the committed n decide what memory keeps.
            committed = await persist_turn(conversation_id, new_turn, new_message)
            # Cache the fresh answer for future paraphrased first questions
            if is_first_substantive:
                await asyncio.to_thread(
                    container.semantic_cache().store, user_text, full_response
                )

            yield sse_format("done", turn_done_payload(committed))
            terminal_emitted = True

        except HTTPException:
            # Deliberate escape hatch: re-raise rather than report a
            # half-started stream. No code path inside the generator raises
            # this today (all validation happens before StreamingResponse is
            # built), so it is a guard, not a live branch.
            raise
        except Exception as e:
            logger.error("Stream pipeline error: %s", e, exc_info=True)
            # An exception mid-iteration is the one case the client cannot see
            # coming, so it must arrive as a reported error AND a terminal
            # event. Without the terminal event the browser sees a truncated
            # body, which it reads as a network drop: the mic never restarts.
            #
            # The detail is the generic one: this is the broadest catch in the
            # module, so `e` is whatever the failing library happened to raise
            # -- sentence-transformers and httpx both put paths in their text.
            # The traceback above is the record; this is the notice.
            if not terminal_emitted:
                yield sse_format("error", {"detail": UNEXPECTED_ERROR})
                yield sse_format("done", {})
                terminal_emitted = True
        finally:
            if not terminal_emitted:
                # Two very different situations land here and must not share a
                # log level, or every ordinary disconnect drowns the real bugs.
                #
                # GeneratorExit is a BaseException, so it is not caught by the
                # `except Exception` above: a client closing the tab, navigating
                # away or losing the connection raises it at the current yield.
                # The browser settles on EOF, so this is normal operation.
                #
                # Anything else means a path returned or was cancelled without a
                # terminal event, which is a real defect.
                if sys.exc_info()[0] is GeneratorExit:
                    logger.info(
                        "Stream for %s closed by the client before completion",
                        conversation_id,
                    )
                else:
                    # Logged rather than patched over: a terminal event emitted
                    # from this finally would risk yielding into a closing
                    # generator, and an unterminated stream is a bug worth
                    # seeing.
                    logger.error(
                        "Stream for %s ended without a terminal event",
                        conversation_id,
                        exc_info=True,
                    )
            if temp_audio.exists():
                temp_audio.unlink(missing_ok=True)

    return event_generator()
