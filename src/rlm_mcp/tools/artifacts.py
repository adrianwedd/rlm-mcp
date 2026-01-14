"""Artifact management tools: rlm.artifact.*"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rlm_mcp.models import Artifact, ArtifactProvenance, Span, ChunkStrategy, SpanRef
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_artifact_tools(server: "RLMServer") -> None:
    """Register artifact management tools."""
    
    @server.tool("rlm.artifact.store")
    async def rlm_artifact_store(
        session_id: str,
        type: str,
        content: dict[str, Any],
        span_id: str | None = None,
        span: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a derived artifact with provenance.
        
        Args:
            session_id: Session to store artifact in
            type: Artifact type (summary, extraction, classification, custom)
            content: Artifact content
            span_id: Optional span ID for provenance
            span: Optional span reference (doc_id, start, end) - creates span if needed
            provenance: Optional provenance metadata (model, prompt_hash)
        """
        return await _artifact_store(
            server,
            session_id=session_id,
            type=type,
            content=content,
            span_id=span_id,
            span=span,
            provenance=provenance,
        )
    
    @server.tool("rlm.artifact.list")
    async def rlm_artifact_list(
        session_id: str,
        span_id: str | None = None,
        type: str | None = None,
    ) -> dict[str, Any]:
        """List artifacts for a session or span.
        
        Args:
            session_id: Session to query
            span_id: Optional span ID filter
            type: Optional type filter
        """
        return await _artifact_list(
            server, session_id=session_id, span_id=span_id, type=type
        )
    
    @server.tool("rlm.artifact.get")
    async def rlm_artifact_get(
        session_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        """Retrieve artifact content.
        
        Args:
            session_id: Session containing artifact
            artifact_id: Artifact ID to retrieve
        """
        return await _artifact_get(
            server, session_id=session_id, artifact_id=artifact_id
        )


@tool_handler("rlm.artifact.store")
async def _artifact_store(
    server: "RLMServer",
    session_id: str,
    type: str,
    content: dict[str, Any],
    span_id: str | None = None,
    span: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store an artifact."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    resolved_span_id = span_id
    
    # If span reference provided instead of span_id, create/find the span
    if span and not span_id:
        doc_id = span.get("doc_id")
        start = span.get("start")
        end = span.get("end")
        
        if doc_id and start is not None and end is not None:
            # Verify document exists in session
            doc = await server.db.get_document(doc_id)
            if doc is None or doc.session_id != session_id:
                raise ValueError(f"Document {doc_id} not in session {session_id}")
            
            # Get content to compute hash
            span_content = server.blobs.get_slice(doc.content_hash, start, end)
            if span_content is None:
                raise ValueError(f"Content not found for span")
            
            content_hash = server.blobs.hash_content(span_content)
            
            # Create span
            new_span = Span(
                document_id=doc_id,
                start_offset=start,
                end_offset=end,
                content_hash=content_hash,
                strategy=ChunkStrategy(type="manual"),
            )
            await server.db.create_span(new_span)
            resolved_span_id = new_span.id
    
    # Validate span_id if provided
    if resolved_span_id:
        existing_span = await server.db.get_span(resolved_span_id)
        if existing_span is None:
            raise ValueError(f"Span not found: {resolved_span_id}")
        
        # Verify span's document is in this session
        doc = await server.db.get_document(existing_span.document_id)
        if doc is None or doc.session_id != session_id:
            raise ValueError(f"Span {resolved_span_id} not in session {session_id}")
    
    # Create provenance
    prov = ArtifactProvenance(**(provenance or {})) if provenance else None
    
    # Create artifact
    artifact = Artifact(
        session_id=session_id,
        span_id=resolved_span_id,
        type=type,
        content=content,
        provenance=prov,
    )
    await server.db.create_artifact(artifact)
    
    return {
        "artifact_id": artifact.id,
        "span_id": resolved_span_id,
    }


@tool_handler("rlm.artifact.list")
async def _artifact_list(
    server: "RLMServer",
    session_id: str,
    span_id: str | None = None,
    type: str | None = None,
) -> dict[str, Any]:
    """List artifacts."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    artifacts = await server.db.get_artifacts(
        session_id=session_id,
        span_id=span_id,
        artifact_type=type,
    )
    
    return {
        "artifacts": [
            {
                "artifact_id": a.id,
                "span_id": a.span_id,
                "type": a.type,
                "created_at": a.created_at.isoformat(),
                "provenance": a.provenance.model_dump(mode='json') if a.provenance else None,
            }
            for a in artifacts
        ],
    }


@tool_handler("rlm.artifact.get")
async def _artifact_get(
    server: "RLMServer",
    session_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Get artifact content with full provenance."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    artifact = await server.db.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact not found: {artifact_id}")
    
    if artifact.session_id != session_id:
        raise ValueError(f"Artifact {artifact_id} not in session {session_id}")
    
    # Get span reference if available
    span_ref = None
    if artifact.span_id:
        span = await server.db.get_span(artifact.span_id)
        if span:
            span_ref = {
                "doc_id": span.document_id,
                "start": span.start_offset,
                "end": span.end_offset,
            }
    
    return {
        "artifact_id": artifact.id,
        "span_id": artifact.span_id,
        "span": span_ref,
        "type": artifact.type,
        "content": artifact.content,
        "provenance": artifact.provenance.model_dump(mode='json') if artifact.provenance else None,
        "created_at": artifact.created_at.isoformat(),
    }
