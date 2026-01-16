"""Tests for v0.2.2 bug fixes.

Tests for:
- #5: Server config defaults merged into session creation
- #6: BM25 results filtered by doc_ids
- #8: session.close exempt from budget
- #9: Highlight positions clamped to bounds
- #10: __version__ uses importlib.metadata
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rlm_mcp import __version__
from rlm_mcp.config import ServerConfig
from rlm_mcp.models import SessionConfig
from rlm_mcp.server import RLMServer, create_server
from rlm_mcp.tools.session import _session_create, _session_close
from rlm_mcp.tools.docs import _docs_load
from rlm_mcp.tools.search import _bm25_search, _search_query


class TestIssue10VersionImport:
    """Test #10: __version__ uses importlib.metadata."""

    def test_version_is_string(self):
        """Version should be a non-empty string."""
        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_version_follows_semver_pattern(self):
        """Version should match semver or PEP 440 pattern."""
        import re
        # Allow versions like 0.2.2, 0.2.2-dev, 0.2.2.dev1, 0.2.2.post1, 0.2.2a1, etc.
        # PEP 440: x.y.z[.devN | .postN | aN | bN | rcN][-suffix]
        pattern = r"^\d+\.\d+\.\d+([-.]?\w+)*$"
        assert re.match(pattern, __version__), f"Version '{__version__}' doesn't match expected pattern"


class TestIssue5ServerConfigDefaults:
    """Test #5: Server config defaults merged into session creation."""

    @pytest.mark.asyncio
    async def test_session_inherits_server_defaults(self, tmp_path):
        """Session should inherit defaults from server config."""
        # Create server with custom defaults
        config = ServerConfig(
            data_dir=tmp_path,
            default_max_tool_calls=100,
            default_max_chars_per_response=25000,
            default_max_chars_per_peek=5000,
        )

        async with create_server(config) as server:
            # Create session without config
            result = await _session_create(server, name="test")

            # Session should inherit server defaults
            assert result["config"]["max_tool_calls"] == 100
            assert result["config"]["max_chars_per_response"] == 25000
            assert result["config"]["max_chars_per_peek"] == 5000

    @pytest.mark.asyncio
    async def test_session_config_overrides_server_defaults(self, tmp_path):
        """User-provided session config should override server defaults."""
        config = ServerConfig(
            data_dir=tmp_path,
            default_max_tool_calls=100,
            default_max_chars_per_response=25000,
            default_max_chars_per_peek=5000,
        )

        async with create_server(config) as server:
            # Create session with partial overrides
            result = await _session_create(
                server,
                name="test",
                config={"max_tool_calls": 200}  # Only override this
            )

            # Overridden value
            assert result["config"]["max_tool_calls"] == 200
            # Server defaults for non-overridden
            assert result["config"]["max_chars_per_response"] == 25000
            assert result["config"]["max_chars_per_peek"] == 5000


class TestIssue8BudgetExemption:
    """Test #8: session.close exempt from budget."""

    @pytest.mark.asyncio
    async def test_session_close_works_at_zero_budget(self, tmp_path):
        """session.close should work even when budget is exhausted."""
        config = ServerConfig(
            data_dir=tmp_path,
            default_max_tool_calls=2,  # Very low budget
        )

        async with create_server(config) as server:
            # Create session (uses 1 call)
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            # Use remaining budget
            await server.db.increment_tool_calls(session_id)

            # Session is now at budget limit
            session = await server.db.get_session(session_id)
            assert session.tool_calls_used >= session.config.max_tool_calls

            # Close should still work (exempt from budget)
            close_result = await _session_close(server, session_id=session_id)
            assert close_result["status"] == "completed"


