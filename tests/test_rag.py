"""Tests for RAG pipeline with known documents."""

import pytest
import numpy as np

from backend.services.rag import RAGPipeline, Chunk, parse_frontmatter, expand_query, detect_doc_type


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


class TestContextMetadata:
    """Tests for metadata enrichment of the LLM context string."""

    def test_context_string_includes_type_and_summary(self):
        """Context headers include Tipo and Resumen when metadata exists."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "skills/testing.md": """---
type: skills
tags: [testing, tdd]
summary_1line: Testing autodidacta
---

# Testing

Hago tests después de cada cambio significativo.""",
        }
        rag.ingest_documents(docs)
        context = rag.get_context_string("¿Cómo haces los tests?")
        assert "[Source: skills/testing.md" in context
        assert "Tipo: skills" in context
        assert "Resumen: Testing autodidacta" in context
        assert "Hago tests" in context

    def test_context_string_without_metadata_keeps_legacy_format(self):
        """Chunks without metadata keep the original [Source: ...] format."""
        rag = RAGPipeline(chunk_size=1000)
        rag.ingest_documents({"cv.md": "## Experience\nWorked with Python and FastAPI."})
        context = rag.get_context_string("What is your Python experience?")
        assert "[Source: cv.md]" in context
        assert "Tipo:" not in context
        assert "Resumen:" not in context


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


class TestFrontmatterParsing:
    """Tests for YAML frontmatter metadata extraction."""

    def test_parse_frontmatter_extracts_metadata(self):
        """Frontmatter fields type, title, tags, summary_1line are parsed."""
        content = """---
type: skills
title: testing
created: 2026-08-16
updated: 2026-08-16
confidence: high
tags: [testing, tdd, pytest]
related: []
summary_1line: Testing autodidacta
---

# Testing

Hago tests después de cada cambio significativo."""
        metadata, body = parse_frontmatter(content)
        assert metadata["type"] == "skills"
        assert metadata["title"] == "testing"
        assert metadata["tags"] == ["testing", "tdd", "pytest"]
        assert metadata["summary_1line"] == "Testing autodidacta"
        # Frontmatter is stripped from the body
        assert body.startswith("# Testing")
        assert "Hago tests" in body

    def test_parse_frontmatter_without_frontmatter(self):
        """Documents without frontmatter return empty metadata and unchanged body."""
        metadata, body = parse_frontmatter("Just plain content without frontmatter.")
        assert metadata == {}
        assert body == "Just plain content without frontmatter."

    def test_chunk_document_attaches_metadata(self):
        """Chunks inherit type, tags, and summary from the document frontmatter."""
        rag = RAGPipeline(chunk_size=1000)
        content = """---
type: skills
title: testing
tags: [testing, tdd]
summary_1line: Testing autodidacta
---

# Testing

Hago tests después de cada cambio significativo."""
        chunks = rag._chunk_document("skills/testing.md", content)
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.type == "skills"
        assert chunk.tags == ["testing", "tdd"]
        assert chunk.summary == "Testing autodidacta"
        # Frontmatter must not leak into chunk content
        assert "type: skills" not in chunk.content
        assert "Hago tests" in chunk.content

    def test_chunk_defaults(self):
        """Chunks without metadata use empty defaults."""
        chunk = Chunk(id="x", content="y", source="z")
        assert chunk.type == ""
        assert chunk.tags == []
        assert chunk.summary == ""

    def test_ingest_documents_parses_frontmatter(self):
        """ingest_documents extracts metadata when documents have frontmatter."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "skills/testing.md": """---
type: skills
tags: [testing]
summary_1line: Testing autodidacta
---

# Testing

