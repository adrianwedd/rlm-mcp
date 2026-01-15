# Solution Design Patches
**Based on Production Feedback**
**Date**: 2026-01-15

This document addresses the 8 sharp edges identified in code review before they become production issues.

---

## Patch 1: Atomic Index Writes + Better Metadata

### Problem
Pickle corruption if process dies mid-write. Stale index detection only checks `doc_count`, missing tokenizer changes or content edits.

### Solution

**File**: `src/rlm_mcp/index/persistence.py`

```python
import os
import tempfile
import hashlib
from pathlib import Path

class IndexPersistence:
    def save_index(
        self,
        session_id: str,
        index: Any,
        doc_count: int,
        tokenizer_name: str = "unicode",
    ) -> None:
        """Persist index to disk with atomic write."""
        session_dir = self.index_dir / session_id
        session_dir.mkdir(exist_ok=True)

        # Compute document hash fingerprint for staleness detection
        doc_fingerprint = self._compute_doc_fingerprint(session_id)

        # Save metadata
        metadata = {
            "version": "1.0",
            "created_at": datetime.utcnow().isoformat(),
            "doc_count": doc_count,
            "index_type": "bm25",
            "index_schema": 1,  # Bump when BM25 algorithm changes
            "tokenizer": tokenizer_name,
            "doc_fingerprint": doc_fingerprint,  # Hash of all content_hashes
        }

        # Write to temp files first
        index_path = session_dir / "bm25.pkl"
        metadata_path = session_dir / "metadata.json"

        # Atomic write for index
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=session_dir,
            delete=False
        ) as tmp_index:
            pickle.dump(index, tmp_index, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_index_path = tmp_index.name

        # Atomic write for metadata
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=session_dir,
            delete=False
        ) as tmp_meta:
            json.dump(metadata, tmp_meta, indent=2)
            tmp_meta_path = tmp_meta.name

        # Atomic rename (POSIX guarantees atomicity)
        os.replace(tmp_index_path, index_path)
        os.replace(tmp_meta_path, metadata_path)

    def load_index(self, session_id: str) -> tuple[Any, dict] | None:
        """Load index from disk if exists and valid."""
        session_dir = self.index_dir / session_id
        index_path = session_dir / "bm25.pkl"
        metadata_path = session_dir / "metadata.json"

        if not index_path.exists():
            return None

        # Load metadata
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)

        # Verify compatibility
        if metadata.get("index_schema") != 1:
            # Schema changed, invalidate
            self.invalidate_index(session_id)
            return None

        # Load index
        try:
            with open(index_path, 'rb') as f:
                index = pickle.load(f)
        except (pickle.UnpicklingError, EOFError) as e:
            # Corrupted index, delete it
            logger.warning(
                f"Corrupted index for session {session_id}: {e}",
                session_id=session_id
            )
            self.invalidate_index(session_id)
            return None

        return index, metadata

    def _compute_doc_fingerprint(self, session_id: str) -> str:
        """Compute hash of all document content_hashes for staleness detection."""
        # This requires database access, so we'll pass it in from caller
        # Placeholder - will be updated in actual implementation
        return ""

    async def compute_doc_fingerprint(
        self,
        server: "RLMServer",
        session_id: str
    ) -> str:
        """Compute fingerprint from document hashes."""
        docs = await server.db.get_documents_by_session(session_id)

        # Sort by doc_id for consistent ordering
        sorted_docs = sorted(docs, key=lambda d: d.id)

        # Concatenate all content_hashes
        combined = "".join(d.content_hash for d in sorted_docs)

        # Hash the result
        return hashlib.sha256(combined.encode()).hexdigest()

    async def is_index_stale(
        self,
        server: "RLMServer",
        session_id: str,
        metadata: dict
    ) -> bool:
        """Check if persisted index is stale."""
        # Check doc count
        doc_count = await server.db.count_documents(session_id)
        if metadata["doc_count"] != doc_count:
            return True

        # Check document fingerprint
        current_fingerprint = await self.compute_doc_fingerprint(server, session_id)
        if metadata.get("doc_fingerprint") != current_fingerprint:
            return True

        # Check tokenizer
        current_tokenizer = server.config.tokenizer
        if metadata.get("tokenizer") != current_tokenizer:
            return True

        return False
```

**Update**: `src/rlm_mcp/server.py`

