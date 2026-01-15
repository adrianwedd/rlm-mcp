"""Tests for index persistence (atomic writes, fingerprinting, corruption recovery)."""

import os
import pickle
import time
from pathlib import Path

import pytest

from rlm_mcp.models import Session, SessionConfig
from rlm_mcp.server import create_server
from rlm_mcp.tools.session import _session_create, _session_close
from rlm_mcp.tools.docs import _docs_load
from rlm_mcp.tools.search import _search_query


@pytest.fixture
async def server_with_docs(tmp_path):
    """Create server with session and documents for persistence testing."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(
        data_dir=tmp_path,
        default_max_tool_calls=1000,
    )

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="persistence-test")
        session_id = session["session_id"]

        # Load documents
        docs = [
            {"type": "inline", "content": "def calculate_sum(a, b):\n    return a + b\n" * 100},
            {"type": "inline", "content": "class UserManager:\n    def create_user(self):\n        pass\n" * 100},
            {"type": "inline", "content": "async def fetch_data():\n    await asyncio.sleep(1)\n" * 100},
        ]

        await _docs_load(server, session_id=session_id, sources=docs)

        yield server, session_id


@pytest.mark.asyncio
async def test_index_persists_on_close(server_with_docs):
    """Test that index is persisted to disk when session closes."""
    server, session_id = server_with_docs

    # Build index by searching
    result = await _search_query(
        server, session_id=session_id, query="calculate sum", method="bm25"
    )
    assert len(result["matches"]) > 0

    # Verify index in memory
    assert session_id in server._index_cache

    # Close session (should persist index)
    await _session_close(server, session_id=session_id)

    # Verify index persisted to disk
    index_path = server.index_persistence._get_index_path(session_id)
    metadata_path = server.index_persistence._get_metadata_path(session_id)

    assert index_path.exists(), "Index file should exist after close"
    assert metadata_path.exists(), "Metadata file should exist after close"

    # Verify index removed from memory
    assert session_id not in server._index_cache


@pytest.mark.asyncio
async def test_index_loads_on_restart(tmp_path):
    """Test that persisted index loads on server restart."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    # First server: create session, build index, close
    async with create_server(config) as server1:
        session = await _session_create(server1, name="restart-test")
        session_id = session["session_id"]

        docs = [
            {"type": "inline", "content": "def hello():\n    return 'world'\n" * 50}
        ]
        await _docs_load(server1, session_id=session_id, sources=docs)

        # Build index
        result1 = await _search_query(
            server1, session_id=session_id, query="hello", method="bm25"
        )
        assert len(result1["matches"]) > 0

        # Close session (persists index)
        await _session_close(server1, session_id=session_id)

    # Second server: load index from disk
    async with create_server(config) as server2:
        # Index should not be in memory yet
        assert session_id not in server2._index_cache

        # Search should load from disk
        start = time.time()
        result2 = await _search_query(
            server2, session_id=session_id, query="hello", method="bm25"
        )
        load_time = time.time() - start

        assert len(result2["matches"]) > 0
        assert session_id in server2._index_cache

        # Loading from disk should be fast (<500ms)
        assert load_time < 0.5, f"Index load took {load_time:.2f}s, expected <0.5s"


@pytest.mark.asyncio
async def test_index_invalidates_on_doc_load(server_with_docs):
    """Test that loading new docs invalidates both memory and disk indexes."""
    server, session_id = server_with_docs

    # Build index
    await _search_query(server, session_id=session_id, query="calculate", method="bm25")
    assert session_id in server._index_cache

    # Close session to persist
    await _session_close(server, session_id=session_id)

    index_path = server.index_persistence._get_index_path(session_id)
    assert index_path.exists()

    # Reopen session (simulate restart)
    from rlm_mcp.models import SessionStatus
    session = await server.db.get_session(session_id)
    session.status = SessionStatus.ACTIVE
    await server.db.update_session(session)

    # Load new document (should invalidate)
    new_docs = [{"type": "inline", "content": "def new_function():\n    pass\n" * 50}]
    await _docs_load(server, session_id=session_id, sources=new_docs)

    # Memory cache should be cleared
    assert session_id not in server._index_cache

    # Disk index should be deleted
    assert not index_path.exists(), "Persisted index should be deleted after doc load"


