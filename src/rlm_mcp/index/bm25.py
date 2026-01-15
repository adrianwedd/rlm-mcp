"""BM25 index implementation.

Lazy-built and cached per session.
Index lifecycle:
1. On docs.load: Documents stored, no index built
2. On first search.query with method=bm25: Index built synchronously, cached
3. On subsequent BM25 searches: Cached index reused
4. On session.close: Index metadata persisted (for potential future reload)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    from rlm_mcp.models import Document
    from rlm_mcp.storage.blobs import BlobStore


@dataclass
class ScoredDocument:
    """Document with BM25 score."""
    doc_id: str
    score: float
    content: str


class BM25Index:
    """Picklable BM25 index for persistence.

    Wraps BM25Okapi with document mapping for persistence.
    """

    def __init__(self):
        self.bm25: BM25Okapi | None = None
        self.doc_map: list[tuple[str, str]] = []  # (doc_id, content)

    def add_document(self, doc_id: str, content: str) -> None:
        """Add document to index (call before building)."""
        self.doc_map.append((doc_id, content))

    def build(self) -> None:
        """Build the BM25 index from added documents."""
        if not self.doc_map:
            return

        corpus = []
        for doc_id, content in self.doc_map:
            tokens = self._tokenize(content)
            corpus.append(tokens)

        if corpus:
            self.bm25 = BM25Okapi(corpus)

    def search(self, query: str, limit: int = 10) -> list[ScoredDocument]:
        """Search the index.

        Args:
            query: Search query
            limit: Maximum results to return

        Returns:
            List of scored documents, sorted by score descending
        """
        if self.bm25 is None:
            return []

        query_tokens = self._tokenize(query)
        scores = self.bm25.get_scores(query_tokens)

        # Pair scores with doc info
        scored = [
            (idx, score, self.doc_map[idx])
            for idx, score in enumerate(scores)
        ]

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Return top results
        results = []
        for idx, score, (doc_id, content) in scored[:limit]:
            results.append(ScoredDocument(
                doc_id=doc_id,
                score=float(score),
                content=content,
            ))

        return results

    def get_doc_content(self, doc_id: str) -> str | None:
        """Get document content by ID from cached map."""
        for did, content in self.doc_map:
            if did == doc_id:
                return content
        return None

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text for BM25.

        Simple tokenization: lowercase, split on non-alphanumeric and underscores.
        """
        # First split on non-alphanumeric
        tokens = re.findall(r'\w+', text.lower())
        # Then split each token on underscores for better matching
        result = []
        for token in tokens:
            result.extend(token.split('_'))
        # Filter out empty strings
        return [t for t in result if t]


class SessionIndex:
    """Lazy-loaded BM25 index for a session.
    
    The index is built on first query and cached in memory.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._bm25: BM25Okapi | None = None
        self._doc_map: list[tuple[str, str]] = []  # (doc_id, content)
        self._built = False
    
    @property
    def is_built(self) -> bool:
        """Check if index has been built."""
        return self._built
    
    def build(self, documents: list["Document"], blobs: "BlobStore") -> None:
        """Build the BM25 index from documents.
        
        Args:
            documents: Documents to index
            blobs: Blob store for content retrieval
        """
        if self._built:
            return
        
        corpus = []
        self._doc_map = []
        
        for doc in documents:
            content = blobs.get(doc.content_hash)
            if content:
                tokens = self._tokenize(content)
                corpus.append(tokens)
                self._doc_map.append((doc.id, content))
        
        if corpus:
            self._bm25 = BM25Okapi(corpus)
        
        self._built = True
    
    def search(self, query: str, limit: int = 10) -> list[ScoredDocument]:
        """Search the index.
        
        Args:
            query: Search query
            limit: Maximum results to return
            
        Returns:
            List of scored documents, sorted by score descending
            
        Raises:
            RuntimeError: If index not built
        """
        if not self._built:
            raise RuntimeError("Index not built. Call build() first.")
        
        if self._bm25 is None:
            return []
        
        query_tokens = self._tokenize(query)
        scores = self._bm25.get_scores(query_tokens)
        
        # Pair scores with doc info
        # Note: Don't filter score > 0 as BM25 can produce negative scores
        scored = [
            (idx, score, self._doc_map[idx])
            for idx, score in enumerate(scores)
        ]
        
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Return top results
        results = []
        for idx, score, (doc_id, content) in scored[:limit]:
            results.append(ScoredDocument(
                doc_id=doc_id,
                score=float(score),
                content=content,
            ))
        
        return results
    
    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text for BM25.

        Simple tokenization: lowercase, split on non-alphanumeric and underscores.
        """
        # First split on non-alphanumeric
        tokens = re.findall(r'\w+', text.lower())
        # Then split each token on underscores for better matching
        result = []
        for token in tokens:
            result.extend(token.split('_'))
        # Filter out empty strings
        return [t for t in result if t]
    
    def get_doc_content(self, doc_id: str) -> str | None:
        """Get document content by ID from cached map."""
        for did, content in self._doc_map:
            if did == doc_id:
                return content
        return None
