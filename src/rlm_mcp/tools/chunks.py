"""Chunking and span tools: rlm.chunk.*, rlm.span.*"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Iterator

from rlm_mcp.models import ChunkStrategy, Span, SpanRef
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_chunk_tools(server: "RLMServer") -> None:
    """Register chunking and span tools."""
    
    @server.tool("rlm.chunk.create")
    async def rlm_chunk_create(
        session_id: str,
        doc_id: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        """Chunk a document using a specified strategy.
        
        Args:
            session_id: Session containing document
            doc_id: Document ID to chunk
            strategy: Chunking strategy (type, chunk_size, line_count, delimiter, overlap)
        """
        return await _chunk_create(
            server, session_id=session_id, doc_id=doc_id, strategy=strategy
        )
    
    @server.tool("rlm.span.get")
    async def rlm_span_get(
        session_id: str,
        span_ids: list[str],
    ) -> dict[str, Any]:
        """Retrieve the content of one or more spans. Enforces max_chars_per_response.
        
        Args:
            session_id: Session containing spans
            span_ids: List of span IDs to retrieve
        """
        return await _span_get(server, session_id=session_id, span_ids=span_ids)


# --- Chunking Strategies ---

class BaseChunkStrategy(ABC):
    """Abstract base for chunking strategies."""
    
    @abstractmethod
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        """Yield (start, end) offsets for chunks."""
        pass


class FixedChunkStrategy(BaseChunkStrategy):
    """Fixed-size character chunks with optional overlap."""
    
    def __init__(self, chunk_size: int, overlap: int = 0):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        start = 0
        while start < len(content):
            end = min(start + self.chunk_size, len(content))
            yield (start, end)
            if end >= len(content):
                break
            start = end - self.overlap if self.overlap else end


class LinesChunkStrategy(BaseChunkStrategy):
    """Chunk by line count with optional overlap."""
    
    def __init__(self, line_count: int, overlap: int = 0):
        self.line_count = line_count
        self.overlap = overlap
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        lines = content.split('\n')
        line_offsets = []
        
        # Build line offset map
        offset = 0
        for line in lines:
            line_offsets.append(offset)
            offset += len(line) + 1  # +1 for newline
        line_offsets.append(len(content))  # End of content
        
        # Yield chunks
        i = 0
        while i < len(lines):
            end_idx = min(i + self.line_count, len(lines))
            start_offset = line_offsets[i]
            end_offset = line_offsets[end_idx]
            
            yield (start_offset, end_offset)
            
            if end_idx >= len(lines):
                break
            
            i = end_idx - self.overlap if self.overlap else end_idx


class DelimiterChunkStrategy(BaseChunkStrategy):
    """Chunk by delimiter pattern (regex)."""
    
    def __init__(self, delimiter: str):
        self.delimiter = delimiter
        self.pattern = re.compile(delimiter)
    
    def chunk(self, content: str) -> Iterator[tuple[int, int]]:
        # Find all delimiter positions
        matches = list(self.pattern.finditer(content))
        
        if not matches:
            # No delimiters, return entire content
            yield (0, len(content))
            return
        
        # First chunk: start to first delimiter
        if matches[0].start() > 0:
            yield (0, matches[0].start())
        
        # Middle chunks: delimiter to next delimiter
        for i, match in enumerate(matches):
            start = match.start()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(content)
            yield (start, end)


def create_strategy(strategy_spec: dict[str, Any]) -> BaseChunkStrategy:
    """Create chunking strategy from spec."""
    strategy_type = strategy_spec.get("type", "fixed")
    
    if strategy_type == "fixed":
        chunk_size = strategy_spec.get("chunk_size", 50000)
        overlap = strategy_spec.get("overlap", 0)
        return FixedChunkStrategy(chunk_size, overlap)
    
    elif strategy_type == "lines":
        line_count = strategy_spec.get("line_count", 100)
        overlap = strategy_spec.get("overlap", 0)
        return LinesChunkStrategy(line_count, overlap)
    
    elif strategy_type == "delimiter":
        delimiter = strategy_spec.get("delimiter")
        if not delimiter:
            raise ValueError("Delimiter strategy requires 'delimiter' parameter")
        return DelimiterChunkStrategy(delimiter)
    
    else:
        raise ValueError(f"Unknown strategy type: {strategy_type}")


# --- Tool Implementations ---

@tool_handler("rlm.chunk.create")
async def _chunk_create(
    server: "RLMServer",
    session_id: str,
    doc_id: str,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Create chunks for a document."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    doc = await server.db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"Document not found: {doc_id}")
    
    if doc.session_id != session_id:
        raise ValueError(f"Document {doc_id} not in session {session_id}")
    
    # Check for cached chunks with same strategy
    existing_spans = await server.db.get_spans_by_document(doc_id)
    strategy_obj = ChunkStrategy(**strategy)
    
    # Simple cache check: if spans exist with same strategy, return them
    if existing_spans and existing_spans[0].strategy == strategy_obj:
        spans_output = []
        for i, span in enumerate(existing_spans):
            content = server.blobs.get_slice(doc.content_hash, span.start_offset, span.end_offset)
            preview = content[:100] if content else ""
            spans_output.append({
                "span_id": span.id,
                "index": i,
                "span": {
                    "doc_id": doc_id,
                    "start": span.start_offset,
                    "end": span.end_offset,
                },
                "length_chars": span.end_offset - span.start_offset,
                "content_hash": span.content_hash,
                "preview": preview,
            })
        
        return {
            "spans": spans_output,
            "total_spans": len(spans_output),
            "cached": True,
        }
    
    # Get content and chunk
    content = server.blobs.get(doc.content_hash)
    if content is None:
        raise ValueError(f"Content not found for document: {doc_id}")
    
    chunker = create_strategy(strategy)
    max_chunks = strategy.get("max_chunks")
    
    spans_output = []
    for i, (start, end) in enumerate(chunker.chunk(content)):
        if max_chunks and i >= max_chunks:
            break
        
        span_content = content[start:end]
        content_hash = server.blobs.hash_content(span_content)
        
        span = Span(
            document_id=doc_id,
            start_offset=start,
            end_offset=end,
            content_hash=content_hash,
            strategy=strategy_obj,
        )
        await server.db.create_span(span)
        
        preview = span_content[:100]
        spans_output.append({
            "span_id": span.id,
            "index": i,
            "span": {
                "doc_id": doc_id,
                "start": start,
                "end": end,
            },
            "length_chars": end - start,
            "content_hash": content_hash,
            "preview": preview,
        })
    
    return {
        "spans": spans_output,
        "total_spans": len(spans_output),
        "cached": False,
    }


@tool_handler("rlm.span.get")
async def _span_get(
    server: "RLMServer",
    session_id: str,
    span_ids: list[str],
) -> dict[str, Any]:
    """Get span contents with provenance."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    max_chars = server.get_char_limit(session, "response")
    total_chars = 0
    spans_output = []
    
    for span_id in span_ids:
        span = await server.db.get_span(span_id)
        if span is None:
            raise ValueError(f"Span not found: {span_id}")
        
        # Get document to verify session and get content_hash
        doc = await server.db.get_document(span.document_id)
        if doc is None or doc.session_id != session_id:
            raise ValueError(f"Span {span_id} not in session {session_id}")
        
        # Get content
        content = server.blobs.get_slice(
            doc.content_hash, span.start_offset, span.end_offset
        )
        if content is None:
            raise ValueError(f"Content not found for span: {span_id}")
        
        # Check if we'd exceed total char limit
        remaining = max_chars - total_chars
        truncated = len(content) > remaining
        if truncated:
            content = content[:remaining]
        
        total_chars += len(content)
        
        spans_output.append({
            "span_id": span.id,
            "span": {
                "doc_id": span.document_id,
                "start": span.start_offset,
                "end": span.end_offset,
            },
            "content": content,
            "content_hash": span.content_hash,
            "truncated": truncated,
        })
        
        # Stop if we've hit the limit
        if total_chars >= max_chars:
            break
    
    return {
        "spans": spans_output,
        "total_chars_returned": total_chars,
    }
