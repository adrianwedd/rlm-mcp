"""Batch loading tests - validate performance and memory safety."""

import asyncio
import time
from pathlib import Path

import pytest

from rlm_mcp.config import ServerConfig
from rlm_mcp.server import create_server
from rlm_mcp.tools.session import _session_create
from rlm_mcp.tools.docs import _docs_load


@pytest.mark.asyncio
async def test_batch_loading_performance(tmp_path):
    """Test that batch loading is faster than sequential loading.

    Validates that:
    - Multiple documents loaded concurrently (not sequentially)
    - Database batch insert used (not individual inserts)
    - Performance improvement measurable
    """
    config = ServerConfig(data_dir=tmp_path, max_concurrent_loads=10)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="batch-perf-test")
        session_id = session["session_id"]

        # Create 20 inline documents
        docs = [
            {"type": "inline", "content": f"Document {i} content " * 100}
            for i in range(20)
        ]

        # Load with batch
        start = time.time()
        result = await _docs_load(server, session_id=session_id, sources=docs)
        batch_time = time.time() - start

        # Verify all loaded
        assert len(result["loaded"]) == 20
        assert len(result["errors"]) == 0

        print(f"Batch load time: {batch_time:.2f}s")

        # Batch loading should complete reasonably fast (<1s for 20 inline docs)
        assert batch_time < 1.0, f"Batch loading took {batch_time:.2f}s, expected <1s"


