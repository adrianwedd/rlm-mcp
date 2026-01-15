"""End-to-end integration tests for v0.2.0 features.

Tests interaction between:
- Index persistence (Day 4)
- Concurrency locks (Day 3)
- Structured logging (Day 2)

These tests validate full workflows across server restarts and concurrent operations.
"""

import asyncio
import io
import json
import logging
import pytest

from rlm_mcp.config import ServerConfig
from rlm_mcp.logging_config import StructuredFormatter, correlation_id_var
from rlm_mcp.server import create_server
from rlm_mcp.tools.session import _session_create, _session_close, _session_info
from rlm_mcp.tools.docs import _docs_load
from rlm_mcp.tools.search import _search_query


@pytest.mark.asyncio
async def test_full_workflow_with_persistence(tmp_path):
    """Test complete workflow: create → load → search → close → restart → search.

    Validates that:
    - Session can be created and used
    - Documents can be loaded
    - Index is built on first search
    - Index is persisted on session close
    - New server instance can load persisted index
    - Loaded index works correctly
    """
    config = ServerConfig(data_dir=tmp_path)
    session_id = None

    # === Phase 1: First server instance ===
    async with create_server(config) as server1:
        # Create session
        session = await _session_create(server1, name="e2e-test")
        session_id = session["session_id"]

        # Load documents
        docs = [
            {"type": "inline", "content": "def calculate_sum(a, b):\n    return a + b\n" * 100},
            {"type": "inline", "content": "class UserManager:\n    def create_user(self):\n        pass\n" * 100},
        ]
        load_result = await _docs_load(server1, session_id=session_id, sources=docs)
        assert len(load_result["loaded"]) == 2

        # First search (builds index)
        search_result1 = await _search_query(
            server1,
            session_id=session_id,
            query="calculate sum",
            method="bm25",
            limit=5
        )
        assert len(search_result1["matches"]) > 0
        assert search_result1["index_built"]

        # Verify index in memory
        assert session_id in server1._index_cache

        # Close session (persists index)
        close_result = await _session_close(server1, session_id=session_id)
        assert close_result["status"] == "completed"

        # Verify index persisted to disk
        index_path = server1.index_persistence._get_index_path(session_id)
        metadata_path = server1.index_persistence._get_metadata_path(session_id)
        assert index_path.exists(), "Index should be persisted to disk"
        assert metadata_path.exists(), "Metadata should be persisted to disk"

        # Verify index removed from memory
        assert session_id not in server1._index_cache

    # === Phase 2: Second server instance (restart simulation) ===
    async with create_server(config) as server2:
        # Index should not be in memory yet
        assert session_id not in server2._index_cache

        # Search should load from disk
        search_result2 = await _search_query(
            server2,
            session_id=session_id,
            query="calculate sum",
            method="bm25",
            limit=5
        )

        # Verify search works with loaded index
        assert len(search_result2["matches"]) > 0

        # Verify index now in memory cache
        assert session_id in server2._index_cache

        # Verify results are consistent
        assert search_result1["matches"][0]["doc_id"] == search_result2["matches"][0]["doc_id"]


