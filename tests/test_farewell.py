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


#: A question that trips neither the farewell detector nor the FAQ cache.
ORDINARY_QUESTION = "How do you architect a system?"


class TestFarewellTurnIsCounted:
    """The farewell turn reaches disk *and* the sidebar.

    The write happens after ``interview_end`` on purpose, so the goodbye is
    never queued behind a slow disk. The cost of that ordering is that
    ``interview_end`` is yielded before anything knows the turn number: the
    number the DB commits is the *return value* of ``persist_turn``, and it
    does not exist yet. The settler therefore reads ``turnState.last()`` while
    it is still ``null``, the counter never advances, and the interview ends
    one turn short of what was stored.

    Emitting the number inside ``interview_end`` cannot fix that, because at
    that moment there is no number -- only the pipeline's *request*, derived
    from memory, which ``record_turn`` is free to override when the requested
    slot is taken. So the number travels in a second, non-terminal event
    emitted once the write returns.

    The invariant these tests pin is the one the sidebar actually performs:
    the reported ``n`` plus one equals the number of stored turns.
    """

    @pytest.fixture
    def stored(self, tmp_path, monkeypatch):
        """A throwaway store *and* a throwaway audio directory.

        Both targets are redirected because the code under test writes to both:
        without this the counts below would be measured against rows appended
        to the real ``data/interviewtts.db`` by one run and inherited by the
        next, and the synthesised mp3s would land in the real ``audio/``.
        """
        import backend.main as main_mod
        from backend.config import config
        from backend.services.persistence import PersistenceService

        svc = PersistenceService(tmp_path / "farewell.db")
        svc.initialize()
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()  # uploads.stage_upload writes here, so it must exist
        monkeypatch.setattr(main_mod, "persistence", svc)
        monkeypatch.setattr(config, "AUDIO_DIR", audio_dir)
        return svc

    @pytest.fixture
    def tts(self, mock_services):
        """Per-sentence synthesis, which the shared double does not cover.

        ``mock_services`` only mocks the single-file ``synthesize``. An
        ordinary streamed turn calls ``synthesize_sentence`` once per
        sentence, and an unstubbed ``MagicMock`` there is not awaitable -- the
        turn dies at its first sentence, reports a TTS failure and stores
        nothing. That is the right behaviour for the code under test and the
        wrong fixture for these tests, so it is filled in here rather than
        changed for everybody.
        """

        async def fake_sentence(text, sentence_id, output_dir):
            path = Path(output_dir) / f"{sentence_id}.mp3"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            return sentence_id, path

        mock_services["tts"].synthesize_sentence = fake_sentence
        return mock_services

    @pytest.fixture
    def no_report(self):
        """Keep report generation out of the real reports/ directory."""
        with patch("backend.main.report_service") as mock_report:
            mock_report.generate.return_value = None
            yield mock_report

    def _stored_numbers(self, svc, cid):
        hydrated = svc.load_conversation(cid)
        assert hydrated is not None, "the conversation never reached the store"
        return [t["n"] for t in hydrated["turns"]]

    def _payload(self, events, event_type):
        matching = [e for e in events if e["event"] == event_type]
        assert len(matching) == 1, (
            f"expected exactly one {event_type!r}, got {[e['event'] for e in events]}"
        )
        return matching[0]["data"]

    def _ordinary_turn(self, client, services, cid, question=ORDINARY_QUESTION):
        """Stream one non-farewell turn and return its ``done`` payload."""
        services["stt"].transcribe.return_value = question
        return self._payload(_stream(client, cid), "done")

    def _farewell_turn(self, client, services, cid):
        """Stream the farewell, and return its events.

        The transcription is reset rather than inherited: the shared
        ``mock_services`` fixture only answers with the farewell phrase, and
        the ordinary-turn helper overwrites it, so a bare ``_stream`` here
        would silently run a third ordinary turn and pass for the real thing.
        """
        services["stt"].transcribe.return_value = FAREWELL_INPUT
        return _stream(client, cid)

    def test_farewell_reports_the_turn_it_persisted(
        self, client, tts, stored, no_report
    ):
        cid = client.post("/api/conversation").json()["conversation_id"]

        events = _stream(client, cid)
        types = _types(events)
        reported = self._payload(events, "turn_recorded")["n"]
        numbers = self._stored_numbers(stored, cid)

        assert numbers == [0], f"stored turns: {numbers}"
        assert reported == numbers[-1], (
            f"the wire reported turn {reported} but the store committed "
            f"{numbers[-1]}"
        )
        assert reported + 1 == len(numbers), (
            f"the sidebar would show {reported + 1} turns and the store holds "
            f"{len(numbers)}"
        )
        assert types.index("interview_end") < types.index("turn_recorded"), (
            "the committed number must be reported after the terminal event; "
            "reporting it earlier would mean reporting a number that does not "
            "exist yet, and would put the goodbye behind the disk write"
        )

    def test_the_count_is_right_after_several_turns(
        self, client, tts, stored, no_report
    ):
        """Two ordinary turns then a farewell: three stored, three counted."""
        cid = client.post("/api/conversation").json()["conversation_id"]

        first = self._ordinary_turn(client, tts, cid)
        second = self._ordinary_turn(client, tts, cid, "And your testing?")
        farewell = self._payload(self._farewell_turn(client, tts, cid), "turn_recorded")
        numbers = self._stored_numbers(stored, cid)

        assert numbers == [0, 1, 2], f"stored turns: {numbers}"
        # The farewell continues the sequence `done` established, rather than
        # restarting or skipping: the two events must not disagree.
        assert farewell["n"] == second["n"] + 1, (
            f"the farewell reported {farewell['n']} after done reported "
            f"{second['n']}"
        )
        # One payload language for both events, so the client needs one reader.
        assert set(farewell) == set(first) == {"n", "has_context"}, (
            f"payload shapes drifted: done={set(first)} "
            f"turn_recorded={set(farewell)}"
        )
        assert farewell["n"] + 1 == len(numbers)

    def test_an_interview_without_a_farewell_is_untouched(
        self, client, tts, stored, no_report
    ):
        """The ordinary path keeps naming its turn with ``done`` alone.

        A normal turn is settled by ``done``, so its number is already in
        ``turnState`` when the settler runs. Emitting the post-write event
        there too would count the same turn a second time.
        """
        cid = client.post("/api/conversation").json()["conversation_id"]

        first = self._ordinary_turn(client, tts, cid)
        tts["stt"].transcribe.return_value = "And your testing?"
        second_events = _stream(client, cid)
        second = self._payload(second_events, "done")
        numbers = self._stored_numbers(stored, cid)

        assert "turn_recorded" not in _types(second_events), (
            f"a normal turn must be named by done alone, got {_types(second_events)}"
        )
        assert first["n"] == 0 and second["n"] == 1, (first, second)
        assert numbers == [0, 1], f"stored turns: {numbers}"
        assert second["n"] + 1 == len(numbers), (
            f"the sidebar would show {second['n'] + 1} of {len(numbers)} stored turns"
        )

    def test_a_failed_farewell_write_reports_no_turn(
        self, client, tts, stored, no_report
    ):
        """A turn that did not land must not be counted.

        ``persist_turn`` returns ``None`` for a write that failed, and the
        payload builder turns that into ``{}``. The client reads a missing
        ``n`` as "nothing to count" -- the same rule ``done`` follows.
        Inventing a number here would put the counter one ahead of the store,
        which is the same class of bug this whole change is about.
        """
        stored.record_turn = lambda *a, **k: None

        cid = client.post("/api/conversation").json()["conversation_id"]
        events = _stream(client, cid)
        reported = self._payload(events, "turn_recorded")

        assert reported == {}, (
            f"a write that failed must report an empty payload, got {reported}"
        )
        assert self._stored_numbers(stored, cid) == [], (
            "nothing was stored, so nothing may be counted"
        )

    def test_a_hydrated_conversation_counts_what_it_stored(
        self, client, tts, stored, no_report
    ):
        """Reading the conversation back must land on the same count.

        The live sidebar is only half the surface: a restart rebuilds memory
        from these rows, so a turn that is stored but uncounted there shows up
        as a transcript that disagrees with the number beside it.
        """
        import backend.main as main_mod

        cid = client.post("/api/conversation").json()["conversation_id"]
        self._ordinary_turn(client, tts, cid)
        farewell = self._payload(self._farewell_turn(client, tts, cid), "turn_recorded")

        main_mod.conversations.clear()  # a process restart, from the store's side
        hydrated = stored.load_conversation(cid)
        numbers = [t["n"] for t in hydrated["turns"]]

        assert numbers == [0, 1], f"hydrated turns: {numbers}"
        assert farewell["n"] + 1 == len(numbers), (
            f"the reported number would show {farewell['n'] + 1} turns but "
            f"hydration rebuilds {len(numbers)}"
        )
        assert hydrated["turns"][-1]["user_text"] == FAREWELL_INPUT, (
            "the farewell is missing from the hydrated history"
        )
        assert len(hydrated["messages"]) == len(numbers), (
            "transcript and turn numbering disagree after hydration"
        )

    def test_the_report_is_built_from_every_stored_turn(
        self, client, tts, stored, no_report
    ):
        """The post-write report must see the farewell, and see it counted.

        The report is generated after ``persist_turn`` precisely so that it
        describes what survived the write. Handing it a conversation whose turn
        list lagged the store would make the artifact and the sidebar agree on
        a number the database does not hold.
        """
        from backend.main import conversations

        cid = client.post("/api/conversation").json()["conversation_id"]
        self._ordinary_turn(client, tts, cid)
        self._farewell_turn(client, tts, cid)

        assert no_report.generate.call_args[0][0] == cid
        assert len(conversations[cid]["turns"]) == 2, (
            f"the report was handed {len(conversations[cid]['turns'])} turns"
        )


