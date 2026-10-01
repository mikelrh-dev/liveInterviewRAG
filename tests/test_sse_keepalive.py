"""A stream that emits no bytes is a stream nginx will cut, and the browser
will report as a finished interview.

THE DEFECT
----------
``/message/stream``'s first byte is not the request. ``build_stream`` opens with
``await asyncio.to_thread(container.stt_service().transcribe, temp_audio)``
(``backend/turns/streaming.py:125``) and only THEN yields its first event, at
``:130``. The quiet window is therefore the whole of STT: upload plus Whisper on
CPU. With the shipped defaults -- ``WHISPER_MODEL=small`` on
``WHISPER_DEVICE=cpu`` (``backend/config.py:60-62``) and a 30 s ceiling
(``:120``) -- a slow transcription of a full-length recording can sit silent for
more than nginx's 60 s ``proxy_read_timeout``. nginx then closes the connection,
the browser's ``reader.read()`` rejects, and the client renders its
"entrevista finalizada" state with the turn half-answered.

TWO HALVES, OR COSMETIC
-----------------------
Raising ``proxy_read_timeout`` alone is necessary and not sufficient: it moves
the cliff from 60 s to whatever the new value is, and a stall past the new value
loses the turn the same way. It is also not *reliable* on its own, because the
timeout can be reached by any single slow await, of which there are several.

A keepalive alone is also not sufficient: it keeps the connection warm but does
not widen the window, so a stall longer than the timeout still cuts the stream.

So both are required, and the coupling between them is worth a test rather than a
comment. ``TestTheTwoHalvesCannotDrift`` asserts the nginx ceiling stays well
above the keepalive interval, because a keepalive at 15 s under a 60 s timeout
is a different (and strictly better) system than a keepalive at 15 s under a 10 s
one -- and the config file is not where anyone goes looking for a Python
constant.

THE FRAME
---------
A comment-only SSE frame: ``": keepalive\\n\\n"``. Per the HTML spec a line
beginning with ``:`` is a comment, ignored by ``EventSource`` and by every
hand-rolled reader. This repository's own reader skips it at
    ``frontend/app.js:2718`` (``if (!trimmed || !trimmed.startsWith("data: "))
continue;``), which is asserted here by re-implementing that filter rather than
by importing it -- the point is that the frame is dropped by the *filter*, not
by a lucky parser.

It is deliberately NOT a ``data:`` line carrying a new event name. That would
require a frontend branch, would enlarge the event contract, and would put a new
unknown event on the wire during exactly the stall where the client is least
able to cope with one.
"""

import asyncio
import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend import sse
from backend.sse import sse_keepalive, with_keepalive
from tests.conftest import stub_rag_context_shapes

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "nginx" / "interview.conf"

#: nginx's own default when ``proxy_read_timeout`` is absent from a location.
#: Worth naming so the failure message can say what it is being compared with.
NGINX_DEFAULT_READ_TIMEOUT = 60

#: The smallest acceptable ratio between the nginx ceiling and the keepalive
#: interval. 1 would be "not below", which is already a broken system: a
#: keepalive that races its own timeout is a coin flip. 3 means a late frame
#: still leaves two full intervals of margin before the cliff.
MIN_TIMEOUT_TO_KEEPALIVE_RATIO = 3


# ─── The frame ──────────────────────────────────────────────────────────────


def frontend_reads(raw: str) -> list:
    """Exactly the line filter in ``frontend/app.js:2716-2725``.

    Reproduced rather than imported: the assertion is that the frame is dropped
    by the client's own filter, so the filter has to be the client's, written
    out. Any future change to ``app.js`` is a reason to revisit this copy, and
    the assertion that would catch it is the one at the bottom of this class.
    """
    events = []
    for line in raw.split("\n"):
        trimmed = line.strip()
        if not trimmed or not trimmed.startswith("data: "):
            continue
        try:
            events.append(__import__("json").loads(trimmed[6:]))
        except ValueError:
            continue
    return events


