"""RAG pipeline — document chunking, embedding, and retrieval."""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

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
CHUNK_FILTER_VERSION = "5"

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
    """
    parts: List[str] = []
    seen: set = set()
    for candidate in (chunk.h1, chunk.summary, chunk.section):
        value = candidate.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        parts.append(value)
    if not parts:
        return chunk.content
    return f"{'. '.join(parts)}. {chunk.content}"



class RAGPipeline:
    """In-memory RAG pipeline with cosine similarity retrieval."""

    #: How many features the TF-IDF fallback keeps. Part of the cache identity
    #: below, because a different ceiling is a different vector space.
    TFIDF_MAX_FEATURES = 384

    def __init__(self, chunk_size: int = 400, chunk_overlap: int = 50, threshold: float = 0.25,
                 cache_dir: Optional[Path] = None,
                 embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        """``embedding_model``'s default is the model the app ships, and it is
        kept here rather than read from ``backend.config`` so the service stays
        free of a module-level global: the pipeline is constructed with whatever
        it is told to use, and the configuration layer decides what that is.

        It is spelled out a second time in ``config.py`` on purpose, and the two
        copies are checked against each other by
        ``tests/test_rag_cache_identity.py::TestTheModelNameIsASingleSourceOfTruth``
        -- a duplicated literal that a test keeps in agreement is a contract,
        where a duplicated literal that nothing checks is a future incident.
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Minimum cosine a chunk needs to be returned at all.
        #
        # MEASURED on the corpus this repository's tests actually load --
        # the real ``wiki/`` (124 chunks at 400/50), the 49 labelled questions
        # in ``tests/real_wiki.py``, real ``expand_query``. The guard that
        # holds this number is
        # ``tests/test_rag.py::TestRetrievalThresholdIsHonest``; read it before
        # changing this value, and re-derive its floors rather than adjusting
        # them. A floor that keeps its value while its corpus changes is not a
        # floor.
        #
        # It is not inert. Over the 49 x 124 (question, chunk) matrix this
        # default discards most of the candidate pool, and because the filter
        # runs BEFORE the ``top_k`` slice it changes what a caller receives
        # whenever fewer than ``top_k`` chunks clear it. The earlier claim that
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
        # count the 49 labelled questions return 854 results with the filter
        # and 1813 without, so at 0.25 the filter discards 959 of the 1813
        # results -- 52.9% -- and at the shipped top_k=3 it costs the caller
        # nothing at all, which is the fact the two numbers together say: a wide
        # filter that is invisible until a question runs thin.
        #
        # Both counts are counts of RESULTS, which is the only space
        # ``retrieve()`` can be asked about, and they are bounded by the page
        # count (37) rather than the chunk count. Over the raw cosine cells the
        # filter drops before deduplication the same threshold discards 4100 of
        # 6076 (question, chunk) pairs, 67.5% -- that is how many candidates it
        # ever sees, and it is a different question from how much the caller
        # loses. This sentence used to read "956 of 6125 pairs, 15.6%": 956 was
        # deduplicated results and 6125 was raw cosine cells, so the ratio was a
        # percentage of nothing, and both numbers described a 125-chunk corpus
        # this repository stopped shipping.
        # ``tests/test_recall_claims.py`` now checks the two counts, their share
        # and the chunk count against a live measurement.
        self.threshold = threshold
        self.chunks: List[Chunk] = []
        self._embedder = None
        self._use_tfidf = False
        self._tfidf_vectorizer = None
        self._initialized = False
        self._embedding_model = embedding_model
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
            # Tags are variable-length; store as JSON strings
            tags_json = np.array([json.dumps(c.tags) for c in chunks], dtype=object)
            embeddings = np.stack([c.embedding for c in chunks]) if chunks else np.empty((0, 0), dtype=np.float32)

            npz_path = self._cache_dir / "embeddings.npz"
            np.savez_compressed(
                npz_path,
                ids=ids, contents=contents, sources=sources,
                sections=sections, types=types, summaries=summaries, h1s=h1s,
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

        # ── Try cache first ────────────────────────────────────────────
        cached = self._load_embeddings_cache(documents)
        if cached is not None:
            self.chunks = cached
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

        elapsed = time.time() - start_time
        logger.info("Ingestion (compute) completed in %.2fs", elapsed)
        return len(self.chunks)

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
        the only honest fix for a filter that removes the right answer some of
        the time is to not filter. The keyword table went with it: keeping a
        table whose every key named a type nothing could filter on would be
        keeping a lookup nobody calls. ``Chunk.type`` stays -- it is read from
        frontmatter, shown in the context header, and part of the embedding
        cache's identity; what is gone is the filter over it.
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
        return self._one_chunk_per_page(scores, top_k)

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
        stable sort -- it mutates nothing. One call therefore yields exactly
        what two calls yielded, and both formatters are pure functions of the
        result. Nothing is memoised, so there is no cache to go stale, no key
        to be wrong, and no per-turn state to leak between requests.

        Returns:
            ``(context_string, chunks_with_scores)`` -- in that order, matching
            the order the streaming pipeline uses them in.
        """
        results = self._retrieve_for_context(query, top_k)
        return (
            self._format_context_string(results),
            self._format_chunks_with_scores(results),
        )

