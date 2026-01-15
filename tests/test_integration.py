"""Integration smoke test: exercises core RLM tools in a happy path.

This test validates the full workflow:
session.create → docs.load → docs.list → docs.peek → search.query →
chunk.create → span.get → artifact.store → artifact.list → artifact.get →
session.info → session.close
"""

from __future__ import annotations

import logging
import pytest

from rlm_mcp.server import RLMServer
from rlm_mcp.config import ServerConfig


@pytest.mark.asyncio
async def test_full_workflow_smoke(server: RLMServer, sample_python_code: str):
    """Smoke test: complete session lifecycle with all tools."""
    
    # --- 1. Session Create ---
    from rlm_mcp.tools.session import _session_create
    
    session_result = await _session_create(
        server,
        name="smoke-test-session",
        config={"max_tool_calls": 100},
    )
    
    assert "session_id" in session_result
    session_id = session_result["session_id"]
    assert session_result["config"]["max_tool_calls"] == 100
    
    # --- 2. Docs Load (inline) ---
    from rlm_mcp.tools.docs import _docs_load
    
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[
            {"type": "inline", "content": sample_python_code},
        ],
    )
    
    assert len(load_result["loaded"]) == 1
    assert load_result["errors"] == []
    doc_id = load_result["loaded"][0]["doc_id"]
    content_hash = load_result["loaded"][0]["content_hash"]
    assert load_result["total_chars"] == len(sample_python_code)
    
    # --- 3. Docs List ---
    from rlm_mcp.tools.docs import _docs_list
    
    list_result = await _docs_list(server, session_id=session_id)
    
    assert list_result["total"] == 1
    assert list_result["documents"][0]["doc_id"] == doc_id
    
    # --- 4. Docs Peek ---
    from rlm_mcp.tools.docs import _docs_peek
    
    peek_result = await _docs_peek(
        server,
        session_id=session_id,
        doc_id=doc_id,
        start=0,
        end=100,
    )
    
    assert "content" in peek_result
    assert peek_result["span"]["doc_id"] == doc_id
    assert peek_result["span"]["start"] == 0
    assert "content_hash" in peek_result
    assert not peek_result["truncated"]
    
    # --- 5. Search Query (BM25) ---
    from rlm_mcp.tools.search import _search_query
    
    search_result = await _search_query(
        server,
        session_id=session_id,
        query="Calculator add",
        method="bm25",
        limit=5,
    )
    
    assert "matches" in search_result
    # Note: index_built_this_call no longer tracked with persistence layer
    assert search_result["index_built"]
    
    # Verify index persists across queries
    search_result_2 = await _search_query(
        server,
        session_id=session_id,
        query="hello",
        method="bm25",
    )
    assert not search_result_2["index_built_this_call"]  # Should be cached
    
    # --- 6. Chunk Create ---
    from rlm_mcp.tools.chunks import _chunk_create
    
    chunk_result = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={"type": "delimiter", "delimiter": r"\ndef "},
    )
    
    assert chunk_result["total_spans"] > 0
    assert not chunk_result["cached"]
    span_id = chunk_result["spans"][0]["span_id"]
    
    # Verify caching
    chunk_result_2 = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={"type": "delimiter", "delimiter": r"\ndef "},
    )
    assert chunk_result_2["cached"]
    
    # --- 7. Span Get ---
    from rlm_mcp.tools.chunks import _span_get
    
    span_result = await _span_get(
        server,
        session_id=session_id,
        span_ids=[span_id],
    )
    
    assert len(span_result["spans"]) == 1
    assert span_result["spans"][0]["span_id"] == span_id
    assert "content" in span_result["spans"][0]
    assert "span" in span_result["spans"][0]  # Provenance
    assert "content_hash" in span_result["spans"][0]
    
    # --- 8. Artifact Store ---
    from rlm_mcp.tools.artifacts import _artifact_store
    
    artifact_result = await _artifact_store(
        server,
        session_id=session_id,
        type="summary",
        content={"text": "This module defines a Calculator class"},
        span_id=span_id,
        provenance={"model": "test-model"},
    )
    
    assert "artifact_id" in artifact_result
    artifact_id = artifact_result["artifact_id"]
    assert artifact_result["span_id"] == span_id
    
    # --- 9. Artifact List ---
    from rlm_mcp.tools.artifacts import _artifact_list
    
    list_artifacts = await _artifact_list(
        server,
        session_id=session_id,
        type="summary",
    )
    
    assert len(list_artifacts["artifacts"]) == 1
    assert list_artifacts["artifacts"][0]["artifact_id"] == artifact_id
    
    # --- 10. Artifact Get ---
    from rlm_mcp.tools.artifacts import _artifact_get
    
    get_artifact = await _artifact_get(
        server,
        session_id=session_id,
        artifact_id=artifact_id,
    )
    
    assert get_artifact["type"] == "summary"
    assert get_artifact["content"]["text"] == "This module defines a Calculator class"
    assert get_artifact["span_id"] == span_id
    assert get_artifact["span"] is not None  # Full span reference
    assert get_artifact["provenance"]["model"] == "test-model"
    
    # --- 11. Session Info ---
    from rlm_mcp.tools.session import _session_info
    
    info_result = await _session_info(server, session_id=session_id)
    
    assert info_result["session_id"] == session_id
    assert info_result["status"] == "active"
    assert info_result["document_count"] == 1
    assert info_result["index_built"]  # BM25 was built
    assert info_result["tool_calls_used"] > 0  # Budget tracking works
    
    # --- 12. Session Close ---
    from rlm_mcp.tools.session import _session_close
    
    close_result = await _session_close(server, session_id=session_id)
    
    assert close_result["status"] == "completed"
    assert close_result["summary"]["documents"] == 1
    assert close_result["summary"]["artifacts"] == 1
    assert close_result["summary"]["tool_calls"] > 0


