"""Tests for SemanticAnswerCache — Cap-3 instant paraphrase answers.

Spec: Semantic Answer Cache — Cache Store · Similarity Lookup Before LLM ·
Mandatory Guardrails · Embedding Stability Guard. Uses a numpy fake embedder;
no model is ever loaded.
"""

import hashlib
import logging
import sqlite3
from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.services.semantic_cache import SemanticAnswerCache


DIM = 8


def _unit(vec):
    vec = np.asarray(vec, dtype=np.float32)
    return vec / np.linalg.norm(vec)


class FakeEmbedder:
    """Deterministic stub embedder: table-driven with hash-based fallback."""

    def __init__(self, dim=DIM, table=None):
        self.dim = dim
        self.table = dict(table or {})

    def encode(self, texts, **kwargs):
        out = []
        for text in texts:
            if text in self.table:
                vec = np.asarray(self.table[text], dtype=np.float32)
            else:
                seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
                rng = np.random.default_rng(seed)
                vec = rng.standard_normal(self.dim).astype(np.float32)
            out.append(_unit(vec))
        return np.array(out, dtype=np.float32)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cache.db"


@pytest.fixture(autouse=True)
def _quiet(caplog):
    caplog.set_level(logging.WARNING, logger="backend.services.semantic_cache")
    yield


_DEFAULT_EMBEDDER = object()  # sentinel: substitute FakeEmbedder only for this


def make_cache(db_path, embedder=_DEFAULT_EMBEDDER, *, enabled=True, ttl_days=14,
               max_rows=500, threshold=0.93):
    # An EXPLICIT None means "provider returns None" (TF-IDF / uninit case)
    if embedder is _DEFAULT_EMBEDDER:
        embedder = FakeEmbedder()
    return SemanticAnswerCache(
        db_path,
        lambda: embedder,
        enabled=enabled,
        ttl_days=ttl_days,
        max_rows=max_rows,
        threshold=threshold,
    )


def _row_count(db_path):
    """Row count; a never-created table counts as 0 (disabled-cache no-op)."""
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


class TestStoreAndLookup:
    """Core roundtrip and threshold behavior (design D9 mechanics)."""

    def test_store_lookup_roundtrip_hit_count_zero(self, db_path):
        """A stored first-turn answer comes back verbatim; hit_count starts at 0."""
        cache = make_cache(db_path)
        cache.store("What technologies did you use?", "Mostly Python and FastAPI.")

        assert _row_count(db_path) == 1

        con = sqlite3.connect(str(db_path))
        try:
            hits = con.execute(
                "SELECT hit_count FROM semantic_cache"
            ).fetchone()[0]
        finally:
            con.close()
        # Spec: row exists with hit_count=0 right after the store, before any lookup
        assert hits == 0

        assert cache.lookup("What technologies did you use?") == (
            "Mostly Python and FastAPI."
        )

    def test_hit_at_or_above_threshold_served(self, db_path):
        """A paraphrase at 0.95 cosine (≥ 0.93) serves the stored answer."""
        base = np.zeros(DIM, dtype=np.float32)
        base[0] = 1.0
        theta = float(np.arccos(0.95))
        paraphrase = _unit([np.cos(theta), np.sin(theta)] + [0.0] * (DIM - 2))

        embedder = FakeEmbedder(table={
            "original question": base,
            "rephrased question": paraphrase,
        })
        cache = make_cache(db_path, embedder)

        cache.store("original question", "Cached answer text.")
        assert cache.lookup("rephrased question") == "Cached answer text."

    def test_miss_below_threshold_returns_none(self, db_path):
        """A 0.92-cosine question sits below the 0.93 threshold — no hit."""
        base = np.zeros(DIM, dtype=np.float32)
        base[0] = 1.0
        theta = float(np.arccos(0.92))
        lookalike = _unit([np.cos(theta), np.sin(theta)] + [0.0] * (DIM - 2))

        embedder = FakeEmbedder(table={
            "original question": base,
            "lookalike question": lookalike,
        })
        cache = make_cache(db_path, embedder)

        cache.store("original question", "Cached answer text.")
        assert cache.lookup("lookalike question") is None


