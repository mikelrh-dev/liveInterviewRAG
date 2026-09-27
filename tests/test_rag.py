"""Tests for RAG pipeline with known documents."""

import json
import logging
import re
import numpy as np
import pytest
from pathlib import Path

from backend.services.rag import (
    DOC_TYPE_ALIASES,
    QUERY_TYPE_KEYWORDS,
    Chunk,
    RAGPipeline,
    canonical_doc_type,
    detect_doc_type,
    expand_query,
    parse_frontmatter,
)


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


class TestTypeNormalization:
    """Tests for project/projects type alias normalization (Bug 2 fix)."""

    def test_detect_project_singular(self):
        """detect_doc_type returns 'project' for singular form."""
        assert detect_doc_type("Cuéntame sobre tu proyecto") == "project"

    def test_detect_project_plural(self):
        """detect_doc_type returns 'project' for plural form (normalized)."""
        assert detect_doc_type("Cuéntame sobre tus proyectos") == "project"

    def test_retrieve_finds_chunks_with_singular_type(self):
        """Chunks with type='project' are found when filtering by 'project'."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "wiki/interviewtts.md": """---
type: project
tags: [interviewtts]
summary_1line: InterviewTTS portfolio project
---

# InterviewTTS

Portfolio project with voice AI.""",
        }
        rag.ingest_documents(docs)
        results = rag.retrieve("interviewtts project", top_k=3, doc_type="project")
        assert len(results) > 0
        assert results[0][0].type == "project"

    def test_retrieve_finds_chunks_with_plural_type_via_normalization(self):
        """Chunks with type='projects' are found when filtering by 'project'."""
        rag = RAGPipeline(chunk_size=1000, threshold=0.0)
        docs = {
            "wiki/projects.md": """---
type: projects
tags: [portfolio]
summary_1line: Portfolio projects
---

# Projects

Built several apps.""",
        }
        rag.ingest_documents(docs)
        # Query detects "project" (singular), but chunk has "projects" (plural)
        results = rag.retrieve("mis proyectos", top_k=3, doc_type="project")
        assert len(results) > 0
        assert results[0][0].type == "projects"

    def test_get_context_string_with_project_type(self):
        """get_context_string auto-detects project type and retrieves matching chunks."""
        rag = RAGPipeline(chunk_size=1000)
        docs = {
            "wiki/interviewtts.md": """---
type: project
tags: [interviewtts]
summary_1line: InterviewTTS
---

# InterviewTTS

