"""A turn that ends badly must not leave work running behind it.

THE DEFECT THIS GUARDS
----------------------
The streaming turn launches one asyncio task per synthesised sentence and keeps
them in ``tts_futures``. That dict is local to the generator, and only the happy
path ever emptied it: the ``while listening_to_llm or tts_futures:`` loop
collects each task's result as it completes, so if the turn leaves early -- an
LLM that dies mid-answer, or a client that closes the tab -- the tasks are still
in flight when the generator returns. They then go out of scope with nothing
awaiting them and no result retrieved.

Measured before the fix: 3 sentences launched, 3 still alive at stream end, 0
cancelled, and all three went on to write ``sentence_0/1/2.mp3`` into the
conversation's audio directory that nothing ever references. Under a provider
outage -- which is exactly when this path fires -- every turn multiplies
outbound TTS calls that nobody is listening for.

The executor thread running the LLM had the same shape: ``run_in_executor``
returns a future the generator discarded, so the thread kept pulling tokens
from the provider into a queue with no reader.

WHAT IS ASSERTED HERE
---------------------
* Every TTS task this turn launched is finished (cancelled or completed) by the
  time the stream ends -- on the mid-stream-failure path AND on the
  client-disconnect path.
* The LLM thread stops pulling tokens once the stream is over.
* The terminal-event flag is set BEFORE the terminal yield, so a cancellation
  delivered at the terminal event (or at the error event immediately before it)
  is recognised as a client going away rather than reported as a missing
  terminal event -- which is the whole point of the two-branch split in the
  generator's ``finally``.
"""

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import stub_rag_context_shapes
from backend.conversation import conversations
from backend.turns import streaming


def _register(cid):
    conversations[cid] = {
        "id": cid, "messages": [], "turns": [], "summary": "",
        "created_at": "", "last_activity_at": "",
    }
    return cid


class _TTSProbe:
    """Records the task each synthesised sentence ran on, and how it ended."""

    def __init__(self, hold=30.0):
        self.hold = hold
        self.tasks: list = []
        self.synthesised: list = []

    async def sentence(self, text, sentence_id=0, output_dir=None, **kwargs):
        self.tasks.append(asyncio.current_task())
        await asyncio.sleep(self.hold)  # still streaming from the "provider"
        self.synthesised.append(sentence_id)
        return sentence_id, Path(f"sentence_{sentence_id}.mp3")

    async def whole(self, text, output_path=None, **kwargs):
        self.synthesised.append(-1)
        return output_path

    @property
    def unfinished(self) -> list:
        return [t for t in self.tasks if not t.done()]


def _container(question, *, tokens, tts, provider_dies=False,
               transcript="Habla de tu proyecto"):
    stt = MagicMock()
    stt.transcribe.return_value = question

    def stream_with_context(*args, **kwargs):
        def generate():
            for token in tokens:
                yield token
            if provider_dies:
                # The provider dies AFTER the sentences above were dispatched
                # for synthesis: that is the window where work is in flight
                # and the turn has to terminate early.
                raise RuntimeError("provider 503")
        return (generate(), [])

    llm = MagicMock()
    llm.generate_stream_with_context = stream_with_context

    rag = MagicMock()
    rag.get_chunks_with_scores.return_value = []
    stub_rag_context_shapes(rag)

    tts_service = MagicMock()
    tts_service.synthesize_sentence = tts.sentence
    tts_service.synthesize = tts.whole

    container = MagicMock()
    container.stt_service.return_value = stt
    container.llm_service.return_value = llm
    container.rag_pipeline.return_value = rag
    container.persistence.return_value = MagicMock()
    container.tts_service.return_value = tts_service
    return container


def _temp_audio(tmp_path) -> Path:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"RIFF")
    return audio


def _terminal_state(tasks) -> str:
    return [
        "cancelled" if t.cancelled() else ("done" if t.done() else "alive")
        for t in tasks
    ]


