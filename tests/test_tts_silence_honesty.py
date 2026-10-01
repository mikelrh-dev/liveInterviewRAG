"""A turn nobody heard must not be filed, counted, or reported as delivered.

THE DEFECT THIS GUARDS
----------------------
``backend/turns/errors.py`` documented ``TTS_CHUNK_FAILED`` as "One sentence
could not be spoken; **the rest of the answer still plays**". That is true, and
it is the whole message, so every failure below it inherited the promise. The
frontend dispatcher (``frontend/app.js``) treats ANY ``error`` event carrying a
numeric ``id`` as a recoverable ``chunkSkipped``: it skips the chunk, says a
fragment was omitted, and waits for more audio. So when the provider failed on
the *first* sentence, the measured stream was::

    error{id:0}  error{id:1}  done{n:0, has_context:true}

...with 1 turn persisted and **0 audio files emitted**. The candidate read a
complete answer on screen, heard absolute silence, and watched the counter go
green. The recoverable-chunk contract is only honest while audio is still
coming; at the last chunk it is the opposite of the truth.

The blocking route never had this shape: it answers 503 and stores nothing.

WHAT IS ASSERTED HERE
---------------------
* Every sentence failing is a failed TURN: nothing persisted, a fatal
  (non-recoverable) error, and a terminal event so the mic comes back.
* Some sentences sounding is not a failed turn, but it is not a WHOLE one: the
  turn is stored and marked ``incomplete``, on the message that reaches the store
  and on the terminal event, because the text on disk is every sentence the model
  produced and the candidate heard a fraction of them.
* Every sentence sounding is a whole turn, marked nothing: a mark that lands on
  finished answers stops meaning anything.
* The note and the label that render the mark name both of its causes, since the
  mark no longer means only "the model stopped generating".
* The docstring on ``TTS_CHUNK_FAILED`` no longer states something false.

The zero-dispatch case is deliberately NOT in the failure branch. If the model
produced nothing speakable, synthesis was never asked for anything, so there is
no provider failure to report and the text answer is real: it is filed, and the
transcript and the report stay correct. Only "TTS was asked and delivered
nothing" is a failure.
"""

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import stub_rag_context_shapes
from backend import conversation
from backend.conversation import conversations
from backend.services.report import ReportService
from backend.turns import streaming
from backend.turns.errors import TTS_FAILED

ERRORS_PY = Path(__file__).resolve().parents[1] / "backend" / "turns" / "errors.py"


def _register(cid: str) -> str:
    conversations[cid] = {
        "id": cid, "messages": [], "turns": [], "summary": "",
        "created_at": "", "last_activity_at": "",
    }
    return cid


class _TTS:
    """Synthesis that succeeds for the given sentence ids and fails for the rest.

    Records the sentence ids it was asked for, so a test can prove the failure it
    triggered is the one it set up rather than a coincidence.
    """

    def __init__(self, failing: set[int] | None = None, hold: float = 0.0):
        self.failing = set(failing or ())
        self.hold = hold
        self.asked: list[int] = []

    async def sentence(self, text, sentence_id=0, output_dir=None, **kwargs):
        self.asked.append(sentence_id)
        if self.hold:
            await asyncio.sleep(self.hold)
        if sentence_id in self.failing:
            raise RuntimeError("provider 503")
        return sentence_id, Path(f"sentence_{sentence_id}.mp3")

    async def whole(self, text, output_path=None, **kwargs):
        return output_path


def _container(question, *, tokens, tts):
    stt = MagicMock()
    stt.transcribe.return_value = question

    llm = MagicMock()

    def stream_with_context(*args, **kwargs):
        def generate():
            for token in tokens:
                yield token
        return (generate(), [])

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