```python
async def get_or_build_index(
    self,
    session_id: str
) -> tuple[Any, bool]:
    """Get cached index or load from disk or build new."""
    # Check in-memory cache first
    if session_id in self._index_cache:
        return self._index_cache[session_id], False

    # Try loading from disk
    persisted = self.index_persistence.load_index(session_id)
    if persisted is not None:
        index, metadata = persisted

        # Check for staleness
        if await self.index_persistence.is_index_stale(self, session_id, metadata):
            logger.info(
                "Persisted index stale, will rebuild",
                session_id=session_id,
                reason="doc_count/fingerprint/tokenizer mismatch"
            )
            self.index_persistence.invalidate_index(session_id)
        else:
            # Valid cached index
            self._index_cache[session_id] = index
            return index, False

    # Build required
    return None, True
```

**Update**: `src/rlm_mcp/tools/session.py`

```python
async def _session_close(...):
    # ... existing code ...

    # Persist index if exists
    if session_id in server._index_cache:
        doc_count = await server.db.count_documents(session_id)

        # Get current tokenizer config
        tokenizer_name = server.config.tokenizer

        server.index_persistence.save_index(
            session_id,
            server._index_cache[session_id],
            doc_count,
            tokenizer_name
        )
        del server._index_cache[session_id]
```

**Testing**:

```python
@pytest.mark.asyncio
async def test_atomic_write_prevents_corruption(server: RLMServer):
    """Test that killing during save doesn't corrupt index."""
    # This is hard to test deterministically, but we can verify:
    # 1. Temp files are cleaned up
    # 2. Either old index exists or new index exists (never partial)

    session = await _session_create(server, name="atomic-test")
    session_id = session["session_id"]

    # Build and persist index
    await _docs_load(server, session_id=session_id, sources=[...])
    await _search_query(server, session_id=session_id, query="test")
    await _session_close(server, session_id=session_id)

    # Verify no temp files left
    session_dir = server.index_persistence.index_dir / session_id
    temp_files = list(session_dir.glob("*.tmp"))
    assert len(temp_files) == 0

@pytest.mark.asyncio
async def test_tokenizer_change_invalidates_index(server: RLMServer):
    """Test that changing tokenizer invalidates persisted index."""
    session = await _session_create(server, name="tokenizer-test")
    session_id = session["session_id"]

    # Build with unicode tokenizer
    server.config.tokenizer = "unicode"
    await _docs_load(server, session_id=session_id, sources=[...])
    await _search_query(server, session_id=session_id, query="test")
    await _session_close(server, session_id=session_id)

    # Clear cache
    server._index_cache.clear()

    # Change tokenizer
    server.config.tokenizer = "cjk"

    # Next search should rebuild (not load stale index)
    result = await _search_query(server, session_id=session_id, query="test")
    assert result["index_built_this_call"] == True
```

---

## Patch 2: Multi-Process Lock Documentation + Future Path

### Problem
`asyncio.Lock()` only works within a single process. If we ever run multiple server processes, locks won't protect across them.

### Solution

**Current State**: Document the limitation clearly.

**File**: `CLAUDE.md` (add section)

```markdown
## Concurrency Model

### Current: Single-Process Safe

RLM-MCP v0.2.0 uses `asyncio.Lock()` for per-session concurrency control, which is safe for:
- Single MCP server process (stdio mode)
- Multiple concurrent tool calls from one client
- Multiple concurrent clients to one server process

**Not safe for**:
- Multiple server processes
- Container replicas
- Distributed deployments

### Future: Multi-Process Support

If you need multi-process concurrency (v0.4.0+), we'll add one of:

1. **File-based locks**: `fcntl.flock()` on index files (Unix-only)
2. **Database lease table**: Lightweight row locks in SQLite
3. **External coordinator**: Redis/etcd for distributed locks

For most MCP use cases (single user, stdio), single-process is sufficient.
```

**File**: `src/rlm_mcp/server.py` (add comment)

```python
class RLMServer:
    def __init__(self, config: ServerConfig | None = None):
        # ...

        # Per-session locks for critical sections
        # NOTE: asyncio.Lock is single-process only. For multi-process
        # deployments, see CLAUDE.md for upgrade path (v0.4.0+).
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock_manager_lock = asyncio.Lock()
```

**Future implementation option** (for reference, not implemented now):

