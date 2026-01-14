"""Search tools: rlm.search.*

BM25 index is lazy-built on first query and cached per session.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from rlm_mcp.models import SearchMatch, SpanRef
from rlm_mcp.server import tool_handler

if TYPE_CHECKING:
    from rlm_mcp.server import RLMServer


def register_search_tools(server: "RLMServer") -> None:
    """Register search tools."""
    
    @server.tool("rlm.search.query")
    async def rlm_search_query(
        session_id: str,
        query: str,
        method: str = "bm25",
        doc_ids: list[str] | None = None,
        limit: int = 10,
        context_chars: int = 200,
    ) -> dict[str, Any]:
        """Search documents. BM25 index is lazy-built on first use.
        
        Args:
            session_id: Session to search
            query: Search query string
            method: Search method (bm25, regex, literal)
            doc_ids: Optional list of doc IDs to limit search
            limit: Max matches to return
            context_chars: Characters of context around each match
        """
        return await _search_query(
            server,
            session_id=session_id,
            query=query,
            method=method,
            doc_ids=doc_ids,
            limit=limit,
            context_chars=context_chars,
        )


@tool_handler("rlm.search.query")
async def _search_query(
    server: "RLMServer",
    session_id: str,
    query: str,
    method: str = "bm25",
    doc_ids: list[str] | None = None,
    limit: int = 10,
    context_chars: int = 200,
) -> dict[str, Any]:
    """Execute search query."""
    session = await server.db.get_session(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    
    # Get documents to search
    all_docs = await server.db.get_documents(session_id, limit=10000)
    if doc_ids:
        docs = [d for d in all_docs if d.id in doc_ids]
    else:
        docs = all_docs
    
    if not docs:
        return {
            "matches": [],
            "total_matches": 0,
            "index_built": session_id in server._index_cache,
            "index_built_this_call": False,
        }
    
    # Dispatch to search method
    if method == "bm25":
        matches, index_built_this_call = await _bm25_search(
            server, session_id, docs, query, limit, context_chars
        )
    elif method == "regex":
        matches = await _regex_search(server, docs, query, limit, context_chars)
        index_built_this_call = False
    elif method == "literal":
        matches = await _literal_search(server, docs, query, limit, context_chars)
        index_built_this_call = False
    else:
        raise ValueError(f"Unknown search method: {method}")
    
    # Apply response char limit
    max_chars = server.get_char_limit(session, "response")
    total_context_chars = sum(len(m.context) for m in matches)
    
    # Truncate matches if needed
    output_matches = []
    chars_used = 0
    for match in matches:
        if chars_used + len(match.context) > max_chars:
            # Truncate this match's context
            remaining = max_chars - chars_used
            if remaining > 0:
                match.context = match.context[:remaining]
                output_matches.append(match)
            break
        output_matches.append(match)
        chars_used += len(match.context)
    
    return {
        "matches": [
            {
                "doc_id": m.doc_id,
                "span": {
                    "doc_id": m.span.doc_id,
                    "start": m.span.start,
                    "end": m.span.end,
                },
                "span_id": m.span_id,
                "score": m.score,
                "context": m.context,
                "highlight_start": m.highlight_start,
                "highlight_end": m.highlight_end,
            }
            for m in output_matches
        ],
        "total_matches": len(matches),
        "index_built": session_id in server._index_cache,
        "index_built_this_call": index_built_this_call,
    }


async def _bm25_search(
    server: "RLMServer",
    session_id: str,
    docs: list,
    query: str,
    limit: int,
    context_chars: int,
) -> tuple[list[SearchMatch], bool]:
    """BM25 search with lazy index building."""
    from rank_bm25 import BM25Okapi
    
    index_built_this_call = False
    
    # Check cache
    if session_id not in server._index_cache:
        # Build index
        corpus = []
        doc_map = []  # (doc_id, content)
        
        for doc in docs:
            content = server.blobs.get(doc.content_hash)
            if content:
                tokens = _tokenize(content)
                corpus.append(tokens)
                doc_map.append((doc.id, content))
        
        if corpus:
            bm25 = BM25Okapi(corpus)
            server._index_cache[session_id] = {
                "bm25": bm25,
                "doc_map": doc_map,
            }
            index_built_this_call = True
    
    cache = server._index_cache.get(session_id)
    if not cache:
        return [], index_built_this_call
    
    bm25 = cache["bm25"]
    doc_map = cache["doc_map"]
    
    # Search
    query_tokens = _tokenize(query)
    scores = bm25.get_scores(query_tokens)
    
    # Get top results
    scored_docs = list(zip(range(len(scores)), scores))
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    matches = []
    for idx, score in scored_docs[:limit]:
        # Note: BM25 scores can be negative, so don't filter them out
        doc_id, content = doc_map[idx]
        
        # Find best match position for context
        match_pos = _find_best_match_position(content, query)
        
        # Extract context
        start = max(0, match_pos - context_chars // 2)
        end = min(len(content), match_pos + context_chars // 2)
        context = content[start:end]
        
        # Highlight position within context
        highlight_start = match_pos - start
        highlight_end = min(highlight_start + len(query), len(context))
        
        matches.append(SearchMatch(
            doc_id=doc_id,
            span=SpanRef(doc_id=doc_id, start=start, end=end),
            span_id=None,
            score=float(score),
            context=context,
            highlight_start=highlight_start,
            highlight_end=highlight_end,
        ))
    
    return matches, index_built_this_call


async def _regex_search(
    server: "RLMServer",
    docs: list,
    query: str,
    limit: int,
    context_chars: int,
) -> list[SearchMatch]:
    """Regex pattern search."""
    pattern = re.compile(query, re.IGNORECASE)
    matches = []
    
    for doc in docs:
        content = server.blobs.get(doc.content_hash)
        if not content:
            continue
        
        for match in pattern.finditer(content):
            if len(matches) >= limit:
                break
            
            match_start = match.start()
            match_end = match.end()
            
            # Extract context
            start = max(0, match_start - context_chars // 2)
            end = min(len(content), match_end + context_chars // 2)
            context = content[start:end]
            
            matches.append(SearchMatch(
                doc_id=doc.id,
                span=SpanRef(doc_id=doc.id, start=start, end=end),
                span_id=None,
                score=1.0,
                context=context,
                highlight_start=match_start - start,
                highlight_end=match_end - start,
            ))
        
        if len(matches) >= limit:
            break
    
    return matches


async def _literal_search(
    server: "RLMServer",
    docs: list,
    query: str,
    limit: int,
    context_chars: int,
) -> list[SearchMatch]:
    """Literal string search."""
    query_lower = query.lower()
    matches = []
    
    for doc in docs:
        content = server.blobs.get(doc.content_hash)
        if not content:
            continue
        
        content_lower = content.lower()
        pos = 0
        
        while True:
            idx = content_lower.find(query_lower, pos)
            if idx == -1:
                break
            
            if len(matches) >= limit:
                break
            
            # Extract context
            start = max(0, idx - context_chars // 2)
            end = min(len(content), idx + len(query) + context_chars // 2)
            context = content[start:end]
            
            matches.append(SearchMatch(
                doc_id=doc.id,
                span=SpanRef(doc_id=doc.id, start=start, end=end),
                span_id=None,
                score=1.0,
                context=context,
                highlight_start=idx - start,
                highlight_end=idx - start + len(query),
            ))
            
            pos = idx + 1
        
        if len(matches) >= limit:
            break
    
    return matches


def _tokenize(text: str) -> list[str]:
    """Simple tokenization for BM25."""
    # Lowercase and split on non-alphanumeric
    tokens = re.findall(r'\w+', text.lower())
    return tokens


def _find_best_match_position(content: str, query: str) -> int:
    """Find best position in content for query match."""
    # Try literal match first
    idx = content.lower().find(query.lower())
    if idx >= 0:
        return idx
    
    # Try first query token
    tokens = _tokenize(query)
    if tokens:
        idx = content.lower().find(tokens[0])
        if idx >= 0:
            return idx
    
    # Default to start
    return 0