App de entrevistas por voz.""",
        }
        rag.ingest_documents(docs)
        context = rag.get_context_string("¿Qué es InterviewTTS?")
        assert "entrevistas por voz" in context


# ── Item B: every real wiki `type:` must be reachable through a filter ──────
#
# Regression net.  QUERY_TYPE_KEYWORDS used plural keys ("stories", "opinions",
# "decisions") while the wiki frontmatter uses singular `type:` values
# ("story", "opinion", "decision").  retrieve() normalised only project↔projects,
# so a recruiter question about a decision, an opinion or a story produced a
# filter that matched nothing and returned [] *silently* — the LLM then answered
# with zero grounding from the candidate's own profile.

# The canonical types actually present in wiki/, each with a probe query built
# from a token that maps to exactly that type (so detect_doc_type is unambiguous).
_TYPE_PROBES = {
    "profile": "preséntate",
    "project": "portfolio",
    "experience": "retail",
    "skills": "frameworks",
    "story": "anécdota",
    "opinion": "crees",
    "decision": "decisión",
    "faq": "fortalezas",
}


def _doc_for_type(doc_type: str) -> str:
    """A minimal single-section document carrying the given frontmatter type."""
    return (
        f"---\n"
        f"type: {doc_type}\n"
        f"tags: [{doc_type}]\n"
        f"summary_1line: Contenido de tipo {doc_type}\n"
        f"---\n\n"
        f"# {doc_type}\n\n"
        f"Contenido único y relevante del tipo {doc_type} para la entrevista.\n"
    )


class TestDocTypeFilterCoverage:
    """Every real wiki type must produce a non-empty filtered candidate set."""

    def test_wiki_types_are_all_covered_by_the_type_mapping(self):
        """Read the real wiki/ frontmatter: no `type:` may be unreachable.

        This is the data-driven net. Adding a new `type:` to the wiki without
        extending the mapping must fail here, loudly, instead of silently
        producing ungrounded answers at interview time.
        """
        wiki_dir = Path(__file__).resolve().parent.parent / "wiki"
        real_types = set()
        for md in wiki_dir.rglob("*.md"):
            meta, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            raw = str(meta.get("type", "") or "").strip()
            if raw and "|" not in raw:  # CONVENCIONES.md lists all types
                real_types.add(raw)

        assert real_types, f"no wiki documents with a type: found under {wiki_dir}"
        unreached = {
            t for t in real_types if canonical_doc_type(t) != t or t not in _TYPE_PROBES
        }
        assert not unreached, (
            f"wiki types not covered by the doc_type mapping: {sorted(unreached)}. "
            f"Add them to DOC_TYPE_ALIASES and to _TYPE_PROBES so recruiter "
            f"questions about them stay grounded."
        )

    def test_each_real_type_yields_non_empty_candidates(self, caplog):
        """For each real type, a keywordised query must reach that type's chunks.

        Asserts the FILTER matched, not merely that results came back: the
        documented unfiltered fallback would otherwise satisfy a loose
        "non-empty" assertion and hide this very bug forever. Proof that the
        filter itself worked: every returned chunk is of the requested type, and
        no unmatched-filter warning was logged.
        """
        rag = RAGPipeline(chunk_size=1000, threshold=0.0)
        rag.ingest_documents(
            {f"{t}.md": _doc_for_type(t) for t in _TYPE_PROBES}
        )

        for doc_type, probe in _TYPE_PROBES.items():
            detected = detect_doc_type(probe)
            assert detected == doc_type, (
                f"probe {probe!r} should detect {doc_type!r}, got {detected!r}"
            )
            with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
                caplog.clear()
                results = rag.retrieve(probe, top_k=5, doc_type=detected)

            assert results, (
                f"type {doc_type!r} produced ZERO candidates for probe {probe!r} "
                f"— the filter cannot match, so the answer loses its grounding"
            )
            assert all(c.type == doc_type for c, _ in results), (
                f"type {doc_type!r} filter returned chunks of other types: "
                f"{[c.type for c, _ in results]}"
            )
            assert not [
                r for r in caplog.records if "matched no chunks" in r.getMessage()
            ], (
                f"type {doc_type!r} only reached results via the unfiltered "
                f"fallback — the filter itself still cannot match"
            )

    def test_plural_and_singular_spellings_both_reach_the_same_chunks(self):
        """Frontmatter may be singular or plural; both must reach the same type."""
        rag = RAGPipeline(chunk_size=1000, threshold=0.0)
        rag.ingest_documents({
            "story-a.md": _doc_for_type("story"),
            "opinion-a.md": _doc_for_type("opinion"),
            "decision-a.md": _doc_for_type("decision"),
        })

        for plural, probe in [
            ("stories", "anécdota"),
            ("opinions", "crees"),
            ("decisions", "decisión"),
        ]:
            canonical = canonical_doc_type(plural)
            results = rag.retrieve(probe, top_k=5, doc_type=canonical)
            assert results, f"plural spelling {plural!r} must reach canonical {canonical!r}"
            assert all(canonical_doc_type(c.type) == canonical for c, _ in results)

    def test_no_query_type_key_is_absent_from_the_wiki(self):
        """QUERY_TYPE_KEYWORDS keys must be real, resolvable document types."""
        for key in QUERY_TYPE_KEYWORDS:
            assert key in DOC_TYPE_ALIASES, (
                f"QUERY_TYPE_KEYWORDS key {key!r} is not a canonical document "
                f"type — detect_doc_type would emit a filter that matches nothing"
            )


class TestUnmatchedDocTypeIsLoud:
    """A filter that matches nothing must warn and fall back, not vanish."""

    def test_unmatched_filter_logs_warning_naming_types_and_falls_back(
        self, caplog
    ):
        rag = RAGPipeline(chunk_size=1000, threshold=0.0)
        rag.ingest_documents({
            "skills/testing.md": _doc_for_type("skills"),
            "faq/area.md": _doc_for_type("faq"),
        })

        with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
            results = rag.retrieve("algo sin filtro valido", top_k=5,
                                   doc_type="decision")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "an unmatched doc_type filter must be logged, not silent"
        message = warnings[0].getMessage()
        assert "decision" in message, "the warning must name the requested type"
        assert "skills" in message and "faq" in message, (
            "the warning must name the available types so the owner can fix the "
            "mapping: %r" % message
        )
        # Documented fallback: unfiltered retrieval, so the answer stays grounded
        assert results, "unmatched filter must fall back to unfiltered retrieval"
        assert {canonical_doc_type(c.type) for c, _ in results} == {"skills", "faq"}

    def test_matched_filter_does_not_warn(self, caplog):
        """A filter that matches must stay quiet — no warning spam per request."""
        rag = RAGPipeline(chunk_size=1000, threshold=0.0)
        rag.ingest_documents({"story-a.md": _doc_for_type("story")})

        with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
            assert rag.retrieve("anécdota", top_k=3, doc_type="story")

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestPlaceholderStripping:
    """Item C: the RAG must not serve `[TODO` placeholders to the LLM.

    `_chunk_document` used to strip only the frontmatter, then split by headings
    and tokens — filtering nothing, and never reading the `confidence` field.
    Measured over the real wiki: 38 documents -> 214 chunks, 11 of them
    containing a literal `[TODO`, inside sections titled "Outcomes" and "What I'd
    do differently" — exactly what a recruiter asks about. The LLM could read a
    TODO aloud, or invent the missing figure, with nothing in the wiki to
    contradict it.

    The wiki is the candidate's own data and is NEVER modified: this changes how
    the RAG *reads* it, and the per-document warning names the file so the owner
    can find the hole.
    """

    TODO_DOC = """---