```python
# Option 1: File locks (Unix-only, but simple)
import fcntl

class FileLockManager:
    def __init__(self, lock_dir: Path):
        self.lock_dir = lock_dir
        self.lock_dir.mkdir(exist_ok=True)

    @asynccontextmanager
    async def lock(self, session_id: str):
        lock_file = self.lock_dir / f"{session_id}.lock"

        # Open/create lock file
        fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY)

        try:
            # Acquire exclusive lock (blocks if held by another process)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            # Release lock
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

# Option 2: Database lease table
async def acquire_session_lock(self, session_id: str, timeout: int = 30):
    """Acquire lock via database lease."""
    expiry = datetime.utcnow() + timedelta(seconds=timeout)

    async with self.db.connection() as conn:
        # Try to acquire lock
        await conn.execute(
            """
            INSERT INTO session_locks (session_id, holder, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE
            SET holder = ?, expires_at = ?
            WHERE expires_at < ?
            """,
            (session_id, os.getpid(), expiry, os.getpid(), expiry, datetime.utcnow())
        )

        # Check if we got it
        cursor = await conn.execute(
            "SELECT holder FROM session_locks WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()

        if row and row[0] == os.getpid():
            return True
        return False
```

**Decision**: Ship v0.2.0 with single-process locks, add multi-process in v0.4.0 only if needed.

---

## Patch 3: Structured Logging Test Fix

### Problem
`caplog.records[i].message` is the raw message, not the formatted JSON. `json.loads(record.message)` will fail.

### Solution

**File**: `tests/test_logging.py`

```python
import json
import logging

@pytest.mark.asyncio
async def test_structured_logs(server: RLMServer, caplog):
    """Test that structured logs are emitted."""
    # Configure logging with structured formatter
    from rlm_mcp.logging_config import StructuredFormatter

    # Capture handler output, not raw records
    import io
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("rlm_mcp")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        session = await _session_create(server, name="log-test")

        # Get log output
        log_output = log_stream.getvalue()
        log_lines = [line for line in log_output.split('\n') if line.strip()]

        # Parse JSON logs
        logs = [json.loads(line) for line in log_lines]

        # Find session.create log
        create_logs = [l for l in logs if l.get("operation") == "rlm.session.create"]
        assert len(create_logs) > 0

        create_log = create_logs[0]
        assert create_log["level"] == "INFO"
        assert "session_id" in create_log
        assert "duration_ms" in create_log
        assert create_log["message"].startswith("Completed")

    finally:
        logger.removeHandler(handler)

def test_correlation_ids(caplog):
    """Test that correlation IDs are unique per operation."""
    import io
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("rlm_mcp")
    logger.addHandler(handler)

    try:
        # Make multiple tool calls
        # ... (simulate tool calls) ...

        log_output = log_stream.getvalue()
        logs = [json.loads(line) for line in log_output.split('\n') if line.strip()]

        correlation_ids = {l.get("correlation_id") for l in logs if "correlation_id" in l}

        # Each operation should have unique correlation ID
        assert len(correlation_ids) > 1

    finally:
        logger.removeHandler(handler)

@pytest.mark.asyncio
async def test_correlation_id_cleanup(server: RLMServer):
    """Test that correlation IDs don't leak across operations."""
    from rlm_mcp.server import correlation_id_var

    # Should be None initially
    assert correlation_id_var.get() is None

    # After operation, should be reset
    await _session_create(server, name="cleanup-test")
    assert correlation_id_var.get() is None
```

---

## Patch 4: Improved Unicode Tokenization

### Problem
Current `\w+` pattern drops hyphens, apostrophes, and splits contractions/compounds incorrectly.

### Solution

**File**: `src/rlm_mcp/index/tokenizers.py`

```python
class UnicodeTokenizer(Tokenizer):
    """Unicode-aware tokenizer with improved word boundary handling."""

    def __init__(self, min_length: int = 2):
        self.min_length = min_length

        # Pattern matches:
        # - Unicode letters (including accented)
        # - Numbers
        # - Internal hyphens/apostrophes (don't, state-of-the-art)
        # But not leading/trailing punctuation
        self.pattern = re.compile(
            r"[\w\u00C0-\u017F]+(?:[-'][\w\u00C0-\u017F]+)*",
            re.UNICODE
        )

    def tokenize(self, text: str) -> list[str]:
        # Normalize unicode (NFC form)
        text = unicodedata.normalize('NFC', text)

        # Extract words
        tokens = self.pattern.findall(text.lower())

        # Filter by length
        return [t for t in tokens if len(t) >= self.min_length]

# Add test cases:
def test_unicode_tokenizer_handles_contractions():
    tokenizer = UnicodeTokenizer()

    text = "don't can't state-of-the-art real-time"
    tokens = tokenizer.tokenize(text)

    # Should preserve internal punctuation
    assert "don't" in tokens
    assert "can't" in tokens
    assert "state-of-the-art" in tokens
    assert "real-time" in tokens

def test_unicode_tokenizer_strips_leading_trailing():
    tokenizer = UnicodeTokenizer()

    text = "-leading trailing- 'quoted'"
    tokens = tokenizer.tokenize(text)

    # Should not include punctuation-only tokens
    assert "-" not in tokens
    assert "'" not in tokens

    # But should get the words
    assert "leading" in tokens
    assert "trailing" in tokens
    assert "quoted" in tokens
```

