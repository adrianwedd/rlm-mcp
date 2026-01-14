# RLM-MCP: Recursive Language Model Server for Claude Code

## Solution Design Document
**Version:** 0.1.3
**Date:** 2026-01-14
**Status:** ✅ COMPLETE — Implemented, Tested & Validated

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 0.1.3 | 2026-01-14 | Tool naming enforcement (strict/compat mode); `ToolNamingError` exception; `allow_noncanonical_tool_names` config; index invalidation on `docs.load`; budget bypass tightened to exact match |
| 0.1.2 | 2026-01-14 | Spans as first-class provenance; DOS protection (max_chars_* caps); doc_id vs content_hash clarified; BM25 lazy+cached lifecycle; export branch naming + idempotency; canonical tool naming |
| 0.1.1 | 2026-01-14 | Token estimation fix (vendor-neutral); span references in search; budget model correction; added `session.close`, `docs.list`; v0.1 scope cuts |
| 0.1.0 | 2026-01-14 | Initial draft |

---

## 1. Executive Summary

### 1.1 Problem Statement

Large language models face fundamental limitations when processing long contexts:
- **Context rot**: Quality degrades as context length increases, even within stated limits
- **Hard limits**: Physical token limits prevent processing of large codebases, document corpora, or logs
- **Task complexity scaling**: More complex tasks (aggregation, pairwise reasoning) fail at shorter lengths than simpler tasks (needle-in-haystack)

### 1.2 Proposed Solution

Implement a **Recursive Language Model (RLM)** architecture as an MCP server for Claude Code, based on Zhang et al. (2025). The key insight: *treat prompts as part of the external environment* rather than feeding them directly into the neural network.

The RLM exposes the same interface as an LLM (string in → string out) but:
1. Loads context into persistent memory outside the model's attention
2. Allows programmatic access (slice, search, chunk)
3. Enables recursive sub-LLM calls over context snippets (client-managed)
4. Maintains execution state across iterations

### 1.3 Key Results from Paper

