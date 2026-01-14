#!/usr/bin/env python3
"""Validate that tools are registered with canonical names."""

import asyncio
from rlm_mcp.server import create_server
from rlm_mcp.config import load_config


async def main():
    config = load_config()

    async with create_server(config) as server:
        # Get tools from FastMCP's tool manager
        tools = server.mcp._tool_manager._tools

        print("Registered tools:")
        print("-" * 60)

        expected_prefixes = [
            "rlm.session.",
            "rlm.docs.",
            "rlm.chunk.",
            "rlm.span.",
            "rlm.search.",
            "rlm.artifact.",
            "rlm.export.",
        ]

        canonical_count = 0
        non_canonical_count = 0

        for tool_name in sorted(tools.keys()):
            is_canonical = any(tool_name.startswith(prefix) for prefix in expected_prefixes)
            status = "✓" if is_canonical else "✗"
            print(f"{status} {tool_name}")

            if is_canonical:
                canonical_count += 1
            else:
                non_canonical_count += 1

        print("-" * 60)
        print(f"Total tools: {len(tools)}")
        print(f"Canonical names: {canonical_count}")
        print(f"Non-canonical names: {non_canonical_count}")

        if non_canonical_count > 0:
            print("\n❌ ERROR: Some tools are not using canonical naming!")
            return 1
        else:
            print("\n✅ SUCCESS: All tools use canonical naming (rlm.category.action)")
            return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
