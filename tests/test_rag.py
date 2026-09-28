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
    split_sections,
)

# The labelled retrieval set, the synthetic corpus and the isolation fixture.
#
# WHY A FIXTURE AND NOT THE REPOSITORY'S OWN wiki/
# -----------------------------------------------
# Every test below that used to read the real `wiki/` now reads
# `tests/fixtures/retrieval_corpus/`, an entirely invented corpus (see its
# README; `python -m tests.fixture_corpus --verify-no-derivation` proves no
# name, employer, project or product is shared with the real wiki). The real
# wiki is gitignored and private, so a test that read it was green only on
# the one machine that has it: a clean clone failed seven of them.
#
# The consequence for the numbers below is the important part. Figures that
# were MEASURED ON THE REAL CORPUS — recall, chunk counts, cosine scores,
# the 0.23 dilution, the 0.0081 margin — are recomputed against the fixture
# where the test still needs a number, and every docstring says which corpus
# its number came from. A floor that keeps its old value while its corpus
# changes is not a floor, it is a coincidence.
from tests.fixture_corpus import (
    FIXTURE_ROOT,
    LABELLED_CASES,
    build_pipeline,
    load_documents,
)

# A body line that is nothing but wikilinks: ``- [[profile/mikel]]``.
_WIKILINK_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\[\[[^\]]*\]\](?:[,;]\s*)?)+\s*$")


def _heading_and_body(section: str) -> tuple[str, list[str]]:
    """Split a raw section into its heading text and its non-blank body lines."""
    lines = [l for l in section.split("\n") if l.strip()]
    if not lines:
        return "", []
    m = re.match(r"^#{1,3}\s+(.+)", lines[0])
    return (m.group(1).strip() if m else ""), (lines[1:] if m else lines)


