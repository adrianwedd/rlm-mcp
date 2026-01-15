"""Custom exceptions for RLM-MCP with user-friendly context."""

from typing import Any


class RLMError(Exception):
    """Base error for RLM-MCP."""

    def __init__(self, message: str, **context: Any):
        self.message = message
        self.context = context
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format error message with context."""
        parts = [self.message]

        if self.context:
            ctx_parts = [f"{k}={v}" for k, v in self.context.items() if v is not None]
            if ctx_parts:
                parts.append(f"({', '.join(ctx_parts)})")

        return " ".join(parts)


class SessionNotFoundError(RLMError):
    """Session not found or already closed."""

    def __init__(self, session_id: str, **context: Any):
        super().__init__(
            f"Session '{session_id}' not found. It may have been closed or never existed.",
            session_id=session_id,
            **context
        )


class DocumentNotFoundError(RLMError):
    """Document not found in session."""

    def __init__(
        self,
        doc_id: str,
        session_id: str | None = None,
        **context: Any
    ):
        msg = f"Document '{doc_id}' not found"
        if session_id:
            msg += f" in session '{session_id}'"
        super().__init__(msg, doc_id=doc_id, session_id=session_id, **context)


class SpanNotFoundError(RLMError):
    """Span (chunk) not found in session."""

    def __init__(
        self,
        span_id: str,
        session_id: str | None = None,
        document_name: str | None = None,
        chunk_index: int | None = None,
        **context: Any
    ):
        # Build user-friendly message
        if chunk_index is not None and document_name:
            msg = f"Chunk #{chunk_index} from document '{document_name}' not found"
        elif document_name:
            msg = f"Chunk from document '{document_name}' not found"
        else:
            msg = f"Chunk '{span_id}' not found"

        if session_id:
            msg += f" in session '{session_id}'"

        hint = context.pop("hint", None)
        if hint:
            msg += f". {hint}"
        else:
            msg += ". It may have been deleted or never created."

        super().__init__(msg, span_id=span_id, session_id=session_id, **context)


class BudgetExceededError(RLMError):
    """Tool call budget exceeded for session."""

    def __init__(
        self,
        session_id: str,
        used: int,
        limit: int,
        **context: Any
    ):
        super().__init__(
            f"Tool call budget exceeded: {used}/{limit} calls used. "
            f"Close this session or create a new one with higher max_tool_calls.",
            session_id=session_id,
            used=used,
            limit=limit,
            **context
        )


class ContentNotFoundError(RLMError):
    """Content not found in blob store."""

    def __init__(
        self,
        content_hash: str,
        context_msg: str | None = None,
        **context: Any
    ):
        msg = "Content not found in blob store"
        if context_msg:
            msg += f" ({context_msg})"
        msg += ". The blob store may be corrupted."

        super().__init__(msg, content_hash=content_hash, **context)