**Configuration option**:

```yaml
# ~/.rlm-mcp/config.yaml
tokenizer: unicode  # simple, unicode, cjk
tokenizer_options:
  min_length: 2
  preserve_hyphens: true  # Keep hyphenated compounds
  preserve_apostrophes: true  # Keep contractions
```

---

## Patch 5: Better Highlight Data Structure

### Problem
Current highlighting concatenates terms into strings, which loses information about which terms matched.

### Solution

**File**: `src/rlm_mcp/index/bm25.py`

```python
def _find_highlights(
    self,
    context: str,
    query_tokens: list[str],
    context_offset: int
) -> list[dict]:
    """Find positions of query terms in context with improved structure."""
    highlights = []
    context_lower = context.lower()

    for term in query_tokens:
        # Find all occurrences of term
        pos = 0
        while True:
            idx = context_lower.find(term, pos)
            if idx == -1:
                break

            # Check if it's a word boundary match (more accurate)
            # Simple version: just record position
            # Advanced: use regex with \b boundaries

            highlights.append({
                "start": idx,
                "end": idx + len(term),
                "term": term,
                "term_index": query_tokens.index(term),  # Which query term
            })
            pos = idx + 1

    # Sort by position
    highlights.sort(key=lambda h: h["start"])

    # Merge overlapping highlights, preserving term list
    merged = []
    for highlight in highlights:
        if merged and highlight["start"] <= merged[-1]["end"]:
            # Overlapping - extend previous
            merged[-1]["end"] = max(merged[-1]["end"], highlight["end"])

            # Track which terms matched in this region
            if "terms" not in merged[-1]:
                merged[-1]["terms"] = [merged[-1]["term"]]
                del merged[-1]["term"]

            if highlight["term"] not in merged[-1]["terms"]:
                merged[-1]["terms"].append(highlight["term"])
        else:
            # New region
            merged.append(highlight)

    return merged
```

**Response format**:

```json
{
  "matches": [
    {
      "context": "The Python programming language is widely used...",
      "highlights": [
        {
          "start": 4,
          "end": 10,
          "term": "python",
          "term_index": 0
        },
        {
          "start": 11,
          "end": 22,
          "terms": ["programming", "language"],
          "term_index": 1
        }
      ]
    }
  ]
}
```

---

## Patch 6: Batch Loading Memory Safety

### Problem
`asyncio.gather()` on 100+ large files can spike RAM usage.

### Solution

**File**: `src/rlm_mcp/tools/docs.py`

```python
async def _docs_load(
    server: "RLMServer",
    session_id: str,
    sources: list[dict[str, Any]],
    max_concurrent: int = 20,  # Limit concurrent loads
) -> dict[str, Any]:
    # ... existing validation ...

    # Load documents with concurrency limit
    semaphore = asyncio.Semaphore(max_concurrent)

    async def load_with_limit(source_spec: dict[str, Any]) -> Document:
        async with semaphore:
            source_type = source_spec.get("type")
            if source_type == "inline":
                return await _load_inline(server, session_id, ...)
            elif source_type == "file":
                return await _load_file(server, session_id, ...)
            # ... etc

    # Create tasks
    load_tasks = [load_with_limit(source) for source in sources]

    # Execute with concurrency limit
    loaded_docs = await asyncio.gather(*load_tasks, return_exceptions=True)

    # ... rest of processing ...
```

**Configuration**:

```python
class ServerConfig(BaseModel):
    # ... existing ...

    max_concurrent_loads: int = 20  # Limit concurrent file loads
    max_file_size_mb: int = 100  # Reject files larger than this
```

**Test**:

