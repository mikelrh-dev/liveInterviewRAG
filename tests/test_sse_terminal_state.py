"""Terminal-event guarantees for /message/stream.

Two halves of one contract:

1. Backend -- a stream must reach exactly one terminal event (``done`` or
   ``interview_end``). Several paths used to ``return`` after an ``error``
   with no terminal event, which the frontend cannot distinguish from a
   network failure: the mic never restarts and the audio indicator spins on.

2. Frontend -- every terminal path must funnel through one idempotent
   settler, and the ``error`` branch must not abort the stream. These are
   verified two ways: behaviourally, by running the Node test for the
   extracted settler (see tests/frontend/terminal_state.test.mjs), and
   structurally, by asserting each terminal branch in app.js routes through
   it. Node has no DOM, so the DOM-touching code in those branches is not
   covered by automation; the structural tests are the guard for it.
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_APP = REPO_ROOT / "frontend" / "app.js"
NODE_TEST = Path(__file__).resolve().parent / "frontend" / "terminal_state.test.mjs"

#: Events that terminate a turn. ``error`` is deliberately NOT one of them:
#: the codebase uses it as an advisory event that can be followed by ``done``
#: (a failed sentence is skipped and the stream continues), and
#: tests/test_api.py::TestStreamingTTSErrors already asserts that shape.
TERMINAL_EVENTS = ("done", "interview_end")


def _stream_events(client, conversation_id) -> list[dict]:
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


def _terminal_count(events) -> int:
    return sum(1 for e in events if e["event"] in TERMINAL_EVENTS)


@pytest.fixture
def mock_services():
    with patch("backend.main.stt_service") as mock_stt, \
         patch("backend.main.llm_service") as mock_llm, \
         patch("backend.main.tts_service") as mock_tts, \
         patch("backend.main.rag_pipeline") as mock_rag, \
         patch("backend.main.candidate_profile") as mock_profile:

        mock_stt.is_loaded = True
        mock_stt.transcribe.return_value = "How do you architect a system?"

        mock_rag.get_context_string.return_value = ""
        mock_rag.get_chunks_with_scores.return_value = []
        mock_rag.chunks = [MagicMock()]

        mock_llm.generate.return_value = "An answer."
        mock_llm.generate_stream_with_context.return_value = (
            iter(["A sentence. Another one."]),
            [],
        )

        async def mock_synthesize(text, output_path=None):
            if output_path is None:
                raise ValueError("Cannot synthesize empty text")
            path = Path(output_path)
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


# ─── Backend: exactly one terminal event, always ──────────────────────────


class TestTerminalEventGuarantee:
    """No path may end a stream without a terminal event."""

    def test_happy_path_has_exactly_one_terminal_event(
        self, client, mock_services
    ):
        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)

        assert _terminal_count(events) == 1, [e["event"] for e in events]
        assert events[-1]["event"] == "done", (
            "the terminal event must be last, got "
            f"{[e['event'] for e in events]}"
        )

    def test_empty_transcription_terminates(self, client, mock_services):
        """No speech detected: the stream must still terminate, not hang."""
        mock_services["stt"].transcribe.return_value = "   "
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream_events(client, conversation_id)

        assert any(e["event"] == "error" for e in events), (
            "the user must be told nothing was heard"
        )
        assert _terminal_count(events) == 1, [e["event"] for e in events]

    def test_empty_transcription_leaves_the_interview_usable(
        self, client, mock_services
    ):
        """A silent recording must not end the interview.

        The error event used to be thrown out of the read loop into a catch
        that called stopInterview(), so saying nothing killed the session.
        With a terminal event instead, the frontend's checkAllDone() puts the
        mic back.
        """
        mock_services["stt"].transcribe.return_value = ""
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        events = _stream_events(client, conversation_id)

        assert "interview_end" not in [e["event"] for e in events], (
            "a silent recording must not end the interview"
        )
        assert _terminal_count(events) == 1

    def test_cached_answer_tts_failure_terminates(self, client, mock_services):
        """A cache-hit TTS failure reports and terminates."""
        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"

        async def failing(text, output_path=None):
            raise RuntimeError("edge-tts down")

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        types = [e["event"] for e in events]

        assert "error" in types, types
        assert _terminal_count(events) == 1, types

    def test_cached_answer_tts_failure_does_not_persist_the_turn(
        self, client, mock_services
    ):
        """The failed exchange must not be stored as if it succeeded.

        The original code returned before the write-through, so the DB and
        the sidebar both stayed at the previous turn. The invariant that
        matters is simply that the two agree, asserted directly.
        """
        from backend.main import conversations

        mock_services["stt"].transcribe.return_value = "¿Qué es InterviewTTS?"

        async def failing(text, output_path=None):
            raise RuntimeError("edge-tts down")

        mock_services["tts"].synthesize = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        _stream_events(client, conversation_id)

        turns = conversations[conversation_id]["turns"]
        messages = conversations[conversation_id]["messages"]
        assert len(turns) == len(messages), (
            f"{len(turns)} turns but {len(messages)} messages -- the transcript "
            "and the turn numbering disagree"
        )

    def test_tts_failure_mid_stream_terminates_once(self, client, mock_services):
        """Per-sentence TTS failure: skip the chunk, terminate once."""
        async def failing(text, sentence_id, output_dir):
            raise RuntimeError("TTS failed")

        mock_services["tts"].synthesize_sentence = failing

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        types = [e["event"] for e in events]

        assert "error" in types
        assert _terminal_count(events) == 1, types

    def test_mid_stream_exception_terminates(self, client, mock_services):
        """An exception thrown mid-iteration yields error plus one terminal event."""
        mock_services["rag"].get_context_string.side_effect = RuntimeError("RAG exploded")

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        types = [e["event"] for e in events]

        assert "error" in types, types
        assert "RAG exploded" in json.dumps(events), types
        assert _terminal_count(events) == 1, types

    def test_llm_stream_error_terminates(self, client, mock_services):
        """A provider that dies mid-generation still terminates the stream."""
        def exploding_stream(*args, **kwargs):
            def gen():
                yield "Partial answer."
                raise RuntimeError("provider connection reset")

            return (gen(), [])

        mock_services["llm"].generate_stream_with_context.side_effect = exploding_stream

        conversation_id = client.post("/api/conversation").json()["conversation_id"]
        events = _stream_events(client, conversation_id)
        types = [e["event"] for e in events]

        assert "error" in types, types
        assert _terminal_count(events) == 1, types

    def test_farewell_terminates_with_interview_end(self, client, mock_services):
        mock_services["stt"].transcribe.return_value = "Muchas gracias, eso es todo"
        conversation_id = client.post("/api/conversation").json()["conversation_id"]

        with patch("backend.main.report_service") as mock_report:
            mock_report.generate.return_value = None
            events = _stream_events(client, conversation_id)
        types = [e["event"] for e in events]

        assert _terminal_count(events) == 1, types
        assert types[-1] == "interview_end", types


# ─── Frontend: the extracted settler, behaviourally ───────────────────────


class TestTerminalSettlerBehaviour:
    """Run the Node test for createTurnSettler against the real app.js."""

    def test_node_terminal_state_suite_passes(self):
        """The JS terminal-state suite is wired into the Python run.

        Skipped, not failed, when Node is unavailable: the frontend is
        static assets with no build step, and its absence must not break the
        backend suite. The structural tests below still apply.
        """
        if shutil.which("node") is None:
            pytest.skip("node not available")

        result = subprocess.run(
            ["node", "--test", str(NODE_TEST)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            "node --test failed for tests/frontend/terminal_state.test.mjs\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_frontend_defines_the_settler(self):
        source = FRONTEND_APP.read_text(encoding="utf-8")
        assert "function createTurnSettler(" in source, (
            "app.js must expose createTurnSettler() as the single terminal-state "
            "owner, so the exactly-once contract is testable without a DOM"
        )


# ─── Frontend: wiring, verified structurally ──────────────────────────────


def _sse_dispatch_body() -> str:
    source = FRONTEND_APP.read_text(encoding="utf-8")
    start = source.index("async function processRecordingStream()")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError("unbalanced braces in processRecordingStream()")


def _strip_js_comments(source: str) -> str:
    """Drop // and /* */ comments.

    These tests assert on the absence of a call like ``stopInterview()``. The
    source legitimately *mentions* that call in explanatory comments, so a
    naive substring check would report a false failure.
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        two = source[i : i + 2]
        if two == "//":
            while i < n and source[i] != "\n":
                i += 1
        elif two == "/*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif source[i] in "\"'`":
            quote = source[i]
            out.append(source[i])
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\":
                    out.append(source[i])
                    i += 1
                if i < n:
                    out.append(source[i])
                    i += 1
            if i < n:
                out.append(source[i])
                i += 1
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def _js_branch(source: str, event_type: str) -> str:
    """The body of the ``type === "<event_type>"`` branch of the if/else chain.

    Brace-matched from the branch's own opening brace, so it works for the
    last branch in the chain and never bleeds into the following ``catch``.
    """
    anchor = source.index(f'type === "{event_type}"')
    brace = source.index("{", anchor)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[anchor : i + 1]
    raise AssertionError(f"unbalanced braces in the {event_type} branch")


