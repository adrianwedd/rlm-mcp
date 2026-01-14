"""Test fixtures for RLM-MCP."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from rlm_mcp.config import ServerConfig
from rlm_mcp.server import RLMServer, create_server
from rlm_mcp.storage import BlobStore, Database


@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def config(temp_dir: Path) -> ServerConfig:
    """Create test configuration."""
    return ServerConfig(
        data_dir=temp_dir,
        database_path=temp_dir / "test.db",
        blob_dir=temp_dir / "blobs",
        index_dir=temp_dir / "indexes",
    )


@pytest_asyncio.fixture
async def database(config: ServerConfig) -> AsyncGenerator[Database, None]:
    """Create test database."""
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(config.database_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def blob_store(config: ServerConfig) -> BlobStore:
    """Create test blob store."""
    config.blob_dir.mkdir(parents=True, exist_ok=True)
    return BlobStore(config.blob_dir)


@pytest_asyncio.fixture
async def server(config: ServerConfig) -> AsyncGenerator[RLMServer, None]:
    """Create test server."""
    async with create_server(config) as srv:
        yield srv


# --- Sample Content Fixtures ---

@pytest.fixture
def sample_python_code() -> str:
    """Sample Python code for testing."""
    return '''"""Sample module for testing."""

def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


class Calculator:
    """Simple calculator class."""
    
    def __init__(self):
        self.result = 0
    
    def add(self, value: int) -> "Calculator":
        self.result += value
        return self
    
    def subtract(self, value: int) -> "Calculator":
        self.result -= value
        return self
    
    def reset(self) -> "Calculator":
        self.result = 0
        return self
'''


@pytest.fixture
def sample_log_lines() -> str:
    """Sample log lines for testing."""
    return """2024-01-15 10:00:00 INFO Starting application
2024-01-15 10:00:01 DEBUG Loading configuration from /etc/app/config.yaml
2024-01-15 10:00:02 INFO Configuration loaded successfully
2024-01-15 10:00:03 DEBUG Connecting to database at localhost:5432
2024-01-15 10:00:04 INFO Database connection established
2024-01-15 10:00:05 WARNING High memory usage detected: 85%
2024-01-15 10:00:06 INFO Processing batch 1 of 10
2024-01-15 10:00:07 DEBUG Processed 100 records
2024-01-15 10:00:08 INFO Processing batch 2 of 10
2024-01-15 10:00:09 ERROR Failed to process record 42: Invalid format
2024-01-15 10:00:10 INFO Retrying record 42
2024-01-15 10:00:11 INFO Successfully processed record 42
"""


@pytest.fixture
def sample_markdown() -> str:
    """Sample markdown for testing."""
    return """# Project Documentation

## Overview

This is a sample project for testing the RLM-MCP server.

## Installation

Run the following command:

```bash
pip install rlm-mcp
```

## Usage

### Basic Example

```python
from rlm_mcp import create_server

async with create_server() as server:
    # Use the server
    pass
```

## API Reference

### Session Management

- `rlm.session.create` - Create a new session
- `rlm.session.info` - Get session info
- `rlm.session.close` - Close a session

## License

MIT License
"""


@pytest.fixture  
def sample_secrets() -> str:
    """Sample content with secrets for testing redaction."""
    return """
# Configuration
api_key = "sk-1234567890abcdefghijklmnop"
database_password = "super_secret_password_123"
github_token = "ghp_abcdefghijklmnopqrstuvwxyz123456"

# AWS credentials
aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"
aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# Bearer token
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.example
"""
