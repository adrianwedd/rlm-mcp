# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RLM-MCP is a Model Context Protocol (MCP) server implementing the Recursive Language Model pattern from Zhang et al. (2025). It enables LLMs to process arbitrarily long contexts by treating prompts as external environment objects rather than feeding them directly into the neural network.

The server exposes tools for session management, document loading, chunking, BM25 search, and artifact storage. Claude Code acts as the orchestrator, making LLM subcalls while the MCP server provides the "world" (document storage, search, etc.).

## Common Commands

### Development Setup
```bash
# Install with uv (recommended)
uv sync --extra dev

# Or with pip (editable install required for tests)
pip install -e ".[dev]"
```

### Running Tests
```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_integration.py

# Run specific test function
uv run pytest tests/test_integration.py::test_session_lifecycle
```

### Type Checking and Linting
```bash
# Type checking
uv run mypy src/

# Linting
uv run ruff check src/

# Auto-fix linting issues
uv run ruff check --fix src/
```

### Running the Server
```bash
# Start MCP server (stdio mode)
uv run rlm-mcp

# Or via Python module
uv run python -m rlm_mcp.server
```

### Validating Tool Names
```bash
# Validate that all tools use canonical naming
uv run python validate_tools.py
```

Expected output: All 13 tools should have canonical names like `rlm.session.create` (not `rlm_session_create`).

### Testing with MCP Inspector
```bash
# Terminal 1: Start server
uv run rlm-mcp

# Terminal 2: Connect inspector
npx @anthropic/mcp-inspector
```

Verify that tool names appear as `rlm.session.create` (not `rlm_session_create`).

## Architecture

### Layer Separation

The codebase follows a strict separation between the MCP server (runtime/data plane) and Claude skills (policy/orchestration plane):

- **MCP Server** (`src/rlm_mcp/`): Provides tools, manages sessions, stores documents/spans/artifacts, builds indexes. Does NOT make LLM calls.
- **Claude Skills** (`skills/`): Policy layer defining when to use RLM, chunking strategies, and workflow patterns. Lives outside the MCP server.

The client (Claude Code) makes all LLM subcalls. The server is the "world" that the client queries.

### Core Components

**Tool Registration** (`src/rlm_mcp/server.py`):
- `RLMServer` wraps `FastMCP` (from `mcp.server.fastmcp`) for ergonomic tool registration
- `FastMCP` supports canonical naming via `.tool(name=...)` decorator
- `named_tool()` decorator handles SDK compatibility (strict mode requires `tool(name=...)` support)
- `tool_handler()` decorator wraps handlers for tracing and budget enforcement
- Tool categories: session, docs, chunk, span, search, artifact
- **Important**: Must use `FastMCP`, not base `Server` class, for canonical tool naming

**Storage Layer** (`src/rlm_mcp/storage/`):
- `Database`: Async SQLite wrapper for sessions, documents, spans, artifacts, traces
- `BlobStore`: Content-addressed storage using SHA256 hashes
- Migrations in `storage/migrations/` run automatically on startup

**Data Models** (`src/rlm_mcp/models.py`):
- Sessions track state, config, and tool call budgets
- Documents are immutable and content-addressed (doc_id vs content_hash distinction)
- Spans represent chunks with provenance (start/end offsets)
- Artifacts store derived results with span references for traceability

**BM25 Search** (`src/rlm_mcp/index/bm25.py`):
- Lazy-built on first `rlm.search.query` call
- Cached in `RLMServer._index_cache` dict per session
- Simple word-based tokenization for vendor neutrality

**Tool Categories** (`src/rlm_mcp/tools/`):
- `session.py`: Create, info, close operations
- `docs.py`: Load files/directories/inline content, list, peek
- `chunks.py`: On-demand chunking (fixed/lines/delimiter strategies)
- `search.py`: BM25 search with span references
- `artifacts.py`: Store/list/get derived results with provenance

### Key Design Principles

1. **Immutable documents**: Once loaded, content is content-addressed and never modified
2. **On-demand chunking**: Chunks created at query time with caching, not pre-chunked
3. **Lazy index building**: BM25 index built on first search, then cached
4. **DOS protection**: Hard caps on response sizes (`max_chars_per_response`, `max_chars_per_peek`)
5. **Vendor-neutral**: No hard dependencies on specific LLM tokenizers (4 chars/token heuristic)
6. **Session-scoped**: All operations require valid session_id; sessions have tool call budgets

