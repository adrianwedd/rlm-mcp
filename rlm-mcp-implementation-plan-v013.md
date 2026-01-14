# RLM-MCP Implementation Plan

**Version:** 0.1.3
**Date:** 2026-01-14
**Status:** ✅ COMPLETE — Validated & Ready for Alpha Release

---

## Refinements Applied (v0.1.2 → v0.1.3)

| # | Refinement | Resolution |
|---|------------|------------|
| 1 | Tool naming enforcement | Strict mode by default; `ToolNamingError` if SDK doesn't support `name=`; `allow_noncanonical_tool_names` config for compat mode |
| 2 | Index invalidation | `docs.load` deletes `_index_cache[session_id]` to prevent stale BM25 results |
| 3 | Budget bypass tightened | Changed from `.endswith("session.create")` to exact match `== "rlm.session.create"` |
| 4 | One-time warning | Compat mode logs fallback warning once per startup, not per-tool |

---

## Refinements Applied (v0.1.1 → v0.1.2)

| # | Refinement | Resolution |
|---|------------|------------|
| 1 | Spans as first-class provenance | `span.get` returns `{span, content_hash, truncated}` |
| 2 | DOS protection | `max_chars_per_response`, `max_chars_per_peek` in session config |
| 3 | doc_id vs content_hash | Explicitly documented: `doc_id` = session-scoped UUID, `content_hash` = blob store address |
| 4 | Index lifecycle | Lazy + cached on first BM25 query; `session.close` persists metadata |
| 5 | Export idempotency | Branch: `rlm/session/<timestamp>-<session_id[:8]>`; output includes `export_path` |
| 6 | Tool naming | Canonical: `rlm.<category>.<action>` everywhere |

---

## Epic Structure

```
E0: Foundation          → M0 (Week 1-2)
E1: Core Tools          → M1 (Week 2-3)  
E2: Search & Artifacts  → M2 (Week 3-4)
E3: Export              → M3 (Week 4-5)
E4: Skills & Ship       → M4 (Week 5-6)
```

---

## E0: Foundation

**Goal:** Repository scaffold, storage layer, MCP skeleton

### M0.1: Repository Setup
| Task | Description | Est. |
|------|-------------|------|
| T0.1.1 | Create repo with `uv init`, Python 3.11+ | 0.5h |
| T0.1.2 | Configure `pyproject.toml` (deps: `mcp`, `rank_bm25`, `PyGithub`) | 0.5h |
| T0.1.3 | Set up directory structure (see below) | 0.5h |
| T0.1.4 | Add `.gitignore`, `README.md` stub, `LICENSE` | 0.5h |

**Directory structure:**
```
rlm-mcp/
├── src/rlm_mcp/
│   ├── __init__.py
│   ├── server.py              # MCP entrypoint
│   ├── config.py              # Config loading + defaults
│   ├── models.py              # Pydantic models for entities
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── session.py         # rlm.session.*
│   │   ├── docs.py            # rlm.docs.*
│   │   ├── chunks.py          # rlm.chunk.*, rlm.span.*
│   │   ├── search.py          # rlm.search.*
│   │   ├── artifacts.py       # rlm.artifact.*
│   │   └── export.py          # rlm.export.*
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py        # SQLite operations
│   │   ├── blobs.py           # Content-addressed blob store
│   │   └── migrations/
│   │       └── 001_initial.sql
│   ├── index/
│   │   ├── __init__.py
│   │   └── bm25.py            # BM25 index implementation
│   └── export/
│       ├── __init__.py
│       ├── github.py          # GitHub API wrapper
│       └── secrets.py         # Secret scanning
├── schemas/
│   └── manifest.schema.json   # Export manifest schema
├── skills/
│   ├── SKILL.md
│   └── patterns/
│       └── decomposition.md
├── tests/
│   ├── conftest.py
│   ├── test_storage.py
│   ├── test_tools/
│   └── fixtures/
├── pyproject.toml
├── README.md
└── .gitignore
```

### M0.2: Storage Layer
| Task | Description | Est. |
|------|-------------|------|
| T0.2.1 | Define Pydantic models: `Session`, `Document`, `Span`, `Artifact`, `Trace` | 1h |
| T0.2.2 | Write SQLite schema (`001_initial.sql`) | 1h |
| T0.2.3 | Implement `database.py`: connection pool, migrations, CRUD | 2h |
| T0.2.4 | Implement `blobs.py`: content-addressed store with `{hash[:2]}/{hash}` layout | 1h |
| T0.2.5 | Unit tests for storage layer | 1h |

