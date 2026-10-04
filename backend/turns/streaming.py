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
                      {"n": int, "has_context": bool,  terminal (answer the
                       "incomplete": true}              candidate did not get
                                                       whole, still stored)
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
transcription), so the client asks the Context panel about nothing.

A provider that dies mid-generation is neither of those. The candidate heard
the sentences that were synthesised and read the tokens that were streamed, so
the exchange is kept — and ``done`` carries ``incomplete`` so every reader of
it downstream, the transcript, the report and the page itself, can say that the
answer stops half way through instead of presenting a fragment as a reply.

``incomplete`` means the answer the candidate heard is not the whole answer, and
there are two ways that happens. The model stopped generating; or the model
generated all of it and the synthesiser delivered only part. The second is not a
rarer edge: ``TTS_CHUNK_FAILED`` is recoverable and the stream continues, so one
dead sentence among five is an ordinary provider hiccup, and filing that turn
unmarked is the same defect as a truncated one — a transcript and a report that
cite every sentence for a candidate who heard a fraction of them. So the mark is
set whenever synthesis was asked for more than it delivered AND something was
heard; the two ways it can be true are named in ``errors.py``, next to the
message the candidate reads when it happens.

Two cases store nothing, and both are about audio rather than text. A provider
that died before emitting a single token has no answer to keep. And a turn
whose TTS delivered nothing at all — synthesis was called for and every
sentence failed — is a dead turn: the tokens are on screen but nothing was
spoken, and filing it would hand the recruiter a transcript and a report
claiming an answer that has no voice. It ends on fatal ``TTS_FAILED`` with an
empty ``done``, exactly as the blocking route's 503 stores nothing. That is the
one case the partial rule above deliberately excludes: nothing heard is not a
short answer, it is no answer, and marking it would spend the flag on a silence.

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
from backend.sse import sse_format, with_keepalive
from backend.turns.answer_source import LLM, resolve_answer_source
from backend.turns.errors import (
    FAREWELL_TTS_FAILED,
    LLM_FAILED,
    TTS_CHUNK_FAILED,
    TTS_FAILED,
    UNEXPECTED_ERROR,
)

logger = logging.getLogger(__name__)


def _sentence_files(directory: Path, sentence_id: int) -> set[str]:
    """Names of the audio files synthesis wrote for one sentence id.

    Scoped by the id prefix rather than by "everything in the directory",
    because a conversation's directory accumulates one file per sentence for the
    whole interview and every turn before this one is still referenced by its
    own transcript. Each synthesis names its own file
    (``sentence_{id}_{uuid}.mp3``), so the id prefix is the finest honest
    partition available without reaching into the TTS service.
    """
    try:
        return {
            entry.name
            for entry in directory.glob(f"sentence_{sentence_id}_*.mp3")
            if entry.is_file()
        }
    except OSError:
        return set()


def _sentence_files_any(directory: Path) -> set[str]:
    """Every per-sentence file in the directory, whatever its id.

    The turn-start snapshot. It is read once and then only subtracted, so the
    sweep cannot delete a file that was here before this turn began speaking.
    """
    try:
        return {
            entry.name
            for entry in directory.glob("sentence_*_*.mp3")
            if entry.is_file()
        }
    except OSError:
        return set()


async def _store_truncated_turn(
    conversation_id: str,
    user_text: str,
    partial_response: str,
    context_chunks: list,
) -> dict | None:
    """Keep the answer the candidate heard when the model stops mid-sentence.

    Returns the committed turn, or ``None`` when nothing reached disk. A write
    that raises is reported and swallowed exactly as the farewell's is: letting
    it reach the outer handler would replace the provider's own error with a
    generic one and lose the message that explains the turn.
    """
    try:
        new_turn, new_message = build_turn(
            conversation_id,
            user_text,
            partial_response,
            context_chunks,
            # The directory, not one file: a streamed answer is many
            # per-sentence chunks and this one is a prefix of them.
            f"/audio/{conversation_id}/",
            incomplete=True,
        )
        touch_activity(conversation_id)
        return await persist_turn(conversation_id, new_turn, new_message)
    except Exception as e:
        logger.error(
            "Truncated turn write failed for %s: %s", conversation_id, e, exc_info=True
        )
        return None


