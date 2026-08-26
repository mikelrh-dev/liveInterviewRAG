"""Semantic answer cache for InterviewTTS (Cap-3 instant paraphrase answers).

Stores (question, float32 embedding, answer) rows in the shared SQLite DB and
serves stored answers when an incoming first-substantive question is
paraphrase-close (cosine ≥ threshold). Reuses the RAG pipeline's embedder via
a provider callable — never loads a second model.

Guardrails (spec: Mandatory Guardrails): lookup/store only when enabled; the
embedder provider returning ``None`` (kill-switch config, TF-IDF fallback,
uninitialized pipeline) is the universal disabled signal; entries expire after
``ttl_days``; the table is FIFO-capped at ``max_rows``. Lookup embeds the RAW
question — no ``expand_query``, which is retrieval-oriented and would skew
question↔question similarity (design D9).
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    embedding BLOB NOT NULL,
    answer TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


class SemanticAnswerCache:
    """Cosine-similarity answer cache; failures never propagate to callers."""

    def __init__(
        self,
        db_path: Path,
        embedder_provider: Callable[[], object | None],
        *,
        enabled: bool,
        ttl_days: int,
        max_rows: int,
        threshold: float,
    ) -> None:
        self.db_path = Path(db_path)
        self.embedder_provider = embedder_provider
        self.enabled = bool(enabled)
        self.ttl_days = int(ttl_days)
        self.max_rows = int(max_rows)
        self.threshold = float(threshold)
        self._schema_ready = False

    # ── Plumbing ─────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Short-lived connection with the same pragmas as PersistenceService."""
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
        if self._schema_ready:
            return
        con.executescript(_SCHEMA)
        con.commit()
        self._schema_ready = True

    def _get_active_embedder(self):
        """Provider returning None is the universal 'cache disabled' signal."""
        return self.embedder_provider()

    def _embed_question(self, embedder, question: str) -> np.ndarray | None:
        """Embed and L2-normalize the RAW question (no expand_query, D9)."""
        vec = np.asarray(embedder.encode([question])[0], dtype=np.float32).ravel()
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return None
        return vec / norm

    def _cutoff_iso(self) -> str:
        return (
            datetime.utcnow() - timedelta(days=self.ttl_days)
        ).isoformat()

    # ── Public API ───────────────────────────────────────────

    def lookup(self, question: str) -> str | None:
        """Return the cached answer when cosine ≥ threshold, else None.

        Rows whose embedding dimension differs from the active embedder are
        ignored (model-mismatch guard). Never raises.
        """
        if not self.enabled:
            return None
        try:
            embedder = self._get_active_embedder()
            if embedder is None:
                return None
            query_vec = self._embed_question(embedder, question)
            if query_vec is None:
                return None

            con = self._connect()
            try:
                self._ensure_schema(con)
                rows = con.execute(
                    """
                    SELECT id, embedding, answer FROM semantic_cache
                    WHERE created_at >= ?
                    """,
                    (self._cutoff_iso(),),
                ).fetchall()
            finally:
                con.close()

            dim = query_vec.size
            ids: list[int] = []
            answers: list[str] = []
            vectors: list[np.ndarray] = []
            for row in rows:
                vec = np.frombuffer(row["embedding"], dtype=np.float32)
                if vec.size != dim:
                    logger.debug(
                        "Ignoring semantic-cache row %d: dimension mismatch "
                        "(stored=%d, active=%d)",
                        row["id"],
                        vec.size,
                        dim,
                    )
                    continue
                ids.append(row["id"])
                answers.append(row["answer"])
                vectors.append(vec)

            if not vectors:
                return None

            similarities = np.stack(vectors) @ query_vec
            best = int(np.argmax(similarities))
            if float(similarities[best]) >= self.threshold:
                self._bump_hit_count(ids[best])
                return answers[best]
            return None
        except Exception as e:
            logger.warning("Semantic cache lookup failed: %s", e)
            return None

    def store(self, question: str, answer: str) -> None:
        """Insert a question/answer pair, sweep TTL, enforce the FIFO cap."""
        if not self.enabled:
            return
        try:
            embedder = self._get_active_embedder()
            if embedder is None:
                return
            vec = self._embed_question(embedder, question)
            if vec is None:
                return

            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    con.execute(
                        """
                        INSERT INTO semantic_cache
                            (question, embedding, answer, hit_count, created_at)
                        VALUES (?, ?, ?, 0, ?)
                        """,
                        (
                            question,
                            vec.astype(np.float32).tobytes(),
                            answer,
                            datetime.utcnow().isoformat(),
                        ),
                    )
                    # TTL sweep + FIFO trim keep the table bounded
                    con.execute(
                        "DELETE FROM semantic_cache WHERE created_at < ?",
                        (self._cutoff_iso(),),
                    )
                    con.execute(
                        """
                        DELETE FROM semantic_cache WHERE id NOT IN (
                            SELECT id FROM semantic_cache
                            ORDER BY id DESC LIMIT ?
                        )
                        """,
                        (self.max_rows,),
                    )
            finally:
                con.close()
        except Exception as e:
            logger.warning("Semantic cache store failed: %s", e)

    def sweep_expired(self) -> int:
        """Delete rows older than ttl_days; return count removed. Never raises."""
        if not self.enabled:
            return 0
        try:
            con = self._connect()
            try:
                self._ensure_schema(con)
                with con:
                    cur = con.execute(
                        "DELETE FROM semantic_cache WHERE created_at < ?",
                        (self._cutoff_iso(),),
                    )
                    return cur.rowcount
            finally:
                con.close()
        except Exception as e:
            logger.warning("Semantic cache TTL sweep failed: %s", e)
            return 0

    def _bump_hit_count(self, row_id: int) -> None:
        try:
            con = self._connect()
            try:
                with con:
                    con.execute(
                        "UPDATE semantic_cache SET hit_count = hit_count + 1 "
                        "WHERE id = ?",
                        (row_id,),
                    )
            finally:
                con.close()
        except Exception as e:
            logger.warning("Semantic cache hit-count update failed: %s", e)