@pytest.mark.asyncio
async def test_index_invalidation_on_doc_load(server: RLMServer, sample_python_code: str):
    """Test that loading new docs invalidates the BM25 cache."""
    from rlm_mcp.tools.session import _session_create
    from rlm_mcp.tools.docs import _docs_load
    from rlm_mcp.tools.search import _search_query
    
    # Create session
    session = await _session_create(server, name="invalidation-test")
    session_id = session["session_id"]
    
    # Load first doc
    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "First document content"}],
    )
    
    # Build index
    result1 = await _search_query(server, session_id=session_id, query="First")
    # Note: index_built_this_call no longer tracked with persistence layer
    assert result1["index_built"]

    # Load second doc (should invalidate index)
    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "Second document content"}],
    )

    # Index should be invalidated (both memory and disk)
    assert session_id not in server._index_cache
    # Check persisted index was also deleted
    index_path = server.index_persistence._get_index_path(session_id)
    assert not index_path.exists()

    # Next search should rebuild
    result2 = await _search_query(server, session_id=session_id, query="Second")
    # Note: index_built_this_call no longer tracked with persistence layer


@pytest.mark.asyncio
async def test_budget_enforcement(server: RLMServer):
    """Test that tool call budget is enforced."""
    from rlm_mcp.tools.session import _session_create, _session_info
    from rlm_mcp.tools.docs import _docs_load
    
    # Create session with tiny budget
    session = await _session_create(
        server,
        name="budget-test",
        config={"max_tool_calls": 3},
    )
    session_id = session["session_id"]

    # session.create counted as call 1

    # Second call (uses 2)
    await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test"}],
    )

    # Third call (uses 3)
    await _session_info(server, session_id=session_id)

    # Fourth call should fail
    with pytest.raises(ValueError, match="budget exceeded"):
        await _session_info(server, session_id=session_id)


@pytest.mark.asyncio  
async def test_char_limit_enforcement(server: RLMServer):
    """Test that character limits are enforced on content-returning tools."""
    from rlm_mcp.tools.session import _session_create
    from rlm_mcp.tools.docs import _docs_load, _docs_peek
    
    # Create session with small char limit
    session = await _session_create(
        server,
        name="char-limit-test",
        config={"max_chars_per_peek": 100},
    )
    session_id = session["session_id"]

    # Load large doc
    large_content = "x" * 1000
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": large_content}],
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Peek should truncate
    peek_result = await _docs_peek(
        server,
        session_id=session_id,
        doc_id=doc_id,
    )

    assert len(peek_result["content"]) == 100
    assert peek_result["truncated"]


class TestToolNaming:
    """Tests for tool naming strict/compat modes."""
    
    def test_strict_mode_raises_on_unsupported_sdk(self):
        """In strict mode, ToolNamingError is raised if SDK doesn't support name=."""
        from rlm_mcp.server import named_tool, ToolNamingError
        
        # Mock an SDK that doesn't support name=
        class MockServer:
            def tool(self, **kwargs):
                if "name" in kwargs:
                    raise TypeError("tool() got an unexpected keyword argument 'name'")
                def decorator(func):
                    return func
                return decorator
        
        mock_server = MockServer()
        
        with pytest.raises(ToolNamingError, match="doesn't support tool"):
            @named_tool(mock_server, "rlm.test.tool", strict=True)
            async def test_tool():
                pass
    
    def test_compat_mode_falls_back_with_warning(self, caplog):
        """In compat mode, fallback works with a one-time warning."""
        from rlm_mcp.server import named_tool, _WARNED_NO_NAME_SUPPORT
        import rlm_mcp.server as server_module
        
        # Reset warning flag for test isolation
        server_module._WARNED_NO_NAME_SUPPORT = False
        
        # Mock an SDK that doesn't support name=
        registered_tools = []
        
        class MockServer:
            def tool(self, **kwargs):
                def decorator(func):
                    registered_tools.append(func.__name__)
                    return func
                if "name" in kwargs:
                    raise TypeError("tool() got an unexpected keyword argument 'name'")
                return decorator
        
        mock_server = MockServer()
        
        # First tool should warn
        with caplog.at_level(logging.WARNING):
            @named_tool(mock_server, "rlm.test.tool1", strict=False)
            async def test_tool_one():
                pass
        
        assert "doesn't support tool(name=...)" in caplog.text
        assert "test_tool_one" in registered_tools
        
        # Clear log for second tool
        caplog.clear()
        
        # Second tool should NOT warn again (one-time)
        @named_tool(mock_server, "rlm.test.tool2", strict=False)
        async def test_tool_two():
            pass
        
        assert "doesn't support" not in caplog.text
        assert "test_tool_two" in registered_tools
    
    def test_canonical_naming_works_with_supported_sdk(self):
        """With a supported SDK, canonical names are registered."""
        from rlm_mcp.server import named_tool
        
        # Mock an SDK that supports name=
        registered_tools = {}
        
        class MockServer:
            def tool(self, name=None):
                def decorator(func):
                    tool_name = name or func.__name__
                    registered_tools[tool_name] = func
                    return func
                return decorator
        
        mock_server = MockServer()
        
        @named_tool(mock_server, "rlm.test.canonical", strict=True)
        async def test_tool():
            pass
        
        assert "rlm.test.canonical" in registered_tools
        assert "test_tool" not in registered_tools
