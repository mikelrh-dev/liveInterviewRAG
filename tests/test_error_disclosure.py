"""Error payloads must not carry internal exception text.

``openspec/specs/conversation-engine/spec.md`` (TTS Error Resilience) requires
that error payloads "SHALL NOT include internal paths, stack traces, or sensitive
details". Every emission below used to interpolate ``str(e)`` straight into the
``detail`` the browser renders, so a real ``sentence-transformers``, ``httpx`` or
provider exception shipped filesystem paths, model cache locations and upstream
response bodies -- fragments of which can include an API key -- to a public
portfolio endpoint.

The shape of the contract, stated once:

* **Sent to the client** -- a fixed, generic, human-readable message naming the
  stage that failed and whether the candidate can simply ask again.
* **Kept server-side** -- the exception itself, logged with ``exc_info=True``.
  The original is never discarded, only withheld: ``LLMService`` still chains the
  cause with ``raise ... from e``, so the object is reachable from the traceback
  even where the message was cleaned.

Every test here is written as the *absence* of a sentinel leak marker. The marker
is deliberately shaped like a real ``sentence-transformers`` cache path plus an
API-key fragment, so the test fails if anything resembling internal detail is
ever interpolated again -- not merely if this exact string is.
"""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

#: Shaped like the real thing: a model cache path and a key fragment.
LEAK = (
    "C:\\Users\\mikel\\.cache\\huggingface\\hub\\models--sbert--LaBSE"
    "\\snapshots\\deadbeef -- sk-or-v1-DEADBEEFCAFE"
)

#: The fragments every disclosure assertion matches on.
#:
#: Deliberately NOT the whole ``LEAK`` string: it contains backslashes, and
#: ``json.dumps`` escapes those, so searching the serialised SSE body for the
#: raw path can never match and the assertion would pass with the leak still
#: in place. These three survive any encoding -- a key fragment, a model cache
#: directory and the cache root -- so the test fails if anything resembling
#: internal detail is interpolated into a payload again.
LEAK_FRAGMENTS = ("sk-or-v1-DEADBEEFCAFE", "models--sbert--LaBSE", "huggingface")

#: A question that is not a cached FAQ literal, so the LLM is really called.
LLM_QUESTION = "How do you handle idempotency in payment webhooks?"

#: Events that terminate a turn. Mirrors tests/test_sse_terminal_state.py.
TERMINAL_EVENTS = ("done", "interview_end")


def _stream_events(client, conversation_id) -> list[dict]:
    with client.stream(
        "POST",
        f"/api/conversation/{conversation_id}/message/stream",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    ) as response:
        assert response.status_code == 200
        body = "".join(
            f"{line}\n"
            for line in response.iter_lines()
            if line and line.startswith("data: ")
        )
    return [json.loads(line[6:]) for line in body.splitlines() if line.strip()]


def _error_details(events) -> list[str]:
    return [e["data"].get("detail", "") for e in events if e["event"] == "error"]


def _terminal_count(events) -> int:
    return sum(1 for e in events if e["event"] in TERMINAL_EVENTS)


def _last_terminal_event(events) -> str | None:
    """The event the stream actually terminated on, or None if it never did."""
    terminal = [e["event"] for e in events if e["event"] in TERMINAL_EVENTS]
    return terminal[-1] if terminal else None


def _events_after_last_terminal(events) -> list[str]:
    """Event names that follow the terminal event.

    Not always empty, and that is deliberate. The farewell's committed turn
    number can only be known once the write returns, and the write happens
    after ``interview_end`` so the goodbye is never queued behind a slow disk.
    The number therefore arrives in one non-terminal event that trails the
    terminal one. Anything *else* trailing it -- a second terminal event, an
    error, more tokens -- means the stream is malformed.
    """
    last = max(
        (i for i, e in enumerate(events) if e["event"] in TERMINAL_EVENTS),
        default=len(events) - 1,
    )
    return [e["event"] for e in events[last + 1 :]]