**SQLite Schema (001_initial.sql):**
```sql
-- Sessions
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'exported')),
    config JSON NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    tool_calls_used INTEGER DEFAULT 0
);

-- Documents  
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    content_hash TEXT NOT NULL,  -- blob store key
    source JSON NOT NULL,        -- {type, path?, url?}
    length_chars INTEGER NOT NULL,
    length_tokens_est INTEGER NOT NULL,
    metadata JSON,
    created_at TEXT NOT NULL
);

-- Spans
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content_hash TEXT NOT NULL,  -- for dedup/integrity
    strategy JSON NOT NULL,
    created_at TEXT NOT NULL
);

-- Artifacts
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    span_id TEXT REFERENCES spans(id),  -- nullable for session-level
    type TEXT NOT NULL,
    content JSON NOT NULL,
    provenance JSON,
    created_at TEXT NOT NULL
);

-- Traces
CREATE TABLE traces (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL,
    input JSON NOT NULL,
    output JSON NOT NULL,
    duration_ms INTEGER NOT NULL,
    client_reported JSON
);

-- Indexes
CREATE INDEX idx_documents_session ON documents(session_id);
CREATE INDEX idx_spans_document ON spans(document_id);
CREATE INDEX idx_artifacts_session ON artifacts(session_id);
CREATE INDEX idx_artifacts_span ON artifacts(span_id);
CREATE INDEX idx_traces_session ON traces(session_id);
```

### M0.3: MCP Server Skeleton
| Task | Description | Est. |
|------|-------------|------|
| T0.3.1 | Set up `server.py` with MCP SDK boilerplate | 1h |
| T0.3.2 | Implement config loading (`~/.rlm-mcp/config.yaml`) | 0.5h |
| T0.3.3 | Add tool registration framework (decorator pattern) | 1h |
| T0.3.4 | Implement trace logging middleware | 1h |
| T0.3.5 | Add tool-call budget enforcement middleware | 0.5h |

**M0 Total: ~12h**

---

## E1: Core Tools

**Goal:** Session management, document operations, chunking

### M1.1: Session Tools
| Task | Description | Est. |
|------|-------------|------|
| T1.1.1 | Implement `rlm.session.create` | 1h |
| T1.1.2 | Implement `rlm.session.info` | 0.5h |
| T1.1.3 | Implement `rlm.session.close` (with index flush) | 1h |
| T1.1.4 | Unit tests for session tools | 1h |

**Session config defaults:**
```python
DEFAULT_SESSION_CONFIG = {
    "max_tool_calls": 500,
    "max_chars_per_response": 50_000,
    "max_chars_per_peek": 10_000,
    "chunk_cache_enabled": True,
    "model_hints": None,  # advisory only
}
```

### M1.2: Document Tools
| Task | Description | Est. |
|------|-------------|------|
| T1.2.1 | Implement `rlm.docs.load` (file, directory, glob, inline) | 2h |
| T1.2.2 | Implement `rlm.docs.list` (with pagination) | 0.5h |
| T1.2.3 | Implement `rlm.docs.peek` (with `max_chars_per_peek` enforcement) | 1h |
| T1.2.4 | Add glob expansion + exclude pattern handling | 1h |
| T1.2.5 | Unit tests for document tools | 1h |

**Token estimation helper:**
```python
def estimate_tokens(chars: int, hint: int | None = None) -> int:
    """Vendor-neutral token estimation."""
    if hint is not None:
        return hint
    return math.ceil(chars / 4)
```

### M1.3: Chunking Tools
| Task | Description | Est. |
|------|-------------|------|
| T1.3.1 | Implement chunking strategies: `fixed`, `lines`, `delimiter` | 2h |
| T1.3.2 | Implement `rlm.chunk.create` (with caching) | 1h |
| T1.3.3 | Implement `rlm.span.get` (with provenance: span, content_hash, truncated) | 1h |
| T1.3.4 | Add overlap handling for chunking | 0.5h |
| T1.3.5 | Unit tests + property tests (no gaps, overlap invariants) | 1.5h |

**Chunking interface:**
```python
from abc import ABC, abstractmethod
from typing import Iterator

class ChunkStrategy(ABC):
    @abstractmethod
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        """Yield (start, end) offsets."""
        pass

class FixedChunkStrategy(ChunkStrategy):
    def __init__(self, chunk_size: int, overlap: int = 0):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        start = 0
        while start < len(content):
            end = min(start + self.chunk_size, len(content))
            yield (start, end)
            start = end - self.overlap if self.overlap else end

class LinesChunkStrategy(ChunkStrategy):
    def __init__(self, line_count: int, overlap: int = 0):
        self.line_count = line_count
        self.overlap = overlap
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        lines = content.split('\n')
        # ... implementation
        pass

class DelimiterChunkStrategy(ChunkStrategy):
    def __init__(self, delimiter: str):
        self.delimiter = delimiter
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        import re
        # Split on delimiter pattern, preserving positions
        pass
```

**M1 Total: ~14h**

---

## E2: Search & Artifacts

**Goal:** BM25 search, artifact storage with provenance

