"""Behavioural regression test for how the SSE stream logs an unfinished turn.

The generator's ``finally`` must distinguish two situations that both arrive
with ``terminal_emitted == False``:

* ``GeneratorExit`` -- the client closed the tab, navigated away or dropped the
  connection. It raises at the current ``yield`` and, being a ``BaseException``,
  is never caught by the generator's ``except Exception``. The browser settles
  on EOF, so this is normal operation.
* Anything else -- a path returned or was cancelled without a terminal event,
  which is a real defect.

Logging both at ERROR is not cosmetic: it trains the reader to ignore the one
line that reports a genuinely missing terminal event. This test therefore
drives the real generator and reads real log records -- asserting on source
text would only prove the words are present, which is the weakness a previous
version of this test had.
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.turns import streaming


async def _fake_synth(text, output_path=None):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


async def _fake_sentence(text, sentence_id=0, output_path=None, **kwargs):
    return await _fake_synth(text, output_path or "sentence.mp3")


def _container():
    stt = MagicMock()
    stt.transcribe.return_value = "hola"

    async def token_stream(*args, **kwargs):
        for token in ["Hola ", "que tal"]:
            yield token

    llm = MagicMock()
    llm.generate_stream_with_context = token_stream

    rag = MagicMock()
    rag.get_chunks_with_scores.return_value = []

    tts = MagicMock()
    tts.synthesize = _fake_synth
    tts.synthesize_sentence = _fake_sentence

    container = MagicMock()
    container.stt_service.return_value = stt
    container.llm_service.return_value = llm
    container.rag_pipeline.return_value = rag
    container.persistence.return_value = MagicMock()
    container.tts_service.return_value = tts
    return container


def _drive_a_disconnect(tmp_path, caplog):
    """Consume one event, then abandon the generator as a closing tab would."""

    async def run():
        audio = tmp_path / "input.wav"
        audio.write_bytes(b"RIFF")
        with patch.object(streaming, "container", _container()):
            stream = streaming.build_stream("conv-disconnect", audio)
            await stream.__anext__()
            # aclose() throws GeneratorExit into the generator at its current
            # yield: precisely the browser-abandons-the-response case.
            await stream.aclose()

    with caplog.at_level(logging.DEBUG, logger="backend.turns.streaming"):
        asyncio.run(run())


class TestDisconnectLogging:
    def test_client_disconnect_is_not_logged_as_an_error(self, tmp_path, caplog):
        """The regression: closing the tab used to write an ERROR every time."""
        _drive_a_disconnect(tmp_path, caplog)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not errors, (
            "a normal client disconnect must not be logged as a stream defect: "
            f"{[r.getMessage() for r in errors]}"
        )

    def test_client_disconnect_is_reported_at_info(self, tmp_path, caplog):
        """It is not merely silenced -- the abort stays visible for support."""
        _drive_a_disconnect(tmp_path, caplog)

        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "closed by the client" in r.getMessage()
        ]
        assert infos, (
            "an aborted stream should still be traceable; expected an INFO "
            "recording naming the client close"
        )
        assert "conv-disconnect" in infos[0].getMessage()

    def test_temp_audio_is_cleaned_up_on_disconnect(self, tmp_path):
        """Cleanup runs in the same finally, so a tab close leaks no file."""
        audio = tmp_path / "input.wav"
        audio.write_bytes(b"RIFF")

        async def run():
            with patch.object(streaming, "container", _container()):
                stream = streaming.build_stream("conv-cleanup", audio)
                await stream.__anext__()
                await stream.aclose()

        asyncio.run(run())
        assert not audio.exists(), "an abandoned stream must not leak its temp file"


class TestFinallyBlockShape:
    def test_finally_never_yields(self):
        """A yield inside `finally` raises RuntimeError during GeneratorExit.

        Structural on purpose: this cannot be provoked by consuming events, it
        is a property of the block itself.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(streaming))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and node.finalbody:
                for statement in node.finalbody:
                    for inner in ast.walk(statement):
                        if isinstance(inner, (ast.Yield, ast.YieldFrom)):
                            offenders.append(inner.lineno)
        assert not offenders, f"finally must not yield; offenders at {offenders}"