class TestFarewellTurnIsCountedInTheFrontend:
    """The client half of the same defect.

    Node cannot drive the SSE dispatcher -- it is a DOM-bound browser script --
    so the counting rule is asserted two ways instead: behaviourally on the
    extracted state object (tests/frontend/turn_state.test.mjs) and
    structurally on the branch that has to apply it. The structural half is a
    text check and is labelled as one.
    """

    def test_the_dispatcher_handles_the_post_write_event(self):
        from tests.test_sse_contract import handled_event_types

        assert "turn_recorded" in handled_event_types(), (
            "the frontend has no branch for the event carrying the farewell's "
            "turn number, so the count stays one short of the store"
        )

    def test_the_reporting_branch_publishes_the_committed_turn(self):
        """STRUCTURAL, not behavioural: a text check, and honestly so.

        The counter update *cannot* live in the settler on this path, and that
        is a consequence of the ordering rather than a tidiness problem:
        ``interview_end`` settles the turn, and at that point no number
        exists. So the branch has to apply the same arithmetic as the settler,
        and this test is what keeps the two from being edited apart.

        What it cannot prove is that the arithmetic is right -- that is the
        behavioural test above, and the Node suite.
        """
        from tests.test_sse_terminal_state import (
            _js_branch,
            _sse_dispatch_body,
            _strip_js_comments,
        )

        branch = _strip_js_comments(_js_branch(_sse_dispatch_body(), "turn_recorded"))

        assert "turnState.commit(" in branch, (
            "the branch must hand the committed turn to turnState; anything "
            "else is a second, private numbering rule"
        )
        assert "updateTurnCount(" in branch, (
            "the committed turn must reach the sidebar counter: the settler "
            "already ran on interview_end, when no number existed yet"
        )
        assert "turn.settle(" not in branch, (
            "the turn is already settled by interview_end; settling again is "
            "a second terminal signal for one turn"
        )

    def test_the_ordinary_path_keeps_a_single_counter_call_site(self):
        """The duplication above must not spread.

        A ``done`` that updated the counter in its own branch would give the
        two paths two places to drift -- which is precisely how the farewell
        count drifted in the first place.
        """
        from tests.test_sse_terminal_state import (
            _js_branch,
            _sse_dispatch_body,
            _strip_js_comments,
        )

        branch = _strip_js_comments(_js_branch(_sse_dispatch_body(), "done"))
        assert "updateTurnCount(" not in branch
        assert "fetchContext(" not in branch


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
