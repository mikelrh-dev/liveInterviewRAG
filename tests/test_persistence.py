"""Tests for PersistenceService — SQLite write-through store (Cap-2 core).

Spec: Conversation Persistence — Write-Through · Crash Safety · Retention
and Report Survival · Failure Isolation.
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import stub_rag_context_shapes
from backend.services.persistence import PersistenceService


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Keep the shared rate-limit store isolated between tests."""
    from backend.main import _rate_limit_store

    _rate_limit_store.clear()


def _make_service(tmp_path, name="app.db", **kwargs):
    svc = PersistenceService(tmp_path / name, **kwargs)
    svc.initialize()
    return svc


def _raw_counts(db_path, cid):
    con = sqlite3.connect(str(db_path))
    try:
        counts = {}
        for table in ("conversations", "turns", "messages", "reports"):
            counts[table] = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE "
                + ("conversation_id" if table != "conversations" else "id")
                + " = ?",
                (cid,),
            ).fetchone()[0]
        return counts
    finally:
        con.close()


class TestSchemaAndPragmas:
    """DDL creation and connection pragmas (design D3)."""

    def test_initialize_creates_dir_and_schema_tables(self, tmp_path):
        db_path = tmp_path / "nested" / "data" / "app.db"
        svc = PersistenceService(db_path)
        svc.initialize()

        assert db_path.exists(), "DB file must be created, parent dirs included"
        con = sqlite3.connect(str(db_path))
        try:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            con.close()
        assert {"conversations", "turns", "messages", "reports"} <= tables
        # The semantic_cache table is deliberately absent. It was the DDL half
        # of a cache that could never return a hit; leaving the table behind
        # would keep creating a store for data nothing reads or writes. This
        # assertion is what makes that removal stick.
        assert "semantic_cache" not in tables, (
            "the semantic_cache table is back in the schema -- if a cache that "
            "stores raw recruiter questions is being reintroduced, re-measure "
            "it first (see test_rag.py::TestSemanticAnswerCacheWasNotViable)"
        )


    def test_wal_fk_busy_timeout_pragmas_active(self, tmp_path):
        svc = _make_service(tmp_path)
        con = svc._connect()
        try:
            mode = con.execute("PRAGMA journal_mode").fetchone()[0]
            assert str(mode).lower() == "wal"
            fk = con.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1
            timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout == 5000
        finally:
            con.close()