type: project
confidence: high
summary_1line: InterviewTTS
---

# InterviewTTS

Entrevista por voz en tiempo real con FastAPI y WebSockets.

## Outcomes

- [TODO: ask Mikel] — Any metrics? (e.g., response latency, user testing?)

## What I'd do differently

- [TODO: ask Mikel] — What would you change about InterviewTTS?
- Persistiría las conversaciones en SQLite desde el primer día.
"""

    def test_todo_document_yields_no_todo_chunks(self):
        """A document with [TODO ...] must yield zero chunks containing '[TODO'."""
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/interview-tts.md", self.TODO_DOC)
        assert chunks, "the real content must survive"
        assert not [c for c in chunks if "[TODO" in c.content], (
            "the RAG is still serving literal [TODO placeholders to the LLM"
        )

    def test_real_content_survives_todo_removal(self):
        """Only the placeholder goes; the candidate's real answers stay."""
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/interview-tts.md", self.TODO_DOC)
        joined = "\n".join(c.content for c in chunks)
        assert "Entrevista por voz en tiempo real con FastAPI" in joined
        assert "SQLite desde el primer día" in joined, (
            "removing a placeholder must not take the surrounding answer with it"
        )
        assert "Any metrics?" not in joined, (
            "the placeholder's question text must go too — it invites the LLM "
            "to invent the missing figure"
        )

    def test_todo_removal_leaves_no_punctuation_artifacts(self):
        """Stripping a placeholder must not leave orphaned bullets or dashes."""
        rag = RAGPipeline(chunk_size=1000)
        doc = """---
type: story
confidence: high
---

# Historia

- Un logro real y verificable del candidato.
- [TODO: ask Mikel] — Something the owner must still fill in?
- Otro logro real y verificable.
"""
        chunks = rag._chunk_document("stories/x.md", doc)
        for c in chunks:
            assert not re.search(r"^\s*[-*]\s*[-*—–:]\s*$", c.content, re.MULTILINE), (
                f"orphaned bullet/punctuation left behind: {c.content!r}"
            )
            assert "---" not in c.content, f"orphaned rule left behind: {c.content!r}"
            assert not re.search(r"\s—\s*$", c.content), (
                f"dangling em dash left behind: {c.content!r}"
            )

    def test_confidence_low_page_contributes_no_chunks(self, caplog):
        """`confidence: low` is placeholder content per wiki/CONVENCIONES.md."""
        doc = """---
type: story
confidence: low
summary_1line: Borrador sin confirmar
---

# Historia

Contenido inferido por IA que el dueño aún no ha confirmado.
"""
        rag = RAGPipeline(chunk_size=1000)
        with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
            chunks = rag._chunk_document("stories/draft.md", doc)
        assert chunks == [], (
            "a confidence:low page must contribute no chunks — per the wiki's own "
            "convention it is draft/placeholder content, not the candidate's truth"
        )
        assert any("draft.md" in r.getMessage() for r in caplog.records), (
            "dropping a whole page must be logged, naming the file"
        )

    def test_confidence_medium_page_still_contributes_its_content(self, caplog):
        """Over-filtering guard: `medium` is reviewed real content, not a hole.

        Per wiki/CONVENCIONES.md, `medium` means "Reviewed but not tested /
        inferred from the codebase". Dropping it would silently break the
        candidate's answers — this test exists to stop that from happening.
        """
        doc = """---
type: project
confidence: medium
summary_1line: Proyecto revisado
---

# Proyecto

Contenido revisado por el dueño; real y aprovechable en la entrevista.
"""
        rag = RAGPipeline(chunk_size=1000)
        with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
            chunks = rag._chunk_document("projects/medium.md", doc)
        assert chunks, "a confidence:medium page MUST still contribute its content"
        assert any("revisado por el dueño" in c.content for c in chunks)
        assert not [
            r for r in caplog.records
            if "medium.md" in r.getMessage() and r.levelno >= logging.WARNING
        ], "a healthy medium page must not be reported as filtered"

    def test_document_without_confidence_is_untouched(self):
        """No `confidence:` field at all means unstated, not low."""
        doc = "---\ntype: faq\n---\n\n# FAQ\n\nRespuesta real sin campo confidence.\n"
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("faq/x.md", doc)
        assert chunks and any("Respuesta real" in c.content for c in chunks)

    def test_stripped_content_is_logged_once_per_document(self, caplog):
        """The owner must learn the wiki still has holes, once, findably."""
        rag = RAGPipeline(chunk_size=1000)
        with caplog.at_level(logging.WARNING, logger="backend.services.rag"):
            rag._chunk_document("projects/interview-tts.md", self.TODO_DOC)
        hits = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "interview-tts.md" in r.getMessage()
        ]
        assert len(hits) == 1, (
            f"expected exactly one warning for the document, got {len(hits)}"
        )

    def test_clean_document_chunks_exactly_as_before(self):
        """No regression in chunk count, boundaries, or content for clean input."""
        doc = """---
type: project
confidence: high
tags: [python]
summary_1line: Proyecto limpio
---

# Proyecto

Una primera seccion con contenido real.

## Subseccion

Contenido de la subseccion, tambien real y verificado.
"""
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/clean.md", doc)

        # Pinned to the pre-existing output: the heading split regex yields two
        # sections, and both must come through byte-identical.
        assert len(chunks) == 2, "clean input must chunk exactly as before"
        assert [c.id for c in chunks] == [
            "projects/clean.md-0", "projects/clean.md-1",
        ]
        assert [c.section for c in chunks] == ["Proyecto", "Subseccion"]
        assert [c.type for c in chunks] == ["project", "project"]
        assert [c.tags for c in chunks] == [["python"], ["python"]]
        assert [c.summary for c in chunks] == ["Proyecto limpio"] * 2
        assert [c.content for c in chunks] == [
            "# Proyecto\n\nUna primera seccion con contenido real.",
            "## Subseccion\n\nContenido de la subseccion, tambien real y verificado.",
        ]

    def test_large_clean_document_split_is_unchanged(self):
        """Token-overlap splitting for clean input must not shift either."""
        rag = RAGPipeline(chunk_size=10, chunk_overlap=2)
        chunks = rag._chunk_document("clean.md", " ".join(["palabra"] * 50))
        assert len(chunks) == 7  # unchanged boundary arithmetic
        assert all("palabra" in c.content for c in chunks)

    def test_section_emptied_by_stripping_keeps_its_heading(self):
        """Deliberate: a section that held only a placeholder keeps its heading.

        The heading is real structure ("What I'd do differently"), so dropping
        it would erase a topic the candidate does have. A bare heading is inert
        for retrieval — it states nothing the candidate did not — and the corpus
        already carries 36 such heading-only chunks, so this is the established
        shape rather than a new one.
        """
        doc = """---
type: project
confidence: high
---

# Proyecto

Contenido real del proyecto.

## What I'd do differently

- [TODO: ask Mikel] — What would you change about InterviewTTS?
"""
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/x.md", doc)
        emptied = [c for c in chunks if "differently" in c.content]
        assert emptied, "the emptied section should survive as its heading"
        assert emptied[0].content.strip() == "## What I'd do differently"
        assert not any("[TODO" in c.content for c in chunks)

    def test_stale_cache_carrying_todo_content_is_rejected(self, tmp_path):
        """A cache built before filtering must not be served after it.

        The cache document_hash covers the RAW wiki text, which this change does
        not touch. Without a filter-version guard the old, unfiltered cache would
        be restored and every fix here silently undone.
        """
        cache_dir = tmp_path / "cache"
        docs = {"projects/t.md": self.TODO_DOC}
        old = RAGPipeline(chunk_size=100, cache_dir=cache_dir)
        old.ingest_documents(docs)

        meta_path = cache_dir / "embeddings.json"
        meta = json.loads(meta_path.read_text())
        meta.pop("chunk_filter_version", None)  # simulate a pre-fix cache
        meta_path.write_text(json.dumps(meta))

        fresh = RAGPipeline(chunk_size=100, cache_dir=cache_dir)
        fresh.ingest_documents(docs)
        assert not [c for c in fresh.chunks if "[TODO" in c.content], (
            "a stale pre-filter cache was restored and still carries [TODO"
        )
        assert "chunk_filter_version" in json.loads(meta_path.read_text())