```python
@pytest.mark.asyncio
async def test_batch_loading_memory_bounded(server: RLMServer):
    """Test that batch loading doesn't spike memory."""
    import resource

    # Track memory usage
    start_mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # Load 100 documents
    docs = [
        {"type": "inline", "content": "x" * 100_000}  # 100KB each
        for _ in range(100)
    ]

    await _docs_load(server, session_id=session_id, sources=docs)

    end_mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # Memory increase should be reasonable (not 100 * 100KB in RAM)
    mem_increase_mb = (end_mem - start_mem) / 1024 / 1024
    assert mem_increase_mb < 50  # Bounded increase
```

---

## Patch 7: Optimized AST Offset Computation

### Problem
Summing line lengths repeatedly is O(n²) for offset lookups.

### Solution

**File**: `src/rlm_mcp/tools/chunks.py`

```python
class ASTChunkStrategy(BaseChunkStrategy):
    """Chunk Python code by top-level definitions with optimized offsets."""

    def __init__(self, min_chunk_size: int = 100):
        self.min_chunk_size = min_chunk_size

    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Fall back to entire content if not valid Python
            yield (0, len(content))
            return

        # Precompute line start offsets (O(n) once)
        line_starts = self._compute_line_starts(content)

        # Get all top-level nodes
        nodes = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                start_line = node.lineno - 1  # 0-indexed
                end_line = node.end_lineno  # This is 1-indexed in ast

                # Convert to char offsets (O(1) lookup)
                start_offset = line_starts[start_line]
                end_offset = line_starts[end_line] if end_line < len(line_starts) else len(content)

                nodes.append((start_offset, end_offset))

        # Sort by start position
        nodes.sort()

        if not nodes:
            yield (0, len(content))
            return

        # Yield preamble (imports, module docstring, etc)
        if nodes[0][0] > self.min_chunk_size:
            yield (0, nodes[0][0])

        # Yield each definition
        for start, end in nodes:
            if end - start >= self.min_chunk_size:
                yield (start, end)

    @staticmethod
    def _compute_line_starts(content: str) -> list[int]:
        """Precompute starting offset of each line."""
        line_starts = [0]

        for i, char in enumerate(content):
            if char == '\n':
                line_starts.append(i + 1)

        return line_starts
```

**Test to verify correctness**:

```python
def test_ast_offset_computation():
    """Verify AST offsets match actual content."""
    code = '''import os

def hello():
    print("hello")

class Calculator:
    def add(self, a, b):
        return a + b
'''

    strategy = ASTChunkStrategy()
    chunks = list(strategy.chunk(code))

    # Verify each chunk is valid Python
    for start, end in chunks:
        chunk_code = code[start:end]
        # Should at least be parseable (might not be complete module)
        try:
            compile(chunk_code, '<chunk>', 'exec')
        except SyntaxError:
            # Some chunks (like standalone methods) won't compile,
            # but should at least start with 'def' or 'class'
            assert chunk_code.strip().startswith(('def', 'class', 'import', 'from'))
```

---

## Patch 8: Better Error Messages - Fix Span Lookup Bug

### Problem
In `_span_get`, when span is None, we try to access `span.document_id`, which will crash.

### Solution

**File**: `src/rlm_mcp/tools/chunks.py`

```python
from rlm_mcp.errors import SpanNotFoundError, DocumentNotFoundError

async def _span_get(
    server: "RLMServer",
    session_id: str,
    span_ids: list[str],
) -> dict[str, Any]:
    """Get span contents with provenance."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    max_chars = server.get_char_limit(session, "response")
    total_chars = 0
    spans_output = []

    for span_id in span_ids:
        span = await server.db.get_span(span_id)
        if span is None:
            # Span not found - try to get helpful context
            # We can't get doc from span.document_id (span is None)
            # So we need to provide helpful error without it
            raise SpanNotFoundError(
                span_id=span_id,
                session_id=session_id,
                hint="This chunk may have been deleted or never created. "
                     "Check that you're using the correct span_id from chunk.create results."
            )

        # Get document to verify session and get content_hash
        doc = await server.db.get_document(span.document_id)
        if doc is None:
            raise DocumentNotFoundError(
                doc_id=span.document_id,
                session_id=session_id,
                context=f"referenced by span {span_id}"
            )

        if doc.session_id != session_id:
            raise ValueError(
                f"Span '{span_id}' belongs to session '{doc.session_id}', "
                f"not '{session_id}'. Spans cannot be accessed across sessions."
            )

        # Get content
        content = server.blobs.get_slice(
            doc.content_hash, span.start_offset, span.end_offset
        )
        if content is None:
            raise ValueError(
                f"Content not found for chunk in document '{doc.name}' "
                f"(span {span_id}). The blob store may be corrupted."
            )

        # ... rest of logic ...
```

