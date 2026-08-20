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