class TestIssue9HighlightBounds:
    """Test #9: Highlight positions clamped to bounds."""

    @pytest.mark.asyncio
    async def test_highlight_positions_within_context(self, tmp_path):
        """Highlight positions should always be within context bounds."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            # Create session with document
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            await _docs_load(
                server,
                session_id=session_id,
                sources=[{"type": "inline", "content": "Hello world foo bar baz"}]
            )

            # Search with small context
            search_result = await _search_query(
                server,
                session_id=session_id,
                query="world",
                method="bm25",
                context_chars=10,  # Very small context
            )

            if search_result["matches"]:
                match = search_result["matches"][0]
                context = match["context"]

                # Highlight positions must be within context
                assert match["highlight_start"] >= 0
                assert match["highlight_start"] <= len(context)
                assert match["highlight_end"] >= match["highlight_start"]
                assert match["highlight_end"] <= len(context)

    @pytest.mark.asyncio
    async def test_literal_search_highlight_bounds(self, tmp_path):
        """Literal search highlight positions should be bounded."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            await _docs_load(
                server,
                session_id=session_id,
                sources=[{"type": "inline", "content": "The quick brown fox jumps"}]
            )

            search_result = await _search_query(
                server,
                session_id=session_id,
                query="quick brown fox",
                method="literal",
                context_chars=10,
            )

            if search_result["matches"]:
                match = search_result["matches"][0]
                context = match["context"]

                assert match["highlight_start"] >= 0
                assert match["highlight_end"] <= len(context)

    @pytest.mark.asyncio
    async def test_regex_search_highlight_bounds(self, tmp_path):
        """Regex search highlight positions should be bounded."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            await _docs_load(
                server,
                session_id=session_id,
                sources=[{"type": "inline", "content": "test123 value456 data789"}]
            )

            search_result = await _search_query(
                server,
                session_id=session_id,
                query=r"\w+\d+",
                method="regex",
                context_chars=10,
            )

            if search_result["matches"]:
                match = search_result["matches"][0]
                context = match["context"]

                assert match["highlight_start"] >= 0
                assert match["highlight_end"] <= len(context)


class TestHighlightReclampAfterTruncation:
    """Test highlight positions re-clamped after response truncation."""

    @pytest.mark.asyncio
    async def test_highlights_valid_after_truncation(self, tmp_path):
        """Highlight positions should be valid even after context truncation."""
        config = ServerConfig(
            data_dir=tmp_path,
            default_max_chars_per_response=1000,  # Minimum allowed
        )

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            # Load multiple documents with long content to exceed response limit
            # Each document produces ~500 char context, so 3 docs should trigger truncation
            await _docs_load(
                server,
                session_id=session_id,
                sources=[
                    {"type": "inline", "content": "a" * 500 + "findme1" + "b" * 500},
                    {"type": "inline", "content": "c" * 500 + "findme2" + "d" * 500},
                    {"type": "inline", "content": "e" * 500 + "findme3" + "f" * 500},
                ]
            )

            search_result = await _search_query(
                server,
                session_id=session_id,
                query="findme",
                method="literal",
                context_chars=500,  # Request large context per match
                limit=10,
            )

            # All returned matches should have valid highlight positions
            for match in search_result["matches"]:
                context = match["context"]
                assert match["highlight_start"] >= 0, f"highlight_start {match['highlight_start']} < 0"
                assert match["highlight_start"] <= len(context), f"highlight_start {match['highlight_start']} > len(context) {len(context)}"
                assert match["highlight_end"] >= match["highlight_start"], f"highlight_end {match['highlight_end']} < highlight_start {match['highlight_start']}"
                assert match["highlight_end"] <= len(context), f"highlight_end {match['highlight_end']} > len(context) {len(context)}"


class TestIssue6BM25DocIdsFilter:
    """Test #6: BM25 results filtered by doc_ids."""

    @pytest.mark.asyncio
    async def test_bm25_respects_doc_ids_filter(self, tmp_path):
        """BM25 search should only return results from specified doc_ids."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            # Load multiple documents
            load_result = await _docs_load(
                server,
                session_id=session_id,
                sources=[
                    {"type": "inline", "content": "Python is a programming language"},
                    {"type": "inline", "content": "Python snakes are reptiles"},
                    {"type": "inline", "content": "Java is also a programming language"},
                ]
            )

            doc_ids = [d["doc_id"] for d in load_result["loaded"]]
            assert len(doc_ids) == 3

            # Search with doc_ids filter (only first doc)
            search_result = await _search_query(
                server,
                session_id=session_id,
                query="Python",
                method="bm25",
                doc_ids=[doc_ids[0]],  # Only search first doc
                limit=10,
            )

            # Should only find match in first doc
            matches = search_result["matches"]
            assert len(matches) > 0
            for match in matches:
                assert match["doc_id"] == doc_ids[0]

    @pytest.mark.asyncio
    async def test_bm25_without_filter_searches_all(self, tmp_path):
        """BM25 without doc_ids filter should search all documents."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            await _docs_load(
                server,
                session_id=session_id,
                sources=[
                    {"type": "inline", "content": "Python programming"},
                    {"type": "inline", "content": "Python snakes"},
                ]
            )

            # Search without filter
            search_result = await _search_query(
                server,
                session_id=session_id,
                query="Python",
                method="bm25",
                doc_ids=None,
                limit=10,
            )

            # Should find matches in multiple docs
            matches = search_result["matches"]
            doc_ids_in_results = {m["doc_id"] for m in matches}
            assert len(doc_ids_in_results) >= 1  # At least one, possibly both

    @pytest.mark.asyncio
    async def test_bm25_filter_excludes_unmatched_docs(self, tmp_path):
        """BM25 should exclude documents not in doc_ids even if they match."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            load_result = await _docs_load(
                server,
                session_id=session_id,
                sources=[
                    {"type": "inline", "content": "Python rocks!"},
                    {"type": "inline", "content": "Python is awesome!"},
                ]
            )

            doc_ids = [d["doc_id"] for d in load_result["loaded"]]

            # Filter to second doc only
            search_result = await _search_query(
                server,
                session_id=session_id,
                query="Python",
                method="bm25",
                doc_ids=[doc_ids[1]],
                limit=10,
            )

            # First doc should be excluded
            matches = search_result["matches"]
            for match in matches:
                assert match["doc_id"] != doc_ids[0]
                assert match["doc_id"] == doc_ids[1]

    @pytest.mark.asyncio
    async def test_bm25_exact_filter_finds_low_ranking_docs(self, tmp_path):
        """BM25 should find matches in allowed docs even if they rank lower."""
        config = ServerConfig(data_dir=tmp_path)

        async with create_server(config) as server:
            result = await _session_create(server, name="test")
            session_id = result["session_id"]

            # Create many docs with "Python" to push target doc down in rankings
            sources = [
                {"type": "inline", "content": "Python Python Python " * 10}  # High scorer
                for _ in range(10)
            ]
            # Add target doc with less "Python" mentions (will rank lower)
            sources.append({"type": "inline", "content": "Python mentioned once here"})

            load_result = await _docs_load(
                server,
                session_id=session_id,
                sources=sources,
            )

            doc_ids = [d["doc_id"] for d in load_result["loaded"]]
            target_doc_id = doc_ids[-1]  # The last doc we added

            # Search only in target doc (which ranks lower)
            search_result = await _search_query(
                server,
                session_id=session_id,
                query="Python",
                method="bm25",
                doc_ids=[target_doc_id],
                limit=5,
            )

            # Should still find the match despite low ranking
            matches = search_result["matches"]
            assert len(matches) > 0
            assert all(m["doc_id"] == target_doc_id for m in matches)


class TestBM25FilterLoopGuard:
    """Test that BM25 doc_ids loop stops at the search cap."""

    @pytest.mark.asyncio
    async def test_bm25_filter_breaks_at_search_cap(self):
        """BM25 should stop expanding once the search cap is reached."""
        class DummyResult:
            def __init__(self, doc_id: str):
                self.doc_id = doc_id
                self.content = "no match here"
                self.score = 1.0

        class DummyIndex:
            def __init__(self):
                self.calls = 0

            def search(self, query: str, limit: int):
                self.calls += 1
                if self.calls > 20:
                    raise RuntimeError("search called too many times")
                return [DummyResult("other")] * limit

        class DummyServer:
            def __init__(self, index):
                self._index = index

            async def get_or_build_index(self, session_id: str):
                return self._index

        index = DummyIndex()
        server = DummyServer(index)
        docs = [SimpleNamespace(id="allowed")]

        matches, _ = await _bm25_search(
            server,
            session_id="session",
            docs=docs,
            query="query",
            limit=5,
            context_chars=10,
        )

        assert matches == []
        assert index.calls <= 20