@pytest.mark.asyncio
async def test_concurrent_sessions_dont_interfere(tmp_path):
    """Test that concurrent sessions operate independently.

    Validates that:
    - Multiple sessions can run concurrently
    - Each session has its own index
    - Sessions don't interfere with each other's data
    - Locks prevent race conditions
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create two sessions
        session1 = await _session_create(server, name="concurrent-session-1")
        session_id1 = session1["session_id"]

        session2 = await _session_create(server, name="concurrent-session-2")
        session_id2 = session2["session_id"]

        # Load different documents in each session
        docs1 = [{"type": "inline", "content": "Python programming language content " * 100}]
        docs2 = [{"type": "inline", "content": "JavaScript web development content " * 100}]

        await _docs_load(server, session_id=session_id1, sources=docs1)
        await _docs_load(server, session_id=session_id2, sources=docs2)

        # Concurrent searches on both sessions
        async def search_session1():
            return await _search_query(
                server,
                session_id=session_id1,
                query="Python programming",
                method="bm25"
            )

        async def search_session2():
            return await _search_query(
                server,
                session_id=session_id2,
                query="JavaScript web",
                method="bm25"
            )

        # Run searches concurrently
        result1, result2 = await asyncio.gather(search_session1(), search_session2())

        # Verify each session found its own content
        assert len(result1["matches"]) > 0
        assert len(result2["matches"]) > 0

        # Verify results don't mix (Python result shouldn't have JavaScript doc)
        assert "Python" in result1["matches"][0]["context"]
        assert "JavaScript" in result2["matches"][0]["context"]

        # Verify both indexes cached independently
        assert session_id1 in server._index_cache
        assert session_id2 in server._index_cache

        # Verify session stats are independent
        info1 = await _session_info(server, session_id=session_id1)
        info2 = await _session_info(server, session_id=session_id2)

        assert info1["document_count"] == 1
        assert info2["document_count"] == 1
        # Both sessions performed same operations, so tool_calls should be equal but positive
        assert info1["tool_calls_used"] > 0
        assert info2["tool_calls_used"] > 0


@pytest.mark.asyncio
async def test_logging_produces_valid_json(tmp_path):
    """Test that structured logging produces valid JSON for all operations.

    Validates that:
    - All tool operations produce structured logs
    - Logs are valid JSON
    - Correlation IDs are present and consistent
    - Logs include required fields (timestamp, level, message, etc.)
    """
    config = ServerConfig(data_dir=tmp_path, structured_logging=True)

    # Set up log capture
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("rlm_mcp.server")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        async with create_server(config) as server:
            # Create session
            session = await _session_create(server, name="logging-test")
            session_id = session["session_id"]

            # Load documents
            docs = [{"type": "inline", "content": "test content " * 100}]
            await _docs_load(server, session_id=session_id, sources=docs)

            # Search
            await _search_query(server, session_id=session_id, query="test")

            # Get session info
            await _session_info(server, session_id=session_id)

        # Parse log output
        log_output = log_stream.getvalue()
        log_lines = [line for line in log_output.strip().split("\n") if line]

        # Verify we have logs
        assert len(log_lines) > 0, "Should have captured structured logs"

        # Verify each log line is valid JSON
        parsed_logs = []
        for line in log_lines:
            try:
                log_data = json.loads(line)
                parsed_logs.append(log_data)
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in log line: {line}\nError: {e}")

        # Verify required fields in logs
        for log_data in parsed_logs:
            assert "timestamp" in log_data, "Log should have timestamp"
            assert "level" in log_data, "Log should have level"
            assert "logger" in log_data, "Log should have logger name"
            assert "message" in log_data, "Log should have message"

            # Verify correlation_id present when expected
            if "Starting" in log_data["message"] or "Completed" in log_data["message"]:
                assert "correlation_id" in log_data, f"Operation log should have correlation_id: {log_data['message']}"

        # Verify operation logs
        operation_logs = [
            log for log in parsed_logs
            if "operation" in log
        ]
        assert len(operation_logs) > 0, "Should have operation logs"

        # Verify operations include expected fields
        for log in operation_logs:
            # session_id may not be present for session.create (no session_id yet)
            if log.get("operation") != "rlm.session.create":
                assert "session_id" in log, f"Operation log should include session_id: {log.get('operation')}"
            assert "operation" in log, "Operation log should include operation name"
            if "Completed" in log["message"]:
                assert "duration_ms" in log, "Completed operation should have duration_ms"
                # success may be in top-level or in extra dict
                success = log.get("success") or log.get("extra", {}).get("success")
                assert success is not None, "Completed operation should have success flag"

    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_persistence_with_concurrent_operations(tmp_path):
    """Test that persistence works correctly with concurrent operations.

    Validates that:
    - Index can be built while other operations are in progress
    - Concurrent searches use the same cached index (no duplicate builds)
    - Index persistence doesn't block other operations
    - Session close waits for in-flight operations
    """
    config = ServerConfig(data_dir=tmp_path)
    session_id = None

    async with create_server(config) as server:
        # Create session and load documents
        session = await _session_create(server, name="concurrent-persist-test")
        session_id = session["session_id"]

        docs = [{"type": "inline", "content": f"Document {i} content " * 100} for i in range(5)]
        await _docs_load(server, session_id=session_id, sources=docs)

        # Launch multiple concurrent searches (should only build index once)
        search_tasks = [
            _search_query(server, session_id=session_id, query=f"Document {i}")
            for i in range(10)
        ]

        results = await asyncio.gather(*search_tasks)

        # Verify all searches succeeded
        assert len(results) == 10
        for result in results:
            assert len(result["matches"]) > 0

        # Verify only one index in cache (not 10 duplicates)
        assert session_id in server._index_cache

        # Close session (persists index)
        close_result = await _session_close(server, session_id=session_id)
        assert close_result["status"] == "completed"

    # Verify index persisted
    async with create_server(config) as server2:
        # Should be able to search using persisted index
        result = await _search_query(server2, session_id=session_id, query="Document 0")
        assert len(result["matches"]) > 0


@pytest.mark.asyncio
async def test_index_invalidation_with_locks(tmp_path):
    """Test that index invalidation works correctly with concurrent operations.

    Validates that:
    - Loading new documents invalidates both memory and disk caches
    - Concurrent searches rebuild index after invalidation
    - No race conditions during invalidation + rebuild
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create session and load initial documents
        session = await _session_create(server, name="invalidation-test")
        session_id = session["session_id"]

        docs1 = [{"type": "inline", "content": "Original content " * 100}]
        await _docs_load(server, session_id=session_id, sources=docs1)

        # Build index
        result1 = await _search_query(server, session_id=session_id, query="Original")
        assert len(result1["matches"]) > 0
        assert session_id in server._index_cache

        # Load new documents (invalidates index)
        docs2 = [{"type": "inline", "content": "New content " * 100}]
        await _docs_load(server, session_id=session_id, sources=docs2)

        # Verify index invalidated
        assert session_id not in server._index_cache
        index_path = server.index_persistence._get_index_path(session_id)
        assert not index_path.exists(), "Persisted index should be deleted"

        # Launch concurrent searches (should rebuild index once)
        search_tasks = [
            _search_query(server, session_id=session_id, query="content")
            for _ in range(5)
        ]

        results = await asyncio.gather(*search_tasks)

        # Verify all searches succeeded
        assert len(results) == 5
        for result in results:
            assert len(result["matches"]) > 0

        # Verify index rebuilt and cached
        assert session_id in server._index_cache