## Configuration

User config: `~/.rlm-mcp/config.yaml`

```yaml
data_dir: ~/.rlm-mcp
default_max_tool_calls: 500
default_max_chars_per_response: 50000
default_max_chars_per_peek: 10000
allow_noncanonical_tool_names: false  # Strict mode (default)

# Logging configuration
log_level: "INFO"              # DEBUG, INFO, WARNING, ERROR
structured_logging: true       # JSON format (true) vs human-readable (false)
log_file: null                 # Optional file path for logs (null = console only)
```

**Tool naming modes**:
- Strict (default): Requires SDK with `tool(name=...)` support; fails fast if unavailable
- Compat: Falls back to function names with warning; use only for experimentation

## Testing Strategy

**Test Structure**:
- `tests/conftest.py`: Fixtures for temp dirs, config, database, blob store
- `tests/test_integration.py`: End-to-end workflows (session lifecycle, chunking, search)
- `tests/test_storage.py`: Database and blob store operations
- `tests/test_tools/`: Per-tool unit tests
- `tests/fixtures/`: Sample files for testing

**Async Testing**:
- Uses `pytest-asyncio` with `asyncio_mode = "auto"`
- Database fixtures use `@pytest_asyncio.fixture` with proper connect/close lifecycle
- Always use `async with` or explicit connect/close for database in tests

## Identifier Semantics

Understand the distinction between these identifiers:

- **session_id**: UUID for a processing session
- **doc_id**: Session-scoped stable identifier for documents
- **content_hash**: SHA256 of content; global blob store key for deduplication
- **span_id**: Session-scoped identifier for chunks (used in artifact provenance)

Documents are referenced by `doc_id` within a session but stored by `content_hash` globally.

## Tracing and Debugging

All tool calls are traced via `tool_handler()` decorator:
- Creates `TraceEntry` with operation, input, output, duration
- Stored in database for session replay/debugging
- Check `traces` table for operation history

To enable server logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Structured Logging

**Infrastructure** (`src/rlm_mcp/logging_config.py`):
- `StructuredFormatter`: JSON formatter for production observability
- `StructuredLogger`: Helper class for logging with context fields
- `correlation_id_var`: Context variable for tracking operations across async calls
- `configure_logging()`: One-time setup called in `run_server()`

**Configuration** (`~/.rlm-mcp/config.yaml`):
```yaml
log_level: "INFO"              # DEBUG, INFO, WARNING, ERROR
structured_logging: true       # JSON format (true) vs human-readable (false)
log_file: "/var/log/rlm.log"  # Optional file output (null = console only)
```

**JSON Log Format**:
```json
{
  "timestamp": "2026-01-15T10:30:45.123456Z",
  "level": "INFO",
  "logger": "rlm_mcp.server",
  "message": "Completed rlm.session.create",
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "session_id": "session-123",
  "operation": "rlm.session.create",
  "duration_ms": 42,
  "success": true
}
```

**Correlation IDs**:
- Every tool call gets a unique correlation_id (UUID)
- Set via `correlation_id_var.set(correlation_id)` in `tool_handler()` decorator
- Automatically included in all logs during that operation
- Always cleared in `finally` block to prevent leaks across operations
- Enables tracing related operations in production logs

**Usage in Code**:
```python
from rlm_mcp.logging_config import StructuredLogger

logger = StructuredLogger(__name__)

# Basic logging
logger.info("Operation completed")

# With context fields
logger.info(
    "Search completed",
    session_id="session-123",
    operation="rlm.search.query",
    duration_ms=150,
    result_count=5
)

# Error logging
logger.error(
    "Operation failed",
    session_id="session-123",
    operation="rlm.chunk.create",
    error=str(e),
    error_type=type(e).__name__
)
```

**Testing Structured Logs**:
- **IMPORTANT**: Use `StringIO` handler to capture formatted output
- **DO NOT** use `caplog.records[i].message` (gives raw message, not JSON)
```python
import io
import json
import logging
from rlm_mcp.logging_config import StructuredFormatter

def test_structured_logs():
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test")
    logger.addHandler(handler)
    logger.info("Test message")

    # Parse JSON output
    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "INFO"
    assert log_data["message"] == "Test message"
```

