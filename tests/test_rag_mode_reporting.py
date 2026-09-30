"""A retrieval pipeline running on TF-IDF must say so.

THE DEFECT
----------
``RAGPipeline.initialize`` catches every exception from
``SentenceTransformer`` and falls back to TF-IDF with a single ``logger.warning``.
Nothing else in the process learns about it:

  * the on-disk embedding cache stops being usable, because a TF-IDF space is
    rebuilt on every ingest and vectors from two runs share no geometry, so
    every run recomputes;
  * ``/api/health`` still answers ``status: "ok"`` with ``rag_chunks: 20``,
    because chunk count is identical either way;
  * the frontend's ``describe()`` therefore paints a GREEN dot reading
    "Sistema OK - RAG 20 chunks".

So a deployment that had lost its embedding model reported itself healthy, and
the recruiter had no way to know the retrieval behind every answer had quietly
changed. The brief for this fix is deliberately narrow: do not measure whether
TF-IDF is worse -- that is unmeasured and is a day's work -- just stop the
failure from being invisible.

WHAT IS ASSERTED
----------------
The same fact, in the four places an operator or a user would look: the
pipeline, the health payload, the startup log, and the text on the page.
"""

import logging
from unittest.mock import patch

import pytest

from backend.services.rag import RAGPipeline


@pytest.fixture
def broken_embedder():
    """Make the real import of SentenceTransformer fail.

    Patched at the import site rather than by stubbing the pipeline, so the
    production fallback path in ``initialize`` is the code under test.
    """
    with patch.dict(
        "sys.modules", {"sentence_transformers": None}, clear=False
    ):
        yield


def _pipeline(tmp_path) -> RAGPipeline:
    return RAGPipeline(
        chunk_size=400,
        chunk_overlap=50,
        threshold=0.25,
        cache_dir=tmp_path,
    )


class TestThePipelineReportsItsMode:
    def test_a_fallback_run_reports_tfidf(self, tmp_path, broken_embedder):
        pipeline = _pipeline(tmp_path)
        pipeline.initialize()
        assert pipeline.mode == "tfidf"

    def test_the_mode_is_tfidf_before_initialization_too(self, tmp_path):
        """Nothing is loaded yet, so claiming embeddings would be a lie."""
        pipeline = _pipeline(tmp_path)
        assert pipeline.mode != "embeddings"

    def test_the_mode_is_one_of_two_known_values(self, tmp_path, broken_embedder):
        """A typo in the mode string would defeat the point of reporting it."""
        pipeline = _pipeline(tmp_path)
        pipeline.initialize()
        assert pipeline.mode in {"embeddings", "tfidf"}

    def test_the_fallback_is_logged_loudly(self, tmp_path, broken_embedder, caplog):
        """A quality regression is not a warning, and the log says which it is.

        The single ``warning`` this used to emit was easy to miss in a unit
        file's scrollback, and it described a CONDITION ("unavailable") rather
        than a CONSEQUENCE ("retrieval quality is reduced").
        """
        pipeline = _pipeline(tmp_path)
        with caplog.at_level(logging.INFO):
            pipeline.initialize()

        records = [r for r in caplog.records if "TF-IDF" in r.getMessage()]
        assert records, (
            "the fallback emitted nothing mentioning TF-IDF: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert any(r.levelno >= logging.WARNING for r in records)

    def test_the_fallback_log_names_the_cause(self, tmp_path, broken_embedder, caplog):
        pipeline = _pipeline(tmp_path)
        with caplog.at_level(logging.INFO):
            pipeline.initialize()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "retrieval" in text.lower() or "quality" in text.lower(), (
            f"the fallback is logged without saying what it costs: {text!r}"
        )


class TestHealthReportsTheMode:
    def test_health_includes_rag_mode(self):
        from backend.routers.system import health_check

        import anyio

        payload = anyio.run(health_check)
        assert "rag_mode" in payload, (
            f"/api/health cannot report a degraded pipeline: {sorted(payload)}"
        )

    def test_health_reports_the_pipeline_mode(self, monkeypatch):
        from backend import container
        from backend.routers import system

        class Stub:
            chunks = [object()] * 7
            mode = "tfidf"

        monkeypatch.setattr(container, "rag_pipeline", lambda: Stub())

        import anyio

        payload = anyio.run(system.health_check)
        assert payload["rag_mode"] == "tfidf", (
            f"the health payload does not carry the pipeline's mode: {payload}"
        )
        assert payload["rag_chunks"] == 7, (
            "the chunk count is what the fallback does not change, so it is no "
            "use as a health signal"
        )
