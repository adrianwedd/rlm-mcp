# RLM-MCP: Recursive Language Model Server for Claude Code

**Status**: ✅ v0.2.0 - Production-Ready for Team Environments

A Model Context Protocol (MCP) server implementing the Recursive Language Model pattern from [Zhang et al. (2025)](https://arxiv.org/abs/2512.24601), enabling LLMs to process arbitrarily long contexts by treating prompts as external environment objects.

**What's New in v0.2.0**: Persistent indexes survive restarts • Concurrent session safety • Structured logging with correlation IDs • Batch document loading for 2-3x faster imports

## Key Insight

> Long prompts should not be fed into the neural network directly but should instead be treated as part of the environment that the LLM can symbolically interact with.

## Features

### Core Capabilities
- **Session-based document management** — Load files, directories, or inline content with batch processing
- **On-demand chunking** — Fixed, line-based, or delimiter-based strategies with intelligent caching
- **BM25 search** — Lazy-built, persistently cached, survives server restarts
- **Artifact storage** — Store derived results with complete span provenance

### Production Features (v0.2.0)
- **Persistent indexes** — BM25 indexes saved to disk with atomic writes and corruption recovery
- **Concurrent session safety** — Per-session locks prevent race conditions in multi-user environments
- **Structured logging** — JSON output with correlation IDs for production observability
- **Batch document loading** — Concurrent file loading with memory-bounded semaphores (2-3x faster)

## Status & Validation

**v0.2.0** production-ready validation:

- ✅ **88/88 tests passing** (100% functionality + production features)
- ✅ **All 13 core tools** implemented with canonical naming
- ✅ **MCP protocol integration** confirmed with real clients
- ✅ **Large corpus tested** — 1M+ chars loaded and indexed
- ✅ **Performance validated** — Sub-second searches, <100ms index loads from disk
- ✅ **Concurrency tested** — 50 concurrent operations, no race conditions
- ✅ **Memory safety** — Bounded semaphores prevent OOM on large batches
- ✅ **Production logging** — JSON structured logs with correlation tracking

### Test Coverage
- Error handling: 13 tests
- Concurrency safety: 8 tests
- Index persistence: 10 tests
- Integration workflows: 14 tests
- Large corpus performance: 5 tests
- Structured logging: 13 tests
- Batch loading: 7 tests
- Provenance tracking: 8 tests
- Storage layer: 11 tests

See `MIGRATION_v0.1_to_v0.2.md` for upgrade guide.

## Installation

```bash
pip install rlm-mcp
```

Or with development dependencies:

```bash
pip install rlm-mcp[dev]
```

## Quick Start

```python
from rlm_mcp import run_server

# Start the MCP server
run_server()
```

## Tools

All tools use canonical naming: `rlm.<category>.<action>`

| Category | Tools |
|----------|-------|
| `rlm.session` | `create`, `info`, `close` |
| `rlm.docs` | `load`, `list`, `peek` |
| `rlm.chunk` | `create` |
| `rlm.span` | `get` |
| `rlm.search` | `query` |
| `rlm.artifact` | `store`, `list`, `get` |

## Workflow Pattern

1. **Initialize**: `rlm.session.create` with config
2. **Load**: `rlm.docs.load` documents
3. **Probe**: `rlm.docs.peek` at structure
4. **Search**: `rlm.search.query` to find relevant sections
5. **Chunk**: `rlm.chunk.create` with appropriate strategy
6. **Process**: `rlm.span.get` + client subcalls
7. **Store**: `rlm.artifact.store` results with provenance
8. **Close**: `rlm.session.close`

## Configuration

Configuration file: `~/.rlm-mcp/config.yaml`

```yaml
# Data storage
data_dir: ~/.rlm-mcp

# Session limits (per-session overridable)
default_max_tool_calls: 500
default_max_chars_per_response: 50000
default_max_chars_per_peek: 10000

# Batch loading (v0.2.0)
max_concurrent_loads: 20   # Max concurrent file loads (memory safety)
max_file_size_mb: 100      # Reject files larger than this

# Logging (v0.2.0)
log_level: "INFO"              # DEBUG, INFO, WARNING, ERROR
structured_logging: true       # JSON format (true) vs human-readable (false)
log_file: null                 # Optional: "/var/log/rlm-mcp.log"

# Tool naming: strict by default (fails if SDK doesn't support canonical names)
# Only set to true for experimentation with older MCP SDKs
allow_noncanonical_tool_names: false
```

### Tool Naming (Strict vs Compat Mode)

By default, RLM-MCP requires an MCP SDK that supports explicit tool naming (e.g., FastMCP). This ensures tools are discoverable as `rlm.session.create`, not `rlm_session_create`.

- **Strict mode (default)**: Server fails to start if SDK doesn't support `tool(name=...)`
- **Compat mode**: Falls back to function names with a warning. Use only for experimentation.

```yaml
# ~/.rlm-mcp/config.yaml
allow_noncanonical_tool_names: true  # Enable compat mode (not recommended)
```

## Logging (v0.2.0)

RLM-MCP produces structured JSON logs for production observability. Each operation gets a unique correlation ID for tracing related events.

### JSON Log Format

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

### Filtering Logs

```bash
# Filter by session
cat /var/log/rlm-mcp.log | jq 'select(.session_id == "session-123")'

# Filter by operation
cat /var/log/rlm-mcp.log | jq 'select(.operation == "rlm.search.query")'

# Track operation with correlation ID
cat /var/log/rlm-mcp.log | jq 'select(.correlation_id == "a1b2c3d4...")'

# Only errors
cat /var/log/rlm-mcp.log | jq 'select(.level == "ERROR")'
```

See `docs/LOGGING.md` for detailed logging guide.

## Session Config

```python
{
    "max_tool_calls": 500,           # Budget enforcement
    "max_chars_per_response": 50000, # DOS protection
    "max_chars_per_peek": 10000,     # DOS protection
    "chunk_cache_enabled": True,
    "model_hints": {                 # Advisory for client
        "root_model": "claude-opus-4-5-20251101",
        "subcall_model": "claude-sonnet-4-5-20250929",
        "bulk_model": "claude-haiku-4-5-20251001"
    }
}
```

## Architecture

```
┌─────────────────────────────────────────┐
│  Claude Skills (policy layer)           │
├─────────────────────────────────────────┤
│  MCP Server (RLM Runtime)               │
│  • Session management + concurrency     │
│  • Document/span operations             │
│  • BM25 search (lazy, persisted)        │
│  • Batch loading with semaphores        │
│  • Response size caps                   │
├─────────────────────────────────────────┤
│  Local Persistence (v0.2.0)             │
│  • SQLite: sessions, docs, spans        │
│  • Blob store: content-addressed        │
│  • Index cache: persistent BM25         │
│  • Structured logs: JSON + correlation  │
└─────────────────────────────────────────┘
```

## Design Principles

1. **Local-first** — All reads/writes hit local storage
2. **Client-managed subcalls** — MCP is the "world", client makes LLM calls
3. **Immutable documents** — Content-addressed, never modified
4. **On-demand chunking** — Chunk at query time, cache results
5. **DOS protection** — Hard caps on response sizes

## Development

```bash
# Clone and install with uv (recommended)
git clone https://github.com/yourorg/rlm-mcp.git
cd rlm-mcp
uv sync --extra dev

# Run tests
uv run pytest

# Or with pip (editable install required for tests)
pip install -e ".[dev]"
pytest

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
```

### Smoke Test with MCP Inspector

To validate tool discovery and schemas from a real client:

```bash
# Start server
uv run rlm-mcp

# In another terminal, use MCP Inspector
npx @anthropic/mcp-inspector
```

Verify:
- Tool names appear as `rlm.session.create`, not `rlm_session_create`
- Schemas match expected input/output structures
- `truncated` and `index_built` fields appear in responses

## License

MIT

## References

- Zhang, A. L., Kraska, T., & Khattab, O. (2025). *Recursive Language Models*. arXiv:2512.24601
