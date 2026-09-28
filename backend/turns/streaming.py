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
    - interview_end:  {"message": "...",              terminal (farewell)
                      "n": int, "has_context": bool}
                      {"message": "..."}               terminal (nothing stored)

Exactly one terminal event is emitted per stream, and it is the last event.
``audio_url`` is the single canonical audio event name; the optional ``id`` is
the frontend playback cursor and is absent when the answer is one whole file.

The two events that name a turn — ``done`` and ``interview_end`` — carry the
identical turn-number payload, built by the same function from the turn the DB
actually committed. The frontend must not count transcript elements to find a
turn number, and must not read one event differently from the other. Absent
turn-number fields mean no turn was stored (a failed write, an empty
transcription, an LLM that died mid-stream), so the client asks the Context
panel about nothing.

Each terminal event names the turn it terminates on, which is why the farewell
is written *before* ``interview_end`` rather than after it. The number the DB
commits is the return value of the write, and ``record_turn`` is free to
override the number the pipeline requested from memory, so the request is not a
report — the committed one is. Writing first also keeps the terminal event
last, which is what lets a client stop reading at it. The cost is one write on
a path that has already blocked on TTS for the goodbye, which is the larger
cost by two orders of magnitude.

This module is where the two endpoints still diverge: only the streaming path
detects a farewell and ends the interview. That is a known gap, not an
oversight, and unifying it is follow-up work rather than something to smuggle
into a refactor.
"""

import asyncio
import logging
import sys
import threading
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
        # stream. Assigned IMMEDIATELY BEFORE the terminal sequence is yielded,
        # never after, and asserted in the finally block so a new early return
        # cannot silently truncate the stream.
        #
        # "Before", not "after", is the whole point. A cancellation delivered at
        # a yield -- a client that closes the tab while the last event is being
        # written -- lands inside this generator with the flag still False, and
        # the finally then reports a stream that ended without a terminal event.
        # That is the exact line the two-branch split exists to avoid writing,
        # and it fires on every ordinary disconnect that happens to arrive at one
        # of these yields. Once the terminal sequence has been entered the stream
        # is over; nothing after it can be a defect in the stream's shape.
        terminal_emitted = False
        # Synthesis in flight for this turn. Declared here, not at its first use
        # below, because the `finally` that settles them runs on paths that never
        # reach that line.
        tts_futures: dict[asyncio.Task, int] = {}  # task → sentence_id
        # Cooperative stop for the LLM executor thread. A thread already running
        # cannot be cancelled or joined from the event loop, so the only honest
        # handle is a flag the thread itself checks between tokens.
        llm_stop = threading.Event()

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
                terminal_emitted = True
                yield sse_format("error", {"detail": "No se detectó voz en el audio"})
                yield sse_format("done", {})
                return

            async def emit_cached_answer(response_text: str):
                """Shared FAQ hit contract (verbatim token, single-file
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
                    terminal_emitted = True
                    yield sse_format("error", {"detail": TTS_FAILED})
                    yield sse_format("done", {})
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
                terminal_emitted = True
                yield sse_format("done", turn_done_payload(committed))

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

                # Store the farewell in conversation (messages + turns stay in
                # sync so build_conversation_context sees the full history).
                # audio_url is empty when TTS failed, so a later read of the
                # transcript never points at a file that was never written.
                farewell_turn, farewell_message = build_turn(
                    conversation_id, user_text, farewell, [], audio_url
                )
                # Write-through: persist the closing exchange atomically, then
                # let the committed n decide what memory keeps. Runs *before*
                # the terminal event so `interview_end` can carry the committed
                # number, and so the terminal event is genuinely last.
                #
                # The write is not the bottleneck this ordering was chosen to
                # avoid. The candidate's wait is already dominated by TTS for
                # the goodbye — measured ~1.3 s on this machine — so a ~5.8 ms
                # SQLite write (median, p95 ~8.9 ms, n=200) is 0.4% of it. What
                # the ordering actually bought was a terminal event that named
                # no turn, so the counter fell a turn short of the store, and a
                # client that closes the stream on a terminal event lost the
                # follow-up event entirely.
                #
                # A write that raises is treated exactly like one that returned
                # None: reported, then terminal. Letting it reach the outer
                # handler would emit `error` + `done` and leave the interview
                # running, which is worse than losing a turn.
                try:
                    committed = await persist_turn(
                        conversation_id, farewell_turn, farewell_message
                    )
                except Exception as e:
                    logger.error(
                        "Farewell turn write failed: %s", e, exc_info=True
                    )
                    committed = None

                # The terminal event carries what the DB committed — same
                # builder as `done`, so the two cannot disagree — alongside the
                # goodbye text it always carried. A failed or empty write
                # contributes no turn-number fields, which the client reads as
                # "nothing was stored" rather than as a number to guess.
                terminal_emitted = True
                yield sse_format(
                    "interview_end",
                    {"message": farewell, **turn_done_payload(committed)},
                )

                # Post-hoc report — must never break the SSE stream. Memory was
                # reconciled by the write above, so the report describes exactly
                # what survived it. to_thread keeps the event loop free during
                # the file write.
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

            # ── Step 2: cache precedence (FAQ literal, then the LLM) ──
            source, cached_text = resolve_answer_source(
                user_text,
                path_label=" (streaming)",
            )
            if source != LLM:
                async for event in emit_cached_answer(cached_text):
                    yield event
                return

            # ── Step 3: RAG ──────────────────────────────────────
            # One retrieval, both shapes. These used to be two separate calls
            # that each ran `retrieve()` and therefore each embedded the query:
            # the same question, the same embedding, the same result, twice per
            # turn. `retrieve_with_context` is a single `retrieve()` behind two
            # formatters -- no cache, no per-turn state, nothing to go stale.
            # Read through the container on every call, so the test suite's
            # rebinding of `backend.main.rag_pipeline` still applies.
            context, context_chunks = container.rag_pipeline().retrieve_with_context(
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
                        # Checked per token, not once: the thread is the only
                        # thing that can stop this pull, and the queue it fills
                        # has no reader once the stream is over.
                        if llm_stop.is_set():
                            logger.info(
                                "LLM thread for %s stopping: the stream is over",
                                conversation_id,
                            )
                            return
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

            # Held, not discarded. `run_in_executor` hands back a future that
            # reports the thread's outcome; dropping it on the floor means the
            # only thing that ever hears about this thread finishing is nothing.
            # It cannot be joined -- a thread already running is not the event
            # loop's to wait on -- so `llm_stop` is what actually ends the pull.
            llm_future = loop.run_in_executor(None, run_llm_stream)

            # ── Step 5: Event loop — LLM tokens + TTS completions ──
            queue_task = None
            while listening_to_llm or tts_futures:
                pending = list(tts_futures.keys())
                if listening_to_llm:
                    # Reuse queue_task if it wasn't consumed
                    if queue_task is None or queue_task.done():
                        queue_task = asyncio.create_task(queue.get())
                    pending.append(queue_task)

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
                            # Terminate: a provider that dies mid-generation
                            # leaves nothing to stream, and the frontend needs
                            # a terminal event to hand the mic back. Any
                            # synthesis still in flight is settled by the
                            # finally, which is the only place all three exits
                            # converge.
                            terminal_emitted = True
                            yield sse_format("error", {"detail": LLM_FAILED})
                            yield sse_format("done", {})
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

            terminal_emitted = True
            yield sse_format("done", turn_done_payload(committed))

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
                terminal_emitted = True
                yield sse_format("error", {"detail": UNEXPECTED_ERROR})
                yield sse_format("done", {})
        finally:
            # Every exit converges here: the happy path (with an empty dict),
            # the mid-stream LLM failure, the client disconnect, the broad
            # handler, and a cancelled task. Whatever synthesis is still in
            # flight is settled now, because after this the dict goes out of
            # scope with nothing awaiting it and no result retrieved -- the
            # tasks finish on their own, write audio files under the
            # conversation's directory that nothing references, and hold an
            # outbound provider call for its whole timeout. Under a provider
            # outage, which is exactly when this path fires, that multiplies.
            llm_stop.set()
            outstanding = list(tts_futures)
            for task in outstanding:
                task.cancel()
            if outstanding:
                try:
                    # Cancelling `synthesize_sentence` aborts it at its await,
                    # so this really does stop the outbound call rather than
                    # merely forgetting about it. `return_exceptions=True`
                    # because every one of these is now expected to raise.
                    await asyncio.gather(*outstanding, return_exceptions=True)
                except asyncio.CancelledError:
                    # This turn is itself being cancelled, so the loop will not
                    # run the reap. The cancels above are already delivered; the
                    # task's death is the only thing that could report otherwise.
                    logger.debug(
                        "Turn %s torn down mid-teardown with %d synthesis "
                        "task(s) cancelled but not reaped",
                        conversation_id, len(outstanding),
                    )
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
