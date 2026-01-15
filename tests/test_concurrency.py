"""Tests for concurrency safety (session locks, atomic operations)."""

import asyncio
from pathlib import Path

import pytest

from rlm_mcp.models import Session, SessionConfig, SessionStatus
from rlm_mcp.server import create_server


@pytest.fixture
async def server_with_session(tmp_path):
    """Create server with test session and sample document."""
    from rlm_mcp.config import ServerConfig

    config = ServerConfig(
        data_dir=tmp_path,
        default_max_tool_calls=1000,
    )

    async with create_server(config) as server:
        # Create session
        session = Session(
            name="test-session",
            config=SessionConfig(
                max_tool_calls=1000,
                max_chars_per_response=50000,
                max_chars_per_peek=10000,
            ),
        )
        await server.db.create_session(session)

        # Load a document
        test_content = "This is a test document for concurrency testing. " * 100
        test_file = tmp_path / "test.txt"
        test_file.write_text(test_content)

        from rlm_mcp.tools.docs import _docs_load

        await _docs_load(
            server,
            session_id=session.id,
            sources=[{"type": "file", "path": str(test_file)}],
        )

        yield server, session.id


@pytest.mark.asyncio
async def test_concurrent_budget_increments(server_with_session):
    """Test that concurrent tool calls accurately increment budget.

    Atomic UPDATE ensures no lost updates when multiple operations
    happen concurrently.
    """
    server, session_id = server_with_session

    # Reset tool call counter
    session = await server.db.get_session(session_id)
    session.tool_calls_used = 0
    await server.db.update_session(session)

    # Concurrently increment budget 50 times
    num_increments = 50

    async def increment():
        return await server.db.increment_tool_calls(session_id)

    results = await asyncio.gather(*[increment() for _ in range(num_increments)])

    # Verify final count is accurate
    session = await server.db.get_session(session_id)
    assert session.tool_calls_used == num_increments

    # Verify all intermediate values are unique and in order
    assert len(set(results)) == num_increments  # All values unique
    assert min(results) == 1  # First increment
    assert max(results) == num_increments  # Last increment


@pytest.mark.asyncio
async def test_session_lock_prevents_concurrent_close(server_with_session):
    """Test that session locks prevent concurrent close operations."""
    server, session_id = server_with_session

    from rlm_mcp.tools.session import _session_close

    # Try to close the session concurrently multiple times
    # Only one should succeed; others should fail gracefully
    results = []
    errors = []

    async def attempt_close():
        try:
            result = await _session_close(server, session_id=session_id)
            results.append(result)
        except ValueError as e:
            errors.append(str(e))

    # Launch 5 concurrent close attempts
    await asyncio.gather(*[attempt_close() for _ in range(5)], return_exceptions=True)

    # Exactly one should succeed
    assert len(results) == 1, f"Expected 1 success, got {len(results)}"

    # Others should fail with "already closed" error
    assert len(errors) == 4, f"Expected 4 errors, got {len(errors)}"
    for error in errors:
        assert "already closed" in error.lower()

    # Verify session is closed
    session = await server.db.get_session(session_id)
    assert session.status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_session_lock_acquired_and_released(server_with_session):
    """Test that session locks are properly acquired and released."""
    server, session_id = server_with_session

    # Get lock
    lock1 = await server.get_session_lock(session_id)
    assert not lock1.locked()  # Not held yet

    # Acquire it
    async with lock1:
        assert lock1.locked()  # Now held

    # Lock released after context
    assert not lock1.locked()

    # Get same lock again (should return same instance)
    lock2 = await server.get_session_lock(session_id)
    assert lock1 is lock2  # Same object

    # Release lock from cache
    await server.release_session_lock(session_id)

    # Getting lock again should create new instance
    lock3 = await server.get_session_lock(session_id)
    assert lock1 is not lock3  # Different object


@pytest.mark.asyncio
async def test_concurrent_index_cache_operations(server_with_session):
    """Test that concurrent operations on index cache don't cause issues.

    This test simulates multiple concurrent searches that would trigger
    index builds and cache operations.
    """
    server, session_id = server_with_session

    # Simulate concurrent cache operations
    async def cache_operation(value: int):
        # Simulate index build and caching
        lock = await server.get_session_lock(session_id)
        async with lock:
            # Check if in cache
            if session_id not in server._index_cache:
                # Simulate index build with small delay
                await asyncio.sleep(0.01)
                server._index_cache[session_id] = f"index-{value}"
            return server._index_cache[session_id]

    # Launch 10 concurrent cache operations
    results = await asyncio.gather(*[cache_operation(i) for i in range(10)])

    # All should get the same cached value (first one wins)
    assert len(set(results)) == 1, "Expected all operations to get same cached value"
    assert results[0].startswith("index-")


