"""Tests for PersistenceService — SQLite write-through store (Cap-2 core).

Spec: Conversation Persistence — Write-Through · Crash Safety · Retention
and Report Survival · Failure Isolation.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

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
        assert {"conversations", "turns", "messages", "reports", "semantic_cache"} <= tables

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

        def _dead_connect():
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

        def _dead_connect():
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