**Better approach**: Store chunk index in database for user-friendly errors

**File**: `src/rlm_mcp/models.py`

```python
class Span(BaseModel):
    # ... existing fields ...

    chunk_index: int | None = None  # Position in chunking sequence (0-based)
```

**File**: `src/rlm_mcp/tools/chunks.py`

```python
async def _chunk_create(...):
    # ... existing code ...

    for i, (start, end) in enumerate(chunker.chunk(content)):
        # ... existing code ...

        span = Span(
            document_id=doc_id,
            start_offset=start,
            end_offset=end,
            content_hash=content_hash,
            strategy=strategy_obj,
            chunk_index=i,  # Store index for better errors
        )
        await server.db.create_span(span)
```

**Now error messages can say**:

```python
raise SpanNotFoundError(
    span_id=span_id,
    session_id=session_id,
    document_name=doc.name,
    chunk_index=span.chunk_index,  # e.g., "Chunk #3"
)
```

**Error message becomes**:

```
Chunk #3 from document 'server.py' not found in session 'abc-123'.
It may have been deleted or the session was closed.
```

---

## Summary: Implementation Order

### Immediate (before any v0.2.0 work)

1. **Patch 3**: Fix logging tests (prevents test failures)
2. **Patch 8**: Fix span error bug (prevents crashes)

### Priority 1 (v0.2.0 blockers)

3. **Patch 1**: Atomic writes + fingerprinting (data safety)
4. **Patch 2**: Document multi-process limitation (clarity)

### Priority 2 (v0.2.1 quality)

5. **Patch 4**: Better tokenization (search quality)
6. **Patch 5**: Better highlights (UX improvement)
7. **Patch 6**: Batch memory safety (stability)

### Priority 3 (v0.3.0 optimization)

8. **Patch 7**: AST offset optimization (performance)

---

## Testing Strategy

For each patch, add:

1. **Unit tests**: Specific to the patched component
2. **Integration tests**: Ensure patch doesn't break existing flow
3. **Regression tests**: Verify the bug can't resurface

**Example test matrix for Patch 1**:

| Test Case | What It Verifies |
|-----------|------------------|
| `test_atomic_write_prevents_corruption` | No partial writes |
| `test_tokenizer_change_invalidates_index` | Fingerprint detects staleness |
| `test_doc_edit_invalidates_index` | Content changes detected |
| `test_corrupted_index_rebuilds` | Pickle errors handled |
| `test_index_load_performance` | Load time < 100ms for 1M chars |

---

## Risk Assessment

| Patch | Risk | Impact if Skipped |
|-------|------|-------------------|
| #1 (Atomic writes) | Low | Index corruption on crashes |
| #2 (Multi-process docs) | None | Confusion if users scale |
| #3 (Log tests) | None | Tests fail |
| #4 (Tokenization) | Low | Poor search for non-ASCII |
| #5 (Highlights) | None | Less useful search UX |
| #6 (Batch memory) | Medium | OOM on large batches |
| #7 (AST perf) | None | Slower chunking (still works) |
| #8 (Error bug) | **High** | Crashes on missing spans |

**Critical Path**: Patch #8 → Patch #3 → Patch #1 → everything else

---

## Configuration Changes

Add to `~/.rlm-mcp/config.yaml`:

```yaml
# Existing fields...

# Index persistence (Patch 1)
index_schema_version: 1
index_fingerprint_enabled: true

# Batch loading (Patch 6)
max_concurrent_loads: 20
max_file_size_mb: 100

# Tokenization (Patch 4)
tokenizer_options:
  min_length: 2
  preserve_hyphens: true
  preserve_apostrophes: true
```

---

## Migration Notes

**v0.1.3 → v0.2.0**:

- Old persisted indexes (if any) will be invalidated on first load due to missing `index_schema` field
- No data migration required
- Config file backward compatible

**v0.2.0 → v0.2.1**:

- Changing `tokenizer` setting will invalidate all persisted indexes (expected)
- Search results may differ slightly due to improved tokenization
- Highlight format adds `terms` array for merged regions
