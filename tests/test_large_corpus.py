"""Large corpus tests - validate 1M+ char processing without OOM."""

import pytest
from rlm_mcp.server import RLMServer
from rlm_mcp.tools.session import _session_create
from rlm_mcp.tools.docs import _docs_load, _docs_list
from rlm_mcp.tools.search import _search_query
from rlm_mcp.tools.chunks import _chunk_create
import time


@pytest.mark.asyncio
async def test_1m_char_corpus_loading(server: RLMServer):
    """Test loading and querying 1M+ character corpus."""

    # Create session
    session = await _session_create(server, name="large-corpus-test")
    session_id = session["session_id"]

    # Generate 1M char corpus (simulate large codebase)
    # 10 files x 100K chars each
    files = []
    for i in range(10):
        content = f"# File {i}\n" + ("x" * 99990)
        files.append({
            "type": "inline",
            "content": content,
            "metadata": {"file_index": i}
        })

    # Load all files
    start = time.time()
    result = await _docs_load(server, session_id=session_id, sources=files)
    load_time = time.time() - start

    assert len(result["loaded"]) == 10
    assert result["total_chars"] >= 999_000  # Close to 1M (10 * ~100K)
    print(f"Loaded {result['total_chars']} chars in {load_time:.2f}s")

    # Verify all docs are listed
    list_result = await _docs_list(server, session_id=session_id)
    assert list_result["total"] == 10


@pytest.mark.asyncio
async def test_bm25_search_performance(server: RLMServer):
    """Test BM25 search performance on 1M+ char corpus."""

    # Create session with 500K chars of realistic content
    session = await _session_create(server, name="search-perf-test")
    session_id = session["session_id"]

    # Create 5 documents with different searchable content
    docs = [
        {"type": "inline", "content": "def calculate_sum(a, b):\n    return a + b\n" * 1000},
        {"type": "inline", "content": "class UserManager:\n    def create_user(self):\n        pass\n" * 1000},
        {"type": "inline", "content": "async def fetch_data():\n    await asyncio.sleep(1)\n" * 1000},
        {"type": "inline", "content": "def validate_email(email):\n    return '@' in email\n" * 1000},
        {"type": "inline", "content": "class ConfigParser:\n    def parse(self):\n        pass\n" * 1000},
    ]

    await _docs_load(server, session_id=session_id, sources=docs)

    # First search builds index
    start = time.time()
    result1 = await _search_query(
        server,
        session_id=session_id,
        query="calculate sum",
        method="bm25",
        limit=5
    )
    first_search_time = time.time() - start

    # Note: index_built_this_call no longer tracked with persistence layer
    assert result1["index_built"]
    assert len(result1["matches"]) > 0
    print(f"First search (with index build): {first_search_time:.2f}s")

    # Second search uses cached index
    start = time.time()
    result2 = await _search_query(
        server,
        session_id=session_id,
        query="validate email",
        method="bm25",
        limit=5
    )
    cached_search_time = time.time() - start

    # Note: index_built_this_call no longer tracked with persistence layer
    # Cache hit should be fast
    assert cached_search_time < 1.0, f"Cached search took {cached_search_time:.2f}s, expected < 1s"
    print(f"Cached search: {cached_search_time:.3f}s")


@pytest.mark.asyncio
async def test_chunking_large_document(server: RLMServer):
    """Test chunking strategies on large documents."""

    session = await _session_create(server, name="chunking-test")
    session_id = session["session_id"]

    # Create 200K char document
    large_doc = "Line " + str(1) + "\n"
    for i in range(2, 10000):
        large_doc += f"Line {i}\n"

    load_result = await _docs_load(
        server,
        session_id=session_id,
        sources=[{"type": "inline", "content": large_doc}]
    )
    doc_id = load_result["loaded"][0]["doc_id"]

    # Test fixed-size chunking
    start = time.time()
    chunk_result = await _chunk_create(
        server,
        session_id=session_id,
        doc_id=doc_id,
        strategy={
            "type": "fixed",
            "chunk_size": 10000,
            "overlap": 100
        }
    )
    chunk_time = time.time() - start

    assert len(chunk_result["spans"]) > 0
    assert chunk_time < 1.0, f"Chunking took {chunk_time:.2f}s, expected < 1s"

    # Verify no gaps in coverage
    total_coverage = sum(
        span["span"]["end"] - span["span"]["start"]
        for span in chunk_result["spans"]
    )
    # Should cover at least 90% of document (accounting for overlap)
    assert total_coverage >= len(large_doc) * 0.9


@pytest.mark.asyncio
async def test_memory_efficiency_many_documents(server: RLMServer):
    """Test memory efficiency with many small documents."""

    session = await _session_create(server, name="many-docs-test")
    session_id = session["session_id"]

    # Create 100 documents of 10K chars each (1M total)
    docs = []
    for i in range(100):
        content = f"Document {i}\n" + ("content " * 1600)
        docs.append({"type": "inline", "content": content})

    # Load in batches to avoid timeout
    batch_size = 20
    total_loaded = 0

    for i in range(0, len(docs), batch_size):
        batch = docs[i:i+batch_size]
        result = await _docs_load(server, session_id=session_id, sources=batch)
        total_loaded += len(result["loaded"])

    assert total_loaded == 100

    # Verify all are listed
    list_result = await _docs_list(server, session_id=session_id)
    assert list_result["total"] == 100

    # Calculate total chars from documents
    total_chars = sum(doc["length_chars"] for doc in list_result["documents"])
    assert total_chars >= 900_000  # Should be close to 1M


@pytest.mark.asyncio
async def test_search_result_quality(server: RLMServer):
    """Test that BM25 returns relevant results."""

    session = await _session_create(server, name="search-quality-test")
    session_id = session["session_id"]

    # Load documents with distinct topics
    docs = [
        {
            "type": "inline",
            "content": """
            The Python programming language is a high-level, interpreted language.
            Python is known for its clear syntax and readability.
            Many developers choose Python for web development and data science.
            """ * 50
        },
        {
            "type": "inline",
            "content": """
            JavaScript is the language of the web browser.
            JavaScript enables interactive web pages and dynamic content.
            Modern JavaScript includes async/await syntax for asynchronous programming.
            """ * 50
        },
        {
            "type": "inline",
            "content": """
            The Rust programming language focuses on memory safety.
            Rust prevents memory errors at compile time.
            Systems programming is a key use case for Rust.
            """ * 50
        }
    ]

    await _docs_load(server, session_id=session_id, sources=docs)

    # Search for Python-specific content
    result = await _search_query(
        server,
        session_id=session_id,
        query="Python programming language data science",
        method="bm25",
        limit=3
    )

    # First result should be the Python document
    assert len(result["matches"]) > 0
    first_match = result["matches"][0]
    assert "Python" in first_match["context"]

    # Score should be positive
    assert first_match["score"] > 0
