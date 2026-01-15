"""RLM-MCP Server - Recursive Language Model MCP Server.

A Model Context Protocol server that implements the RLM pattern from
Zhang et al. (2025), treating prompts as external environment objects
for programmatic manipulation.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP

from rlm_mcp.config import ServerConfig, ensure_directories, load_config
from rlm_mcp.index.persistence import IndexPersistence
from rlm_mcp.logging_config import StructuredLogger, configure_logging, correlation_id_var
from rlm_mcp.models import Session, TraceEntry
from rlm_mcp.storage import BlobStore, Database

logger = StructuredLogger(__name__)

# Type for tool handlers
T = TypeVar("T")

# Track whether we've warned about tool naming (one-time only)
_WARNED_NO_NAME_SUPPORT = False


class ToolNamingError(Exception):
    """Raised when canonical tool naming fails in strict mode."""
    pass


def named_tool(mcp_server: FastMCP, canonical_name: str, *, strict: bool = True):
    """Register a tool with canonical naming.

    Args:
        mcp_server: The MCP Server instance
        canonical_name: Canonical tool name (e.g., "rlm.session.create")
        strict: If True (default), fail fast when SDK doesn't support name=.
                If False, fall back to function names with a warning.

    Raises:
        ToolNamingError: In strict mode, if SDK doesn't support canonical naming.
    """
    global _WARNED_NO_NAME_SUPPORT

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        global _WARNED_NO_NAME_SUPPORT
        try:
            # Try with explicit name parameter
            return mcp_server.tool(name=canonical_name)(func)
        except TypeError as e:
            if "name" not in str(e):
                raise

            # SDK doesn't support name= parameter
            if strict:
                raise ToolNamingError(
                    f"MCP SDK doesn't support tool(name=...). "
                    f"Cannot register '{canonical_name}' with canonical name. "
                    f"Either upgrade to FastMCP/newer SDK, or set "
                    f"allow_noncanonical_tool_names=True in server config."
                ) from e

            # Compat mode: fall back with one-time warning
            if not _WARNED_NO_NAME_SUPPORT:
                logger.warning(
                    "MCP SDK doesn't support tool(name=...). "
                    "Falling back to function names (e.g., 'rlm_session_create' "
                    "instead of 'rlm.session.create'). Skills and clients "
                    "expecting canonical names may not work correctly."
                )
                _WARNED_NO_NAME_SUPPORT = True

            return mcp_server.tool()(func)

    return decorator


class RLMServer:
    """RLM-MCP Server with middleware for tracing, budgets, and char caps.

    Concurrency Model:
    - Per-session locks prevent race conditions during index builds and session close
    - Single-process only (locks are in-memory asyncio.Lock instances)
    - Multi-process deployments require external coordination (Redis, file locks, etc.)
    """

    def __init__(self, config: ServerConfig | None = None):
        self.config = config or load_config()
        ensure_directories(self.config)

        self.db = Database(self.config.database_path)
        self.blobs = BlobStore(self.config.blob_dir)

        # MCP server instance
        self.mcp = FastMCP("rlm-mcp")

        # Session index cache (lazy-built BM25 indexes)
        self._index_cache: dict[str, Any] = {}

        # Index persistence layer (atomic writes + fingerprinting)
        self.index_persistence = IndexPersistence(self.config.index_dir)

        # Per-session concurrency locks (single-process only)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock_manager_lock: asyncio.Lock = asyncio.Lock()

        # Register tools
        self._register_tools()

    async def start(self) -> None:
        """Start the server."""
        await self.db.connect()

    async def stop(self) -> None:
        """Stop the server."""
        await self.db.close()

    def _register_tools(self) -> None:
        """Register all MCP tools."""
        # Import tool handlers
        from rlm_mcp.tools.artifacts import register_artifact_tools
        from rlm_mcp.tools.chunks import register_chunk_tools
        from rlm_mcp.tools.docs import register_docs_tools
        from rlm_mcp.tools.search import register_search_tools
        from rlm_mcp.tools.session import register_session_tools

        # Register each category
        register_session_tools(self)
        register_docs_tools(self)
        register_chunk_tools(self)
        register_search_tools(self)
        register_artifact_tools(self)

    def tool(self, name: str):
        """Register a tool with canonical naming.

        Uses named_tool wrapper. By default, fails fast if SDK doesn't support
        canonical naming. Set allow_noncanonical_tool_names=True in config
        to fall back to function names (not recommended for production).

        Args:
            name: Canonical tool name (e.g., "rlm.session.create")
        """
        strict = not self.config.allow_noncanonical_tool_names
        return named_tool(self.mcp, name, strict=strict)

    # --- Lock Management ---

    async def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a lock for a session.

        Locks are session-scoped to prevent race conditions during:
        - Index building (multiple concurrent searches)
        - Session close (cleanup operations)
        - Budget increments (concurrent tool calls)

        IMPORTANT: This is single-process only. Locks are in-memory asyncio.Lock
        instances and do NOT coordinate across multiple processes. For multi-process
        deployments, use external coordination (Redis locks, file locks, database
        advisory locks, etc.).

        Args:
            session_id: Session identifier

        Returns:
            asyncio.Lock instance for this session
        """
        async with self._lock_manager_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    async def release_session_lock(self, session_id: str) -> None:
        """Release and remove a session lock.

        Called after session close to free memory. The lock should not be held
        when calling this method.

        Args:
            session_id: Session identifier
        """
        async with self._lock_manager_lock:
            self._session_locks.pop(session_id, None)

    # --- Index Persistence ---

    async def get_or_build_index(self, session_id: str) -> Any:
        """Get cached index or build from scratch.

        Check strategy (with session lock):
        1. Check in-memory cache -> return if present
        2. Try loading from disk -> validate staleness
        3. Build from scratch if needed -> cache in memory

        Uses session lock to prevent concurrent builds.

        Args:
            session_id: Session identifier

        Returns:
            BM25Index instance
        """
        # Acquire session lock to prevent concurrent builds
        lock = await self.get_session_lock(session_id)
        async with lock:
            # 1. Check in-memory cache first (fastest)
            if session_id in self._index_cache:
                logger.debug(
                    "Index cache hit (memory)",
                    session_id=session_id,
                )
                return self._index_cache[session_id]

            # 2. Try loading from disk
            index, metadata = self.index_persistence.load_index(session_id)

            if index is not None and metadata is not None:
                # Validate staleness using fingerprinting
                doc_count = await self.db.count_documents(session_id)
                doc_fingerprints = await self.db.get_document_fingerprints(session_id)
                doc_fingerprint = self.index_persistence.compute_doc_fingerprint(
                    doc_fingerprints
                )
                tokenizer_name = self.index_persistence.get_tokenizer_name()

                is_stale = self.index_persistence.is_index_stale(
                    metadata, doc_count, doc_fingerprint, tokenizer_name
                )

                if not is_stale:
                    # Index is fresh, cache and return
                    logger.info(
                        "Index cache hit (disk)",
                        session_id=session_id,
                        doc_count=doc_count,
                    )
                    self._index_cache[session_id] = index
                    return index

                logger.info(
                    "Index stale, rebuilding",
                    session_id=session_id,
                    reason="fingerprint_mismatch",
                )

            # 3. Build from scratch
            logger.info(
                "Building index from scratch",
                session_id=session_id,
            )

            # Get all documents for session (with limit for DOS protection)
            from rlm_mcp.index.bm25 import BM25Index

            total_doc_count = await self.db.count_documents(session_id)
            INDEX_BUILD_LIMIT = 100000

            documents = await self.db.get_documents(session_id, limit=INDEX_BUILD_LIMIT)

            if total_doc_count > INDEX_BUILD_LIMIT:
                logger.warning(
                    f"Session {session_id} has {total_doc_count} documents but index build is limited to {INDEX_BUILD_LIMIT}. "
                    f"Only the first {INDEX_BUILD_LIMIT} documents will be indexed. "
                    f"Consider splitting large corpora across multiple sessions.",
                    session_id=session_id,
                )

            # Build index
            index = BM25Index()
            for doc in documents:
                content = self.blobs.get(doc.content_hash)
                if content:
                    index.add_document(doc.id, content)

            # Build the BM25 index
            index.build()

            # Cache in memory
            self._index_cache[session_id] = index

            logger.info(
                "Index built successfully",
                session_id=session_id,
                doc_count=len(documents),
            )

            return index

    async def cache_index(self, session_id: str, index: Any) -> None:
        """Cache index in memory.

        Args:
            session_id: Session identifier
            index: BM25Index instance to cache
        """
        lock = await self.get_session_lock(session_id)
        async with lock:
            self._index_cache[session_id] = index
            logger.debug(
                "Cached index in memory",
                session_id=session_id,
            )

    # --- Middleware ---

    async def check_budget(self, session_id: str) -> tuple[bool, int, int]:
        """Check if session has remaining tool call budget.

        Returns:
            (allowed, used, remaining)
        """
        session = await self.db.get_session(session_id)
        if session is None:
            return False, 0, 0

        max_calls = session.config.max_tool_calls
        used = session.tool_calls_used
        remaining = max_calls - used

        return remaining > 0, used, remaining

    async def increment_budget(self, session_id: str) -> int:
        """Increment tool call counter, return new used count."""
        return await self.db.increment_tool_calls(session_id)

    async def log_trace(
        self,
        session_id: str,
        operation: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        duration_ms: int,
        client_reported: dict[str, Any] | None = None,
    ) -> None:
        """Log a trace entry."""
        trace = TraceEntry(
            session_id=session_id,
            operation=operation,
            input=input_data,
            output=output_data,
            duration_ms=duration_ms,
            client_reported=client_reported,
        )
        await self.db.create_trace(trace)

    def get_char_limit(self, session: Session, limit_type: str) -> int:
        """Get character limit for a session.

        Args:
            session: Session instance
            limit_type: 'response' or 'peek'

        Returns:
            Character limit
        """
        if limit_type == "peek":
            return session.config.max_chars_per_peek
        return session.config.max_chars_per_response

    def truncate_content(
        self,
        content: str,
        max_chars: int
    ) -> tuple[str, bool]:
        """Truncate content to max chars.

        Returns:
            (content, truncated)
        """
        if len(content) <= max_chars:
            return content, False
        return content[:max_chars], True


