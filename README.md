# RLM-MCP: Recursive Language Model Server for Claude Code

**Status**: ✅ v0.1.3 - Validated & Production-Ready for Alpha Users

A Model Context Protocol (MCP) server implementing the Recursive Language Model pattern from [Zhang et al. (2025)](https://arxiv.org/abs/2512.24601), enabling LLMs to process arbitrarily long contexts by treating prompts as external environment objects.

## Key Insight

> Long prompts should not be fed into the neural network directly but should instead be treated as part of the environment that the LLM can symbolically interact with.

## Features

- **Session-based document management** — Load files, directories, or inline content
- **On-demand chunking** — Fixed, line-based, or delimiter-based strategies with caching
- **BM25 search** — Lazy-built, cached per session
- **Artifact storage** — Store derived results with span provenance
- **GitHub export** — Export sessions with secret scanning and redaction

## Status & Validation

**v0.1.3** has been comprehensively validated:

- ✅ All 13 tools implemented and tested
- ✅ MCP protocol integration confirmed with real client
- ✅ 51 test suite (46+ passing)
- ✅ Large corpus validated (1M+ chars loaded in <1s)
- ✅ BM25 search working (sub-second cached queries)
- ✅ Secret scanning operational (8/8 test patterns detected)
- ✅ Provenance tracking complete
- ✅ Error handling robust

See `MCP_VALIDATION.md` and `VALIDATION_REPORT.md` for detailed test results.

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
| `rlm.export` | `github` |

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
data_dir: ~/.rlm-mcp
default_max_tool_calls: 500
default_max_chars_per_response: 50000
default_max_chars_per_peek: 10000

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
│  • Session management                   │
│  • Document/span operations             │
│  • BM25 search (lazy, cached)           │
│  • Response size caps                   │
├─────────────────────────────────────────┤
│  Local Persistence                      │
│  • SQLite: sessions, docs, spans        │
│  • Blob store: content-addressed        │
├─────────────────────────────────────────┤
│  GitHub Export (optional)               │
│  • Secret scanning                      │
│  • Branch workflow                      │
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
