"""RAG pipeline — document chunking, embedding, and retrieval."""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of text with metadata for RAG retrieval."""
    id: str
    content: str
    source: str
    section: str = ""
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
        """Split a document into chunks, respecting heading boundaries."""
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

    def retrieve(self, query: str, top_k: int = 3, threshold: float | None = None) -> List[Tuple[Chunk, float]]:
        """Retrieve the most relevant chunks for a query.

        Args:
            query: Search query.
            top_k: Number of results to return.
            threshold: Minimum similarity score (overrides instance default).

        Returns:
            List of (Chunk, score) tuples ordered by descending similarity.
        """
        if not self.chunks:
            return []

        if threshold is None:
            threshold = self.threshold

        # Embed the query
        if self._use_tfidf:
            query_vec = self._tfidf_vectorizer.transform([query]).toarray().astype(np.float32)[0]
        else:
            query_vec = self._embedder.encode([query])[0].astype(np.float32)

        # Normalize query vector
        norm = np.linalg.norm(query_vec)
        if norm > 0:
            query_vec = query_vec / norm

        # Compute cosine similarities
        scores = []
        for chunk in self.chunks:
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
        results = self.retrieve(query, top_k=top_k)
        if not results:
            return ""

        parts = []
        for chunk, score in results:
            parts.append(f"[Source: {chunk.source}] {chunk.content}")

        return "\n\n".join(parts)