class TestWikiCorpusHasNoPlaceholders:
    """The real corpus must reach the LLM free of [TODO placeholders."""

    def _real_wiki_documents(self) -> dict:
        from backend.services.candidate import CandidateProfile

        profile = CandidateProfile(
            Path(__file__).resolve().parent.parent / "candidate",
            wiki_dir=Path(__file__).resolve().parent.parent / "wiki",
        )
        profile.load()
        assert profile.documents, "the real wiki must still load"
        return profile.documents

    def test_no_chunk_from_the_real_wiki_contains_todo(self):
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        documents = self._real_wiki_documents()
        offenders = []
        total = 0
        for name, content in documents.items():
            for c in rag._chunk_document(name, content):
                total += 1
                if "[TODO" in c.content:
                    offenders.append((c.source, c.section))
        assert not offenders, (
            f"{len(offenders)} chunk(s) from the real wiki still contain [TODO: "
            f"{offenders}"
        )
        assert total > 100, f"expected a substantial corpus, chunked {total}"


class TestEmbeddingCache:
    """Tests for embedding cache save/load and invalidation."""

    DOCS = {
        "cv.md": "## Experience\nWorked with Python and FastAPI.",
        "skills.md": "## Skills\nJavaScript, React for frontend.",
    }

    def _make_rag(self, tmp_path: Path, model: str = "all-MiniLM-L6-v2") -> RAGPipeline:
        return RAGPipeline(
            chunk_size=100,
            cache_dir=tmp_path / "cache",
            embedding_model=model,
        )

    def test_first_run_creates_cache(self, tmp_path):
        """Test first run computes and saves cache."""
        rag = self._make_rag(tmp_path)
        count = rag.ingest_documents(self.DOCS)
        assert count > 0

        # Cache files should exist
        cache_dir = tmp_path / "cache"
        assert (cache_dir / "embeddings.npz").exists()
        assert (cache_dir / "embeddings.json").exists()

        # Metadata should be valid
        with open(cache_dir / "embeddings.json") as f:
            meta = json.load(f)
        assert meta["model"] == "all-MiniLM-L6-v2"
        assert meta["chunk_count"] == count
        assert len(meta["document_hash"]) == 64  # SHA-256 hex

    def test_cache_hit_reuses_embeddings(self, tmp_path):
        """Test cache hit restores chunks without recompute."""
        rag1 = self._make_rag(tmp_path)
        rag1.ingest_documents(self.DOCS)

        # Second ingest with same docs should use cache
        rag2 = self._make_rag(tmp_path)
        count = rag2.ingest_documents(self.DOCS)
        assert count > 0

        # Embeddings should be loaded and identical
        for chunk in rag2.chunks:
            assert chunk.embedding is not None

    def test_cache_invalidation_on_doc_change(self, tmp_path):
        """Test stale cache is discarded when documents change."""
        rag1 = self._make_rag(tmp_path)
        rag1.ingest_documents(self.DOCS)

        # Modify a document
        changed_docs = {**self.DOCS, "cv.md": "## Experience\nWorked with Go and Rust."}
        rag2 = self._make_rag(tmp_path)
        count = rag2.ingest_documents(changed_docs)
        assert count > 0

        # The loaded chunks should reflect the NEW content
        cv_chunks = [c for c in rag2.chunks if c.source == "cv.md"]
        assert any("Go" in c.content for c in cv_chunks)

    def test_model_mismatch_triggers_recompute(self, tmp_path):
        """Test different model name forces recompute."""
        rag1 = self._make_rag(tmp_path, model="all-MiniLM-L6-v2")
        rag1.ingest_documents(self.DOCS)

        # Same docs, different model
        rag2 = self._make_rag(tmp_path, model="paraphrase-multilingual-MiniLM-L12-v2")
        count = rag2.ingest_documents(self.DOCS)
        assert count > 0

        # Metadata should now have the new model
        with open(tmp_path / "cache" / "embeddings.json") as f:
            meta = json.load(f)
        assert meta["model"] == "paraphrase-multilingual-MiniLM-L12-v2"

    def test_corrupted_npz_falls_back_to_compute(self, tmp_path):
        """Test corrupted npz file triggers recompute."""
        rag1 = self._make_rag(tmp_path)
        rag1.ingest_documents(self.DOCS)

        # Corrupt the npz file
        npz_path = tmp_path / "cache" / "embeddings.npz"
        npz_path.write_bytes(b"not a valid npz file")

        rag2 = self._make_rag(tmp_path)
        count = rag2.ingest_documents(self.DOCS)
        assert count > 0
        # Should have recompute and re-saved a valid cache
        assert npz_path.exists()

    def test_corrupted_json_falls_back_to_compute(self, tmp_path):
        """Test corrupted metadata JSON triggers recompute."""
        rag1 = self._make_rag(tmp_path)
        rag1.ingest_documents(self.DOCS)

        # Corrupt the metadata file
        json_path = tmp_path / "cache" / "embeddings.json"
        json_path.write_text("not valid json {{{")

        rag2 = self._make_rag(tmp_path)
        count = rag2.ingest_documents(self.DOCS)
        assert count > 0

    def test_no_cache_dir_still_works(self, tmp_path):
        """Test pipeline works without cache_dir (backward compat)."""
        rag = RAGPipeline(chunk_size=100)
        count = rag.ingest_documents(self.DOCS)
        assert count > 0
        for chunk in rag.chunks:
            assert chunk.embedding is not None

    def test_chunk_count_mismatch_triggers_recompute(self, tmp_path):
        """Test cache rejected when chunk count doesn't match."""
        rag1 = self._make_rag(tmp_path)
        rag1.ingest_documents(self.DOCS)

        # Tamper with the metadata chunk_count
        json_path = tmp_path / "cache" / "embeddings.json"
        with open(json_path) as f:
            meta = json.load(f)
        meta["chunk_count"] = 9999
        with open(json_path, "w") as f:
            json.dump(meta, f)

        rag2 = self._make_rag(tmp_path)
        count = rag2.ingest_documents(self.DOCS)
        assert count > 0
        assert count != 9999  # Should have recomputed

    def test_compute_documents_hash_deterministic(self, tmp_path):
        """Test hash is deterministic for same inputs."""
        h1 = RAGPipeline._compute_documents_hash(self.DOCS)
        h2 = RAGPipeline._compute_documents_hash(self.DOCS)
        assert h1 == h2
        assert len(h1) == 64

    def test_compute_documents_hash_different_for_different_docs(self, tmp_path):
        """Test different docs produce different hashes."""
        h1 = RAGPipeline._compute_documents_hash(self.DOCS)
        h2 = RAGPipeline._compute_documents_hash({"other.md": "Completely different content."})
        assert h1 != h2


class TestEmbedderProperty:
    """Read-only embedder exposure for the semantic answer cache (design D8)."""

    def test_embedder_none_before_initialization(self):
        """Property returns None while the pipeline has never been initialized."""
        rag = RAGPipeline()
        assert rag.embedder is None

    def test_embedder_none_in_tfidf_fallback_mode(self):
        """TF-IDF fallback vectors are unstable — must never be exposed."""
        rag = RAGPipeline()
        rag._initialized = True
        rag._use_tfidf = True
        rag._embedder = object()  # sentinel: even a live object must be hidden
        assert rag.embedder is None

    def test_embedder_exposed_when_active(self):
        """An initialized sentence-transformer pipeline exposes its embedder."""
        rag = RAGPipeline()
        rag._initialized = True
        sentinel = object()
        rag._embedder = sentinel
        assert rag.embedder is sentinel
