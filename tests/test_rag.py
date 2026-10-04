"""Tests for RAG pipeline with known documents."""

import json
import logging
import re
import numpy as np
import pytest
from pathlib import Path

from backend.services.rag import (
    Chunk,
    RAGPipeline,
    embedding_text,
    expand_query,
    is_bare_heading,
    parse_frontmatter,
    split_sections,
)

# The labelled retrieval set, the real corpus, and the guard's provenance.
#
# WHY THE REAL wiki/ AND NOT A FIXTURE
# ------------------------------------
# This file used to measure `tests/fixtures/retrieval_corpus/`, an "entirely
# invented" stand-in, on the stated ground that the real wiki "is gitignored
# and private". That ground was false and is now contradicted in the repository
# itself: `git ls-files wiki` returns 50 files, they are in `origin/main`, and
# `.github/workflows/tests.yml` checks them out on every run. A clean clone has
# the corpus. The guard could always have measured it.
#
# The stand-in was not neutral either. It reproduced this wiki's structure
# exactly -- the same eight directories, 13 identical filenames, 9 of 11 FAQ
# slugs byte-identical, 14 of 42 pages present under the same name -- and the
# check that certified it as independent read named entities out of page
# BODIES and never looked at a filename or a directory, so it printed
# `shared 0 / OK` over a clone. Its gold pages also sat about 6x further from
# their nearest distractor than real pages do (median gold-vs-distractor cosine
# margin +0.0360 fixture vs +0.0060 real), which is precisely the condition
# under which a recall floor stops being sensitive to the retriever.
#
# The cost, measured: on the 49 questions that were authored against the REAL
# pages, recall@3 is 0.6531. On the fixture it read 0.776 -- and the same
# current chunker read 0.776 there against the pre-refactor chunker's 0.816.
# The "improvement" was a corpus swap, and the fixture floor was rejecting the
# better chunker while the production corpus went unconsulted.
#
# So everything below that needs a corpus reads the real `wiki/`, every floor
# is re-derived from it, and every docstring says so. A floor that keeps its
# value while its corpus changes is not a floor.
from tests.real_wiki import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CORPUS_DIGESTS,
    FLOOR_CHUNKER,
    FLOOR_EMBEDDER,
    FLOOR_SET_BY,
    FLOOR_VIEW,
    LABELLED_CASES,
    MEASUREMENTS,
    TOLERATED_QUESTIONS,
    WIKI_ROOT,
    build_pipeline,
    corpus_digest,
    guard_blocker,
    load_documents,
    measurement_for,
    resolved_cases,
    unresolved_gold_pages,
    wiki_is_present,
)

#: Emitted by the guard's skip and by its sentinel, so the reason is in the
#: output in every mode rather than only under ``-rs``.
GUARD_BANNER = "!! RETRIEVAL GUARD DID NOT RUN -- THIS IS NOT A PASS !!"

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
    """The real corpus must reach the LLM free of [TODO placeholders.

    CORPUS: the real ``wiki/`` -- tracked, 50 files, present on a clean clone
    and in CI. It is not a machine-local artefact, so this is an ordinary test
    and not a conditional one. The filter it guards is a read-time transform
    and the corpus still carries real ``[TODO`` markers in the sections a
    recruiter asks about, which is what makes the negative test meaningful
    rather than vacuous.
    """

    def test_no_chunk_from_the_corpus_contains_todo(self, wiki_targets, real_wiki_documents):
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        documents = real_wiki_documents
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

    def test_the_corpus_actually_carries_placeholders_to_strip(
        self, wiki_targets, real_wiki_documents
    ):
        """The negative test above is only meaningful if there is something to strip.

        Without this, a corpus edit that deleted every ``[TODO`` would turn
        the guard into a tautology and nothing would say so.
        """
        documents = real_wiki_documents
        raw = [
            name for name, content in documents.items() if "[TODO" in content
        ]
        assert len(raw) >= 2, (
            f"expected markers in several pages, found {raw}. The placeholder "
            f"stripping is no longer being tested against real input."
        )
        # ...and the page that carries one also keeps the prose around it.
        # On the real corpus the markers sit under English headings -- "What I'd
        # do differently", "Results" -- because that is how these pages are
        # written. The property is not the spelling of a heading but that the
        # marker is inside a section a recruiter asks about, which is why this
        # asserts on the SECTION the marker falls in rather than on the page.
        recruiter_sections = {
            "what i'd do differently", "que haria distinto",
            "results", "resultados", "outcomes", "impact",
        }
        marked_in_recruiter_section = [
            (name, _heading_and_body(section)[0])
            for name, content in documents.items() if "[TODO" in content
            for section in split_sections(content) if "[TODO" in section
            and _heading_and_body(section)[0].strip().lower() in recruiter_sections
        ]
        assert marked_in_recruiter_section, (
            "no [TODO marker sits in a section a recruiter asks about any more, "
            "so the filter is no longer being tested against the input that "
            f"motivated it. Sections carrying a marker: "
            f"{[(n, _heading_and_body(s)[0]) for n, c in documents.items() if '[TODO' in c for s in split_sections(c) if '[TODO' in s]}"
        )


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

    def test_corpus_emits_no_bare_h1_chunk(self, wiki_targets, real_wiki_documents):
        """Guard the real corpus, not just a synthetic document.

        CORPUS: the real ``wiki/``, which is tracked and present in CI. This
        used to run against an invented stand-in and carry a comment saying it
        could not read the real pages; that was false.

        ``index.md`` is excluded here and not excused: it is a generated
        build artifact (``AUTO-GENERATED ... do not edit``) and the loader
        drops it entirely, so it can never reach the chunker in production.
        """
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        offenders = []
        for name, content in real_wiki_documents.items():
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

    CORPUS: the real ``wiki/`` -- tracked, 50 files, checked out by CI. These
    assertions used to run against an invented stand-in whose stated purpose
    was to avoid reading a corpus that was in the index all along.
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

    def test_corpus_emits_no_wikilink_only_chunk(self, wiki_targets, real_wiki_documents):
        """Guard the real corpus."""
        chunks = self._real_corpus_chunks(real_wiki_documents)
        offenders = [(c.source, c.content[:70]) for c in chunks if _wikilink_only(c.content)]
        assert not offenders, f"{len(offenders)} wikilink-only chunk(s): {offenders[:5]}"

    def test_reference_heading_set_is_exactly_what_the_corpus_uses(
        self, wiki_targets, real_wiki_documents
    ):
        """Pin the header set, so a NEW spelling cannot slip through.

        Each entry below is justified by an occurrence in the corpus:
        ``Fuentes``, ``Ver tambien``, ``Ver también`` and ``See also`` all
        appear in live pages, and ``Sources`` appears in
        ``templates/faq-template.md`` — the template the next FAQ is written
        from, so it is a spelling the corpus will produce even though no live
        page uses it yet.

        CORPUS: the real ``wiki/``. The table in ``REFERENCE_HEADINGS`` is the
        production one and each entry's justification is checked against the
        real corpus, which is what makes it a claim rather than a comment.
        """
        from backend.services.rag import REFERENCE_HEADINGS

        used = set()
        for name, content in real_wiki_documents.items():
            if Path(name).name == "index.md":
                continue
            for sec in split_sections(content):
                heading, _ = _heading_and_body(sec)
                if heading.lower() in REFERENCE_HEADINGS:
                    used.add(heading.lower())

        # ``sources`` is justified by the FAQ TEMPLATE, not by a live page, and
        # is checked separately below. The other four are claimed to occur in
        # live content, and one of them -- ``ver también`` -- used to occur on the
        # reduced population only, because the page spelling it with the accent
        # was one of the four untracked FAQ pages; those are committed now, so the
        # two corpora agree on this. The assertion is still "nothing new appeared,
        # and what is missing is missing for a stated reason", not equality with a
        # hard-coded set: a corpus edit may legitimately lose a heading.
        known_unexercised = {"ver también"}
        unexpected = (used - REFERENCE_HEADINGS) | (REFERENCE_HEADINGS - used - known_unexercised - {"sources"})
        assert not unexpected, (
            f"reference headings drifted: the corpus uses {sorted(used)}, the "
            f"table declares {sorted(REFERENCE_HEADINGS)}, and these are "
            f"neither exercised nor accounted for: {sorted(unexpected)}. A new "
            f"spelling has to be added to REFERENCE_HEADINGS and justified; a "
            f"lost one has to be removed."
        )
        template = (self._wiki_root() / "templates" / "faq-template.md").read_text(
            encoding="utf-8"
        )
        assert "## Sources" in template, (
            "'sources' is in the table only because the FAQ template spells it "
            "that way; if the template changed, re-justify or drop the entry"
        )

    def test_dropping_reference_links_loses_no_answer_content(
        self, wiki_targets, real_wiki_documents
    ):
        """The 'loss is zero' argument, measured rather than asserted.

        Every link in every reference section of the corpus either names no
        document at all (an unfilled ``[[...]]`` placeholder, which cannot
        state a relationship) or names a document that is itself indexed and
        therefore answers that topic on its own.
        """
        documents = real_wiki_documents
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
        return WIKI_ROOT

    def _real_corpus_chunks(self, documents: dict):
        """Chunk the corpus the way production does, page by page.

        The documents come from the ``real_wiki_documents`` fixture rather
        than from a fresh loader call, and that is load-bearing rather than
        tidy: ``CandidateProfile.load()`` FALLS BACK to the compiled
        ``candidate/`` directory when ``wiki/`` is missing, so a test that
        loaded independently would silently measure 38 compiled files
        instead of the corpus and report a pass. Measured that way on a
        checkout with no ``wiki/``, the placeholder guard chunked 38
        documents and asserted nothing useful about the real pages.
        """
        rag = RAGPipeline(chunk_size=400, chunk_overlap=50)
        for name, content in documents.items():
            yield from rag._chunk_document(name, content)


