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
    audio_url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reports (
    conversation_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    embedding BLOB NOT NULL,
    answer TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


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

    # ── Connection / schema plumbing ─────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection with the pragmas from design D3.

        If a pragma fails (e.g. corrupt file), the raw connection is closed
        before raising so Windows never keeps a lock on the DB file — this is
        what lets the corrupt-rename recovery actually move the file aside.
        """
        con = sqlite3.connect(str(self.db_path))
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
        """Apply DDL once per service instance (idempotent CREATE IF NOT EXISTS)."""
        if self._schema_ready:
            return
        con.executescript(_SCHEMA)
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

    def record_turn(self, cid: str, turn: dict, message: dict) -> None:
        """Composite write-through: turn + message + activity upsert atomically."""
        if not self._enabled:
            return
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
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
                    con.execute(
                        """
                        INSERT INTO turns
                            (conversation_id, n, user_text, assistant_text,
                             chunks_used)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            cid,
                            int(turn.get("n", 0)),
                            turn.get("user_text", ""),
                            turn.get("assistant_text", ""),
                            json.dumps(
                                turn.get("chunks_used", []),
                                ensure_ascii=False,
                                default=str,
                            ),
                        ),
                    )
                    con.execute(
                        """
                        INSERT INTO messages
                            (conversation_id, user_text, response_text, audio_url)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            cid,
                            message.get("user_text", ""),
                            message.get("response_text", ""),
                            message.get("audio_url", ""),
                        ),
                    )
            finally:
                con.close()
        except Exception as e:
            logger.warning("Persistence record_turn failed for %s: %s", cid, e)

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
                    SELECT user_text, response_text, audio_url
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