class _Turn:
    """What one drained turn produced: the events, the store, and memory.

    Memory is snapshotted before the conversation is dropped, because the
    assertion is about what the turn left behind and the fixture is torn down
    immediately afterwards.
    """

    def __init__(self, events, container, memory, messages):
        self.events = events
        self.container = container
        self.memory = memory
        self.messages = messages

    @property
    def store(self) -> MagicMock:
        return self.container.persistence.return_value

    def of(self, name: str) -> list[str]:
        return [e for e in self.events if f'"event": "{name}"' in e]

    @property
    def done(self) -> dict:
        """The payload of the single terminal event."""
        frames = self.of("done")
        assert len(frames) == 1, f"expected one terminal event, got {frames}"
        return json.loads(frames[0].split("data: ", 1)[1].strip())["data"]

    @property
    def stored_message(self) -> dict:
        """The ``messages`` row the pipeline handed to the store.

        The store is a double here, so this is the boundary under test: what
        ``record_turn`` received is exactly what persistence writes into the
        ``messages`` table, which
        ``tests/test_truncated_turn_honesty.py::TestTheTruncatedTurnIsKept``
        then proves against a real SQLite file.
        """
        assert self.store.record_turn.called, (
            f"precondition: a turn reached the store. calls: "
            f"{self.store.record_turn.call_args_list}"
        )
        return self.store.record_turn.call_args[0][2]

    @property
    def errors(self) -> list[dict]:
        """The ``data`` object of every ``error`` event, in order.

        An SSE frame is ``data: {"event": ..., "data": {...}}``, so the payload
        under test is one level down: ``{"detail": ..., "id": ...}``.
        """
        return [
            json.loads(raw.split("data: ", 1)[1].strip())["data"]
            for raw in self.of("error")
        ]


def _run(question, tokens, tts, tmp_path, cid) -> _Turn:
    container = _container(question, tokens=tokens, tts=tts)

    async def go():
        # Both modules resolve the composition root for themselves:
        # `streaming` reads it for the services, `conversation` for the
        # write-through. Patching one leaves the other on the real services,
        # which is how a "was the turn stored?" assertion silently passes.
        with patch.object(streaming, "container", container), patch.object(
            conversation, "container", container
        ):
            return [
                event
                async for event in streaming.build_stream(cid, _temp_audio(tmp_path))
            ]

    events = asyncio.run(go())
    memory = list(conversations[cid].get("turns", []))
    messages = list(conversations[cid].get("messages", []))
    conversations.pop(cid, None)
    return _Turn(events, container, memory, messages)


# ─── Every sentence fails: the turn is a failure, not an answer ─────────────


class TestWhenNoSentenceCouldBeSpoken:
    def test_no_turn_is_persisted(self, tmp_path):
        tts = _TTS(failing={0, 1, 2})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("silent-turn-not-stored"),
        )

        assert not turn.store.record_turn.called, (
            "a turn nobody heard was written to the store: "
            f"{turn.store.record_turn.call_args_list}"
        )
        assert turn.memory == [], (
            f"a turn nobody heard was kept in memory: {turn.memory}"
        )

    def test_the_stream_says_so_fatally_not_as_a_skipped_chunk(self, tmp_path):
        """The error that ENDS the turn must carry no chunk id.

        A numeric `id` is the frontend's "keep going" signal: the dispatcher
        skips the chunk, announces a gap, and waits for more audio. The
        per-chunk errors emitted while synthesis was still in flight may keep
        it -- that was true when they happened. The last one decides what the
        page concludes about the turn, and with an id it concludes "wait".
        """
        tts = _TTS(failing={0, 1, 2})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("silent-turn-is-fatal"),
        )

        assert turn.errors, "the silent turn reported nothing at all"
        last = turn.errors[-1]
        assert "id" not in last, (
            "the turn ended on a recoverable per-chunk skip, so the page kept "
            f"waiting for audio that never came: {turn.errors}"
        )
        assert last["detail"] == TTS_FAILED, (
            "the turn failed without saying the answer could not be spoken: "
            f"{[e['detail'] for e in turn.errors]}"
        )

    def test_the_fatal_error_comes_after_the_per_chunk_ones(self, tmp_path):
        """Order is the contract: report each gap, then end the turn."""
        tts = _TTS(failing={0, 1, 2})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("silent-turn-order"),
        )

        fatal = [
            i for i, e in enumerate(turn.errors) if "id" not in e
        ]
        assert fatal == [len(turn.errors) - 1], (
            "the fatal error must be the last thing said, not the first: "
            f"{turn.errors}"
        )

    def test_the_terminal_event_names_no_turn(self, tmp_path):
        """`{}` is how the client learns not to count a turn that is not there."""
        tts = _TTS(failing={0, 1})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. "],
            tts,
            tmp_path,
            _register("silent-turn-terminal"),
        )

        done = turn.of("done")
        assert len(done) == 1, f"expected exactly one terminal event, got {done}"
        assert '"n"' not in done[0], (
            "the silent turn still named a turn, so the counter advanced over "
            f"an exchange nobody heard: {done[0]}"
        )

    def test_the_answer_text_is_not_withheld_from_the_candidate(self, tmp_path):
        """The failure is honest about the AUDIO, not about the text.

        The tokens were really streamed; the candidate can read the answer. What
        must not happen is the opposite lie -- pretending they heard it.
        """
        tts = _TTS(failing={0, 1})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. "],
            tts,
            tmp_path,
            _register("silent-turn-keeps-text"),
        )

        assert turn.of("token"), "the streamed answer was taken back off the screen"


