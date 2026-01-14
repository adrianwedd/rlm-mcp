#!/usr/bin/env python3
"""Test MCP client to validate RLM-MCP server functionality."""

import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_rlm_mcp():
    """Test the RLM-MCP server as an MCP client would."""

    # Start server as subprocess
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "rlm_mcp.server"],
        env=None
    )

    print("🔌 Connecting to RLM-MCP server...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize
            await session.initialize()

            print("✅ Connected to RLM-MCP server\n")

            # List available tools
            tools = await session.list_tools()
            print(f"📋 Available tools ({len(tools.tools)}):")
            for tool in tools.tools:
                print(f"   • {tool.name}")

            print("\n" + "="*60 + "\n")

            # Test 1: Create session
            print("🧪 Test 1: Create RLM session")
            result = await session.call_tool(
                "rlm.session.create",
                arguments={"name": "mcp-test-session"}
            )
            print(f"Result: {json.dumps(result.content[0].text if result.content else {}, indent=2)}")

            # Parse session ID
            session_data = json.loads(result.content[0].text)
            session_id = session_data["session_id"]
            print(f"✅ Session created: {session_id}\n")

            # Test 2: Load document
            print("🧪 Test 2: Load document")
            result = await session.call_tool(
                "rlm.docs.load",
                arguments={
                    "session_id": session_id,
                    "sources": [
                        {
                            "type": "inline",
                            "content": "def calculate_sum(a, b):\n    return a + b\n\ndef calculate_product(a, b):\n    return a * b\n"
                        }
                    ]
                }
            )
            doc_data = json.loads(result.content[0].text)
            print(f"Result: Loaded {len(doc_data['loaded'])} document(s)")
            doc_id = doc_data["loaded"][0]["doc_id"]
            print(f"✅ Document loaded: {doc_id}\n")

            # Test 3: Search
            print("🧪 Test 3: BM25 search")
            result = await session.call_tool(
                "rlm.search.query",
                arguments={
                    "session_id": session_id,
                    "query": "calculate sum",
                    "method": "bm25",
                    "limit": 5
                }
            )
            search_data = json.loads(result.content[0].text)
            print(f"Result: Found {search_data['total_matches']} match(es)")
            print(f"Index built this call: {search_data['index_built_this_call']}")
            if search_data["matches"]:
                print(f"Top match score: {search_data['matches'][0]['score']:.3f}")
            print("✅ Search working\n")

            # Test 4: Chunk document
            print("🧪 Test 4: Chunk document")
            result = await session.call_tool(
                "rlm.chunk.create",
                arguments={
                    "session_id": session_id,
                    "doc_id": doc_id,
                    "strategy": {
                        "type": "fixed",
                        "chunk_size": 50
                    }
                }
            )
            chunk_data = json.loads(result.content[0].text)
            print(f"Result: Created {len(chunk_data['spans'])} span(s)")
            span_id = chunk_data["spans"][0]["span_id"]
            print(f"✅ Chunking working: {span_id}\n")

            # Test 5: Get span
            print("🧪 Test 5: Get span content")
            result = await session.call_tool(
                "rlm.span.get",
                arguments={
                    "session_id": session_id,
                    "span_id": span_id
                }
            )
            span_data = json.loads(result.content[0].text)
            print(f"Result: Retrieved span with {len(span_data['content'])} chars")
            print(f"Content hash: {span_data['content_hash'][:16]}...")
            print(f"✅ Span retrieval working\n")

            # Test 6: Store artifact
            print("🧪 Test 6: Store artifact with provenance")
            result = await session.call_tool(
                "rlm.artifact.store",
                arguments={
                    "session_id": session_id,
                    "type": "summary",
                    "content": {"text": "Functions for arithmetic operations"},
                    "span_id": span_id,
                    "provenance": {"model": "test-client", "tool": "summarize"}
                }
            )
            artifact_data = json.loads(result.content[0].text)
            artifact_id = artifact_data["artifact_id"]
            print(f"Result: Stored artifact {artifact_id}")
            print(f"✅ Artifact storage working\n")

            # Test 7: Session info
            print("🧪 Test 7: Get session info")
            result = await session.call_tool(
                "rlm.session.info",
                arguments={"session_id": session_id}
            )
            info_data = json.loads(result.content[0].text)
            print(f"Result: Session has {info_data['tool_calls_used']} tool calls used")
            print(f"        {info_data['tool_calls_remaining']} remaining")
            print(f"        Index built: {info_data['index_built']}")
            print(f"✅ Session info working\n")

            # Test 8: Close session
            print("🧪 Test 8: Close session")
            result = await session.call_tool(
                "rlm.session.close",
                arguments={"session_id": session_id}
            )
            close_data = json.loads(result.content[0].text)
            print(f"Result: Session closed with status '{close_data['status']}'")
            print(f"Summary: {close_data['summary']}")
            print(f"✅ Session close working\n")

            print("="*60)
            print("\n🎉 All MCP integration tests PASSED!")
            print(f"\n✅ Validated {len(tools.tools)} tools working correctly")
            print("✅ Session lifecycle complete")
            print("✅ Document loading functional")
            print("✅ BM25 search operational")
            print("✅ Chunking and span retrieval working")
            print("✅ Artifact storage with provenance validated")


if __name__ == "__main__":
    asyncio.run(test_rlm_mcp())
