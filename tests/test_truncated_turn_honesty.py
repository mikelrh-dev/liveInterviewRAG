"""A turn the LLM abandoned is still a turn -- but it must say it was cut short.

THE DEFECT
----------
Driving the real pipeline with a stub provider that emits two sentences and then
raises produced this wire::

    transcription  {"text": "hableme de fraud detector"}
    token          {"text": "Tengo experiencia senior en deteccion de fraude. "}
    token          {"text": "Lideré un equipo de ocho personas. "}
    audio_url      {"id": 0, "url": "/audio/<cid>/chunk_0.mp3"}
    error          {"detail": "No se pudo generar la respuesta. Repite la pregunta…"}
    done           {}

Measured on that run:

* **Zero turns and zero messages persisted**, although the candidate had already
  heard ``chunk_0`` through the speaker. The whole exchange simply vanished.
* **Two mp3 files on disk.** ``chunk_0`` was announced and played; ``chunk_1``
  was written by a synthesis task the ``finally`` then cancelled, and nobody was
  ever told about it -- an orphan that sat in ``audio/`` until the hourly sweep.
* The truncated text stayed in the transcript forever, and read as a finished
  answer. That is the specific dishonesty this repository has been removing one
  commit at a time: a twin that answers with half a sentence and lets the
  recruiter believe it is the whole answer.
* ``done {}`` carried no ``n``, so ``turnState.commit({})`` returned ``null``
  and the turn counter did not advance either.

The temp upload *was* cleaned up, which is the one part of that run that was
already right.

WHAT IS ASSERTED HERE
---------------------
* The turn reaches disk, carrying the truncated answer AND the fact that it is
  truncated -- through the ``messages`` table, through ``load_conversation``, and
  into the Markdown report.
* Audio the TTS wrote but never announced is deleted. Audio the client was told
  about is not, because the candidate may still be listening to it.
* The terminal event names the committed turn on this branch too, so the
  counter advances.
* An LLM that dies before saying anything stores nothing -- there is no answer
  to keep, and a turn with an empty body would be a worse lie than a lost one.
* A healthy turn is untouched: nothing stored is marked incomplete, and no
  announced audio is deleted.
"""

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import stub_rag_context_shapes
from backend import conversation as conversation_module
from backend.conversation import conversations
from backend.services.persistence import PersistenceService
from backend.services.report import ReportService
from backend.turns import streaming

QUESTION = "hableme de un proyecto raro"


# ─── A provider that dies mid-answer, and a TTS that really writes ──────────


class _TTSProbe:
    """Writes real files, like the provider does, and remembers what it wrote.

    ``written`` is the ground truth for "the TTS produced a file here", because
    the point of the fix is that such a file is gone by the end of the turn --
    so counting files on disk afterwards would be counting the fix.
    """

    def __init__(self, *, hold_last: bool = True):
        #: Sentence ids whose synthesis is still streaming when the turn ends.
        #: The last one hangs on purpose: that is the task the ``finally``
        #: cancels AFTER the file has been written, which is exactly how an
        #: orphan appears in production.
        self.hold_last = hold_last
        self.written: list[int] = []
        self.paths: dict[int, Path] = {}
        self._condition = threading.Condition()

    def wait_for_writes(self, count: int, timeout: float = 10.0) -> None:
        """Block the provider thread until ``count`` files exist.

        Without this the interleaving is luck: the ``finally`` can cancel a
        synthesis before its first line runs, and then there is no orphan to
        have cleaned, so the deletion assertions would pass for the wrong
        reason. Bounded by ``timeout`` so a regression fails the test instead of
        hanging the suite.
        """
        with self._condition:
            self._condition.wait_for(lambda: len(self.written) >= count, timeout)

    def _record(self, sentence_id: int, path: Path) -> None:
        with self._condition:
            self.written.append(sentence_id)
            self.paths[sentence_id] = path
            self._condition.notify_all()

    async def sentence(self, text, sentence_id=0, output_dir=None, **kwargs):
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"sentence_{sentence_id}_{uuid.uuid4().hex}.mp3"
        path.write_bytes(b"ID3-fake-audio")
        self._record(sentence_id, path)
        if self.hold_last and sentence_id > 0:
            # Still streaming from the provider. The file exists; the answer
            # does not.
            await asyncio.sleep(30)
        return sentence_id, path

    async def whole(self, text, output_path=None, **kwargs):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ID3-fake-audio")
        return path


