"""Provenance tracking tests - ensure all artifacts have proper span references."""

import pytest
from rlm_mcp.server import RLMServer
from rlm_mcp.tools.session import _session_create
from rlm_mcp.tools.docs import _docs_load, _docs_peek
from rlm_mcp.tools.chunks import _chunk_create
from rlm_mcp.tools.search import _search_query
from rlm_mcp.tools.artifacts import _artifact_store, _artifact_get, _artifact_list


@pytest.mark.asyncio
async def test_span_get_includes_provenance(server: RLMServer):
    """Test that span.get returns proper provenance."""

    from rlm_mcp.tools.chunks import _span_get

    session = await _session_create(server, name="span-provenance-test")
    session_id = session["session_id"]

    # Load document
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "Hello, World!"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Create chunks
    chunk_result = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={"type": "fixed", "chunk_size": 100}
    )

    span_id = chunk_result["spans"][0]["span_id"]

    # Get span
    span_result = await _span_get(server, session_id=session_id, span_id=span_id)

    # Verify provenance fields
    assert "span_id" in span_result
    assert "span" in span_result
    assert "doc_id" in span_result["span"]
    assert "start" in span_result["span"]
    assert "end" in span_result["span"]
    assert "content_hash" in span_result
    assert "truncated" in span_result


@pytest.mark.asyncio
async def test_artifact_stores_span_reference(server: RLMServer):
    """Test that artifacts properly reference their source spans."""

    session = await _session_create(server, name="artifact-span-test")
    session_id = session["session_id"]

    # Load document
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "def foo():\n    pass\n" * 100}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Create chunk
    chunk_result = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={"type": "fixed", "chunk_size": 500}
    )
    span_id = chunk_result["spans"][0]["span_id"]

    # Store artifact with span reference
    artifact_result = await _artifact_store(
        server,
        session_id=session_id,
        type="summary",
        content={"text": "Functions defined: foo"},
        span_id=span_id,
        provenance={"model": "claude-sonnet-4-5", "tool": "test"}
    )

    artifact_id = artifact_result["artifact_id"]

    # Retrieve artifact
    artifact = await _artifact_get(
        server,
        session_id=session_id,
        artifact_id=artifact_id
    )

    # Verify provenance
    assert artifact["span_id"] == span_id
    assert artifact["span"] is not None
    assert artifact["span"]["doc_id"] == doc_id
    assert artifact["provenance"] is not None
    assert artifact["provenance"]["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_artifact_with_inline_span_creates_span(server: RLMServer):
    """Test that artifact.store creates span when given inline span reference."""

    session = await _session_create(server, name="inline-span-test")
    session_id = session["session_id"]

    # Load document
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "Line 1\nLine 2\nLine 3\n"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Store artifact with inline span (not pre-created)
    artifact_result = await _artifact_store(
        server,
        session_id=session_id,
        type="extraction",
        content={"extracted": "Line 2"},
        span={"doc_id": doc_id, "start": 7, "end": 14},  # "Line 2\n"
        provenance={"model": "test"}
    )

    # Artifact should have span_id
    assert "span_id" in artifact_result
    assert artifact_result["span_id"] is not None

    # Retrieve to verify span was created
    artifact = await _artifact_get(
        server,
        session_id=session_id,
        artifact_id=artifact_result["artifact_id"]
    )

    assert artifact["span"] is not None
    assert artifact["span"]["start"] == 7
    assert artifact["span"]["end"] == 14


@pytest.mark.asyncio
async def test_search_results_include_span_references(server: RLMServer):
    """Test that search results include proper span references."""

    session = await _session_create(server, name="search-span-test")
    session_id = session["session_id"]

    # Load documents
    await _docs_load(
        server,
        session_id=session_id,
        sources=[
            {"type": "inline", "content": "Python is great for data science"},
            {"type": "inline", "content": "JavaScript powers the web"},
        ]
    )

    # Search
    result = await _search_query(
        server,
        session_id=session_id,
        query="Python data",
        method="bm25",
        limit=5
    )

    # Verify span references in results
    assert len(result["matches"]) > 0
    match = result["matches"][0]

    assert "span" in match
    assert "doc_id" in match["span"]
    assert "start" in match["span"]
    assert "end" in match["span"]
    assert "doc_id" in match


@pytest.mark.asyncio
async def test_peek_includes_content_hash(server: RLMServer):
    """Test that peek results include content hash for integrity."""

    session = await _session_create(server, name="peek-hash-test")
    session_id = session["session_id"]

    content = "Hello, World!"
    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": content}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]
    expected_hash = load_result["loaded"][0]["content_hash"]

    # Peek should include content hash
    peek_result = await _docs_peek(server, session_id=session_id, doc_id=doc_id)

    assert "content_hash" in peek_result
    assert peek_result["content_hash"] == expected_hash


@pytest.mark.asyncio
async def test_chunk_spans_have_content_hashes(server: RLMServer):
    """Test that chunk spans include content hashes."""

    session = await _session_create(server, name="chunk-hash-test")
    session_id = session["session_id"]

    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "x" * 1000}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Create chunks
    chunk_result = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={"type": "fixed", "chunk_size": 100}
    )

    # Each span should have content_hash
    for span_info in chunk_result["spans"]:
        assert "content_hash" in span_info
        assert span_info["content_hash"] is not None
        assert len(span_info["content_hash"]) == 64  # SHA256


@pytest.mark.asyncio
async def test_artifact_list_preserves_provenance(server: RLMServer):
    """Test that artifact.list includes provenance metadata."""

    session = await _session_create(server, name="list-provenance-test")
    session_id = session["session_id"]

    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": "test content"}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Store multiple artifacts with different provenance
    await _artifact_store(
        server,
        session_id=session_id,
        type="summary",
        content={"text": "Summary 1"},
        span={"doc_id": doc_id, "start": 0, "end": 4},
        provenance={"model": "sonnet", "tool": "summarize"}
    )

    await _artifact_store(
        server,
        session_id=session_id,
        type="extraction",
        content={"extracted": "content"},
        span={"doc_id": doc_id, "start": 5, "end": 12},
        provenance={"model": "haiku", "tool": "extract"}
    )

    # List artifacts
    list_result = await _artifact_list(server, session_id=session_id)

    assert len(list_result["artifacts"]) == 2

    # Verify provenance is included
    for artifact in list_result["artifacts"]:
        assert "provenance" in artifact
        if artifact["type"] == "summary":
            assert artifact["provenance"]["model"] == "sonnet"
        elif artifact["type"] == "extraction":
            assert artifact["provenance"]["model"] == "haiku"


@pytest.mark.asyncio
async def test_session_level_artifact_no_span(server: RLMServer):
    """Test that session-level artifacts can exist without span reference."""

    session = await _session_create(server, name="session-artifact-test")
    session_id = session["session_id"]

    # Store session-level artifact (no span)
    artifact_result = await _artifact_store(
        server,
        session_id=session_id,
        type="metadata",
        content={"summary": "This session analyzed 10 files"},
        provenance={"model": "opus"}
    )

    # Should succeed without span
    assert "artifact_id" in artifact_result
    assert artifact_result["span_id"] is None

    # Retrieve
    artifact = await _artifact_get(
        server,
        session_id=session_id,
        artifact_id=artifact_result["artifact_id"]
    )

    assert artifact["span_id"] is None
    assert artifact["span"] is None
    assert artifact["content"]["summary"] == "This session analyzed 10 files"
