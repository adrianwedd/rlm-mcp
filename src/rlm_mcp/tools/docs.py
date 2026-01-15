"""Document management tools: rlm.docs.*"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rlm_mcp.models import Document, DocumentSource, estimate_tokens
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_docs_tools(server: "RLMServer") -> None:
    """Register document management tools."""
    
    @server.tool("rlm.docs.load")
    async def rlm_docs_load(
        session_id: str,
        sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Load documents into the session context.
        
        Args:
            session_id: Session to load into
            sources: List of source specs (type, path, content, etc.)
        """
        return await _docs_load(server, session_id=session_id, sources=sources)
    
    @server.tool("rlm.docs.list")
    async def rlm_docs_list(
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List documents in session.
        
        Args:
            session_id: Session to query
            limit: Max documents to return
            offset: Pagination offset
        """
        return await _docs_list(server, session_id=session_id, limit=limit, offset=offset)
    
    @server.tool("rlm.docs.peek")
    async def rlm_docs_peek(
        session_id: str,
        doc_id: str,
        start: int = 0,
        end: int = -1,
    ) -> dict[str, Any]:
        """View a portion of a document. Enforces max_chars_per_peek.
        
        Args:
            session_id: Session containing document
            doc_id: Document ID to peek
            start: Start offset (inclusive)
            end: End offset (exclusive), -1 for end of doc
        """
        return await _docs_peek(server, session_id=session_id, doc_id=doc_id, start=start, end=end)


@tool_handler("rlm.docs.load")
async def _docs_load(
    server: "RLMServer",
    session_id: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Load documents into session with batch insert and concurrency control."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")

    # Invalidate BM25 index (new docs make it stale)
    # 1. Invalidate in-memory cache
    if session_id in server._index_cache:
        del server._index_cache[session_id]

    # 2. Invalidate persisted index on disk
    server.index_persistence.invalidate_index(session_id)

    # Create semaphore for concurrency control
    max_concurrent = server.config.max_concurrent_loads
    semaphore = asyncio.Semaphore(max_concurrent)

    loaded = []
    errors = []
    all_docs = []  # Collect all documents for batch insert

    # Process each source
    async def load_source(source_spec):
        """Load a single source with semaphore control."""
        source_type = source_spec.get("type", "file")

        try:
            if source_type == "inline":
                # Inline content
                if "content" not in source_spec:
                    return None, "Inline source missing content"
                content = source_spec["content"]
                docs = [await _load_inline_no_save(server, session_id, content, source_spec)]

            elif source_type == "file":
                # Single file (with semaphore)
                path = source_spec.get("path")
                if not path:
                    return None, "File source missing path"

                async with semaphore:
                    docs = [await _load_file_no_save(server, session_id, Path(path), source_spec)]

            elif source_type == "glob":
                # Glob pattern (concurrent file loads with semaphore)
                path = source_spec.get("path")
                if not path:
                    return None, "Glob source missing path"

                docs = await _load_glob_concurrent(server, session_id, path, source_spec, semaphore)

            elif source_type == "directory":
                # Directory (concurrent file loads with semaphore)
                path = source_spec.get("path")
                if not path:
                    return None, "Directory source missing path"

                docs = await _load_directory_concurrent(server, session_id, Path(path), source_spec, semaphore)
            else:
                return None, f"Unknown source type: {source_type}"

            return docs, None

        except Exception as e:
            return None, f"Error loading {source_spec}: {e}"

    # Load all sources concurrently
    results = await asyncio.gather(*[load_source(spec) for spec in sources])

    # Process results
    for docs, error in results:
        if error:
            errors.append(error)
        elif docs:
            all_docs.extend(docs)

    # Batch insert all documents
    if all_docs:
        await server.db.create_documents_batch(all_docs)

    # Build response
    total_chars = 0
    total_tokens_est = 0

    for doc in all_docs:
        loaded.append({
            "doc_id": doc.id,
            "content_hash": doc.content_hash,
            "source": str(doc.source.path or doc.source.url or "inline"),
            "length_chars": doc.length_chars,
            "length_tokens_est": doc.length_tokens_est,
        })
        total_chars += doc.length_chars
        total_tokens_est += doc.length_tokens_est

    return {
        "loaded": loaded,
        "errors": errors,
        "total_chars": total_chars,
        "total_tokens_est": total_tokens_est,
    }


async def _load_inline(
    server: "RLMServer",
    session_id: str,
    content: str,
    source_spec: dict[str, Any],
) -> Document:
    """Load inline content as document."""
    content_hash = server.blobs.put(content)
    token_hint = source_spec.get("token_count_hint")
    
    doc = Document(
        session_id=session_id,
        content_hash=content_hash,
        source=DocumentSource(type="inline"),
        length_chars=len(content),
        length_tokens_est=estimate_tokens(len(content), token_hint),
    )
    await server.db.create_document(doc)
    return doc


async def _load_file(
    server: "RLMServer",
    session_id: str,
    path: Path,
    source_spec: dict[str, Any],
) -> Document:
    """Load a single file as document."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    content = path.read_text(encoding="utf-8")
    content_hash = server.blobs.put(content)
    token_hint = source_spec.get("token_count_hint")
    
    doc = Document(
        session_id=session_id,
        content_hash=content_hash,
        source=DocumentSource(type="file", path=str(path.absolute())),
        length_chars=len(content),
        length_tokens_est=estimate_tokens(len(content), token_hint),
        metadata={"filename": path.name},
    )
    await server.db.create_document(doc)
    return doc


async def _load_glob(
    server: "RLMServer",
    session_id: str,
    pattern: str,
    source_spec: dict[str, Any],
) -> list[Document]:
    """Load files matching glob pattern."""
    import glob as glob_module
    
    recursive = source_spec.get("recursive", False)
    include_pattern = source_spec.get("include_pattern")
    exclude_pattern = source_spec.get("exclude_pattern")
    
    # Find matching files
    if recursive:
        files = list(Path(".").glob(f"**/{pattern}"))
    else:
        files = [Path(p) for p in glob_module.glob(pattern)]
    
    # Filter
    if include_pattern:
        import re
        include_re = re.compile(include_pattern)
        files = [f for f in files if include_re.search(str(f))]
    
    if exclude_pattern:
        import re
        exclude_re = re.compile(exclude_pattern)
        files = [f for f in files if not exclude_re.search(str(f))]
    
    # Load each file
    docs = []
    for file_path in files:
        if file_path.is_file():
            doc = await _load_file(server, session_id, file_path, source_spec)
            docs.append(doc)
    
    return docs


async def _load_directory(
    server: "RLMServer",
    session_id: str,
    dir_path: Path,
    source_spec: dict[str, Any],
) -> list[Document]:
    """Load all files in directory."""
    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")
    
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")
    
    recursive = source_spec.get("recursive", False)
    include_pattern = source_spec.get("include_pattern")
    exclude_pattern = source_spec.get("exclude_pattern")
    
    # Find files
    if recursive:
        files = list(dir_path.rglob("*"))
    else:
        files = list(dir_path.iterdir())
    
    # Filter to files only
    files = [f for f in files if f.is_file()]
    
    # Apply patterns
    if include_pattern:
        import re
        include_re = re.compile(include_pattern)
        files = [f for f in files if include_re.search(str(f))]
    
    if exclude_pattern:
        import re
        exclude_re = re.compile(exclude_pattern)
        files = [f for f in files if not exclude_re.search(str(f))]
    
    # Load each file
    docs = []
    for file_path in files:
        try:
            doc = await _load_file(server, session_id, file_path, source_spec)
            docs.append(doc)
        except Exception:
            # Skip files that can't be read as text
            pass
    
    return docs


# --- No-save versions for batch loading ---

async def _load_inline_no_save(
    server: "RLMServer",
    session_id: str,
    content: str,
    source_spec: dict[str, Any],
) -> Document:
    """Load inline content as document (no DB save)."""
    content_hash = server.blobs.put(content)
    token_hint = source_spec.get("token_count_hint")

    doc = Document(
        session_id=session_id,
        content_hash=content_hash,
        source=DocumentSource(type="inline"),
        length_chars=len(content),
        length_tokens_est=estimate_tokens(len(content), token_hint),
    )
    return doc


async def _load_file_no_save(
    server: "RLMServer",
    session_id: str,
    path: Path,
    source_spec: dict[str, Any],
) -> Document:
    """Load a single file as document (no DB save)."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    # Check file size limit
    file_size_mb = path.stat().st_size / (1024 * 1024)
    if file_size_mb > server.config.max_file_size_mb:
        raise ValueError(
            f"File too large: {path} ({file_size_mb:.1f}MB > {server.config.max_file_size_mb}MB limit)"
        )

    content = path.read_text(encoding="utf-8")
    content_hash = server.blobs.put(content)
    token_hint = source_spec.get("token_count_hint")

    doc = Document(
        session_id=session_id,
        content_hash=content_hash,
        source=DocumentSource(type="file", path=str(path.absolute())),
        length_chars=len(content),
        length_tokens_est=estimate_tokens(len(content), token_hint),
        metadata={"filename": path.name},
    )
    return doc


async def _load_glob_concurrent(
    server: "RLMServer",
    session_id: str,
    pattern: str,
    source_spec: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> list[Document]:
    """Load files matching glob pattern with concurrent loading."""
    import glob as glob_module

    recursive = source_spec.get("recursive", False)
    include_pattern = source_spec.get("include_pattern")
    exclude_pattern = source_spec.get("exclude_pattern")

    # Find matching files
    if recursive:
        files = list(Path(".").glob(f"**/{pattern}"))
    else:
        files = [Path(p) for p in glob_module.glob(pattern)]

    # Filter
    if include_pattern:
        import re
        include_re = re.compile(include_pattern)
        files = [f for f in files if include_re.search(str(f))]

    if exclude_pattern:
        import re
        exclude_re = re.compile(exclude_pattern)
        files = [f for f in files if not exclude_re.search(str(f))]

    # Load files concurrently with semaphore
    async def load_with_semaphore(file_path):
        if not file_path.is_file():
            return None
        async with semaphore:
            try:
                return await _load_file_no_save(server, session_id, file_path, source_spec)
            except Exception:
                # Skip files that can't be loaded
                return None

    docs = await asyncio.gather(*[load_with_semaphore(f) for f in files])
    return [doc for doc in docs if doc is not None]


async def _load_directory_concurrent(
    server: "RLMServer",
    session_id: str,
    dir_path: Path,
    source_spec: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> list[Document]:
    """Load all files in directory with concurrent loading."""
    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    recursive = source_spec.get("recursive", False)
    include_pattern = source_spec.get("include_pattern")
    exclude_pattern = source_spec.get("exclude_pattern")

    # Find files
    if recursive:
        files = list(dir_path.rglob("*"))
    else:
        files = list(dir_path.iterdir())

    # Filter to files only
    files = [f for f in files if f.is_file()]

    # Apply patterns
    if include_pattern:
        import re
        include_re = re.compile(include_pattern)
        files = [f for f in files if include_re.search(str(f))]

    if exclude_pattern:
        import re
        exclude_re = re.compile(exclude_pattern)
        files = [f for f in files if not exclude_re.search(str(f))]

    # Load files concurrently with semaphore
    async def load_with_semaphore(file_path):
        async with semaphore:
            try:
                return await _load_file_no_save(server, session_id, file_path, source_spec)
            except Exception:
                # Skip files that can't be read as text
                return None

    docs = await asyncio.gather(*[load_with_semaphore(f) for f in files])
    return [doc for doc in docs if doc is not None]


@tool_handler("rlm.docs.list")
async def _docs_list(
    server: "RLMServer",
    session_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List documents in session."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    docs = await server.db.get_documents(session_id, limit=limit + 1, offset=offset)
    has_more = len(docs) > limit
    docs = docs[:limit]
    
    total = await server.db.count_documents(session_id)
    
    documents = []
    for doc in docs:
        span_count = await server.db.count_spans_for_document(doc.id)
        documents.append({
            "doc_id": doc.id,
            "content_hash": doc.content_hash,
            "source": str(doc.source.path or doc.source.url or "inline"),
            "length_chars": doc.length_chars,
            "length_tokens_est": doc.length_tokens_est,
            "span_count": span_count,
        })
    
    return {
        "documents": documents,
        "total": total,
        "has_more": has_more,
    }


@tool_handler("rlm.docs.peek")
async def _docs_peek(
    server: "RLMServer",
    session_id: str,
    doc_id: str,
    start: int = 0,
    end: int = -1,
) -> dict[str, Any]:
    """Peek at document content with char limit."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    doc = await server.db.get_document(doc_id)
    if doc is None:
        raise ValueError(f"Document not found: {doc_id}")
    
    if doc.session_id != session_id:
        raise ValueError(f"Document {doc_id} not in session {session_id}")
    
    # Get content slice
    content = server.blobs.get_slice(doc.content_hash, start, end)
    if content is None:
        raise ValueError(f"Content not found for document: {doc_id}")
    
    # Apply char limit
    max_chars = server.get_char_limit(session, "peek")
    content, truncated = server.truncate_content(content, max_chars)
    
    # Compute actual end
    actual_end = start + len(content) if end == -1 else min(end, start + len(content))
    
    return {
        "content": content,
        "span": {
            "doc_id": doc_id,
            "start": start,
            "end": actual_end,
        },
        "content_hash": server.blobs.hash_content(content),
        "truncated": truncated,
        "total_length": doc.length_chars,
    }
