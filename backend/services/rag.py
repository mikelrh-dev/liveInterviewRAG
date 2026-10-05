"""RAG pipeline — document chunking, embedding, and retrieval."""

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

from backend.services.rerank import RERANK_VERSION, Reranker

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# Topic keywords -> synonyms appended to the query before embedding.
# Improves recall when the user's wording differs from the document's.
QUERY_EXPANSIONS: Dict[str, List[str]] = {
    "tests": ["testing", "pytest", "tdd", "skills"],
    "testing": ["tests", "pytest", "tdd", "skills"],
    "test": ["testing", "pytest", "tdd", "skills"],
    "projects": ["project", "entrevista", "prácticas"],
    "proyectos": ["project", "entrevista", "prácticas"],
    "proyecto": ["project", "entrevista", "prácticas"],
}

# ── Placeholder filtering (read-time, never destructive) ─────────────────────
#
# The wiki marks unwritten content with `[TODO: ...]` markers and rates each page
# with `confidence:`. Before this, `_chunk_document` stripped only the
# frontmatter: it filtered nothing and read `confidence` nowhere, so the LLM was
# served 11 chunks containing a literal `[TODO` from the real corpus — inside
# "Outcomes" and "What I'd do differently", exactly what a recruiter asks
# about. The model could read a TODO aloud, or invent the missing figure, with
# nothing in the wiki to contradict it.
#
# Two complementary parts, because neither is sufficient alone:
#   1. This module strips the markers (below) and drops `confidence: low` pages.
#   2. ``_chunk_document`` WARNS once per affected document, so the owner learns
#      the wiki still has holes instead of the holes quietly disappearing.
#
# NOTHING under wiki/ is ever modified. This changes how the RAG *reads* the
# candidate's data; the transformation is pure and fully reversible by filling
# the marker in the file. Semantics follow the wiki's own conventions
# (wiki/CONVENCIONES.md "Confidence Lifecycle"):
#   * ``low``    -> draft/placeholder awaiting owner confirmation -> SERVE NOTHING
#   * ``medium`` -> "reviewed but not tested", real content     -> SERVED AS IS
#   * absent     -> unstated, not low                           -> SERVED AS IS
# Discarding `medium` would be the dangerous move — it is the level most of the
# wiki sits at, and it holds real, reviewed answers.

# Bumped whenever chunking semantics change. Stored in the embedding cache
# metadata: the cache's document_hash covers the RAW wiki text, which this
# filtering deliberately does not touch, so without this guard a pre-filter
# cache would be restored and silently undo every fix below.
#
# "3": a document's own H1 now rides with the body it titles, and sections that
# are nothing but [[wikilinks]] are no longer emitted. Both change the chunks
# produced from UNCHANGED document text, so a cache written by version "2"
# still contains all 34 bare H1 titles and all 42 wikilink sections and must be
# rejected rather than served. Proven by
# ``TestChunkFilterVersionGuardsTheStaleCache``.
#
# "4": the text embedded per chunk is now the page identity (H1, summary,
# section) followed by the body, where it was the bare body. This is the one
# version bump that changes NO chunk and NO field: the chunk set, the contents
# and the corpus hash are all byte-identical to version "3", and the vectors
# are still simply wrong for the retriever now shipping — they are cosine
# distances measured against a sentence that never mentioned which page it was
# from. A cache whose metadata says version "3" is therefore a cache of
# pre-identity vectors wearing a post-identity label, and it has to be
# rejected. Without this bump the model change below would be enough to
# invalidate the cache on a developer machine and nothing at all on a machine
# that had already run the multilingual model once, which is the worst of both.
#
# "5": a heading section with no body under it is no longer emitted (see
# ``is_bare_heading``). Unlike "4" this one DOES change the chunk set — the real
# corpus loses ``## Alternativas consideradas`` from
# ``decisions/fraud-detector-3-layer-architecture.md`` — from UNCHANGED document
# text, which is exactly the case the version exists to catch: the document hash
# is computed over the raw wiki and cannot see it. Without this bump a version
# "4" cache keeps serving a bodyless heading as the top-1 context for 3 of the
# 49 labelled questions.
#
# "6": the text embedded per chunk gained a fourth identity part, the section's
# INTENT (see ``section_intent``). Like "4" this changes NO chunk and NO document
# text: the corpus still loads 37 pages and chunks to 124, and the document hash
# is byte-identical. But it changes the embedded text of the 10 of 124 chunks that
# are a career section, so a version "5" cache would be restored and those
# chunks' VECTORS would be cosine distances against a sentence that never
# mentioned what the section is FOR -- while ``_lexical_index`` and the reranker,
# both of which read ``embedding_text``, would read the new sentence. A cache is
# not allowed to be half-updated: it would serve a dense order computed from one
# text and a lexical/cross-encoder order computed from another.
CHUNK_FILTER_VERSION = "6"

#: Which lexical-rescue behaviour produced a given set of results. NOT part of
#: the embedding cache's identity, and that is a decision rather than an
#: omission, so it is recorded here rather than left to be inferred.
#:
#: The cache stores VECTORS, and the rescue stores nothing: it is fitted at
#: query time from ``embedding_text(chunk)`` over whatever chunks the run
#: already holds. Those five fields -- ``content``, ``h1``, ``summary``,
#: ``section`` and ``intent`` -- are exactly what ``_save_embeddings_cache``
#: persists, so a run
#: restored from cache and a run that recomputed hold byte-identical text and
#: therefore produce byte-identical BM25 scores. Bumping ``CHUNK_FILTER_VERSION``
#: for this would invalidate a cache whose vectors are perfectly correct and cost
#: a full re-embed (~100s on this corpus) to rebuild numbers that did not move.
#: The invariant that actually matters is pinned by
#: ``tests/test_lexical_rescue.py::TestTheRescueDoesNotDependOnTheCache``.
#:
#: So: bump THIS when the analyzer, the k1/b pair or the gate changes, and leave
#: ``CHUNK_FILTER_VERSION`` alone. It is the version of a retrieval DECISION,
#: not of a chunking semantic.
LEXICAL_RESCUE_VERSION = "1"

# ``[TODO ...]`` and friends. Tolerates the real spellings seen in wiki/:
# ``[TODO: ask Mikel]``, ``[TODO]``, ``[TODO — fill in]``.
_PLACEHOLDER_RE = re.compile(r"\[\s*TODO\b[^\]]*\]", re.IGNORECASE)

# Markdown emphasis and list/separator punctuation that can be orphaned once a
# marker is removed, e.g. ``- **[TODO: x]:** text`` -> ``- :** text``. Underscores
# and hashes are excluded from the class used on body text: they appear inside
# real identifiers (``snake_case_name``) far more often than as noise, and
# rewriting them would corrupt the indexed content.
_MD_NOISE_RE = re.compile(r"[*`]+")
# Punctuation a removed marker can orphan at either end of what is left.
_SEPARATORS = " \t\u2014\u2013-:,;.!?\u00bb\u00ab"
# ...but a terminal MARK is the author's, not the marker's. `?`, `!` and `.`
# are in the class above because a marker sitting between a bullet and its text
# leaves ``- [TODO] :** text`` behind; applied to the trailing end as well,
# they took the sentence's own punctuation off the prose that survived.
#
# What the marker usually trails is a note-to-self ("[TODO] -- Any metrics?"),
# and losing the mark makes it worse rather than better: a note-to-self that
# reads as an unfinished fragment is LESS obviously a note-to-self. A recruiter
# asking about metrics got a section that read as a question the candidate
# asked -- 10 of the real corpus's 16 such lines, and one chunk of 125 that
# was nothing but unanswered questions.
#
# So the two ends differ. The leading end still eats the full class, because
# that is the debris the marker leaves in front of the surviving prose. The
# trailing end keeps only whitespace and dashes: a dash stranded at the end of
# a line is debris (``40% -- [TODO]``), while a question mark is punctuation
# the owner wrote.
_TRAILING_SEPARATORS = " \t\u2014\u2013"
_HAS_WORD_RE = re.compile(r"\w", re.UNICODE)
# A heading line: leading #'s define the section boundary the chunker splits on,
# so a placeholder inside a heading must not cost them.
_HEADING_RE = re.compile(r"^#{1,6}\s")

# Section boundaries: every heading of level 1-3 starts a new section.
_SECTION_SPLIT_RE = re.compile(r"\n(?=#{1,3}\s)")
# A section that is nothing but the document's own H1 title.
_H1_ONLY_RE = re.compile(r"^#\s+\S")
# The title text of an H1 line. Level-1 only, so an H2 or H3 cannot be mistaken
# for the page's name.
_H1_TITLE_RE = re.compile(r"^#\s+(.+)$")


def document_h1(body: str) -> str:
    """The document's own H1 title, or ``""`` when the body does not open with one.

    "Own" means the same thing here that it means in ``split_sections``: the
    first thing the body says. A *later* H1 is a genuine top-level section
    boundary, not the page's identity, and reading one as the title would stamp
    every chunk on the page with a heading from the middle of it.

    The distinction is not cosmetic. ``split_sections`` re-attaches the leading
    H1 to the section that follows it, so the H1 is inside the content of
    exactly ONE of a page's chunks — for a two-chunk page, one chunk carries
    the title and the other has never seen it. Every chunk therefore records
    the title for itself, and it is that field, not ``content``, that reaches
    the vector (see ``embedding_text``).
    """
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        match = _H1_TITLE_RE.match(stripped)
        return match.group(1).strip() if match else ""
    return ""


def split_sections(content: str) -> List[str]:
    """Split a document body into sections, keeping its own H1 attached.

    Splitting before EVERY H1-H3 turned the document's own H1 into a standalone
    chunk with no body: 34 of the real corpus's 214 chunks were a bare title,
    and the first real section lost the title that gave it meaning. That is not
    merely wasted volume — an H1 like ``# Frutero — BM Supermercados
    (2015-2016)`` or ``# Arquitectura de 3 Capas para Fraud Detector`` *is* the
    answer to what a recruiter asks, and splitting it off discards role,
    employer, dates and architecture name away from the paragraph explaining
    them.

    So the leading H1 is re-attached to the section that follows it. Only the
    document's OWN title: a *later* H1 is a genuine top-level boundary and keeps
    splitting, otherwise two distinct top-level sections would merge and
    ``section`` would be mislabelled for the merged body.

    H4-H6 are deliberately not boundaries here, unchanged from before: the
    heading parser below only understands levels 1-3, so a deeper heading would
    fall back to naming the whole file as its section.
    """
    parts = [p for p in _SECTION_SPLIT_RE.split(content) if p.strip()]
    if len(parts) < 2 or not _H1_ONLY_RE.match(parts[0].strip()):
        return parts
    return [f"{parts[0].rstrip()}\n\n{parts[1].lstrip()}", *parts[2:]]