def _container(probe, *, tokens, dies, store, question=QUESTION):
    """The composition root, with a provider that streams and then dies.

    The thread that pulls tokens waits for each synthesis to have written its
    file before it moves on, and then sleeps before dying, so the event loop has
    every chance to announce the finished chunk first. That is not decoration:
    it is what makes the difference between "one file announced, one orphaned"
    and a race the assertions could pass on by accident.
    """
    stt = MagicMock()
    stt.transcribe.return_value = question

    def stream_with_context(*args, **kwargs):
        def generate():
            for index, token in enumerate(tokens):
                yield token
                probe.wait_for_writes(index + 1)
                time.sleep(0.02)
            if dies:
                time.sleep(0.05)
                raise RuntimeError("provider 503")
        return (generate(), [])

    llm = MagicMock()
    llm.generate_stream_with_context = stream_with_context

    rag = MagicMock()
    rag.get_chunks_with_scores.return_value = []
    stub_rag_context_shapes(rag)

    tts = MagicMock()
    tts.synthesize_sentence = probe.sentence
    tts.synthesize = probe.whole

    container = MagicMock()
    container.stt_service.return_value = stt
    container.llm_service.return_value = llm
    container.rag_pipeline.return_value = rag
    container.tts_service.return_value = tts
    container.persistence.return_value = store
    return container


def _register(cid):
    conversations[cid] = {
        "id": cid, "messages": [], "turns": [], "summary": "",
        "created_at": "2026-09-30T10:00:00", "last_activity_at": "",
    }
    return cid


def _events(raw: list[str]) -> list[dict]:
    parsed = []
    for frame in raw:
        for line in frame.splitlines():
            if line.startswith("data: "):
                parsed.append(json.loads(line[6:]))
    return parsed


def _of_type(events, name):
    return [e for e in events if e["event"] == name]


def _run_stream(container, cid, tmp_path, audio_dir):
    """Drain one whole stream against the isolated audio directory.

    ``backend.conversation`` holds its OWN reference to the composition root, and
    that is the one ``persist_turn`` and ``store_is_configured`` resolve through
    -- so patching only the pipeline's would leave the write going to the real
    singleton, and the assertions about what reached disk would be reading a
    different file from the one the pipeline wrote. Both are rebound together,
    to the same double.
    """

    async def run():
        with patch.object(streaming, "container", container), patch.object(
            conversation_module, "container", container
        ):
            audio = tmp_path / f"input-{cid}.wav"
            audio.write_bytes(b"RIFF")
            return [frame async for frame in streaming.build_stream(cid, audio)]

    return asyncio.run(run())


@pytest.fixture
def store(tmp_path, isolated_write_targets):
    """A real, initialised store: the assertions are about what reaches disk."""
    persistence = PersistenceService(tmp_path / "turns.db", enabled=True)
    persistence.initialize()
    return persistence


@pytest.fixture
def truncated_turn(isolated_write_targets, store, tmp_path):
    """One real turn in which the provider dies after two sentences."""
    cid = _register("truncated-turn")
    probe = _TTSProbe()
    try:
        raw = _run_stream(
            _container(probe, tokens=["Uno. ", "Dos. "], dies=True, store=store),
            cid,
            tmp_path,
            isolated_write_targets.audio,
        )
        yield {
            "cid": cid,
            "events": _events(raw),
            "probe": probe,
            "audio_dir": isolated_write_targets.audio / cid,
            "store": store,
        }
    finally:
        conversations.pop(cid, None)


# ─── 1. The turn is persisted, and marked as incomplete ────────────────────