@pytest.mark.asyncio
async def test_atomic_write_prevents_corruption(server_with_docs):
    """Test that atomic writes prevent partial writes (no .tmp files left)."""
    server, session_id = server_with_docs

    # Build index
    await _search_query(server, session_id=session_id, query="calculate", method="bm25")

    # Close session (persists with atomic writes)
    await _session_close(server, session_id=session_id)

    session_dir = server.index_persistence._get_session_dir(session_id)

    # Check for leftover temp files (shouldn't exist with atomic writes)
    tmp_files = list(session_dir.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Found temp files: {tmp_files}. Atomic writes should clean up."

    # Verify final files exist
    index_path = server.index_persistence._get_index_path(session_id)
    metadata_path = server.index_persistence._get_metadata_path(session_id)

    assert index_path.exists()
    assert metadata_path.exists()


@pytest.mark.asyncio
async def test_tokenizer_change_invalidates_index(tmp_path):
    """Test that changing tokenizer name invalidates persisted index."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    # First server: build and persist index
    async with create_server(config) as server1:
        session = await _session_create(server1, name="tokenizer-test")
        session_id = session["session_id"]

        docs = [{"type": "inline", "content": "test content " * 100}]
        await _docs_load(server1, session_id=session_id, sources=docs)

        await _search_query(server1, session_id=session_id, query="test", method="bm25")
        await _session_close(server1, session_id=session_id)

    # Second server: mock tokenizer change
    async with create_server(config) as server2:
        # Modify get_tokenizer_name to return different version
        original_method = server2.index_persistence.get_tokenizer_name
        server2.index_persistence.get_tokenizer_name = lambda: "simple-v2"

        try:
            # Search should detect stale index and rebuild
            result = await _search_query(
                server2, session_id=session_id, query="test", method="bm25"
            )
            assert len(result["matches"]) > 0

            # Index should be in cache (rebuilt)
            assert session_id in server2._index_cache

        finally:
            # Restore original method
            server2.index_persistence.get_tokenizer_name = original_method


@pytest.mark.asyncio
async def test_doc_edit_invalidates_index(tmp_path):
    """Test that document changes (fingerprint) invalidate persisted index."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    session_id = None

    # First server: build and persist index
    async with create_server(config) as server1:
        session = await _session_create(server1, name="doc-edit-test")
        session_id = session["session_id"]

        docs = [{"type": "inline", "content": "original content " * 100}]
        await _docs_load(server1, session_id=session_id, sources=docs)

        await _search_query(server1, session_id=session_id, query="original", method="bm25")
        await _session_close(server1, session_id=session_id)

    # Second server: modify docs and check staleness
    async with create_server(config) as server2:
        # Reopen session
        from rlm_mcp.models import SessionStatus
        session = await server2.db.get_session(session_id)
        session.status = SessionStatus.ACTIVE
        await server2.db.update_session(session)

        # Load different content (changes fingerprint)
        new_docs = [{"type": "inline", "content": "modified content " * 100}]
        await _docs_load(server2, session_id=session_id, sources=new_docs)

        # Index should be invalidated
        assert session_id not in server2._index_cache

        index_path = server2.index_persistence._get_index_path(session_id)
        assert not index_path.exists()


@pytest.mark.asyncio
async def test_corrupted_index_rebuilds(server_with_docs):
    """Test that corrupted index is detected and gracefully rebuilt."""
    server, session_id = server_with_docs

    # Build and persist index
    await _search_query(server, session_id=session_id, query="calculate", method="bm25")
    await _session_close(server, session_id=session_id)

    # Corrupt the index file
    index_path = server.index_persistence._get_index_path(session_id)
    with open(index_path, "wb") as f:
        f.write(b"corrupted data that is not valid pickle")

    # Reopen session
    from rlm_mcp.models import SessionStatus
    session = await server.db.get_session(session_id)
    session.status = SessionStatus.ACTIVE
    await server.db.update_session(session)

    # Search should detect corruption, log warning, and rebuild
    result = await _search_query(
        server, session_id=session_id, query="calculate", method="bm25"
    )

    # Should successfully rebuild and search
    assert len(result["matches"]) > 0
    assert session_id in server._index_cache


@pytest.mark.asyncio
async def test_fingerprint_computation(tmp_path):
    """Test that fingerprint correctly detects document changes."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        persistence = server.index_persistence

        # Test 1: Same docs, same fingerprint
        docs1 = [
            {"id": "doc1", "content_hash": "hash1"},
            {"id": "doc2", "content_hash": "hash2"},
        ]
        fp1 = persistence.compute_doc_fingerprint(docs1)

        docs1_reordered = [
            {"id": "doc2", "content_hash": "hash2"},
            {"id": "doc1", "content_hash": "hash1"},
        ]
        fp1_reordered = persistence.compute_doc_fingerprint(docs1_reordered)

        # Should be same (sorted by ID)
        assert fp1 == fp1_reordered

        # Test 2: Different content, different fingerprint
        docs2 = [
            {"id": "doc1", "content_hash": "hash1_modified"},
            {"id": "doc2", "content_hash": "hash2"},
        ]
        fp2 = persistence.compute_doc_fingerprint(docs2)

        assert fp1 != fp2

        # Test 3: Different doc count, different fingerprint
        docs3 = [
            {"id": "doc1", "content_hash": "hash1"},
            {"id": "doc2", "content_hash": "hash2"},
            {"id": "doc3", "content_hash": "hash3"},
        ]
        fp3 = persistence.compute_doc_fingerprint(docs3)

        assert fp1 != fp3


@pytest.mark.asyncio
async def test_index_load_performance(tmp_path):
    """Test that loading persisted index is fast (<100ms for typical index)."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    session_id = None

    # Build and persist index
    async with create_server(config) as server1:
        session = await _session_create(server1, name="perf-test")
        session_id = session["session_id"]

        # Medium-sized corpus (100K chars)
        docs = [
            {"type": "inline", "content": f"Document {i} with some searchable content.\n" * 200}
            for i in range(5)
        ]
        await _docs_load(server1, session_id=session_id, sources=docs)

        # Build index
        await _search_query(server1, session_id=session_id, query="searchable", method="bm25")
        await _session_close(server1, session_id=session_id)

    # Load from disk and measure time
    async with create_server(config) as server2:
        start = time.time()

        # This should load from disk
        index = await server2.get_or_build_index(session_id)

        load_time = time.time() - start

        # Loading should be fast
        assert load_time < 0.1, f"Index load took {load_time:.3f}s, expected <0.1s"
        assert index is not None


@pytest.mark.asyncio
async def test_concurrent_persistence_operations(tmp_path):
    """Test that persistence operations are safe with session locks."""
    import asyncio
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        session = await _session_create(server, name="concurrent-test")
        session_id = session["session_id"]

        docs = [{"type": "inline", "content": "concurrent test content " * 100}]
        await _docs_load(server, session_id=session_id, sources=docs)

        # Concurrent searches should all use same index (only build once)
        async def search():
            return await _search_query(
                server, session_id=session_id, query="concurrent", method="bm25"
            )

        results = await asyncio.gather(*[search() for _ in range(10)])

        # All should succeed
        assert len(results) == 10
        for result in results:
            assert len(result["matches"]) > 0

        # Index should be in cache
        assert session_id in server._index_cache