def tool_handler(operation: str):
    """Decorator for tool handlers with tracing, budget middleware, and structured logging.

    Args:
        operation: Canonical operation name (e.g., "rlm.session.create")
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(server: RLMServer, **kwargs: Any) -> Any:
            # Set correlation ID for this operation
            correlation_id = str(uuid.uuid4())
            correlation_id_var.set(correlation_id)

            start_time = time.time()
            session_id = kwargs.get("session_id")

            # Log operation start
            logger.info(
                f"Starting {operation}",
                session_id=session_id,
                operation=operation,
                input_keys=list(kwargs.keys())
            )

            try:
                # Budget check (skip only for session.create which has no session_id yet)
                if session_id and operation != "rlm.session.create":
                    # First verify session exists (avoid confusing error messages)
                    session = await server.db.get_session(session_id)
                    if session is None:
                        raise ValueError(f"Session not found: {session_id}")

                    # Then check budget
                    allowed, used, remaining = await server.check_budget(session_id)
                    if not allowed:
                        raise ValueError(
                            f"Tool call budget exceeded: {used} calls used, "
                            f"{remaining} remaining. Close session or increase max_tool_calls."
                        )
                    await server.increment_budget(session_id)

                # Execute handler
                result = await func(server, **kwargs)
                duration_ms = int((time.time() - start_time) * 1000)

                # Log operation completion
                logger.info(
                    f"Completed {operation}",
                    session_id=session_id,
                    operation=operation,
                    duration_ms=duration_ms,
                    success=True
                )

                # Log trace to database
                if session_id:
                    await server.log_trace(
                        session_id=session_id,
                        operation=operation,
                        input_data=kwargs,
                        output_data=result if isinstance(result, dict) else {"result": str(result)},
                        duration_ms=duration_ms,
                    )

                return result

            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)

                # Log error with context
                logger.error(
                    f"Failed {operation}: {str(e)}",
                    session_id=session_id,
                    operation=operation,
                    duration_ms=duration_ms,
                    error=str(e),
                    error_type=type(e).__name__
                )

                # Log failed trace to database
                if session_id:
                    await server.log_trace(
                        session_id=session_id,
                        operation=operation,
                        input_data=kwargs,
                        output_data={"error": str(e)},
                        duration_ms=duration_ms,
                    )

                raise

            finally:
                # Clear correlation ID to prevent leaks
                correlation_id_var.set(None)

        return wrapper
    return decorator


@asynccontextmanager
async def create_server(config: ServerConfig | None = None):
    """Create and manage server lifecycle."""
    server = RLMServer(config)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def run_server() -> None:
    """Run the MCP server."""
    config = load_config()

    # Configure logging before starting server
    configure_logging(
        log_level=config.log_level,
        structured=config.structured_logging,
        log_file=config.log_file
    )

    async with create_server(config) as server:
        # FastMCP handles stdio internally
        await server.mcp.run_stdio_async()


def main() -> None:
    """Entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
