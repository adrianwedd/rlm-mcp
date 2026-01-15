"""Content-addressed blob store for RLM-MCP.

Layout: {blob_dir}/{hash[:2]}/{hash}

This allows same content to be deduplicated across sessions while
maintaining session-scoped doc_ids for stable references.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class BlobStore:
    """Content-addressed blob storage."""

    def __init__(self, blob_dir: Path):
        self.blob_dir = blob_dir
        self.blob_dir.mkdir(parents=True, exist_ok=True)

    def put(self, content: str) -> str:
        """Store content and return its SHA256 hash.

        Args:
            content: String content to store

        Returns:
            SHA256 hash (hex) of the content
        """
        content_bytes = content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Create directory structure: {hash[:2]}/{hash}
        blob_subdir = self.blob_dir / content_hash[:2]
        blob_subdir.mkdir(exist_ok=True)

        blob_path = blob_subdir / content_hash

        # Only write if doesn't exist (content-addressed = idempotent)
        if not blob_path.exists():
            blob_path.write_bytes(content_bytes)

        return content_hash

    def get(self, content_hash: str) -> str | None:
        """Retrieve content by hash.

        Args:
            content_hash: SHA256 hash (hex) of content

        Returns:
            Content string, or None if not found
        """
        blob_path = self.blob_dir / content_hash[:2] / content_hash

        if not blob_path.exists():
            return None

        return blob_path.read_text(encoding="utf-8")

    def exists(self, content_hash: str) -> bool:
        """Check if content exists by hash."""
        blob_path = self.blob_dir / content_hash[:2] / content_hash
        return blob_path.exists()

    def delete(self, content_hash: str) -> bool:
        """Delete content by hash.

        Note: In practice, we rarely delete blobs since they may be
        referenced by multiple sessions. This is mainly for cleanup.

        Returns:
            True if deleted, False if didn't exist
        """
        blob_path = self.blob_dir / content_hash[:2] / content_hash

        if blob_path.exists():
            blob_path.unlink()
            return True
        return False

    def hash_content(self, content: str) -> str:
        """Compute hash without storing.

        Useful for checking if content already exists or for
        computing span content hashes.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get_slice(self, content_hash: str, start: int, end: int) -> str | None:
        """Get a slice of content by hash.

        Args:
            content_hash: SHA256 hash of full content
            start: Start offset (inclusive)
            end: End offset (exclusive), -1 for end of content

        Returns:
            Content slice, or None if not found
        """
        content = self.get(content_hash)
        if content is None:
            return None

        if end == -1:
            end = len(content)

        return content[start:end]