**Integration with tool_handler()**:
- `tool_handler()` decorator automatically logs:
  - Operation start with input parameter keys
  - Operation completion with duration and success status
  - Operation failures with error context
- All logs include correlation_id for tracing
- All logs include session_id if present
- See `src/rlm_mcp/server.py:217-313` for implementation

## Concurrency Model

**Single-Process Architecture**:
- Per-session locks prevent race conditions during index builds and session cleanup
- Locks are in-memory `asyncio.Lock` instances (NOT distributed locks)
- Suitable for single-process deployments (typical MCP server usage)
- **Multi-process NOT supported** without external coordination

**Lock Infrastructure** (`src/rlm_mcp/server.py`):
```python
class RLMServer:
    def __init__(self, config: ServerConfig | None = None):
        # Per-session locks (prevent race conditions)
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Lock manager lock (protects _session_locks dict)
        self._lock_manager_lock: asyncio.Lock = asyncio.Lock()

    async def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create lock for a session (creates if doesn't exist)."""
        async with self._lock_manager_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    async def release_session_lock(self, session_id: str) -> None:
        """Release lock after session close (frees memory)."""
        async with self._lock_manager_lock:
            self._session_locks.pop(session_id, None)
```

**What's Protected**:
1. **Index Cache Operations**: Concurrent searches on same session only build index once
2. **Session Close**: Prevents concurrent close attempts, ensures clean shutdown
3. **Budget Increments**: Atomic UPDATE with RETURNING clause prevents lost updates

**Usage Pattern**:
```python
# In tool implementations
lock = await server.get_session_lock(session_id)
async with lock:
    # Critical section: index build, cache update, etc.
    if session_id not in server._index_cache:
        index = build_index(...)  # Only one concurrent caller builds
        server._index_cache[session_id] = index
```

**Atomic Database Operations**:
```python
# Budget increments use atomic UPDATE
async def increment_tool_calls(self, session_id: str) -> int:
    async with self.conn.execute(
        "UPDATE sessions SET tool_calls_used = tool_calls_used + 1 "
        "WHERE id = ? RETURNING tool_calls_used",
        (session_id,),
    ) as cursor:
        row = await cursor.fetchone()
        await self.conn.commit()
        return row[0]
```

**Multi-Process Considerations**:
If you need multi-process deployments, you must implement external coordination:
- **Redis locks**: Use `redis-py` with `SET NX EX` for distributed locks
- **File locks**: Use `fcntl.flock()` on shared filesystem
- **Database advisory locks**: Use PostgreSQL/MySQL advisory locks
- **Distributed lock managers**: Use Consul, etcd, ZooKeeper

The current implementation will NOT coordinate across processes. Each process will have its own in-memory locks.

**Testing Concurrency**:
```python
# Example: Test concurrent budget increments
async def test_concurrent_budget_increments():
    num_increments = 50
    results = await asyncio.gather(*[
        server.db.increment_tool_calls(session_id)
        for _ in range(num_increments)
    ])

    # Verify atomic behavior
    session = await server.db.get_session(session_id)
    assert session.tool_calls_used == num_increments
    assert len(set(results)) == num_increments  # All unique
```

See `tests/test_concurrency.py` for comprehensive concurrency tests.

## Common Development Patterns

**Adding a new tool**:
1. Define handler function in appropriate `src/rlm_mcp/tools/*.py` file
2. Register with `@server.tool("rlm.category.action")` decorator
3. Wrap implementation with `@tool_handler("rlm.category.action")`
4. Add schema validation using Pydantic models
5. Add tests in `tests/test_tools/`

**Database schema changes**:
1. Create new migration in `src/rlm_mcp/storage/migrations/`
2. Name format: `{version:04d}_{description}.sql`
3. Include version tracking: `INSERT INTO schema_version (version) VALUES ({version});`
4. Migrations run automatically on server startup

**Adding chunk strategies**:
- Implement in `src/rlm_mcp/tools/chunks.py`
- Support `fixed`, `lines`, `delimiter` types
- Always respect `max_chunks` parameter
- Return spans with proper provenance (doc_id, start/end offsets)
