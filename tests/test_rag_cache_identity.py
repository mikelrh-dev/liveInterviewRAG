"""The embedding cache must refuse to serve vectors it cannot vouch for.

THE DEFECT THIS GUARDS
----------------------
The cache's metadata recorded the model name, the document hash, the chunk
filter version and the chunk count -- and NOT which embedder actually produced
the numbers. Two consequences, both silent:

* **A fallback run poisoned the cache for everyone after it.** When
  sentence-transformers is unavailable the pipeline falls back to TF-IDF and
  writes its vectors into the same ``embeddings.npz``, tagged
  ``all-MiniLM-L6-v2``. The next run -- a real one, with the real model --
  matched that tag and restored TF-IDF numbers as if they were embeddings.
  Measured on the real corpus: 0 of 121 cached vectors equalled genuine MiniLM
  output and ``retrieve("como testias tu codigo")`` returned ``[]``. Every
  question was answered with zero grounding from the candidate's own profile,
  and the only trace was an INFO line.
* **A different chunking was served as if it were this one.** ``chunk_size``
  and ``chunk_overlap`` were absent from the key, so a 400/50 pipeline was
  served 124 chunks where a genuine 400/50 ingest produces 121.

The model name itself was also a fiction in one direction: ``initialize()``
hardcoded ``SentenceTransformer("all-MiniLM-L6-v2")`` and never read
``self._embedding_model``, even though that field is what the cache was tagged
with and validated against. So ``EMBEDDING_MODEL`` configured nothing.

WHAT IS ASSERTED HERE
---------------------
* A run that fell back to TF-IDF leaves nothing a later run can restore, and a
  later real run's vectors are the real embedder's.
* A cache that does not say which embedder wrote it -- the shape every
  already-poisoned cache on disk has -- is rejected and recomputed.
* Changing the chunking does not serve the previous chunking's chunks.
* A cache that does match is still served, without recomputing.
* The configured model name is the model that gets loaded.
"""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from backend.services.rag import RAGPipeline

DIMENSIONS = 384

# Long enough that a 60-word ceiling actually splits it and a 400-word one
# does not, so the two chunkings are distinguishable by content and not only
# by count.
LONG_DOC = "---\ntype: faq\nconfidence: high\n---\n\n# Resultados\n\n" + (
    " ".join(f"palabra{i}" for i in range(300))
)


def _fake_module(dimensions=DIMENSIONS, available=True, record=None):
    """A stand-in for ``sentence_transformers`` that needs no model download.

    The embedder is deterministic per text, so a test can assert that a chunk's
    vector is the one THIS embedder produced rather than merely that some
    vector exists.
    """
    module = types.ModuleType("sentence_transformers")

    def _vector(text):
        seed = abs(hash(text)) % (2**31)
        generator = np.random.default_rng(seed)
        raw = generator.random(dimensions).astype(np.float32)
        return raw / np.linalg.norm(raw)

    class SentenceTransformer:
        def __init__(self, name):
            self.name = name
            if record is not None:
                record.append(name)
            if not available:
                raise RuntimeError("sentence-transformers is not installed here")

        def encode(self, texts, show_progress_bar=False):
            return np.stack([_vector(text) for text in texts])

    module.SentenceTransformer = SentenceTransformer
    return module


@pytest.fixture
def sentence_transformers(monkeypatch):
    """Install the fake embedder and hand back the list of models requested."""

    def _install(*, available=True):
        requested: list = []
        monkeypatch.setitem(
            sys.modules, "sentence_transformers",
            _fake_module(available=available, record=requested),
        )
        return requested

    return _install


def _read_metadata(cache_dir: Path) -> dict:
    return json.loads((cache_dir / "embeddings.json").read_text(encoding="utf-8"))


def _count_recomputes(rag: RAGPipeline) -> list:
    """Count how many times this pipeline recomputes vectors instead of reading.

    ``ingest_documents`` initialises before it consults the cache, so "the model
    was loaded" says nothing about whether the cache was used. This is the step
    that distinguishes the two.
    """
    calls: list = []
    original = rag._compute_embeddings

    def counted():
        calls.append(1)
        return original()

    rag._compute_embeddings = counted
    return calls


