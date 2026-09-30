"""SQLite persistence service for InterviewTTS (Cap-2 conversation durability).

Stdlib-only store (``data/interviewtts.db``, WAL mode) backing conversations,
turns, messages, and reports. Connections are short-lived (one per operation,
invoked via ``asyncio.to_thread`` from async callers) with per-connection
pragmas: ``journal_mode=WAL``, ``foreign_keys=ON``, ``busy_timeout=5000``.

Failure policy (design D7): every public method swallows exceptions, logs a
warning, and returns a sentinel — persistence must NEVER break the live
pipeline. A corrupt database is renamed aside as ``{db}.corrupt-{ts}`` and
recreated from scratch.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: Recorded in ``PRAGMA user_version`` so a migration can tell whether it has
#: already run. It was 0 everywhere and nothing read it, which left every future
#: migration with no way to know the state of a deployed file.
#:
#: 2 -- ``messages.incomplete``, the mark on an answer whose generation was cut
#: short. See ``_add_incomplete_column``.
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    user_text TEXT NOT NULL DEFAULT '',
    assistant_text TEXT NOT NULL DEFAULT '',
    chunks_used TEXT NOT NULL DEFAULT '[]',
    UNIQUE(conversation_id, n)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_text TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT '',
    audio_url TEXT NOT NULL DEFAULT '',
    incomplete INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
    conversation_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _drop_legacy_semantic_cache(con: sqlite3.Connection) -> None:
    """v1 -- remove the table an older schema created and no code now uses.

    ``semantic_cache`` held ``(question, embedding, answer, hit_count,
    created_at)``: the recruiter's question text, a float32 vector of it, and
    the candidate's answer, written on every first-substantive turn. The code
    that wrote it is gone, and ``_SCHEMA`` deliberately does not recreate it --
    but a database FILE deployed before that removal still has the table, and
    nothing here ever dropped it.

    The consequence was that the retention story had a hole in it: the
    transcript containing the same exchange is pruned after
    SESSION_TTL_HOURS (2h by default), while a copy of the candidate's
    questions did not expire at all. That is a privacy defect, not untidiness,
    which is why this warns when it finds something.

    The warning is conditional on finding the table on purpose. Emitted every
    boot it would mean nothing, and an operator learns to skip the one line
    that tells them their stored questions were deleted.
    """
    present = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_cache'"
    ).fetchone()
    if present is None:
        return

    con.execute("DROP TABLE semantic_cache")
    logger.warning(
        "Dropped the legacy semantic_cache table: it stored recruiter questions, "
        "their embeddings and the candidate's answers, and unlike the transcript "
        "it had no retention. Its rows are gone and the capability is not coming "
        "back without a re-measurement."
    )


def _add_incomplete_column(con: sqlite3.Connection) -> None:
    """v2 -- mark transcript entries whose answer was cut short.

    A provider that dies mid-answer used to cost the candidate the whole
    exchange: nothing was stored, so the recruiter's report neither had the
    answer nor any sign that one had been started. The answer now survives, and
    this column is what says it is a fragment -- the difference between a
    transcript that is honest and one that launders a truncated reply as a
    finished one.

    It lives on ``messages`` and nowhere else. ``messages`` is the transcript
    row, it is what ``report.py`` renders, and it is what ``load_conversation``
    hands back -- so the mark arrives where every reader of it actually looks,
    and comes back after a restart from the same row it went into. Putting it
    on ``turns`` instead would have meant stitching it back onto the message
    list across two independently ordered queries (``ORDER BY n`` for turns,
    ``ORDER BY id`` for messages), and those orders genuinely differ once
    ``record_turn`` resolves an ``n`` collision by committing at the next free
    number.

    ``ADD COLUMN`` cannot be conditional, and it fails when the column is
    already there -- so the presence check is the step. ``NOT NULL DEFAULT 0``
    is what makes that safe: rows written by the deployed version are read as
    complete, which is what they are, rather than the migration having to
    backfill them.
    """
    columns = {row[1] for row in con.execute("PRAGMA table_info(messages)")}
    if "incomplete" in columns:
        return
    con.execute(
        "ALTER TABLE messages ADD COLUMN incomplete INTEGER NOT NULL DEFAULT 0"
    )


def _migrate(con: sqlite3.Connection) -> None:
    """Bring an existing database file up to :data:`SCHEMA_VERSION`.

    Idempotent by construction, which matters because this runs on every
    ``initialize`` rather than once: each step checks the live schema rather
    than trusting the recorded version alone, so a file that was hand-edited,
    restored from a partial backup, or created by an older build is still
    brought to the right state.
    """
    _drop_legacy_semantic_cache(con)
    _add_incomplete_column(con)

    current = con.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _replay_summary(turns: list) -> str:
    """Rebuild the rolling summary by replaying the live updater's algorithm.

    Mirrors ``backend.main.update_conversation_summary`` exactly (80-char user
    brief, 120-char assistant brief, 1500-char cap dropping oldest lines) so a
    hydrated conversation prompts the LLM identically to an in-memory one.
    """
    summary = ""
    max_chars = 1500
    for turn in turns:
        user_brief = (turn.get("user_text") or "")[:80]
        assist_brief = (turn.get("assistant_text") or "")[:120]
        new_line = f"- P: {user_brief} → R: {assist_brief}\n"

        combined = summary + new_line
        if len(combined) > max_chars:
            lines = combined.split("\n")
            while len("\n".join(lines)) > max_chars and len(lines) > 1:
                lines.pop(0)
            combined = (
                "[Resumen — turnos más antiguos omitidos por longitud]\n"
                + "\n".join(lines)
            )
        summary = combined
    return summary


class PersistenceService:
    """Write-through SQLite store; never raises into the request path."""

    def __init__(self, db_path: Path, *, enabled: bool = True) -> None:
        self.db_path = Path(db_path)
        self._enabled = bool(enabled)
        self._schema_ready = False

    # ── Public state ─────────────────────────────────────

    def is_enabled(self) -> bool:
        """Whether this store is meant to receive writes at all.

        The public answer to the question callers actually have, which is
        about writes rather than about the flag behind them. It exists so no
        caller has to reach for ``_enabled``: that reach made a rename inside
        this module a silent behaviour change on the other side of the
        ``getattr`` default, with every test still green.

        A store that cannot answer this (one predating the accessor) is the
        caller's decision to make, not this class's.
        """
        return self._enabled

    # ── Reachability ──────────────────────────────────────

    def health(self) -> str:
        """Can this store be opened right now? ``"ok"``, ``"disabled"`` or ``"error"``.

        A LIVE probe, and the only one that can be. The alternative -- a flag
        flipped by whatever failed last -- is not derivable from anything this
        class already knows, because of the failure policy at the top of the
        module: every public method, ``initialize`` included, swallows its
        exception, logs it, and returns a sentinel. A store that cannot be
        opened therefore leaves no exception anywhere for a caller to observe
        and no state on the instance for a flag to read. ``/api/health`` used to
        answer ``status: "ok"`` over exactly that process.

        So this opens a connection and asks. It is one short-lived connection
        and one statement, on an endpoint that is already behind the rate
        limiter and polled once a minute; measured against the alternative --
        guessing -- it is the only version of the answer that can be false.

        ``SELECT 1`` is the probe rather than a schema read on purpose: it
        touches the file (an unopenable one raises here) without depending on
        the schema being present, so it still answers for a store whose
        ``initialize`` never got as far as creating its tables.

        ``"disabled"`` is deliberately not a fault. The deployment has said not
        to write, so nothing that would have been written is missing, and
        reporting it as broken would paint a health rail amber over a working
        instance every time an operator turns a flag off.

        Never raises. A health probe that can fail is not a probe.
        """
        if not self._enabled:
            return "disabled"
        try:
            con = self._connect()
            try:
                con.execute("SELECT 1").fetchone()
            finally:
                con.close()
        except Exception as e:
            logger.error("Persistence store at %s is not reachable: %s", self.db_path, e)
            return "error"
        return "ok"

    # ── Connection / schema plumbing ─────────────────────────

    def _connect(self, *, autocommit: bool = False) -> sqlite3.Connection:
        """Open a short-lived connection with the pragmas from design D3.

        If a pragma fails (e.g. corrupt file), the raw connection is closed
        before raising so Windows never keeps a lock on the DB file — this is
        what lets the corrupt-rename recovery actually move the file aside.

        ``autocommit=True`` drops the driver's implicit-transaction mode so the
        caller can open its own ``BEGIN IMMEDIATE``. It is needed only where a
        read and the write that depends on it have to be atomic
        (``record_turn``); every other operation lets the driver's ``with con:``
        do it, which is one fewer thing to get wrong.
        """
        con = sqlite3.connect(str(self.db_path), isolation_level=None if autocommit else "")
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
        except Exception:
            con.close()
            raise
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        """Apply DDL and migrations once per service instance.

        The CREATE statements are idempotent, so they cover a database that
        predates the current schema. They cannot cover one that predates a
        REMOVAL: ``CREATE TABLE IF NOT EXISTS`` leaves an unwanted table exactly
        where it is, which is how ``semantic_cache`` outlived the code that
        created it. That is what ``_migrate`` is for.
        """
        if self._schema_ready:
            return
        con.executescript(_SCHEMA)
        _migrate(con)
        con.commit()
        self._schema_ready = True

    def initialize(self) -> None:
        """Create parent dirs + schema; quarantine and rebuild a corrupt DB.

        Never raises. On ``sqlite3.DatabaseError`` the file is renamed aside
        as ``{db}.corrupt-{ts}`` and recreated fresh (loud error log).
        """
        if not self._enabled:
            return
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            con = self._connect()
            try:
                self._ensure_schema(con)
            finally:
                con.close()
        except sqlite3.DatabaseError:
            self._recover_corrupt_database()
            try:
                con = self._connect()
                try:
                    self._ensure_schema(con)
                finally:
                    con.close()
            except Exception as e:
                logger.error(
                    "Persistence re-initialization failed after corrupt "
                    "recovery of %s: %s",
                    self.db_path,
                    e,
                )
        except Exception as e:
            logger.warning("Persistence initialize failed: %s", e)

    def _recover_corrupt_database(self) -> None:
        """Rename the corrupt DB aside and drop its WAL/SHM sidecars."""
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        aside = self.db_path.with_name(f"{self.db_path.name}.corrupt-{timestamp}")
        try:
            os.replace(self.db_path, aside)
            logger.error(
                "Persistent store at %s was CORRUPT — moved aside to %s and "
                "recreating from scratch",
                self.db_path,
                aside,
            )
        except OSError as e:
            logger.error(
                "Could not move corrupt database aside (%s): %s", aside, e
            )
            return
        for suffix in ("-wal", "-shm"):
            sidecar = self.db_path.with_name(self.db_path.name + suffix)
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Write-through API ────────────────────────────────────

    def record_conversation(
        self, cid: str, summary: str, created_at: str, last_activity_at: str
    ) -> None:
        """Persist a conversation row on creation (upsert on conflict)."""
        if not self._enabled:
            return
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    con.execute(
                        """
                        INSERT INTO conversations
                            (id, summary, created_at, last_activity_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE
                            SET last_activity_at = excluded.last_activity_at
                        """,
                        (cid, summary, created_at, last_activity_at),
                    )
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence record_conversation failed for %s: %s", cid, e)

    def record_turn(self, cid: str, turn: dict, message: dict) -> dict | None:
        """Composite write-through: turn + message + activity upsert atomically.

        Returns the turn dict this call recorded, or ``None`` when it recorded
        nothing — either because the write failed, or because an identical
        exchange was already there (see below). The returned ``n`` is
        authoritative: callers must reconcile memory from it rather than
        assuming the ``n`` they asked for was used.

        Duplicate-``n`` semantics (turns carry UNIQUE(conversation_id, n), and
        the live pipeline derives ``n`` from ``len(conversations[cid]["turns"])``,
        so two in-flight requests can pick the same ``n``). The discriminator is
        the turn's CONTENT, because a byte-identical exchange is the same
        exchange: STT is greedy (``beam_size=1``), so a double-tapped mic
        re-transcribes to the same ``user_text``, and the FAQ cache returns the
        same ``answer_text``.

        * **Same ``n``, same content** — a retry. Nothing is written and
          ``None`` is returned, so the caller appends nothing to memory either.
          One exchange is one ``turns`` row and one ``messages`` row; the
          paired message used to be inserted unconditionally, which turned one
          exchange into two transcript entries and made the durable report
          claim N+1 turns.
        * **Same ``n``, different content** — a genuine collision. The turn is
          inserted at the next free ``n`` and reported, so neither answer is
          lost.

        Either way the turn the user actually heard reaches disk, so memory can
        never be ahead of disk.  Previously the plain INSERT raised
        ``IntegrityError``, the whole transaction rolled back (losing the paired
        message too), and the failure was swallowed as a warning — the client
        already had its audio and ``done``, so a restart forgot the answer.
        """
        if not self._enabled:
            return None
        try:
            # The retry test is a read whose answer decides a write, so the two
            # halves must not be interleavable. SQLite would in fact serialise
            # this transaction even under the driver's deferred BEGIN: the
            # first statement inside it is the ``conversations`` upsert, and a
            # deferred transaction that OPENS with a write takes the write lock
            # there, so the loser blocks before it ever reaches the reads.
            #
            # That is an emergent property of the statement order, not a
            # property of the transaction, and an emergent property of a
            # durability invariant is not one. Move the upsert below the reads
            # -- a natural-looking refactor -- and the loser reads "nothing at
            # n=0", picks n=0 again, and has its INSERT rejected by
            # UNIQUE(conversation_id, n): an exchange the candidate just heard
            # aloud is dropped. BEGIN IMMEDIATE takes the lock up front so the
            # guarantee does not rest on what the first statement happens to be.
            # busy_timeout (5s) covers the wait.
            con = self._connect(autocommit=True)
            try:
                self._ensure_schema(con)
                con.execute("BEGIN IMMEDIATE")
                try:
                    now_iso = datetime.utcnow().isoformat()
                    con.execute(
                        """
                        INSERT INTO conversations
                            (id, summary, created_at, last_activity_at)
                        VALUES (?, '', ?, ?)
                        ON CONFLICT(id) DO UPDATE
                            SET last_activity_at = excluded.last_activity_at
                        """,
                        (cid, now_iso, now_iso),
                    )

                    user_text = turn.get("user_text", "")
                    assistant_text = turn.get("assistant_text", "")
                    chunks_json = json.dumps(
                        turn.get("chunks_used", []),
                        ensure_ascii=False,
                        default=str,
                    )
                    requested_n = int(turn.get("n", 0))

                    already_there = con.execute(
                        """
                        SELECT 1 FROM turns
                        WHERE conversation_id = ? AND n = ?
                            AND user_text = ? AND assistant_text = ?
                            AND chunks_used = ?
                        """,
                        (cid, requested_n, user_text, assistant_text, chunks_json),
                    ).fetchone()
                    if already_there is not None:
                        con.execute("ROLLBACK")
                        logger.info(
                            "Turn %d for %s is already recorded verbatim — "
                            "retry ignored, no second transcript entry",
                            requested_n, cid,
                        )
                        return None

                    # The row at ``requested_n`` is either absent or a different
                    # exchange. Absent: take the number asked for. Different:
                    # a genuine collision, re-derived below so neither answer
                    # is lost.
                    taken = con.execute(
                        "SELECT 1 FROM turns WHERE conversation_id = ? AND n = ?",
                        (cid, requested_n),
                    ).fetchone()
                    committed_n = (
                        self._next_free_n(con, cid, requested_n)
                        if taken is not None
                        else requested_n
                    )
                    con.execute(
                        """
                        INSERT INTO turns
                            (conversation_id, n, user_text, assistant_text,
                             chunks_used)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (cid, committed_n, user_text, assistant_text, chunks_json),
                    )

                    con.execute(
                        """
                        INSERT INTO messages
                            (conversation_id, user_text, response_text, audio_url,
                             incomplete)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            cid,
                            message.get("user_text", ""),
                            message.get("response_text", ""),
                            message.get("audio_url", ""),
                            1 if message.get("incomplete") else 0,
                        ),
                    )
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence record_turn failed for %s: %s", cid, e)
            return None

        return {
            "n": committed_n,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "chunks_used": turn.get("chunks_used", []),
        }

    @staticmethod
    def _next_free_n(con: sqlite3.Connection, cid: str, after: int) -> int:
        """Next ``n`` to use for ``cid`` when ``after`` is already taken.

        Appends after the conversation's current maximum rather than filling
        gaps: turns are written in ascending ``n`` order and ``load_conversation``
        reads back ``ORDER BY n``, so a gap is not needed and skipping one keeps
        the derivation trivially correct under any pre-existing state.

        Called inside ``record_turn``'s ``BEGIN IMMEDIATE``, which holds the
        write lock, so no racing writer can claim ``n`` between the SELECT and
        the INSERT. The UNIQUE(conversation_id, n) index remains the final
        arbiter regardless: a violation is reported as a failed write (``None``)
        rather than vanishing.
        """
        row = con.execute(
            "SELECT MAX(n) FROM turns WHERE conversation_id = ?", (cid,)
        ).fetchone()
        if row is not None and row[0] is not None:
            return max(int(row[0]) + 1, after + 1)
        return after + 1



    def load_conversation(self, cid: str) -> dict | None:
        """Return a hydrated conversation dict, or None when unknown/failure."""
        if not self._enabled:
            return None
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                row = con.execute(
                    "SELECT * FROM conversations WHERE id = ?", (cid,)
                ).fetchone()
                if row is None:
                    return None
                turn_rows = con.execute(
                    """
                    SELECT n, user_text, assistant_text, chunks_used
                    FROM turns WHERE conversation_id = ? ORDER BY n
                    """,
                    (cid,),
                ).fetchall()
                msg_rows = con.execute(
                    """
                    SELECT user_text, response_text, audio_url, incomplete
                    FROM messages WHERE conversation_id = ? ORDER BY id
                    """,
                    (cid,),
                ).fetchall()
            finally:
                con.close()

            turns = []
            for r in turn_rows:
                try:
                    chunks = json.loads(r["chunks_used"])
                except (json.JSONDecodeError, TypeError):
                    chunks = []
                turns.append(
                    {
                        "n": r["n"],
                        "user_text": r["user_text"],
                        "assistant_text": r["assistant_text"],
                        "chunks_used": chunks,
                    }
                )
            messages = [dict(r) for r in msg_rows]

            return {
                "id": cid,
                "messages": messages,
                "turns": turns,
                # Rolling summary recomputed over hydrated turns (design D5)
                "summary": _replay_summary(turns),
                "created_at": row["created_at"],
                "last_activity_at": row["last_activity_at"],
            }
        except Exception as e:
            logger.warning("Persistence load_conversation failed for %s: %s", cid, e)
            return None

    def evict_conversation(self, cid: str) -> None:
        """Delete conversation/turn/message rows; the reports row survives."""
        if not self._enabled:
            return
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    # ON DELETE CASCADE removes turns + messages
                    con.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence evict_conversation failed for %s: %s", cid, e)

    def record_report(self, cid: str, path: str) -> None:
        """Upsert the cid → report-file linkage (survives eviction)."""
        if not self._enabled:
            return
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    con.execute(
                        """
                        INSERT INTO reports (conversation_id, path, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(conversation_id) DO UPDATE SET
                            path = excluded.path,
                            created_at = excluded.created_at
                        """,
                        (cid, path, datetime.utcnow().isoformat()),
                    )
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence record_report failed for %s: %s", cid, e)

    def prune_conversations(self, older_than_hours: int) -> int:
        """Delete conversation/turn/message rows older than ``older_than_hours``.

        Reports survive per spec — only conversations, turns, and messages
        are removed.  Returns the count of pruned conversations.
        """
        if not self._enabled:
            return 0
        try:
            cutoff = (
                datetime.utcnow() - timedelta(hours=int(older_than_hours))
            ).isoformat()
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    cur = con.execute(
                        "DELETE FROM conversations WHERE last_activity_at < ?",
                        (cutoff,),
                    )
                    return cur.rowcount
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence prune_conversations failed: %s", e)
            return 0

    def prune_reports(self, days: int) -> int:
        """Delete report rows older than ``days``; return count removed."""
        if not self._enabled:
            return 0
        try:
            cutoff = (datetime.utcnow() - timedelta(days=int(days))).isoformat()
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    cur = con.execute(
                        "DELETE FROM reports WHERE created_at < ?", (cutoff,)
                    )
                    return cur.rowcount
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence prune_reports failed: %s", e)
            return 0