class TestTheFrameIsInvisibleToEveryClient:
    def test_it_is_a_comment_line_and_carries_no_data(self):
        frame = sse_keepalive()
        assert frame.startswith(":"), f"not an SSE comment: {frame!r}"
        assert "\n\n" in frame, "a frame must terminate the event or the reader waits"
        body = frame.strip()
        assert not body.startswith("data:"), (
            "a data: line is an event every client must understand; the keepalive "
            "must be a comment"
        )
        assert "event:" not in body, "naming an event would enlarge the contract"

    def test_the_frontend_reader_drops_it(self):
        assert frontend_reads(sse_keepalive()) == []

    def test_the_contract_parser_drops_it(self):
        """``tests/test_sse_contract.py`` must not see a new event name.

        That parser is the regression net for backend/frontend event drift. A
        keepalive that reached it as an event would force a frontend branch for
        a frame whose entire purpose is that no frontend branch is needed.
        """
        from tests.test_sse_contract import parse_events

        assert parse_events(sse_keepalive() + 'data: {"event": "done", "data": {}}\n\n') == [
            {"event": "done", "data": {}}
        ]


# ─── The wrapper ────────────────────────────────────────────────────────────


async def _drain(stream) -> list:
    return [chunk async for chunk in stream]


class TestTheStreamIsNotSilentDuringAStall:
    async def test_a_stall_before_the_first_event_emits_a_keepalive(self):
        """The exact window: request sent, first byte still 0.4 s away."""

        async def stalled():
            await asyncio.sleep(0.4)
            yield 'data: {"event": "transcription", "data": {}}\n\n'

        out = await _drain(with_keepalive(stalled(), interval=0.05))

        assert out[0] == sse_keepalive(), (
            f"nothing was emitted during the stall; the stream went silent for "
            f"0.4 s and the first chunk was {out[0]!r}"
        )
        assert out.count(sse_keepalive()) >= 1
        assert out[-1].startswith("data: "), "the real event must still arrive"

    async def test_a_stall_between_two_events_emits_a_keepalive(self):
        async def middle_stall():
            yield 'data: {"event": "transcription", "data": {}}\n\n'
            await asyncio.sleep(0.3)
            yield 'data: {"event": "done", "data": {}}\n\n'

        out = await _drain(with_keepalive(middle_stall(), interval=0.05))

        assert out[0].startswith("data: "), "the first event must not be delayed"
        assert sse_keepalive() in out, "the mid-stream stall produced no keepalive"
        assert out[-1].startswith("data: "), "the terminal event must still arrive"
        assert len(frontend_reads("".join(out))) == 2, (
            "a client must see exactly the two real events, in order, with the "
            f"keepalive invisible; it saw {frontend_reads(''.join(out))}"
        )

    async def test_a_fast_stream_gets_no_keepalive_at_all(self):
        async def quick():
            yield 'data: {"event": "a", "data": {}}\n\n'
            yield 'data: {"event": "b", "data": {}}\n\n'

        out = await _drain(with_keepalive(quick(), interval=5.0))

        assert sse_keepalive() not in out, (
            "a keepalive on a fast stream is noise: it wakes the proxy, the "
            f"client and the logs for nothing. Got {out}"
        )

    async def test_nothing_is_emitted_after_the_stream_ends(self):
        async def ending():
            yield 'data: {"event": "done", "data": {}}\n\n'

        out = await _drain(with_keepalive(ending(), interval=0.01))

        assert sse_keepalive() not in out, (
            "a keepalive after the terminal event is a frame the client reads "
            f"after the interview is over: {out}"
        )

    async def test_event_order_is_preserved_exactly(self):
        async def many():
            for i in range(6):
                await asyncio.sleep(0.04)
                yield f'data: {{"event": "token", "data": {{"i": {i}}}}}\n\n'

        out = await _drain(with_keepalive(many(), interval=0.01))
        events = frontend_reads("".join(out))

        assert [e["data"]["i"] for e in events] == list(range(6)), (
            f"reordering or dropping under keepalive pressure: {events}"
        )


