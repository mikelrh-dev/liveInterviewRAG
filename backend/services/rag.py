"""RAG pipeline — document chunking, embedding, and retrieval."""

import logging
import re
import time
from dataclasses import dataclass, field
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

# Query keywords -> document type. Used to pre-filter chunks before similarity
# search when a query clearly maps to a single type.
QUERY_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "skills": ["tests", "testing", "test", "pytest", "tdd", "skills", "lenguajes", "frameworks"],
    "experience": ["experiencia", "mercadona", "encargado", "gerente", "retail"],
    "project": ["proyecto", "proyectos", "project", "projects", "portfolio"],
    "stories": ["historia", "anécdota", "story", "situación"],
    "opinions": ["opinión", "opinion", "piensas", "crees"],
    "decisions": ["decisión", "decision", "dejaste", "dejar"],
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

    def __init__(self, chunk_size: int = 400, chunk_overlap: int = 50, threshold: float = 0.3):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.threshold = threshold
        self.chunks: List[Chunk] = []
        self._embedder = None
        self._use_tfidf = False
        self._tfidf_vectorizer = None
        self._initialized = False

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

    def ingest_documents(self, documents: dict[str, str]) -> int:
        """Chunk and embed documents.

        Args:
            documents: Dict of filename -> content.

        Returns:
            Total number of chunks created.
        """
        if not self._initialized:
            self.initialize()

        start_time = time.time()
        self.chunks = []

        for filename, content in documents.items():
            doc_chunks = self._chunk_document(filename, content)
            self.chunks.extend(doc_chunks)

        logger.info("Created %d chunks from %d documents", len(self.chunks), len(documents))

        if self.chunks:
            self._compute_embeddings()

        elapsed = time.time() - start_time
        logger.info("Ingestion completed in %.2fs", elapsed)
        return len(self.chunks)

    def _chunk_document(self, filename: str, content: str) -> List[Chunk]:
        """Split a document into chunks, respecting heading boundaries.

        YAML frontmatter (if present) is parsed for metadata and stripped
        from the content before chunking.
        """
        metadata, content = parse_frontmatter(content)
        doc_type = str(metadata.get("type", ""))
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        summary = str(metadata.get("summary_1line", ""))

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
            # Normalize type aliases: "projects" ↔ "project" so wiki docs using
            # either singular or plural in frontmatter are found consistently.
            _norm = "project" if doc_type in ("project", "projects") else doc_type
            candidates = [
                c for c in self.chunks
                if c.type == _norm or c.type == doc_type
                or (doc_type in ("project", "projects") and c.type in ("project", "projects"))
            ]
            if not candidates:
                return []

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
