"""Error handling and edge case tests."""

import pytest
from rlm_mcp.server import RLMServer
from rlm_mcp.tools.session import _session_create, _session_info, _session_close
from rlm_mcp.tools.docs import _docs_load, _docs_peek, _docs_list
from rlm_mcp.tools.chunks import _chunk_create
from rlm_mcp.tools.search import _search_query
from rlm_mcp.tools.artifacts import _artifact_store, _artifact_get


@pytest.mark.asyncio
async def test_invalid_session_id(server: RLMServer):
    """Test that operations fail gracefully with invalid session ID."""

    fake_session_id = "nonexistent-session-id"

    # Session info should fail
    with pytest.raises(ValueError, match="Session not found"):
        await _session_info(server, session_id=fake_session_id)

    # Docs load should fail
    with pytest.raises(ValueError, match="Session not found"):
        await _docs_load(
            server,
            session_id=fake_session_id,
            sources=[{"type": "inline", "content": "test"}]
        )

    # Search should fail
    with pytest.raises(ValueError, match="Session not found"):
        await _search_query(
            server,
            session_id=fake_session_id,
            query="test",
            method="bm25"
        )


@pytest.mark.asyncio
async def test_invalid_doc_id(server: RLMServer):
    """Test that operations fail with invalid document ID."""

    session = await _session_create(server, name="invalid-doc-test")
    session_id = session["session_id"]

    fake_doc_id = "nonexistent-doc-id"

    # Peek should fail
    with pytest.raises(ValueError, match="Document not found"):
        await _docs_peek(server, session_id=session_id, doc_id=fake_doc_id)

    # Chunk should fail
    with pytest.raises(ValueError, match="Document not found"):
        await _chunk_create(
            server,
            session_id=session_id,
            doc_id=fake_doc_id,
            strategy={"type": "fixed", "chunk_size": 1000}
        )


@pytest.mark.asyncio
async def test_budget_enforcement(server: RLMServer):
    """Test that tool call budget is enforced."""

    # Create session with tiny budget
    session = await _session_create(
        server,
        name="budget-test",
        config={"max_tool_calls": 5}
    )
    session_id = session["session_id"]

    # Use up budget (session.create already counted as 1)
    for i in range(4):
        await _docs_load(
            server,
            session_id=session_id,
            sources=[{"type": "inline", "content": f"doc {i}"}]
        )

    # Next call should fail
    with pytest.raises(ValueError, match="budget exceeded"):
        await _docs_load(
            server,
            session_id=session_id,
            sources=[{"type": "inline", "content": "too many"}]
        )


@pytest.mark.asyncio
async def test_double_close_session(server: RLMServer):
    """Test that closing a session twice fails."""

    session = await _session_create(server, name="double-close-test")
    session_id = session["session_id"]

    # First close succeeds
    await _session_close(server, session_id=session_id)

    # Second close should fail
    with pytest.raises(ValueError, match="already closed"):
        await _session_close(server, session_id=session_id)


@pytest.mark.asyncio
async def test_empty_document(server: RLMServer):
    """Test handling of empty documents."""

    session = await _session_create(server, name="empty-doc-test")
    session_id = session["session_id"]

    # Load empty document
    result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": ""}]
    )

    assert len(result["loaded"]) == 1
    assert result["loaded"][0]["length_chars"] == 0

    # Peek should work but return empty
    doc_id = result["loaded"][0]["doc_id"]
    peek_result = await _docs_peek(server, session_id=session_id, doc_id=doc_id)
    assert peek_result["content"] == ""


@pytest.mark.asyncio
async def test_malformed_chunk_strategy(server: RLMServer):
    """Test that malformed chunk strategies are rejected."""

    session = await _session_create(server, name="malformed-chunk-test")
    session_id = session["session_id"]

    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test content"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Missing required fields
    with pytest.raises((ValueError, KeyError, TypeError)):
        await _chunk_create(
            server,
            session_id=session_id,
            doc_id=doc_id,
            strategy={"type": "fixed"}  # Missing chunk_size
        )

    # Invalid type
    with pytest.raises((ValueError, KeyError)):
        await _chunk_create(
            server,
            session_id=session_id,
            doc_id=doc_id,
            strategy={"type": "nonexistent", "chunk_size": 1000}
        )