class TestTheTruncatedTurnIsKept:
    def test_the_answer_the_candidate_heard_reaches_memory(self, truncated_turn):
        turns = conversations[truncated_turn["cid"]]["turns"]

        assert len(turns) == 1, (
            f"the exchange the candidate heard was dropped: {turns}"
        )
        assert turns[0]["assistant_text"] == "Uno. Dos. ", (
            "what the candidate actually heard is what has to be kept, verbatim "
            f"and not repaired: {turns[0]['assistant_text']!r}"
        )
        assert turns[0]["user_text"] == QUESTION

    def test_the_transcript_entry_is_marked_incomplete(self, truncated_turn):
        messages = conversations[truncated_turn["cid"]]["messages"]

        assert len(messages) == 1, f"no transcript entry was written: {messages}"
        assert messages[0]["incomplete"] is True, (
            "the exchange reached the transcript with nothing saying it was cut "
            "off, so every later reader takes a half-answer for the whole answer"
        )

    def test_the_mark_reaches_the_database(self, truncated_turn):
        con = sqlite3.connect(str(truncated_turn["store"].db_path))
        try:
            rows = con.execute(
                "SELECT response_text, incomplete FROM messages "
                "WHERE conversation_id = ?",
                (truncated_turn["cid"],),
            ).fetchall()
        finally:
            con.close()

        assert len(rows) == 1, f"the transcript row never landed: {rows}"
        assert rows[0][1] == 1, (
            "the row is on disk with the truncated answer and no mark, so the "
            "silence survives a restart"
        )

    def test_the_mark_survives_a_restart(self, truncated_turn):
        """The mark has to come BACK, not just go in.

        A hydrated conversation is what the next report is rendered from, so a
        flag that only ever lived in memory would be a flag that disappears at
        the worst moment.
        """
        reloaded = truncated_turn["store"].load_conversation(truncated_turn["cid"])

        assert reloaded is not None
        assert reloaded["messages"][0]["incomplete"] == 1, (
            "a reloaded conversation reports a truncated answer as a complete "
            f"one: {reloaded['messages'][0]}"
        )

    def test_the_report_shows_the_answer_as_incomplete(
        self, truncated_turn, tmp_path
    ):
        report = ReportService(tmp_path / "reports")
        path = report.generate(
            truncated_turn["cid"], conversations[truncated_turn["cid"]]
        )
        content = path.read_text(encoding="utf-8")

        assert "**Gemelo:** Uno. Dos." in content, (
            "the truncated answer is not even in the report:\n" + content
        )
        assert "incompleta" in content.lower(), (
            "the report presents a truncated answer as a finished one. That is "
            "the whole defect, restated in the artefact the recruiter reads:\n"
            + content
        )


class TestACompleteTurnIsNotMarked:
    def test_a_healthy_answer_carries_no_mark(self, isolated_write_targets, store, tmp_path):
        cid = _register("healthy-turn")
        probe = _TTSProbe(hold_last=False)
        try:
            events = _events(
                _run_stream(
                    _container(probe, tokens=["Uno. ", "Dos. "], dies=False, store=store),
                    cid,
                    tmp_path,
                    isolated_write_targets.audio,
                )
            )
            messages = conversations[cid]["messages"]
            done = _of_type(events, "done")[-1]["data"]
        finally:
            conversations.pop(cid, None)

        assert len(messages) == 1
        assert not messages[0].get("incomplete"), (
            "a turn that finished was recorded as cut short, so the honesty mark "
            "trains the reader to ignore it"
        )
        assert "incomplete" not in done, (
            f"`done` grew a spurious incompleteness flag: {done}"
        )