# ─── Some sentences fail: the turn survives, marked ──────────────────────────
#
# (The heading here used to read "unchanged, and deliberately so". It is not: the
# STORAGE is unchanged and the honesty mark is not, and the rule those three
# points now apply to is pinned together further down.)


class TestWhenSomeSentenceSounded:
    def test_the_turn_is_persisted(self, tmp_path):
        tts = _TTS(failing={1})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("partial-turn-stored"),
        )

        assert turn.store.record_turn.called, (
            "a turn the candidate heard most of was thrown away"
        )
        assert len(turn.memory) == 1, (
            f"a turn the candidate heard most of was not kept in memory: "
            f"{turn.memory}"
        )

    def test_the_audible_chunks_still_reach_the_candidate(self, tmp_path):
        tts = _TTS(failing={1})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("partial-turn-audio"),
        )

        assert len(turn.of("audio_url")) == 2, (
            f"expected the 2 synthesised sentences to be announced, got "
            f"{len(turn.of('audio_url'))}"
        )

    def test_the_skipped_chunk_is_still_reported_as_recoverable(self, tmp_path):
        """The recoverable contract stays recoverable while audio is coming."""
        tts = _TTS(failing={1})
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. ", "Tres. "],
            tts,
            tmp_path,
            _register("partial-turn-recoverable"),
        )

        assert turn.errors, "the skipped chunk was not reported"
        assert any(e.get("id") == 1 for e in turn.errors), (
            f"the per-chunk failure lost its id, so the page cannot skip it: "
            f"{turn.errors}"
        )

    def test_a_fully_healthy_turn_is_unaffected(self, tmp_path):
        tts = _TTS()
        turn = _run(
            "hablame de algo raro",
            ["Uno. ", "Dos. "],
            tts,
            tmp_path,
            _register("healthy-turn"),
        )

        assert turn.store.record_turn.called
        assert not turn.errors, f"a healthy turn reported errors: {turn.errors}"


class TestNothingWasEverAskedToBeSpoken:
    """Not a TTS failure: the model produced nothing speakable.

    Filing the text is correct here and the test exists to stop the failure
    branch from growing to swallow it -- an answer the candidate can read is a
    real exchange, and there is no provider failure to report.
    """

    def test_the_turn_is_still_persisted(self, tmp_path):
        tts = _TTS()
        turn = _run(
            "hablame de algo raro",
            ["   ", " *** "],
            tts,
            tmp_path,
            _register("unspeakable-turn"),
        )

        assert tts.asked == [], (
            f"synthesis was asked for {tts.asked}; this test needs an answer "
            "with no speakable sentence in it"
        )
        assert turn.store.record_turn.called, (
            "an answer with nothing speakable in it was dropped instead of filed"
        )


# ─── One rule, three points on the same line ─────────────────────────────────
#
# THE DEFECT
# ----------
# Two of the three answers this rule has to give were already right, and the
# middle one was filed as though it did not need an answer at all. Driving the
# real pipeline with synthesis failing on sentences 1 and 3 of 5 measured::
#
#     token x5   error{id:1}   audio_url x3   error{id:3}   done
#     done : {'n': 0, 'has_context': False}
#     turns persisted: 1   audio announced: 3   incomplete flag: absent
#
# So the transcript and the report cite all five sentences with nothing on them
# saying three of them were ever spoken, and the recruiter reads the answer the
# model produced rather than the 60 % the candidate actually heard. The page
# knows better -- the per-chunk ``error`` it received is the "a fragment was
# omitted" notice -- but that notice has never reached the disk.
#
# It is the same defect commit `1f4b0a2` was born to kill, one level up: the
# extreme case (nothing audible) was made honest and the partial case was left
# presenting half a turn as the whole one.
#
# WHY ALL THREE LIVE HERE TOGETHER
# The condition is a single comparison, so it is one rule with three outcomes,
# and each outcome is a value the same variable takes. Pinned apart, the middle
# one can be "fixed" by a special case that quietly breaks an end of the line.