class TestTheWrapperIsTransparent:
    async def test_an_exception_from_the_stream_propagates(self):
        async def failing():
            yield 'data: {"event": "a", "data": {}}\n\n'
            raise RuntimeError("upstream died")

        with pytest.raises(RuntimeError, match="upstream died"):
            await _drain(with_keepalive(failing(), interval=0.01))

    async def test_an_exception_after_a_long_still_propagates(self):
        async def failing_late():
            await asyncio.sleep(0.3)
            raise RuntimeError("died while silent")
            yield  # pragma: no cover -- makes this an async generator, not a coroutine

        with pytest.raises(RuntimeError, match="died while silent"):
            await _drain(with_keepalive(failing_late(), interval=0.02))

    async def test_abandoning_the_stream_closes_the_inner_generator(self):
        """The inner generator owns the turn's cleanup; leaking it leaks a file.

        ``build_stream``'s ``finally`` is what reaps in-flight TTS tasks and
        unlinks the staged upload. A wrapper that is closed while suspended at
        one of its own yields -- which is what a client disconnect does -- must
        therefore close the iterator it wraps, or that cleanup never runs.
        """
        closed = asyncio.Event()

        async def with_cleanup():
            try:
                yield 'data: {"event": "a", "data": {}}\n\n'
                await asyncio.sleep(10)
            finally:
                closed.set()

        stream = with_keepalive(with_cleanup(), interval=30.0)
        async for _ in stream:
            break
        await stream.aclose()

        assert closed.is_set(), (
            "the wrapped generator's finally block never ran: its cleanup "
            "(TTS reaping, staged-upload unlink) is skipped for every client "
            "that disconnects mid-stream"
        )

    async def test_the_default_interval_is_read_at_call_time(self):
        """A default bound at ``def`` time cannot be patched, and cannot be
        tuned to a deployment that needs a different value."""
        async def stalled():
            await asyncio.sleep(0.2)
            yield "x"

        with patch.object(sse, "SSE_KEEPALIVE_INTERVAL_SECONDS", 0.01):
            out = await _drain(with_keepalive(stalled()))

        assert sse_keepalive() in out, (
            "with_keepalive bound the interval as a default argument, so the "
            "module constant is only a suggestion and no test can shorten it"
        )


# ─── End to end, through the real endpoint ─────────────────────────────────


@pytest.fixture
def mock_services(isolated_write_targets):
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = "¿Qué es InterviewTTS?"

        stub_rag_context_shapes(mock_rag)
        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]

        mock_llm.generate.return_value = "Un proyecto de entrevista."
        mock_llm.generate_stream_with_context.return_value = (iter(["Un proyecto."]), [])

        async def mock_synthesize(text, output_path=None):
            path = output_path or isolated_write_targets.audio / "test.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

        mock_tts.synthesize = mock_synthesize
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {"cv.md": "content"}
        yield {"stt": mock_stt, "llm": mock_llm, "tts": mock_tts}


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