| Benchmark | Base GPT-5 | RLM(GPT-5) | Improvement |
|-----------|------------|------------|-------------|
| BrowseComp+ (1K docs, 6-11M tokens) | 0%* | 91.33% | N/A (base couldn't fit) |
| OOLONG (131K tokens) | 44% | 56.5% | +28.4% |
| OOLONG-Pairs (32K tokens) | 0.04% | 58% | +1450× |
| CodeQA (23K-4.2M tokens) | 24%* | 62% | +158% |

*Asterisk indicates context limit issues

---

## 2. Architecture

### 2.1 Layered Design

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Skills                                               │
│  • When to invoke RLM (context thresholds, task patterns)    │
│  • Chunking strategy selection                               │
│  • Query decomposition patterns                              │
│  • Cost/budget guardrails                                    │
│  • Subcall model recommendations (advisory)                  │
├─────────────────────────────────────────────────────────────┤
│  MCP Server (RLM Runtime)                                    │
│  • Session management                                        │
│  • Document/context store                                    │
│  • Span addressing & chunking                                │
│  • Search/index (BM25, lazy-built)                          │
│  • Trace logging                                             │
│  • Tool-call budget enforcement                              │
│  • Response size caps (DOS protection)                       │
├─────────────────────────────────────────────────────────────┤
│  Local Persistence Layer                                     │
│  • SQLite: sessions, documents, spans, metadata              │
│  • Blob store: content-addressed by sha256                   │
│  • Index store: BM25 indexes (lazy, cached)                  │
├─────────────────────────────────────────────────────────────┤
│  GitHub Export (Optional, Explicit) [v0.1: export only]      │
│  • manifest.json + artifacts + trace.jsonl                   │
│  • Branch workflow (never touches main)                      │
│  • Secret scanning + optional redaction                      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Core Design Principles

1. **Local-first**: All reads/writes hit local storage. GitHub is export only (v0.1).
2. **Client-managed subcalls**: MCP server is the "world"; orchestrator (Claude Code) makes LLM calls.
3. **Immutable documents**: Once ingested, docs are content-addressed and never modified.
4. **On-demand chunking**: Chunk at query time with cached results, not pre-chunked.
5. **Explicit persistence**: Sessions are ephemeral by default; GitHub export is opt-in.
6. **Vendor-neutral**: No hard dependencies on specific LLM tokenizers or APIs.
7. **DOS protection**: Hard caps on response sizes prevent runaway context injection.

### 2.3 Why Not GitHub as Live Backing Store?

| Concern | Impact |
|---------|--------|
| Latency | 100-500ms per API call; RLM traces may have 100+ operations |
| Rate limits | 5000 requests/hour (authenticated); easily exhausted |
| Partial failures | Mid-trace failures corrupt session state |
| Secrets | Accidental commit of API keys, tokens, PII |
| Concurrency | No transactional guarantees across multiple files |

---

## 3. Data Model

### 3.1 Core Entities

```
Session
├── id: uuid                    # Session-scoped stable identifier
├── created_at: timestamp
├── closed_at: timestamp (nullable)
├── config: {budgets, model_hints, char_limits}
├── status: active | completed | exported
└── documents[]

Document
├── id: uuid                    # Session-scoped stable identifier (doc_id)
├── session_id: fk
├── content_hash: sha256        # Blob store address; used for dedup across sessions
├── source: {type: file|url|inline, path?, url?}
├── length_chars: int
├── length_tokens_est: int      # ceil(chars / 4), or client-provided hint
├── metadata: {filename, mimetype, ...}
└── spans[]

Span
├── id: uuid                    # Span identifier for artifact provenance
├── document_id: fk
├── start_offset: int
├── end_offset: int
├── strategy: {type: fixed|lines|delimiter, params}
├── content_hash: sha256        # Hash of span content; for integrity/dedup
└── artifacts[]

Artifact
├── id: uuid
├── span_id: fk (nullable)      # Session-level if null
├── type: summary | extraction | classification | custom
├── content: json
├── provenance: {tool, model, timestamp, span_ref?}

Trace
├── id: uuid
├── session_id: fk
├── timestamp: datetime
├── operation: tool_name        # Canonical: rlm.<category>.<action>
├── input: json
├── output: json
├── duration_ms: int
├── client_reported: json (nullable)  # Optional subcall metrics from client
```

### 3.2 Identifier Semantics

| Identifier | Scope | Purpose |
|------------|-------|---------|
| `doc_id` | Session | Stable reference within session; used in tool calls |
| `content_hash` | Global | Content-addressed blob store key; enables dedup |
| `span_id` | Session | Stable reference for artifact provenance |
| `span.content_hash` | Global | Integrity check; same content = same hash |

**Key invariant**: The same file loaded in two sessions gets different `doc_id`s but the same `content_hash`.

### 3.3 Token Estimation (Vendor-Neutral)

**Problem**: `tiktoken` is OpenAI's tokenizer and does not accurately count tokens for Claude models.

**Solution**: 
- Store `length_chars` (ground truth)
- Compute `length_tokens_est = ceil(chars / 4)` as default heuristic
- Accept optional `token_count_hint` from client when available
- Never present estimates as authoritative counts

```python
def estimate_tokens(chars: int, hint: int | None = None) -> int:
    """Vendor-neutral token estimation."""
    if hint is not None:
        return hint
    return math.ceil(chars / 4)  # ~4 chars/token is reasonable cross-model
```

### 3.4 Storage Layout (Local)

```
~/.rlm-mcp/
├── rlm.db                    # SQLite: sessions, docs, spans, artifacts, traces
├── blobs/
│   └── {content_hash[:2]}/
│       └── {content_hash}    # Raw document content (content-addressed)
├── indexes/
│   └── {session_id}/
│       └── bm25.idx          # Per-session BM25 index (lazy-built)
└── config.yaml               # Server configuration
```

### 3.5 BM25 Index Lifecycle

**Strategy**: Lazy build + cached + invalidate on mutation

1. **On `rlm.docs.load`**: Documents stored, index invalidated (if exists)
2. **On first `rlm.search.query` with `method: bm25`**: Index built synchronously, cached in memory
3. **On subsequent BM25 searches**: Cached index reused
4. **On `rlm.session.close`**: Index metadata persisted (for potential future reload)

**Index invalidation (v0.1.3)**: When `docs.load` adds new documents, any existing BM25 index for that session is deleted from cache. This prevents stale search results that exclude newly loaded documents.

```python
async def _docs_load(server, session_id, sources):
    # ... load documents ...
    
    # Invalidate stale index
    if session_id in server._index_cache:
        del server._index_cache[session_id]
    
    return result
```

```python
class SessionIndex:
    """Lazy-loaded BM25 index for a session."""
    
    def __init__(self, session_id: str):
        self._bm25: BM25Okapi | None = None
        self._built = False
    
    def ensure_built(self, documents: list[Document], blobs: BlobStore) -> None:
        if self._built:
            return
        corpus = [self._tokenize(blobs.get(doc.content_hash)) for doc in documents]
        self._bm25 = BM25Okapi(corpus)
        self._built = True
    
    def search(self, query: str, limit: int) -> list[ScoredDoc]:
        if not self._built:
            raise IndexNotBuiltError("Call ensure_built first")
        # ... search implementation
```

### 3.6 GitHub Export Format

```
.rlm/
└── sessions/
    └── {timestamp}_{session_id[:8]}/
        ├── manifest.json     # Session config + doc inventory + artifact list
        ├── docs/             # Only if --include-docs flag
        │   ├── {doc_id}.meta.json
        │   └── {doc_id}.txt
        ├── artifacts/
        │   └── {artifact_id}.json
        └── traces/
            └── trace.jsonl   # Full operation log
```

**Branch naming**: `rlm/session/{timestamp}-{session_id[:8]}`

Example: `rlm/session/20260114T083210Z-abc12345`

**Export safety rules:**
- Default: metadata + artifacts + traces (NO raw docs)
- `--include-docs` flag required to export document content
- `--redact` flag scrubs secrets from artifacts + trace contexts (not just docs)
- Pre-export secret scan (regex patterns for API keys, tokens)
- Export creates branch, never commits to main directly
- v0.1: User opens PR manually (no automation)
- **Idempotent**: Re-exporting same session overwrites same branch path

---

## 4. MCP Tool Specification

### 4.1 Tool Naming Convention

All tools use canonical format: `rlm.<category>.<action>`

| Category | Tools |
|----------|-------|
| `rlm.session` | `create`, `info`, `close` |
| `rlm.docs` | `load`, `list`, `peek` |
| `rlm.chunk` | `create` |
| `rlm.span` | `get` |
| `rlm.search` | `query` |
| `rlm.artifact` | `store`, `list`, `get` |
| `rlm.export` | `github` |

### 4.1.1 Tool Naming Enforcement (v0.1.3)

Canonical tool names (`rlm.session.create`) are a **product property**, not an SDK accident. Skills and clients depend on exact names for discovery and invocation.

**Problem**: Not all MCP SDK versions support `@server.tool(name="...")`. Without explicit naming, tools register under function names (e.g., `rlm_session_create`), breaking client expectations silently.

**Solution**: Strict/compat mode with explicit configuration.

| Mode | Behavior | Config |
|------|----------|--------|
| **Strict** (default) | Fail fast with `ToolNamingError` if SDK doesn't support `name=` | `allow_noncanonical_tool_names: false` |
| **Compat** | Fall back to function names with one-time warning | `allow_noncanonical_tool_names: true` |

```python
class ToolNamingError(Exception):
    """Raised when canonical tool naming fails in strict mode."""
    pass

def named_tool(mcp_server: Server, canonical_name: str, *, strict: bool = True):
    """Register a tool with canonical naming.
    
    Args:
        canonical_name: e.g., "rlm.session.create"
        strict: If True, fail fast when SDK doesn't support name=.
    
    Raises:
        ToolNamingError: In strict mode, if SDK doesn't support name=.
    """
    def decorator(func):
        try:
            return mcp_server.tool(name=canonical_name)(func)
        except TypeError as e:
            if "name" not in str(e):
                raise
            if strict:
                raise ToolNamingError(
                    f"MCP SDK doesn't support tool(name=...). "
                    f"Cannot register '{canonical_name}'. "
                    f"Set allow_noncanonical_tool_names=True to use compat mode."
                ) from e
            # Compat mode: one-time warning, fallback to function name
            _warn_once("MCP SDK doesn't support tool(name=...). Falling back...")
            return mcp_server.tool()(func)
    return decorator
```

**Recommendation**: Use strict mode (default). Only enable compat mode for experimentation with older SDKs.

### 4.2 Server Configuration

Server-level configuration in `~/.rlm-mcp/config.yaml`:

```yaml
data_dir: ~/.rlm-mcp
default_max_tool_calls: 500
default_max_chars_per_response: 50000
default_max_chars_per_peek: 10000

# Tool naming: strict by default (fail if SDK doesn't support canonical names)
# Only set to true for experimentation with older MCP SDKs
allow_noncanonical_tool_names: false
```

### 4.3 Session Configuration Defaults

```json
{
  "max_tool_calls": 500,
  "max_chars_per_response": 50000,
  "max_chars_per_peek": 10000,
  "chunk_cache_enabled": true,
  "model_hints": null
}
```

**DOS Protection**: `max_chars_per_response` and `max_chars_per_peek` are enforced server-side on all content-returning tools.

### 4.4 Session Tools

#### `rlm.session.create`
```json
{
  "name": "rlm.session.create",
  "description": "Create a new RLM session for processing large contexts",
  "input_schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "Human-readable session name"},
      "config": {
        "type": "object",
        "properties": {
          "max_tool_calls": {"type": "integer", "default": 500},
          "max_chars_per_response": {"type": "integer", "default": 50000},
          "max_chars_per_peek": {"type": "integer", "default": 10000},
          "chunk_cache_enabled": {"type": "boolean", "default": true},
          "model_hints": {
            "type": "object",
            "description": "Advisory metadata for client subcall decisions",
            "properties": {
              "root_model": {"type": "string"},
              "subcall_model": {"type": "string"},
              "bulk_model": {"type": "string"}
            }
          }
        }
      }
    },
    "required": []
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "created_at": {"type": "string", "format": "date-time"},
      "config": {"type": "object"}
    }
  }
}
```

#### `rlm.session.info`
```json
{
  "name": "rlm.session.info",
  "description": "Get session statistics and configuration",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"}
    },
    "required": ["session_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "name": {"type": "string"},
      "status": {"enum": ["active", "completed", "exported"]},
      "created_at": {"type": "string"},
      "closed_at": {"type": "string", "nullable": true},
      "document_count": {"type": "integer"},
      "total_chars": {"type": "integer"},
      "total_tokens_est": {"type": "integer"},
      "tool_calls_used": {"type": "integer"},
      "tool_calls_remaining": {"type": "integer"},
      "index_built": {"type": "boolean"},
      "config": {"type": "object"}
    }
  }
}
```

#### `rlm.session.close`
```json
{
  "name": "rlm.session.close",
  "description": "Mark session complete and persist index metadata",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"}
    },
    "required": ["session_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "status": {"const": "completed"},
      "closed_at": {"type": "string"},
      "summary": {
        "type": "object",
        "properties": {
          "documents": {"type": "integer"},
          "spans": {"type": "integer"},
          "artifacts": {"type": "integer"},
          "tool_calls": {"type": "integer"}
        }
      }
    }
  }
}
```

### 4.4 Document Tools

#### `rlm.docs.load`
```json
{
  "name": "rlm.docs.load",
  "description": "Load documents into the session context",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "sources": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "type": {"enum": ["file", "directory", "glob", "inline"]},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "recursive": {"type": "boolean", "default": false},
            "include_pattern": {"type": "string"},
            "exclude_pattern": {"type": "string"},
            "token_count_hint": {"type": "integer", "description": "Client-provided token estimate"}
          }
        }
      }
    },
    "required": ["session_id", "sources"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "loaded": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "doc_id": {"type": "string"},
            "content_hash": {"type": "string"},
            "source": {"type": "string"},
            "length_chars": {"type": "integer"},
            "length_tokens_est": {"type": "integer"}
          }
        }
      },
      "errors": {"type": "array", "items": {"type": "string"}},
      "total_chars": {"type": "integer"},
      "total_tokens_est": {"type": "integer"}
    }
  }
}
```

#### `rlm.docs.list`
```json
{
  "name": "rlm.docs.list",
  "description": "List documents in session",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "limit": {"type": "integer", "default": 100},
      "offset": {"type": "integer", "default": 0}
    },
    "required": ["session_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "documents": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "doc_id": {"type": "string"},
            "content_hash": {"type": "string"},
            "source": {"type": "string"},
            "length_chars": {"type": "integer"},
            "length_tokens_est": {"type": "integer"},
            "span_count": {"type": "integer"}
          }
        }
      },
      "total": {"type": "integer"},
      "has_more": {"type": "boolean"}
    }
  }
}
```

#### `rlm.docs.peek`
```json
{
  "name": "rlm.docs.peek",
  "description": "View a portion of a document. Enforces max_chars_per_peek.",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "doc_id": {"type": "string"},
      "start": {"type": "integer", "default": 0},
      "end": {"type": "integer", "description": "Exclusive end offset; -1 for end of doc"}
    },
    "required": ["session_id", "doc_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "span": {
        "type": "object",
        "description": "Span reference for provenance",
        "properties": {
          "doc_id": {"type": "string"},
          "start": {"type": "integer"},
          "end": {"type": "integer"}
        }
      },
      "content_hash": {"type": "string", "description": "Hash of returned content"},
      "truncated": {"type": "boolean"},
      "total_length": {"type": "integer"}
    }
  }
}
```

### 4.5 Chunking Tools

#### `rlm.chunk.create`
```json
{
  "name": "rlm.chunk.create",
  "description": "Chunk a document using a specified strategy",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "doc_id": {"type": "string"},
      "strategy": {
        "type": "object",
        "properties": {
          "type": {"enum": ["fixed", "lines", "delimiter"]},
          "chunk_size": {"type": "integer", "description": "For fixed: chars per chunk"},
          "line_count": {"type": "integer", "description": "For lines: lines per chunk"},
          "overlap": {"type": "integer", "default": 0},
          "delimiter": {"type": "string", "description": "For delimiter strategy"},
          "max_chunks": {"type": "integer"}
        },
        "required": ["type"]
      }
    },
    "required": ["session_id", "doc_id", "strategy"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "spans": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "span_id": {"type": "string"},
            "index": {"type": "integer"},
            "span": {
              "type": "object",
              "properties": {
                "doc_id": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"}
              }
            },
            "length_chars": {"type": "integer"},
            "content_hash": {"type": "string"},
            "preview": {"type": "string", "description": "First 100 chars"}
          }
        }
      },
      "total_spans": {"type": "integer"},
      "cached": {"type": "boolean"}
    }
  }
}
```

#### `rlm.span.get`
```json
{
  "name": "rlm.span.get",
  "description": "Retrieve the content of one or more spans. Enforces max_chars_per_response.",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "span_ids": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["session_id", "span_ids"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "spans": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "span_id": {"type": "string"},
            "span": {
              "type": "object",
              "description": "Span reference for provenance",
              "properties": {
                "doc_id": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"}
              }
            },
            "content": {"type": "string"},
            "content_hash": {"type": "string"},
            "truncated": {"type": "boolean"}
          }
        }
      },
      "total_chars_returned": {"type": "integer"}
    }
  }
}
```

### 4.6 Search Tool

#### `rlm.search.query`
```json
{
  "name": "rlm.search.query",
  "description": "Search documents. BM25 index is lazy-built on first use. Enforces max_chars_per_response on total context.",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "query": {"type": "string"},
      "method": {"enum": ["bm25", "regex", "literal"], "default": "bm25"},
      "doc_ids": {"type": "array", "items": {"type": "string"}, "description": "Limit to specific docs"},
      "limit": {"type": "integer", "default": 10},
      "context_chars": {"type": "integer", "default": 200, "description": "Chars around each match"}
    },
    "required": ["session_id", "query"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "matches": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "doc_id": {"type": "string"},
            "span": {
              "type": "object",
              "description": "Span reference for this match",
              "properties": {
                "doc_id": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"}
              }
            },
            "span_id": {"type": "string", "nullable": true, "description": "If span was persisted"},
            "score": {"type": "number"},
            "context": {"type": "string"},
            "highlight_start": {"type": "integer", "description": "Offset within context"},
            "highlight_end": {"type": "integer"}
          }
        }
      },
      "total_matches": {"type": "integer"},
      "index_built_this_call": {"type": "boolean"}
    }
  }
}
```

### 4.7 Artifact Tools

#### `rlm.artifact.store`
```json
{
  "name": "rlm.artifact.store",
  "description": "Store a derived artifact with provenance",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "span_id": {"type": "string", "nullable": true, "description": "Optional; null for session-level"},
      "span": {
        "type": "object",
        "nullable": true,
        "description": "Alternative: provide span reference directly (will create/find span)",
        "properties": {
          "doc_id": {"type": "string"},
          "start": {"type": "integer"},
          "end": {"type": "integer"}
        }
      },
      "type": {"type": "string", "description": "summary, extraction, classification, custom"},
      "content": {"type": "object"},
      "provenance": {
        "type": "object",
        "properties": {
          "model": {"type": "string"},
          "prompt_hash": {"type": "string"}
        }
      }
    },
    "required": ["session_id", "type", "content"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "artifact_id": {"type": "string"},
      "span_id": {"type": "string", "nullable": true}
    }
  }
}
```

#### `rlm.artifact.list`
```json
{
  "name": "rlm.artifact.list",
  "description": "List artifacts for a session or span",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "span_id": {"type": "string"},
      "type": {"type": "string"}
    },
    "required": ["session_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "artifacts": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "artifact_id": {"type": "string"},
            "span_id": {"type": "string", "nullable": true},
            "type": {"type": "string"},
            "created_at": {"type": "string"}
          }
        }
      }
    }
  }
}
```

#### `rlm.artifact.get`
```json
{
  "name": "rlm.artifact.get",
  "description": "Retrieve artifact content",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "artifact_id": {"type": "string"}
    },
    "required": ["session_id", "artifact_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "artifact_id": {"type": "string"},
      "span_id": {"type": "string", "nullable": true},
      "span": {
        "type": "object",
        "nullable": true,
        "properties": {
          "doc_id": {"type": "string"},
          "start": {"type": "integer"},
          "end": {"type": "integer"}
        }
      },
      "type": {"type": "string"},
      "content": {"type": "object"},
      "provenance": {"type": "object"},
      "created_at": {"type": "string"}
    }
  }
}
```

### 4.8 Export Tool

#### `rlm.export.github`
```json
{
  "name": "rlm.export.github",
  "description": "Export session to GitHub repository branch",
  "input_schema": {
    "type": "object",
    "properties": {
      "session_id": {"type": "string"},
      "repo": {"type": "string", "description": "owner/repo or full URL"},
      "branch": {"type": "string", "description": "Default: rlm/session/{timestamp}-{session_id[:8]}"},
      "path": {"type": "string", "description": "Default: .rlm/sessions/{timestamp}_{session_id[:8]}"},
      "include_docs": {"type": "boolean", "default": false},
      "redact": {"type": "boolean", "default": false, "description": "Scrub secrets from artifacts/traces"}
    },
    "required": ["session_id", "repo"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "branch": {"type": "string"},
      "commit_sha": {"type": "string"},
      "export_path": {"type": "string", "description": "Actual path used"},
      "files_exported": {"type": "integer"},
      "warnings": {"type": "array", "items": {"type": "string"}},
      "secrets_found": {"type": "integer", "description": "Secrets detected (blocked or redacted)"}
    }
  }
}
```

---

## 5. Claude Skills Layer

### 5.1 Skill: RLM Activation

**File:** `SKILL.md` (to be placed in `/mnt/skills/user/rlm/`)

```markdown
# RLM (Recursive Language Model) Skill

## When to Use RLM

Activate RLM processing when ANY of the following apply:

1. **Context exceeds threshold**: Total input > 100K tokens (~400K chars)
2. **Multi-document reasoning**: Task requires synthesizing 5+ documents
3. **Aggregation tasks**: Questions about "all", "every", "count of", "list all pairs"
4. **Information-dense queries**: Answer depends on most/all of the input (not needle-in-haystack)
5. **Explicit request**: User asks to "analyze this codebase", "process these logs"

## Subcall Model Recommendations (Advisory)

The MCP server does not enforce model selection. These are recommendations:

| Root Model | Subcall Model | Bulk/Map Model | Use Case |
|------------|---------------|----------------|----------|
| Opus 4.5 | Sonnet 4.5 | Haiku 4.5 | Complex synthesis, deep analysis |
| Sonnet 4.5 | Haiku 4.5 | Haiku 4.5 | Standard workflows |
| Haiku 4.5 | Haiku 4.5 | Haiku 4.5 | Cost-sensitive, simple aggregation |

**Critical**: Haiku is for bulk passes only. Never invoke Haiku per-line — batch chunks.

## Workflow Pattern

1. **Initialize**: `rlm.session.create` with appropriate config
2. **Load**: `rlm.docs.load` documents
3. **Probe**: `rlm.docs.peek` at structure (first lines, format detection)
4. **Search**: `rlm.search.query` to find relevant sections (lazy-builds BM25)
5. **Chunk**: `rlm.chunk.create` with appropriate strategy
6. **Process**: `rlm.span.get` + client subcalls on spans
7. **Store**: `rlm.artifact.store` results with span provenance
8. **Synthesize**: Aggregate artifacts into final answer
9. **Close**: `rlm.session.close`

## Chunking Strategy Selection (v0.1)

| Content Type | Strategy | Params | Rationale |
|--------------|----------|--------|-----------|
| Source code | `delimiter` | `"\ndef \|\nclass "` | Preserve semantic units |
| Logs | `lines` | `line_count: 100, overlap: 10` | Temporal locality |
| Markdown | `delimiter` | `"\n## "` | Section boundaries |
| JSON/JSONL | `lines` | `line_count: 1` | Record-level processing |
| Plain text | `fixed` | `chunk_size: 50000, overlap: 500` | Balanced chunks |

## Cost Guardrails

- **Max tool calls per session**: 500 (default), warn at 400
- **Max chars per response**: 50K (server-enforced)
- **Max chars per peek**: 10K (server-enforced)
- **Batch aggressively**: Prefer 10 docs per subcall over 1 doc per subcall
- **Cache reuse**: Check artifacts before re-querying same span
- **Use span provenance**: Every artifact should trace back to its source span

## Anti-patterns to Avoid

❌ One subcall per line (Qwen3-Coder's failure mode — thousands of calls)
❌ Loading entire context into single subcall
❌ Ignoring cached artifacts from prior analysis
❌ Returning raw subcall outputs without synthesis
❌ Forgetting to close session
❌ Ignoring `truncated: true` in responses
```

### 5.2 Skill: RLM Query Decomposition

**File:** `patterns/decomposition.md`

```markdown
# Query Decomposition Patterns

## Pattern 1: Map-Reduce

For aggregation queries ("count all X", "list all Y"):

1. `rlm.chunk.create` documents
2. Map: Query each chunk (use Haiku, batch chunks)
3. `rlm.artifact.store` map results with span provenance
4. Reduce: `rlm.artifact.list` + aggregate into final answer (use Sonnet/Opus)

## Pattern 2: Filtered Search

For targeted queries ("find where X does Y"):

1. `rlm.search.query` for candidate locations
2. `rlm.docs.peek` at high-scoring matches
3. Client subcall on promising spans only
4. `rlm.artifact.store` findings with span provenance

## Pattern 3: Iterative Refinement

For exploratory queries ("explain how this system works"):

1. `rlm.docs.list` to understand corpus
2. `rlm.search.query` for key terms
3. Deep-dive subcalls on relevant spans
4. `rlm.artifact.store` synthesis as session-level artifact

## Pattern 4: Pairwise Reasoning

For relationship queries ("find all pairs where..."):

1. First pass: Classify each item → `rlm.artifact.store` per span
2. `rlm.artifact.list` to load classifications
3. Compute pairs programmatically from artifacts
4. Verify sample pairs with targeted subcalls
5. `rlm.artifact.store` final pairs as session artifact
```

---

## 6. Implementation Scope

### 6.1 v0.1 Scope (Explicit)

**In scope:**
- 13 tools (session: 3, docs: 3, chunk: 1, span: 1, search: 1, artifact: 3, export: 1)
- Chunking strategies: fixed, lines, delimiter
- Search methods: BM25 (lazy), regex, literal
- Local persistence: SQLite + blob store
- GitHub export (branch only)
- DOS protection (char caps)
- Trace logging
- Tool-call budgets

**Deferred to v0.2+:**
- `rlm.import.github`
- Semantic chunking
- PR automation
- MCP-brokered subcalls
- Vector search
- PDF/image support

---

## 7. Decision Log

| Question | Decision | Rationale |
|----------|----------|-----------|
| Sub-LLM provider | Client-managed (v0.1) | MCP stays "world", orchestrator makes calls |
| Chunk granularity | On-demand + cache | Flexibility, avoid storage explosion |
| Persistence | Local-first + explicit GitHub export | Reliability, no rate limit pain |
| Token counting | Vendor-neutral estimates | `tiktoken` doesn't work for Claude |
| Search results | Return span references | Enables seamless peek→artifact flow |
| Budget tracking | Tool calls (server), subcalls (client-reported) | Server enforces what it controls |
| Budget bypass | Exact match on `rlm.session.create` only | Suffix matching accidentally exempts `rlm.chunk.create` |
| GitHub workflow | Branch only (v0.1), manual PR | Reduce scope, ship sooner |
| Identifier semantics | doc_id = session-scoped, content_hash = global | Clear dedup vs reference separation |
| Index lifecycle | Lazy + cached + invalidate | Avoid upfront cost; invalidate on `docs.load` prevents stale results |
| Response sizes | Hard caps enforced server-side | DOS protection |
| Tool naming enforcement | Strict by default, compat opt-in | Canonical names are product property, not SDK accident |

---

## 8. Success Criteria

### Functional
- [ ] Process 1M+ char corpus without OOM
- [ ] 10x context window effective expansion (vs raw LLM)
- [ ] Sub-second peek/search operations (local)
- [ ] Successful GitHub export round-trip

### Quality
- [ ] <5% wasted tool calls (redundant queries)
- [ ] Zero secret leaks in default export mode (test suite)
- [ ] Trace reproducibility (same inputs → same tool outputs)
- [ ] All content returns include provenance (span, content_hash, truncated)

### Adoption
- [ ] Works with Claude Code out of the box
- [ ] Skills discoverable and effective
- [ ] Documentation sufficient for self-service

---

## Appendix A: Subcall Model Mapping

From the paper: GPT-5 used GPT-5-mini for subcalls (cost/capability tradeoff).

Recommended Claude mapping:

| Root Model | Subcall Model | Bulk Model | Notes |
|------------|---------------|------------|-------|
| Opus 4.5 | Sonnet 4.5 | Haiku 4.5 | Big decisions at root, semantic work in subcalls |
| Sonnet 4.5 | Haiku 4.5 | Haiku 4.5 | Standard workflow |
| Haiku 4.5 | Haiku 4.5 | Haiku 4.5 | Already efficient |

**Important**: This is advisory metadata in `session.config.model_hints`. The MCP server does not enforce model selection.

---

## Appendix B: Example Trace

```jsonl
{"ts":"2026-01-14T08:32:10Z","op":"rlm.session.create","in":{"name":"codebase-analysis"},"out":{"session_id":"abc12345"},"ms":12}
{"ts":"2026-01-14T08:32:11Z","op":"rlm.docs.load","in":{"sources":[{"type":"glob","path":"src/**/*.py"}]},"out":{"loaded":42,"total_chars":1847293},"ms":340}
{"ts":"2026-01-14T08:32:12Z","op":"rlm.search.query","in":{"query":"authentication","method":"bm25","limit":5},"out":{"matches":5,"index_built_this_call":true},"ms":890}
{"ts":"2026-01-14T08:32:13Z","op":"rlm.chunk.create","in":{"doc_id":"doc_017","strategy":{"type":"delimiter","delimiter":"\ndef "}},"out":{"total_spans":23},"ms":45}
{"ts":"2026-01-14T08:32:14Z","op":"rlm.span.get","in":{"span_ids":["span_017_003","span_017_007"]},"out":{"spans":2,"total_chars_returned":8420},"ms":8}
{"ts":"2026-01-14T08:32:20Z","op":"rlm.artifact.store","in":{"type":"summary","span_id":"span_017_003"},"out":{"artifact_id":"art_001"},"ms":5}
{"ts":"2026-01-14T08:35:00Z","op":"rlm.session.close","in":{"session_id":"abc12345"},"out":{"status":"completed","summary":{"documents":42,"spans":147,"artifacts":23,"tool_calls":89}},"ms":120}
```

---

## Appendix C: Secret Scanning Patterns

Default patterns for pre-export scanning:

```python
SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[\w-]{20,}', 'API Key'),
    (r'(?i)(secret|token|password|passwd|pwd)\s*[:=]\s*["\']?[\w-]{8,}', 'Secret/Password'),
    (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI API Key'),
    (r'sk-ant-[a-zA-Z0-9-]{20,}', 'Anthropic API Key'),
    (r'ghp_[a-zA-Z0-9]{36}', 'GitHub PAT'),
    (r'gho_[a-zA-Z0-9]{36}', 'GitHub OAuth'),
    (r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----', 'Private Key'),
    (r'(?i)bearer\s+[a-zA-Z0-9._-]{20,}', 'Bearer Token'),
]
```

---

*Document version: 0.1.3*  
*Status: Design Complete — Scaffold Implemented*  
*Next step: Run smoke tests via MCP Inspector*