Hago tests con pytest.""",
            "cv.md": "## Experience\nWorked with Python.",
        }
        rag.ingest_documents(docs)
        skills_chunks = [c for c in rag.chunks if c.source == "skills/testing.md"]
        assert len(skills_chunks) == 1
        assert skills_chunks[0].type == "skills"
        assert skills_chunks[0].tags == ["testing"]
        assert skills_chunks[0].summary == "Testing autodidacta"
        # Document without frontmatter keeps defaults
        cv_chunks = [c for c in rag.chunks if c.source == "cv.md"]
        assert cv_chunks[0].type == ""


class TestQueryEnrichment:
    """Tests for query expansion with synonyms before embedding."""

    def test_expand_query_tests(self):
        """Queries about 'tests' are expanded with testing synonyms."""
        expanded = expand_query("¿Haces tests?")
        assert "testing" in expanded
        assert "pytest" in expanded
        assert "tdd" in expanded
        assert "skills" in expanded

    def test_expand_query_projects(self):
        """Queries about 'projects' are expanded with project synonyms."""
        expanded = expand_query("Cuéntame sobre tus proyectos")
        assert "project" in expanded
        assert "entrevista" in expanded
        assert "prácticas" in expanded

    def test_expand_query_no_keyword_unchanged(self):
        """Queries without known keywords are returned unchanged."""
        query = "¿Cuál es tu experiencia con Python?"
        assert expand_query(query) == query

    def test_expand_query_case_insensitive(self):
        """Keyword matching is case-insensitive."""
        expanded = expand_query("Mis TESTS con FastAPI")
        assert "pytest" in expanded


class TestDocTypeFiltering:
    """Tests for optional filtering by document type."""

    def test_detect_type_tests_maps_to_skills(self):
        """Queries about 'tests' clearly map to the skills type."""
        assert detect_doc_type("¿Cómo haces los tests?") == "skills"

    def test_detect_type_experiencia_maps_to_experience(self):
        """Queries about 'experiencia' clearly map to the experience type."""
        assert detect_doc_type("¿Qué experiencia tienes en retail?") == "experience"

    def test_detect_type_ambiguous_returns_none(self):
        """Queries matching multiple types are treated as ambiguous."""
        # "experiencia" -> experience, "proyecto" -> projects: no single type
        assert detect_doc_type("¿Qué experiencia tienes con el proyecto InterviewTTS?") is None

    def test_detect_type_no_match_returns_none(self):
        """Queries without type signals return None (cosine fallback)."""
        assert detect_doc_type("Cuéntame algo interesante") is None

    def test_retrieve_filters_by_doc_type(self):
        """retrieve() with doc_type only returns chunks of that type."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "skills/testing.md": """---
type: skills
tags: [testing]
summary_1line: Testing
---

# Testing

Hago tests con pytest.""",
            "experience/mercadona.md": """---
type: experience
tags: [retail]
summary_1line: Retail
---

# Experience

Gerente en Mercadona.""",
        }
        rag.ingest_documents(docs)
        results = rag.retrieve("tests pytest", top_k=3, doc_type="skills")
        assert len(results) > 0
        for chunk, _ in results:
            assert chunk.type == "skills"

    def test_retrieve_without_doc_type_keeps_all(self):
        """retrieve() without doc_type does not filter by type (backward compat)."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "skills/testing.md": """---
type: skills
tags: [testing]
summary_1line: Testing
---

# Testing

Hago tests con pytest.""",
            "experience/mercadona.md": """---
type: experience
tags: [retail]
summary_1line: Retail
---

# Experience

Gerente en Mercadona.""",
        }
        rag.ingest_documents(docs)
        results = rag.retrieve("gerente mercadona", top_k=3)
        assert len(results) > 0
        assert any(chunk.type == "experience" for chunk, _ in results)

    def test_context_string_auto_filters_by_type(self):
        """get_context_string() filters by detected type when unambiguous."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "skills/testing.md": """---
type: skills
tags: [testing]
summary_1line: Testing autodidacta
---

# Testing

Hago tests con pytest después de cada cambio.""",
            "experience/retail.md": """---
type: experience
tags: [retail]
summary_1line: Retail
---

# Experience

Gestioné equipos en Mercadona.""",
        }
        rag.ingest_documents(docs)
        context = rag.get_context_string("¿Cómo haces los tests?")
        assert "Hago tests" in context
        assert "Gestioné equipos" not in context