class TestSynthesisIsNotOrphaned:
    def test_a_mid_stream_llm_failure_leaves_no_synthesis_running(self, tmp_path):
        """The finding's own reproduction, as a regression net.

        Three sentences are launched, the provider dies on the fourth token,
        and the turn terminates. Every sentence task must be finished by then.
        """
        tts = _TTSProbe()
        cid = _register("stream-llm-dies")

        async def run():
            with patch.object(
                streaming, "container",
                _container("hablame de un proyecto raro", tokens=["Uno. ", "Dos. ", "Tres. "],
                           tts=tts, provider_dies=True),
            ):
                stream = streaming.build_stream(cid, _temp_audio(tmp_path))
                await _drain(stream)
            return _terminal_state(tts.tasks)

        try:
            states = asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        assert len(tts.tasks) == 3, f"expected 3 sentences in flight, got {len(tts.tasks)}"
        assert "alive" not in states, (
            f"synthesis tasks still running when the stream ended: {states}. "
            "They go out of scope with nothing awaiting them and write audio "
            "files nothing references."
        )

    def test_a_client_disconnect_leaves_no_synthesis_running(self, tmp_path):
        """The other early exit: the tab closes mid-answer."""
        tts = _TTSProbe()
        cid = _register("stream-client-leaves")

        async def run():
            with patch.object(
                streaming, "container",
                _container("hablame de un proyecto raro", tokens=["Uno. ", "Dos. ", "Tres. "],
                           tts=tts),
            ):
                stream = streaming.build_stream(cid, _temp_audio(tmp_path))
                seen = 0
                async for _event in stream:
                    seen += 1
                    if seen >= 3:
                        break
                await stream.aclose()
            return _terminal_state(tts.tasks)

        try:
            states = asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        assert tts.tasks, "no synthesis was in flight, so nothing was proven"
        assert "alive" not in states, (
            f"synthesis tasks still running after the client left: {states}"
        )

    def test_a_healthy_turn_is_unaffected(self, tmp_path):
        """Draining must not swallow the results a healthy turn needs."""
        tts = _TTSProbe(hold=0.0)
        cid = _register("stream-healthy")

        async def run():
            with patch.object(
                streaming, "container",
                _container("hablame de un proyecto raro", tokens=["Uno. ", "Dos. "],
                           tts=tts),
            ):
                return [e async for e in streaming.build_stream(cid, _temp_audio(tmp_path))]

        try:
            events = asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        joined = "\n".join(events)
        assert '"audio_url"' in joined, "the synthesised audio was never emitted"
        assert _terminal_state(tts.tasks) == ["done", "done"], (
            "a healthy turn must complete its synthesis, not cancel it: "
            f"{_terminal_state(tts.tasks)}"
        )
        assert sorted(tts.synthesised) == [0, 1], (
            f"the cancelled work wrote audio for nobody: {sorted(tts.synthesised)}"
        )


async def _drain(stream):
    async for _event in stream:
        pass


class TestTheLLMThreadStopsWhenTheStreamDoes:
    def test_tokens_stop_being_pulled_once_the_client_leaves(self, tmp_path):
        """``run_in_executor`` returned a future nobody held.

        The thread kept walking the provider's generator and pushing tokens
        onto a queue with no reader -- outbound traffic for a turn whose client
        is already gone, and a thread holding a pool slot until the provider's
        own timeout expires.
        """
        limit = 60
        pulled = {"n": 0}

        def stream_with_context(*args, **kwargs):
            def generate():
                for _ in range(limit):
                    pulled["n"] += 1
                    time.sleep(0.01)
                    yield "palabra "
            return (generate(), [])

        tts = _TTSProbe()
        cid = _register("stream-llm-thread")

        async def run():
            container = _container("hablame de un proyecto raro", tokens=[], tts=tts)
            container.llm_service.return_value.generate_stream_with_context = (
                stream_with_context
            )
            with patch.object(streaming, "container", container):
                stream = streaming.build_stream(cid, _temp_audio(tmp_path))
                seen = 0
                async for _event in stream:
                    seen += 1
                    if seen >= 2:
                        break
                await stream.aclose()
                at_disconnect = pulled["n"]
                await asyncio.sleep(0.3)
                return at_disconnect, pulled["n"]

        try:
            at_disconnect, after = asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        assert at_disconnect < limit, (
            f"the provider was drained before the client left ({at_disconnect}"
            f"/{limit}); the test proves nothing"
        )
        assert after - at_disconnect <= 2, (
            f"the LLM thread kept pulling from the provider after the stream "
            f"was over: {at_disconnect} -> {after} of {limit} tokens"
        )


