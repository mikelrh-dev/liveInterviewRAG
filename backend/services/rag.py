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

# Canonical document types -> the frontmatter ``type:`` spellings that map to
# them. Data-driven from the ``type:`` values actually present in wiki/ (all
# SINGULAR: profile, project, experience, skills, story, opinion, decision, faq),
# extended with the plural spellings authors reach for naturally.
#
# This table replaces the old ``if doc_type == "projects" ...`` special-case that
# only ever covered project: because QUERY_TYPE_KEYWORDS used PLURAL keys
# ("stories", "opinions", "decisions") while the wiki used singular values, a
# recruiter question about a story, opinion or decision produced a filter that
# matched nothing and returned [] with no warning at all — the LLM then answered
# with zero grounding from the candidate's own profile.
DOC_TYPE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "profile": ("profile", "profiles"),
    "project": ("project", "projects"),
    "experience": ("experience", "experiences"),
    "skills": ("skill", "skills"),
    "story": ("story", "stories"),
    "opinion": ("opinion", "opinions"),
    "decision": ("decision", "decisions"),
    "faq": ("faq", "faqs"),
}

# Reverse index: every accepted spelling -> its canonical type.
_TYPE_LOOKUP: Dict[str, str] = {
    alias: canonical
    for canonical, aliases in DOC_TYPE_ALIASES.items()
    for alias in aliases
}


def canonical_doc_type(value: Optional[str]) -> str:
    """Normalise a frontmatter ``type:`` or a filter request to canonical form.

    Unknown or absent types are returned lowercased and unchanged, so an
    unfamiliar type still compares by exact match (and, if nothing matches,
    retrieve() will warn loudly rather than silently returning nothing).
    """
    if not value:
        return ""
    key = str(value).strip().lower()
    return _TYPE_LOOKUP.get(key, key)


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
CHUNK_FILTER_VERSION = "2"

# ``[TODO ...]`` and friends. Tolerates the real spellings seen in wiki/:
# ``[TODO: ask Mikel]``, ``[TODO]``, ``[TODO — fill in]``.
_PLACEHOLDER_RE = re.compile(r"\[\s*TODO\b[^\]]*\]", re.IGNORECASE)

# Markdown emphasis and list/separator punctuation that can be orphaned once a
# marker is removed, e.g. ``- **[TODO: x]:** text`` -> ``- :** text``. Underscores
# and hashes are excluded from the class used on body text: they appear inside
# real identifiers (``snake_case_name``) far more often than as noise, and
# rewriting them would corrupt the indexed content.
_MD_NOISE_RE = re.compile(r"[*`]+")
_SEPARATORS = " \t\u2014\u2013-:,;.!?\u00bb\u00ab"
_HAS_WORD_RE = re.compile(r"\w", re.UNICODE)
# A heading line: leading #'s define the section boundary the chunker splits on,
# so a placeholder inside a heading must not cost them.
_HEADING_RE = re.compile(r"^#{1,6}\s")


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
            cleaned = " ".join(cleaned).strip(_SEPARATORS).strip()

        if _HAS_WORD_RE.search(cleaned):
            kept_lines.append(cleaned)

    return "\n".join(kept_lines), removed


# Query keywords -> canonical document type. Used to pre-filter chunks before
# similarity search when a query clearly maps to a single type.
# Keys are CANONICAL types (see DOC_TYPE_ALIASES), so every filter this table
# can emit is reachable from the wiki corpus.
QUERY_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "skills": ["tests", "testing", "test", "pytest", "tdd", "skills", "lenguajes", "frameworks"],
    "experience": ["experiencia", "mercadona", "encargado", "gerente", "retail"],
    "project": ["proyecto", "proyectos", "project", "projects", "portfolio"],
    "story": ["historia", "anécdota", "story", "situación"],
    "opinion": ["opinión", "opinion", "piensas", "crees"],
    "decision": ["decisión", "decision", "dejaste", "dejar"],
    "faq": ["presentación", "presentacion", "fortalezas", "debilidades", "área preferida"],
    "profile": ["sobre ti", "quién eres", "quien eres", "presentate", "preséntate"],
}


def detect_doc_type(query: str) -> Optional[str]:
    """Return the document type a query clearly maps to, or None if ambiguous.

    A query maps to a type when it matches keywords for exactly one type.
    Zero matches (no signal) or multiple matches (conflicting signals) return
    None so cosine similarity acts as the fallback.
    """
    query_lower = query.lower()
    matched = [
        doc_type
        for doc_type, keywords in QUERY_TYPE_KEYWORDS.items()
        if any(re.search(rf"\b{re.escape(kw)}\b", query_lower) for kw in keywords)
    ]
    return matched[0] if len(matched) == 1 else None


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
    embedding: Optional[np.ndarray] = field(default=None, repr=False)