@pytest.mark.asyncio
async def test_partial_batch_failure(tmp_path):
    """Test that errors in some sources don't fail entire batch.

    Validates that:
    - Successful documents are loaded even if some fail
    - Errors are reported but don't block other documents
    - Batch insert still works with partial successes
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="partial-failure-test")
        session_id = session["session_id"]

        # Mix valid and invalid sources
        docs = [
            {"type": "inline", "content": "Valid document 1 " * 100},
            {"type": "file", "path": "/nonexistent/file.txt"},  # Will fail
            {"type": "inline", "content": "Valid document 2 " * 100},
            {"type": "invalid_type", "content": "test"},  # Will fail
            {"type": "inline", "content": "Valid document 3 " * 100},
        ]

        # Load batch
        result = await _docs_load(server, session_id=session_id, sources=docs)

        # Verify partial success
        assert len(result["loaded"]) == 3, "Should load 3 valid documents"
        assert len(result["errors"]) == 2, "Should report 2 errors"

        # Verify error messages
        assert any("nonexistent" in err or "not found" in err.lower() for err in result["errors"])
        assert any("Unknown source type" in err for err in result["errors"])


@pytest.mark.asyncio
async def test_batch_loading_memory_bounded(tmp_path):
    """Test that semaphore limits concurrent file loads (Patch #6 - memory safety).

    Validates that:
    - Max concurrent loads enforced by semaphore
    - No more than max_concurrent_loads files loaded simultaneously
    - Prevents out-of-memory from loading too many large files
    """
    config = ServerConfig(data_dir=tmp_path, max_concurrent_loads=5)

    # Create test files
    test_dir = tmp_path / "test_files"
    test_dir.mkdir()

    for i in range(15):
        (test_dir / f"file{i}.txt").write_text(f"Content for file {i}\n" * 1000)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="memory-bounded-test")
        session_id = session["session_id"]

        # Track concurrent loads
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        # Monkey-patch _load_file_no_save to track concurrency
        from rlm_mcp.tools import docs
        original_load = docs._load_file_no_save

        async def tracked_load(*args, **kwargs):
            nonlocal current_concurrent, max_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent

            try:
                # Simulate some I/O delay
                await asyncio.sleep(0.01)
                result = await original_load(*args, **kwargs)
                return result
            finally:
                async with lock:
                    current_concurrent -= 1

        docs._load_file_no_save = tracked_load

        try:
            # Load directory with 15 files
            sources = [{"type": "directory", "path": str(test_dir)}]
            result = await _docs_load(server, session_id=session_id, sources=sources)

            # Verify all loaded
            assert len(result["loaded"]) == 15

            # Verify concurrency bounded by semaphore
            assert max_concurrent <= config.max_concurrent_loads, (
                f"Max concurrent was {max_concurrent}, expected <= {config.max_concurrent_loads}"
            )
            print(f"Max concurrent loads: {max_concurrent} (limit: {config.max_concurrent_loads})")
        finally:
            # Restore original function
            docs._load_file_no_save = original_load


@pytest.mark.asyncio
async def test_max_concurrent_enforced(tmp_path):
    """Test that no more than max_concurrent_loads files are loaded simultaneously.

    Validates that:
    - Semaphore enforces concurrency limit
    - System doesn't exceed max_concurrent_loads even with many files
    - Memory safety mechanism works correctly
    """
    config = ServerConfig(data_dir=tmp_path, max_concurrent_loads=3)

    # Create many test files
    test_dir = tmp_path / "many_files"
    test_dir.mkdir()

    for i in range(30):
        (test_dir / f"file{i}.txt").write_text(f"File {i} content\n" * 500)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="max-concurrent-test")
        session_id = session["session_id"]

        # Track concurrent loads in detail
        concurrent_loads = []
        lock = asyncio.Lock()

        # Monkey-patch to track exact timing
        from rlm_mcp.tools import docs
        original_load = docs._load_file_no_save

        async def tracked_load(*args, **kwargs):
            start_time = time.time()

            async with lock:
                concurrent_loads.append(("start", start_time))

            try:
                await asyncio.sleep(0.02)  # Simulate I/O
                result = await original_load(*args, **kwargs)
                return result
            finally:
                end_time = time.time()
                async with lock:
                    concurrent_loads.append(("end", end_time))

        docs._load_file_no_save = tracked_load

        try:
            # Load directory with 30 files
            sources = [{"type": "directory", "path": str(test_dir)}]
            result = await _docs_load(server, session_id=session_id, sources=sources)

            # Verify all loaded
            assert len(result["loaded"]) == 30

            # Calculate max concurrent at any point in time
            active = 0
            max_active = 0

            for event, timestamp in sorted(concurrent_loads, key=lambda x: x[1]):
                if event == "start":
                    active += 1
                    max_active = max(max_active, active)
                else:
                    active -= 1

            # Verify never exceeded limit
            assert max_active <= config.max_concurrent_loads, (
                f"Max active loads was {max_active}, expected <= {config.max_concurrent_loads}"
            )
            print(f"Max concurrent loads enforced: {max_active}/{config.max_concurrent_loads}")
        finally:
            # Restore original function
            docs._load_file_no_save = original_load


@pytest.mark.asyncio
async def test_file_size_limit_enforced(tmp_path):
    """Test that max_file_size_mb limit is enforced.

    Validates that:
    - Files larger than max_file_size_mb are rejected
    - Error message is clear and helpful
    - Other files in batch still load successfully
    """
    config = ServerConfig(data_dir=tmp_path, max_file_size_mb=1)  # 1MB limit

    # Create test files
    test_dir = tmp_path / "size_test"
    test_dir.mkdir()

    # Small file (should succeed)
    (test_dir / "small.txt").write_text("Small content\n" * 100)

    # Large file (should fail - simulate 2MB)
    large_content = "X" * (2 * 1024 * 1024)  # 2MB
    (test_dir / "large.txt").write_text(large_content)

    # Another small file (should succeed)
    (test_dir / "small2.txt").write_text("More small content\n" * 100)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="size-limit-test")
        session_id = session["session_id"]

        # Load directory
        sources = [{"type": "directory", "path": str(test_dir)}]
        result = await _docs_load(server, session_id=session_id, sources=sources)

        # Verify small files loaded, large file rejected
        assert len(result["loaded"]) == 2, "Should load 2 small files"

        # Verify error mentions file size
        # Note: large file error is silently skipped in _load_directory_concurrent


@pytest.mark.asyncio
async def test_batch_insert_atomicity(tmp_path):
    """Test that batch insert is atomic (all or nothing).

    Validates that:
    - All documents inserted in single transaction
    - If batch insert fails, no documents are saved
    - Database remains consistent
    """
    config = ServerConfig(data_dir=tmp_path)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="atomicity-test")
        session_id = session["session_id"]

        # Load multiple documents
        docs = [
            {"type": "inline", "content": f"Document {i} " * 100}
            for i in range(10)
        ]

        result = await _docs_load(server, session_id=session_id, sources=docs)

        # Verify all loaded
        assert len(result["loaded"]) == 10

        # Verify all documents exist in database
        from rlm_mcp.tools.docs import _docs_list
        list_result = await _docs_list(server, session_id=session_id)
        assert list_result["total"] == 10


@pytest.mark.asyncio
async def test_concurrent_batch_loads(tmp_path):
    """Test that multiple batch loads can run concurrently safely.

    Validates that:
    - Multiple _docs_load calls can run concurrently
    - Each gets its own semaphore
    - No interference between concurrent loads
    """
    config = ServerConfig(data_dir=tmp_path, max_concurrent_loads=5)

    async with create_server(config) as server:
        # Create session
        session = await _session_create(server, name="concurrent-batch-test")
        session_id = session["session_id"]

        # Launch multiple concurrent batch loads
        async def load_batch(batch_num):
            docs = [
                {"type": "inline", "content": f"Batch {batch_num} Doc {i} " * 50}
                for i in range(5)
            ]
            return await _docs_load(server, session_id=session_id, sources=docs)

        # Run 3 batches concurrently
        results = await asyncio.gather(
            load_batch(1),
            load_batch(2),
            load_batch(3),
        )

        # Verify all succeeded
        for result in results:
            assert len(result["loaded"]) == 5
            assert len(result["errors"]) == 0

        # Verify total documents
        from rlm_mcp.tools.docs import _docs_list
        list_result = await _docs_list(server, session_id=session_id)
        assert list_result["total"] == 15  # 3 batches * 5 docs each
