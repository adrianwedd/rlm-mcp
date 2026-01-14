"""Session management tools: rlm.session.*"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from mcp.types import Tool, TextContent

from rlm_mcp.models import Session, SessionConfig, SessionStatus, SessionSummary
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_session_tools(server: "RLMServer") -> None:
    """Register session management tools."""
    
    @server.tool("rlm.session.create")
    async def rlm_session_create(
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new RLM session for processing large contexts.
        
        Args:
            name: Human-readable session name
            config: Session configuration (max_tool_calls, max_chars_per_response, etc.)
        """
        return await _session_create(server, name=name, config=config)
    
    @server.tool("rlm.session.info")
    async def rlm_session_info(session_id: str) -> dict[str, Any]:
        """Get session statistics and configuration.
        
        Args:
            session_id: Session ID to query
        """
        return await _session_info(server, session_id=session_id)
    
    @server.tool("rlm.session.close")
    async def rlm_session_close(session_id: str) -> dict[str, Any]:
        """Mark session complete and persist index metadata.
        
        Args:
            session_id: Session ID to close
        """
        return await _session_close(server, session_id=session_id)


@tool_handler("rlm.session.create")
async def _session_create(
    server: "RLMServer",
    name: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new session."""
    session_config = SessionConfig(**(config or {}))
    session = Session(name=name, config=session_config)

    await server.db.create_session(session)

    # Count session.create as a tool call
    await server.increment_budget(session.id)

    return {
        "session_id": session.id,
        "created_at": session.created_at.isoformat(),
        "config": session.config.model_dump(),
    }


@tool_handler("rlm.session.info")
async def _session_info(
    server: "RLMServer",
    session_id: str,
) -> dict[str, Any]:
    """Get session info."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    doc_count = await server.db.count_documents(session_id)
    stats = await server.db.get_session_stats(session_id)
    
    # Check if index is built
    index_built = session_id in server._index_cache
    
    return {
        "session_id": session.id,
        "name": session.name,
        "status": session.status.value,
        "created_at": session.created_at.isoformat(),
        "closed_at": session.closed_at.isoformat() if session.closed_at else None,
        "document_count": doc_count,
        "total_chars": stats["total_chars"],
        "total_tokens_est": stats["total_tokens_est"],
        "tool_calls_used": session.tool_calls_used,
        "tool_calls_remaining": session.config.max_tool_calls - session.tool_calls_used,
        "index_built": index_built,
        "config": session.config.model_dump(),
    }


@tool_handler("rlm.session.close")
async def _session_close(
    server: "RLMServer",
    session_id: str,
) -> dict[str, Any]:
    """Close session and flush indexes."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    if session.status != SessionStatus.ACTIVE:
        raise ValueError(f"Session already closed: {session_id}")
    
    # Update session status
    session.status = SessionStatus.COMPLETED
    session.closed_at = datetime.utcnow()
    await server.db.update_session(session)
    
    # Get summary stats
    doc_count = await server.db.count_documents(session_id)
    span_count = await server.db.count_spans(session_id)
    artifact_count = await server.db.count_artifacts(session_id)
    
    # Clean up index cache
    if session_id in server._index_cache:
        del server._index_cache[session_id]
    
    return {
        "session_id": session.id,
        "status": session.status.value,
        "closed_at": session.closed_at.isoformat(),
        "summary": {
            "documents": doc_count,
            "spans": span_count,
            "artifacts": artifact_count,
            "tool_calls": session.tool_calls_used,
        },
    }
