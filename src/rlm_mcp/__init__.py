"""RLM-MCP: Recursive Language Model MCP Server.

A Model Context Protocol server that implements the RLM pattern from
Zhang et al. (2025), treating prompts as external environment objects
for programmatic manipulation.

Key features:
- Session-based document management
- On-demand chunking with multiple strategies
- BM25 search (lazy-built, cached per session)
- Artifact storage with span provenance
- GitHub export with secret scanning

Tool naming convention: rlm.<category>.<action>
"""

__version__ = "0.1.0"

from rlm_mcp.server import RLMServer, ToolNamingError, create_server, run_server

__all__ = ["RLMServer", "ToolNamingError", "create_server", "run_server", "__version__"]