class TestRecordAndLoad:
    """Composite record_turn / hydrated load_conversation roundtrip."""

    def test_record_turn_load_roundtrip_with_summary_replay_and_chunks_json(self, tmp_path):
        svc = _make_service(tmp_path)
        created = "2026-08-26T10:00:00"
        svc.record_conversation("c1", "", created, created)

        turns = [
            {
                "n": 0,
                "user_text": "¿Qué tecnologías usaste?",
                "assistant_text": "Principalmente Python y FastAPI.",
                "chunks_used": [
                    {"text": "cv context", "score": 0.91, "source": "cv.md"}
                ],
            },
            {
                "n": 1,
                "user_text": "¿Y proyectos?",
                "assistant_text": "InterviewTTS es mi proyecto principal.",
                "chunks_used": [],
            },
        ]
        messages = [
            {
                "user_text": turns[0]["user_text"],
                "response_text": turns[0]["assistant_text"],
                "audio_url": "/audio/c1/aaa.mp3",
            },
            {
                "user_text": turns[1]["user_text"],
                "response_text": turns[1]["assistant_text"],
                "audio_url": "/audio/c1/bbb.mp3",
            },
        ]
        for turn, message in zip(turns, messages):
            svc.record_turn("c1", turn, message)

        loaded = svc.load_conversation("c1")
        assert loaded is not None
        assert loaded["id"] == "c1"
        assert loaded["created_at"] == created, "creation timestamp must survive"
        assert [t["n"] for t in loaded["turns"]] == [0, 1]
        # chunks_used JSON-decoded back into structured dicts
        assert loaded["turns"][0]["chunks_used"][0]["source"] == "cv.md"
        assert loaded["turns"][1]["chunks_used"] == []
        assert loaded["messages"][1]["audio_url"] == "/audio/c1/bbb.mp3"

        # Rolling summary recomputed exactly like the live updater replays it
        import backend.main as main_mod

        main_mod.conversations["_roundtrip_ref"] = {
            "id": "_roundtrip_ref",
            "messages": [],
            "turns": [],
            "summary": "",
            "created_at": created,
            "last_activity_at": created,
        }
        try:
            for turn in turns:
                main_mod.update_conversation_summary("_roundtrip_ref", turn)
            expected_summary = main_mod.conversations["_roundtrip_ref"]["summary"]
        finally:
            del main_mod.conversations["_roundtrip_ref"]

        assert loaded["summary"] == expected_summary
        assert "¿Qué tecnologías usaste?"[:80] in loaded["summary"]

    def test_load_unknown_cid_returns_none(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.load_conversation("never-seen-cid") is None


class TestTurnWriteIdempotency:
    """record_turn must never lose a turn to the UNIQUE(conversation_id, n).

    Concurrency regression: the live pipeline derives ``n`` from
    ``len(conversations[cid]["turns"])``, so two in-flight requests on the same
    conversation pick the SAME ``n``.  With a plain INSERT the loser's turn (and
    its paired message) was rolled back and swallowed as a ``logger.warning``,
    leaving memory ahead of disk — the user heard an answer that a restart
    forgets.
    """

    def test_collision_with_different_content_reports_committed_n(self, tmp_path):
        """A genuine (cid, n) collision must commit, and REPORT the n it used.

        The caller needs the committed ``n`` to reconcile memory with disk.
        """
        svc = _make_service(tmp_path)
        first = {"n": 0, "user_text": "q1", "assistant_text": "a1", "chunks_used": []}
        second = {"n": 0, "user_text": "q2", "assistant_text": "a2", "chunks_used": []}

        committed_first = svc.record_turn("c1", first, {
            "user_text": "q1", "response_text": "a1", "audio_url": "/1.mp3"})
        committed_second = svc.record_turn("c1", second, {
            "user_text": "q2", "response_text": "a2", "audio_url": "/2.mp3"})

        assert committed_first == {"n": 0, "user_text": "q1", "assistant_text": "a1",
                                   "chunks_used": []}
        # The second turn collided on n=0 and must have been re-derived, not lost
        assert committed_second["n"] == 1, "a genuine collision re-derives the next free n"
        assert committed_second["user_text"] == "q2"
        assert committed_second["assistant_text"] == "a2", (
            "the colliding turn's own content must be preserved, not overwritten"
        )

    def test_forced_collision_keeps_memory_and_disk_in_agreement(self, tmp_path):
        """Whatever the caller put in memory must be exactly what disk holds.

        Models the live pipeline: ``n = len(memory_turns)``, memory is appended
        with the turn ``record_turn`` reports as committed, then both are
        compared. Under a forced collision disk must not fall behind memory.
        """
        svc = _make_service(tmp_path)
        memory_turns = []

        # Two turns that both resolve to n=0 because memory was empty for both
        for n, (q, a) in enumerate([("q0", "a0"), ("q0", "a0-different")]):
            reported = svc.record_turn(
                "c1",
                {"n": len(memory_turns), "user_text": q, "assistant_text": a,
                 "chunks_used": []},
                {"user_text": q, "response_text": a, "audio_url": f"/{n}.mp3"},
            )
            assert reported is not None, "a successful write must report its committed turn"
            memory_turns.append(reported)

        disk = svc.load_conversation("c1")
        assert [t["n"] for t in disk["turns"]] == [t["n"] for t in memory_turns]
        assert [t["user_text"] for t in disk["turns"]] == [
            t["user_text"] for t in memory_turns
        ]
        assert [t["assistant_text"] for t in disk["turns"]] == [
            t["assistant_text"] for t in memory_turns
        ], "memory must never be ahead of disk"
        assert [t["n"] for t in memory_turns] == [0, 1]

    def test_sequential_multiturn_unchanged_distinct_n(self, tmp_path):
        """Normal sequential conversation: N turns, N distinct ascending n."""
        svc = _make_service(tmp_path)
        for n in range(5):
            reported = svc.record_turn(
                "c1",
                {"n": n, "user_text": f"q{n}", "assistant_text": f"a{n}",
                 "chunks_used": []},
                {"user_text": f"q{n}", "response_text": f"a{n}",
                 "audio_url": f"/{n}.mp3"},
            )
            assert reported["n"] == n, "an uncontended write must keep the requested n"

        loaded = svc.load_conversation("c1")
        assert [t["n"] for t in loaded["turns"]] == [0, 1, 2, 3, 4]
        assert len({t["n"] for t in loaded["turns"]}) == 5, "n values must be distinct"
        assert [t["assistant_text"] for t in loaded["turns"]] == [
            "a0", "a1", "a2", "a3", "a4"
        ]
        assert len(loaded["messages"]) == 5

    def test_record_turn_reports_none_when_disabled(self, tmp_path):
        """The failure path must not fabricate a committed turn."""
        svc = PersistenceService(tmp_path / "off.db", enabled=False)
        assert svc.record_turn(
            "c1", {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
            {"user_text": "q", "response_text": "a", "audio_url": ""},
        ) is None

    def test_record_turn_reports_none_and_warns_on_failure(self, tmp_path, monkeypatch,
                                                          caplog):
        """A failed write reports None so the caller can stop trusting memory."""
        svc = _make_service(tmp_path)

        def _dead_connect(*args, **kwargs):
            # ``*args, **kwargs`` because this replaces a method: the
            # replacement has to accept whatever the caller passes, or the test
            # passes on a TypeError from the stub instead of on the dead store
            # it is pretending to model.
            raise RuntimeError("disk dead")

        monkeypatch.setattr(svc, "_connect", _dead_connect)
        caplog.set_level(logging.WARNING, logger="backend.services.persistence")

        assert svc.record_turn(
            "c1", {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
            {"user_text": "q", "response_text": "a", "audio_url": ""},
        ) is None
        assert any(r.levelno == logging.WARNING for r in caplog.records)


class TestOneExchangeIsRecordedOnce:
    """A retried turn must not become a second transcript entry.

    THE DEFECT THIS REPLACES
    -----------------------
    ``record_turn`` de-duplicated the ``turns`` row on a retry and then ran an
    UNCONDITIONAL ``INSERT INTO messages``. One exchange therefore became two
    transcript entries, and the durable report (``services/report.py`` renders
    ``Turnos: len(state['messages'])``) claimed N+1 turns and printed the same
    exchange twice.

    An earlier version of this file asserted that as the requirement --
    ``assert len(loaded["messages"]) == 2, "messages are append-only, never
    de-duplicated"``. The schema cannot distinguish an append-only transcript
    from a duplicated one: ``messages`` carries no turn number, so "append
    only" and "appended twice by a retry" are the same row shape. The
    discriminator has to be content, and it lives on the ``turns`` row.

    WHY CONTENT IS THE RIGHT DISCRIMINATOR
    ---------------------------------------
    A byte-identical exchange is the SAME exchange. STT is greedy
    (``beam_size=1``), so a double-tapped mic re-transcribes to the same
    ``user_text``; the FAQ cache returns the same ``answer_text``. Same ``n`` +
    same content == one exchange, recorded once. Same ``n`` + DIFFERENT content
    == a genuine collision: the second exchange is real and is committed at the
    next free ``n``, so neither answer is lost.
    """

    TURN = {
        "n": 0,
        "user_text": "como testias tu codigo",
        "assistant_text": "Hago tests unitarios y de integracion con pytest.",
        "chunks_used": [{"text": "pytest", "score": 0.8, "source": "skills/testing.md"}],
    }
    MESSAGE = {
        "user_text": "como testias tu codigo",
        "response_text": "Hago tests unitarios y de integracion con pytest.",
        "audio_url": "/audio/c1/abc.mp3",
    }

    def test_identical_retry_records_the_exchange_exactly_once(self, tmp_path):
        svc = _make_service(tmp_path)

        first = svc.record_turn("c1", dict(self.TURN), dict(self.MESSAGE))
        assert first is not None, "the first write is a real exchange"
        second = svc.record_turn("c1", dict(self.TURN), dict(self.MESSAGE))

        loaded = svc.load_conversation("c1")
        assert loaded is not None
        assert len(loaded["turns"]) == 1, "an identical retry must not duplicate the turn"
        assert loaded["turns"][0]["n"] == 0
        assert loaded["turns"][0]["assistant_text"] == self.TURN["assistant_text"]
        assert len(loaded["messages"]) == 1, (
            "the retry added a second transcript entry for one exchange: "
            f"{[m['response_text'] for m in loaded['messages']]}"
        )
        assert second is None, (
            "a retry must report that it added nothing, or the caller appends "
            "the same turn number to memory a second time"
        )

    @staticmethod
    def _two_racing_writers(tmp_path, name, monkeypatch, delay=0.05):
        """Two independent services on one file, each with a widened window.

        Separate objects, because each holds its own connection and its own
        "DDL applied" flag: one shared instance would serialise on nothing and
        hide the behaviour under test.

        ``delay`` matters more than the barrier. Two GIL-bound writes usually
        land in whatever order the scheduler picks, which is not a race -- it
        is luck, and a test that passes by luck is a test that cannot fail.
        The trace callback sleeps on each connection's FIRST statement, i.e.
        after it has taken whatever lock it takes and before it has decided
        anything, so the second writer reliably arrives mid-transaction.
        """
        db_path = tmp_path / name
        writers = [PersistenceService(db_path), PersistenceService(db_path)]
        for writer in writers:
            writer.initialize()

        for writer in writers:
            bound = writer._connect

            def factory(*args, _bound=bound, **kwargs):
                con = _bound(*args, **kwargs)
                state = {"slept": False}

                def _trace(_sql):
                    if not state["slept"]:
                        state["slept"] = True
                        time.sleep(delay)

                con.set_trace_callback(_trace)
                return con

            monkeypatch.setattr(writer, "_connect", factory)
        return writers

    @staticmethod
    def _race(writers, payloads):
        """Run one ``record_turn`` per writer from real threads, simultaneously."""
        barrier = threading.Barrier(len(writers))
        reported: list = []
        errors: list = []

        def _write(service, turn, message):
            try:
                barrier.wait(timeout=10)
                reported.append(service.record_turn("c1", turn, message))
            except BaseException as exc:  # noqa: BLE001 - re-raised by the asserts
                errors.append(exc)

        threads = [
            threading.Thread(target=_write, args=(w, t, m))
            for w, (t, m) in zip(writers, payloads)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not any(t.is_alive() for t in threads), (
            "a writer is still blocked on the store lock after 30s"
        )
        assert not errors, f"a writer raised: {errors!r}"
        return reported

    def test_concurrent_writers_of_one_exchange_record_it_exactly_once(
        self, tmp_path, monkeypatch
    ):
        """Two writers, one exchange: the store is the arbiter, not the caller.

        Both in-flight requests read ``len(turns)`` before either appends, so
        both ask for the same ``n``. A read-then-write retry check is only a
        retry check if the two halves cannot be interleaved, so this drives two
        real connections that genuinely overlap rather than calling
        ``record_turn`` twice from one thread -- which a sequential
        implementation passes while still duplicating the exchange under the
        race that causes it.
        """
        writers = self._two_racing_writers(tmp_path, "race.db", monkeypatch)
        payloads = [(dict(self.TURN), dict(self.MESSAGE))] * 2

        reported = self._race(writers, payloads)

        loaded = writers[0].load_conversation("c1")
        assert loaded is not None
        assert len(loaded["turns"]) == 1, (
            f"two writers produced {len(loaded['turns'])} turn rows"
        )
        assert len(loaded["messages"]) == 1, (
            f"two writers produced {len(loaded['messages'])} transcript entries "
            "for one exchange"
        )
        assert [r for r in reported if r is not None] != [], (
            "the winning writer must report the exchange it recorded"
        )
        assert len([r for r in reported if r is not None]) == 1, (
            f"both writers reported a new exchange: {reported!r}"
        )

    def test_a_different_second_exchange_at_the_same_n_is_still_recorded(self, tmp_path):
        """Content is the discriminator, so a genuine collision is never dropped.

        The counterpart to the retry case: if the de-duplication were keyed on
        ``(conversation_id, n)`` alone, this second exchange would be silently
        discarded and the candidate would hear an answer that no restart
        recalls.
        """
        svc = _make_service(tmp_path)
        first_turn = {**self.TURN, "assistant_text": "La primera respuesta."}
        second_turn = {**self.TURN, "assistant_text": "Una respuesta distinta."}

        svc.record_turn("c1", first_turn, {
            **self.MESSAGE, "response_text": first_turn["assistant_text"]})
        svc.record_turn("c1", second_turn, {
            **self.MESSAGE, "response_text": second_turn["assistant_text"]})

        loaded = svc.load_conversation("c1")
        assert [t["assistant_text"] for t in loaded["turns"]] == [
            "La primera respuesta.", "Una respuesta distinta."
        ], "a different exchange at the same n must be recorded, not swallowed"
        assert [t["n"] for t in loaded["turns"]] == [0, 1]
        assert len(loaded["messages"]) == 2, "two exchanges are two transcript entries"

    def test_concurrent_writers_of_different_exchanges_both_survive(
        self, tmp_path, monkeypatch
    ):
        """The same race with different content must lose nothing.

        This is the case a deferred transaction gets wrong. The loser's retry
        check and its "is this ``n`` taken" check both read a state the winner
        has not committed yet, so it picks ``n=0`` again and its INSERT is
        rejected by UNIQUE(conversation_id, n) -- the exchange the candidate
        just heard aloud is dropped, which is the blocker this class exists for.
        """
        writers = self._two_racing_writers(tmp_path, "race-diff.db", monkeypatch)
        answers = ["La primera respuesta.", "Una respuesta distinta."]
        payloads = [
            ({**self.TURN, "assistant_text": answer},
             {**self.MESSAGE, "response_text": answer})
            for answer in answers
        ]

        reported = self._race(writers, payloads)

        loaded = writers[0].load_conversation("c1")
        assert sorted(t["assistant_text"] for t in loaded["turns"]) == sorted(answers), (
            f"a racing writer's exchange was dropped instead of re-derived: "
            f"{[t['assistant_text'] for t in loaded['turns']]}"
        )
        assert sorted({t["n"] for t in loaded["turns"]}) == [0, 1], (
            "the two exchanges must occupy distinct turn numbers"
        )
        assert len(loaded["messages"]) == 2
        assert all(r is not None for r in reported), (
            f"both writers recorded a new exchange and both must say so: {reported!r}"
        )

    def test_a_retry_does_not_push_a_duplicate_n_into_memory(self, tmp_path):
        """Memory must not gain a second copy of a turn number already held.

        ``persist_turn`` appends whatever the write reports. A retry that
        reports the turn it found would put the same ``n`` in ``turns`` twice;
        ``build_turn`` then derives the next ``n`` from ``len(turns)``, which has
        silently drifted from the store's ``MAX(n)`` and never recovers.
        """
        import asyncio as _asyncio

        import backend.main as main_mod
        from backend.conversation import persist_turn

        svc = _make_service(tmp_path, name="memory.db")
        cid = "retry-memory"
        main_mod.conversations[cid] = {
            "id": cid, "messages": [], "turns": [], "summary": "",
            "created_at": "", "last_activity_at": "",
        }
        original = main_mod.persistence
        main_mod.persistence = svc
        try:
            first = _asyncio.run(
                persist_turn(cid, dict(self.TURN), dict(self.MESSAGE))
            )
            retry = _asyncio.run(
                persist_turn(cid, dict(self.TURN), dict(self.MESSAGE))
            )
            state = main_mod.conversations[cid]
        finally:
            main_mod.persistence = original
            main_mod.conversations.pop(cid, None)

        assert first is not None and first["n"] == 0
        assert retry is None, (
            "a retry must report that it stored nothing, so nothing is appended"
        )
        assert [t["n"] for t in state["turns"]] == [0], (
            f"memory holds {state['turns']!r}; a retried turn number was "
            "appended a second time"
        )
        assert len(state["messages"]) == 1, (
            f"memory holds {len(state['messages'])} transcript entries for one "
            "exchange"
        )
        assert svc.load_conversation(cid)["turns"][0]["n"] == 0


class TestEvictAndReports:
    """Eviction deletes conversation data but preserves reports (spec)."""

    def test_evict_deletes_rows_but_keeps_report_row(self, tmp_path):
        db_path = tmp_path / "evict.db"
        svc = _make_service(tmp_path, name="evict.db")
        svc.record_conversation("c1", "", "2026-08-26T10:00:00", "2026-08-26T10:00:00")
        svc.record_turn(
            "c1",
            {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
            {"user_text": "q", "response_text": "a", "audio_url": ""},
        )
        svc.record_report("c1", "/reports/c1/report.md")

        svc.evict_conversation("c1")

        counts = _raw_counts(db_path, "c1")
        assert counts["conversations"] == 0
        assert counts["turns"] == 0, "turns must cascade-delete with the conversation"
        assert counts["messages"] == 0, "messages must cascade-delete with the conversation"
        assert counts["reports"] == 1, "report row MUST survive eviction"
        assert svc.load_conversation("c1") is None

    def test_record_report_upsert_and_prune_reports_retention(self, tmp_path):
        db_path = tmp_path / "reports.db"
        svc = _make_service(tmp_path, name="reports.db")

        svc.record_report("c1", "/reports/c1/first.md")
        svc.record_report("c1", "/reports/c1/second.md")  # upsert, not duplicate
        svc.record_report("c2", "/reports/c2/keep.md")

        con = sqlite3.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT conversation_id, path FROM reports ORDER BY conversation_id"
            ).fetchall()
        finally:
            con.close()
        assert len(rows) == 2, "re-reporting the same cid must upsert"
        assert dict(rows)["c1"] == "/reports/c1/second.md"

        # Backdate c1 beyond the retention window; c2 stays fresh
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "UPDATE reports SET created_at = ? WHERE conversation_id = 'c1'",
                ("2020-01-01T00:00:00",),
            )
            con.commit()
        finally:
            con.close()

        deleted = svc.prune_reports(30)
        assert deleted == 1

        con = sqlite3.connect(str(db_path))
        try:
            remaining = [
                r[0] for r in con.execute("SELECT conversation_id FROM reports")
            ]
        finally:
            con.close()
        assert remaining == ["c2"]


class TestFailureIsolation:
    """Persistence failures never propagate into the request path (design D7)."""

    def test_execute_failure_logs_warning_returns_sentinel(self, tmp_path, monkeypatch, caplog):
        svc = _make_service(tmp_path)

        def _dead_connect(*args, **kwargs):
            # ``*args, **kwargs`` because this replaces a method: the
            # replacement has to accept whatever the caller passes, or the test
            # passes on a TypeError from the stub instead of on the dead store
            # it is pretending to model.
            raise RuntimeError("disk dead")

        monkeypatch.setattr(svc, "_connect", _dead_connect)
        caplog.set_level(logging.WARNING, logger="backend.services.persistence")

        # None of these may raise
        assert svc.record_turn(
            "c1",
            {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
            {"user_text": "q", "response_text": "a", "audio_url": ""},
        ) is None
        assert svc.load_conversation("c1") is None
        assert svc.evict_conversation("c1") is None
        assert svc.record_report("c1", "/x.md") is None
        assert svc.prune_reports(30) == 0

        warnings = [
            r for r in caplog.records
            if "disk dead" in r.getMessage() and r.levelno == logging.WARNING
        ]
        assert warnings, "failures must be logged as warnings, never raised"


class TestCrashSafety:
    """Corrupt DB is quarantined and rebuilt (design D7)."""

    def test_corrupt_db_renamed_aside_and_recreated(self, tmp_path):
        db_path = tmp_path / "corrupt.db"
        db_path.write_bytes(b"this is definitely not a sqlite database")

        svc = PersistenceService(db_path)
        svc.initialize()  # must not raise

        assert db_path.exists()
        con = sqlite3.connect(str(db_path))
        try:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            con.close()
        assert "conversations" in tables, "schema must be rebuilt after corruption"

        quarantined = list(tmp_path.glob("*.corrupt-*"))
        assert quarantined, "corrupt file must be renamed aside, not deleted"
        assert b"definitely not a sqlite" in quarantined[0].read_bytes()


class TestPruneConversations:
    """Prune stale conversation/turn/message rows (reports survive)."""

    def test_prune_conversations_deletes_stale_rows(self, tmp_path):
        """Insert 2 old + 1 fresh conversation, prune, verify 2 deleted,
        1 survived, and reports intact."""
        db_path = tmp_path / "prune.db"
        svc = _make_service(tmp_path, name="prune.db")

        now_ts = datetime.utcnow().isoformat()

        # Create old conversations with fresh timestamps, then backdate via UPDATE
        # (record_turn upserts last_activity_at to now, so we must UPDATE after)
        svc.record_conversation("old1", "", now_ts, now_ts)
        svc.record_turn(
            "old1",
            {"n": 0, "user_text": "q1", "assistant_text": "a1", "chunks_used": []},
            {"user_text": "q1", "response_text": "a1", "audio_url": ""},
        )
        svc.record_report("old1", "/reports/old1.md")

        svc.record_conversation("old2", "", now_ts, now_ts)
        svc.record_turn(
            "old2",
            {"n": 0, "user_text": "q2", "assistant_text": "a2", "chunks_used": []},
            {"user_text": "q2", "response_text": "a2", "audio_url": ""},
        )

        # Create fresh conversation
        svc.record_conversation("fresh1", "", now_ts, now_ts)
        svc.record_turn(
            "fresh1",
            {"n": 0, "user_text": "qf", "assistant_text": "af", "chunks_used": []},
            {"user_text": "qf", "response_text": "af", "audio_url": ""},
        )
        svc.record_report("fresh1", "/reports/fresh1.md")

        # Backdate old1 and old2 beyond the 2-hour TTL (like test_report_prune)
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "UPDATE conversations SET last_activity_at = ? WHERE id IN ('old1', 'old2')",
                ("2026-08-25T00:00:00",),
            )
            con.commit()
        finally:
            con.close()

        # Prune with 2-hour TTL (older_than_hours=2)
        pruned = svc.prune_conversations(older_than_hours=2)
        assert pruned == 2, "must return count of pruned conversations"

        # Verify old1 and old2 are gone from conversations/turns/messages
        con = sqlite3.connect(str(db_path))
        try:
            remaining_cids = [
                r[0] for r in con.execute("SELECT id FROM conversations ORDER BY id")
            ]
            turns_count = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            messages_count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            con.close()
        assert remaining_cids == ["fresh1"], "only fresh conversation must survive"
        assert turns_count == 1, "only fresh turn must remain"
        assert messages_count == 1, "only fresh message must remain"

        # Reports must survive for both old and fresh
        counts_old1 = _raw_counts(db_path, "old1")
        counts_fresh = _raw_counts(db_path, "fresh1")
        assert counts_old1["reports"] == 1, "old conversation's report MUST survive prune"
        assert counts_fresh["reports"] == 1, "fresh conversation's report MUST survive prune"

    def test_prune_conversations_failure_is_silent(self, tmp_path, monkeypatch, caplog):
        """If execute raises, prune_conversations returns 0 and never propagates."""
        svc = _make_service(tmp_path)

        def _dead_connect(*args, **kwargs):
            # ``*args, **kwargs`` because this replaces a method: see the note
            # in ``test_record_turn_reports_none_and_warns_on_failure``.
            raise RuntimeError("database locked")

        monkeypatch.setattr(svc, "_connect", _dead_connect)
        caplog.set_level(logging.WARNING, logger="backend.services.persistence")

        result = svc.prune_conversations(older_than_hours=2)
        assert result == 0

        warnings = [
            r for r in caplog.records
            if "database locked" in r.getMessage() and r.levelno == logging.WARNING
        ]
        assert warnings, "failures must be logged as warnings, never raised"


class TestDisabledFlag:
    """enabled=False gates every method to a no-op."""

    def test_disabled_flag_no_ops_all_methods(self, tmp_path):
        db_path = tmp_path / "disabled.db"
        svc = PersistenceService(db_path, enabled=False)

        svc.initialize()  # no-op — must not even create the directory/file
        assert not db_path.exists()
        assert svc.record_turn(
            "c1",
            {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
            {"user_text": "q", "response_text": "a", "audio_url": ""},
        ) is None
        assert svc.load_conversation("c1") is None
        assert svc.evict_conversation("c1") is None
        assert svc.record_report("c1", "/x.md") is None
        assert svc.prune_reports(30) == 0
        assert svc.prune_conversations(2) == 0
        assert not db_path.exists(), "disabled store must never touch disk"


class TestIsEnabledAccessor:
    """``is_enabled()`` is the public answer to "does this store take writes?".

    ``store_is_configured`` in ``backend/conversation.py`` used to read the
    private ``_enabled`` attribute through ``getattr``. That reach had two
    costs: renaming the flag inside this module silently changed what the
    caller believed, and the flag's meaning had no owner — the question the
    caller actually asks is about writes, not about a field. A public accessor
    makes the rename a non-event and puts the semantics next to the flag.
    """

    def test_reports_true_for_a_live_store(self, tmp_path):
        assert _make_service(tmp_path, "live.db").is_enabled() is True

    def test_reports_false_for_a_disabled_store(self, tmp_path):
        svc = PersistenceService(tmp_path / "off.db", enabled=False)
        assert svc.is_enabled() is False

    def test_reports_a_real_bool_not_the_constructor_argument(self, tmp_path):
        """``bool`` is the contract, whatever truthy value was passed in.

        The caller branches on this value, so an ``enabled=1`` that leaked
        through as ``1`` would work by accident and break the moment anything
        compared it to ``False``.
        """
        svc = PersistenceService(tmp_path / "truthy.db", enabled=1)
        assert svc.is_enabled() is True

    def test_the_accessor_is_public_and_callable(self, tmp_path):
        """A method named without an underscore, reachable from outside.

        This is the part of the rename defence that *is* observable from
        outside the class: the caller can only stay decoupled from ``_enabled``
        while a public method exists. The complementary half — that
        ``conversation.py`` actually uses it and names nothing private — is
        asserted in tests/test_conversation_memory.py, because that is the
        file that used to break.
        """
        svc = _make_service(tmp_path, "public.db")

        assert callable(getattr(svc, "is_enabled", None)), (
            "PersistenceService must expose is_enabled(); without it the "
            "caller has to reach into _enabled and a rename lands as a "
            "silent behaviour change"
        )
        assert svc.is_enabled() is True


class TestHydrationWiring:
    """_get_conversation_or_hydrate rebuilds memory from the DB on miss."""

    @pytest.fixture
    def hydrated_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import backend.main as main_mod

        with patch("backend.main.stt_service") as mock_stt, \
             patch("backend.main.llm_service") as mock_llm, \
             patch("backend.main.tts_service") as mock_tts, \
             patch("backend.main.rag_pipeline") as mock_rag, \
             patch("backend.main.candidate_profile"):

            mock_stt.transcribe.return_value = "Second question?"
            mock_rag.get_context_string.return_value = "Built InterviewTTS with Python."
            mock_rag.get_chunks_with_scores.return_value = []
            stub_rag_context_shapes(mock_rag)
            mock_rag.chunks = [MagicMock()]


            mock_llm.generate.return_value = "Second answer."

            async def fake_synth(text, output_path=None):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.touch()
                return output_path

            mock_tts.synthesize = fake_synth

            svc = PersistenceService(tmp_path / "hydrate.db")
            svc.initialize()
            monkeypatch.setattr(main_mod, "persistence", svc)
            main_mod.conversations.clear()

            yield TestClient(main_mod.app), svc, main_mod

    def test_hydrate_on_miss_returns_persisted_conversation(self, hydrated_client):
        client, svc, main_mod = hydrated_client
        cid = "hydrate-me-01"

        # A previous process persisted this conversation; memory is empty
        svc.record_conversation(cid, "", "2026-08-26T09:00:00", "2026-08-26T09:00:00")
        svc.record_turn(
            cid,
            {
                "n": 0,
                "user_text": "First question?",
                "assistant_text": "First answer.",
                "chunks_used": [],
            },
            {
                "user_text": "First question?",
                "response_text": "First answer.",
                "audio_url": "",
            },
        )
        assert cid not in main_mod.conversations

        response = client.post(
            f"/api/conversation/{cid}/message",
            files={"audio": ("t.webm", b"audio-bytes", "audio/webm")},
        )

        assert response.status_code == 200
        hydrated = main_mod.conversations[cid]
        assert hydrated["id"] == cid
        assert len(hydrated["turns"]) == 2, "seeded turn + newly answered turn"
        assert hydrated["turns"][0]["user_text"] == "First question?"
        assert hydrated["turns"][0]["assistant_text"] == "First answer."
        assert hydrated["messages"][0]["response_text"] == "First answer."