@pytest.mark.asyncio
async def test_lock_cleanup_after_session_close(server_with_session):
    """Test that locks are released and removed after session close."""
    server, session_id = server_with_session

    # Get lock to verify it exists
    lock = await server.get_session_lock(session_id)
    assert session_id in server._session_locks

    # Close session (should release lock)
    from rlm_mcp.tools.session import _session_close

    await _session_close(server, session_id=session_id)

    # Verify lock was removed from cache
    assert session_id not in server._session_locks


@pytest.mark.asyncio
async def test_atomic_budget_with_session_query(server_with_session):
    """Test that budget increments are atomic even with concurrent session queries."""
    server, session_id = server_with_session

    # Reset counter
    session = await server.db.get_session(session_id)
    session.tool_calls_used = 0
    await server.db.update_session(session)

    num_operations = 30

    async def mixed_operation(op_id: int):
        if op_id % 2 == 0:
            # Increment budget
            return await server.db.increment_tool_calls(session_id)
        else:
            # Query session
            session = await server.db.get_session(session_id)
            return session.tool_calls_used

    results = await asyncio.gather(*[mixed_operation(i) for i in range(num_operations)])

    # Final count should be num_operations / 2 (only half increment)
    session = await server.db.get_session(session_id)
    expected_increments = num_operations // 2
    assert session.tool_calls_used == expected_increments


@pytest.mark.asyncio
async def test_lock_manager_lock_prevents_race():
    """Test that the lock manager lock prevents race conditions."""
    from rlm_mcp.config import ServerConfig
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        config = ServerConfig(data_dir=Path(tmp_dir))

        async with create_server(config) as server:
            # Create multiple sessions concurrently
            num_sessions = 20
            session_ids = [f"session-{i}" for i in range(num_sessions)]

            async def get_lock_concurrent(sid: str):
                return await server.get_session_lock(sid)

            # Get locks concurrently for all sessions
            locks = await asyncio.gather(*[get_lock_concurrent(sid) for sid in session_ids])

            # Verify each session has exactly one lock
            assert len(server._session_locks) == num_sessions

            # Verify all locks are different instances
            assert len(set(id(lock) for lock in locks)) == num_sessions


@pytest.mark.asyncio
async def test_increment_nonexistent_session_raises_error(server_with_session):
    """Test that incrementing budget for nonexistent session raises error."""
    server, _ = server_with_session

    with pytest.raises(ValueError, match="Session not found"):
        await server.db.increment_tool_calls("nonexistent-session-id")


@pytest.mark.asyncio
async def test_atomic_budget_enforcement_prevents_race(server_with_session):
    """Test that try_increment_tool_calls prevents concurrent calls from exceeding budget."""
    server, session_id = server_with_session

    # Set budget to 100
    max_calls = 100

    # Check current count (fixture may have used some calls)
    session = await server.db.get_session(session_id)
    current_used = session.tool_calls_used

    # Increment to 99 (one below limit)
    remaining_to_99 = 99 - current_used
    for _ in range(remaining_to_99):
        await server.db.increment_tool_calls(session_id)

    # Verify we're at 99
    session = await server.db.get_session(session_id)
    assert session.tool_calls_used == 99

    # Race 10 concurrent calls at the boundary
    # Only 1 should succeed (bringing count to 100), 9 should fail
    async def try_call():
        return await server.db.try_increment_tool_calls(session_id, max_calls)

    results = await asyncio.gather(*[try_call() for _ in range(10)])

    # Count successes and failures
    successes = sum(1 for allowed, _ in results if allowed)
    failures = sum(1 for allowed, _ in results if not allowed)

    # Exactly 1 should succeed, 9 should fail
    assert successes == 1, f"Expected 1 success, got {successes}"
    assert failures == 9, f"Expected 9 failures, got {failures}"

    # Final count should be exactly 100 (not 101, 102, etc.)
    reloaded = await server.db.get_session(session_id)
    assert reloaded.tool_calls_used == 100

    # All subsequent calls should fail
    allowed, used = await server.db.try_increment_tool_calls(session_id, max_calls)
    assert not allowed
    assert used == 100