def _require_measurable_corpus():
    """Skip with an unmistakable reason, or return the documents.

    The skip is not the signal. ``pytest -q`` renders a skip as a single ``s``
    and a count, which is exactly how a guard that measured nothing comes to
    read as a guard that measured something. So the skip here is only half the
    answer: ``TestTheRetrievalGuardActuallyRan`` re-derives the same
    precondition and FAILS when it holds, which is what makes the run red and
    what carries the full explanation. The reason here is kept to one loud line
    so that thirteen of them are readable.
    """
    if not wiki_is_present():
        pytest.skip(
            f"{GUARD_BANNER} the real-corpus guard could not run: "
            f"{guard_blocker()} 49 labelled questions, 0 scored. "
            f"TestTheRetrievalGuardActuallyRan FAILS on this state."
        )
    documents = load_documents()
    blocker = guard_blocker(documents)
    if blocker:
        missing = unresolved_gold_pages(documents)
        affected = sum(1 for c in LABELLED_CASES if c.primary in missing)
        pytest.skip(
            f"{GUARD_BANNER} the real-corpus guard could not run: {blocker} "
            f"The {len(LABELLED_CASES)}-question floor is NOT applied to the "
            f"remaining {len(LABELLED_CASES) - affected}. "
            f"TestTheRetrievalGuardActuallyRan FAILS on this state."
        )
    return documents


@pytest.fixture(scope="module")
def real_wiki_documents():
    """The real ``wiki/``, loaded once through the production loader.

    Separate from the pipeline so the corpus-shaped invariants (types, TODO
    markers, bare H1s, wikilink sections) can use it without paying for the
    embeddings.
    """
    return _require_measurable_corpus()