def _wikilink_only(section: str) -> bool:
    """True when a section states no prose at all — only ``[[wikilinks]]``."""
    _, body = _heading_and_body(section)
    return bool(body) and all(_WIKILINK_LINE.match(l) for l in body)


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
        """Retrieval returns empty when nothing clears the score filter.

        The filter is set on the pipeline, not per call: ``retrieve()`` takes no
        ``threshold`` argument (see ``TestRetrievalThresholdIsHonest``), because
        no production caller passed one and a second unmeasured knob is how this
        filter came to be mistaken for inert.
        """
        rag = RAGPipeline(chunk_size=100, threshold=0.99)
        docs = {"cv.md": "Python experience and skills."}
        rag.ingest_documents(docs)

        results = rag.retrieve("quantum physics superposition", top_k=3)
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
        """Read the committed corpus's frontmatter: no `type:` may be unreachable.

        This is the data-driven net. Adding a new `type:` to the corpus without
        extending the mapping must fail here, loudly, instead of silently
        producing ungrounded answers at interview time.

        CORPUS: ``tests/fixtures/retrieval_corpus/``. It is not the owner's
        ``wiki/``, which is gitignored and private — a test that read it was
        green only where it happened to exist. The corpus carries all eight
        types on purpose, so the net is still exercised end to end; the owner
        gets the same coverage by running the same check against their own
        pages, which is what ``scripts/wiki/validate.py`` is for.
        """
        real_types = set()
        for md in FIXTURE_ROOT.rglob("*.md"):
            meta, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            raw = str(meta.get("type", "") or "").strip()
            if raw and "|" not in raw:  # CONVENCIONES.md lists all types
                real_types.add(raw)

        assert real_types, f"no documents with a type: found under {FIXTURE_ROOT}"
        assert real_types == set(_TYPE_PROBES), (
            f"the committed corpus must exercise every type the mapping claims "
            f"to cover, and it is missing {sorted(set(_TYPE_PROBES) - real_types)}"
        )
        unreached = {
            t for t in real_types if canonical_doc_type(t) != t or t not in _TYPE_PROBES
        }
        assert not unreached, (
            f"corpus types not covered by the doc_type mapping: {sorted(unreached)}. "
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
        """Only the marker is removed; every real answer around it stays.

        Sanitisation must not decide what is an answer. A line like
        ``- [TODO: metricas] Reduje la latencia un 40%`` carries content the
        owner already wrote, and the prose trailing a marker ("Any metrics?")
        is indistinguishable from a real answer without a semantic judgement
        this layer must not make. Resolving the marker is the owner's task.
        """
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/interview-tts.md", self.TODO_DOC)
        joined = "\n".join(c.content for c in chunks)
        assert "Entrevista por voz en tiempo real con FastAPI" in joined
        assert "SQLite desde el primer día" in joined, (
            "removing a placeholder must not take the surrounding answer with it"
        )
        assert "Any metrics" in joined, (
            "prose trailing a marker must survive: dropping it is a content "
            "decision, not a sanitisation one"
        )

    def test_answer_written_after_a_leading_marker_is_kept(self):
        """The real defect: a leading marker used to take the whole line with it."""
        doc = (
            "---\ntype: project\nconfidence: high\n---\n\n"
            "# P\n\n## Outcomes\n\n- [TODO: metricas] Reduje la latencia un 40%\n"
        )
        rag = RAGPipeline(chunk_size=1000)
        joined = "\n".join(
            c.content for c in rag._chunk_document("projects/p.md", doc)
        )
        assert "Reduje la latencia un 40%" in joined, (
            "a real answer written after a placeholder must not be deleted"
        )

    def test_note_to_self_prose_keeps_its_terminal_mark(self):
        """The strip was removing the mark that made the line what it is.

        ``strip_placeholders`` ended with ``.strip(_SEPARATORS)``, and
        ``_SEPARATORS`` contains ``?``, ``!`` and ``.``. Those are there to
        clean up the orphaned bullet and colon a removed marker leaves at the
        START of a line -- but applied to both ends they also took the terminal
        mark off the prose that survived.

        The result is worse than the marker was. What the marker usually trails
        is a note-to-self ("[TODO] -- Any metrics?"), and a note-to-self that
        reads as an unfinished fragment is *less* obviously a note-to-self: a
        recruiter asking about metrics gets a section that reads as a question
        the candidate asked, which is the opposite of what it is.

        Measured on the real corpus: 10 of 16 note-to-self lines lost the mark,
        and one chunk of 125 came out as nothing but unanswered questions.
        """
        from backend.services.rag import strip_placeholders

        cleaned, removed = strip_placeholders(
            "- [TODO: metricas] — El proyecto esta deployado? Metricas de produccion?"
        )
        assert removed == 1
        assert cleaned == (
            "El proyecto esta deployado? Metricas de produccion?"
        ), f"the note-to-self line came out as {cleaned!r}"

    @pytest.mark.parametrize(
        "line,expected",
        [
            pytest.param("- [TODO] Any metrics?", "Any metrics?", id="question"),
            pytest.param("- [TODO] What would you change!", "What would you change!",
                         id="exclamation"),
            pytest.param("- [TODO] Shipped on Friday.", "Shipped on Friday.",
                         id="full-stop"),
            pytest.param("- [TODO] (e.g. latency?)", "(e.g. latency?)", id="closing-paren"),
        ],
    )
    def test_terminal_punctuation_survives_marker_removal(self, line, expected):
        from backend.services.rag import strip_placeholders

        cleaned, _ = strip_placeholders(line)
        assert cleaned == expected, f"{line!r} came out as {cleaned!r}"

    def test_the_orphaned_bullet_the_marker_left_is_still_stripped(self):
        """The leading side is what the strip was FOR; it must not regress.

        ``- [TODO: x]:** text`` leaves ``- :** text`` behind. Removing the
        marker has to take that punctuation with it, or every filtered line
        starts with an orphan bullet and a stray colon.
        """
        from backend.services.rag import strip_placeholders

        cleaned, _ = strip_placeholders("- **[TODO: x]:** Reduje la latencia un 40%")
        assert cleaned == "Reduje la latencia un 40%", f"got {cleaned!r}"

    def test_a_dash_left_by_a_trailing_marker_is_still_stripped(self):
        """Trailing cleanup keeps the characters a marker removal orphans.

        A dash is not a terminal mark: ``40% —`` is punctuation debris from a
        marker that used to be between it and the end of the line. A question
        mark is the author's, so one side of the strip keeps the full
        separator class and the other cannot.
        """
        from backend.services.rag import strip_placeholders

        cleaned, _ = strip_placeholders("Reduje la latencia un 40% — [TODO: completar]")
        assert cleaned == "Reduje la latencia un 40%", f"got {cleaned!r}"

    def test_heading_keeps_its_hashes_so_chunking_is_unchanged(self):
        """Stripping a marker from a heading must not merge two sections."""
        doc = (
            "---\ntype: project\nconfidence: high\n---\n\n"
            "# P\n\n## A [TODO: renombrar]\ncontenido A\n\n## B\ncontenido B\n"
        )
        rag = RAGPipeline(chunk_size=1000)
        chunks = rag._chunk_document("projects/p.md", doc)
        contents = [c.content for c in chunks]
        assert any("## B" in c and "contenido A" not in c for c in contents), (
            "losing a heading's #'s merges sections and shifts chunk boundaries"
        )

    def test_identifiers_with_underscores_are_not_rewritten(self):
        """`_MD_NOISE_RE` must not strip '_' out of real words in body text."""
        from backend.services.rag import strip_placeholders

        cleaned, _ = strip_placeholders("- field snake_case_name [TODO: x]")
        assert "snake_case_name" in cleaned

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

        # Repinned for the H1 re-attachment: the document's own title now opens
        # the first chunk instead of standing alone as chunk 0, so a clean
        # document yields ONE chunk carrying the title and both bodies. The
        # purpose of this test is unchanged — placeholder filtering must not
        # perturb clean input — only the pinned expectation moved.
        assert len(chunks) == 1, "clean input must chunk exactly as before"
        assert [c.id for c in chunks] == ["projects/clean.md-0"]
        assert [c.section for c in chunks] == ["Proyecto"]
        assert [c.type for c in chunks] == ["project"]
        assert [c.tags for c in chunks] == [["python"]]
        assert [c.summary for c in chunks] == ["Proyecto limpio"]
        assert [c.content for c in chunks] == [
            "# Proyecto\n\nUna primera seccion con contenido real.\n\n"
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
        it would erase a topic the candidate does have.
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
        assert emptied, "the section should survive with its heading intact"
        # The section is no longer *empty*: the prose that trailed the marker is
        # the owner's own question, and sanitisation does not get to delete it.
        # The heading keeps its '##' so this stays a section boundary.
        #
        # It is no longer the FIRST line either: the document's own H1 now opens
        # the chunk it titles, so assert presence, not position. The bare-H2-
        # alone shape this used to rely on is gone from the corpus entirely.
        assert "## What I'd do differently" in emptied[0].content
        assert "What would you change about InterviewTTS" in emptied[0].content, (
            "the owner's question survives; only the [TODO marker] is removed"
        )
        assert "[TODO" not in emptied[0].content
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
    """The committed corpus must reach the LLM free of [TODO placeholders.

    CORPUS: ``tests/fixtures/retrieval_corpus/``. The real wiki is gitignored
    and private, so this cannot read it — and the filter it guards is a read-
    time transform, so it holds for any corpus equally. The floor below is
    FIXTURE-DERIVED: the fixture is built with real ``[TODO`` markers in
    realistic positions (inside "Resultados medidos" and "Que haria distinto",
    which is exactly where a recruiter's question lands), so the count proves
    the filter ran on real input rather than on a corpus that had nothing to
    filter.
    """

    def _real_wiki_documents(self) -> dict:
        return load_documents()

    def test_no_chunk_from_the_corpus_contains_todo(self, fixture_corpus_targets):
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
            f"{len(offenders)} chunk(s) from the corpus still contain [TODO: "
            f"{offenders}"
        )
        assert total > 100, f"expected a substantial corpus, chunked {total}"

    def test_the_corpus_actually_carries_placeholders_to_strip(self, fixture_corpus_targets):
        """The negative test above is only meaningful if there is something to strip.

        Without this, a corpus edit that deleted every ``[TODO`` would turn
        the guard into a tautology and nothing would say so.
        """
        documents = self._real_wiki_documents()
        raw = [
            name for name, content in documents.items() if "[TODO" in content
        ]
        assert len(raw) >= 2, (
            f"expected markers in several pages, found {raw}. The placeholder "
            f"stripping is no longer being tested against real input."
        )
        # ...and the page that carries one also keeps the prose around it.
        assert any(
            "Resultados" in content or "que haria distinto" in content.lower()
            for content in documents.values()
            if "[TODO" in content
        ), "a marker must sit in a section a recruiter actually asks about"


class TestEmbeddingCache:
    """Tests for embedding cache save/load and invalidation."""

    DOCS = {
        "cv.md": "## Experience\nWorked with Python and FastAPI.",
        "skills.md": "## Skills\nJavaScript, React for frontend.",
    }

    @pytest.fixture(autouse=True)
    def _stub_embedder(self, monkeypatch):
        """Give every model name in this class a stand-in, and download nothing.

        ``initialize()`` now loads ``self._embedding_model`` instead of a
        hardcoded name, which is the whole point of the fix -- and it means a
        test that varies the model name now varies which model is fetched.
        Without this, ``test_model_mismatch_triggers_recompute`` pulled a second
        ~470 MB checkpoint off the network to prove something about a JSON file.

        Deterministic per text, so the cache's contents are still comparable
        across runs; the real embedder is exercised by the retrieval sweeps.
        """
        import sys
        import types

        module = types.ModuleType("sentence_transformers")

        def _vector(text: str, dimensions: int = 384) -> np.ndarray:
            generator = np.random.default_rng(abs(hash(text)) % (2**31))
            raw = generator.random(dimensions).astype(np.float32)
            return raw / np.linalg.norm(raw)

        class SentenceTransformer:
            def __init__(self, name):
                self.name = name

            def encode(self, texts, show_progress_bar=False):
                return np.stack([_vector(text) for text in texts])

        module.SentenceTransformer = SentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", module)

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


class TestH1StaysWithTheBodyItTitles:
    """The document's own H1 is answer content, not a section of its own.

    Measured on the real corpus: splitting before every H1-H3 turned 34 of the
    214 chunks into a bare title with no body. Worse, the orphaned title is
    often the *answer* to the question — ``# Frutero en BM Supermercados
    (2015-2016)`` carries role, employer and years, exactly what a recruiter
    asks about. Splitting it off both wastes a top-k slot and throws the fact
    away from the paragraph that explains it.
    """

    # Shaped like wiki/experience/frutero-bm-2015-2016.md: H1, then a short
    # answer section under H2, exactly as the FAQ/experience pages are written.
    DOC = """# Frutero — BM Supermercados (2015-2016)

## Respuesta corta (30s)
Empecé reponiendo yubicando fruta y verdura en la seccion de perecedero,
atendiendo al cliente de forma directa.

## Respuesta larga (2min)
Mi primer trabajo en retail fue reponer y reponer el linear de frescos.
"""

    def _chunks(self):
        return RAGPipeline(chunk_size=1000)._chunk_document(
            "experience/frutero.md", self.DOC
        )

    def test_h1_is_not_emitted_as_a_bare_chunk(self):
        """A chunk that is nothing but an H1 wastes a top-k slot on no answer."""
        chunks = self._chunks()
        bare = [
            c for c in chunks
            if re.match(r"^#\s", c.content) and "\n" not in c.content.strip()
        ]
        assert not bare, (
            f"{len(bare)} bare-H1 chunk(s) emitted; contents: "
            f"{[c.content for c in bare]}"
        )

    def test_h1_text_is_in_the_same_chunk_as_the_body_it_titles(self):
        """The title must ride in the CONTENT of the chunk it introduces.

        An embedder reads content, not metadata: a title that survives only in
        ``chunk.section`` is invisible to retrieval.
        """
        chunks = self._chunks()
        titled = [c for c in chunks if "Frutero" in c.content and "BM Supermercados" in c.content]
        assert titled, (
            "no chunk carries both the employer/years title and the body it "
            f"titles; chunks were {[c.content[:60] for c in chunks]}"
        )
        assert any("yubicando fruta y verdura" in c.content for c in titled), (
            "the title and the body it titles are in different chunks"
        )

    def test_h1_is_not_kept_only_in_metadata(self):
        """The title must not be demoted to metadata-only.

        Before the fix the title was reachable via ``chunk.section`` while the
        embedding saw an empty-ish body — exactly the split this forbids.
        """
        chunks = self._chunks()
        content_only = [c for c in chunks if "Frutero" in c.content]
        assert content_only, "the H1 title is absent from every chunk's content"

    def test_a_later_h1_still_splits_into_its_own_section(self):
        """Only the document's *own* title is re-attached.

        A second H1 is a genuine top-level boundary. Merging it into the
        previous section would drop a real boundary and mislabel ``section``,
        so it must keep splitting.
        """
        rag = RAGPipeline(chunk_size=1000)
        content = (
            "# Primer titulo\nCuerpo de la primera seccion.\n\n"
            "## Subseccion\nCuerpo de la subseccion.\n\n"
            "# Segundo titulo\nCuerpo de la segunda seccion.\n"
        )
        chunks = rag._chunk_document("doc.md", content)
        sections = [c.section for c in chunks]
        assert "Segundo titulo" in sections, (
            f"a later H1 stopped being a boundary; sections were {sections}"
        )
        body_of_second = [c for c in chunks if c.section == "Segundo titulo"][0]
        assert "Cuerpo de la segunda seccion" in body_of_second.content
        assert "Cuerpo de la primera seccion" not in body_of_second.content

    def test_document_without_h1_is_unchanged(self):
        """Frontmatter-only bodies (no H1) must chunk exactly as before."""
        rag = RAGPipeline(chunk_size=1000)
        content = "## Seccion A\nCuerpo A.\n\n## Seccion B\nCuerpo B.\n"
        chunks = rag._chunk_document("doc.md", content)
        assert [c.section for c in chunks] == ["Seccion A", "Seccion B"]
        assert chunks[0].content == "## Seccion A\nCuerpo A."

    def test_corpus_emits_no_bare_h1_chunk(self, fixture_corpus_targets):
        """Guard the committed corpus, not just a synthetic document.

        CORPUS: ``tests/fixtures/retrieval_corpus/``. The real wiki is
        gitignored and private, so this cannot read it. The fixture is built
        with both H1 shapes the corpus actually has — FAQ pages whose H1 IS
        the interviewer's question, and narrative pages whose H1 carries role,
        employer and years — so the guard still sees the case that motivated
        the fix.

        ``index.md`` is excluded here and not excused: it is a generated
        build artifact (``AUTO-GENERATED ... do not edit``) and the loader
        drops it entirely, so it can never reach the chunker in production.
        """
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        offenders = []
        for name, content in load_documents().items():
            if Path(name).name == "index.md":
                continue
            for c in rag._chunk_document(name, content):
                if re.match(r"^#\s", c.content) and "\n" not in c.content.strip():
                    offenders.append((c.source, c.content.strip()))
        assert not offenders, (
            f"{len(offenders)} bare-H1 chunk(s) still emitted: {offenders[:5]}"
        )


class TestWikilinkReferenceSectionsAreNotIndexed:
    """A list of ``[[wikilinks]]`` is navigation, not an answer.

    As chunk text these sections are actively harmful. The literal string
    ``- [[profile/nuria-belvis]]`` means nothing to a sentence embedder; the
    headings "Fuentes" and "Ver tambien" are generic Spanish that matches no
    recruiter question; and on the real corpus 42 of its 214 chunks were
    exactly this, so roughly a seventh of the available top-k pool held no
    answer at all.

    Dropping them is lossless: every real link resolves to a document that is
    indexed on its own, and a question about that topic retrieves that
    document's own content. See ``test_dropping_reference_links_loses_no_
    answer_content`` for the measured proof.

    CORPUS: ``tests/fixtures/retrieval_corpus/``. The 42-of-214 figure is the
    REAL corpus's and is kept as the motivating measurement; the assertions
    below run against the fixture, which is built with the same structure —
    two or three link-only sections per page — and also carries unfilled
    ``[[...]]`` placeholders, so the "not every link resolves" branch is
    exercised by real input.
    """

    DOC = """# InterviewTTS

## Qué es

Un gemelo digital de voz para entrevistas de trabajo.

## Fuentes
- [[profile/mikel]]
- [[decisions/por-que-interviewtts]]

## Ver tambien
- [[skills/backend]]
"""

    def _chunks(self, doc=None, **kw):
        return RAGPipeline(chunk_size=1000, **kw)._chunk_document(
            "projects/interview-tts.md", doc or self.DOC
        )

    def test_no_chunk_is_a_wikilink_only_section(self):
        """The defect itself: a chunk whose every line is a bare wikilink."""
        offenders = [
            c.content for c in self._chunks() if _wikilink_only(c.content)
        ]
        assert not offenders, f"{len(offenders)} wikilink-only chunk(s): {offenders}"

    def test_the_prose_sections_around_them_survive(self):
        """Only the link lists go — the answer beside them must be untouched."""
        chunks = self._chunks()
        # One chunk, not two: the document's own H1 re-attachment (see
        # TestH1StaysWithTheBodyItTitles) means "## Qué es" opens the first
        # chunk rather than standing as a section of its own.
        assert [c.section for c in chunks] == ["InterviewTTS"]
        assert "gemelo digital de voz" in chunks[0].content

    def test_ellipsis_placeholder_links_are_also_dropped(self):
        """``[[faq/...]]`` names no document, so it states no relationship.

        Four of the corpus's reference links are unfilled ellipsis
        placeholders. They cannot express a relationship because they name
        nothing, so keeping them would cost a slot to say literally nothing.
        """
        doc = (
            "# Faq\n\n## Fuentes\n- [[faq/...]]\n- [[opinions/...]]\n"
        )
        assert not [c for c in self._chunks(doc) if _wikilink_only(c.content)]

    def test_a_section_mixing_prose_with_links_is_kept(self):
        """The rule is "no prose at all", never "contains a wikilink".

        A source that explains WHY it points somewhere carries real content
        and must reach the LLM. This is the guard against over-filtering.
        """
        doc = (
            "# Faq\n\n## Fuentes\n- [[profile/mikel]] para el nivel de ingles.\n"
            "- Ver tambien [[faq/presentacion-30-segundos]] para el pitch.\n"
        )
        kept = [c for c in self._chunks(doc) if "nivel de ingles" in c.content]
        assert kept, (
            "a reference section that carries prose was dropped — that prose "
            "is answer content"
        )
        assert "presentacion-30-segundos" in kept[0].content

    def test_a_bare_related_heading_with_no_body_is_dropped(self):
        """An empty reference heading states nothing either."""
        doc = "# Faq\n\nRespuesta real con contenido.\n\n## Fuentes\n\n## Ver tambien\n"
        chunks = self._chunks(doc)
        assert not any(_wikilink_only(c.content) for c in chunks)
        assert not any(
            c.section.strip().lower() in {"fuentes", "see also", "sources"}
            for c in chunks
        ), f"empty reference headings survived as chunks: {[(c.section, c.content) for c in chunks]}"

    def test_corpus_emits_no_wikilink_only_chunk(self, fixture_corpus_targets):
        """Guard the committed corpus."""
        chunks = self._real_corpus_chunks()
        offenders = [(c.source, c.content[:70]) for c in chunks if _wikilink_only(c.content)]
        assert not offenders, f"{len(offenders)} wikilink-only chunk(s): {offenders[:5]}"

    def test_reference_heading_set_is_exactly_what_the_corpus_uses(self, fixture_corpus_targets):
        """Pin the header set, so a NEW spelling cannot slip through.

        Each entry below is justified by an occurrence in the corpus:
        ``Fuentes``, ``Ver tambien``, ``Ver también`` and ``See also`` all
        appear in live pages, and ``Sources`` appears in
        ``templates/faq-template.md`` — the template the next FAQ is written
        from, so it is a spelling the corpus will produce even though no live
        page uses it yet.

        CORPUS: ``tests/fixtures/retrieval_corpus/``, built to carry all five
        spellings. The real wiki is gitignored and private. The table in
        ``REFERENCE_HEADINGS`` is unchanged and is still the production one;
        what is re-pinned here is that the committed corpus exercises every
        entry, which is what makes the table's per-entry justification a
        checked claim instead of a comment.
        """
        from backend.services.rag import REFERENCE_HEADINGS

        used = set()
        for name, content in self._real_corpus_documents().items():
            if Path(name).name == "index.md":
                continue
            for sec in split_sections(content):
                heading, _ = _heading_and_body(sec)
                if heading.lower() in REFERENCE_HEADINGS:
                    used.add(heading.lower())
        assert used == REFERENCE_HEADINGS - {"sources"}, (
            f"corpus reference headings drifted: found {sorted(used)}, "
            f"table declares {sorted(REFERENCE_HEADINGS)}"
        )
        template = (self._wiki_root() / "templates" / "faq-template.md").read_text(
            encoding="utf-8"
        )
        assert "## Sources" in template, (
            "'sources' is in the table only because the FAQ template spells it "
            "that way; if the template changed, re-justify or drop the entry"
        )

    def test_dropping_reference_links_loses_no_answer_content(self, fixture_corpus_targets):
        """The 'loss is zero' argument, measured rather than asserted.

        Every link in every reference section of the corpus either names no
        document at all (an unfilled ``[[...]]`` placeholder, which cannot
        state a relationship) or names a document that is itself indexed and
        therefore answers that topic on its own.
        """
        documents = self._real_corpus_documents()
        indexed = {k.replace("\\", "/") for k in documents}
        placeholders, resolved, dangling = 0, 0, []

        for name, content in documents.items():
            for sec in split_sections(content):
                if not _wikilink_only(sec):
                    continue
                for target in re.findall(r"\[\[([^\]]+)\]\]", sec):
                    # An unfilled template placeholder such as ``faq/...`` names
                    # no document, so it cannot state a relationship.
                    if target.endswith("...") or set(target) == {"."}:
                        placeholders += 1
                        continue
                    resolved += 1
                    path = target if target.endswith(".md") else f"{target}.md"
                    if path not in indexed:
                        dangling.append((name, target))

        assert not dangling, f"reference links pointing at nothing: {dangling}"
        assert resolved > 0 and placeholders > 0, (
            f"corpus changed shape: {resolved} real links, {placeholders} placeholders"
        )

    def _wiki_root(self) -> Path:
        return FIXTURE_ROOT

    def _real_corpus_documents(self) -> dict:
        from backend.services.candidate import CandidateProfile

        profile = CandidateProfile(
            Path(__file__).resolve().parent.parent / "candidate",
            wiki_dir=self._wiki_root(),
        )
        profile.load()
        assert profile.documents, "the real wiki must still load"
        return profile.documents

    def _real_corpus_chunks(self):
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        for name, content in self._real_corpus_documents().items():
            yield from rag._chunk_document(name, content)


@pytest.fixture(scope="module")
def real_wiki_pipeline():
    """The committed fixture corpus, ingested once with real embeddings.

    Module-scoped because embedding the corpus costs ~100s. This is the only
    test that can catch a chunking "cleanup" that quietly degrades answers:
    a count assertion proves nothing about what the LLM actually receives.

    CORPUS: ``tests/fixtures/retrieval_corpus/`` — 42 typed pages, 121 chunks
    at the shipped 400/50, all invented. It is named ``real_wiki_pipeline``
    only to keep the diff in the test names readable; it is not the owner's
    wiki, which is gitignored, private, and unreadable from a clean clone.
    ``cache_dir`` is left at its ``None`` default, so this cannot write
    ``backend/.rag_cache/``.
    """
    return build_pipeline(chunk_size=400, chunk_overlap=50)


def _norm_source(source: str) -> str:
    """Document keys are ``str(Path.relative_to(...))``, so separators vary."""
    return source.replace("\\", "/")


class TestRetrievalRegressionGuard:
    """Representative recruiter questions must still find their own document.

    Counts prove nothing about quality. This is the guard that stops a
    "cleanup" from silently degrading answers: every question below is phrased
    the way Whisper emits it (lowercase, unpunctuated, accents unreliable) and
    names the page that genuinely holds the answer.

    CORPUS: ``tests/fixtures/retrieval_corpus/``. The six cases are a
    CHARACTERISATION set, not a discriminator for the chunking fixes — they
    lock in what already works so a later change that breaks it fails loudly,
    and they are deliberately spread across the shapes the corpus has (two
    FAQ pages whose H1 is the question, a story, an opinion-adjacent FAQ, and
    a skills page). What actually discriminates is
    ``test_top_chunk_carries_an_answer_not_just_a_title``.

    TALKING POINT: the guard is proven, not asserted. The fix that points
    these at a synthetic corpus also re-pins them against a deliberately
    broken retriever and shows them going red; see the commit message for
    that transcript. A guard that cannot fail is a comment.

    ONE CASE IS A KNOWN FAILURE AND IS MARKED, NOT DELETED. A guard that
    quietly drops the question it fails is the exact failure mode it exists
    to catch, so it is kept with its measured cause and will flip to XPASS
    when the cause is removed.
    """

    # (question, gold document) — questions this pipeline is expected to serve.
    CASES = [
        ("cual es tu nivel de ingles", "faq/nivel-ingles.md"),
        ("cuando podrias incorporarte al puesto", "faq/disponibilidad.md"),
        ("cuales son tus fortalezas y debilidades", "faq/fortalezas-y-debilidades.md"),
        ("cuentame lo del apagon del horno cuatro",
         "stories/apagon-horno-cuatro.md"),
        ("que hiciste cuando un cliente te pidio diez dias",
         "stories/cliente-pide-plazo-diez-dias.md"),
        ("que experiencia tienes con typescript y frontend", "skills/frontend.md"),
    ]

    def test_question_retrieves_its_own_document(self, real_wiki_pipeline):
        missing = []
        for question, gold in self.CASES:
            sources = [_norm_source(c.source) for c, _ in real_wiki_pipeline.retrieve(question, top_k=3)]
            if gold not in sources:
                missing.append((question, gold, sources))
        assert not missing, f"{len(missing)} question(s) lost their document: {missing}"

    @pytest.mark.xfail(
        reason=(
            "REGRESSION from the H1 re-attachment, measured. On a FAQ page the H1 "
            "IS the canonical interview question, so a title-only chunk is a sharp "
            "retrieval key. On the REAL corpus '# \"Cuéntame sobre ti\" — Presentación "
            "de 30 segundos' scored 0.745 on this question, the best score in that "
            "corpus; merged into its 67-word body it scored 0.514, a 0.23 cosine "
            "drop that pushed it out of the top 3. The title is no longer discarded, "
            "but merging it dilutes it. Root cause is chunk sizing, a separate "
            "decision. The corpus moved to tests/fixtures/retrieval_corpus/, so "
            "this is now re-measured there: the fixture's "
            "'# presentate en treinta segundos' page reproduces the same shape and "
            "the same dilution (see test_rag_chunk_size_sweep.py for the measured "
            "title-only / title+15 / full-merged curve on the fixture). If the "
            "fixture stops reproducing it, this will XPASS and that is a finding, "
            "not something to delete: the real corpus still exhibits the regression."
        ),
        strict=True,
    )
    def test_title_only_chunk_kept_the_exact_question_reachable(self, real_wiki_pipeline):
        gold = "faq/presentacion-30-segundos.md"
        sources = [_norm_source(c.source) for c, _ in
                   real_wiki_pipeline.retrieve("cuentame sobre ti en treinta segundos", top_k=3)]
        assert gold in sources, f"expected {gold} in {sources}"

    @pytest.mark.xfail(
        reason=(
            "PRE-EXISTING gap, not caused by the chunking fixes: this question "
            "missed the gold document before them too (measured rank: absent from "
            "the top 3 both before and after). Recorded here so the gap stays "
            "visible rather than being rediscovered later. Re-measured against "
            "tests/fixtures/retrieval_corpus/, where it still misses."
        ),
        strict=True,
    )
    def test_open_ended_role_question_finds_the_role(self, real_wiki_pipeline):
        gold = "experience/jefa-produccion-vinalar-2018-2024.md"
        sources = [_norm_source(c.source) for c, _ in
                   real_wiki_pipeline.retrieve("que estabas haciendo en vinalar los ultimos años", top_k=3)]
        assert gold in sources, f"expected {gold} in {sources}"

    def test_top_chunk_carries_an_answer_not_just_a_title(self, real_wiki_pipeline):
        """The LLM's first piece of context must be an answer, not a restated question.

        This is the discriminating test for the H1 fix, and it is what the
        defect actually cost. Before it, ``# ¿Cuál es tu disponibilidad?``
        (5 words) and ``# "Cuéntame sobre ti" — Presentación de 30 segundos``
        (9 words) were the top-1 retrieved chunks for those questions: the
        model received the interviewer's own question back and nothing else.

        A heading names a topic; a body answers it. Any body text at all is
        enough, so this does not encode an opinion about how long a chunk
        should be — that is a separate decision.
        """
        title_only = []
        for question, _gold in self.CASES:
            results = real_wiki_pipeline.retrieve(question, top_k=1)
            assert results, f"{question!r} retrieved nothing at all"
            top = results[0][0]
            lines = [l for l in top.content.split("\n") if l.strip()]
            body = lines[1:] if lines and re.match(r"^#{1,3}\s", lines[0]) else lines
            if not body:
                title_only.append((question, top.content.strip()[:60]))
        assert not title_only, (
            "the top-1 context chunk is a bare heading for these questions — "
            f"the LLM gets the question back instead of the answer: {title_only}"
        )

    def test_no_top_three_slot_is_spent_on_a_link_list(self, real_wiki_pipeline):
        """A slot holding no answer is a slot a real answer cannot occupy."""
        wasted = []
        for question, _gold in self.CASES:
            for c, _score in real_wiki_pipeline.retrieve(question, top_k=3):
                if _wikilink_only(c.content) or _norm_source(c.source) == "index.md":
                    wasted.append((question, c.source))
        assert not wasted, f"top-k slots wasted on link lists or the index: {wasted}"


class TestChunkFilterVersionGuardsTheStaleCache:
    """A cache written before this change must not be served after it.

    ``document_hash`` covers the RAW wiki text, which none of the three fixes
    touch, so without a version bump an old cache is restored in full and every
    fix is silently undone: the bare H1 titles and the wikilink sections come
    straight back. This builds a cache the way the PREVIOUS chunker did and
    proves the current pipeline refuses it.
    """

    PREVIOUS_VERSION = "2"

    DOCS = {
        "faq/nivel-ingles.md": (
            "---\ntype: faq\nconfidence: high\n---\n\n"
            "# Como es tu nivel de ingles\n\n## Fuentes\n- [[profile/mikel]]\n"
        ),
    }

    def _previous_chunker(self, filename, content):
        """The chunker as it behaved before this change: split at H1-H3 and
        index every section, wikilink lists included."""
        chunks, chunk_id = [], 0
        for section in re.split(r'\n(?=#{1,3}\s)', content):
            section = section.strip()
            if not section:
                continue
            match = re.match(r'^#{1,3}\s+(.+)', section)
            chunks.append(Chunk(
                id=f"{filename}-{chunk_id}",
                content=section,
                source=filename,
                section=match.group(1) if match else filename,
                type="faq",
            ))
            chunk_id += 1
        return chunks

    def _write_previous_cache(self, cache_dir):
        """Persist a cache holding the old chunker's output, tagged with the
        old filter version, exactly as a warm ``backend/.rag_cache/`` would."""
        from backend.services.rag import strip_placeholders

        chunks = []
        for filename, content in self.DOCS.items():
            _, body = parse_frontmatter(content)
            body, _ = strip_placeholders(body)
            chunks.extend(self._previous_chunker(filename, body))

        cache_dir.mkdir(parents=True, exist_ok=True)
        n = len(chunks)
        np.savez_compressed(
            cache_dir / "embeddings.npz",
            ids=np.array([c.id for c in chunks], dtype=object),
            contents=np.array([c.content for c in chunks], dtype=object),
            sources=np.array([c.source for c in chunks], dtype=object),
            sections=np.array([c.section for c in chunks], dtype=object),
            types=np.array([c.type for c in chunks], dtype=object),
            summaries=np.array([""] * n, dtype=object),
            tags_json=np.array(["[]"] * n, dtype=object),
            # A distinct non-zero vector per chunk is enough: these must never
            # be served, so their value is irrelevant to the assertion.
            embeddings=np.eye(n, dtype=np.float32),
        )
        meta = {
            "model": "all-MiniLM-L6-v2",
            "document_hash": RAGPipeline._compute_documents_hash(self.DOCS),
            "chunk_filter_version": self.PREVIOUS_VERSION,
            "chunk_count": n,
        }
        (cache_dir / "embeddings.json").write_text(json.dumps(meta), encoding="utf-8")
        return n

    def test_previous_version_is_not_the_current_one(self):
        """The guard only means something if the version actually moved."""
        from backend.services.rag import CHUNK_FILTER_VERSION

        assert CHUNK_FILTER_VERSION != self.PREVIOUS_VERSION, (
            "CHUNK_FILTER_VERSION was not bumped, so a cache written before "
            "this change would still be restored and every fix undone"
        )

    def test_previous_cache_is_rejected_and_recomputed(self, tmp_path):
        cache_dir = tmp_path / "cache"
        old_count = self._write_previous_cache(cache_dir)

        fresh = RAGPipeline(chunk_size=400, cache_dir=cache_dir)
        fresh.ingest_documents(self.DOCS)

        assert not [c for c in fresh.chunks if _wikilink_only(c.content)], (
            "a stale pre-fix cache was restored: wikilink sections are back"
        )
        assert not [
            c for c in fresh.chunks
            if re.match(r"^#\s", c.content) and "\n" not in c.content.strip()
        ], "a stale pre-fix cache was restored: bare H1 titles are back"
        assert len(fresh.chunks) != old_count, (
            f"the stale cache ({old_count} chunks) was served instead of "
            f"recomputed ({len(fresh.chunks)} chunks)"
        )
        rewritten = json.loads((cache_dir / "embeddings.json").read_text(encoding="utf-8"))
        from backend.services.rag import CHUNK_FILTER_VERSION

        assert rewritten["chunk_filter_version"] == CHUNK_FILTER_VERSION, (
            "the rejected cache was not rewritten at the current version"
        )


class TestRetrievalThresholdIsHonest:
    """What the 0.3 score filter actually does, measured — not assumed.

    THE BRIEF THIS REPLACES
    ----------------------
    A review claimed the threshold was "inert": the lowest top-1 cosine across
    the 49-question labelled set is 0.414, well above the 0.3 default, so the
    filter "never removes anything". The 0.414 reproduced exactly (measured,
    real 37-page wiki, real ``all-MiniLM-L6-v2``, real ``expand_query``). The
    INFERENCE DOES NOT.

    ``retrieve()`` filters every candidate, THEN sorts descending and slices
    ``top_k``. So a score filter can only change what the caller receives when
    fewer than ``top_k`` candidates survive it. Two things are true at once:

      * The filter is NOT inert. Over the 49 x 125 (question, chunk) matrix it
        dropped 1792 of 6125 pairs -- 29% of the candidate pool. The
        full-matrix minimum is -0.0906, not 0.414: 0.414 is the best chunk per
        question, which is a different statistic entirely.
      * On the DIRECT path it could not change an answer on that corpus: at
        least 12 of its 125 chunks cleared 0.3 for every one of the 49
        questions, so ``top_k`` up to 12 was unaffected.
      * On the PRODUCTION path (``get_context_string`` /
        ``get_chunks_with_scores``, which also apply ``detect_doc_type``) it
        DID change an answer. ``detect_doc_type`` mapped "cuentame sobre ti en
        treinta segundos" to ``profile``, leaving 1 surviving chunk at 0.3081
        against a second-best of 0.2252. The margin was 0.0081.

    WHY THE CORPUS MOVED
    --------------------
    Those figures are the real wiki's, and the real wiki is gitignored and
    private — so a guard built on them could only ever run on one machine.
    The measurements below are re-taken on ``tests/fixtures/retrieval_corpus/``
    (42 pages, 121 chunks, 49 labelled questions, all invented) and every floor
    is re-derived from it. Where a number did not survive the move, the
    assertion changed shape rather than the number changing silently; both
    such changes are called out in the individual docstrings, and they are the
    two places where a real finding about the TEST came out of this work:

      * "the direct path is unaffected" was never a property of the code, only
        of a corpus big enough to hide the filter at ``top_k=3``. On the
        fixture the filter legitimately bites 3 questions, dropping chunks
        that score 0.20-0.29.
      * the shipped 0.3 is now exercised by a genuinely bilingual labelled
        set, which exposed that ``all-MiniLM-L6-v2`` is English-only: an
        English question against a Spanish page retrieves nothing at all. The
        real corpus's 49 questions were all Spanish and could not have found
        this. It is pinned in
        ``test_english_question_against_a_spanish_page_retrieves_nothing``.

    The default stays at 0.3. The surface that invited the wrong conclusion is
    still gone: no per-call ``threshold`` override, because no production
    caller passes one and only one value has ever been measured.
    """

    def test_the_filter_is_live_and_drops_a_fourth_of_the_candidate_pool(self, real_wiki_pipeline):
        """Guard the OTHER direction: the filter must not be vacuous.

        Without this, every "the default drops nothing" test below could be
        satisfied by a filter that never binds at all (a threshold of -1, a
        comparison against a constant). Pinning that 0.3 really does discard
        29% of the matrix is what makes them non-vacuous.

        The count is taken from ``retrieve()`` with a ``top_k`` larger than the
        corpus, so the number of returned chunks IS the number of survivors.
        Recomputing the cosine here instead would test the test: an earlier
        draft of this assertion re-implemented the dot product and therefore
        passed unchanged when ``retrieve()``'s comparison was mutated to
        ``score >= -1.0``. Verified by mutation — do not "simplify" this back
        into local arithmetic.
        """
        rag = real_wiki_pipeline
        shipped = rag.threshold
        assert shipped == 0.3, (
            f"the shipped default is {shipped}, not 0.3. Raising it is not free: "
            f"measured over the labelled set, 0.40 costs 0 questions of recall@3, "
            f"0.45 costs 1, 0.50 costs 3, 0.60 costs 7."
        )

        deeper = len(rag.chunks) * 2
        survivors = sum(len(rag.retrieve(case.question, top_k=deeper)) for case in LABELLED_CASES)
        total = len(LABELLED_CASES) * len(rag.chunks)
        dropped = total - survivors

        assert total == len(LABELLED_CASES) * len(rag.chunks), (
            f"expected {len(LABELLED_CASES)} questions x {len(rag.chunks)} chunks "
            f"= {total} pairs, scored {survivors} survivors"
        )
        assert dropped > 0.15 * total, (
            f"the 0.3 filter now drops only {dropped}/{total} "
            f"({dropped / total:.1%}) of the candidate pool. If it has stopped "
            f"binding, every 'the default drops no real result' test in this "
            f"class is vacuously true and this file is guarding nothing. It was "
            f"measured dropping 1792/6125 (29%)."
        )

    def test_shipped_default_drops_no_result_the_unfiltered_run_keeps(
        self, real_wiki_pipeline
    ):
        """The direct path: the shipped 0.3 must cost the caller almost nothing.

        This is the non-vacuous version of the review's claim. It is stated
        behaviourally -- the same chunks, in the same order, with and without
        the filter -- rather than as a statistic about top-1, because top-1 is
        precisely the statistic that made the original claim look safe while
        the filter was still dropping a third of the pool.

        FIXTURE-DERIVED, AND THE ASSERTION HAD TO CHANGE SHAPE. The original
        asserted the filtered and unfiltered top-3 were *identical* for all 49
        questions. That was true of the real 37-page corpus for a reason that
        has nothing to do with the code: at least 12 of its 125 chunks cleared
        0.3 for every question, so ``top_k`` up to 12 was never reached. It is
        a property of that corpus's score distribution, not a guarantee this
        pipeline makes.

        On the 121-chunk fixture the guarantee is false and the filter
        legitimately bites: 3 of 49 questions have fewer than three survivors,
        and for each the dropped chunks score 0.20-0.29 — weak chunks the
        caller was better off without. So asserting "identical" would be
        asserting that the filter never binds at top_k=3, which is false in
        general and would only be true of a corpus large enough to hide it.

        The bar is therefore two-sided and still binds in the direction that
        matters. RAISING the default must not starve the direct path, so the
        number of questions whose top-3 changes is capped at the measured
        fixture figure. LOWERING it must not be rewarded, so the minimum
        number of survivors per question is floored as well. A threshold that
        stopped filtering, or one that filtered half the pool, fails one of
        the two.
        """
        rag = real_wiki_pipeline
        shipped = rag.threshold
        changed, survivors = [], []
        try:
            for case in LABELLED_CASES:
                filtered = rag.retrieve(case.question, top_k=3)
                rag.threshold = -1.0
                unfiltered = rag.retrieve(case.question, top_k=3)
                rag.threshold = shipped

                filtered_sources = [_norm_source(c.source) for c, _ in filtered]
                if filtered_sources != [_norm_source(c.source) for c, _ in unfiltered]:
                    changed.append(case.question)
                survivors.append(len(filtered))
        finally:
            rag.threshold = shipped

        assert len(changed) <= 3, (
            f"the shipped threshold of {shipped} changed the top-3 for "
            f"{len(changed)} of {len(LABELLED_CASES)} questions: {changed}. On "
            f"this corpus it changed 3. Either the default got more aggressive "
            f"or the corpus got harder; both need a deliberate re-baseline."
        )
        assert min(survivors) >= 1, (
            f"the shipped threshold leaves at least one question with NO "
            f"result on the direct path: {survivors.count(0)} of "
            f"{len(survivors)} questions returned nothing."
        )

    def test_shipped_default_never_empties_the_production_context(self, real_wiki_pipeline):
        """The production path: no question in the labelled set may go ungrounded.

        ``get_context_string`` additionally runs ``detect_doc_type``, which is a
        much narrower candidate pool than the direct path, and that is where
        the default actually bites. An empty context is the failure this file
        already documents as worse than a loose one.

        On the real corpus this passed with a 0.0081 margin on its tightest
        question, and the docstring used to say the coupling to the live wiki
        was an accepted limit. That limit is gone: the corpus is now
        ``tests/fixtures/retrieval_corpus/``, committed, so a failure here is
        always a code change or a corpus edit the author made on purpose —
        never "the owner's personal pages are not on this machine".

        FIXTURE-DERIVED: the tightest margin here is 0.1096, on the English
        backend question whose page is itself in English. Comfortable, and
        that is the point: a floor that only passes on one machine teaches its
        reader nothing.
        """
        rag = real_wiki_pipeline
        empty = [c.question for c in LABELLED_CASES if not rag.get_context_string(c.question, top_k=3)]
        assert not empty, (
            f"the shipped threshold of {rag.threshold} leaves {len(empty)} of "
            f"{len(LABELLED_CASES)} labelled questions with NO context at all: "
            f"{empty}. An interview answer with no grounding from the profile "
            f"is worse than a loose one. Either lower the default (measured to "
            f"cost 0 recall questions down to 0.40) or fix the detect_doc_type "
            f"misroute, which is the actual cause here."
        )

    def test_english_question_against_a_spanish_page_retrieves_nothing(self, real_wiki_pipeline):
        """KNOWN FAILURE, marked not deleted: the embedder is English-only.

        ``all-MiniLM-L6-v2`` — the model this pipeline ships — is an English
        model. Spanish prose is a long way from its training distribution, and
        the top score for a Spanish question over a Spanish page sits at
        0.41-0.51, comfortably above the 0.3 filter. Cross the two and the
        score falls to nothing: the question below retrieves ZERO chunks even
        with the threshold disabled, so this is not a threshold problem.

        It was found by doing the thing this corpus exists to make possible:
        putting a genuinely bilingual labelled set in front of the pipeline.
        The real corpus's 49 questions are all Spanish, so the real
        measurement could not have found it.

        Why it is not in ``LABELLED_CASES``: that set is the baseline every
        figure in this file is measured against, and a question that cannot
        return a result makes the whole class un-runnable. Moving it here
        keeps the assertion — the same assertion, "no context is worse than a
        loose one" — alive and in one place, with its cause named, rather
        than dropping the question and hoping nobody asks.

        What would clear it: a multilingual embedder. That is a product
        decision, not a test edit, and it is the owner's call.
        """
        rag = real_wiki_pipeline
        question = "how do you document for the person who comes next"
        gold = "opinions/documentacion-para-quien-viene.md"

        shipped = rag.threshold
        try:
            rag.threshold = -1.0
            unfiltered = rag.retrieve(question, top_k=3)
            rag.threshold = shipped
        finally:
            rag.threshold = shipped

        assert _norm_source(gold) in [_norm_source(c.source) for c, _ in unfiltered], (
            "the Spanish page is indexed and reachable by an English question "
            "now — the premise of this xfail has changed, so the embedder is "
            "either no longer English-only or the corpus changed. Re-measure "
            "and either drop the mark or record the new cause."
        )
        assert not rag.get_context_string(question, top_k=3), (
            "an English question against a Spanish page now returns context. "
            "The embedder may no longer be English-only — if so this xfail is "
            "wrong and the question belongs back in LABELLED_CASES."
        )

    def test_retrieve_takes_no_per_call_threshold(self):
        """The per-call override is dead surface and must not come back.

        No production caller passes it: ``main.py`` constructs ``RAGPipeline``
        with the instance default, and both production entry points call
        ``retrieve(query, top_k=..., doc_type=...)``. A second, per-call
        threshold is one more way for a reader to believe the filter is doing
        something configurable when only one value has ever been measured.

        This is the assertion that failed before the change; it is here so the
        removal is a contract rather than an edit someone can revert.
        """
        import inspect

        params = [
            name
            for name in inspect.signature(RAGPipeline.retrieve).parameters
            if name != "self"
        ]
        assert params == ["query", "top_k", "doc_type"], (
            f"retrieve() takes {params}; the per-call `threshold` override is "
            f"back. No production caller passes it, only one value has ever "
            f"been measured, and it is the surface that made this filter look "
            f"configurable when it is not."
        )


class TestEmbedderProperty:
    """Read-only embedder exposure, and the TF-IDF-fallback guard behind it.

    Kept, not deleted with the semantic answer cache that first needed it: the
    property still has three other readers, which use it as a readiness check
    ("is there a real sentence embedder, or did this run fall back to TF-IDF?").
    Only the rationale in its docstring was stale.
    """


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

class TestSemanticAnswerCacheWasNotViable:
    """Why there is no semantic answer cache, measured rather than asserted.

    A similarity cache in front of the LLM was built, configured, shipped and
    unit-tested, and could never once return a hit. It was removed; this class
    is what remains, so the decision is a measurement someone can re-check
    instead of a story in a commit message.

    It is also the answer to "why did the tests not catch this". The old suite
    drove the cache with a ``FakeEmbedder`` whose fallback vectors were
    MD5-seeded random numbers, which makes unrelated texts near-orthogonal.
    Every threshold looked correct against that, including the one that was
    unreachable against the real model. A calibration validated against a stub
    is worse than no calibration, because it is read as evidence.

    Everything below uses the real ``all-MiniLM-L6-v2`` over the real Spanish
    question surface. If these tests start failing, the embedder changed, and a
    paraphrase cache may have become viable -- re-measure before adding one.
    Do not simply lower the threshold; the overlap is the finding, not the
    threshold's fault.

    Scope note: the population is a small hand-built set, not a corpus, so
    these are structural claims ("the classes overlap") rather than a recall
    figure to quote. A larger set moves the numbers, not the overlap.
    """

    #: Base question plus two things a recruiter actually says instead of it.
    GROUPS = (
        ("¿Puedes presentarte brevemente?",
         "Háblame un poco de ti, por favor.",
         "Preséntate ante mí en un minuto."),
        ("¿Por qué quieres trabajar con nosotros?",
         "¿Qué te atrae de esta empresa?",
         "Dime por qué deberíamos contratarte."),
        ("¿Cómo manejas la presión o los plazos cerrados?",
         "Cuando tienes mucho trabajo, ¿qué haces?",
         "¿Cómo trabajas bajo estrés?"),
        ("¿Has trabajado en equipo? Cuéntanos un ejemplo.",
         "¿Cómo te llevas con tus compañeros?",
         "Ejemplifica una vez que colaboraste con un equipo."),
        ("¿Qué herramientas o tecnologías dominas?",
         "¿Con qué programas trabajas mejor?",
         "Dime tus habilidades técnicas."),
        ("¿Qué idiomas hablas?",
         "¿Hablas inglés u otro idioma?",
         "Dime tus niveles de idiomas."),
        ("¿Qué salario esperas para este puesto?",
         "¿Cuánto quieres cobrar?",
         "¿Expectativas salariales para el puesto?"),
        ("¿Por qué cambiaste de trabajo anteriormente?",
         "¿A qué se debe tu cambio de empresa?",
         "Cuéntanos por qué te fuiste del último trabajo."),
        ("¿Qué harías si un cliente se queja?",
         "¿Cómo actúas ante una queja?",
         "Cuéntanos cómo resuelves un problema con un cliente."),
        ("¿Por qué deberíamos contratarte?",
         "¿Qué te hace ser el candidato adecuado?",
         "Dame tres razones para contratarte."),
    )

    #: The threshold the module shipped with. Named here so the value under
    #: test is the value that was actually in config.py, not a round number.
    SHIPPED_THRESHOLD = 0.93

    @classmethod
    @pytest.fixture(scope="class")
    def similarities(cls):
        """(positives, negatives) cosine over the raw question, L2-normalised.

        Raw, not ``expand_query``: that is what the cache scored, and
        expansion is retrieval-oriented and would skew question-to-question
        similarity.
        """
        rag = build_pipeline()
        if rag._use_tfidf:
            pytest.skip("TF-IDF fallback active: the measurement needs a real embedder")
        model = rag.embedder

        base = [g[0] for g in cls.GROUPS]
        paras = [p for g in cls.GROUPS for p in g[1:]]
        vecs = model.encode(base + paras, show_progress_bar=False)
        vecs = np.asarray(vecs, dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

        b, p = vecs[: len(base)], vecs[len(base):]
        positives = [float(b[i] @ p[2 * i + j]) for i in range(len(base)) for j in (0, 1)]
        negatives = [
            float(b[i] @ p[k])
            for i in range(len(base))
            for k in range(len(p))
            if k // 2 != i
        ]
        return positives, negatives


    def test_no_paraphrase_reaches_the_shipped_threshold(self, similarities):
        """Recall at the shipped threshold is zero, so the cache never hit."""
        positives, _ = similarities
        hits = [s for s in positives if s >= self.SHIPPED_THRESHOLD]
        assert hits == [], (
            f"{len(hits)}/{len(positives)} paraphrases now clear "
            f"{self.SHIPPED_THRESHOLD} (max {max(positives):.4f}). The embedder "
            "or the question set changed -- re-measure before reintroducing a "
            "paraphrase cache."
        )

    def test_the_two_classes_overlap(self, similarities):
        """The distributions are not separable, which is the real finding.

        A threshold is only usable if it sits above every negative (no false
        positives) and below the typical positive. This asserts it is not,
        directly, so the reason the cache was retired survives any later
        tuning of the threshold.
        """
        positives, negatives = similarities
        assert min(positives) < max(negatives), (
            "now separable: every paraphrase scores above the closest "
            "different question. That is the precondition for a cache like "
            "this, so re-measure it rather than assuming."
        )
        assert np.median(positives) < max(negatives), (
            f"positive median {np.median(positives):.4f} is below negative max "
            f"{max(negatives):.4f}: the classes overlap, so any threshold that "
            "catches paraphrases also catches unrelated questions."
        )

    def test_the_zero_false_positive_floor_is_useless(self, similarities):
        """Even the safest possible threshold would serve almost nothing.

        This is the number that closes the question. Lowering the threshold to
        the highest negative is the best a paraphrase cache could do without
        risking a confidently-wrong answer, and the answer is a handful of
        pairs -- for a table of retained raw questions.
        """
        positives, negatives = similarities
        floor = max(negatives)
        served = [s for s in positives if s >= floor]
        assert len(served) < len(positives) * 0.25, (
            f"the zero-false-positive floor {floor:.4f} now serves "
            f"{len(served)}/{len(positives)} paraphrases. If that is high "
            "enough to be worth a cache, this decision should be revisited."
        )

    def test_the_module_and_its_configuration_are_gone(self):
        """Nothing in the service graph or config still refers to the cache.

        A retirement that leaves the config keys behind is a retirement a
        future reader can undo by accident, and one that leaves a table behind
        keeps storing nothing about everyone for no reason.
        """
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("backend.services.semantic_cache")

        from backend import container, config as config_module

        assert not hasattr(container, "semantic_cache")
        assert not hasattr(config_module.config, "SEMANTIC_CACHE_ENABLED")
        for key in ("TTL_DAYS", "MAX_ROWS", "THRESHOLD"):
            assert not hasattr(config_module.config, f"SEMANTIC_CACHE_{key}")

        import backend.main as main_mod

        assert not hasattr(main_mod, "semantic_cache")