class TestTheTerminalFlagIsSetBeforeTheYield:
    """The flag's own contract, on every path that sets it.

    ``terminal_emitted`` exists so the ``finally`` can tell "the client went
    away" from "a path returned without a terminal event". It was assigned
    AFTER the yields that emit the terminal event, so a cancellation delivered
    at that yield -- an ordinary client disconnect, not a defect -- was
    reported as a stream that ended without one. That is the exact line the
    two-branch split exists to avoid writing, and it fires on every disconnect
    that lands on a terminal event.
    """

    @staticmethod
    def _missing_terminal(caplog):
        """The specific line the flag governs.

        Not "any ERROR": the broad handler logs the failure that got it there,
        and that log is correct. What must not happen is the stream also being
        reported as having ended without a terminal event.
        """
        return [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
            and "ended without a terminal event" in r.getMessage()
        ]

    def _assert_clean(self, caplog, what):
        offenders = self._missing_terminal(caplog)
        assert not offenders, (
            f"{what} was logged as a stream defect: "
            f"{[r.getMessage() for r in offenders]}"
        )

    @pytest.mark.parametrize(
        "question,tokens,terminal",
        [
            pytest.param("", [], "done", id="silent-recording"),
            pytest.param("que opinas de la ia", [], "done", id="faq-hit"),
            pytest.param("hablame de algo raro", ["Hola ", "que tal"], "done",
                         id="llm-answer"),
        ],
    )
    def test_cancel_at_the_terminal_event_is_not_a_defect(
        self, tmp_path, caplog, question, tokens, terminal
    ):
        tts = _TTSProbe(hold=0.0)
        cid = _register(f"stream-terminal-{terminal}-{abs(hash(question)) % 997}")

        async def run():
            with patch.object(
                streaming, "container",
                _container(question, tokens=tokens, tts=tts),
            ):
                stream = streaming.build_stream(cid, _temp_audio(tmp_path))
                async for event in stream:
                    if f'"event": "{terminal}"' in event or f'"{terminal}"' in event:
                        # A cancellation delivered exactly at the terminal
                        # yield: what a client that disappears while reading
                        # the last event looks like from here.
                        with pytest.raises(asyncio.CancelledError):
                            await stream.athrow(asyncio.CancelledError())
                        break

        try:
            with caplog.at_level(logging.DEBUG, logger="backend.turns.streaming"):
                asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        self._assert_clean(
            caplog, f"a cancellation at the {terminal} event of a {cid} turn"
        )

    def test_cancel_at_the_error_before_done_is_not_a_defect(self, tmp_path, caplog):
        """The specific shape the finding reports.

        The broad handler emits ``error`` and then ``done``. A cancellation
        delivered at the ``error`` -- before the ``done`` exists -- used to
        leave the flag false, so the ``finally`` reported a missing terminal
        event. The client had simply gone away.
        """
        tts = _TTSProbe(hold=0.0)
        cid = _register("stream-cancel-at-error")

        def exploding_container():
            container = _container("hablame de algo raro", tokens=["Hola "], tts=tts)
            broken = MagicMock()

            def explode(*args, **kwargs):
                raise RuntimeError("retrieval exploded")

            broken.retrieve_with_context.side_effect = explode
            container.rag_pipeline.return_value = broken
            return container

        async def run():
            with patch.object(streaming, "container", exploding_container()):
                stream = streaming.build_stream(cid, _temp_audio(tmp_path))
                async for event in stream:
                    if '"error"' in event:
                        with pytest.raises(asyncio.CancelledError):
                            await stream.athrow(asyncio.CancelledError())
                        break

        try:
            with caplog.at_level(logging.DEBUG, logger="backend.turns.streaming"):
                asyncio.run(run())
        finally:
            conversations.pop(cid, None)

        self._assert_clean(
            caplog, "a cancellation at the error event preceding done"
        )