class TestTerminalWiring:
    """Every terminal event must funnel through the one settler."""

    def test_done_settles_the_turn(self):
        branch = _js_branch(_sse_dispatch_body(), "done")
        assert 'settle("done")' in branch, (
            "the done branch must settle the turn through createTurnSettler"
        )

    def test_interview_end_settles_the_turn(self):
        branch = _js_branch(_sse_dispatch_body(), "interview_end")
        assert 'settle("interview_end")' in branch, (
            "the interview_end branch must settle the turn too, otherwise the "
            "farewell turn is persisted but never counted"
        )

    def test_error_settles_the_turn(self):
        branch = _js_branch(_sse_dispatch_body(), "error")
        assert 'settle("error")' in branch, (
            "the error branch must settle the turn so the mic can restart"
        )

    def test_stream_eof_settles_the_turn(self):
        """EOF is a terminal signal: a truncated stream must still be clean."""
        body = _sse_dispatch_body()
        assert 'settle("eof")' in body, (
            "the stream read loop must settle on EOF; a truncated stream "
            "otherwise leaves allChunksReceived false and the mic stranded"
        )

    def test_turn_is_created_per_stream(self):
        body = _sse_dispatch_body()
        assert "createTurnSettler(" in body, (
            "processRecordingStream must create one settler per turn, so state "
            "never leaks between turns"
        )

    def test_settler_owns_the_shared_terminal_bookkeeping(self):
        """The counter update and indicator cleanup must live in one place.

        Duplicating them per branch is how the counter drift in the farewell
        path happened in the first place. The effects belong in the injected
        onSettle hook at the single call site, not inside the branches and not
        inside the settler itself (which stays pure).
        """
        body = _sse_dispatch_body()
        call_site = body[body.index("createTurnSettler({") : body.index("try {")]

        for hook in (
            "updateTurnCount",
            "fetchContext",
            "removeAudioIndicator",
            "hideTyping",
            "allChunksReceived",
        ):
            assert hook in call_site, (
                f"{hook} must be driven once by the onSettle hook, not "
                "duplicated across individual event branches"
            )

        # And no individual branch re-implements them.
        for event_type in ("done", "interview_end"):
            branch = _strip_js_comments(_js_branch(body, event_type))
            for hook in ("updateTurnCount", "fetchContext"):
                assert hook not in branch, (
                    f"{hook}() is duplicated in the {event_type} branch; it "
                    "belongs in the onSettle hook only"
                )

    def test_error_branch_is_not_blocking(self):
        """An SSE error must surface and continue, not throw out of the loop.

        Throwing landed in the catch block, which called stopInterview() -- a
        single failed TTS sentence killed the candidate's interview.
        """
        branch = _strip_js_comments(_js_branch(_sse_dispatch_body(), "error"))

        assert "throw new Error(" not in branch, (
            "the error branch still throws out of the read loop, which kills "
            "the interview via the catch block's stopInterview()"
        )
        assert "stopInterview()" not in branch, (
            "an SSE error event must leave the UI usable, not end the interview"
        )
        assert "addMessage(" in branch, (
            "the error branch must surface a visible message to the user"
        )

    def test_error_branch_clears_thinking_and_audio_indicator(self):
        body = _sse_dispatch_body()
        branch = _js_branch(body, "error")
        settler_hooks = _strip_js_comments(
            body[body.index("createTurnSettler({") : body.index("try {")]
        )

        # Either the branch clears them directly, or the settler hook does.
        for hook in ("hideTyping()", "removeAudioIndicator()"):
            assert hook in branch or hook in settler_hooks, (
                f"the error path must clear {hook}, or the UI looks stuck even "
                "after the error is reported"
            )

    def test_recoverable_chunk_error_does_not_settle(self):
        """A per-chunk TTS error is recoverable and must not end the turn.

        The stream continues and a later ``done`` is still expected; settling
        here would tear the turn down before the remaining audio plays.
        """
        branch = _strip_js_comments(_js_branch(_sse_dispatch_body(), "error"))

        assert "skippedChunkIds.add(" in branch, (
            "the per-chunk recoverable path must be preserved"
        )
        assert "advancePastSkippedChunks()" in branch
        # Exactly one settle call in the branch, and it is on the fatal path
        # (the recoverable path returns before reaching it).
        assert branch.count("turn.settle(") == 1, (
            "the error branch must settle exactly once, on the fatal path only"
        )

    def test_frontend_keeps_handling_every_backend_event(self):
        """The Item A contract must still hold after the Item C rewrite."""
        from tests.test_sse_contract import emitted_event_types, handled_event_types

        assert emitted_event_types() == handled_event_types()
