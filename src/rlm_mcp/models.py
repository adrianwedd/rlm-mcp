"""Core data models for RLM-MCP.

Identifier semantics:
- doc_id: Session-scoped stable identifier (UUID)
- content_hash: Global content-addressed blob store key (SHA256)
- span_id: Session-scoped stable identifier for artifact provenance
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def generate_id() -> str:
    """Generate a new UUID string."""
    return str(uuid4())


def estimate_tokens(chars: int, hint: int | None = None) -> int:
    """Vendor-neutral token estimation.
    
    Args:
        chars: Character count (ground truth)
        hint: Optional client-provided token count
        
    Returns:
        Estimated token count (~4 chars/token heuristic)
    """
    if hint is not None:
        return hint
    return math.ceil(chars / 4)


class SessionStatus(str, Enum):
    """Session lifecycle status."""
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPORTED = "exported"


class SessionConfig(BaseModel):
    """Session configuration with DOS protection caps."""
    max_tool_calls: int = Field(default=500, ge=1)
    max_chars_per_response: int = Field(default=50_000, ge=1000)
    max_chars_per_peek: int = Field(default=10_000, ge=100)
    chunk_cache_enabled: bool = True
    model_hints: ModelHints | None = None


class ModelHints(BaseModel):
    """Advisory metadata for client subcall decisions."""
    root_model: str | None = None
    subcall_model: str | None = None
    bulk_model: str | None = None


class Session(BaseModel):
    """RLM session for processing large contexts."""
    id: str = Field(default_factory=generate_id)
    name: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    config: SessionConfig = Field(default_factory=SessionConfig)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    tool_calls_used: int = 0


class DocumentSource(BaseModel):
    """Document source metadata."""
    type: str  # file, url, inline, directory, glob
    path: str | None = None
    url: str | None = None


class Document(BaseModel):
    """Document loaded into session context.
    
    - id (doc_id): Session-scoped stable identifier
    - content_hash: Blob store address; used for dedup across sessions
    """
    id: str = Field(default_factory=generate_id)
    session_id: str
    content_hash: str  # SHA256 of content
    source: DocumentSource
    length_chars: int
    length_tokens_est: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChunkStrategy(BaseModel):
    """Chunking strategy configuration."""
    type: str  # fixed, lines, delimiter
    chunk_size: int | None = None  # For fixed
    line_count: int | None = None  # For lines
    overlap: int = 0
    delimiter: str | None = None  # For delimiter
    max_chunks: int | None = None


class SpanRef(BaseModel):
    """Span reference for provenance tracking."""
    doc_id: str
    start: int
    end: int


class Span(BaseModel):
    """Document span (chunk) with provenance.
    
    - id (span_id): Session-scoped stable identifier
    - content_hash: Hash of span content for integrity/dedup
    """
    id: str = Field(default_factory=generate_id)
    document_id: str
    start_offset: int
    end_offset: int
    content_hash: str
    strategy: ChunkStrategy
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_ref(self, doc_id: str) -> SpanRef:
        """Convert to SpanRef for provenance."""
        return SpanRef(doc_id=doc_id, start=self.start_offset, end=self.end_offset)


class ArtifactProvenance(BaseModel):
    """Provenance metadata for artifacts."""
    model: str | None = None
    prompt_hash: str | None = None
    tool: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Artifact(BaseModel):
    """Derived artifact with provenance."""
    id: str = Field(default_factory=generate_id)
    session_id: str
    span_id: str | None = None  # Nullable for session-level artifacts
    type: str  # summary, extraction, classification, custom
    content: dict[str, Any]
    provenance: ArtifactProvenance | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TraceEntry(BaseModel):
    """Trace log entry for debugging and replay."""
    id: str = Field(default_factory=generate_id)
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    operation: str  # Canonical: rlm.<category>.<action>
    input: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int
    client_reported: dict[str, Any] | None = None  # Optional subcall metrics


# --- Tool Output Models ---

class SessionSummary(BaseModel):
    """Summary returned on session close."""
    documents: int
    spans: int
    artifacts: int
    tool_calls: int


class LoadedDocument(BaseModel):
    """Document info returned from rlm.docs.load."""
    doc_id: str
    content_hash: str
    source: str
    length_chars: int
    length_tokens_est: int


class SpanOutput(BaseModel):
    """Span output with provenance for rlm.span.get."""
    span_id: str
    span: SpanRef
    content: str
    content_hash: str
    truncated: bool


class SearchMatch(BaseModel):
    """Search result with span reference."""
    doc_id: str
    span: SpanRef
    span_id: str | None = None  # If span was persisted
    score: float
    context: str
    highlight_start: int
    highlight_end: int


class ExportResult(BaseModel):
    """Result from rlm.export.github."""
    branch: str
    commit_sha: str
    export_path: str  # Actual path used
    files_exported: int
    warnings: list[str] = Field(default_factory=list)
    secrets_found: int = 0