### M2.1: BM25 Index
| Task | Description | Est. |
|------|-------------|------|
| T2.1.1 | Implement lazy BM25 index builder (on first query) | 2h |
| T2.1.2 | Add index caching per session | 1h |
| T2.1.3 | Implement tokenization for BM25 | 0.5h |
| T2.1.4 | Unit tests for index | 1h |

**Index lifecycle:**
```python
class SessionIndex:
    def __init__(self, session_id: str, storage: Storage):
        self.session_id = session_id
        self.storage = storage
        self._bm25: BM25Okapi | None = None
        self._doc_map: list[tuple[str, int, int]] = []  # (doc_id, start, end)
    
    def ensure_built(self) -> None:
        """Lazy build on first access."""
        if self._bm25 is not None:
            return
        
        corpus = []
        for doc in self.storage.get_documents(self.session_id):
            content = self.storage.get_blob(doc.content_hash)
            tokens = self._tokenize(content)
            corpus.append(tokens)
            self._doc_map.append((doc.id, 0, len(content)))
        
        self._bm25 = BM25Okapi(corpus)
    
    def search(self, query: str, limit: int = 10) -> list[SearchMatch]:
        self.ensure_built()
        # ... search implementation
```

### M2.2: Search Tool
| Task | Description | Est. |
|------|-------------|------|
| T2.2.1 | Implement `rlm.search.query` with BM25 method | 1.5h |
| T2.2.2 | Add regex search method | 1h |
| T2.2.3 | Add literal search method | 0.5h |
| T2.2.4 | Return span references (not just offsets) | 0.5h |
| T2.2.5 | Enforce `max_chars_per_response` on context snippets | 0.5h |
| T2.2.6 | Unit tests for search | 1h |

**Search output with span references:**
```python
@dataclass
class SearchMatch:
    doc_id: str
    span: SpanRef  # {doc_id, start, end}
    span_id: str | None  # if persisted
    score: float
    context: str
    highlight_start: int  # offset within context
    highlight_end: int
```

### M2.3: Artifact Tools
| Task | Description | Est. |
|------|-------------|------|
| T2.3.1 | Implement `rlm.artifact.store` (with span or span_id provenance) | 1h |
| T2.3.2 | Implement `rlm.artifact.list` (filter by span, type) | 0.5h |
| T2.3.3 | Implement `rlm.artifact.get` | 0.5h |
| T2.3.4 | Add provenance validation (span must exist) | 0.5h |
| T2.3.5 | Unit tests for artifacts | 1h |

**M2 Total: ~12h**

---

## E3: Export

**Goal:** GitHub export with secret scanning

### M3.1: Export Format
| Task | Description | Est. |
|------|-------------|------|
| T3.1.1 | Define manifest.json schema | 1h |
| T3.1.2 | Implement manifest generation | 1h |
| T3.1.3 | Implement artifact export (JSON files) | 0.5h |
| T3.1.4 | Implement trace export (JSONL) | 0.5h |
| T3.1.5 | Add `--include-docs` doc export | 0.5h |

**Manifest schema:**
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "session", "documents", "artifacts", "traces"],
  "properties": {
    "version": {"const": "0.1"},
    "exported_at": {"type": "string", "format": "date-time"},
    "session": {
      "type": "object",
      "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "config": {"type": "object"},
        "created_at": {"type": "string"},
        "closed_at": {"type": "string"}
      }
    },
    "documents": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "doc_id": {"type": "string"},
          "content_hash": {"type": "string"},
          "source": {"type": "object"},
          "length_chars": {"type": "integer"},
          "included": {"type": "boolean"}
        }
      }
    },
    "artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "artifact_id": {"type": "string"},
          "file": {"type": "string"}
        }
      }
    },
    "traces": {
      "type": "object",
      "properties": {
        "file": {"type": "string"},
        "count": {"type": "integer"}
      }
    }
  }
}
```

### M3.2: Secret Scanning
| Task | Description | Est. |
|------|-------------|------|
| T3.2.1 | Implement secret pattern matching | 1h |
| T3.2.2 | Add `--redact` mode (scrub from artifacts/traces) | 1h |
| T3.2.3 | Add pre-export scan with warnings | 0.5h |
| T3.2.4 | Unit tests for secret scanning | 1h |

**Secret patterns:**
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

def scan_for_secrets(content: str) -> list[tuple[str, int, int, str]]:
    """Returns list of (match, start, end, pattern_name)."""
    findings = []
    for pattern, name in SECRET_PATTERNS:
        for match in re.finditer(pattern, content):
            findings.append((match.group(), match.start(), match.end(), name))
    return findings

def redact_secrets(content: str) -> tuple[str, int]:
    """Returns (redacted_content, secrets_found_count)."""
    findings = scan_for_secrets(content)
    redacted = content
    for match, start, end, name in sorted(findings, key=lambda x: -x[1]):
        redacted = redacted[:start] + f'[REDACTED:{name}]' + redacted[end:]
    return redacted, len(findings)
```

