"""Index implementations for RLM-MCP.

BM25 index is lazy-built on first query and cached per session.
"""

from rlm_mcp.index.bm25 import SessionIndex

__all__ = ["SessionIndex"]