class TestNothingToKeepStoresNothing:
    def test_an_llm_that_dies_before_a_word_stores_no_turn(
        self, isolated_write_targets, store, tmp_path
    ):
        """An empty answer is not a turn worth keeping.

        The turn is not lost data here -- there is no answer in it. Storing one
        would put an empty turn in the report and count it in the sidebar, which
        is worse than the turn the candidate can already see never happened.
        """
        cid = _register("dead-before-first-token")
        probe = _TTSProbe()
        try:
            events = _events(
                _run_stream(
                    _container(probe, tokens=[], dies=True, store=store),
                    cid,
                    tmp_path,
                    isolated_write_targets.audio,
                )
            )
            turns = conversations[cid]["turns"]
            messages = conversations[cid]["messages"]
            done = _of_type(events, "done")[-1]["data"]
        finally:
            conversations.pop(cid, None)

        assert turns == [], f"an empty turn was committed: {turns}"
        assert messages == [], f"an empty transcript entry was committed: {messages}"
        assert done == {}, (
            f"`done` named a turn that was never stored: {done}. The client would "
            "count it and ask the Context panel about a turn that does not exist."
        )


# ─── 1b. Dies AFTER streaming text, but before anything could be spoken ─────
#
#
# THE DEFECT THIS EXTENDS
# -----------------------
# The case above is an LLM that dies before a WORD. The guard there is
# `if full_response.strip():`, so it holds. The gap is the provider that streams
# real text and THEN dies, before the first sentence terminator.
#
# `SentenceBuffer` (backend/services/llm.py:60) only emits a sentence on `.`,
# `!`, `?` or `\n`. So a provider that dies mid-clause has dispatched NOTHING:
# no synthesis was requested, no audio exists, and nothing can be spoken. The
# `error` handler skips `flush()` entirely -- the except arm at :513 jumps
# straight to the queue -- so the fragment sitting in the buffer is never
# rescued either.
#
# `full_response.strip()` is nevertheless truthy, so :571 stored the fragment.
# Measured, before the fix:
#
#     transcription, token, token, error, done
#     done : {'n': 0, 'has_context': False, 'incomplete': True}
#     turns persisted: 1   audio announced: 0   mp3 on disk: []
#     report: **Gemelo:** Tengo experiencia senior en deteccion de fraude y equipo
#             > **Respuesta incompleta.**
#
# Two readers are lied to. The candidate reads text they never heard, and the
# report files an exchange that did not happen -- with the honesty mark ON it,
# which is worse, because the mark is supposed to mean "this was cut short",
# not "this was never spoken".
#
# WHY THE POST-LOOP GUARD MISSED IT
# `dispatched_sentences and not announced_audio` (:670) is deliberately "asked
# and delivered nothing" rather than "no audio", because an answer with nothing
# speakable in it is a real exchange worth filing
# (tests/test_tts_silence_honesty.py::TestNothingWasEverAskedToBeSpoken pins
# that). Here synthesis was never ASKED, so the condition is false -- correctly,
# for its own case, and wrongly for this one. It also cannot help: it lives
# after the `return` at :589.


#: A provider that dies mid-clause: real text, no terminator, so no sentence.
DIE_BEFORE_ANY_TERMINATOR = [
    "Gemelo: Tengo experiencia senior en deteccion de fraude y equipo",
    " de deteccion de anomalias en transacciones",
]


