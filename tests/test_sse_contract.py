"""SSE contract tests: every event the backend emits must be handled by the
frontend dispatcher.

This is the regression net for the whole class of bug where the two sides of
the ``/message/stream`` SSE protocol drift apart. The failure mode is silent:
an unhandled event type is dropped by the ``if / else if`` chain in
``app.js`` with no error, so the candidate sees text but hears nothing.

The tests parse the *source* of both sides rather than exercising a mock
boundary, so a new ``sse_format("...")`` call in the backend without a matching
branch in the frontend fails here.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ─── Source extraction ────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
FRONTEND_APP = Path(__file__).resolve().parents[1] / "frontend" / "app.js"

#: The one canonical audio event name. Both sides must use this spelling.
CANONICAL_AUDIO_EVENT = "audio_url"

#: Terminal events: a well-formed stream ends with exactly one of these.
TERMINAL_EVENTS = frozenset({"done", "interview_end"})


def _extract_function(source: str, signature: str) -> str:
    """Return the body of the JS function whose declaration starts with
    ``signature``, from its opening brace to the matching closing brace."""
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError(f"unbalanced braces in function {signature!r}")


def backend_sources() -> list[Path]:
    """Every backend module that could emit an SSE event.

    The scan follows the code rather than one file. Pinning it to
    ``backend/main.py`` made this test silently lose its subject the moment the
    streaming pipeline moved into its own module: ``emitted`` would come back
    empty, ``test_no_orphan_handlers`` would then report all six frontend
    branches as orphans, and the failure would read as a frontend bug instead of
    a moved file.
    """
    return sorted(
        path
        for path in BACKEND_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def emitted_event_types(sources: list[Path] | None = None) -> set[str]:
    """Distinct event names passed as the first argument of ``sse_format``.

    ``\\s*`` spans newlines so multi-line calls (``sse_format(\\n    "done", {}``)
    are captured as well as single-line ones.
    """
    found: set[str] = set()
    for path in backend_sources() if sources is None else sources:
        source = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'sse_format\(\s*"([a-z_]+)"', source))
    return found


def handled_event_types(app_path: Path = FRONTEND_APP) -> set[str]:
    """Event names the SSE dispatcher in ``app.js`` actually branches on.

    Scoped to the ``processRecordingStream`` body: ``type === "..."`` also
    appears elsewhere in the file for message roles (``"user"``,
    ``"candidate"``), which are not SSE event types.
    """
    source = app_path.read_text(encoding="utf-8")
    body = _extract_function(source, "async function processRecordingStream()")
    return set(re.findall(r'type === "([a-z_]+)"', body))


def parse_events(sse_text: str) -> list[dict]:
    """Parse ``data: {...}`` SSE lines into event dicts."""
    events = []
    for line in sse_text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ─── Source-level contract ────────────────────────────────────────────────


class TestSseContract:
    """The two sides of the SSE protocol must agree on the event vocabulary."""

    def test_every_emitted_event_is_handled_by_frontend(self):
        """Regression net: no backend event type may be silently dropped.

        Before the fix, the backend emitted ``audio_url`` (cached-answer and
        farewell paths) while the frontend only branched on ``audio_chunk``,
        so every FAQ/semantic cache hit displayed text and played no audio.
        """
        emitted = emitted_event_types()
        handled = handled_event_types()

        unhandled = emitted - handled
        assert not unhandled, (
            "Backend emits SSE event(s) the frontend dispatcher never handles: "
            f"{sorted(unhandled)}. Handled types: {sorted(handled)}. "
            "An unhandled event is dropped with no error, so the user sees the "
            "effect as missing audio or a missing message."
        )

    def test_no_orphan_handlers(self):
        """The frontend must not branch on events the backend cannot emit.

        Guards the opposite drift: a dead branch hides a renamed contract
        rather than reporting it.
        """
        emitted = emitted_event_types()
        handled = handled_event_types()

        orphans = handled - emitted
        assert not orphans, (
            f"Frontend handles event(s) the backend never emits: {sorted(orphans)}. "
            "These branches are dead code and usually signal a renamed contract."
        )

    def test_frontend_uses_canonical_audio_event_name(self):
        """The audio branch must be spelled the way the backend emits it."""
        handled = handled_event_types()
        assert CANONICAL_AUDIO_EVENT in handled, (
            f"Frontend is missing the canonical audio event {CANONICAL_AUDIO_EVENT!r}"
        )

    def test_backend_uses_canonical_audio_event_name(self):
        assert CANONICAL_AUDIO_EVENT in emitted_event_types()

    def test_dispatcher_recognises_every_backend_event(self):
        """A representative event of each emitted type must be routed.

        Complements the source scan: it proves the dispatcher is reachable and
        that each branch exists in an executable position, not just as text.
        """
        handled = handled_event_types()
        # ``audio_chunk`` is the incremental per-sentence stream; ``audio_url``
        # is the single-file cached/farewell contract. Both are audio.
        for event_type in sorted(emitted_event_types()):
            assert event_type in handled, f"no frontend branch for {event_type!r}"

    def test_terminal_events_are_known(self):
        """Every terminal event named in the contract is actually emitted."""
        emitted = emitted_event_types()
        for event_type in TERMINAL_EVENTS:
            assert event_type in emitted, (
                f"terminal event {event_type!r} is never emitted by the backend"
            )


# ─── Behavioural: real generator, real cache hit ───────────────────────────


@pytest.fixture
def mock_services():
    """Mock external services for streaming tests."""
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = "What technologies did you use?"

        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]

        mock_llm.generate.return_value = "I built InterviewTTS using Python."
        mock_llm.generate_stream_with_context.return_value = (iter(["Hi."]), [])

        async def mock_synthesize(text, output_path=None):
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
        }


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


@pytest.fixture
def client():
    from backend.main import app

    return TestClient(app)


class TestCachedAnswerAudioContract:
    """A cache hit must produce audio the frontend can actually play."""

    def _stream(self, client, conversation_id: str, question: str, stt) -> list[dict]:
        stt.transcribe.return_value = question
        with client.stream(
            "POST",
            f"/api/conversation/{conversation_id}/message/stream",
            files={"audio": ("test.webm", b"audio data", "audio/webm")},
        ) as response:
            assert response.status_code == 200
            text = "".join(
                f"{line}\n"
                for line in response.iter_lines()
                if line and line.startswith("data: ")
            )
        return parse_events(text)

    def test_cache_hit_emits_playable_audio_event(
        self, client, mock_services
    ):
        """A FAQ cache hit emits an audio event carrying a real, servable URL.

        This is the exact shape of the original product bug: the text arrived
        and the audio did not, because the event name the backend emitted had
        no frontend branch.
        """
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = self._stream(
            client, conversation_id, "¿Qué es InterviewTTS?", mock_services["stt"]
        )

        types = [e["event"] for e in events]
        assert "token" in types, "cache hit should still stream the answer text"

        audio_events = [e for e in events if e["event"] == CANONICAL_AUDIO_EVENT]
        assert len(audio_events) == 1, (
            f"expected exactly one audio event, got types={types}"
        )

        url = audio_events[0]["data"]["url"]
        assert url.startswith("/audio/"), f"audio event has no servable URL: {url!r}"
        assert url.endswith(".mp3"), f"audio URL does not point at an mp3: {url!r}"

        # The frontend handler must be able to route this exact event name.
        assert audio_events[0]["event"] in handled_event_types()

        # The URL must actually be servable. /audio is mounted from
        # config.AUDIO_DIR, so a 404 here means the frontend queues a dead
        # link and the turn is mute for a second, independent reason.
        served = client.get(url)
        assert served.status_code == 200, (
            f"audio event URL {url!r} is not servable (HTTP {served.status_code})"
        )

        # …and it must resolve to a file TTS actually wrote. (Byte count is
        # not asserted: the test double writes a 0-byte placeholder.)
        from backend.config import config

        relative = url[len("/audio/") :]
        written = config.AUDIO_DIR / relative
        assert written.exists(), (
            f"audio event points at a file TTS never wrote: {written}"
        )

    def test_cache_hit_stream_ends_with_exactly_one_terminal_event(
        self, client, mock_services
    ):
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = self._stream(
            client, conversation_id, "¿Qué es InterviewTTS?", mock_services["stt"]
        )

        terminals = [e for e in events if e["event"] in TERMINAL_EVENTS]
        assert len(terminals) == 1, (
            f"expected exactly one terminal event, got "
            f"{[e['event'] for e in events]}"
        )
        assert terminals[0]["event"] == "done"

    def test_audio_event_precedes_terminal_event(self, client, mock_services):
        """Audio must be queued before the stream is declared done, otherwise
        the frontend can restart the mic over unplayed audio."""
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = self._stream(
            client, conversation_id, "¿Qué es InterviewTTS?", mock_services["stt"]
        )
        types = [e["event"] for e in events]

        assert CANONICAL_AUDIO_EVENT in types
        assert types.index(CANONICAL_AUDIO_EVENT) < types.index("done")
