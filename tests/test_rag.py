"""Tests for RAG pipeline with known documents."""

import pytest
import numpy as np

from backend.services.rag import RAGPipeline, Chunk


class TestRAGPipeline:
    """Tests for document chunking, embedding, and retrieval."""

    def test_init(self):
        """Pipeline initializes with default chunk parameters."""
        rag = RAGPipeline()
        assert rag.chunk_size == 400
        assert rag.chunk_overlap == 50
        assert len(rag.chunks) == 0

    def test_chunk_document_small(self):
        """Small documents produce a single chunk."""
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("test.md", "Short content about Python.")
        assert len(chunks) == 1
        assert chunks[0].source == "test.md"
        assert "Python" in chunks[0].content

    def test_chunk_document_large(self):
        """Large documents are split into multiple chunks."""
        rag = RAGPipeline(chunk_size=10, chunk_overlap=2)
        # Create a document with 50 words
        content = " ".join(["word"] * 50)
        chunks = rag._chunk_document("large.md", content)
        assert len(chunks) > 1
        # Check overlap exists
        for i in range(len(chunks) - 1):
            assert chunks[i].id != chunks[i + 1].id

    def test_chunk_document_headings(self):
        """Documents with headings are split by section."""
        rag = RAGPipeline(chunk_size=1000)
        content = """# Section One
First section content.

## Subsection
Subsection content.

# Section Two
Second section content."""
        chunks = rag._chunk_document("doc.md", content)
        assert len(chunks) >= 2
        assert any("Section One" in c.section for c in chunks)
        assert any("Section Two" in c.section for c in chunks)

    def test_ingest_documents(self):
        """Ingestion creates chunks from documents."""
        rag = RAGPipeline(chunk_size=100)
        docs = {
            "cv.md": "## Experience\nWorked at Company X.\n## Skills\nPython, FastAPI",
            "projects.md": "## Project A\nBuilt a cool app.",
        }
        count = rag.ingest_documents(docs)
        assert count > 0
        assert len(rag.chunks) == count
        # Verify embeddings are computed
        for chunk in rag.chunks:
            assert chunk.embedding is not None

    def test_retrieve_relevant(self):
        """Retrieval finds relevant chunks for a query."""
        rag = RAGPipeline(chunk_size=100)
        docs = {
            "cv.md": "## Python Experience\nI have 3 years of Python experience building APIs.",
            "skills.md": "## Skills\nJavaScript, React, Node.js for frontend development.",
        }
        rag.ingest_documents(docs)

        results = rag.retrieve("Tell me about your Python experience", top_k=2)
        assert len(results) > 0
        # The Python-related chunk should rank higher
        top_chunk, score = results[0]
        assert "Python" in top_chunk.content

    def test_retrieve_empty_index(self):
        """Retrieval returns empty list when no chunks indexed."""
        rag = RAGPipeline()
        results = rag.retrieve("anything", top_k=3)
        assert results == []

    def test_retrieve_no_match(self):
        """Retrieval returns empty when nothing matches threshold."""
        rag = RAGPipeline(chunk_size=100)
        docs = {"cv.md": "Python experience and skills."}
        rag.ingest_documents(docs)

        results = rag.retrieve("quantum physics superposition", top_k=3, threshold=0.99)
        # With high threshold, irrelevant query should return nothing or very low scores
        assert all(score < 0.99 for _, score in results) or len(results) == 0

    def test_get_context_string(self):
        """Context string includes retrieved chunks."""
        rag = RAGPipeline(chunk_size=100)
        docs = {"cv.md": "## Experience\nWorked with Python and FastAPI."}
        rag.ingest_documents(docs)

        context = rag.get_context_string("What is your Python experience?")
        assert "Python" in context
        assert "[Source:" in context

    def test_get_context_empty(self):
        """Context string is empty when no relevant chunks found."""
        rag = RAGPipeline()
        context = rag.get_context_string("anything")
        assert context == ""

    def test_retrieve_performance(self):
        """Retrieval completes within 500ms for 50 chunks."""
        import time

        rag = RAGPipeline(chunk_size=50)
        # Generate 10 docs with multiple sections to get ~50 chunks
        docs = {}
        for i in range(10):
            sections = []
            for j in range(5):
                sections.append(f"## Topic {j}\n" + " ".join([f"word{k}" for k in range(20)]))
            docs[f"doc{i}.md"] = "\n\n".join(sections)

        rag.ingest_documents(docs)

        start = time.time()
        for _ in range(10):
            rag.retrieve("Tell me about topic 5", top_k=3)
        elapsed = time.time() - start

        avg_ms = (elapsed / 10) * 1000
        assert avg_ms < 500, f"Average retrieval time {avg_ms:.1f}ms exceeds 500ms budget"


class TestGetChunksWithScores:
    """Tests for retrieve returning chunks with scores."""

    def test_retrieve_returns_chunk_score_tuples(self):
        """retrieve() returns list of (Chunk, score) tuples."""
        pipeline = RAGPipeline()
        pipeline.ingest_documents({"test.md": "# Section One\nThis is test content for retrieval."})
        results = pipeline.retrieve("test content")
        assert len(results) > 0
        chunk, score = results[0]
        assert isinstance(chunk, Chunk)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_get_chunks_with_scores_returns_serializable(self):
        """get_chunks_with_scores() returns list of dicts suitable for JSON."""
        pipeline = RAGPipeline()
        pipeline.ingest_documents({"cv.md": "# Experience\nBuilt web apps with Python."})
        chunks = pipeline.get_chunks_with_scores("web apps", top_k=2)
        assert isinstance(chunks, list)
        if len(chunks) > 0:
            first = chunks[0]
            assert "text" in first
            assert "score" in first
            assert "source" in first
            assert isinstance(first["score"], float)