#: Five speakable sentences, so the counts read as counts.
FIVE_SENTENCES = ["Uno. ", "Dos. ", "Tres. ", "Cuatro. ", "Cinco. "]


class TestHowMuchOfTheAnswerWasActuallySpoken:
    """Asked, delivered, filed: the same rule at its three possible answers."""

    @staticmethod
    def _turn(failing: set[int], cid: str, tmp_path) -> _Turn:
        return _run(
            "hablame de algo raro",
            list(FIVE_SENTENCES),
            _TTS(failing=failing),
            tmp_path,
            _register(cid),
        )

    def test_none_of_it_spoken_is_not_filed(self, tmp_path):
        """The end of the line the previous commit got right. Pinned so that
        fixing the middle cannot reach it."""
        turn = self._turn({0, 1, 2, 3, 4}, "spoken-none", tmp_path)

        assert turn.of("audio_url") == [], (
            f"precondition: nothing was announced, got {turn.of('audio_url')}"
        )
        assert not turn.store.record_turn.called, (
            "an answer nobody heard a word of was filed: "
            f"{turn.store.record_turn.call_args_list}"
        )
        assert turn.memory == [] and turn.messages == []
        assert turn.done == {}, (
            f"`done` named a turn that was never stored: {turn.done}"
        )

    def test_part_of_it_spoken_is_filed_and_marked(self, tmp_path):
        """The hole: 3 of 5 audible, filed as a whole answer."""
        turn = self._turn({1, 3}, "spoken-two-of-five", tmp_path)

        assert len(turn.of("audio_url")) == 3, (
            f"precondition: three sentences sounded, got "
            f"{len(turn.of('audio_url'))}"
        )
        assert turn.store.record_turn.called, (
            "an answer the candidate heard most of was thrown away: the mark is "
            "not a licence to lose the turn"
        )
        assert turn.stored_message["incomplete"] is True, (
            "the turn reached the store with no honesty mark, so the transcript "
            "and the report cite five sentences for a candidate who heard three: "
            f"{turn.stored_message}"
        )
        assert turn.messages[0]["incomplete"] is True, (
            f"the mark did not survive into memory: {turn.messages}"
        )
        assert turn.done.get("incomplete") is True, (
            "the terminal event does not carry the mark, so the page cannot label "
            f"the answer beside it: {turn.done}"
        )
        assert turn.done.get("n") is not None, (
            f"the turn was stored but the terminal event names none: {turn.done}"
        )

    def test_all_of_it_spoken_is_filed_whole(self, tmp_path):
        """The other end. A mark that appears on a finished answer stops meaning
        anything, which is worse than never having had one."""
        turn = self._turn(set(), "spoken-all", tmp_path)

        assert len(turn.of("audio_url")) == 5, (
            f"precondition: every sentence sounded, got "
            f"{len(turn.of('audio_url'))}"
        )
        assert turn.store.record_turn.called
        assert not turn.stored_message["incomplete"], (
            "an answer that was generated in full and spoken in full is filed as "
            f"cut short: {turn.stored_message}"
        )
        assert not turn.messages[0]["incomplete"]
        assert "incomplete" not in turn.done, (
            f"`done` grew a spurious incompleteness flag: {turn.done}"
        )

    def test_the_gap_is_still_reported_to_the_candidate_as_it_happens(self, tmp_path):
        """The mark lands on disk; the live notice is not replaced by it.

        Both are true at once and neither says the other's half: the per-chunk
        error is the only thing that can name WHICH sentence was lost, and it is
        on the wire before the turn ends. Dropping it in favour of a flag at the
        end would tell the candidate their answer was cut short without saying
        where the hole was.
        """
        turn = self._turn({1, 3}, "spoken-two-of-five-notices", tmp_path)

        assert [e.get("id") for e in turn.errors if "id" in e] == [1, 3], (
            "the gaps are no longer named while they happen: "
            f"{[e.get('id') for e in turn.errors]}"
        )