class TestAFallbackRunCannotPoisonTheCache:
    def test_a_tfidf_run_leaves_nothing_a_real_run_would_restore(
        self, tmp_path, sentence_transformers
    ):
        """The finding's own scenario, end to end.

        TF-IDF vectors are not embeddings and the cache has no way to store the
        fitted vectorizer that would be needed to interpret them, so a fallback
        run has nothing worth writing and nothing anyone can read.
        """
        sentence_transformers(available=False)
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        fallback = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        fallback.ingest_documents(documents)
        assert fallback._use_tfidf, "the fallback was not exercised"

        requested = sentence_transformers(available=True)
        real = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        real.ingest_documents(documents)

        assert real.embedder is not None, "the real run did not load an embedder"
        assert not real._use_tfidf
        for chunk in real.chunks:
            assert chunk.embedding.shape == (DIMENSIONS,), (
                f"{chunk.id} came back with a {chunk.embedding.shape} vector, "
                "which is the fallback's geometry rather than the embedder's"
            )
            expected = real.embedder.encode([chunk.content])[0]
            assert np.allclose(chunk.embedding, expected), (
                f"{chunk.id} does not carry this embedder's vector: the cache "
                "served numbers from a different embedder"
            )

    def test_the_cache_records_which_embedder_wrote_it(self, tmp_path,
                                                      sentence_transformers):
        """Tagging has to be honest for validation to mean anything."""
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir).ingest_documents(
            {"cv.md": LONG_DOC}
        )

        meta = _read_metadata(cache_dir)
        assert meta.get("embedder") == "sentence-transformer:all-MiniLM-L6-v2", (
            f"the cache does not say which embedder produced these vectors: {meta}"
        )
        assert meta.get("model") == "all-MiniLM-L6-v2"
        assert meta.get("chunk_size") == 400
        assert meta.get("chunk_overlap") == 50


class TestTheKeyCoversEverythingThatChangesTheVectors:
    def test_a_cache_that_does_not_name_its_embedder_is_rejected(
        self, tmp_path, sentence_transformers
    ):
        """The migration guard, and the reason the mode is in the key.

        Every cache written before this fix has no ``embedder`` field -- including
        every one that was written by a TF-IDF fallback and tagged with the
        sentence-transformer name. The only safe reading of a cache that does not
        say what produced it is "recompute".
        """
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        first = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        first.ingest_documents(documents)

        # Reproduce the old writer's metadata shape and its geometry.
        meta_path = cache_dir / "embeddings.json"
        meta = _read_metadata(cache_dir)
        for absent in ("embedder", "chunk_size", "chunk_overlap"):
            meta.pop(absent, None)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        poisoned = np.load(cache_dir / "embeddings.npz", allow_pickle=True)
        np.savez_compressed(
            cache_dir / "embeddings.npz",
            ids=poisoned["ids"],
            contents=poisoned["contents"],
            sources=poisoned["sources"],
            sections=poisoned["sections"],
            types=poisoned["types"],
            summaries=poisoned["summaries"],
            tags_json=poisoned["tags_json"],
            embeddings=np.zeros((len(poisoned["ids"]), 7), dtype=np.float32),
        )

        requested = sentence_transformers()
        second = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        second.ingest_documents(documents)

        for chunk in second.chunks:
            assert chunk.embedding.shape == (DIMENSIONS,), (
                f"{chunk.id} was restored from a cache that never said what "
                f"produced it: {chunk.embedding.shape}"
            )
        assert len(requested) == 1, (
            f"the embedder was loaded {len(requested)} times; the poisoned "
            "cache was served instead of recomputed"
        )

    def test_changing_the_chunk_size_does_not_serve_another_chunking(
        self, tmp_path, sentence_transformers
    ):
        """``chunk_size`` and ``chunk_overlap`` decide WHICH chunks exist."""
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        coarse = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        coarse.ingest_documents(documents)
        coarse_chunks = [c.content for c in coarse.chunks]
        assert len(coarse_chunks) == 1, "the fixture must not split at 400 words"

        fine = RAGPipeline(chunk_size=60, chunk_overlap=5, cache_dir=cache_dir)
        fine.ingest_documents(documents)
        fine_chunks = [c.content for c in fine.chunks]

        assert len(fine_chunks) > 1, "the fixture must split at 60 words"
        assert fine_chunks != coarse_chunks, (
            "the 60-word run served the 400-word run's chunks: the chunking is "
            "not part of the cache key"
        )

        # And the cache now records the chunking that is actually in it.
        assert _read_metadata(cache_dir)["chunk_size"] == 60

    def test_changing_the_chunk_overlap_does_not_serve_another_chunking(
        self, tmp_path, sentence_transformers
    ):
        """Overlap shifts where every chunk after the first starts."""
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        narrow = RAGPipeline(chunk_size=60, chunk_overlap=5, cache_dir=cache_dir)
        narrow.ingest_documents(documents)
        narrow_contents = [c.content for c in narrow.chunks]

        wide = RAGPipeline(chunk_size=60, chunk_overlap=30, cache_dir=cache_dir)
        wide.ingest_documents(documents)
        wide_contents = [c.content for c in wide.chunks]

        # What this configuration produces with no cache in the picture at all.
        sentence_transformers()
        scratch = RAGPipeline(chunk_size=60, chunk_overlap=30)
        scratch.ingest_documents(documents)

        assert len(narrow_contents) > 1, "the fixture must split at 60 words"
        assert _read_metadata(cache_dir)["chunk_overlap"] == 30
        assert wide_contents != narrow_contents, (
            "the 30-word-overlap run served the 5-word-overlap run's chunks"
        )
        assert wide_contents == [c.content for c in scratch.chunks], (
            "the 30-word-overlap run's chunks are not the ones that "
            "configuration produces from scratch"
        )

    def test_a_matching_cache_is_still_served_without_recomputing(
        self, tmp_path, sentence_transformers
    ):
        """The guard must not have become "never use the cache".

        The probe is whether the vectors were recomputed, not whether the
        embedder was loaded: ``ingest_documents`` initialises before it looks at
        the cache, so loading the model is not evidence of anything.
        """
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        sentence_transformers()
        first = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        first.ingest_documents(documents)
        written = [c.embedding for c in first.chunks]

        sentence_transformers()
        again = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir)
        recomputed = _count_recomputes(again)
        again.ingest_documents(documents)

        assert len(recomputed) == 0, (
            f"the vectors were recomputed {len(recomputed)} time(s) for a cache that "
            "matched exactly; the cache has stopped being a cache"
        )
        assert [c.content for c in again.chunks] == [c.content for c in first.chunks]
        assert all(
            np.allclose(a.embedding, b) for a, b in zip(again.chunks, written)
        ), "the restored chunks are not the ones that were written"

    def test_a_different_model_is_not_served(self, tmp_path, sentence_transformers):
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        documents = {"cv.md": LONG_DOC}

        RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir,
                    embedding_model="all-MiniLM-L6-v2").ingest_documents(documents)
        requested = sentence_transformers()
        other = RAGPipeline(chunk_size=400, chunk_overlap=50, cache_dir=cache_dir,
                            embedding_model="paraphrase-multilingual-MiniLM-L12-v2")
        recomputed = _count_recomputes(other)
        other.ingest_documents(documents)

        assert len(recomputed) == 1, (
            "another model's cache was served instead of recomputed: two "
            "embedding spaces are not interchangeable"
        )
        assert requested == ["paraphrase-multilingual-MiniLM-L12-v2"], (
            f"the second run loaded {requested!r}"
        )
        assert _read_metadata(cache_dir)["model"] == "paraphrase-multilingual-MiniLM-L12-v2"