# ── Section intent: what KIND of question a section is able to answer ───────
#
# WHY THIS EXISTS, MEASURED
# -------------------------
# Five pages in this corpus talk about Mercadona and only ONE of them answers
# "¿cuándo empezaste?". The gold was not missing and not below threshold -- it
# ranked 4th, one slot outside a top-3 that spends one slot per page
# (``_one_chunk_per_page``). And the reason it was 4th is the thing this
# function fixes: on ``experience/gerente-mercadona-2019-2025.md`` the chunk
# that LITERALLY STATES THE PERIOD -- "**Period:** 2019 to November 2025", inside
# ``## Context`` -- was the 11th-ranked chunk of the whole corpus for that
# question, while the page's ``## Measurable achievements`` chunk ranked 6th.
# ``np.dot`` was working; the ranking was working; the answer-bearing chunk was
# simply not the chunk that won.
#
# Why it lost is a missing signal, and it is missing from the OTHER three chunks
# too. ``embedding_text`` already gives every chunk of a page the same identity --
# H1, ``summary_1line``, section -- so all four chunks of the Mercadona page say
# "Gerente B (Encargado), Mercadona, 2019-Nov 2025" equally loudly and none of
# them says WHICH QUESTION it answers. The dates live in ``## Context``; the
# accomplishments live in ``## Measurable achievements``; the embedder is given
# no reason to prefer one, and it prefers the one whose prose is closest to a
# generic question about work.
#
# So the identity is not the missing half. The INTENT is.
#
# WHY A TAXONOMY AND NOT A MODEL
# ------------------------------
# The label is a pure function of the heading text and the body, read out of the
# page's own Markdown. No LLM is called, nothing is inferred, and the same input
# always yields the same label -- which is what makes the whole thing auditable
# and what lets the retrieval numbers be re-derived rather than re-argued.
#
# Every key below is a heading that appears in ``wiki/templates/*.md``: the wiki
# defines its own section vocabulary, so the vocabulary is read off the contract
# rather than fitted to a question set. ``experience-template.md`` supplies
# ``Context`` / ``Responsibilities`` / ``What this role taught you`` and
# ``profile-template.md`` supplies the career timeline, and those are exactly the
# sections a "¿en qué empresas has trabajado?" / "¿qué puesto tenías?" / "¿cuándo
# empezaste?" question is answered by.
#
# THE NARROWING IS MEASURED, AND IT COST SOMETHING
# -------------------------------------------------
# A first version of this table also labelled ``## Measurable achievements``,
# ``## Outcomes``, ``## Task``/``## Action``/``## Result``, ``## Stack`` and the
# FAQ pages' own question headings, and applied to every page type. That version
# labelled 56 of 124 chunks and cost the 49 labelled questions ONE of their 44:
# ``"puedes empezar a trabajar ya estas disponible"`` stopped returning
# ``faq/disponibilidad.md``. The work-history set liked it (2/6 strict) but a
# recall floor over 49 questions is the harder constraint and it lost.
#
# Two reasons it lost, and both are about the label being REDUNDANT rather than
# wrong. On a FAQ page the H1 already IS the interview question -- that is what
# ``tests/real_wiki.py`` says the FAQ group is -- so "this section answers
# '¿cuál es tu disponibilidad?'" adds nothing the chunk does not already carry,
# and it spends tokens in a prefix the cross-encoder also reads. On a ``project``
# or ``story`` page the section heading is already self-describing (``Why``,
# ``Stack``, ``Outcomes``, ``Task``, ``Action``, ``Result``), and there is no
# biography question competing for the slot.
#
# The competition this exists to fix is specific: several SECTIONS OF ONE PAGE
# all claiming the same top-3 slot for a question only one of them can answer.
# ``experience/*.md`` and ``profile/mikel.md`` have that shape and nothing else in
# the corpus does. So the table below is scoped to the career vocabulary and
# ``_CAREER_TYPES`` scopes it to the pages that use it: 10 of 124 chunks change,
# and the other 114 stay byte-identical.
#
# WHAT THE NARROWING BOUGHT, ON THE SAME CORPUS AND THE SAME 49 QUESTIONS
# -----------------------------------------------------------------------
#                                 work-history strict / relevant   recall@3
#   no intent (before)                     1/6 · 2/6              44/49
#   broad table, every type                2/6 · 3/6              43/49   REGRESSION
#   this table, career types only          3/6 · 4/6              44/49
#
# Two more of the six recovered, one of them the question this was written for:
# "¿qué puesto tenías en mercadona" went from not-in-the-top-3 to rank 1. And the
# accent pair stopped diverging -- "donde has trabajado" and "dónde has trabajado"
# now return the same rank (2), where before they returned 1 and nothing.

#: ``(heading token, intent)``, matched as a SUBSTRING of the accent-stripped,
#: lowercased heading. Substring rather than equality because the same section is
#: spelled several ways across the corpus ("Career timeline (corrected)",
#: "Contexto", "Context") and an exact-match table would have to carry every
#: variant. Order matters only where one token contains another; the pairs that do
#: are written so the longer, more specific one is tested first.
_SECTION_INTENT_RULES: Tuple[Tuple[str, str], ...] = (
    ("responsibilit", "que hacia en el puesto: tareas y responsabilidades concretas"),
    ("timeline", "cronologia laboral: empresas y fechas de cada puesto"),
    ("career", "cronologia laboral: empresas y fechas de cada puesto"),
    ("historial", "cronologia laboral: empresas y fechas de cada puesto"),
    ("context", "empresa, sector, equipo y fechas de inicio y fin del puesto"),
    ("taught", "que aprendio, habilidades y lecciones del puesto"),
)

#: The frontmatter ``type`` values the career vocabulary above applies to. Not a
#: whitelist of pages (a page's own path would be a second, redundant signal --
#: ``CONVENCIONES.md`` already requires type to match the folder): it is the set
#: of types whose template describes a PIECE OF A CAREER, which is the only thing
#: these headings are about.
_CAREER_TYPES = frozenset({"experience", "profile"})

#: Appended to an intent when the section's own body carries a period. This is the
#: deterministic date signal the biography questions need and it is read from the
#: page, not inferred: a year, or a month name in either language, anywhere in the
#: section body. It is a SECOND signal, not the first -- ``## Context`` says
#: "**Period:** 2019 to November 2025" and this is what tells the vector that the
#: dates in it are the answer rather than scenery.
_PERIOD_IN_BODY_RE = re.compile(
    r"\b(19|20)\d{2}\b"
    r"|\b(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|"
    r"noviembre|diciembre|january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\b",
    re.IGNORECASE,
)
_PERIOD_INTENT = "incluye el periodo y los anos de inicio y fin"

