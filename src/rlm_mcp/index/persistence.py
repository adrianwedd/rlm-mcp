"""Index persistence with atomic writes and fingerprinting.

Implements Patch #1: Atomic writes + fingerprinting for corruption prevention.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import shutil
from pathlib import Path
from typing import Any

from rlm_mcp.logging_config import StructuredLogger

logger = StructuredLogger(__name__)


class IndexMetadata:
    """Metadata for persisted index.

    Used to detect staleness (doc changes, tokenizer changes).
    """

    def __init__(
        self,
        doc_count: int,
        doc_fingerprint: str,
        tokenizer_name: str,
    ):
        self.doc_count = doc_count
        self.doc_fingerprint = doc_fingerprint
        self.tokenizer_name = tokenizer_name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IndexMetadata):
            return False
        return (
            self.doc_count == other.doc_count
            and self.doc_fingerprint == other.doc_fingerprint
            and self.tokenizer_name == other.tokenizer_name
        )

    def __repr__(self) -> str:
        return (
            f"IndexMetadata(doc_count={self.doc_count}, "
            f"doc_fingerprint={self.doc_fingerprint[:8]}..., "
            f"tokenizer_name={self.tokenizer_name})"
        )


class IndexPersistence:
    """Manages persistent storage of BM25 indexes.

    Features:
    - Atomic writes (temp file + os.replace()) to prevent corruption
    - Fingerprinting (doc count + content hash) to detect staleness
    - Tokenizer tracking to invalidate on algorithm changes
    - Graceful corruption recovery (returns None, rebuilds index)

    Directory structure:
        {index_dir}/
            {session_id}/
                index.pkl       # Pickled BM25Index
                metadata.pkl    # Pickled IndexMetadata
    """

    def __init__(self, index_dir: Path):
        """Initialize persistence layer.

        Args:
            index_dir: Root directory for persisted indexes
        """
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def _get_session_dir(self, session_id: str) -> Path:
        """Get directory for session's persisted index."""
        return self.index_dir / session_id

    def _get_index_path(self, session_id: str) -> Path:
        """Get path to persisted index file."""
        return self._get_session_dir(session_id) / "index.pkl"

    def _get_metadata_path(self, session_id: str) -> Path:
        """Get path to metadata file."""
        return self._get_session_dir(session_id) / "metadata.pkl"

    def save_index(
        self,
        session_id: str,
        index: Any,
        metadata: IndexMetadata,
    ) -> None:
        """Save index to disk with atomic write.

        Uses temp file + os.replace() for atomicity (Patch #1).
        Prevents corruption from crashes during write.

        Args:
            session_id: Session identifier
            index: BM25Index instance to persist
            metadata: Index metadata (doc count, fingerprint, tokenizer)
        """
        session_dir = self._get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        index_path = self._get_index_path(session_id)
        metadata_path = self._get_metadata_path(session_id)

        # Write to temp files first (atomic write pattern)
        index_tmp = index_path.with_suffix(".pkl.tmp")
        metadata_tmp = metadata_path.with_suffix(".pkl.tmp")

        try:
            # Write index
            with open(index_tmp, "wb") as f:
                pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)

            # Write metadata
            with open(metadata_tmp, "wb") as f:
                pickle.dump(metadata, f, protocol=pickle.HIGHEST_PROTOCOL)

            # Atomic rename (os.replace is atomic on all platforms)
            os.replace(index_tmp, index_path)
            os.replace(metadata_tmp, metadata_path)

            logger.info(
                f"Persisted index for session {session_id}",
                session_id=session_id,
                doc_count=metadata.doc_count,
                tokenizer=metadata.tokenizer_name,
            )

        except Exception as e:
            # Clean up temp files if write failed
            index_tmp.unlink(missing_ok=True)
            metadata_tmp.unlink(missing_ok=True)
            logger.error(
                f"Failed to persist index for session {session_id}: {e}",
                session_id=session_id,
                error=str(e),
            )
            raise

    def load_index(
        self, session_id: str
    ) -> tuple[Any, IndexMetadata] | tuple[None, None]:
        """Load index from disk with corruption handling.

        Returns None if index doesn't exist or is corrupted.
        Logs corruption for debugging.

        Args:
            session_id: Session identifier

        Returns:
            (index, metadata) if successful, (None, None) otherwise
        """
        index_path = self._get_index_path(session_id)
        metadata_path = self._get_metadata_path(session_id)

        if not index_path.exists() or not metadata_path.exists():
            logger.debug(
                f"No persisted index found for session {session_id}",
                session_id=session_id,
            )
            return None, None

        try:
            # Load metadata first (smaller, faster to validate)
            with open(metadata_path, "rb") as f:
                metadata = pickle.load(f)

            # Load index
            with open(index_path, "rb") as f:
                index = pickle.load(f)

            logger.info(
                f"Loaded persisted index for session {session_id}",
                session_id=session_id,
                doc_count=metadata.doc_count,
                tokenizer=metadata.tokenizer_name,
            )

            return index, metadata

        except (pickle.UnpicklingError, EOFError, OSError) as e:
            # Corruption detected - log and return None to trigger rebuild
            logger.warning(
                f"Corrupted index detected for session {session_id}: {e}",
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            # Invalidate corrupted index
            self.invalidate_index(session_id)
            return None, None

    def is_index_stale(
        self,
        metadata: IndexMetadata,
        current_doc_count: int,
        current_doc_fingerprint: str,
        current_tokenizer_name: str,
    ) -> bool:
        """Check if persisted index is stale.

        Index is stale if:
        - Document count changed
        - Document fingerprint changed (content modified)
        - Tokenizer name changed (algorithm updated)

        Args:
            metadata: Persisted index metadata
            current_doc_count: Current document count
            current_doc_fingerprint: Current document fingerprint
            current_tokenizer_name: Current tokenizer name

        Returns:
            True if index is stale and should be rebuilt
        """
        if metadata.doc_count != current_doc_count:
            logger.debug(
                "Index stale: doc count changed",
                old_count=metadata.doc_count,
                new_count=current_doc_count,
            )
            return True

        if metadata.doc_fingerprint != current_doc_fingerprint:
            logger.debug(
                "Index stale: doc fingerprint changed",
                old_fingerprint=metadata.doc_fingerprint[:8],
                new_fingerprint=current_doc_fingerprint[:8],
            )
            return True

        if metadata.tokenizer_name != current_tokenizer_name:
            logger.debug(
                "Index stale: tokenizer changed",
                old_tokenizer=metadata.tokenizer_name,
                new_tokenizer=current_tokenizer_name,
            )
            return True

        return False

    def compute_doc_fingerprint(self, documents: list[dict[str, Any]]) -> str:
        """Compute fingerprint for document set.

        Fingerprint = SHA256(sorted content_hashes concatenated)

        Detects:
        - Document additions/removals (count changes)
        - Document modifications (content hash changes)
        - Document reordering (sort by ID for stability)

        Args:
            documents: List of document dicts with 'id' and 'content_hash' keys

        Returns:
            SHA256 hex digest of concatenated content hashes
        """
        # Sort by ID for stable ordering
        sorted_docs = sorted(documents, key=lambda d: d["id"])

        # Concatenate content hashes
        fingerprint_input = "".join(d["content_hash"] for d in sorted_docs)

        # Hash concatenated string
        return hashlib.sha256(fingerprint_input.encode()).hexdigest()

    def invalidate_index(self, session_id: str) -> None:
        """Invalidate (delete) persisted index for session.

        Called when:
        - Documents are loaded (content changed)
        - Corruption detected
        - Manual invalidation requested

        Args:
            session_id: Session identifier
        """
        session_dir = self._get_session_dir(session_id)

        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
                logger.info(
                    f"Invalidated persisted index for session {session_id}",
                    session_id=session_id,
                )
            except OSError as e:
                logger.warning(
                    f"Failed to invalidate index for session {session_id}: {e}",
                    session_id=session_id,
                    error=str(e),
                )
        else:
            logger.debug(
                f"No persisted index to invalidate for session {session_id}",
                session_id=session_id,
            )

    def get_tokenizer_name(self) -> str:
        """Get tokenizer name for fingerprinting.

        Returns simple identifier for current tokenizer implementation.
        Change this if tokenizer algorithm changes.

        Returns:
            Tokenizer identifier (e.g., "simple-v1")
        """
        return "simple-v1"
