"""Tests for storage layer."""

from __future__ import annotations

import pytest
from rlm_mcp.storage import BlobStore, Database
from rlm_mcp.models import Session, SessionConfig


class TestBlobStore:
    """Tests for content-addressed blob store."""
    
    def test_put_and_get(self, blob_store: BlobStore):
        """Test storing and retrieving content."""
        content = "Hello, World!"
        content_hash = blob_store.put(content)
        
        assert content_hash is not None
        assert len(content_hash) == 64  # SHA256 hex
        
        retrieved = blob_store.get(content_hash)
        assert retrieved == content
    
    def test_content_addressing(self, blob_store: BlobStore):
        """Test that same content produces same hash."""
        content = "Test content"
        
        hash1 = blob_store.put(content)
        hash2 = blob_store.put(content)
        
        assert hash1 == hash2
    
    def test_different_content_different_hash(self, blob_store: BlobStore):
        """Test that different content produces different hashes."""
        hash1 = blob_store.put("Content A")
        hash2 = blob_store.put("Content B")
        
        assert hash1 != hash2
    
    def test_get_nonexistent(self, blob_store: BlobStore):
        """Test getting non-existent content returns None."""
        result = blob_store.get("0" * 64)
        assert result is None
    
    def test_exists(self, blob_store: BlobStore):
        """Test exists check."""
        content_hash = blob_store.put("Test")
        
        assert blob_store.exists(content_hash)
        assert not blob_store.exists("0" * 64)
    
    def test_get_slice(self, blob_store: BlobStore):
        """Test getting content slices."""
        content = "Hello, World!"
        content_hash = blob_store.put(content)
        
        # Slice from start
        assert blob_store.get_slice(content_hash, 0, 5) == "Hello"
        
        # Slice from middle
        assert blob_store.get_slice(content_hash, 7, 12) == "World"
        
        # Slice to end
        assert blob_store.get_slice(content_hash, 7, -1) == "World!"
    
    def test_hash_content_without_storing(self, blob_store: BlobStore):
        """Test computing hash without storing."""
        content = "Test content"
        
        hash_only = blob_store.hash_content(content)
        assert not blob_store.exists(hash_only)
        
        stored_hash = blob_store.put(content)
        assert hash_only == stored_hash


class TestDatabase:
    """Tests for SQLite database operations."""
    
    @pytest.mark.asyncio
    async def test_session_crud(self, database: Database):
        """Test session create, read, update."""
        # Create
        session = Session(name="Test Session")
        await database.create_session(session)
        
        # Read
        retrieved = await database.get_session(session.id)
        assert retrieved is not None
        assert retrieved.name == "Test Session"
        assert retrieved.status.value == "active"
        
        # Update
        session.name = "Updated Session"
        await database.update_session(session)
        
        retrieved = await database.get_session(session.id)
        assert retrieved.name == "Updated Session"
    
    @pytest.mark.asyncio
    async def test_session_not_found(self, database: Database):
        """Test getting non-existent session."""
        result = await database.get_session("nonexistent")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_increment_tool_calls(self, database: Database):
        """Test tool call counter."""
        session = Session(name="Test")
        await database.create_session(session)
        
        count = await database.increment_tool_calls(session.id)
        assert count == 1
        
        count = await database.increment_tool_calls(session.id)
        assert count == 2
        
        # Verify persisted
        retrieved = await database.get_session(session.id)
        assert retrieved.tool_calls_used == 2

    @pytest.mark.asyncio
    async def test_span_chunk_index_persistence(self, database: Database):
        """Test that chunk_index field is persisted and loaded correctly."""
        from rlm_mcp.models import Session, Document, DocumentSource, Span, ChunkStrategy

        # Create session and document
        session = Session(name="Test")
        await database.create_session(session)

        doc = Document(
            session_id=session.id,
            content_hash="abc123",
            source=DocumentSource(type="inline", content="test"),
            length_chars=4,
            length_tokens_est=1,
        )
        await database.create_document(doc)

        # Create span with chunk_index=5
        span = Span(
            document_id=doc.id,
            start_offset=0,
            end_offset=4,
            content_hash="abc123",
            strategy=ChunkStrategy(type="fixed", chunk_size=4),
            chunk_index=5,
        )
        await database.create_span(span)

        # Reload from database
        loaded = await database.get_span(span.id)
        assert loaded is not None
        assert loaded.chunk_index == 5

        # Test with None chunk_index
        span2 = Span(
            document_id=doc.id,
            start_offset=0,
            end_offset=4,
            content_hash="abc123",
            strategy=ChunkStrategy(type="fixed", chunk_size=4),
            chunk_index=None,
        )
        await database.create_span(span2)

        loaded2 = await database.get_span(span2.id)
        assert loaded2 is not None
        assert loaded2.chunk_index is None