class TestDiesAfterStreamingTextButBeforeAnythingSpoke:
    """The ghost turn: filed, read, and never spoken."""

    @pytest.fixture
    def ghost_turn(self, isolated_write_targets, store, tmp_path):
        """One real turn in which the provider dies before the first period."""
        cid = _register("died-before-first-terminator")
        probe = _TTSProbe(hold_last=False)
        try:
            raw = _run_stream(
                _container(
                    probe, tokens=DIE_BEFORE_ANY_TERMINATOR, dies=True, store=store
                ),
                cid,
                tmp_path,
                isolated_write_targets.audio,
            )
            yield {
                "cid": cid,
                "events": _events(raw),
                "probe": probe,
                "turns": list(conversations[cid]["turns"]),
                "messages": list(conversations[cid]["messages"]),
            }
        finally:
            conversations.pop(cid, None)

    def test_the_precondition_nothing_was_ever_synthesised(self, ghost_turn):
        """Guard first: the whole defect lives in this being empty.

        A test that does not establish it can pass on the healthy path, where
        the fragment IS filed, and prove nothing about the death path.
        """
        assert ghost_turn["probe"].written == [], (
            f"synthesis was called for {ghost_turn['probe'].written}; this case "
            "needs a provider that dies before the first terminator, so no "
            "sentence is ever dispatched"
        )
        assert _of_type(ghost_turn["events"], "audio_url") == [], (
            "no audio can be announced when nothing was synthesised"
        )

    def test_no_turn_is_persisted(self, ghost_turn):
        assert ghost_turn["turns"] == [], (
            f"a turn the candidate never heard was committed: {ghost_turn['turns']}\n"
            "The provider died mid-clause. Synthesis was never requested, so "
            "there is no audio and no exchange -- only a fragment of an answer "
            "the model never finished."
        )

    def test_nothing_is_written_to_the_transcript(self, ghost_turn):
        assert ghost_turn["messages"] == [], (
            f"a transcript entry was written for a turn that never happened: "
            f"{ghost_turn['messages']}"
        )

    def test_the_terminal_event_names_no_turn(self, ghost_turn):
        done = _of_type(ghost_turn["events"], "done")[-1]["data"]

        assert "n" not in done, (
            f"`done` counted a turn that was never stored: {done}. The client "
            "advances its counter and the Context panel is asked about a turn "
            "that does not exist."
        )
        assert done.get("incomplete") is not True, (
            f"the turn was filed as `incomplete`, which claims something WAS "
            f"spoken and cut short: {done}. Nothing was spoken at all. The mark "
            "has to mean one thing or it teaches its reader to ignore it."
        )

    def test_the_report_records_no_exchange(self, ghost_turn, tmp_path):
        report = ReportService(tmp_path / "reports")
        path = report.generate(
            ghost_turn["cid"], conversations[ghost_turn["cid"]]
        )

        # `generate` returns None when there is nothing to report, and a turn
        # that never happened is exactly that. Asserting the file exists would
        # assert the opposite of the fix: it would demand an empty transcript be
        # written for an exchange with no audio and no stored turn.
        assert path is None, (
            f"a report was written for an exchange that never happened: {path}\n"
            + path.read_text(encoding="utf-8")
        )

    def test_the_candidate_is_still_told_the_provider_failed(self, ghost_turn):
        """Dropping the turn must not turn a failure into a silent success.

        The turn is not stored and nothing is spoken, but the terminal pair is
        unchanged: an `error` then a `done`. A `done` alone would leave the page
        waiting for audio that will never arrive.
        """
        errors = _of_type(ghost_turn["events"], "error")

        assert errors, (
            "the provider failure was swallowed: the candidate is left with a "
            f"terminal event and no explanation. events: {ghost_turn['events']}"
        )
        assert _of_type(ghost_turn["events"], "done"), "precondition: a terminal event exists"


class TestDiesAfterSomethingWasSpoken:
    """The control. `incomplete: true` keeps exactly the meaning it had.

    Commit `1f4b0a2` documented the mark as "the answer was cut short" -- which
    is only true when the candidate HEARD something. This pins that half so the
    fix above cannot quietly take the whole thing with it.
    """

    @pytest.fixture
    def spoken_turn(self, isolated_write_targets, store, tmp_path):
        """One real turn in which a sentence sounded and then the provider died."""
        cid = _register("died-after-a-sentence-spoke")
        probe = _TTSProbe()
        try:
            raw = _run_stream(
                _container(probe, tokens=["Uno. ", "Dos y medio"], dies=True, store=store),
                cid,
                tmp_path,
                isolated_write_targets.audio,
            )
            yield {
                "cid": cid,
                "events": _events(raw),
                "probe": probe,
                "turns": list(conversations[cid]["turns"]),
                "messages": list(conversations[cid]["messages"]),
            }
        finally:
            conversations.pop(cid, None)

    def test_the_turn_is_still_persisted(self, spoken_turn):
        assert _of_type(spoken_turn["events"], "audio_url"), (
            "precondition: a sentence sounded, which is what earns the turn a "
            "place on disk"
        )
        assert len(spoken_turn["turns"]) == 1, (
            f"a turn the candidate actually heard was dropped: {spoken_turn['turns']}"
        )

    def test_it_is_still_marked_incomplete(self, spoken_turn):
        messages = spoken_turn["messages"]

        assert len(messages) == 1
        assert messages[0]["incomplete"] is True, (
            "the answer was cut off mid-sentence and nothing says so, so a "
            f"half-answer reads as a whole one: {messages}"
        )

    def test_the_terminal_event_still_names_it(self, spoken_turn):
        done = _of_type(spoken_turn["events"], "done")[-1]["data"]

        # Zero-based, like every other turn on the wire: `build_turn` derives
        # `n` from `len(conversations[cid]["turns"])`, so the first committed
        # turn of a fresh conversation is 0. `fetchContext` treats `n < 0` as
        # "no such turn" and 0 as real, so reporting the wrong number here
        # would 404 the panel for a turn that is on disk.
        assert done.get("n") == 0, f"`done` lost the committed turn: {done}"
        assert done.get("incomplete") is True, f"the mark was dropped: {done}"