### M3.3: GitHub Export Tool
| Task | Description | Est. |
|------|-------------|------|
| T3.3.1 | Implement GitHub API wrapper (PyGithub) | 1h |
| T3.3.2 | Implement branch creation (`rlm/session/<timestamp>-<id[:8]>`) | 0.5h |
| T3.3.3 | Implement file upload to branch | 1h |
| T3.3.4 | Implement `rlm.export.github` tool | 1h |
| T3.3.5 | Add idempotency handling (overwrite vs new folder) | 0.5h |
| T3.3.6 | Integration test with GitHub API (mocked) | 1h |

**Export output:**
```python
@dataclass
class ExportResult:
    branch: str
    commit_sha: str
    export_path: str  # actual path used
    files_exported: int
    warnings: list[str]
    secrets_found: int
```

**M3 Total: ~12h**

---

## E4: Skills & Ship

**Goal:** Claude skills, integration tests, documentation

### M4.1: Skills
| Task | Description | Est. |
|------|-------------|------|
| T4.1.1 | Write `SKILL.md` (activation triggers, guardrails) | 1h |
| T4.1.2 | Write `patterns/decomposition.md` | 1h |
| T4.1.3 | Add example workflows in skill docs | 1h |

### M4.2: Integration Tests
| Task | Description | Est. |
|------|-------------|------|
| T4.2.1 | Create test fixtures (sample codebase, logs) | 1h |
| T4.2.2 | Write end-to-end workflow test: load → search → chunk → artifact | 2h |
| T4.2.3 | Write export round-trip test | 1h |
| T4.2.4 | Load test: 10M+ char corpus | 1h |

### M4.3: Documentation & Ship
| Task | Description | Est. |
|------|-------------|------|
| T4.3.1 | Write README.md (installation, quickstart) | 1h |
| T4.3.2 | Document all tools (API reference) | 1.5h |
| T4.3.3 | Add example Claude Code integration | 1h |
| T4.3.4 | Final review, version tag, release | 0.5h |

**M4 Total: ~12h**

---

## Summary

| Epic | Milestone | Est. Hours | Cumulative |
|------|-----------|------------|------------|
| E0 | Foundation | 12h | 12h |
| E1 | Core Tools | 14h | 26h |
| E2 | Search & Artifacts | 12h | 38h |
| E3 | Export | 12h | 50h |
| E4 | Skills & Ship | 12h | 62h |

**Total: ~62 hours**

At 5-10h/week: **6-12 weeks**

---

## Dependencies

```
[project]
name = "rlm-mcp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "rank-bm25>=0.2.2",
    "PyGithub>=2.1.0",
    "pydantic>=2.0.0",
    "aiosqlite>=0.19.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "hypothesis>=6.0.0",  # property tests
    "ruff>=0.1.0",
]
```

---

## Risk Checkpoints

| Milestone | Risk Check |
|-----------|------------|
| M0 | Storage layer handles 10M+ chars without OOM |
| M1 | Chunking produces no gaps, overlap works correctly |
| M2 | BM25 search returns in <1s for 1M char corpus |
| M3 | Secret scan catches all test patterns, redaction is complete |
| M4 | Claude Code integration works end-to-end |

---

## Definition of Done (v0.1)

- [x] All 13 tools implemented and tested
- [ ] Process 1M+ char corpus without OOM
- [ ] Sub-second peek/search on local storage
- [ ] Zero secret leaks in default export mode
- [ ] Skills work with Claude Code
- [ ] README sufficient for self-service setup

---

## Scaffold Status (v0.1.3)

| Component | Status | Notes |
|-----------|--------|-------|
| Repository structure | ✅ | All directories, pyproject.toml |
| Storage layer | ✅ | SQLite schema, blob store, migrations |
| MCP server skeleton | ✅ | Tool registration, middleware |
| Session tools (3) | ✅ | create, info, close |
| Document tools (3) | ✅ | load, list, peek |
| Chunk tools (2) | ✅ | chunk.create, span.get |
| Search tools (1) | ✅ | query (BM25, regex, literal) |
| Artifact tools (3) | ✅ | store, list, get |
| Export tools (1) | ✅ | github |
| Tool naming enforcement | ✅ | Strict/compat mode |
| Integration tests | ✅ | Happy path + tool naming tests |
| Skills | ✅ | SKILL.md + decomposition.md |

**Next steps:**
1. `uv sync --extra dev && uv run pytest`
2. Validate with MCP Inspector
3. Test with Claude Code

---

*Plan version: 0.1.3*  
*Scaffold complete: Yes*  
*Ready for testing: Yes*
