"""A database deployed before the semantic cache was removed still holds it.

THE DEFECT
----------
The ``semantic_cache`` table was created by an earlier version of this schema
and held ``(question, embedding, answer)`` for every first-substantive turn: the
recruiter's question text, a float32 vector of it, and the candidate's answer.
The code that created it is gone -- ``_SCHEMA`` above deliberately does not
include it, and a test asserts it stays out -- but a DEPLOYED database file is
not touched by that. Nothing in this module ran a ``DROP TABLE``, and there was
no schema versioning at all (``PRAGMA user_version`` was 0 everywhere and
nothing read it), so:

    a deployment that predates the removal keeps every question it ever asked,
    forever, while the transcript that contains the same exchange is pruned
    after SESSION_TTL_HOURS (2h by default).

So the retention story was: the conversation expires, and a copy of the
recruiter's questions does not. That is a privacy defect, not a tidiness one,
which is why dropping it is announced in the log rather than done quietly.

WHAT IS ASSERTED
----------------
The migration is idempotent (it runs on every ``initialize``, so it must be
safe to run twice), it drops the table only when it is there, it says so, and a
database that never had the table is untouched.
"""

import logging
import sqlite3

from backend.services.persistence import PersistenceService

LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS semantic_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    embedding BLOB NOT NULL,
    answer TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _seed_legacy_db(db_path, rows: int = 3):
    """A database as an older deployment left it: the table, and data in it."""
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(LEGACY_DDL)
        for index in range(rows):
            con.execute(
                "INSERT INTO semantic_cache (question, embedding, answer, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    f"¿Qué experiencia tienes con Python? #{index}",
                    b"\x00" * 16,
                    "Python es mi lenguaje principal.",
                    "2026-09-01T10:00:00",
                ),
            )
        con.commit()
    finally:
        con.close()


def _tables(db_path) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        return {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        con.close()


def _user_version(db_path) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


class TestTheLegacyTableIsDropped:
    def test_a_deployed_database_loses_the_table(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path)

        PersistenceService(db_path).initialize()

        assert "semantic_cache" not in _tables(db_path), (
            "a database deployed before the removal still stores the "
            "recruiter's questions, their embeddings and the answers forever"
        )

    def test_the_rows_go_with_it(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path, rows=5)

        PersistenceService(db_path).initialize()

        con = sqlite3.connect(str(db_path))
        try:
            surviving = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='semantic_cache'"
            ).fetchone()[0]
        finally:
            con.close()
        assert surviving == 0

    def test_the_rest_of_the_schema_survives(self, tmp_path):
        """A migration that drops the wrong thing is worse than no migration."""
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path)

        PersistenceService(db_path).initialize()

        assert {"conversations", "turns", "messages", "reports"} <= _tables(db_path)


class TestTheDropIsAnnounced:
    def test_it_warns_when_it_finds_the_table(self, tmp_path, caplog):
        # Not a detail: it is the record that candidate interview questions
        # stored by an older version were deleted, and an operator reading the
        # log needs to be able to find that.
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path)

        with caplog.at_level(logging.WARNING):
            PersistenceService(db_path).initialize()

        text = " ".join(r.getMessage() for r in caplog.records)
        assert "semantic_cache" in text, (
            f"the table was dropped without a word in the log: {text!r}"
        )
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_it_says_nothing_on_a_database_that_never_had_it(self, tmp_path, caplog):
        # The warning has to mean "I found your data and removed it". Emitting
        # it every boot would train an operator to ignore it.
        db_path = tmp_path / "fresh.db"

        with caplog.at_level(logging.WARNING):
            PersistenceService(db_path).initialize()

        text = " ".join(r.getMessage() for r in caplog.records)
        assert "semantic_cache" not in text, (
            f"a fresh database logged a semantic_cache removal: {text!r}"
        )


class TestTheMigrationIsIdempotent:
    def test_running_it_twice_is_safe(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path)

        first = PersistenceService(db_path)
        first.initialize()
        # A SECOND service instance, so the once-per-instance `_schema_ready`
        # flag cannot mask a migration that only works the first time.
        PersistenceService(db_path).initialize()

        assert "semantic_cache" not in _tables(db_path)
        assert {"conversations", "turns", "messages", "reports"} <= _tables(db_path)

    def test_running_it_twice_warns_only_about_the_first(self, tmp_path, caplog):
        db_path = tmp_path / "legacy.db"
        _seed_legacy_db(db_path)

        with caplog.at_level(logging.WARNING):
            PersistenceService(db_path).initialize()
        first_count = sum("semantic_cache" in r.getMessage() for r in caplog.records)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            PersistenceService(db_path).initialize()
        second_count = sum("semantic_cache" in r.getMessage() for r in caplog.records)

        assert first_count == 1
        assert second_count == 0, (
            "the removal was announced twice; the warning has to mean that "
            "something was found and deleted"
        )

    def test_a_stamp_is_left_behind(self, tmp_path):
        """So the next migration has something to compare against.

        Without a version, ``user_version`` stays 0 forever and every future
        migration has to guess whether it has already run -- which is how a
        migration ends up either skipped or applied to a table it no longer
        owns.
        """
        db_path = tmp_path / "app.db"
        PersistenceService(db_path).initialize()

        version = _user_version(db_path)
        assert version >= 1, (
            f"PRAGMA user_version is still {version}; nothing records that the "
            "legacy table was dealt with"
        )

    def test_the_version_matches_the_module(self, tmp_path):
        from backend.services.persistence import SCHEMA_VERSION

        db_path = tmp_path / "app.db"
        PersistenceService(db_path).initialize()

        assert _user_version(db_path) == SCHEMA_VERSION