# ─── 2. Orphan audio is deleted; announced audio is not ────────────────────


class TestOnlyUnannouncedAudioIsDeleted:
    def test_the_reproduction_writes_the_two_files_the_finding_describes(
        self, truncated_turn
    ):
        """Guard first: the defect's precondition, or nothing below proves
        anything. Two sentences synthesised, one announced."""
        probe = truncated_turn["probe"]
        announced = _of_type(truncated_turn["events"], "audio_url")

        assert sorted(probe.written) == [0, 1], (
            f"the turn only synthesised {probe.written}; the orphan this fixes is "
            "a file written for a sentence that was never announced"
        )
        assert len(announced) == 1, (
            f"{len(announced)} chunk(s) were announced; expected the first one"
        )

    def test_no_orphan_audio_survives_the_turn(self, truncated_turn):
        remaining = sorted(p.name for p in truncated_turn["audio_dir"].glob("*.mp3"))
        announced = {
            Path(e["data"]["url"]).name
            for e in _of_type(truncated_turn["events"], "audio_url")
        }
        orphan_written = truncated_turn["probe"].written[1]

        assert remaining == sorted(announced), (
            f"files left on disk: {remaining}. The turn synthesised "
            f"{sorted(truncated_turn['probe'].written)} and announced "
            f"{sorted(announced)}; sentence {orphan_written} was written and "
            "never announced, so it is an orphan that costs an hour of disk."
        )

    def test_the_announced_chunk_is_still_there_to_play(self, truncated_turn):
        announced = {
            Path(e["data"]["url"]).name
            for e in _of_type(truncated_turn["events"], "audio_url")
        }
        remaining = {p.name for p in truncated_turn["audio_dir"].glob("*.mp3")}

        assert announced, "no chunk was announced, so this proves nothing"
        assert announced <= remaining, (
            f"the sweep deleted audio the candidate was already listening to: "
            f"announced {sorted(announced)}, left {sorted(remaining)}"
        )

    def test_a_healthy_turn_keeps_every_announced_file(
        self, isolated_write_targets, store, tmp_path
    ):
        """The opposite direction, and the one that would hurt if it broke.

        Every chunk of a finished answer is announced and every one of them is
        in the queue or playing. A sweep that removed them would mute the
        interview rather than tidy up after it.
        """
        cid = _register("healthy-audio")
        probe = _TTSProbe(hold_last=False)
        try:
            events = _events(
                _run_stream(
                    _container(probe, tokens=["Uno. ", "Dos. "], dies=False, store=store),
                    cid,
                    tmp_path,
                    isolated_write_targets.audio,
                )
            )
            directory = isolated_write_targets.audio / cid
            remaining = sorted(p.name for p in directory.glob("*.mp3"))
            announced = sorted(
                Path(e["data"]["url"]).name
                for e in _of_type(events, "audio_url")
            )
        finally:
            conversations.pop(cid, None)

        assert announced == sorted(probe.paths[i].name for i in probe.written)
        assert remaining == announced, (
            f"a finished answer lost audio: announced {announced}, left {remaining}"
        )


