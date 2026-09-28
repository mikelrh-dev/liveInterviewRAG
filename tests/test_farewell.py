"""Farewell-path tests: the interview must end out loud, and end consistently.

Before the fix the farewell branch emitted ``token`` events and then
``interview_end`` with ``audio_url: ""`` and never called TTS, so every
interview ended in silence -- in the one interaction that defines the
product. Because no ``done`` was emitted either, the frontend's turn counter
never advanced and ended one turn behind what was persisted.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.response_cache import get_cached_response

# A phrase that trips detect_farewell() and is not a cached question.
FAREWELL_INPUT = "Muchas gracias, eso es todo"


@pytest.fixture
def mock_services():
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = FAREWELL_INPUT

        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]

        mock_llm.generate.return_value = "I built InterviewTTS using Python."
        mock_llm.generate_stream_with_context.return_value = (iter(["Hi."]), [])

        # A recording double, so tests can assert on what was actually spoken
        # without reaching for await_args on a bare function.
        spoken: list[tuple] = []

        async def mock_synthesize(text, output_path=None):
            spoken.append((text, output_path))
            path = output_path or Path("audio/test.mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return path

        mock_tts.synthesize = mock_synthesize
        mock_profile.profile_data = {"name": "Mikel"}
        mock_profile.documents = {"cv.md": "content"}

        yield {
            "stt": mock_stt,
            "llm": mock_llm,
            "tts": mock_tts,
            "rag": mock_rag,
            "profile": mock_profile,
            "spoken": spoken,
        }


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


@pytest.fixture
def client():
    from backend.main import app

    return TestClient(app)


def _stream(client, conversation_id, files=None):
    """POST audio to /message/stream and return the parsed SSE events."""
    with client.stream(
        "POST",
        f"/api/conversation/{conversation_id}/message/stream",
        files={"audio": ("test.webm", b"audio data", "audio/webm")},
    ) as response:
        assert response.status_code == 200
        body = "".join(
            f"{line}\n" for line in response.iter_lines()
            if line and line.startswith("data: ")
        )
    return [json.loads(line[6:]) for line in body.splitlines() if line.strip()]


def _types(events):
    return [e["event"] for e in events]


class TestFarewellSpeaks:
    """The farewell must be synthesized and played, not just printed."""

    def test_farewell_emits_audio_event_with_real_path(self, client, mock_services):
        """A farewell produces an audio event whose URL resolves to a real file."""
        assert get_cached_response(FAREWELL_INPUT) is None, (
            "the farewell fixture must not be a cached question, "
            "otherwise this test silently exercises the cache path"
        )
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, conversation_id)
        types = _types(events)

        assert "interview_end" in types, f"farewell never ended: {types}"
        assert "token" in types, f"farewell text was not streamed: {types}"

        audio_events = [e for e in events if e["event"] == "audio_url"]
        assert len(audio_events) == 1, (
            f"expected exactly one farewell audio event, got {types}"
        )

        url = audio_events[0]["data"]["url"]
        assert url, "farewell audio event carries an empty URL"
        assert url.startswith("/audio/") and url.endswith(".mp3"), (
            f"farewell audio URL is not a servable mp3: {url!r}"
        )

        from backend.config import config

        written = config.AUDIO_DIR / url[len("/audio/") :]
        assert written.exists(), (
            f"farewell audio event points at a file TTS never wrote: {written}"
        )

    def test_farewell_calls_tts(self, client, mock_services):
        """TTS is actually invoked for the farewell text."""
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        _stream(client, conversation_id)

        spoken = mock_services["spoken"]
        assert len(spoken) == 1, f"the farewell was never synthesized: {spoken}"
        text, output_path = spoken[0]
        assert text, "TTS received empty text"
        assert output_path is not None, "TTS received no output path"

    def test_farewell_text_is_sanitized_before_tts(self, client, mock_services):
        """The farewell goes through sanitize_for_tts like every other call site."""
        from backend.prompts.candidate import sanitize_for_tts

        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, conversation_id)
        streamed = "".join(
            e["data"]["text"] for e in events if e["event"] == "token"
        )
        spoken = mock_services["spoken"][0][0]

        # No markdown markers survive into the spoken text.
        assert "*" not in spoken and "`" not in spoken, (
            f"farewell text was not sanitized for TTS: {spoken!r}"
        )
        # And the spoken text is the sanitized form of what was streamed.
        assert spoken == sanitize_for_tts(streamed)

    def test_audio_event_precedes_interview_end(self, client, mock_services):
        """Audio is queued before the stream terminates.

        If interview_end arrived first the frontend tears the session down and
        the queued audio never plays -- the same silence, arrived at
        differently.
        """
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, conversation_id)
        types = _types(events)

        assert "audio_url" in types, f"no audio event at all: {types}"
        assert types.index("audio_url") < types.index("interview_end"), (
            f"audio must be queued before terminal event, got {types}"
        )

    def test_farewell_ends_with_exactly_one_terminal_event(
        self, client, mock_services
    ):
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, conversation_id)
        types = _types(events)

        terminals = [t for t in types if t in ("done", "interview_end")]
        assert len(terminals) == 1, f"expected one terminal event, got {types}"
        assert terminals[0] == "interview_end"


class TestFarewellTurnConsistency:
    """The persisted turn count must match what the UI derives."""

    def test_persisted_turn_count_matches_ui_derivation(self, client, mock_services):
        """Every exchange is one turn on both sides of the wire.

        The frontend derives the turn from the rendered user/candidate message
        pairs and only advances the counter on a terminal event. The farewell
        branch used to persist its turn but emit no ``done``, so the sidebar
        lagged the database by exactly one.
        """
        from backend.main import conversations

        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, conversation_id)
        types = _types(events)

        # What the UI can derive: one user message plus one candidate message
        # (the candidate div is created by the first token and reused).
        user_messages = types.count("transcription")
        candidate_messages = 1 if "token" in types else 0
        ui_turns = min(user_messages, candidate_messages)

        assert user_messages == 1 and candidate_messages == 1
        assert len(conversations[conversation_id]["turns"]) == ui_turns, (
            f"persisted {len(conversations[conversation_id]['turns'])} turns "
            f"but the UI derives {ui_turns}"
        )

    def test_interview_end_advances_the_turn_counter(self, client, mock_services):
        """interview_end must drive the same terminal bookkeeping as done.

        The turn counter advances on settlement. If the farewell terminates on
        interview_end without settling, the sidebar lags the persisted turn
        count by exactly one -- which is what happened while that update lived
        only inside the `done` branch.
        """
        from tests.test_sse_contract import FRONTEND_APP

        source = FRONTEND_APP.read_text(encoding="utf-8")
        branch_start = source.index('type === "interview_end"')
        branch = source[branch_start : source.index('type === "error"', branch_start)]

        assert "turn.settle(" in branch, (
            "the interview_end branch must settle the turn, otherwise the "
            "sidebar lags the persisted turn count by one"
        )
        assert "stopInterview()" in branch, (
            "the farewell must still end the interview session"
        )

        # The counter advance is the settler hook's job, shared by all
        # terminal events.
        from tests.test_sse_terminal_state import _sse_dispatch_body

        call_site = _sse_dispatch_body()
        hook = call_site[
            call_site.index("onSettle(reason)") : call_site.index("try {")
        ]
        assert "updateTurnCount(" in hook, (
            "settling must advance the turn counter for every terminal event"
        )

    def test_farewell_turn_number_is_sequential(self, client, mock_services):
        """The farewell turn is numbered as the next turn, not restarted."""
        from backend.main import conversations

        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        _stream(client, conversation_id)

        turns = conversations[conversation_id]["turns"]
        assert len(turns) == 1
        assert turns[0]["n"] == 0
        assert turns[0]["user_text"] == FAREWELL_INPUT
        assert turns[0]["chunks_used"] == []


class TestFarewellTtsFailure:
    """A TTS failure must degrade, never wedge the interview."""

    def test_tts_failure_still_emits_interview_end(self, client, mock_services):
        """The stream reaches its terminal event even when TTS raises."""
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        async def failing_synth(text, output_path=None):
            raise RuntimeError("edge-tts exploded")

        mock_services["tts"].synthesize = failing_synth

        events = _stream(client, conversation_id)
        types = _types(events)

        assert "interview_end" in types, (
            f"a TTS failure stranded the interview (no terminal event): {types}"
        )
        terminals = [t for t in types if t in ("done", "interview_end")]
        assert len(terminals) == 1, f"expected one terminal event, got {types}"

    def test_tts_failure_emits_an_error_event(self, client, mock_services):
        """The user is told why the farewell was silent."""
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        async def failing_synth(text, output_path=None):
            raise RuntimeError("edge-tts exploded")

        mock_services["tts"].synthesize = failing_synth

        events = _stream(client, conversation_id)

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1, f"expected exactly one error event, got {_types(events)}"
        assert errors[0]["data"]["detail"], "error event carries no detail"
        # The candidate is told the goodbye was silent, and nothing more: the
        # provider's own text is server-side only.
        assert "despedida" in errors[0]["data"]["detail"].lower()
        assert "edge-tts" not in errors[0]["data"]["detail"]

    def test_tts_failure_generator_does_not_raise(self, client, mock_services):
        """A TTS failure is reported over SSE, not as a transport error.

        Raising out of the generator would surface to the client as a
        truncated body, which the frontend cannot distinguish from a network
        failure and which leaves the audio indicator spinning.
        """
        from backend.main import app
        from starlette.testclient import TestClient as RawClient

        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        async def failing_synth(text, output_path=None):
            raise RuntimeError("edge-tts exploded")

        mock_services["tts"].synthesize = failing_synth

        with patch("backend.main.report_service") as mock_report:
            mock_report.generate.return_value = None
            raw = RawClient(app, raise_server_exceptions=True)
            response = raw.post(
                f"/api/conversation/{conversation_id}/message/stream",
                files={"audio": ("test.webm", b"audio data", "audio/webm")},
            )

        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert "interview_end" in _types(events)

    def test_tts_failure_does_not_persist_an_audio_url(
        self, client, mock_services
    ):
        """A failed synthesis is persisted with an empty audio_url, not a lie.

        Storing a URL that will 404 would replay the silence on every later
        read of the transcript.
        """
        from backend.main import conversations

        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        async def failing_synth(text, output_path=None):
            raise RuntimeError("edge-tts exploded")

        mock_services["tts"].synthesize = failing_synth

        with patch("backend.main.report_service") as mock_report:
            mock_report.generate.return_value = None
            _stream(client, conversation_id)

        messages = conversations[conversation_id]["messages"]
        assert messages, "the farewell exchange was not persisted at all"
        assert messages[-1]["audio_url"] == ""