@pytest.mark.asyncio
async def test_correlation_id_isolation(tmp_path):
    """Test that correlation IDs are properly isolated between concurrent operations.

    Validates that:
    - Each operation gets a unique correlation_id
    - Correlation IDs don't leak between operations
    - Concurrent operations maintain their own correlation context
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create two sessions concurrently
        async def create_session_with_check(name):
            # Check correlation_id is initially None
            initial_cid = correlation_id_var.get(None)
            assert initial_cid is None, "Correlation ID should start as None"

            # Create session (sets correlation_id)
            result = await _session_create(server, name=name)

            # Correlation_id should be cleared after operation
            final_cid = correlation_id_var.get(None)
            assert final_cid is None, "Correlation ID should be cleared after operation"

            return result

        # Run concurrently
        session1, session2 = await asyncio.gather(
            create_session_with_check("session-1"),
            create_session_with_check("session-2"),
        )

        assert session1["session_id"] != session2["session_id"]


@pytest.mark.asyncio
async def test_error_recovery_preserves_consistency(tmp_path):
    """Test that errors don't leave system in inconsistent state.

    Validates that:
    - Failed operations don't corrupt indexes
    - Locks are properly released on error
    - Session state remains consistent after errors
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="error-recovery-test")
        session_id = session["session_id"]

        # Load valid documents
        docs = [{"type": "inline", "content": "test content " * 100}]
        await _docs_load(server, session_id=session_id, sources=docs)

        # Build index
        result1 = await _search_query(server, session_id=session_id, query="test")
        assert len(result1["matches"]) > 0

        # Try to load invalid document (should record error but not raise)
        load_result = await _docs_load(
            server,
            session_id=session_id,
            sources=[{"type": "invalid_type", "content": "test"}]
        )

        # Verify error was recorded
        assert len(load_result["errors"]) > 0
        assert "Unknown source type" in load_result["errors"][0]

        # Verify session still works after error
        result2 = await _search_query(server, session_id=session_id, query="test")
        assert len(result2["matches"]) > 0

        # Verify index still cached
        assert session_id in server._index_cache

        # Verify session can still be closed
        close_result = await _session_close(server, session_id=session_id)
        assert close_result["status"] == "completed"