class TestTheRealEndpointIsNotSilentWhileWhisperThinks:
    def test_a_slow_transcription_still_puts_bytes_on_the_wire(self, mock_services):
        """The wire, not the unit: a real POST, real generator, real stall.

        Everything below the endpoint is real. STT is the only double, because
        making a real Whisper slow is the one thing a test cannot do cheaply --
        and it is the wait being modelled, which is the entire subject here.
        """
        from backend.main import app

        # The transcript is captured as a VALUE, not as the mock: installing a
        # side_effect on the same mock and then calling it from inside that
        # side_effect is unbounded recursion, and it surfaces as a
        # RecursionError logged from inside the stream rather than as an
        # assertion failure.
        transcript = mock_services["stt"].transcribe.return_value

        def slow_transcribe(_path):
            # A 0.6 s transcription against a 0.05 s keepalive: the same ratio a
            # 60 s CPU transcription has against a 5 s one, compressed.
            time.sleep(0.6)
            return transcript

        mock_services["stt"].transcribe.side_effect = slow_transcribe

        client = TestClient(app)
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        with patch.object(sse, "SSE_KEEPALIVE_INTERVAL_SECONDS", 0.05):
            with client.stream(
                "POST",
                f"/api/conversation/{conversation_id}/message/stream",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            ) as response:
                assert response.status_code == 200
                raw = "".join(
                    chunk.decode("utf-8") for chunk in response.iter_raw()
                )

        keepalives = raw.count(sse_keepalive())
        assert keepalives >= 1, (
            "a 0.6 s STT with a 0.05 s keepalive produced no keepalive frame: the "
            "response body was silent for the whole transcription, which is the "
            f"defect. Body was {raw!r}"
        )
        assert raw.index(sse_keepalive()) < raw.index('"transcription"'), (
            "the keepalive came after the first real event, so it protected "
            "nothing"
        )
        events = frontend_reads(raw)
        assert [e["event"] for e in events][-1] == "done", (
            f"the turn did not complete; events were {[e['event'] for e in events]}"
        )


# ─── The two halves cannot drift ───────────────────────────────────────────


def _strip_comments(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def _braced_block(source: str, header_re: str) -> str:
    match = re.search(header_re, source)
    assert match is not None, f"no block matching {header_re!r} in {NGINX_CONF}"
    start = source.index("{", match.end() - 1)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in {NGINX_CONF}")


def _read_timeout_seconds() -> int | None:
    match = re.search(
        r"proxy_read_timeout\s+(\d+)([smh]?)\s*;",
        _strip_comments(_braced_block(NGINX_CONF.read_text(encoding="utf-8"),
                                      r"location\s+/api/\s*\{")),
    )
    if match is None:
        return None
    scale = {"": 1, "s": 1, "m": 60, "h": 3600}
    return int(match.group(1)) * scale[match.group(2).lower()]


class TestTheTwoHalvesCannotDrift:
    def test_the_api_location_sets_a_read_timeout_at_all(self):
        assert _read_timeout_seconds() is not None, (
            "location /api/ sets no proxy_read_timeout, so nginx applies its "
            f"{NGINX_DEFAULT_READ_TIMEOUT}s default and closes a stream that "
            "has been silent for longer -- which is the whole STT window on a "
            "slow CPU transcription"
        )

    def test_the_read_timeout_outlives_several_keepalives(self):
        declared = _read_timeout_seconds()
        assert declared is not None
        minimum = SSE_KEEPALIVE_MINIMUM_RATIO * sse.SSE_KEEPALIVE_INTERVAL_SECONDS

        assert declared >= minimum, (
            f"proxy_read_timeout is {declared}s but the keepalive fires every "
            f"{sse.SSE_KEEPALIVE_INTERVAL_SECONDS}s, so the timeout can be "
            f"reached with fewer than {SSE_KEEPALIVE_MINIMUM_RATIO} keepalives "
            "inside it. Raise the timeout or shorten the interval; do not leave "
            "one reading the other as a comment"
        )

    def test_the_keepalive_alone_beats_the_stock_nginx_default(self):
        """Defence in depth, and the reason the change is not a coin flip.

        Even with ``proxy_read_timeout`` absent, a keepalive interval under 60 s
        means the connection is never idle for the default. So the wrapper alone
        already prevents the reported failure, and the timeout stops being the
        only thing standing between a slow turn and a lost one.
        """
        assert sse.SSE_KEEPALIVE_INTERVAL_SECONDS < NGINX_DEFAULT_READ_TIMEOUT, (
            f"the keepalive interval ({sse.SSE_KEEPALIVE_INTERVAL_SECONDS}s) is "
            f"at or above nginx's stock {NGINX_DEFAULT_READ_TIMEOUT}s default, so "
            "the frame would arrive too late to stop the truncation it exists "
            "to prevent"
        )


SSE_KEEPALIVE_MINIMUM_RATIO = MIN_TIMEOUT_TO_KEEPALIVE_RATIO