class TestTheModelNameIsASingleSourceOfTruth:
    def test_the_configured_model_is_the_model_that_is_loaded(
        self, tmp_path, sentence_transformers
    ):
        """``initialize()`` hardcoded the name and ignored the configuration.

        ``main.py`` passes ``embedding_model=config.EMBEDDING_MODEL``, and the
        cache was tagged and validated against that same field -- so the one
        place it could have taken effect was the one place that never read it.
        Setting ``EMBEDDING_MODEL`` to a multilingual model to fix the
        English-query blind spot did nothing at all.
        """
        requested = sentence_transformers()
        RAGPipeline(
            chunk_size=400,
            chunk_overlap=50,
            embedding_model="paraphrase-multilingual-MiniLM-L12-v2",
        ).initialize()

        assert requested == ["paraphrase-multilingual-MiniLM-L12-v2"], (
            f"the pipeline loaded {requested!r} instead of the configured "
            "model: EMBEDDING_MODEL has no effect on the model that is loaded"
        )

    def test_the_default_is_still_the_default(self, sentence_transformers):
        requested = sentence_transformers()
        RAGPipeline().initialize()

        assert requested == ["all-MiniLM-L6-v2"]

    def test_the_loaded_model_matches_the_field_the_cache_is_tagged_with(
        self, tmp_path, sentence_transformers
    ):
        """The tag and the model must not be able to disagree.

        A cache is only as good as the claim in its metadata, and the claim is
        only worth anything if it names the object that did the work.
        """
        sentence_transformers()
        cache_dir = tmp_path / "cache"
        rag = RAGPipeline(
            chunk_size=400, chunk_overlap=50, cache_dir=cache_dir,
            embedding_model="paraphrase-multilingual-MiniLM-L12-v2",
        )
        rag.ingest_documents({"cv.md": LONG_DOC})

        meta = _read_metadata(cache_dir)
        assert meta["model"] == "paraphrase-multilingual-MiniLM-L12-v2"
        assert rag.embedder is not None and rag.embedder.name == meta["model"]