@pytest.mark.asyncio
async def test_search_on_empty_session(server: RLMServer):
    """Test search on session with no documents."""

    session = await _session_create(server, name="empty-search-test")
    session_id = session["session_id"]

    # Search should work but return no results
    result = await _search_query(
        server,
        session_id=session_id,
        query="test",
        method="bm25"
    )

    assert result["matches"] == []
    assert result["total_matches"] == 0


@pytest.mark.asyncio
async def test_artifact_wrong_session(server: RLMServer):
    """Test that artifacts can't be accessed from wrong session."""

    # Create two sessions
    session1 = await _session_create(server, name="session-1")
    session1_id = session1["session_id"]

    session2 = await _session_create(server, name="session-2")
    session2_id = session2["session_id"]

    # Load doc in session 1
    load_result = await _docs_load(
        server,
        session_id=session1_id,
        sources=[{"type": "inline", "content": "test"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Store artifact in session 1
    artifact_result = await _artifact_store(
        server,
        session_id=session1_id,
        type="summary",
        content={"text": "test summary"},
        span={"doc_id": doc_id, "start": 0, "end": 4}
    )
    artifact_id = artifact_result["artifact_id"]

    # Try to get artifact from session 2
    with pytest.raises(ValueError, match="not in session"):
        await _artifact_get(
            server,
            session_id=session2_id,
            artifact_id=artifact_id
        )


@pytest.mark.asyncio
async def test_peek_beyond_document_bounds(server: RLMServer):
    """Test peek with out-of-bounds offsets."""

    session = await _session_create(server, name="bounds-test")
    session_id = session["session_id"]

    content = "Hello, World!"
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": content}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Peek beyond end should be clamped
    result = await _docs_peek(
        server,
        session_id=session_id,
        doc_id=doc_id,
        start=0,
        end=1000  # Beyond document length
    )

    assert len(result["content"]) == len(content)
    assert result["content"] == content


@pytest.mark.asyncio
async def test_chunk_overlap_larger_than_chunk(server: RLMServer):
    """Test chunking with overlap >= chunk_size."""

    session = await _session_create(server, name="overlap-test")
    session_id = session["session_id"]

    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "x" * 10000}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Overlap equal to chunk_size should fail or be clamped
    with pytest.raises((ValueError, AssertionError)):
        await _chunk_create(
            server,
            session_id=session_id,
            doc_id=doc_id,
            strategy={
                "type": "fixed",
                "chunk_size": 1000,
                "overlap": 1000  # Equal to chunk_size
            }
        )


@pytest.mark.asyncio
async def test_dos_protection_peek(server: RLMServer):
    """Test that peek respects max_chars_per_peek limit."""

    session = await _session_create(
        server,
        name="dos-peek-test",
        config={"max_chars_per_peek": 100}
    )
    session_id = session["session_id"]

    # Load large document
    large_content = "x" * 10000
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": large_content}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Peek should be truncated to 100 chars
    result = await _docs_peek(server, session_id=session_id, doc_id=doc_id)

    assert len(result["content"]) == 100
    assert result["truncated"] == True


@pytest.mark.asyncio
async def test_invalid_source_type(server: RLMServer):
    """Test that invalid source types are rejected."""

    session = await _session_create(server, name="invalid-source-test")
    session_id = session["session_id"]

    # Try to load with invalid source type
    result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "invalid_type", "content": "test"}]
    )

    # Should have error for this source
    assert len(result["errors"]) > 0
    assert "invalid_type" in str(result["errors"][0]).lower() or "unsupported" in str(result["errors"][0]).lower()


@pytest.mark.asyncio
async def test_concurrent_session_isolation(server: RLMServer):
    """Test that concurrent sessions don't interfere."""

    # Create two sessions
    session1 = await _session_create(server, name="concurrent-1")
    session1_id = session1["session_id"]

    session2 = await _session_create(server, name="concurrent-2")
    session2_id = session2["session_id"]

    # Load different docs in each session
    await _docs_load(
        server,
        session_id=session1_id,
        sources=[{"type": "inline", "content": "Session 1 content"}]
    )

    await _docs_load(
        server,
        session_id=session2_id,
        sources=[{"type": "inline", "content": "Session 2 content"}]
    )

    # Verify isolation
    list1 = await _docs_list(server, session_id=session1_id)
    list2 = await _docs_list(server, session_id=session2_id)

    assert list1["total"] == 1
    assert list2["total"] == 1
    assert list1["documents"][0]["doc_id"] != list2["documents"][0]["doc_id"]