def _sweep_orphan_audio(
    directory: Path,
    dispatched: set[int],
    announced: set[str],
    pre_existing: set[str],
) -> int:
    """Delete audio this turn synthesised and never announced. Returns the count.

    The TTS writes a file and THEN streams; cancelling the task aborts it at an
    await, which cannot take the file back. So a turn that ends early — a
    provider that died, a client that left — leaves behind whatever the
    cancelled syntheses had already written, and nothing ever points at those
    files. They sat in ``audio/`` until the hourly sweep noticed them.

    Only this turn's unannounced output is eligible, and a file is removed only
    when all three hold:

    * it carries the id of a sentence THIS turn asked for,
    * it was not already on disk when the turn started speaking, and
    * no ``audio_url`` was emitted for it.

    The middle clause is what protects a concurrent turn: two streaming turns on
    one conversation pick the same sentence ids, and without it the loser would
    delete audio the winner is still playing. It assumes turns on a conversation
    do not overlap their speech, which the page guarantees by holding the mic
    for the length of a turn; the prefix and the announced set cover the window
    even if that ever changes.

    Never raises. This runs in the generator's ``finally``, where an exception
    would replace whatever the turn was actually ending with.
    """
    orphans = 0
    for sentence_id in sorted(dispatched):
        for name in _sentence_files(directory, sentence_id) - announced - pre_existing:
            try:
                (directory / name).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not delete orphaned audio %s: %s", name, e)
                continue
            orphans += 1
    if orphans:
        logger.info(
            "Deleted %d synthesised-but-never-announced audio file(s) from %s",
            orphans,
            directory,
        )
    return orphans


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
        # ── Audio bookkeeping for the orphan sweep ──
        # Four sets. The first three feed the sweep; the fourth is what decides
        # whether the turn is filed as whole.
        #
        # ``dispatched_sentences``
        #     every sentence id this turn asked the TTS for. A synthesis that
        #     was cancelled AFTER the provider wrote its file leaves a file
        #     that no event will ever name -- that is the orphan.
        # ``announced_audio``
        #     the files whose ``audio_url`` actually went out. These are the
        #     ones the candidate may still be listening to, so the sweep must
        #     never touch them.
        # ``pre_existing_audio``
        #     what was already in the conversation's directory when the turn
        #     started speaking. Files from an earlier turn in the same interview
        #     are still referenced by that turn's transcript.
        # ``failed_sentences``
        #     the ids synthesis was asked for and did not deliver. This is the
        #     honesty mark's own bookkeeping and it is NOT the inverse of the
        #     other two: the loop below only ends once every dispatched task has
        #     settled, so at that point the ids are complete, but a turn that was
        #     cancelled mid-flight leaves an orphan rather than a failure and is
        #     not in here.
        dispatched_sentences: set[int] = set()
        announced_audio: set[str] = set()
        pre_existing_audio: set[str] = set()
        failed_sentences: set[int] = set()

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

                The chunks it tracks are for the PANEL, and the `done` payload
                says so: `grounded=False`, because the answer was a fixed string
                from the cache and the RAG never saw it. A recruiter reading
                "the passages the answer was built from" under a canned reply
                would be reading a provenance claim about a pipeline that did
                not run.
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
                # grounded=False, and this is the whole point of the flag: the
                # chunks above were retrieved for the PANEL. The answer is a
                # fixed string from response_cache.py, chosen by a match on the
                # question. No LLM ran and no context string was ever built, so
                # calling these passages the answer's source would be a claim
                # about a pipeline that did not execute.
                yield sse_format(
                    "done", turn_done_payload(committed, grounded=False)
                )

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
                context,
                conversation_context=conversation_context,
                # Outside the RAG path on purpose: retrieval can lose an employer
                # (measured, `tests/work_history_cases.py`), this cannot. Read
                # through the container so the test suite can rebind it.
                work_history=container.candidate_profile().get_work_history_block(),
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
            # Snapshot before the first synthesis, so the sweep in the finally
            # can tell this turn's files from the interview's earlier ones.
            audio_dir = config.AUDIO_DIR / conversation_id
            pre_existing_audio = _sentence_files_any(audio_dir)

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
                            # One rule, both ways round: the exchange is kept
                            # when the candidate HEARD something, and dropped
                            # when nothing was ever asked of the synthesiser.
                            #
                            # The second half is not a rare branch. `SentenceBuffer`
                            # only emits on `. ! ? \n`, so a provider that dies
                            # before its first terminator leaves
                            # `dispatched_sentences` empty -- synthesis is never
                            # called, nothing is ever announced, and the tokens
                            # that did stream were read on screen and never
                            # spoken. Storing that writes a turn into the
                            # transcript for an exchange that did not happen,
                            # and `incomplete: true` would label a silence as a
                            # half-answer rather than as the absence of one.
                            committed = None
                            if full_response.strip() and announced_audio:
                                committed = await _store_truncated_turn(
                                    conversation_id,
                                    user_text,
                                    full_response,
                                    context_chunks,
                                )
                            # Terminate: the frontend needs a terminal event to
                            # hand the mic back, and it needs this one to name
                            # the turn so the counter advances. Any synthesis
                            # still in flight is settled by the finally, which is
                            # the only place all three exits converge.
                            terminal_emitted = True
                            yield sse_format("error", {"detail": LLM_FAILED})
                            yield sse_format(
                                "done",
                                turn_done_payload(committed, incomplete=True),
                            )
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
                                        output_dir=audio_dir,
                                    )
                                )
                                tts_futures[task] = sentence_id
                                dispatched_sentences.add(sentence_id)
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
                                # The gap is in the answer the candidate will
                                # hear, whether or not the provider was ever
                                # reached: this id has no audio and will not get
                                # any, so the turn is a partial one.
                                failed_sentences.add(sentence_id)
                                sentence_id += 1
                    else:
                        # A TTS task completed — yield the audio chunk immediately
                        try:
                            sid, audio_path = done.result()
                            announced_audio.add(audio_path.name)
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
                            # Recorded by id, not by comparing the two sets above:
                            # the mark has to mean "this sentence was asked for
                            # and never spoken", and a file-naming detail must
                            # not be what makes that true or false.
                            failed_sentences.add(sid)
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

            # A turn whose TTS produced NOTHING is a failed turn, not an answer.
            #
            # Every sentence failing used to fall through to the store: the
            # `error` events above carry a chunk id, which the frontend reads as
            # a recoverable skip, so the page skipped three chunks, said a
            # fragment was omitted, and then received `done{n: 0,
            # has_context: true}` -- a complete answer on screen, total silence,
            # and a green counter. The blocked route never had this shape: it
            # answers 503 and keeps nothing.
            #
            # The condition is "asked and delivered nothing", not "no audio":
            # `dispatched_sentences` counts what synthesis was actually called
            # for. An answer with nothing speakable in it dispatches nothing, so
            # there is no provider failure to report and the text is a real
            # exchange worth filing.
            if dispatched_sentences and not announced_audio:
                logger.error(
                    "TTS produced no audio for %s: all %d sentence(s) failed, "
                    "so the turn is not stored",
                    conversation_id,
                    len(dispatched_sentences),
                )
                terminal_emitted = True
                yield sse_format("error", {"detail": TTS_FAILED})
                yield sse_format("done", {})
                return

            # The other end of the same line: synthesis was asked for more than
            # it delivered, and at least one sentence sounded, so the turn is
            # filed -- marked, because the text on disk is every sentence the
            # model produced while the candidate heard a fraction of them. Filed
            # unmarked it was the same defect as the all-failed case above, one
            # notch less loud: the transcript and the report would cite five
            # sentences for an answer three of which were never spoken.
            #
            # `announced_audio` is what keeps the nothing-spoken case out, and it
            # has already returned by the time this runs; the clause is still
            # stated rather than relied upon, because the rule is read here and a
            # rule that depends on the line above it having returned is a rule
            # with a hole in it again.
            partially_spoken = bool(announced_audio) and bool(failed_sentences)
            if partially_spoken:
                logger.warning(
                    "TTS delivered only part of %s: %d of %d sentence(s) failed, "
                    "so the turn is stored as incomplete",
                    conversation_id,
                    len(failed_sentences),
                    len(dispatched_sentences),
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
                incomplete=partially_spoken,
            )
            touch_activity(conversation_id)

            # Write-through: persist turn + message + activity atomically, then
            # let the committed n decide what memory keeps.
            committed = await persist_turn(conversation_id, new_turn, new_message)

            terminal_emitted = True
            yield sse_format(
                "done", turn_done_payload(committed, incomplete=partially_spoken)
            )

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
            # After the cancels above, so it sweeps what they actually wrote.
            # A synthesis cancelled at its await has already written its file
            # and cannot be un-written; this is the only place every exit
            # converges, so it is the only place that can notice.
            _sweep_orphan_audio(
                config.AUDIO_DIR / conversation_id,
                dispatched_sentences,
                announced_audio,
                pre_existing_audio,
            )
            if temp_audio.exists():
                temp_audio.unlink(missing_ok=True)

    # Wrapped, not merely passed through. The generator below is silent for the
    # whole of STT -- its first `yield` is at :130, after the transcription at
    # :125 -- and a silent stream is a stream nginx's proxy_read_timeout closes
    # and the browser reports as a finished interview. The wrapper emits a
    # comment frame through any stall, so the silence is never actually silent
    # on the wire. See backend/sse.py and tests/test_sse_keepalive.py, which
    # also holds this in step with the nginx side of the same fix.
    return with_keepalive(event_generator())