@pytest.fixture(scope="module")
def real_wiki_pipeline(real_wiki_documents):
    """The real wiki, ingested once with real embeddings.

    Module-scoped because embedding the corpus costs ~100s. This is the only
    test that can catch a chunking "cleanup" that quietly degrades answers:
    a count assertion proves nothing about what the LLM actually receives.

    CORPUS: ``wiki/`` -- 50 files, tracked, present on a clean clone and
    checked out by CI. It is named ``real_wiki_pipeline`` because that is what
    it is, which is the whole point.

    ``cache_dir`` is left at its ``None`` default, so this cannot write
    ``backend/.rag_cache/``.
    """
    from backend.services.rag import RAGPipeline

    rag = RAGPipeline(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    rag.ingest_documents(real_wiki_documents)
    return rag


def _norm_source(source: str) -> str:
    """Document keys are ``str(Path.relative_to(...))``, so separators vary."""
    return source.replace("\\", "/")


def _strict_hit(case, results, k: int = 3) -> bool:
    """True when the case's PRIMARY gold page is in the top ``k``.

    Strict on purpose. One case in the set is a documented duplicate -- two
    pages genuinely answer "why leave retail for DAM" -- and a lenient rule
    would let that pair mask the FAQ page losing its own retrieval key, which
    is exactly the regression the H1 decision turns on.
    """
    return case.primary in [_norm_source(c.source) for c, _ in results[:k]]


def _body_of(chunk_text: str) -> str:
    """The chunk's answer-bearing text: everything after its leading heading."""
    lines = [l for l in chunk_text.split("\n") if l.strip()]
    if lines and re.match(r"^#{1,3}\s", lines[0]):
        return " ".join(lines[1:])
    return " ".join(lines)


class TestRetrievalRegressionGuard:
    """Recruiter questions must still find the page that holds their answer.

    Counts prove nothing about quality. This is the guard that stops a
    "cleanup" from silently degrading answers. It measures the REAL corpus
    over the 49 questions that were hand-authored against the real pages
    (recovered from git at ``769f30f``), and its floor is derived from that
    measurement rather than chosen.

    WHAT IT MEASURES, PRECISELY
    ---------------------------
    Strict recall@3: the fraction of the 49 questions whose PRIMARY gold page
    appears in the top 3 of ``retrieve(question, top_k=3)``. Reported on every
    run, pass or fail, together with the corpus, the chunker, the embedder and
    the per-question misses, because a floor nobody can re-derive is a
    threshold somebody will eventually move to make a build green.

    THE FLOOR
    ---------
    Measured 0.6531 (32 of 49) on the real corpus at 400/50. The floor is
    0.6122 -- that measurement minus TWO questions, because one question is
    1/49 = 0.0204 of recall and the chunk-size sweep already recorded a single
    question moving recall by 0.034 on a smaller set. A two-question wobble is
    not a result; a three-question loss is. The floor is written as an
    expression in ``tests/real_wiki.py`` so that raising the measurement
    without deciding about the tolerance cannot happen by accident.

    THE OLD FLOOR, AND WHY IT WAS WRONG
    -----------------------------------
    The previous guard measured ``tests/fixtures/retrieval_corpus/`` -- a
    structural clone of this very wiki -- and held 0.776 against a 49-question
    set re-pinned onto invented pages. Its numbers were not wrong about the
    fixture; they were wrong about the product. On the real corpus the same
    code reads 0.6531, and the fixture's floor of 0.776 was REJECTING the
    pre-refactor chunker's 0.816 on the fixture while the real retriever went
    unmeasured. A guard that blocks the correct fix is worse than no guard.

    THE H1 TRADE, STATED AS A NUMBER
    --------------------------------
    ``split_sections`` re-attaches a document's own H1 to the body it titles.
    Measured on the real corpus, that costs recall@3: 0.7755 with it off
    against 0.6531 with it on -- six questions. It buys, for 36 of the same 49
    questions, a top-1 context chunk with a real body (median 36 words) instead
    of a bare heading (median 0 words) that restates the interviewer's own
    question. Reverting would raise the floor's measurement by 0.122 and hand
    the model a heading as its first piece of evidence for three quarters of
    the questions. That is a trade, not a bug, so it is reported here rather
    than resolved by moving a number.
    """

    def test_recall_at_three_meets_its_derived_floor(
        self, real_wiki_pipeline, real_wiki_documents, capsys
    ):
        """The one floor, on the real corpus, with its provenance printed.

        Two populations, two floors, and the run says which one it used:

          * ``full``    the working tree of the checkout running this guard.
            Measured 40/49 = 0.8163, floor 0.7755.
          * ``reduced`` ``git show HEAD:`` -- what ``git clone`` serves, and the
            only population anybody else ever reads. Measured 40/49 = 0.8163,
            floor 0.7755 on the COMMITTED corpus rather than on a working tree
            (see ``tests/test_committed_corpus_figures.py``).

        Both are 49 questions and 37 pages since commit ``efda998`` committed
        the four FAQ pages that used to make ``reduced`` a 41-question
        population. They are told apart by a digest of the served corpus, not by a
        count, and the floor they share -- 0.8163 both -- is the coincidence that
        makes looking necessary.

        A third corpus is a refusal, not a fallback: see ``measurement_for``.
        The report below prints the corpus, the chunker, the embedder, the
        population, the measurement, the floor and every miss -- on every run,
        pass or fail.
        """
        measurement = measurement_for(real_wiki_documents)
        assert measurement is not None, (
            f"the corpus resolves {len(resolved_cases(real_wiki_documents))} of "
            f"{len(LABELLED_CASES)} labelled questions and there is no floor "
            f"calibrated for that population. See tests/real_wiki.py."
        )
        cases = resolved_cases(real_wiki_documents)

        misses, hits = [], 0
        for case in cases:
            results = real_wiki_pipeline.retrieve(case.question, top_k=3)
            if _strict_hit(case, results):
                hits += 1
            else:
                got = [_norm_source(c.source) for c, _ in results]
                misses.append((case.question, case.primary, got))

        measured = hits / len(cases)
        floor = measurement.floor
        with capsys.disabled():
            print("\n" + "=" * 72)
            print("RETRIEVAL GUARD -- measured on the REAL corpus")
            print(f"  corpus    : {measurement.corpus}")
            print(f"  chunker   : {FLOOR_CHUNKER}")
            print(f"  embedder  : {FLOOR_EMBEDDER}")
            print(f"  view      : {FLOOR_VIEW}")
            print(f"  population: {measurement.name} -- {len(cases)} of "
                  f"{len(LABELLED_CASES)} labelled questions, gold pages resolved")
            unresolved = unresolved_gold_pages(real_wiki_documents)
            if unresolved:
                print(f"              not resolved (absent from the index): {unresolved}")
            print(f"  chunks    : {len(real_wiki_pipeline.chunks)}")
            print(f"  measured  : recall@3 = {measured:.4f}  ({hits}/{len(cases)})")
            print(f"  floor     : {floor:.4f}  = {measurement.recall3:.4f} "
                  f"- {TOLERATED_QUESTIONS}/{measurement.questions}")
            print(f"  set by    : {FLOOR_SET_BY}")
            if misses:
                print(f"  misses ({len(misses)}):")
                for question, gold, got in misses:
                    print(f"    {question!r}\n        want {gold}\n        got  {got}")
            print("=" * 72)

        assert measured >= floor, (
            f"recall@3 on the real wiki fell to {measured:.4f} "
            f"({hits}/{len(cases)}), below the floor {floor:.4f}.\n"
            f"  corpus     : {measurement.corpus}\n"
            f"  population : {measurement.name} ({len(cases)} of {len(LABELLED_CASES)})\n"
            f"  chunker    : {FLOOR_CHUNKER}\n"
            f"  embedder   : {FLOOR_EMBEDDER}\n"
            f"  view       : {FLOOR_VIEW}\n"
            f"The floor was derived from a measurement, not chosen to make a run "
            f"pass: {measurement.recall3:.4f} minus {TOLERATED_QUESTIONS} "
            f"question(s) of noise at n={measurement.questions}. So this is a real "
            f"regression of {measurement.hits - hits} question(s) -- fix the "
            f"retriever or the chunker, and if the fix is real then re-derive the "
            f"floor in tests/real_wiki.py and say in the commit message what the "
            f"old value was, what the new one is, what moved it, and what "
            f"regression the move would now tolerate. Do not adjust the number to "
            f"make this green.\n"
            + "\n".join(f"  MISS {q!r} want {g}" for q, g, _ in misses)
        )

    def test_the_floor_is_a_function_of_the_measurement_not_a_literal(self):
        """A floor that can drift from its measurement is a coincidence.

        Pins the DERIVATION, not the number, so that a future edit cannot
        quietly replace "measured minus two questions" with whatever value makes
        the suite pass. If the tolerance is genuinely wrong, change
        ``TOLERATED_QUESTIONS`` in ``tests/real_wiki.py`` and justify it there.
        """
        for measurement in MEASUREMENTS:
            assert measurement.floor == pytest.approx(
                measurement.recall3 - TOLERATED_QUESTIONS / measurement.questions
            ), (
                f"the {measurement.name!r} floor is no longer the measurement minus "
                f"the stated tolerance: {measurement.floor} vs "
                f"{measurement.recall3 - TOLERATED_QUESTIONS / measurement.questions}"
            )
        assert {m.questions for m in MEASUREMENTS} == {len(LABELLED_CASES)}, (
            "every calibrated population serves the whole labelled set now: "
            f"{sorted(m.questions for m in MEASUREMENTS)} against "
            f"{len(LABELLED_CASES)}. Commit `efda998` committed the four FAQ pages "
            "the 41-question population was defined by excluding, so a number "
            "other than 49 here means somebody reintroduced a missing gold page."
        )
        assert len({CORPUS_DIGESTS[m.name] for m in MEASUREMENTS}) == len(MEASUREMENTS), (
            "the calibrated populations are distinguished by a digest of the served "
            f"corpus, and two of them share one: "
            f"{[CORPUS_DIGESTS[m.name] for m in MEASUREMENTS]}. Identical digests "
            "mean `full` and `reduced` are one corpus under two names, which makes "
            "one of the two rows a figure nobody can check."
        )
        assert {m.name for m in MEASUREMENTS} == set(CORPUS_DIGESTS), (
            "every recorded corpus digest has a Measurement and vice versa: "
            f"{sorted(m.name for m in MEASUREMENTS)} against "
            f"{sorted(CORPUS_DIGESTS)}."
        )

    def test_no_labelled_question_retrieves_nothing_at_all(
        self, real_wiki_pipeline, real_wiki_documents
    ):
        """A question that returns nothing is a different failure from a miss.

        The recall floor tolerates misses, because misses are a ranking
        question and top_k=3 is a choice. An EMPTY result is not: the caller
        gets zero context and the model answers ungrounded. Held separately so
        it cannot be absorbed by the floor.
        """
        cases = resolved_cases(real_wiki_documents)
        empty = [
            c.question for c in cases
            if not real_wiki_pipeline.retrieve(c.question, top_k=3)
        ]
        assert not empty, (
            f"{len(empty)} labelled question(s) retrieved NOTHING: {empty}. An "
            f"interview answer with no grounding from the candidate's own profile "
            f"is worse than a loosely grounded one."
        )

    def test_top_chunk_carries_an_answer_not_just_a_title(
        self, real_wiki_pipeline, real_wiki_documents
    ):
        """The LLM's first piece of context must be an answer, not a restated question.

        This is the OTHER side of the H1 trade, and it is the half a recall
        floor is structurally blind to. recall@3 asks whether the right page is
        among three; it cannot ask whether any of the three says anything. With
        the H1 re-attachment disabled the same code scores 0.7755 instead of
        0.6531 -- and the top-1 context is a bare heading, median 0 body words,
        for 36 of these 49 questions. The recall gain and this test are the
        same decision seen from two ends, so both are asserted: you cannot take
        the +0.122 without failing this.
        """
        cases = resolved_cases(real_wiki_documents)
        title_only = []
        for case in cases:
            results = real_wiki_pipeline.retrieve(case.question, top_k=1)
            assert results, f"{case.question!r} retrieved nothing at all"
            top = results[0][0]
            if not _body_of(top.content).strip():
                title_only.append((case.question, top.content.strip()[:60]))
        assert not title_only, (
            f"the top-1 context chunk is a bare heading for {len(title_only)} of "
            f"{len(cases)} questions — the LLM gets the question back "
            f"instead of the answer: {title_only[:5]}"
        )

    def test_no_top_three_slot_is_spent_on_a_link_list(
        self, real_wiki_pipeline, real_wiki_documents
    ):
        """A slot holding no answer is a slot a real answer cannot occupy."""
        wasted = []
        for case in resolved_cases(real_wiki_documents):
            for c, _score in real_wiki_pipeline.retrieve(case.question, top_k=3):
                if _wikilink_only(c.content) or _norm_source(c.source) == "index.md":
                    wasted.append((case.question, c.source))
        assert not wasted, f"top-k slots wasted on link lists or the index: {wasted}"


class TestTheEmbeddedTextCarriesThePageIdentity:
    """The string that reaches the vector is the page identity, then the body.

    ``backend/services/rag.py``'s ``embedding_text`` docstring names this class
    as the thing that re-measures its prefix order rather than restating it. It
    has to exist, or that docstring is a promise to nobody.

    WHY THE IDENTITY IS IN THE VECTOR AT ALL
    ----------------------------------------
    ``summary`` was already filled in, already printed in the context the LLM
    reads and already written to the cache, and never reached the vector. The
    H1 has the same problem for a different reason: ``split_sections`` merges a
    page's own H1 into the first section, so the title is inside the content of
    exactly ONE of a page's chunks.

    These are unit tests about the STRING, deliberately, not about recall. The
    recall consequences are measured by the guard above and the sweep; what is
    asserted here is the mechanism, because a mechanism that silently stops
    being applied would leave the recall numbers unexplained.
    """

    def test_every_real_chunk_carries_its_pages_h1(self, real_wiki_pipeline):
        """The title that separates a FAQ from its twelve siblings must be there."""
        chunks = real_wiki_pipeline.chunks
        missing = [
            c.id for c in chunks
            if not c.h1.strip()
        ]
        assert not missing, (
            f"{len(missing)} of {len(chunks)} chunks carry no page H1: "
            f"{missing[:5]}. The token that distinguishes one FAQ from its "
            f"siblings is not reaching the vector for these."
        )

    def test_the_embedded_text_starts_with_the_identity_and_ends_with_the_body(
        self, real_wiki_pipeline
    ):
        """Order and both ends. A prefix that does not lead is not a prefix."""
        chunk = real_wiki_pipeline.chunks[0]
        embedded = embedding_text(chunk)

        assert embedded.endswith(chunk.content), (
            "the identity prefix must PREPEND, not replace or append: the body "
            "is what the LLM answers from and it has to be in the vector whole"
        )
        assert embedded != chunk.content, (
            f"{chunk.id} embeds its bare content: the page identity is not "
            f"reaching the vector for this chunk"
        )
        assert chunk.h1 in embedded, f"{chunk.id} does not lead with its H1"
        assert embedded.index(chunk.h1) < embedded.index(chunk.summary), (
            "the order H1, summary, section is the measured one; the H1 is what "
            "separates sibling pages, so it goes first"
        )

    def test_duplicate_and_empty_identity_parts_are_dropped_not_repeated(
        self, real_wiki_pipeline
    ):
        """For a first chunk the section IS the H1, and a repeated title is noise.

        ``split_sections`` re-attaches the leading H1 to the body it titles, so
        ``chunk.section == chunk.h1`` for the first chunk of every page (37 of
        the 124 chunks, one per page). Spelling the title twice in the PREFIX
        makes the embedder spend capacity on it for no gain.

        The count is over the prefix only. The title legitimately appears a
        second time inside the body itself, because the first chunk's content
        opens with the heading it titles -- counting the whole embedded string
        would fail on correct behaviour.
        """
        firsts = [
            c for c in real_wiki_pipeline.chunks
            if c.section.strip() == c.h1.strip()
        ]
        assert firsts, (
            "no chunk has section == h1: split_sections is no longer "
            "re-attaching the leading H1, so this test is not testing what it "
            "thinks it is"
        )
        for chunk in firsts[:20]:
            embedded = embedding_text(chunk)
            assert embedded.endswith(chunk.content)
            prefix = embedded[: -len(chunk.content)].rstrip(". ")
            assert prefix.count(chunk.h1) == 1, (
                f"{chunk.id} spells its title {prefix.count(chunk.h1)} times in "
                f"the identity prefix {prefix!r}"
            )

    def test_a_chunk_with_no_identity_still_embeds_its_content(self):
        """The fallback must not be ``"None. None. None. <content>"``."""
        bare = Chunk(id="x-0", content="# Body\n\nthe answer", source="x.md",
                     section="", tags=[])
        assert embedding_text(bare) == bare.content, (
            "a chunk with no H1, no summary and no section should embed its "
            f"content untouched, got {embedding_text(bare)[:60]!r}"
        )


class TestTheTopKIsCutOverPagesNotChunks:
    """A top-k slot is a slot some OTHER page cannot occupy.

    Measured before this existed: "empezaste como frutero en mercadona no"
    returned ``[dejar-mercadona-para-dam, lo-mas-dificil-dam,
    dejar-mercadona-para-dam]`` — two of three slots on one page, so the model
    was handed three fragments of a story and no fact.

    A recall floor is structurally blind to the harm here: recall@3 asks whether
    the gold PAGE is among three results, and three results from one page can
    satisfy it. The guard is that the list holds three DIFFERENT pages.
    """

    def test_no_top_k_slot_is_spent_twice_on_the_same_page(self, real_wiki_pipeline):
        repeated = []
        for case in LABELLED_CASES:
            sources = [_norm_source(c.source) for c, _ in
                       real_wiki_pipeline.retrieve(case.question, top_k=3)]
            if len(set(sources)) < len(sources):
                repeated.append((case.question, sources))
        assert not repeated, (
            f"{len(repeated)} of {len(LABELLED_CASES)} questions spent two of "
            f"their three slots on one page: {repeated[:3]}"
        )

    def test_the_best_chunk_of_a_page_is_the_one_that_survives(self, real_wiki_pipeline):
        """A cut, not a filter: the FIRST chunk of a page in score order wins.

        The alternative -- keeping the highest-scoring chunk of each page but
        re-sorting, or keeping a later chunk that happens to sit higher -- would
        change rank 1, and rank 1 is what recall@1 measures.
        """
        chunk = real_wiki_pipeline.chunks[0]
        scores = [(chunk, 0.9), (real_wiki_pipeline.chunks[1], 0.8)]
        cut = RAGPipeline._one_chunk_per_page(scores, top_k=1)
        assert cut[0][0] is chunk, "the cut did not keep the first chunk in score order"

    def test_fewer_than_top_k_results_is_allowed(self):
        """Padding back to top_k would mean re-admitting a page or serving a
        below-threshold chunk, both worse than a short list."""
        def chunk(source, chunk_id):
            return Chunk(id=f"{source}-{chunk_id}", content="body", source=source,
                         section="Body", type="faq", tags=[], summary="", h1="")

        scores = [
            (chunk("a.md", 0), 0.9),
            (chunk("a.md", 1), 0.8),
            (chunk("b.md", 0), 0.7),
        ]
        cut = RAGPipeline._one_chunk_per_page(scores, top_k=5)
        assert [x.source for x, _ in cut] == ["a.md", "b.md"], (
            f"expected one chunk per page and no padding, got "
            f"{[x.source for x, _ in cut]}"
        )


class TestNoBodylessHeadingReachesTheContext:
    """A heading with no body under it cannot answer anything.

    ``## Alternativas consideradas`` in
    ``decisions/fraud-detector-3-layer-architecture.md`` is followed immediately
    by an H3, so the section split produced it as a section with a title and
    nothing else. With the English embedder it ranked low and nobody noticed.
    With the multilingual embedder and the identity prefix it became the TOP-1
    context for 3 of the 49 labelled questions, all fraud-detector questions:
    the model's first piece of evidence for "que es el detector de fraude" was
    the word "Alternativas".

    ``TestTheTopChunkCarriesAnAnswerNotJustATitle`` is the guard that caught
    this. It is left exactly as it was; what changed is that it no longer fires.
    """

    def test_the_corpus_produces_no_bodyless_heading_chunk(self, real_wiki_pipeline):
        bodyless = [
            (c.id, c.content[:60])
            for c in real_wiki_pipeline.chunks
            if is_bare_heading(c.content.strip())
        ]
        assert not bodyless, (
            f"{len(bodyless)} chunk(s) are a heading and nothing else: {bodyless}. "
            f"They cannot answer anything and they win rank 1."
        )

    def test_a_heading_followed_by_a_deeper_heading_is_dropped(self):
        """The structural cause, pinned so the corpus cannot drift back into it.

        The fixture carries YAML frontmatter on purpose, because that is what
        makes the defect possible. ``split_sections`` re-attaches a document's
        own H1 to the body it titles -- but only when the FIRST section is an
        H1. With frontmatter the first section is the frontmatter, so the
        re-attachment never fires and every heading on the page becomes its own
        section. That is why the real corpus has exactly one bodyless heading
        and why the fixture has to reproduce it rather than a plain heading.
        """
        content = (
            "---\ntype: decision\nconfidence: medium\n---\n\n"
            "# Arquitectura de 3 Capas\n\n"
            "## Alternativas consideradas\n\n"
            "### Solo reglas\n\n"
            "Se descarto por Cardinalidad.\n"
        )
        sections = split_sections(content)
        assert any(
            is_bare_heading(s.strip()) for s in sections
        ), "the fixture no longer produces a bodyless heading; it cannot test the filter"

        kept = [s for s in sections if not is_bare_heading(s.strip())]
        assert not any(is_bare_heading(s.strip()) for s in kept)
        assert any("Cardinalidad" in s for s in kept), (
            "the filter removed a section that had content: it must drop only "
            "sections with nothing under them"
        )


class TestTheRetrievalGuardActuallyRan:
    """A guard that could not measure anything must not read as a pass.

    THE DEFECT THIS EXISTS TO FIX
    -----------------------------
    The guard this file used to ship measured an invented stand-in corpus, and
    a test file, a CI workflow and a helper module all asserted that the real
    ``wiki/`` "is private, is not in this repository, and is not this test's
    concern". That was false: 50 files, in the index, in ``origin/main``,
    checked out by CI. So a retrieval guard had been reading the wrong corpus
    and calling it a pass -- and the wrong corpus was a structural clone of
    the right one, with gold pages 6x further from their nearest distractor,
    which is exactly the condition under which a floor stops discriminating.

    WHY A SEPARATE TEST AND NOT A LOUDER SKIP
    -----------------------------------------
    Because pytest renders a skip as one character and a count. There is no
    skip reason, banner or exit code that a ``-q`` run cannot collapse into
    something that reads as success. So the guard skips -- because a guard
    with no corpus has no verdict and must not invent one -- and THIS test,
    which always runs and re-derives the same precondition, FAILS. The run
    goes red. That is the intended outcome: a red run is information, and a
    green run that measured nothing is the disease.

    The two failure states are distinguished on purpose:

      * ``wiki/`` absent -- the corpus was removed. The 50 files are tracked
        today; the owner's decision to keep a personal dossier on a public
        remote is a known open issue they have deferred, so this can happen.
      * ``wiki/`` present but a labelled gold page missing -- 4 FAQ pages
        (``nivel-ingles``, ``disponibilidad``, ``hobbies-intereses``,
        ``por-que-contratarte``) are on disk and NOT in the index, covering 8
        of the 49 labels. The floor is 49-question-derived; scoring 41 and
        reporting it as the 49-question number is the exact mistake being
        repaired, so no number is reported at all.
    """

    def test_the_real_corpus_is_measurable(self):
        blocker = guard_blocker()
        assert blocker is None, (
            f"\n{GUARD_BANNER}\n"
            f"{blocker}\n\n"
            f"The retrieval guard in this file produced NO verdict for this run, "
            f"so recall@3 was not measured at all. That is a skip, and a skip "
            f"is not a pass: the suite must go red here rather than report a "
            f"green run that measured nothing.\n\n"
            f"To fix it, restore the corpus so its labelled gold pages load. If a "
            f"page is genuinely gone, re-measure on the population that remains "
            f"and add a Measurement for it in tests/real_wiki.py -- do not delete "
            f"the questions: they were written against the real pages, and "
            f"replacing them with questions written today would reintroduce "
            f"exactly the bias this guard was rebuilt to remove.\n"
        )

    def test_the_population_this_corpus_produces_is_a_calibrated_one(self, real_wiki_documents):
        """Names WHICH corpus this is, by content, rather than by label count.

        Both calibrated populations now resolve 49 of the 49 labels and load 37
        pages, so the count can no longer say which one this is -- and a third
        corpus is now possible WITHOUT changing any count at all, which is the
        dangerous version: editing one ``wiki/*.md`` used to move nothing a guard
        could see. The test is therefore the digest, and the assertion below it is
        that a resolved population serves every gold page.
        """
        population = len(resolved_cases(real_wiki_documents))
        measurement = measurement_for(real_wiki_documents)
        missing = unresolved_gold_pages(real_wiki_documents)

        assert measurement is not None, (
            f"the served corpus digests to {corpus_digest(real_wiki_documents)[:12]}, "
            f"which is neither calibrated population "
            f"({', '.join(f'{name} {digest[:12]}' for name, digest in sorted(CORPUS_DIGESTS.items()))}). "
            f"It resolves {population} of {len(LABELLED_CASES)} labelled questions "
            f"and leaves these gold pages unserved: {missing}. Either commit or "
            f"revert the wiki edit that moved the corpus -- and re-measure the row "
            f"you moved off -- or add a Measurement for this corpus. Scoring it "
            f"against a floor derived from another corpus is the mistake this "
            f"module exists to prevent."
        )
        assert not missing, (
            f"the {measurement.name!r} population is claimed but {missing} are "
            f"unresolved by the loader. That is a corpus problem, not a population "
            f"problem: every calibrated population serves all "
            f"{len(LABELLED_CASES)} gold pages, and the pages above are not among "
            f"the differences that define one."
        )

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

    THE INFERENCE THAT WAS WRONG
    ----------------------------
    A review claimed the threshold was "inert": the lowest top-1 cosine across
    the 49-question labelled set is 0.414, well above the 0.3 default, so the
    filter "never removes anything". The 0.414 reproduces (measured on the
    real corpus with the real embedder: 0.4142). The INFERENCE DOES NOT.

    ``retrieve()`` filters every candidate, THEN sorts descending and slices
    ``top_k``. So a score filter can only change what the caller receives when
    fewer than ``top_k`` candidates survive it, and "the best chunk per
    question clears 0.3" says nothing about how many clear it.

    EVERYTHING BELOW IS RE-MEASURED ON THE REAL CORPUS
    ---------------------------------------------------
    These assertions used to run against ``tests/fixtures/retrieval_corpus/``,
    an invented stand-in, on the stated ground that the real wiki was private
    and untracked. It is neither: 50 files, in the index, checked out by CI.

    Re-measured 2026-09-29 under ``paraphrase-multilingual-MiniLM-L12-v2`` with
    the identity-prefixed chunk text (124 chunks after the bodyless-heading
    filter), the 49 labelled questions and real ``expand_query``:

      * the 0.25 default drops 956 of 6125 (question, chunk) pairs -- 15.6% --
        of its own accord. This figure needed a method change; see the note
        inside ``test_the_filter_is_live_and_drops_real_pairs``;
      * the direct path at ``top_k=3`` is unchanged by it for all 49
        questions, and the thinnest still has exactly 3 survivors;
      * the cliff: 0.29 costs 1 question a result, 0.30 costs 2, 0.35 costs 9
        and empties one. 0.30 was the OLD default, calibrated against the
        English embedder, and it sat exactly on the cliff.

    The surface that invited the wrong conclusion is still gone: no per-call
    ``threshold`` override, because no production caller passes one and only
    one value has ever been measured.
    """

    def test_the_filter_is_live_and_drops_real_pairs(self, real_wiki_pipeline):
        """Guard the OTHER direction: the filter must not be vacuous.

        Without this, every "the default drops nothing" test below could be
        satisfied by a filter that never binds at all (a threshold of -1, a
        comparison against a constant). Pinning that the default really does
        discard pairs is what makes them non-vacuous.

        THE MEASUREMENT HAD TO BE REWRITTEN, AND THE OLD ONE WAS LYING
        -------------------------------------------------------------
        The previous version counted ``total - survivors`` at a deep ``top_k``
        and asserted 15% of it. That was correct when ``retrieve()`` returned
        raw score-ordered chunks. It stopped being correct the moment
        ``retrieve()`` started returning AT MOST ONE CHUNK PER PAGE: the deep
        call is then bounded by the number of PAGES (37) instead of the number
        of chunks (124), so 70.4% of the matrix is missing before the threshold
        has done anything at all. The assertion still passed -- 70.4% is
        comfortably over 15% -- while measuring page-dedup instead of the
        filter. Every "the default drops no real result" test in this class was
        vacuous behind it, which is precisely what its own docstring warns
        about.

        So the count is now the DIFFERENCE between the survivors at the shipped
        threshold and the survivors with the filter disabled, both taken from
        ``retrieve()`` at the same deep ``top_k``. Dedup cancels, and what is
        left is what the threshold alone discarded. Recomputing the cosine here
        instead would test the test: an earlier draft of this assertion
        re-implemented the dot product and therefore passed unchanged when
        ``retrieve()``'s comparison was mutated to ``score >= -1.0``. Verified
        by mutation -- do not "simplify" this back into local arithmetic.
        """
        rag = real_wiki_pipeline
        shipped = rag.threshold
        assert shipped == 0.25, (
            f"the shipped default is {shipped}, not 0.25. Re-swept on the real "
            f"corpus over the labelled set with the current embedder: 0.20-0.28 "
            f"cost 0 questions, 0.29 costs 1 and leaves one question with 2 "
            f"results, 0.30 costs 2, 0.35 costs 9 and empties one. See the "
            f"threshold block in RAGPipeline.__init__ for why 0.25 rather than "
            f"the highest passing value."
        )

        deeper = len(rag.chunks) * 2
        total = len(LABELLED_CASES) * len(rag.chunks)
        try:
            rag.threshold = shipped
            filtered = sum(len(rag.retrieve(c.question, top_k=deeper)) for c in LABELLED_CASES)
            rag.threshold = -1.0
            unfiltered = sum(len(rag.retrieve(c.question, top_k=deeper)) for c in LABELLED_CASES)
        finally:
            rag.threshold = shipped

        dropped = unfiltered - filtered

        assert total == len(LABELLED_CASES) * len(rag.chunks), (
            f"expected {len(LABELLED_CASES)} questions x {len(rag.chunks)} chunks "
            f"= {total} pairs, scored {filtered} survivors"
        )
        assert dropped > 0, (
            f"the {shipped} filter now discards NOTHING ({filtered} survivors "
            f"with it and {unfiltered} without, over {total} pairs). If it has "
            f"stopped binding, every 'the default drops no real result' test in "
            f"this class is vacuously true and this file is guarding nothing. "
            f"On the real corpus it discards 956/6125 (15.6%)."
        )

    def test_shipped_default_drops_no_result_the_unfiltered_run_keeps(
        self, real_wiki_pipeline
    ):
        """The direct path: the shipped 0.3 must cost the caller almost nothing.

        This is the non-vacuous version of the review's claim. It is stated
        behaviourally -- the same chunks, in the same order, with and without
        the filter -- rather than as a statistic about top-1, because top-1 is
        precisely the statistic that made the original claim look safe while
        the filter was still dropping 29% of the pool.

        MEASURED ON THE REAL CORPUS, re-measured 2026-09-29 under the
        multilingual embedder: the filtered and unfiltered top-3 are IDENTICAL
        for all 49 questions, and the thinnest question still has exactly 3
        survivors. So on this corpus at ``top_k=3`` the filter costs the caller
        nothing -- which is a fact about this corpus's score distribution, not a
        guarantee this pipeline makes, and the reason the assertion is a cap
        rather than an equality. A corpus with fewer chunks, or a question whose
        top candidates all sit near 0.25, would legitimately change; the cap is
        what lets that change fail loudly instead of being argued about.

        This is the assertion that caught the 0.30 default sitting on the cliff
        with the current model: 0.30 changed the top-3 for 2 of 49 questions
        and left one of them with only 2 results. The fix was to re-sweep the
        threshold, not to widen this cap.

        The bar is two-sided. RAISING the default must not starve the direct
        path, so the number of questions whose top-3 changes is capped at 0.
        LOWERING it must not be rewarded, so the minimum number of survivors
        per question is floored at 3. A threshold that stopped filtering, or
        one that filtered half the pool, fails one of the two.
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

        assert not changed, (
            f"the shipped threshold of {shipped} changed the top-3 for "
            f"{len(changed)} of {len(LABELLED_CASES)} questions: {changed}. On "
            f"the real corpus it changed 0. Either the default got more "
            f"aggressive or the corpus got harder; both need a deliberate "
            f"re-baseline rather than a larger cap."
        )
        assert min(survivors) >= 3, (
            f"the shipped threshold leaves {sum(1 for s in survivors if s < 3)} "
            f"question(s) with FEWER THAN THREE results on the direct path "
            f"(minimum {min(survivors)}); measured, it was exactly 3 for all 49."
        )

    def test_shipped_default_never_empties_the_production_context(self, real_wiki_pipeline):
        """The production path: no question in the labelled set may go ungrounded.

        ``get_context_string`` is what an interview turn actually calls, so an
        empty context here is the failure this file documents as worse than a
        loose one. It used to be measured with ``detect_doc_type`` also applied,
        which is a second variable; the pre-filter is gone, so this now tests
        the threshold alone.

        CORPUS: the real ``wiki/``, tracked and checked out by CI, so a failure
        here is always a code change or a corpus edit somebody made on purpose
        -- never "the owner's personal pages are not on this machine".
        """
        rag = real_wiki_pipeline
        empty = [c.question for c in LABELLED_CASES if not rag.get_context_string(c.question, top_k=3)]
        assert not empty, (
            f"the shipped threshold of {rag.threshold} leaves {len(empty)} of "
            f"{len(LABELLED_CASES)} labelled questions with NO context at all: "
            f"{empty}. An interview answer with no grounding from the profile "
            f"is worse than a loose one. Measured on the real corpus under the "
            f"current embedder, 0.20-0.28 empty nothing and 0.35 empties one -- "
            f"so if this ever fires, check the corpus first and the threshold "
            f"second."
        )

    def test_an_english_question_reaches_its_spanish_gold_page(self, real_wiki_pipeline):
        """A KNOWN LIMITATION THAT WAS FIXED, asserted so it stays fixed.

        This test used to assert the opposite. It read:

            ``all-MiniLM-L6-v2`` -- the model this pipeline ships -- is an
            English model. ... "what are your strengths and weaknesses" scores
            0.2747 against its own page, so the gold is not even in the top 3
            with the threshold DISABLED, and ``get_context_string`` returns the
            empty string. ... What would clear it: a multilingual embedder.
            That is a product decision, not a test edit, and it is the owner's
            call.

        The owner's call was made: the default embedder is now
        ``paraphrase-multilingual-MiniLM-L12-v2``, because ``wiki/`` is Spanish
        and the question reaches this pipeline verbatim from Whisper, in
        Spanish. The limitation is gone, and the test was inverted rather than
        deleted, because a limitation that is gone and unguarded is a limitation
        that comes back.

        The old failure message asked for exactly this ("re-measure and either
        drop the mark or record the new cause"). This is the new cause.

        MEASURED, threshold DISABLED: unfiltered top 3 is
        ``[('faq/fortalezas-y-debilidades.md', 0.5637),
        ('faq/por-que-esta-empresa.md', 0.4744),
        ('faq/area-preferida.md', 0.3991)]`` -- the gold is RANK 1, against
        0.2747 and unreachable before. 0.5637 clears the shipped threshold
        comfortably, so the filtered path agrees with the unfiltered one.

        Both halves are still asserted and both are now positive: the gold is
        reachable at all, and it is reachable through the threshold the app
        actually ships with. Asserting only the first would pass on a pipeline
        that could find the page and then refuse to show it.
        """
        rag = real_wiki_pipeline
        question = "what are your strengths and weaknesses"
        gold = "faq/fortalezas-y-debilidades.md"

        shipped = rag.threshold
        try:
            rag.threshold = -1.0
            unfiltered = rag.retrieve(question, top_k=3)
            rag.threshold = shipped
        finally:
            rag.threshold = shipped

        assert _norm_source(gold) in [_norm_source(c.source) for c, _ in unfiltered], (
            "an English question can no longer reach its Spanish gold page even "
            "with the threshold disabled. The multilingual embedder stopped "
            "bridging the two languages: either the default has been reverted or "
            "the corpus no longer answers it. Unfiltered top 3 was "
            f"{[(c.source, round(s, 4)) for c, s in unfiltered]}"
        )
        assert rag.get_context_string(question, top_k=3), (
            "an English question against a Spanish page retrieves the page but "
            f"returns no context under the shipped threshold of {rag.threshold}. "
            "The score is being filtered away after retrieval, which is a "
            "different defect from the one this test was written for."
        )

    def test_retrieve_takes_no_per_call_threshold_and_no_doc_type(self):
        """The two removed parameters are dead surface and must not come back.

        Neither is a preference. Both were ways for a reader to believe
        something configurable that no production caller has ever configured, and
        one of them was actively harmful.

        ``threshold``: no production caller passes it -- ``main.py`` constructs
        ``RAGPipeline`` with the instance default and every call site passes at
        most ``top_k``. A second way to set it is a second unmeasured value,
        which is how a filter this narrow came to be read as inert.

        ``doc_type``: the only caller that ever passed it was GUESSING.
        ``detect_doc_type`` matched the question against a keyword table and fed
        the guess into a hard filter over the candidate set. On the 49
        real-corpus labelled questions the guess fired 14 times and named a type
        the gold page does not carry in 8 of them, deleting the answer from the
        candidate set outright -- strict recall@3 0.5714 with the guess applied
        against 0.6531 without; two questions fixed, six broken. The keyword
        table is gone with it. See ``RAGPipeline.retrieve``'s docstring, which
        keeps the numbers.

        Read as a signature, this says: what retrieval does is decided in two
        places, the constructor and ``top_k``. Both are measured.

        This is the assertion that failed before the change; it is here so the
        removals are a contract rather than an edit someone can revert.
        """
        import inspect

        params = [
            name
            for name in inspect.signature(RAGPipeline.retrieve).parameters
            if name != "self"
        ]
        assert params == ["query", "top_k"], (
            f"retrieve() takes {params}. Either the per-call `threshold` override "
            f"is back -- no production caller passes it, only one value has ever "
            f"been measured -- or `doc_type` is, which is the filter that "
            f"deleted the labelled answer for 8 of the 49 real-corpus questions. "
            f"Neither is a per-call decision; both belong in the constructor, "
            f"where the measurements are."
        )


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
        model = rag._embedder

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