class RAGPipeline:
    """In-memory RAG pipeline with cosine similarity retrieval."""

    def __init__(self, chunk_size: int = 400, chunk_overlap: int = 50, threshold: float = 0.3,
                 cache_dir: Optional[Path] = None, embedding_model: str = "all-MiniLM-L6-v2"):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
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
        """Load the embedding model. Falls back to TF-IDF if unavailable."""
        if self._initialized:
            return
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer model...")
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Sentence-transformer model loaded successfully")
        except Exception as e:
            logger.warning("Sentence-transformers unavailable (%s), falling back to TF-IDF", e)
            self._use_tfidf = True
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._tfidf_vectorizer = TfidfVectorizer(max_features=384)
        self._initialized = True

    @property
    def embedder(self):
        """Active sentence-transformer embedder, or None when unusable.

        Returns None before initialization and in TF-IDF fallback mode:
        TF-IDF vectors are unstable across restarts, so the semantic answer
        cache must never store or serve them (design D8).
        """
        if not self._initialized or self._use_tfidf:
            return None
        return self._embedder

    # ── Embedding cache helpers ──────────────────────────────────────────

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
        - ``embeddings.json`` — validation metadata (model, hash, counts).
        """
        if not self._cache_dir:
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
            # Tags are variable-length; store as JSON strings
            tags_json = np.array([json.dumps(c.tags) for c in chunks], dtype=object)
            embeddings = np.stack([c.embedding for c in chunks]) if chunks else np.empty((0, 0), dtype=np.float32)

            npz_path = self._cache_dir / "embeddings.npz"
            np.savez_compressed(
                npz_path,
                ids=ids, contents=contents, sources=sources,
                sections=sections, types=types, summaries=summaries,
                tags_json=tags_json, embeddings=embeddings,
            )

            # Write metadata
            metadata = {
                "model": self._embedding_model,
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
        """Try to load cached embeddings. Returns None if cache is invalid or missing."""
        if not self._cache_dir or not self._metadata_path:
            return None

        npz_path = self._cache_dir / "embeddings.npz"
        if not npz_path.exists() or not self._metadata_path.exists():
            return None

        try:
            # 1. Read and validate metadata
            with open(self._metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            if meta.get("model") != self._embedding_model:
                logger.info(
                    "Cache model mismatch (cache=%s, current=%s) — recomputing",
                    meta.get("model"), self._embedding_model,
                )
                return None

            # 1b. Validate the chunking/filtering semantics. The document hash
            # below covers the RAW wiki text, which placeholder filtering does
            # not touch, so a cache written before this filter existed would
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

        # Split by headings first
        sections = re.split(r'\n(?=#{1,3}\s)', content)

        chunk_id = 0
        for section in sections:
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
                    ))
                    chunk_id += 1
                    start += self.chunk_size - self.chunk_overlap

        return chunks

    def _compute_embeddings(self) -> None:
        """Compute embeddings for all chunks."""
        texts = [c.content for c in self.chunks]

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

    def retrieve(self, query: str, top_k: int = 3, threshold: float | None = None,
                 doc_type: Optional[str] = None) -> List[Tuple[Chunk, float]]:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: Search query.
            top_k: Number of results to return.
            threshold: Minimum similarity score (overrides instance default).
            doc_type: If given, only chunks of this document type are
                considered (pre-filter before similarity search).

        Returns:
            List of (Chunk, score) tuples ordered by descending similarity.
        """
        if not self.chunks:
            return []

        if threshold is None:
            threshold = self.threshold

        candidates = self.chunks
        if doc_type:
            # Normalise both sides through canonical_doc_type so wiki docs using
            # either singular or plural frontmatter (story/stories, project/
            # projects, ...) are all reachable, for EVERY type — not just
            # project, which was the only one previously special-cased.
            wanted = canonical_doc_type(doc_type)
            candidates = [
                c for c in self.chunks if canonical_doc_type(c.type) == wanted
            ]
            if not candidates:
                # Documented fallback: RETRIEVE UNFILTERED rather than return [].
                #
                # A filter matching nothing means the very next step hands the
                # LLM zero context, and an interview answer with no grounding
                # from the candidate's own profile is worse than a slightly less
                # precise one. Retrieval semantics are therefore NOT changed
                # silently: the substitution is logged as a warning naming the
                # requested type and the available ones, so the owner sees the
                # mapping gap instead of the hole quietly disappearing.
                available = sorted({c.type for c in self.chunks if c.type})
                logger.warning(
                    "doc_type filter %r matched no chunks (canonical %r); "
                    "available types: %s. Falling back to unfiltered retrieval "
                    "so the answer stays grounded in the candidate's profile.",
                    doc_type,
                    wanted,
                    ", ".join(available) if available else "<none>",
                )
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
        return scores[:top_k]

    def get_context_string(self, query: str, top_k: int = 3) -> str:
        """Retrieve and format context for the LLM.

        Args:
            query: User's question.
            top_k: Number of chunks to retrieve.

        Returns:
            Formatted context string, or empty string if no relevant chunks.
        """
        results = self.retrieve(query, top_k=top_k, doc_type=detect_doc_type(query))
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

    def get_chunks_with_scores(self, query: str, top_k: int = 3) -> List[dict]:
        """Retrieve chunks with similarity scores as serializable dicts.

        Args:
            query: User's question.
            top_k: Number of chunks to return.

        Returns:
            List of dicts: [{"text": "...", "score": 0.82, "source": "cv.md"}, ...]
        """
        results = self.retrieve(query, top_k=top_k, doc_type=detect_doc_type(query))
        return [
            {"text": chunk.content, "score": round(score, 3), "source": chunk.source}
            for chunk, score in results
        ]