# ─── 3. The terminal event names the turn on this branch too ───────────────


class TestTheTerminalEventStillNamesTheTurn:
    def test_done_carries_the_committed_number(self, truncated_turn):
        done = _of_type(truncated_turn["events"], "done")[-1]["data"]

        assert done.get("n") == 0, (
            f"`done` named no turn: {done}. The sidebar counter is driven by this "
            "number and nothing else, so the interview loses a turn it played."
        )

    def test_done_says_the_answer_was_cut_short(self, truncated_turn):
        done = _of_type(truncated_turn["events"], "done")[-1]["data"]

        assert done.get("incomplete") is True, (
            "the client cannot mark what it was never told about: " f"{done}"
        )

    def test_the_error_still_precedes_the_terminal_event(self, truncated_turn):
        types = [e["event"] for e in truncated_turn["events"]]

        assert types[-1] == "done", f"the stream does not end on `done`: {types}"
        assert types.index("error") < types.index("done"), (
            f"the error arrived after the terminal event: {types}"
        )


# ─── 4. The column, and the migration that adds it ─────────────────────────


#: A ``messages`` table exactly as v1 created it. Used to prove the column is
#: ADDED to a deployed file, not only declared for a fresh one.
MESSAGES_V1_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    user_text TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT '',
    audio_url TEXT NOT NULL DEFAULT ''
);
"""


def _v1_database(db_path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(MESSAGES_V1_DDL)
        con.execute(
            "INSERT INTO messages (conversation_id, user_text, response_text, audio_url)"
            " VALUES ('old', 'q', 'a', '')"
        )
        con.execute("PRAGMA user_version = 1")
        con.commit()
    finally:
        con.close()


def _columns(db_path, table="messages") -> list[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


class TestTheIncompleteColumnMigration:
    def test_a_deployed_database_gains_the_column(self, tmp_path):
        db_path = tmp_path / "deployed.db"
        _v1_database(db_path)

        PersistenceService(db_path).initialize()

        assert "incomplete" in _columns(db_path), (
            "a database deployed before this change keeps its transcript rows in "
            "the old shape, and every write that names the column fails against it"
        )

    def test_the_rows_deployed_there_are_not_disturbed(self, tmp_path):
        """A migration that rewrites the wrong thing is worse than none."""
        db_path = tmp_path / "deployed.db"
        _v1_database(db_path)

        PersistenceService(db_path).initialize()

        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT user_text, response_text, incomplete FROM messages"
                " WHERE conversation_id = 'old'"
            ).fetchone()
        finally:
            con.close()
        assert row == ("q", "a", 0), (
            f"an existing transcript row was altered by the migration: {row}"
        )

    def test_running_the_migration_twice_is_safe(self, tmp_path):
        db_path = tmp_path / "deployed.db"
        _v1_database(db_path)

        first = PersistenceService(db_path)
        first.initialize()
        # A SECOND instance, so the per-instance ``_schema_ready`` flag cannot
        # hide a migration that only works the first time -- the same shape the
        # semantic_cache migration is held to.
        PersistenceService(db_path).initialize()

        assert "incomplete" in _columns(db_path)

    def test_a_fresh_database_declares_it_up_front(self, tmp_path):
        """The new schema, not only the ALTER.

        A fresh file created by ``_SCHEMA`` that lacks the column would work
        until the first ``ALTER`` attempt on an already-new file -- which is not
        a state anyone would test in production and not one to ship.
        """
        db_path = tmp_path / "fresh.db"

        PersistenceService(db_path).initialize()

        assert "incomplete" in _columns(db_path)

    def test_the_version_moved(self, tmp_path):
        from backend.services.persistence import SCHEMA_VERSION

        db_path = tmp_path / "fresh.db"
        PersistenceService(db_path).initialize()

        con = sqlite3.connect(str(db_path))
        try:
            stamped = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()

        assert stamped == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 2, (
            "the incompleteness column shipped without recording itself, so the "
            "next migration has no way to know it ran"
        )