class TestGuardrails:
    """TTL expiry, FIFO cap, and kill switch (spec: Mandatory Guardrails)."""

    def test_ttl_expired_row_never_served(self, db_path):
        """Rows older than ttl_days are never served and are swept away."""
        cache = make_cache(db_path, ttl_days=14)
        cache.store("old question", "Stale answer.")

        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "UPDATE semantic_cache SET created_at = ?",
                ("2020-01-01T00:00:00",),
            )
            con.commit()
        finally:
            con.close()

        # Identical wording, but expired → never served
        assert cache.lookup("old question") is None
        # Periodic TTL sweep removes the expired row
        assert cache.sweep_expired() == 1
        assert cache.sweep_expired() == 0  # nothing left to sweep
        assert _row_count(db_path) == 0

    def test_fifo_cap_500_trims_oldest(self, db_path):
        """Inserting past the 500-row cap evicts the OLDEST rows first."""
        cache = make_cache(db_path, max_rows=500)
        total = 505
        for i in range(total):
            cache.store(f"question number {i}", f"answer {i}")

        assert _row_count(db_path) == 500, "cap must be enforced"

        con = sqlite3.connect(str(db_path))
        try:
            questions = [
                r[0]
                for r in con.execute(
                    "SELECT question FROM semantic_cache ORDER BY id"
                )
            ]
        finally:
            con.close()

        # Oldest five evicted, newest 500 kept (FIFO, not random)
        assert "question number 0" not in questions
        assert "question number 4" not in questions
        assert questions[0] == "question number 5"
        assert questions[-1] == f"question number {total - 1}"

    def test_kill_switch_disables_lookup_and_store(self, db_path):
        """SEMANTIC_CACHE_ENABLED=false: zero overhead — provider never asked."""
        spy_provider = MagicMock(return_value=FakeEmbedder())
        cache = SemanticAnswerCache(
            db_path,
            spy_provider,
            enabled=False,
            ttl_days=14,
            max_rows=500,
            threshold=0.93,
        )

        assert cache.lookup("anything") is None
        cache.store("anything", "answer")  # must be a silent no-op

        spy_provider.assert_not_called(), "kill switch must short-circuit early"
        assert _row_count(db_path) == 0

    def test_embedder_none_tfidf_disables_cache(self, db_path):
        """Provider returning None (TF-IDF fallback / uninit) disables everything."""
        cache = make_cache(db_path, embedder=None)

        cache.store("question", "answer")  # no-op
        assert cache.lookup("question") is None
        assert _row_count(db_path) == 0, "TF-IDF vectors must never be stored"

    def test_hit_count_increments_on_hit(self, db_path):
        """Each served hit increments the row's hit_count."""
        cache = make_cache(db_path)
        cache.store("popular question", "Popular answer.")

        assert cache.lookup("popular question") == "Popular answer."
        assert cache.lookup("popular question") == "Popular answer."

        con = sqlite3.connect(str(db_path))
        try:
            hits = con.execute(
                "SELECT hit_count FROM semantic_cache"
            ).fetchone()[0]
        finally:
            con.close()
        assert hits == 2


class TestEmbeddingStabilityGuard:
    """Model/dimension mismatch handling (spec: Embedding Stability Guard)."""

    def test_model_dimension_mismatch_rows_ignored(self, db_path):
        """Stored 8-dim rows are invisible to a different-dimension model."""
        cache_8d = make_cache(db_path, FakeEmbedder(dim=8))
        cache_8d.store("same words", "Old-model answer.")
        assert _row_count(db_path) == 1

        cache_4d = make_cache(db_path, FakeEmbedder(dim=4))
        assert cache_4d.lookup("same words") is None, (
            "mismatched-dimension rows must never be served"
        )

    def test_failure_returns_sentinel_logged(self, db_path, monkeypatch, caplog):
        """Broken embedder or broken DB → sentinel + warning, never raised."""
        def exploding_provider():
            raise RuntimeError("embedder exploded")

        broken_provider_cache = SemanticAnswerCache(
            db_path,
            exploding_provider,
            enabled=True,
            ttl_days=14,
            max_rows=500,
            threshold=0.93,
        )

        assert broken_provider_cache.lookup("q") is None
        broken_provider_cache.store("q", "a")  # must swallow too
        warnings = [
            r for r in caplog.records
            if "embedder exploded" in r.getMessage()
        ]
        assert warnings, "provider failures must be logged"

        # Dead database also degrades to sentinels
        healthy = make_cache(db_path)

        def dead_connect():
            raise RuntimeError("db gone")

        monkeypatch.setattr(healthy, "_connect", dead_connect)
        assert healthy.lookup("q") is None
        healthy.store("q", "a")  # swallowed
        assert healthy.sweep_expired() == 0