#: Every heading in a section, deepest last, as ``(level, title)``. Level 1 is
#: separated from the rest because it means something different (see
#: ``section_intent``).
_SECTION_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _fold_heading(text: str) -> str:
    """Lowercase, accent-stripped, punctuation-to-space form of a heading.

    Accent stripping is not cosmetic here. The corpus is bilingual and the
    questions arrive from a microphone that drops tildes ("donde has trabajado"),
    so a token table keyed on accented text would simply not match
    "## Contexto" against the same heading written "## Context". Keys are
    written unaccented and this is what makes them match both.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return f" {re.sub(r'[^a-z0-9]+', ' ', plain.lower()).strip()} "


def section_intent(section: str, doc_type: str = "") -> str:
    """What kind of question this section can answer, or ``""`` if unclear.

    Reads the section's own HEADING and, for the period signal, its own body.

    ``doc_type`` is the page's frontmatter ``type``, and it gates the whole
    function: only ``_CAREER_TYPES`` gets an intent. The reasoning, and the
    measurement that forced it, is at the top of this section -- the short
    version is that a label has to add information the identity prefix does not
    already carry, and on a FAQ page the H1 already is the question.

    The heading it uses is the first H2-or-deeper one, never the H1, and that
    distinction is the whole reason this function is not a one-liner over
    ``chunk.section``. ``split_sections`` re-attaches the page's own H1 to the
    first real section, so ``_chunk_document`` records ``section`` for that
    chunk as the H1 -- "Gerente B (Encargado) - Mercadona (2019-Nov 2025)" --
    and the H2 that actually names what the section is about ("Context") is
    nowhere in that field. It is in the text, which is why this takes the
    section body rather than a metadata field: the H1 is the page's identity and
    already reaches the vector through ``chunk.h1``, so reading it here would
    return the page's name for every one of its sections and say nothing at all.

    ``""`` is the common answer -- 114 of the real corpus's 124 chunks -- and it
    means either "no such heading" or "not a career section". An unmatched chunk
    is left byte-identical rather than given a generic label: a label that
    describes every section describes none, and it would spend embedder capacity
    on all 124 chunks to move none of them.
    """
    if doc_type not in _CAREER_TYPES:
        return ""

    heading = ""
    for level, title in _SECTION_HEADING_RE.findall(section):
        if len(level) > 1:
            heading = title
            break
    if not heading:
        return ""

    key = _fold_heading(heading)
    intent = ""
    for token, label in _SECTION_INTENT_RULES:
        if token in key:
            intent = label
            break
    if not intent:
        return ""
    if _PERIOD_IN_BODY_RE.search(section):
        intent = f"{intent}, {_PERIOD_INTENT}"
    # Capitalised because ``embedding_text`` joins the identity parts with ". ",
    # and every other part (H1, summary_1line, section heading) arrives
    # capitalised. A label that breaks the sentence reads as a fragment of a
    # different one, and the embedder is being handed prose.
    return intent[:1].upper() + intent[1:]


# Headings the wiki uses for a section that is nothing but links to other
# pages. Every entry is justified by an occurrence in this repository, and the
# set is pinned by ``test_reference_heading_set_is_exactly_what_the_corpus_
# uses`` so a new spelling cannot slip through unnoticed:
#
#   "fuentes"      15 in live content  — Spanish "sources" (decisions/, faq/, ...)
#   "ver tambien"  11 in live content  — unaccented Spanish (decisions/, faq/, ...)
#   "ver también"   4 in live content  — accented Spanish (faq/)
#   "see also"     12 in live content  — English, used by experience/, profile/,
#                                     projects/, skills/, stories/
#   "sources"       1, in wiki/templates/faq-template.md — the English spelling
#                     the owner's own FAQ template produces, so it appears in
#                     generated content even though no live page uses it yet.
#
# The corpus is bilingual in its scaffolding, so the table is bilingual too.
REFERENCE_HEADINGS: frozenset = frozenset({
    "fuentes", "see also", "sources", "ver tambien", "ver también",
})

# A body line that carries no prose: a list item made only of wikilinks.
_WIKILINK_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\[\[[^\]]*\]\](?:[,;]\s*)?)+\s*$")
_HEADING_LINE_RE = re.compile(r"^#{1,3}\s+(.+)")


def is_wikilink_reference(section: str) -> bool:
    """True when a section states no answer at all — only ``[[wikilinks]]``.

    As chunk text these sections are actively harmful. The literal string
    ``- [[profile/mikel]]`` carries no meaning for a sentence embedder, and
    "Fuentes" / "Ver tambien" is generic Spanish that matches no question a
    recruiter would ask. 42 of the real corpus's 214 chunks were exactly this.

    Dropping them is lossless, and the loss is measured rather than asserted
    (``test_dropping_reference_links_loses_no_answer_content``): every link in
    the corpus either names no document at all — an unfilled ``[[faq/...]]``
    template placeholder, which cannot state a relationship because it names
    nothing — or names a document that is indexed on its own and therefore
    answers that topic with its real content. A bare pointer is always a worse
    representation of a document's content than the content itself.

    The decisive test is the CONTENT, not the heading, so a source that
    explains WHY it points somewhere keeps its prose. ``REFERENCE_HEADINGS`` is
    needed for the degenerate case of a reference heading with an empty body,
    and it documents intent.
    """
    lines = [l for l in section.split("\n") if l.strip()]
    if not lines:
        return False
    heading_match = _HEADING_LINE_RE.match(lines[0])
    body = lines[1:] if heading_match else lines
    if not body:
        return bool(heading_match) and heading_match.group(1).strip().lower() in REFERENCE_HEADINGS
    return all(_WIKILINK_LINE_RE.match(line) for line in body)


def is_bare_heading(section: str) -> bool:
    """True when a section is a heading and nothing else -- a title with no body.

    This is the other way a section can state no answer at all, and it is not
    caught by ``is_wikilink_reference``, which only recognises a heading whose
    body is links. A heading with no body at all cannot be an answer either.

    It is produced by the split, not by the author: ``split_sections`` cuts on
    every H1-H3, so a heading immediately followed by a deeper heading becomes
    its own empty section. The real corpus has exactly one --
    ``## Alternativas consideradas`` in
    ``decisions/fraud-detector-3-layer-architecture.md``, whose H3 children
    follow on the next line. The author wrote no such empty heading.

    Dropping it is lossless for a structural reason rather than a judgemental
    one: the section holds no characters of content to lose, and the H3
    subsections that were under it are separate sections either way.

    WHY IT NEEDED A FIX RATHER THAN A NOTE
    --------------------------------------
    It was harmless while an English embedder ranked it low, and stopped being
    harmless the moment one did not: with the multilingual model and the
    identity prefix, ``## Alternativas consideradas`` became the TOP-1 context
    for 3 of the 49 labelled questions, all of them fraud-detector questions.
    The LLM's first piece of evidence for "que es el detector de fraude" was the
    word "Alternativas". That is the exact failure
    ``TestTheTopChunkCarriesAnAnswerNotJustATitle`` exists to catch, and the
    model change is what made it reachable -- so the chunker is fixed and the
    test is left alone.
    """
    lines = [l for l in section.split("\n") if l.strip()]
    return len(lines) == 1 and bool(_HEADING_LINE_RE.match(lines[0]))


def strip_placeholders(text: str) -> Tuple[str, int]:
    """Remove ``[TODO ...]`` placeholders from ``text``.

    Only the marker itself is removed. Text before *and* after it on the same
    line survives: a line such as ``- [TODO: metricas] Reduje la latencia un
    40%`` carries a real answer, and dropping the tail would silently delete
    content the owner already wrote. The owner's job is to remove the marker;
    what they wrote around it is data.

    What the marker usually trails is a note-to-self ("[TODO: ask Mikel] — Any
    metrics?"), and that trailing prose is indistinguishable from an answer
    without a semantic judgement this function must not make. The chunker keeps
    it, the RAG answers from it, and resolving the marker is the owner's task.
    Stripping it is a content decision, not a sanitisation one.

    What the marker leaves behind is a different matter and IS cleaned here:
    the orphaned bullet and colon in front of the surviving prose. That cleanup
    is deliberately asymmetric (see ``_TRAILING_SEPARATORS``) so it never takes
    the terminal mark off the owner's own sentence on the way out.

    Returns the cleaned text and the number of markers removed. Headings keep
    their ``#`` markers, so a heading that happens to carry a placeholder still
    splits as a section boundary.
    """
    if not _PLACEHOLDER_RE.search(text):
        return text, 0

    kept_lines = []
    removed = 0
    for line in text.split("\n"):
        markers = _PLACEHOLDER_RE.findall(line)
        if not markers:
            kept_lines.append(line)
            continue
        removed += len(markers)

        # A heading keeps its leading #'s: losing them would merge the section
        # into its predecessor and silently change chunk boundaries.
        is_heading = bool(_HEADING_RE.match(line))
        prefix = _PLACEHOLDER_RE.split(line)[0]
        suffix = _PLACEHOLDER_RE.split(line)[-1]

        if is_heading:
            cleaned = prefix + suffix
        else:
            # Outside a heading, only orphaned emphasis/separators are noise;
            # stripping '_' or '#' here would rewrite real words.
            head = _MD_NOISE_RE.sub("", prefix)
            tail = _MD_NOISE_RE.sub("", suffix)
            # Collapse the gap the marker leaves, without eating the word
            # boundaries: "40%  con" must not become "40% con" only by luck.
            cleaned = f"{head} {tail}".split()
            cleaned = (
                " ".join(cleaned)
                .lstrip(_SEPARATORS)
                .rstrip(_TRAILING_SEPARATORS)
            )

        if _HAS_WORD_RE.search(cleaned):
            kept_lines.append(cleaned)

    return "\n".join(kept_lines), removed


def expand_query(query: str) -> str:
    """Expand a query with synonyms for known topics to improve embedding recall.

    Matches topic keywords case-insensitively on word boundaries and appends
    the mapped synonyms (deduplicated, order-preserving) to the query. Queries
    without known keywords are returned unchanged.
    """
    query_lower = query.lower()
    additions: List[str] = []
    for keyword, synonyms in QUERY_EXPANSIONS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", query_lower):
            additions.extend(synonyms)
    if not additions:
        return query
    return f"{query} {' '.join(dict.fromkeys(additions))}"


def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """Extract YAML frontmatter metadata from a Markdown document.

    Args:
        content: Raw document content, optionally starting with a YAML
            frontmatter block delimited by ``---`` lines.

    Returns:
        Tuple of (metadata_dict, body). ``metadata_dict`` contains the parsed
        frontmatter fields (``type``, ``title``, ``tags``, ``summary_1line``,
        ...) or ``{}`` when no frontmatter is present. ``body`` is the document
        content with the frontmatter block removed.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("Invalid YAML frontmatter, ignoring metadata: %s", e)
        return {}, content

    if not isinstance(metadata, dict):
        logger.warning("Frontmatter is not a mapping, ignoring metadata")
        return {}, content

    body = content[match.end():].lstrip("\n")
    return metadata, body


@dataclass
class Chunk:
    """A chunk of text with metadata for RAG retrieval."""
    id: str
    content: str
    source: str
    section: str = ""
    type: str = ""
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    #: The page's own H1 (``document_h1``). Carried on every chunk of the page
    #: because ``content`` only contains it on the first one.
    h1: str = ""
    #: What KIND of question this section can answer (``section_intent``), or
    #: ``""``. The fourth identity part, and the only one that differs between
    #: chunks of the SAME page -- which is the point: without it, every chunk of
    #: a page carries the same identity and nothing tells the embedder which one
    #: to prefer for a given question.
    intent: str = ""
    embedding: Optional[np.ndarray] = field(default=None, repr=False)


def embedding_text(chunk: Chunk) -> str:
    """The string actually embedded for ``chunk`` — the identity prefix, then the body.

    WHY THE PREFIX EXISTS
    ---------------------
    The body alone does not say which page it is from. ``summary`` is filled in
    (``_chunk_document``), printed in the context the LLM reads
    (``_format_context_string``) and written to the embedding cache — and never
    reached the vector, so half of the retrieval signal never left the page.

    The token that separates one FAQ from its twelve siblings lives in the H1,
    and the H1 is inside the content of exactly one of a page's chunks
    (``document_h1``). It attacks the dominant failure class of this corpus:
    of the 17 misses the original configuration produced, 11 were ranking
    misses with the gold page at rank 4-15, which is a question of not knowing
    what page this is.

    The order ``h1, summary, section`` is measured, not assumed: the
    alternatives were run over the same 49 labelled questions and the same
    corpus, and the comparison is re-measured by
    ``tests/test_rag.py::TestTheEmbeddedTextCarriesThePageIdentity`` rather
    than restated, because a prefix order is a retrieval decision and not a
    formatting preference.

    Duplicate parts are dropped rather than repeated: for the first chunk of a
    page ``section`` IS the H1 (``split_sections`` merges the two), and a
    title repeated in the same sentence is noise the embedder has to spend
    capacity on. Empty parts are dropped for the same reason, and a chunk with
    no identity at all embeds its bare content exactly as before.

    ``intent`` is LAST, after the three identity parts, for two reasons that
    were both measured rather than arranged for looks. First, order: the identity
    leads because it is what separates sibling pages, and the intent describes
    this chunk rather than the document, so it cannot be doing that job.
    Second, position: appending leaves the measured prefix order ``h1, summary,
    section`` readable as a prefix of the string, which is what
    ``tests/test_rag.py::TestTheEmbeddedTextCarriesThePageIdentity`` and
    ``tests/test_rerank.py::TestThePassageIsTheEmbeddedText`` assert. Putting it
    first would have made both of them describe a different design.
    """
    parts: List[str] = []
    seen: set = set()
    for candidate in (chunk.h1, chunk.summary, chunk.section, chunk.intent):
        value = candidate.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        parts.append(value)
    if not parts:
        return chunk.content
    return f"{'. '.join(parts)}. {chunk.content}"


# ── The lexical ranker (BM25), used only to rescue a full top-k ──────────────
#
# WHY IT EXISTS, AS A RANKING FAILURE AND NOT A COVERAGE FAILURE
# ------------------------------------------------------------
# The dense side does not run out of candidates on this corpus; it fills all
# three production slots on 49 of 49 labelled questions and the gold page still
# misses the top 3. Measured at top_k=3 before this existed: 40 of 49, with the
# gold page present and ranked 4-15. A rescue that only fires when the dense
# side returns FEWER than top_k results therefore never fires at all here --
# measured, 0 of 49 -- and a rescue that appends to a top_k=49 list cannot move
# a rank either, because the gold page is already in that list. Both were run
# against the real corpus before any of this was written.
#
# So the compensation is for the CUT, not for the filter: the last slot is offered
# to a lexical ranker over the same text the embedder saw.


#: Words, so digits and punctuation never become terms.
_BM25_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

#: Accented letters folded to their base form, and ONLY those.
#:
#: An explicit table rather than ``unicodedata.normalize("NFD", ...)`` plus a
#: combining-mark filter, which is the usual spelling of this and is WRONG in
#: Spanish: ``ñ`` decomposes into ``n`` plus a combining tilde, so the filter
#: rewrites ``peña`` as ``pena`` -- and those are two different words in
#: Spanish, one a cliff and one a penalty. ``ñ`` is a letter of its own here, not
#: an ``n`` carrying an accent, so it is absent from this table on purpose and
#: survives tokenisation intact.
#:
#: ``ü`` is here because it occurs (``tecnología``, ``inglés``) and folds to
#: ``u``; the diaeresis is likewise not a separate letter.
_BM25_ACCENT_FOLD = str.maketrans({
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u",
    "Á": "a", "É": "e", "Í": "i", "Ó": "o", "Ú": "u", "Ü": "u",
})

#: There is deliberately NO stopword list, and that is a measurement rather than
#: an omission.
#:
#: An 80-word hand-written Spanish list was here and was removed after being
#: measured: strict recall@3 at top_k=3 over the 49 labelled questions, BM25
#: ranking alone, 0.8367 with the list and 0.8367 without it. Not one question
#: either way. The reason is structural rather than lucky -- BM25's idf already
#: pushes a term down by how many chunks contain it, and a function word is in
#: nearly all of them, so the list re-applied a discount the ranking had already
#: taken. Keeping it would have meant 30 lines of Spanish vocabulary that a
#: future reader has to maintain and cannot tell is doing anything, which is the
#: same defect ``retrieve()``'s docstring records about the removed
#: ``detect_doc_type`` keyword table: "keeping a table whose every key named a
#: type nothing could filter on would be keeping a lookup nobody calls."
#:
#: Single characters ARE still dropped, and that is free: they are length
#: noise in a corpus of prose and there is no IDF argument for them.


def bm25_tokens(text: str) -> List[str]:
    """The terms ``Bm25Index`` counts, and what ``expand_query`` must NOT touch.

    Lowercased, accents folded, single characters dropped, function words KEPT
    (see ``_BM25_STOPWORDS``'s absence above for the measurement).

    Accent folding is the part that earns its place, and it is Spanish-specific
    in the way that matters: a candidate writes ``practicas`` and the page says
    ``prácticas``, so with accents kept the two are different terms and a
    question can name a page it does not lexically match at all. Measured on the
    49 labelled questions at top_k=3, BM25 ranking alone: 0.8163 keeping accents,
    0.8367 folding them -- one question, for one line of ``str.maketrans``.
    """
    folded = text.lower().translate(_BM25_ACCENT_FOLD)
    return [token for token in _BM25_TOKEN_RE.findall(folded) if len(token) > 1]


class Bm25Index:
    """Okapi BM25 over a fixed set of chunk texts, fitted once per ingest.

    THE ASSERTION: given the same chunks, ``score(query)`` is a pure function of
    the query, so the rescue contributes a deterministic rank and not a source
    of run-to-run variation.

    It does NOT choose what is relevant. There is no threshold and no cutoff
    here; the caller decides which chunks are eligible at all (see
    ``RAGPipeline._lexical_rescue``, which hands in only chunks the cosine
    filter already accepted). A lexical ranker with its own idea of what clears
    the bar would silently widen the candidate pool, and the threshold comment
    in ``RAGPipeline.__init__`` would stop being a true statement.

    The weighted matrix is DENSE rather than sparse on purpose. BM25's term
    weight is elementwise in (chunk, term), and the term-frequency saturation
    divides by a per-chunk length, so the whole computation is a handful of
    vectorised operations over a 124 x ~2000 matrix -- 2 MB, built once at
    ingest. Writing it against ``scipy.sparse`` would buy nothing and would make
    the formula harder to read than the thing it computes.

    ``idf`` is the Robertson/Sparck-Jones form with the +1 smoothing sklearn also
    uses in ``TfidfVectorizer``, so it is always positive and a term present in
    every chunk contributes rather than subtracting. Written out here rather than
    taken from a fitted ``TfidfVectorizer`` because a tf-idf cosine is NOT BM25:
    it lacks the saturation below, which is the whole reason this exists instead
    of calling the fallback path.
    """

    def __init__(self, texts: List[str], k1: float, b: float):
        self.k1 = k1
        self.b = b
        self.vocabulary_: Dict[str, int] = {}
        rows: List[Dict[int, int]] = []
        lengths: List[int] = []
        for text in texts:
            counts: Dict[int, int] = {}
            length = 0
            for token in bm25_tokens(text):
                column = self.vocabulary_.setdefault(token, len(self.vocabulary_))
                counts[column] = counts.get(column, 0) + 1
                length += 1
            rows.append(counts)
            lengths.append(length)

        n_docs = len(rows)
        width = len(self.vocabulary_)
        document_frequency = np.zeros(width, dtype=np.float64)
        for counts in rows:
            for column in counts:
                document_frequency[column] += 1.0

        self.idf_ = (
            np.log(1.0 + (n_docs - document_frequency + 0.5) / (document_frequency + 0.5))
            + 1.0
        )

        lengths_array = np.asarray(lengths, dtype=np.float64)
        self.average_length = float(lengths_array.mean()) if n_docs else 1.0

        self.weighted_ = np.zeros((n_docs, width), dtype=np.float64)
        for index, counts in enumerate(rows):
            if not counts:
                continue
            columns = np.fromiter(counts.keys(), dtype=np.intp, count=len(counts))
            frequencies = np.fromiter(
                counts.values(), dtype=np.float64, count=len(counts)
            )
            # |d|/avgdl is per chunk; the saturation is therefore per (chunk,
            # term) and nothing here couples two chunks together.
            length_ratio = lengths_array[index] / (self.average_length or 1.0)
            denominator = frequencies + self.k1 * (1.0 - self.b + self.b * length_ratio)
            self.weighted_[index, columns] = (
                self.idf_[columns] * frequencies * (self.k1 + 1.0) / denominator
            )

    def score(self, query: str, rows: Optional[List[int]] = None) -> np.ndarray:
        """BM25 of ``query`` against the given chunk indices, or against all.

        ``rows`` is the ELIGIBLE subset, not a post-filter: terms are summed over
        the rows handed in, so a chunk outside them contributes nothing. That is
        what lets the caller keep the cosine filter authoritative.

        The query is NOT passed through ``expand_query``, and that is the point.
        The expansion appends synonyms the interviewer did not type, which is a
        reasonable thing to do before EMBEDDING a sentence and a wrong thing to
        do before counting literal terms: the whole premise of this ranker is
        that the candidate named ``fastapi`` and the page says ``FastAPI``.
        Measured at top_k=3 over the 49 labelled questions, expanding here as
        well gives 44 of 49 -- the same count, and not one question different on
        either side, so the expansion buys this ranker nothing.
        """
        vocabulary = self.vocabulary_
        columns: Dict[int, int] = {}
        for token in bm25_tokens(query):
            column = vocabulary.get(token)
            if column is not None:
                columns[column] = columns.get(column, 0) + 1

        selected = (
            np.arange(self.weighted_.shape[0], dtype=np.intp)
            if rows is None
            else np.asarray(rows, dtype=np.intp)
        )
        if not columns or selected.size == 0:
            return np.zeros(selected.size, dtype=np.float64)

        terms = np.fromiter(columns.keys(), dtype=np.intp, count=len(columns))
        occurrences = np.fromiter(
            columns.values(), dtype=np.float64, count=len(columns)
        )
        # The query side saturates too, so a term repeated in one question does
        # not outweigh a term that appears once and matches a rare page name.
        query_weights = (
            occurrences * (self.k1 + 1.0) / (occurrences + self.k1)
        )
        return self.weighted_[np.ix_(selected, terms)].dot(query_weights)


class RAGPipeline:
    """In-memory RAG pipeline with cosine similarity retrieval."""

    #: How many features the TF-IDF fallback keeps. Part of the cache identity
    #: below, because a different ceiling is a different vector space.
    TFIDF_MAX_FEATURES = 384

    #: BM25 term-frequency saturation. k1 is how fast a repeated term stops
    #: paying; b is how much a long chunk is penalised for being long. Both are
    #: the values Robertson and Sparck-Jones published and the ones every BM25
    #: implementation since has kept, and they were NOT swept here: the sweep is
    #: a retrieval decision of its own and deserves its own measurement, not a
    #: number borrowed from the literature inside a change about something else.
    BM25_K1 = 1.5
    BM25_B = 0.75

    #: The smallest ``top_k`` at which the lexical rescue runs.
    #:
    #: It is a floor and not a preference, and the measurement is what set it.
    #: The rescue works by GIVING UP the last dense slot, so what it costs is
    #: whatever that slot was holding. Measured on the full population with the
    #: rescue forced on at both widths:
    #:
    #:     top_k=3   40/49 -> 44/49   5 gained, 1 lost
    #:     top_k=2   39/49 -> 41/49   5 gained, 3 lost
    #:
    #: So at top_k=2 it would still be a net GAIN, and that is exactly why this
    #: is a decision rather than a guard against a regression. Reserving one of
    #: two slots makes the answer context half lexical and changes the evidence
    #: the model reads on 8 of the 49 questions instead of 6; one slot in three
    #: is the share this change was authorised at, and top_k=2 is not the answer
    #: path anyway -- it is what the context panel asks for
    #: (``backend/turns/streaming.py``, ``backend/turns/blocking.py``), while the
    #: answer context is ``config.RAG_TOP_K`` at 3.
    #:
    #: If the intent is that the rescue serves every caller, delete this constant
    #: and re-derive the two top_k=2 figures above in
    #: ``tests/real_wiki.py``. Do not lower it without that measurement.
    MIN_RESCUE_TOP_K = 3

    #: The widest list the cross-encoder will permute.
    #:
    #: It exists because a width is a CLAIM about what the caller will read, and
    #: the two published measurement widths disagree about that. At the shipped
    #: width (``RAG_TOP_K`` = 3) the list IS the context the model reads, in
    #: order, and reordering it is the whole point. At population width -- which
    #: is where ``tests/test_recall_claims.py`` measures ``recall@1``/``@3``/
    #: ``MRR@5``, and ``tests/real_wiki.py`` derives the floors from -- a top_k
    #: of 49 is a RANKING, and only its first three entries were ever a context.
    #: Permuting all 49 would reorder 46 slots no reader sees, cost roughly seven
    #: times the CPU for the same answer, and move three published figures
    #: describing a width the product does not serve.
    #:
    #: So 3 is derived rather than chosen: it is ``RAG_TOP_K``, and it covers the
    #: context panel too, which asks for 2
    #: (``backend/turns/streaming.py``, ``backend/turns/blocking.py``). Raising it
    #: is a decision about a width nobody uses, not a tuning knob.
    MAX_RERANK_TOP_K = 3

    #: There is deliberately NO cosine threshold on the surrendered slot, and
    #: that is a measured decision rather than an omission.
    #:
    #: A threshold looked like the way to make the rescue lossless, and on the
    #: full population it appeared to work: surrendering only slots below 0.50
    #: gave 43 of 49 with nothing lost, against 44 of 49 with one loss. It does
    #: not survive the second population. On the committed corpus the single
    #: question at stake has a surrendered-slot cosine of 0.3097 while the
    #: CHEAPEST gain available is 0.3264 -- the loss sits BELOW every gain, so
    #: there is no threshold that excludes the loss and keeps a gain. Swept at
    #: 0.01 over 0.25-0.69 on both populations, the only thresholds that lose
    #: nothing are the ones that rescue nothing.
    #:
    #: A parameter-free gate was tried in the same place and is worse: requiring
    #: the surrendered slot to sit below the median (or the mean) eligible cosine
    #: for that question blocks all 49, because dense rank 3 is above the median
    #: on every question measured. Comparing the candidate's BM25 against the
    #: surrendered page's own BM25 fires on 49 of 49 and blocks nothing, because
    #: the surrendered page usually matches no query term and scores zero.
    #:
    #: So the trade is taken openly instead of hidden behind a number that does
    #: not generalise: the rescue gains five questions and loses one on each
    #: population, and
    #: ``tests/test_lexical_rescue.py::TestTheRescueOnlyAdds`` asserts the exact
    #: set of questions it costs, so the cost cannot change without the guard
    #: turning red.

    def __init__(self, chunk_size: int = 400, chunk_overlap: int = 50, threshold: float = 0.25,
                 cache_dir: Optional[Path] = None,
                 embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 reranker: Optional["Reranker"] = None):
        """``embedding_model``'s default is the model the app ships, and it is
        kept here rather than read from ``backend.config`` so the service stays
        free of a module-level global: the pipeline is constructed with whatever
        it is told to use, and the configuration layer decides what that is.

        It is spelled out a second time in ``config.py`` on purpose, and the two
        copies are checked against each other by
        ``tests/test_rag_cache_identity.py::TestTheModelNameIsASingleSourceOfTruth``
        -- a duplicated literal that a test keeps in agreement is a contract,
        where a duplicated literal that nothing checks is a future incident.

        ``reranker`` defaults to a DISABLED one, not to an enabled one. A default
        enabled would mean every construction site -- the measurement harnesses,
        the tests that only want embeddings, ``tests/test_rag.py`` -- silently
        inherits a 470 MB download and ~90 ms per query, which is a decision made
        in a default rather than in ``backend.config.py``, where the operator can
        see it. ``backend/main.py`` passes the real one explicitly.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Minimum cosine a chunk needs to be returned at all.
        #
        # MEASURED on the real ``wiki/`` through the production path (real
        # loader, real ``expand_query``), on the labelled questions in
        # ``tests/real_wiki.py``. The guard that holds this number is
        # ``tests/test_rag.py::TestRetrievalThresholdIsHonest``; read it before
        # changing this value, and re-derive its floors rather than adjusting
        # them. A floor that keeps its value while its corpus changes is not a
        # floor.
        #
        # `wiki/` has TWO states, so the corpus-shape figures below come in
        # two. The four FAQ pages that used to make the states differ in SHAPE are
        # committed as of commit `efda998`, so both sides now load 37 pages and
        # chunk to 124 at 400/50; what still differs is the TEXT, because 15
        # `wiki/*.md` files are modified in the working tree and uncommitted. Both
        # are measured; `tests/real_wiki.py::CommentFigures` owns them and the row
        # is chosen by a digest of the served corpus
        # (`tests/real_wiki.py::CORPUS_DIGESTS`), never by a question count,
        # because both rows are 49 questions wide and a count cannot separate them.
        #     full (37 pages, 49 questions) 124 chunks, 49 x 124 (question, chunk)
        #     reduced (37 pages, 49 questions) 124 chunks, 49 x 124 (question, chunk)
        #
        # It is not inert. Over that (question, chunk) matrix this default
        # discards most of the candidate pool, and because the filter runs
        # BEFORE the ``top_k`` slice it changes what a caller receives whenever
        # fewer than ``top_k`` chunks clear it. The earlier claim that
        # "the lowest top-1 cosine is 0.414, so the filter never removes
        # anything" was an inference from the best chunk per question, not a
        # measurement of the filter: 0.414 is a top-1 statistic, and the minimum
        # over the whole matrix is far below it.
        #
        # 0.25 is the RE-MEASURED default, not the inherited one. The previous
        # 0.30 was calibrated against the English embedder, and a cosine scale
        # is a property of the vector space, so it does not survive a change of
        # model. Re-swept on the current one (multilingual model, identity
        # prefix, page-dedup), over 0.20-0.35 in steps of 0.01:
        #
        #   threshold  questions whose top-3 changed vs no filter  min survivors
        #   0.20-0.28   0                                                     3
        #   0.29        1                                                     2
        #   0.30        2                                                     2
        #   0.35        9                                                     0
        #
        # 0.30 sat exactly on the cliff: the third-best score of "cuentame lo de
        # la huelga de camiones en mercadona" is 0.2861 and of "cual es tu nivel
        # de ingles" is 0.2980, so the old default could not serve a full top-3
        # to either of them. 0.25 is chosen over the highest passing value
        # (0.28) because 0.28 clears the thinnest question by 0.006 while 0.25
        # clears it by 0.036, and a default one question's noise away from
        # starvation is not a default, it is a coincidence.
        #
        # WHAT THE FILTER COSTS, in one unit. At ``top_k`` = twice the chunk
        # count, the labelled questions return these counts of RESULTS:
#     full (37 pages, 49 questions) discards 950 of the 1813 results (52.4%) -- 863 results with the filter and 1813 without
#     reduced (37 pages, 49 questions) discards 949 of the 1813 results (52.3%) -- 864 results with the filter and 1813 without
        # The two rows now share every COUNT on this block -- 37 pages, 49
        # questions, 124 chunks, 1813 unfiltered results -- and differ by ONE
        # survivor of the filter, 863 against 864, because the fifteen
        # uncommitted pages are not the fifteen a clone is served. Re-measured
        # 2026-10-05 on the redacted-and-filled working tree: the gap closed from
        # three survivors to one, and the recall figures on both rows did not
        # move at all.
        # and at the shipped top_k=3 it costs the caller one slot in three, which
        # is the fact the two numbers together say: a wide filter that is
        # invisible until a question runs thin.
        #
        # THAT LAST SENTENCE USED TO SAY "nothing at all", and the lexical rescue
        # is what made it false. One slot in three is now offered to BM25 by
        # ``_lexical_rescue``, which ranks over ALL chunks rather than over this
        # filter's survivors, so a page the cosine side rejected can reach the
        # model. That is deliberate and it is what two of the three questions the
        # rescue was built for need: their gold pages score 0.2301 and 0.1703
        # against this 0.25, so they are filtered out of the candidate set
        # entirely rather than ranked low, and no lexical slot can reach a page
        # the dense side never proposed. The counts above are untouched by it,
        # and not by luck -- the rescue cannot run at a ``top_k`` wider than the
        # corpus, which is the width these counts are measured at, and that is
        # asserted rather than assumed in
        # ``tests/test_lexical_rescue.py::TestTheFilterCountsAreNotWidenedByTheRescue``.
        #
        # Both counts are counts of RESULTS, which is the only space
        # ``retrieve()`` can be asked about, and they are bounded by the page
        # count rather than the chunk count. Over the raw cosine cells the
        # filter drops before deduplication the same threshold discards 4100 of
        # 6076 (question, chunk) pairs, 67.5% on the full population -- that is
        # how many candidates it ever sees, and it is a different question from
        # how much the caller loses. This sentence used to read "956 of 6125
        # pairs, 15.6%": 956 was deduplicated results and 6125 was raw cosine
        # cells, so the ratio was a percentage of nothing, and both numbers
        # described a 125-chunk corpus this repository stopped shipping.
        # ``tests/test_recall_claims.py`` now checks each population's two
        # counts, their share and its chunk count against a live measurement.
        self.threshold = threshold
        self.chunks: List[Chunk] = []
        self._embedder = None
        self._use_tfidf = False
        self._tfidf_vectorizer = None
        self._bm25: Optional[Bm25Index] = None
        self._initialized = False
        self._embedding_model = embedding_model
        self._reranker: "Reranker" = reranker if reranker is not None else Reranker(enabled=False)
        self._cache_dir = cache_dir
        self._metadata_path: Optional[Path] = None
        if self._cache_dir:
            self._metadata_path = self._cache_dir / "embeddings.json"

    def initialize(self) -> None:
        """Load the embedding model. Falls back to TF-IDF if unavailable.

        The model name comes from ``self._embedding_model``, which is the same
        field the cache is tagged and validated against. It used to be
        hardcoded here, which made ``config.EMBEDDING_MODEL`` configure nothing:
        the one place a configured model could have taken effect was the one
        place that never read it, while the cache happily claimed the vectors
        came from whatever the configuration said.
        """
        if self._initialized:
            return
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer model %s...", self._embedding_model)
            self._embedder = SentenceTransformer(self._embedding_model)
            logger.info(
                "Sentence-transformer model %s loaded successfully", self._embedding_model
            )
        except Exception as e:
            # ERROR, not warning, and it names the CONSEQUENCE rather than the
            # condition. This used to be one `warning` reading
            # "Sentence-transformers unavailable, falling back to TF-IDF",
            # which is a description of a cause and says nothing about what the
            # operator now has: retrieval running in a different, weaker space,
            # the on-disk cache unusable, and a health endpoint that reported
            # `status: ok` with an unchanged chunk count. A quality regression
            # that nobody can see is not a handled condition.
            logger.error(
                "Sentence-transformers unavailable (%s); falling back to TF-IDF. "
                "Retrieval quality is reduced and the on-disk embedding cache "
                "cannot be used: a TF-IDF space is rebuilt on every ingest, so "
                "vectors from two runs share no geometry.",
                e,
            )
            self._use_tfidf = True
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._tfidf_vectorizer = TfidfVectorizer(
                max_features=self.TFIDF_MAX_FEATURES
            )
        self._initialized = True

    @property
    def mode(self) -> str:
        """Which retrieval space is actually in use: the one thing to report.

        Read by ``/api/health``, the startup log and the status rail, because a
        pipeline that quietly fell back to TF-IDF is otherwise indistinguishable
        from a healthy one: the chunk count is identical, and the health
        endpoint's ``status: ok`` is still true in the only sense that matters
        for liveness.

        Three values, and the third is deliberate. ``uninitialized`` exists so
        that asking before ``initialize()`` cannot produce the answer
        ``embeddings`` -- a claim about a model that has not been loaded yet is
        the same class of lie this property was added to stop, and it would be
        the first one anyone reads.
        """
        if not self._initialized:
            return "uninitialized"
        return "tfidf" if self._use_tfidf else "embeddings"

    @property
    def rerank_mode(self) -> str:
        """Whether the cross-encoder that reorders the top-k is actually in use.

        A SEPARATE property rather than another value of ``mode``, because the
        two fail independently and an operator needs to tell them apart: a
        TF-IDF fallback with a loaded reranker and a multilingual embedder with a
        dead reranker are different deployments with different fixes, and one
        string cannot say which is which.

        It exists at all because of the same argument as ``mode``. The top-k SET
        is identical with and without the reranker -- a permutation cannot change
        it -- so ``rag_chunks``, the recall figures and the filter counts in
        ``/api/health`` are all identical either way. A pipeline that lost its
        reranker is therefore invisible to every other field in that payload, and
        ``status: ok`` over it would be true of a service nobody is running.
        Read by ``/api/health`` via ``EXPECTED_RERANK_MODE``.
        """
        return self._reranker.mode

    @property
    def rerank_identity(self) -> str:
        """Which reordering decision produced the order being served right now.

        Versioned (``RERANK_VERSION``) for the same reason
        ``LEXICAL_RESCUE_VERSION`` is: so that a log line or a health payload can
        name the retrieval DECISION rather than leaving it to be inferred from
        whatever the defaults happen to be today. It is not part of the embedding
        cache's identity, and the reasoning for that is in
        ``backend/services/rerank.py`` -- the cache stores vectors and four text
        fields, and a reordering changes neither.
        """
        return self._reranker.identity

    # ── Embedding cache helpers ──────────────────────────────────────────

    @property
    def _embedder_identity(self) -> str:
        """Which embedder produced (or will consume) the vectors in this run.

        Not the same question as ``_embedding_model``: that is what the model
        field says, and the field is only a claim. This is what is actually
        loaded, so a fallback run cannot describe its TF-IDF numbers as
        sentence-transformer output.
        """
        if self._use_tfidf:
            return f"tfidf:max_features={self.TFIDF_MAX_FEATURES}"
        return f"sentence-transformer:{self._embedding_model}"

    @property
    def _cache_is_readable(self) -> bool:
        """Whether this run may read the on-disk cache at all.

        False for the TF-IDF fallback, and the reason is not caution: a TF-IDF
        vector is a coordinate in a space defined by the vectorizer that
        produced it, and that fitted vectorizer is not in the cache. A run that
        restored TF-IDF numbers would have an unfitted vectorizer to compare
        queries against, and a run with a real embedder that took them would be
        dotting two unrelated spaces together. Neither is a cache hit; both are
        an ungrounded interview.

        A fallback run therefore also writes nothing: there is no consumer for
        those vectors, and a file only somebody can read is not a cache.
        """
        if not self._cache_dir:
            return False
        if self._use_tfidf:
            logger.info(
                "Embedding cache skipped: this run fell back to TF-IDF, whose "
                "vectors no other run could interpret"
            )
            return False
        return True

    @staticmethod
    def _compute_documents_hash(documents: dict[str, str]) -> str:
        """Compute a deterministic SHA-256 hash of all loaded documents."""
        h = hashlib.sha256()
        for filename in sorted(documents.keys()):
            h.update(filename.encode("utf-8"))
            h.update(documents[filename].encode("utf-8"))
        return h.hexdigest()

    def _save_embeddings_cache(self, chunks: List[Chunk], documents_hash: str) -> None:
        """Persist chunks and embeddings to disk for fast startup.

        Saves two files inside ``_cache_dir``:
        - ``embeddings.npz`` — numpy compressed embeddings + metadata arrays.
        - ``embeddings.json`` — everything a later run must match before it may
          serve these vectors: which embedder wrote them, the model, the
          document hash, the chunking, the filter version and the count.
        """
        if not self._cache_is_readable:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            # Pack arrays into a single npz
            ids = np.array([c.id for c in chunks], dtype=object)
            contents = np.array([c.content for c in chunks], dtype=object)
            sources = np.array([c.source for c in chunks], dtype=object)
            sections = np.array([c.section for c in chunks], dtype=object)
            types = np.array([c.type for c in chunks], dtype=object)
            summaries = np.array([c.summary for c in chunks], dtype=object)
            h1s = np.array([c.h1 for c in chunks], dtype=object)
            intents = np.array([c.intent for c in chunks], dtype=object)
            # Tags are variable-length; store as JSON strings
            tags_json = np.array([json.dumps(c.tags) for c in chunks], dtype=object)
            embeddings = np.stack([c.embedding for c in chunks]) if chunks else np.empty((0, 0), dtype=np.float32)

            npz_path = self._cache_dir / "embeddings.npz"
            np.savez_compressed(
                npz_path,
                ids=ids, contents=contents, sources=sources,
                sections=sections, types=types, summaries=summaries, h1s=h1s,
                intents=intents,
                tags_json=tags_json, embeddings=embeddings,
            )

            # Write metadata
            metadata = {
                "embedder": self._embedder_identity,
                "model": self._embedding_model,
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
                "document_hash": documents_hash,
                "chunk_filter_version": CHUNK_FILTER_VERSION,
                "chunk_count": len(chunks),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self._metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            logger.info("Saved embedding cache (%d chunks) to %s", len(chunks), self._cache_dir)
        except Exception as e:
            logger.warning("Failed to save embedding cache: %s", e)

    def _load_embeddings_cache(self, documents: dict[str, str]) -> Optional[List[Chunk]]:
        """Try to load cached embeddings. Returns None if cache is invalid or missing.

        Every field below is a question whose wrong answer silently returns the
        wrong vectors. A cache is only as good as what it claims about itself,
        so anything it does not claim -- including anything written before the
        field existed -- is treated as "recompute", never as "assume".
        """
        if not self._cache_is_readable or not self._metadata_path:
            return None

        npz_path = self._cache_dir / "embeddings.npz"
        if not npz_path.exists() or not self._metadata_path.exists():
            return None

        try:
            # 1. Read and validate metadata
            with open(self._metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            # 1a. Which embedder produced these numbers. This field's absence
            # was the whole defect: a cache written by the TF-IDF fallback was
            # tagged with the sentence-transformer name and restored as if it
            # were embeddings -- which is how a real corpus's 121 cached chunks
            # came back as vectors no MiniLM run had ever produced, and
            # retrieve() returned nothing for any question.
            if meta.get("embedder") != self._embedder_identity:
                logger.info(
                    "Cache embedder mismatch (cache=%s, current=%s) — recomputing "
                    "so vectors from another embedding space are not served",
                    meta.get("embedder"), self._embedder_identity,
                )
                return None

            if meta.get("model") != self._embedding_model:
                logger.info(
                    "Cache model mismatch (cache=%s, current=%s) — recomputing",
                    meta.get("model"), self._embedding_model,
                )
                return None

            # 1b. The chunking. It decides WHICH chunks exist, not merely how
            # big they are, so a cache written at 400/50 says nothing about a
            # run configured at 400/80.
            if (meta.get("chunk_size"), meta.get("chunk_overlap")) != (
                self.chunk_size,
                self.chunk_overlap,
            ):
                logger.info(
                    "Cache chunking mismatch (cache=%s/%s, current=%s/%s) — "
                    "recomputing so the cached chunk set is the one this "
                    "configuration actually produces",
                    meta.get("chunk_size"), meta.get("chunk_overlap"),
                    self.chunk_size, self.chunk_overlap,
                )
                return None

            # 1c. Validate the filtering semantics. The document hash
            # below covers the RAW wiki text, which placeholder filtering does
            # not touch, so a cache written before this filter existed would be
            # otherwise be restored with its [TODO chunks intact.
            if meta.get("chunk_filter_version") != CHUNK_FILTER_VERSION:
                logger.info(
                    "Cache chunk filter version mismatch (cache=%s, current=%s) "
                    "— recomputing so stale placeholders are not served",
                    meta.get("chunk_filter_version"), CHUNK_FILTER_VERSION,
                )
                return None

            # 2. Validate document hash
            current_hash = self._compute_documents_hash(documents)
            if meta.get("document_hash") != current_hash:
                logger.info("Cache document hash stale — recomputing")
                return None

            # 3. Load npz
            data = np.load(npz_path, allow_pickle=True)
            ids = data["ids"]
            contents = data["contents"]
            sources = data["sources"]
            sections = data["sections"]
            types = data["types"]
            summaries = data["summaries"]
            h1s = data["h1s"]
            tags_json = data["tags_json"]
            embeddings = data["embeddings"]

            # ``intent`` is part of ``embedding_text``, so a cache that does not
            # carry it cannot produce the passages the run would have computed.
            # Recompute rather than serve a half-restored chunk set: the same
            # policy as every other unclaimed field above.
            if "intents" not in data.files:
                logger.info("Cache predates the intent field — recomputing")
                return None
            intents = data["intents"]

            if len(ids) != meta.get("chunk_count"):
                logger.info("Cache chunk count mismatch — recomputing")
                return None

            # 4. Rebuild Chunk objects
            chunks: List[Chunk] = []
            for i in range(len(ids)):
                chunks.append(Chunk(
                    id=str(ids[i]),
                    content=str(contents[i]),
                    source=str(sources[i]),
                    section=str(sections[i]),
                    type=str(types[i]),
                    tags=json.loads(str(tags_json[i])),
                    summary=str(summaries[i]),
                    h1=str(h1s[i]),
                    intent=str(intents[i]),
                    embedding=embeddings[i],
                ))

            logger.info("Loaded %d chunks from embedding cache", len(chunks))
            return chunks

        except Exception as e:
            logger.warning("Failed to load embedding cache, recomputing: %s", e)
            return None

    def ingest_documents(self, documents: dict[str, str]) -> int:
        """Chunk and embed documents.

        Attempts to load pre-computed embeddings from cache first.
        Falls back to full computation on any cache miss or error.

        Args:
            documents: Dict of filename -> content.

        Returns:
            Total number of chunks created.
        """
        if not self._initialized:
            self.initialize()

        start_time = time.time()
        self.chunks = []
        # Both branches below replace ``self.chunks``, so the lexical index has
        # to be dropped HERE rather than at each exit: an index fitted over the
        # PREVIOUS ingest is the one way this class could serve a BM25 score for
        # a chunk that no longer exists. See ``_lexical_index``.
        self._bm25 = None

        # ── Try cache first ────────────────────────────────────────────
        cached = self._load_embeddings_cache(documents)
        if cached is not None:
            self.chunks = cached
            self._warm_reranker()
            elapsed = time.time() - start_time
            logger.info("RAG cache hit — %d chunks restored in %.3fs", len(self.chunks), elapsed)
            return len(self.chunks)

        # ── Cache miss: chunk + embed from scratch ─────────────────────
        for filename, content in documents.items():
            doc_chunks = self._chunk_document(filename, content)
            self.chunks.extend(doc_chunks)

        logger.info("Created %d chunks from %d documents", len(self.chunks), len(documents))

        if self.chunks:
            self._compute_embeddings()

        # ── Persist new cache ──────────────────────────────────────────
        doc_hash = self._compute_documents_hash(documents)
        self._save_embeddings_cache(self.chunks, doc_hash)

        self._warm_reranker()

        elapsed = time.time() - start_time
        logger.info("Ingestion (compute) completed in %.2fs", elapsed)
        return len(self.chunks)

    def _warm_reranker(self) -> None:
        """Load the cross-encoder now, so the first query does not pay for it.

        Deliberately at the END of the ingest and on BOTH branches, because the
        two branches are the two ways a corpus arrives and a warm that only
        happens on a cache miss would leave the cold path paying ~20 s inside a
        user's turn -- with somebody waiting. The trade is that startup pays it
        once instead, which is the moment where paying it costs nothing.

        It is a WARM, not a requirement: ``ensure_loaded`` swallows its own
        failure and records ``failed``, so a deploy with no network still ingests
        and still serves the dense+BM25 order. That is why this returns nothing
        and why the caller does not check.
        """
        if not self._reranker.enabled:
            return
        self._reranker.ensure_loaded()

    def _chunk_document(self, filename: str, content: str) -> List[Chunk]:
        """Split a document into chunks, respecting heading boundaries.

        YAML frontmatter (if present) is parsed for metadata and stripped
        from the content before chunking.

        Placeholder filtering (see ``strip_placeholders``): a ``confidence: low``
        page contributes nothing, and ``[TODO ...]`` markers are removed from
        every other page. Anything stripped is reported as ONE warning naming the
        document, so the owner can see the wiki's holes instead of the pipeline
        hiding them. ``confidence: medium`` is served untouched — it is reviewed
        real content, not a placeholder.
        """
        metadata, content = parse_frontmatter(content)
        doc_type = str(metadata.get("type", ""))
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        summary = str(metadata.get("summary_1line", ""))

        confidence = str(metadata.get("confidence", "") or "").strip().lower()
        if confidence == "low":
            # Per wiki/CONVENCIONES.md, `low` means draft/placeholder content
            # awaiting the owner's confirmation. Serving it is how the LLM ends
            # up stating something the candidate never did.
            logger.warning(
                "Skipping %s: confidence=low (draft/placeholder per the wiki's "
                "confidence lifecycle) — it must not reach the LLM until the "
                "owner confirms it. Filter version %s.",
                filename, CHUNK_FILTER_VERSION,
            )
            return []

        content, removed = strip_placeholders(content)
        if removed:
            logger.warning(
                "Stripped %d [TODO] placeholder(s) from %s — that page is "
                "incomplete; fill them in so the answers are grounded. "
                "Filter version %s.",
                removed, filename, CHUNK_FILTER_VERSION,
            )

        chunks = []

        # The page's own title, read BEFORE the split, because the split merges
        # it into the first section and no later chunk would carry it. Recorded
        # on every chunk (see ``document_h1`` and ``embedding_text``).
        h1 = document_h1(content)

        # Split by headings, keeping the document's own H1 with the body it
        # titles (an orphaned title is a chunk with no answer in it).
        sections = split_sections(content)

        # Sections that are only lists of links to other pages are navigation,
        # not answers. They are dropped rather than indexed (see
        # ``is_wikilink_reference``): each one names a document that is indexed
        # on its own, so nothing is lost and a top-k slot stops being spent on
        # text that cannot answer anything.
        #
        # A heading with no body at all is dropped for the same reason and by
        # the same rule, extended: it holds no answer either (see
        # ``is_bare_heading``). Both predicates are asked in the same loop
        # because they are the same defect -- a section that states nothing --
        # arriving by two different routes.
        kept_sections = []
        dropped = 0
        dropped_empty = 0
        for section in sections:
            stripped = section.strip()
            if is_wikilink_reference(stripped):
                dropped += 1
                continue
            if is_bare_heading(stripped):
                dropped_empty += 1
                continue
            kept_sections.append(section)
        if dropped:
            logger.info(
                "Dropped %d wikilink-only reference section(s) from %s — they "
                "list links to other pages and hold no answer. Filter version %s.",
                dropped, filename, CHUNK_FILTER_VERSION,
            )
        if dropped_empty:
            logger.info(
                "Dropped %d bodyless heading section(s) from %s — a heading with "
                "no body under it cannot answer anything. Filter version %s.",
                dropped_empty, filename, CHUNK_FILTER_VERSION,
            )

        chunk_id = 0
        for section in kept_sections:
            section = section.strip()
            if not section:
                continue

            # Extract section name from first heading
            heading_match = re.match(r'^#{1,3}\s+(.+)', section)
            section_name = heading_match.group(1) if heading_match else filename

            # What question type this section answers, read off its own heading
            # and body (``section_intent``). Computed ONCE per section and stamped
            # on every sub-chunk below, deliberately: when a section is long
            # enough to split, all of its pieces are the same KIND of answer, and
            # a per-piece label would be a function of where the word count
            # happened to fall, which is not a property of the content.
            intent = section_intent(section, doc_type)

            # If section fits in one chunk, keep it whole
            if len(section.split()) <= self.chunk_size:
                chunks.append(Chunk(
                    id=f"{filename}-{chunk_id}",
                    content=section,
                    source=filename,
                    section=section_name,
                    type=doc_type,
                    tags=tags,
                    summary=summary,
                    h1=h1,
                    intent=intent,
                ))
                chunk_id += 1
            else:
                # Split by token count with overlap
                words = section.split()
                start = 0
                while start < len(words):
                    end = min(start + self.chunk_size, len(words))
                    chunk_text = " ".join(words[start:end])
                    chunks.append(Chunk(
                        id=f"{filename}-{chunk_id}",
                        content=chunk_text,
                        source=filename,
                        section=section_name,
                        type=doc_type,
                        tags=tags,
                        summary=summary,
                        h1=h1,
                        intent=intent,
                    ))
                    chunk_id += 1
                    start += self.chunk_size - self.chunk_overlap

        return chunks

    def _compute_embeddings(self) -> None:
        """Compute embeddings for all chunks.

        The text embedded per chunk is ``embedding_text(chunk)`` — the page
        identity followed by the body — and NOT ``chunk.content`` on its own.
        ``content`` is still what the LLM is shown, so the prefix buys retrieval
        without changing a single word of the answer.
        """
        texts = [embedding_text(c) for c in self.chunks]

        if self._use_tfidf:
            logger.info("Computing TF-IDF embeddings for %d chunks", len(texts))
            matrix = self._tfidf_vectorizer.fit_transform(texts)
            embeddings = matrix.toarray().astype(np.float32)
        else:
            logger.info("Computing sentence embeddings for %d chunks", len(texts))
            embeddings = self._embedder.encode(texts, show_progress_bar=False)
            embeddings = np.array(embeddings, dtype=np.float32)

        for i, chunk in enumerate(self.chunks):
            chunk.embedding = embeddings[i]

        # Normalize for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = embeddings / norms
        for i, chunk in enumerate(self.chunks):
            chunk.embedding = normalized[i]

    @property
    def _lexical_index(self) -> Optional[Bm25Index]:
        """The BM25 index over the current chunks, fitted on first use.

        Built lazily rather than in ``ingest_documents`` because most runs never
        retrieve: the context panel path, the health endpoint and every ingest
        that is only measuring do not, and fitting an index nobody scores is
        work done for nothing. ``ingest_documents`` is the only place that
        replaces ``self.chunks``, and it drops the index there, so what is fitted
        always matches what is being retrieved.

        ``None`` when there is no text to rank. A pipeline with no chunks is not
        a pipeline that should fail on the way to returning ``[]``.
        """
        if self._bm25 is None and self.chunks:
            self._bm25 = Bm25Index(
                [embedding_text(c) for c in self.chunks],
                k1=self.BM25_K1,
                b=self.BM25_B,
            )
        return self._bm25

    def retrieve(self, query: str, top_k: int = 3) -> List[Tuple[Chunk, float]]:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: Search query.
            top_k: Number of results to return.

        Returns:
            List of (Chunk, score) tuples ordered by descending similarity.

        NOTE ON THE SCORE FILTER
        -------------------------
        Results are filtered at ``self.threshold`` and the filter is documented
        and measured at ``__init__``, where its value is justified. It was
        deliberately NOT a per-call argument: no production caller passed one,
        and a second way to set it is a second unmeasured value — which is how
        a filter this narrow came to be read as "inert" when it was quietly
        discarding 29% of the candidate pool.

        Because the filter runs before the ``top_k`` slice, it can only change
        what a caller receives when fewer than ``top_k`` chunks clear it, so the
        case is the normal case rather than an edge to know about before raising
        the threshold. See ``__init__`` for the measurements and
        ``tests/test_rag.py::TestRetrievalThresholdIsHonest`` for the floors
        they are held to.

        NOTE ON THE ABSENCE OF A DOCUMENT-TYPE FILTER
        ----------------------------------------------
        This signature used to take ``doc_type``, and the only caller that ever
        passed it was guessing: ``detect_doc_type`` matched the question against
        a keyword table and handed the guess straight to a hard filter over the
        candidate set. On the 49 real-corpus labelled questions the guess fired
        14 times and named a type the gold page does not carry in 8 of them,
        which deletes the answer from the candidate set; strict recall@3 over
        the production path was 0.5714 with the guess applied and 0.6531
        without. Two questions fixed, six broken.

The filter was removed rather than repaired because a substring match
        has no business deciding what the model is allowed to read, and because
        the only honest fix for a filter that removes the right answer some of the
        time is to not filter. The keyword table went with it: keeping a
        table whose every key named a type nothing could filter on would be
        keeping a lookup nobody calls. ``Chunk.type`` stays -- it is read from
        frontmatter, shown in the context header, and part of the embedding
        cache's identity; what is gone is the filter over it.

        NOTE ON THE LEXICAL RESCUE
        ---------------------------
        One of the three slots is offered to BM25 when the cut, not the filter,
        is what lost the answer. Everything above this note is unchanged: the
        filter still runs at ``self.threshold``, the sort still decides the dense
        order, and a query whose eligible pages fit in ``top_k`` returns exactly
        what it returned before. See ``_lexical_rescue`` for the gate and
        ``backend/services/rag.py`` LEXICAL_RESCUE_VERSION for why the embedding
        cache does not carry it.

        NOTE ON THE CROSS-ENCODER RE-RANK
        ----------------------------------
        The list that comes out of the two stages above is then PERMUTED by a
        cross-encoder, and that is the last thing that happens. Reordering rather
        than re-selecting is the whole safety argument: the cosine threshold
        stays a threshold on cosenos applied BEFORE the reranker, the set of
        pages served is bit-for-bit the set without it, and ``recall@3`` is
        therefore untouchable at this stage by construction rather than by a
        favourable measurement. What moves is the ORDER, and with it ``recall@1``:
        measured on the 49 labelled questions, 36 -> 40, net +4 (7 promotions,
        3 demotions); on the reduced population, the same 49 questions over the
        committed text, 37 -> 40, net +3 (8 promotions, 5 demotions).

        Two costs are real and are not hidden by that guarantee. It adds ~90 ms of
        CPU per query to a ~24 ms retrieval, and it cannot recover a page the
        cosine filter already discarded -- five of the 49 questions stay out of
        the top-3 for that reason and no reordering reaches them. See
        ``backend/services/rerank.py`` and ``_rerank``.
        """
        if not self.chunks:
            return []

        threshold = self.threshold

        candidates = self.chunks

        # Expand the query with synonyms before embedding to improve recall
        expanded_query = expand_query(query)

        # Embed the query
        if self._use_tfidf:
            query_vec = self._tfidf_vectorizer.transform([expanded_query]).toarray().astype(np.float32)[0]
        else:
            query_vec = self._embedder.encode([expanded_query])[0].astype(np.float32)

        # Normalize query vector
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Compute cosine similarities
        scores = []
        for chunk in candidates:
            if chunk.embedding is not None:
                score = float(np.dot(query_vec, chunk.embedding))
                if score >= threshold:
                    scores.append((chunk, score))

        # Sort by descending score
        scores.sort(key=lambda x: x[1], reverse=True)
        dense = self._one_chunk_per_page(scores, top_k)
        if len(dense) < top_k:
            # Fewer eligible PAGES than slots: the cut had nothing to cut, so
            # there is nothing for a rescue to rescue and the dense list is the
            # answer. This is the same condition the note above describes, read
            # on the result instead of on the filter.
            return self._rerank(query, dense, top_k)
        return self._rerank(query, self._lexical_rescue(query, dense, top_k), top_k)

    def _rerank(
        self, query: str, ordered: List[Tuple[Chunk, float]], top_k: int
    ) -> List[Tuple[Chunk, float]]:
        """Reorder an already-chosen list with the cross-encoder, or return it.

        THE ASSERTION: the returned list holds the SAME chunks in a different
        order. Nothing is added, nothing is dropped, and the number of distinct
        pages is unchanged -- which is what makes ``recall@3`` structurally
        untouchable by this stage rather than merely unregressed in one
        measurement. The distinctness itself is inherited from
        ``_one_chunk_per_page``, and a permutation cannot break it.

        WHY IT GOES LAST, AFTER THE RESCUE
        ----------------------------------
        Because this stage decides ORDER and the other two decide SET. A
        cross-encoder that could add a page would be changing which pages the
        model is allowed to read, which is the decision ``__init__`` measures and
        publishes with a name, and it would put ``recall@3`` at risk for a gain
        this project has not measured. Permuting last keeps every set-valued
        claim in this repository exactly true.

        The passage handed to the model is ``embedding_text(chunk)``, NOT
        ``chunk.content``, and that choice is worth nine questions: with
        ``content`` the measured net is **-5** (6 promotions, 11 demotions) and
        with ``embedding_text`` it is **+4** (7 and 3). The bi-encoder, BM25 and
        this cross-encoder all read the same text for that reason; the identity
        prefix is what tells a 0.1B model which page it is looking at. Measured
        and asserted by ``tests/test_rerank.py``.

        The returned SCORE is the cross-encoder logit, for the same reason
        ``_lexical_rescue`` returns BM25 rather than the cosine: it is the
        quantity that decided the position. The panel's score column is
        consequently heterogeneous across three scales, which was already true
        of two of them and is visible rather than hidden.

        It is gated by ``MAX_RERANK_TOP_K`` and not applied to every width, for the
        same structural reason the rescue is gated by ``MIN_RESCUE_TOP_K``: a
        width is a claim about what the caller will read. A top_k wide enough to
        hold every page (``tests/test_recall_claims.py`` measures at exactly that)
        is a RANKING, not a context -- reordering it reorders slots that no
        reader ever sees, costs ~7x the CPU, and would move the published
        population-width figures for a change nobody experiences. The gate is
        derived from the shipped width on purpose: ``RAG_TOP_K`` is 3 and the
        context panel asks for 2, so 3 covers every production caller.
        """
        if (
            not self._reranker.enabled
            or top_k > self.MAX_RERANK_TOP_K
            or len(ordered) < 2
        ):
            # Fewer than two slots has no order to improve, and skipping it saves
            # a pointless model call on the questions where the corpus is thin.
            return ordered
        passages = [embedding_text(chunk) for chunk, _ in ordered]
        try:
            ranked = self._reranker.rerank(query, passages)
        except Exception:  # noqa: BLE001 — degradar, nunca propagar
            # ``Reranker.rerank`` already swallows its own failures, so this is
            # defence in depth rather than the primary path: it also covers a
            # subclass, and it means the guarantee "a reranker failure never
            # breaks retrieval" is a property of THIS call site instead of a
            # property of another module's internal discipline. A component that
            # costs 90 ms, 470 MB and 1 GB of RSS can fail in ways this file
            # cannot enumerate, and a retrieval pipeline with an LLM behind it
            # is the wrong place to find out which one it was.
            logger.exception(
                "Cross-encoder re-rank raised; serving the dense+BM25 order "
                "unchanged for %r",
                query,
            )
            return ordered
        if ranked is None:
            # Degraded: no model, or the model failed. The dense+BM25 order is a
            # measured configuration (36/49 and 31/33 gold@1), not a nameless
            # failure mode, so it is served unchanged and ``/api/health`` reports
            # why through ``rerank_mode``.
            return ordered
        return [(ordered[index][0], score) for index, score in ranked]

    def _lexical_rescue(
        self,
        query: str,
        dense: List[Tuple[Chunk, float]],
        top_k: int,
    ) -> List[Tuple[Chunk, float]]:
        """Trade the LAST dense slot for the best page BM25 says was missed.

        THE ASSERTION: a question that already had its gold page inside the
        dense top-k keeps it, and a question whose eligible pages fit in top_k is
        returned untouched. Both are checked directly on the real corpus by
        ``tests/test_lexical_rescue.py::TestTheRescueOnlyAdds``.

        It does this by ADDING a page, never by re-ranking the dense ones: the
        first ``top_k - 1`` results are the dense results, verbatim and in their
        own order, so a page the cosine side already ranked into the answer
        cannot be displaced by one it did not. Measured at top_k=3 over the 49
        labelled questions: strict recall@3 0.8163 -> 0.8980, five questions
        gained and one lost.

        IT IS NOT LOSSLESS, and it is worth being exact about that, because
        "the rescue only adds" is the sentence that makes this look free. A top-k
        is a fixed number of slots: offering one of them to a second ranker
        necessarily means the slot is no longer available to the first. What IS
        guaranteed is directional -- the kept ``top_k - 1`` are never re-ordered,
        never swapped and never dropped -- so the rescue can only change the
        answer at rank ``top_k``. That single rank is worth five questions on
        each population and costs one, and no threshold on the surrendered slot
        can separate the two (see ``RESCUE_MAX_SURRENDERED_COSINE``'s absence
        above). The question it costs is named, per population, in
        ``tests/lexical_rescue_losses.py``.

        WHY IT IS GATED ON THE CUT, WHICH IS THE ONLY THING THAT WORKS HERE
        ----------------------------------------------------------------------
        The first design was "supplement when the dense side returns fewer than
        top_k". On this corpus that condition is never true: the dense side
        fills 3 of 3 on 49 of 49 questions, so the branch was dead code and moved
        nothing (measured: 0 of 49 fired, identical figures on all four metrics).
        The failure this compensates for is the CUT losing an eligible page, so
        the gate asks whether the cut bound at all, which is exactly
        ``len(dense) == top_k``.

        WHY THE CANDIDATE POOL IGNORES THE COSINE THRESHOLD
        ---------------------------------------------------
        This is the part that looks like a mistake and is not, so it is measured
        rather than argued. Two of the three questions this rescue was built for
        are not RANKING misses at all -- their gold pages are BELOW the 0.25
        threshold, at best-cosine 0.2301 (``skills/backend.md``) and 0.1703
        (``stories/autodidacta-fastapi-docker-async.md``) against a page that
        would have been served at 0.3895. Restricting BM25 to the filter's own
        survivors therefore cannot recover them, and does not: measured at
        top_k=3, that variant reaches 42 of 49 and recovers NONE of the three,
        against 45 of 49 and two of three for this one. ``backend/config.py``
        already records why that is not a bug to be argued away -- the previous
        0.30 was calibrated against a different vector space, and a cosine scale
        is a property of the model, not of the corpus.

        The consequence is stated rather than hidden: one slot in three can now
        hold a page the cosine filter rejected, so "at the shipped top_k=3 it
        costs the caller nothing at all" is no longer true of the threshold and
        was corrected where it is published. What the filter still decides
        UNCONDITIONALLY is the dense two slots, and the measured result counts
        the threshold publishes are untouched, because the rescue cannot run at
        the ``top_k`` twice the chunk count those counts are measured at -- see
        ``tests/test_lexical_rescue.py::TestTheRescueLeavesTheCountsAlone``.

        WHAT THE RETURNED SCORE IS
        --------------------------
        The BM25 score, not the chunk's cosine. It is the quantity that actually
        selected the page, and the two are not the same scale: a lexical rank is
        unbounded while a cosine sits in [-1, 1]. Substituting the cosine would
        publish a plausible-looking number with no relationship to why the page
        was chosen, and would put a 0.26 above a 0.45 in the context panel. The
        cost is that the panel's score column is now heterogeneous, which is
        visible rather than hidden.
        """
        if top_k < self.MIN_RESCUE_TOP_K:
            return dense

        index = self._lexical_index
        if index is None:
            return dense

        taken = {chunk.source for chunk, _ in dense}
        pool = [
            position
            for position, chunk in enumerate(self.chunks)
            if chunk.source not in taken
        ]
        if not pool:
            return dense

        scores = index.score(query, rows=pool)
        if not scores.size or float(scores.max()) <= 0.0:
            # The query shares no term with anything in the corpus -- every token
            # of it was a function word. BM25 has no opinion, so every score is
            # zero and sorting them returns CORPUS ORDER, whose first element is
            # whichever page happens to chunk first. That is not a weak match,
            # it is an arbitrary page wearing a score, and it would displace the
            # third-best cosine match to say so. Declining leaves the dense
            # answer intact, which is the right answer to a question with no
            # lexical content. Pinned by
            # ``tests/test_lexical_rescue.py::test_a_query_with_no_lexical_evidence_injects_nothing``.
            return dense
        # Stable, so chunks BM25 ties keep the corpus order they arrived in.
        order = np.argsort(-scores, kind="stable")
        ranked = [(self.chunks[pool[i]], float(scores[i])) for i in order]
        rescued = self._one_chunk_per_page(ranked, 1)
        if not rescued:
            return dense
        return dense[: top_k - 1] + rescued

    @staticmethod
    def _one_chunk_per_page(
        scores: List[Tuple[Chunk, float]], top_k: int
    ) -> List[Tuple[Chunk, float]]:
        """Cut ``top_k`` out of score-ordered results, at most one chunk per page.

        A top-k slot is a slot some OTHER page cannot occupy, and a long page
        can spend all of them. Measured: "empezaste como frutero en mercadona
        no" returned ``[dejar-mercadona-para-dam, lo-mas-dificil-dam,
        dejar-mercadona-para-dam]`` — two of the three slots on the same page,
        so the model was handed three fragments of one story and no fact.

        It is a cut, not a filter. The threshold above still decides what is
        eligible, the sort still decides the order, and the FIRST chunk of each
        page in score order is the one that survives — so the best-matching
        chunk of a page is never traded away for a worse one, and rank 1 cannot
        move. Consequently this can only ADD pages to a top-k, never remove
        one, which is why it improves recall instead of trading against it.

        Fewer than ``top_k`` results is the correct outcome when the corpus
        simply has fewer than ``top_k`` distinct pages above the threshold:
        padding the list back to length would mean either re-admitting a page
        this just excluded or returning something below the threshold.
        """
        taken: List[Tuple[Chunk, float]] = []
        seen_sources: set = set()
        for chunk, score in scores:
            source = chunk.source
            if source in seen_sources:
                continue
            seen_sources.add(source)
            taken.append((chunk, score))
            if len(taken) == top_k:
                break
        return taken

    def _retrieve_for_context(self, query: str, top_k: int) -> List[Tuple[Chunk, float]]:
        """The retrieval both context shapes are built from.

        One definition of "what does this query retrieve", so the two public
        formatters cannot drift into asking the pipeline different questions.

        It used to pass ``doc_type=detect_doc_type(query)`` -- a guess from a
        keyword table, fed straight into a hard filter over the candidate set.
        That is gone, along with the guess, the table and the filter itself; the
        measurement that removed them is recorded on ``retrieve()``, which is
        where the argument used to live and where a reader now looks.

        What replaced the argument is a property rather than a number, and it
        holds whatever the recall figures happen to be: for every labelled
        question, the production path must be able to return the gold page. A
        pre-filter that can delete the answer from the candidate set is a
        correctness hazard, not a precision knob -- the caller cannot tell an
        unfiltered miss from a filtered-out hit, and at interview time the two
        look identical, because the model simply answers from whatever is left.
        See ``tests/test_rag.py::TestRetrievalRegressionGuard`` and the floors
        in ``tests/real_wiki.py``.
        """
        return self.retrieve(query, top_k=top_k)

    def _format_context_string(self, results: List[Tuple[Chunk, float]]) -> str:
        """Render retrieved chunks as the LLM's context block."""
        if not results:
            return ""

        parts = []
        for chunk, score in results:
            header = f"[Source: {chunk.source}"
            if chunk.type:
                header += f" | Tipo: {chunk.type}"
            if chunk.summary:
                header += f" | Resumen: {chunk.summary}"
            header += "]"
            if chunk.type or chunk.summary:
                parts.append(f"{header}\n{chunk.content}")
            else:
                # Legacy format for chunks without metadata
                parts.append(f"{header} {chunk.content}")

        return "\n\n".join(parts)

    def _format_chunks_with_scores(self, results: List[Tuple[Chunk, float]]) -> List[dict]:
        """Render retrieved chunks as serializable dicts for the context panel."""
        return [
            {"text": chunk.content, "score": round(score, 3), "source": chunk.source}
            for chunk, score in results
        ]

    def get_context_string(self, query: str, top_k: int = 3) -> str:
        """Retrieve and format context for the LLM.

        Args:
            query: User's question.
            top_k: Number of chunks to retrieve.

        Returns:
            Formatted context string, or empty string if no relevant chunks.
        """
        return self._format_context_string(self._retrieve_for_context(query, top_k))

    def get_chunks_with_scores(self, query: str, top_k: int = 3) -> List[dict]:
        """Retrieve chunks with similarity scores as serializable dicts.

        Args:
            query: User's question.
            top_k: Number of chunks to return.

        Returns:
            List of dicts: [{"text": "...", "score": 0.82, "source": "cv.md"}, ...]
        """
        return self._format_chunks_with_scores(self._retrieve_for_context(query, top_k))

    def retrieve_with_context(
        self, query: str, top_k: int = 3
    ) -> tuple[str, List[dict]]:
        """Both context shapes for one query, from ONE retrieval.

        The streaming turn needs the LLM's context string AND the context
        panel's scored chunks. Asking for them separately meant calling
        ``retrieve()`` twice, and ``retrieve()`` embeds the query -- so the same
        question was embedded twice per turn, identically, for the same answer.

        This is the deduplication, not a cache. ``retrieve()`` is a pure
        function of ``(query, top_k)``: it reads ``self.chunks`` and
        ``self._embedder``, calls ``expand_query``, and sorts on score with a
        stable sort -- it mutates nothing that a caller can observe. One call
        therefore yields exactly what two calls yielded, and both formatters are
        pure functions of the result. No per-turn state exists to leak between
        requests and no result is memoised, so there is no answer cache to go
        stale and no key to be wrong.

        ONE SCOPE CHANGE, because the cross-encoder made the claim above too
        strong. ``retrieve()`` now also reads ``self._reranker``, whose ``mode``
        is MUTABLE: it goes ``uninitialized`` -> ``loaded`` when the warm at the
        end of ``ingest_documents`` finishes. The same question asked before and
        after that warm can therefore come back in a different ORDER -- never with
        a different set of pages, which is the guarantee that matters and is
        untouched. The dedup this method exists for is unaffected either way,
        because both calls inside one turn see the same mode: the warm happens
        once per ingest, not per query, and ``ingest_documents`` is not reachable
        from a turn. What would break the claim is a reload mid-turn, and nothing
        does that.

        ONE THING IS now fitted rather than recomputed per call, and the claim
        above is scoped to say so: ``_lexical_index`` builds the BM25 matrix on
        first retrieval and keeps it. It is a pure function of ``self.chunks``
        -- which ``embedding_text`` derives from fields the embedding cache
        already persists -- so a fitted index cannot describe a different corpus
        than the one being retrieved, and ``ingest_documents`` drops it in the
        one place ``self.chunks`` is replaced. What it buys is not a faster
        answer but a consistent one: re-fitting per call would make two
        retrievals of the same question depend on nothing at all, which is worth
        less than it sounds and costs a 124 x ~2000 matrix every question.

        Returns:
            ``(context_string, chunks_with_scores)`` -- in that order, matching
            the order the streaming pipeline uses them in.
        """
        results = self._retrieve_for_context(query, top_k)
        return (
            self._format_context_string(results),
            self._format_chunks_with_scores(results),
        )