class TestTheIncompleteNoteNamesBothCauses:
    """``incomplete`` stopped meaning "the model stopped generating".

    The mark used to have exactly one cause, and both renderers spent that fact
    in prose: the report note said the model stopped half way through, and the
    transcript label said the same. An answer that was generated IN FULL and only
    partly spoken now carries the mark too, so a note that names generation as
    the cause is false on the exact turn the mark exists to describe -- and it is
    false in the artefact a recruiter reads.
    """

    @staticmethod
    def _note(tmp_path, incomplete: bool) -> str:
        path = ReportService(tmp_path / "reports").generate(
            "note-cid",
            {
                "created_at": "2026-10-01T10:00:00",
                "last_activity_at": "2026-10-01T10:01:00",
                "messages": [
                    {
                        "user_text": "q",
                        "response_text": "Uno. Dos. Tres. Cuatro. Cinco.",
                        "audio_url": "",
                        "incomplete": incomplete,
                    }
                ],
            },
        )
        return path.read_text(encoding="utf-8")

    def test_the_report_note_names_the_audio_loss_too(self, tmp_path):
        content = self._note(tmp_path, incomplete=True)

        assert re.search(r"audio|escuch", content, re.I), (
            "the report tells the recruiter the model stopped generating, on a "
            "turn where it generated every sentence and only the AUDIO failed:\n"
            + content
        )
        assert "dejó de generar" in content, (
            "the note lost the cause it was written for, so a truncated "
            f"generation is no longer explained:\n{content}"
        )

    def test_a_finished_answer_is_not_given_the_note(self, tmp_path):
        """Otherwise the note is on every turn and says nothing."""
        content = self._note(tmp_path, incomplete=False)

        assert "incompleta" not in content.lower(), (
            f"a whole answer was labelled incomplete in the report:\n{content}"
        )


# ─── The docstring must not state something false ──────────────────────────


def _comment_block_above(name: str) -> str:
    """The comment block immediately above the ``name = ...`` line."""
    source = ERRORS_PY.read_text(encoding="utf-8")
    lines = source.splitlines()
    at = next(
        i for i, line in enumerate(lines) if line.startswith(f"{name} =")
    )
    above = []
    for line in reversed(lines[:at]):
        if not line.strip().startswith("#"):
            break
        above.append(line)
    return "\n".join(reversed(above))


class TestTheErrorDocstringIsTrue:
    def test_it_does_not_promise_the_rest_of_the_answer_plays(self):
        """The claim is false exactly when no audio was produced."""
        block = _comment_block_above("TTS_CHUNK_FAILED")
        unconditional = re.search(
            r"the rest of the answer still plays(?!.*(?:but|unless|when))",
            block,
        )
        assert not unconditional, (
            "TTS_CHUNK_FAILED's comment still states, without a condition, that "
            f"the rest of the answer plays. It does not, when the provider "
            f"fails on the first sentence:\n{block}"
        )

    def test_it_describes_both_halves(self):
        """One half is the recoverable chunk, the other is a dead turn."""
        block = _comment_block_above("TTS_CHUNK_FAILED").lower()
        assert "recoverable" in block or "skip" in block, (
            f"the comment no longer says this is the recoverable case: {block}"
        )
        assert "no audio" in block or "nothing" in block or "silent" in block, (
            "the comment does not say what happens when TTS fails on EVERY "
            f"sentence, which is the case it used to get wrong: {block}"
        )

    def test_it_names_the_fatal_message_the_turn_now_emits(self):
        """So a reader can follow the emitted detail to its documentation."""
        block = _comment_block_above("TTS_CHUNK_FAILED")
        assert "TTS_FAILED" in block, (
            f"the comment does not point at the fatal message a totally silent "
            f"turn emits instead: {block}"
        )


@pytest.mark.parametrize("name", ["TTS_CHUNK_FAILED", "TTS_FAILED"])
def test_the_two_tts_messages_are_not_interchangeable(name):
    """They are different contracts; a docstring must not blur them."""
    source = ERRORS_PY.read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if l.startswith(f"{name} ="))
    assert "id" not in line, (
        f"{name} must not carry a chunk id: a numeric id is the frontend's "
        f"recoverable signal"
    )