def _assert_not_disclosed(payload, where: str) -> None:
    """The one assertion every case shares.

    Matched on ``LEAK_FRAGMENTS`` rather than the whole marker: see the note on
    that constant. A backslash path is invisible inside a JSON-escaped body, and
    an assertion that cannot fail is worse than no assertion.
    """
    for fragment in LEAK_FRAGMENTS:
        assert fragment not in payload, (
            f"internal detail ({fragment!r}) reached the client via {where}: {payload}"
        )


def _assert_disclosed_in_log(text: str, where: str) -> None:
    """The other half: withholding must not mean discarding."""
    for fragment in LEAK_FRAGMENTS:
        assert fragment in text, (
            f"the original failure ({fragment!r}) is not in the log for {where}: {text}"
        )


@pytest.fixture
def mock_services():
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = LLM_QUESTION

        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]

        mock_llm.generate.return_value = "Una respuesta."
        mock_llm.generate_stream_with_context.return_value = (
            iter(["Una frase. Otra frase."]),
            [],
        )

        async def mock_synthesize(text, output_path=None):
            path = Path(output_path or "audio/test.mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

        mock_tts.synthesize = mock_synthesize

        async def mock_synthesize_sentence(text, sentence_id, output_dir):
            path = Path(output_dir) / f"{sentence_id}.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return sentence_id, path

        mock_tts.synthesize_sentence = mock_synthesize_sentence
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {"cv.md": "content"}

        yield {
            "stt": mock_stt,
            "llm": mock_llm,
            "tts": mock_tts,
            "rag": mock_rag,
            "profile": mock_profile,
        }


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


@pytest.fixture
def client():
    from backend.main import app

    return TestClient(app)


def _turn(client, mock_services) -> dict:
    """POST one turn on the non-streaming endpoint."""
    conversation_id = client.post("/api/conversation").json()["conversation_id"]
    return client.post(
        f"/api/conversation/{conversation_id}/message",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    )


# ─── Streaming endpoint: the SSE `error` event ─────────────────────────────


class TestStreamingErrorDisclosure:
    """``backend/turns/streaming.py`` -- every `error` emission."""

    def test_fatal_mid_stream_error_hides_the_exception(self, client, mock_services):
        """A RAG failure mid-iteration used to ship ``str(e)`` verbatim."""
        mock_services["rag"].get_context_string.side_effect = RuntimeError(LEAK)

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        _assert_not_disclosed(json.dumps(events, ensure_ascii=False), "fatal stream")
        details = _error_details(events)
        assert details, [e["event"] for e in events]
        assert any("Error inesperado" in d for d in details), details

    def test_cached_answer_tts_failure_hides_the_exception(
        self, client, mock_services
    ):
        """The FAQ/cache-hit branch echoed the TTS exception into `detail`."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        _assert_not_disclosed(json.dumps(events, ensure_ascii=False), "cached TTS")
        details = _error_details(events)
        assert any("audio" in d for d in details), details

    def test_farewell_tts_failure_hides_the_exception(self, client, mock_services):
        """A goodbye that could not be spoken reported the raw failure."""
        mock_services["stt"].transcribe.return_value = "Gracias, eso es todo."

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        _assert_not_disclosed(json.dumps(events, ensure_ascii=False), "farewell TTS")
        details = _error_details(events)
        assert any("despedida" in d for d in details), details

    def test_llm_stream_failure_hides_the_exception(self, client, mock_services):
        """The provider thread stringified the failure into the queue.

        That string was the single worst case: ``LLMService`` embeds an upstream
        response body slice in several of its own messages, so this channel
        carried provider payloads to the client.
        """
        def exploding_stream(*args, **kwargs):
            def gen():
                yield "Frase parcial."
                raise RuntimeError(LEAK)

            return (gen(), [])

        mock_services["llm"].generate_stream_with_context.side_effect = exploding_stream

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        _assert_not_disclosed(json.dumps(events, ensure_ascii=False), "LLM stream")
        details = _error_details(events)
        assert any("respuesta" in d for d in details), details

    def test_per_sentence_tts_failure_hides_the_exception(
        self, client, mock_services
    ):
        """The done-set loop names the sentence, never the failure."""
        async def failing(text, sentence_id, output_dir):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize_sentence = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        _assert_not_disclosed(json.dumps(events, ensure_ascii=False), "per-sentence TTS")
        # The spec's contract for a recoverable error: a detail and the id of the
        # sentence that failed, so the frontend can skip that audio chunk.
        for event in events:
            if event["event"] == "error":
                assert "id" in event["data"], event


# ─── Blocking endpoint: the HTTP `detail` body ──────────────────────────────


class TestBlockingErrorDisclosure:
    """``backend/turns/blocking.py`` -- three interpolating HTTPExceptions."""

    def test_stt_failure_detail_hides_the_exception(self, client, mock_services):
        mock_services["stt"].transcribe.side_effect = RuntimeError(LEAK)

        response = _turn(client, mock_services)

        assert response.status_code == 422
        _assert_not_disclosed(response.text, "STT 422")
        assert "transcribir" in response.json()["detail"].lower()

    def test_llm_failure_detail_hides_the_exception(self, client, mock_services):
        mock_services["llm"].generate.side_effect = RuntimeError(LEAK)

        response = _turn(client, mock_services)

        assert response.status_code == 503
        _assert_not_disclosed(response.text, "LLM 503")
        assert "respuesta" in response.json()["detail"].lower()

    def test_tts_failure_detail_hides_the_exception(self, client, mock_services):
        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        response = _turn(client, mock_services)

        assert response.status_code == 503
        _assert_not_disclosed(response.text, "TTS 503")
        assert "audio" in response.json()["detail"].lower()


# ─── Provider exceptions must not embed the upstream body ───────────────────


def _http_response(status_code: int, body: str, streaming: bool = False):
    """A response whose upstream body is the leak marker."""
    response = MagicMock()
    response.status_code = status_code
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.text = body
    response.read.return_value = body.encode("utf-8")
    if streaming:
        response.iter_lines = MagicMock(
            side_effect=AssertionError("a non-200 must not be parsed")
        )
    return response


class TestProviderExceptionsCarryNoUpstreamDetail:
    """``backend/services/llm.py`` -- the deepest layer.

    These messages are the source the emission sites copy. Cleaning only the
    emission sites would leave every future caller free to leak again, so the
    upstream body is logged here and never carried in the message.
    """

    def test_openrouter_blocking_error_omits_the_upstream_body(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.post.return_value = _http_response(500, LEAK)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                LLMService(api_key="k")._openrouter_generate("hola")

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_openrouter_transport_error_omits_the_cause(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.post.side_effect = httpx.ConnectError(LEAK)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                LLMService(api_key="k")._openrouter_generate("hola")

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_openrouter_streaming_error_omits_the_upstream_body(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.stream.return_value = _http_response(500, LEAK, streaming=True)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                list(LLMService(api_key="k")._openrouter_generate_stream("hola"))

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_google_blocking_error_omits_the_upstream_body(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.post.return_value = _http_response(500, LEAK)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                LLMService(api_key="k", google_api_key="g")._googleai_generate("hola")

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_google_streaming_error_omits_the_upstream_body(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.stream.return_value = _http_response(500, LEAK, streaming=True)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                list(
                    LLMService(api_key="k", google_api_key="g")
                    ._googleai_generate_stream("hola")
                )

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_google_transport_error_omits_the_cause(self):
        from backend.services.llm import LLMService

        client = MagicMock()
        client.stream.side_effect = httpx.ReadError(LEAK)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                list(
                    LLMService(api_key="k", google_api_key="g")
                    ._googleai_generate_stream("hola")
                )

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_degraded_answer_abort_omits_the_cause(self):
        """The partial-answer abort reported how far it got, plus the raw error.

        The token count is the operator-facing part and stays; the transport
        text is reachable through the chained cause, so the message does not
        need it.
        """
        from backend.services.llm import LLMService

        def _iter_lines():
            # A generator, not a list side_effect: iter_lines() is iterated
            # once, and a plain string return value would be walked character
            # by character and the transport death never reached.
            yield 'data: {"candidates": [{"content": {"parts": [{"text": "A "}]}}]}'
            raise httpx.ReadError(LEAK)

        google = MagicMock()
        google.status_code = 200
        google.__enter__ = MagicMock(return_value=google)
        google.__exit__ = MagicMock(return_value=False)
        google.iter_lines = _iter_lines

        client = MagicMock()
        client.stream.return_value = google

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError) as excinfo:
                list(
                    LLMService(api_key="k", google_api_key="g").generate_stream("hola")
                )

        _assert_not_disclosed(str(excinfo.value), "provider exception message")
        # The part an operator needs: the answer was already truncated, so
        # nothing may be appended to it.
        assert "incomplete" in str(excinfo.value), str(excinfo.value)
        # The cause chain still carries the transport error, so nothing that
        # was cleaned out of the message is actually lost.
        cause = excinfo.value.__cause__
        assert isinstance(cause, RuntimeError), repr(cause)
        assert isinstance(cause.__cause__, httpx.ReadError), repr(cause.__cause__)


class TestServiceExceptionsCarryNoInternalDetail:
    """``tts.py`` / ``stt.py`` re-raise the provider text in their own message."""

    def test_tts_reraise_omits_the_cause(self, tmp_path):
        from backend.services.tts import TTSService

        service = TTSService(output_dir=tmp_path)
        with patch(
            "backend.services.tts.edge_tts.Communicate", side_effect=RuntimeError(LEAK)
        ):
            with pytest.raises(RuntimeError) as excinfo:
                asyncio.run(
                    service.synthesize("hola", output_path=tmp_path / "out.mp3")
                )

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_stt_reraise_omits_the_cause(self, tmp_path):
        from backend.services.stt import STTService

        staged = tmp_path / "audio.webm"
        staged.write_bytes(b"audio data")

        service = STTService()
        service._model = MagicMock()
        service._model.transcribe.side_effect = RuntimeError(LEAK)

        with pytest.raises(RuntimeError) as excinfo:
            service.transcribe(staged)

        _assert_not_disclosed(str(excinfo.value), "provider exception message")

    def test_stt_missing_file_omits_the_path(self, tmp_path):
        """A FileNotFoundError naming the staged path is a filesystem disclosure."""
        from backend.services.stt import STTService

        service = STTService()
        service._model = MagicMock()

        missing = tmp_path / "no-such-dir-abc123" / "audio.webm"
        assert not missing.exists()

        with pytest.raises(FileNotFoundError) as excinfo:
            service.transcribe(missing)

        assert str(tmp_path) not in str(excinfo.value), str(excinfo.value)
        assert "no-such-dir-abc123" not in str(excinfo.value), str(excinfo.value)


# ─── Withheld is not the same as discarded ──────────────────────────────────


class TestInternalDetailIsStillLogged:
    """A failure nobody can see in the logs is not fixed, it is hidden.

    Each case asserts the original text survives in a log record even though it
    no longer reaches the client. For the blocking endpoint this is new
    behaviour: ``HTTPException`` is handled by FastAPI, so before the fix these
    three failures produced no server-side record at all.
    """

    def test_fatal_stream_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.streaming")
        mock_services["rag"].get_context_string.side_effect = RuntimeError(LEAK)

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        _stream_events(client, conversation_id)

        _assert_disclosed_in_log(caplog.text, "server log")
        # A traceback, not just the message: the operator needs the frames.
        assert any(r.exc_info for r in caplog.records), caplog.records

    def test_cached_tts_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.streaming")
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        _stream_events(client, conversation_id)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_farewell_tts_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.streaming")
        mock_services["stt"].transcribe.return_value = "Gracias, eso es todo."

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        _stream_events(client, conversation_id)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_llm_stream_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.streaming")

        def exploding_stream(*args, **kwargs):
            def gen():
                yield "Frase parcial."
                raise RuntimeError(LEAK)

            return (gen(), [])

        mock_services["llm"].generate_stream_with_context.side_effect = exploding_stream

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        _stream_events(client, conversation_id)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_blocking_stt_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.blocking")
        mock_services["stt"].transcribe.side_effect = RuntimeError(LEAK)

        _turn(client, mock_services)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_blocking_llm_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.blocking")
        mock_services["llm"].generate.side_effect = RuntimeError(LEAK)

        _turn(client, mock_services)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_blocking_tts_failure_is_logged_with_the_original(
        self, client, mock_services, caplog
    ):
        caplog.set_level(logging.ERROR, logger="backend.turns.blocking")

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        _turn(client, mock_services)

        _assert_disclosed_in_log(caplog.text, "server log")

    def test_upstream_provider_body_is_logged_not_sent(self, caplog):
        """The body is the only record of what the provider actually said.

        Scoped to the streaming call on purpose: that is where the body was
        read and embedded in the raised message, so it is where withholding it
        has to be paid for with a log line. The blocking ``.post`` path never
        read the body at all, so nothing was lost there -- see the report.
        """
        from backend.services.llm import LLMService

        caplog.set_level(logging.ERROR, logger="backend.services.llm")
        client = MagicMock()
        client.stream.return_value = _http_response(500, LEAK, streaming=True)

        with patch("backend.services.llm._get_client", return_value=client):
            with pytest.raises(RuntimeError):
                list(LLMService(api_key="k")._openrouter_generate_stream("hola"))

        _assert_disclosed_in_log(caplog.text, "server log")


# ─── The terminal-event invariant survives the change ───────────────────────


class TestTerminalEventInvariantSurvives:
    """Withholding detail must not cost the stream its terminal event.

    The client cannot distinguish a truncated body from a network drop, so every
    failing path still has to end with exactly one `done`/`interview_end`.
    """

    def test_fatal_error_still_terminates_once(self, client, mock_services):
        mock_services["rag"].get_context_string.side_effect = RuntimeError(LEAK)
        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        assert _terminal_count(events) == 1, [e["event"] for e in events]

    def test_cached_tts_failure_still_terminates_once(self, client, mock_services):
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        assert _terminal_count(events) == 1, [e["event"] for e in events]

    def test_farewell_tts_failure_still_ends_the_interview_once(
        self, client, mock_services
    ):
        mock_services["stt"].transcribe.return_value = "Gracias, eso es todo."

        async def failing(text, output_path=None):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        assert _terminal_count(events) == 1, [e["event"] for e in events]
        # The stream still terminates on `interview_end`, not on a truncation
        # and not on `done`. This used to read `events[-1]`, which is no longer
        # the same claim: the farewell's committed turn number travels in a
        # non-terminal `turn_recorded` that necessarily follows the terminal
        # event, because the write that produces the number happens after it on
        # purpose. The last *terminal* event is still the invariant, and the
        # trailing-events assertion below keeps the stream's shape pinned in
        # the dimension that matters: nothing but the turn number may follow
        # the goodbye, so a second terminal event or a post-hoc error still
        # fails here.
        assert _last_terminal_event(events) == "interview_end", [e["event"] for e in events]
        assert _events_after_last_terminal(events) in ([], ["turn_recorded"]), [
            e["event"] for e in events
        ]

    def test_llm_stream_failure_still_terminates_once(self, client, mock_services):
        def exploding_stream(*args, **kwargs):
            def gen():
                yield "Frase parcial."
                raise RuntimeError(LEAK)

            return (gen(), [])

        mock_services["llm"].generate_stream_with_context.side_effect = exploding_stream
        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        assert _terminal_count(events) == 1, [e["event"] for e in events]

    def test_per_sentence_tts_failure_still_terminates_once(
        self, client, mock_services
    ):
        async def failing(text, sentence_id, output_dir):
            raise RuntimeError(LEAK)

        mock_services["tts"].synthesize_sentence = failing
        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        assert _terminal_count(events) == 1, [e["event"] for e in events]
