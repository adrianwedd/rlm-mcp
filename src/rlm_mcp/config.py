"""Configuration loading for RLM-MCP server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


DEFAULT_DATA_DIR = Path.home() / ".rlm-mcp"


class ServerConfig(BaseModel):
    """Server-level configuration."""
    data_dir: Path = Field(default=DEFAULT_DATA_DIR)
    database_path: Path | None = None  # Defaults to data_dir/rlm.db
    blob_dir: Path | None = None  # Defaults to data_dir/blobs
    index_dir: Path | None = None  # Defaults to data_dir/indexes
    
    # Default session limits (can be overridden per-session)
    default_max_tool_calls: int = 500
    default_max_chars_per_response: int = 50_000
    default_max_chars_per_peek: int = 10_000
    
    # Tool naming: strict by default (fail if SDK doesn't support canonical names)
    # Set to True only for experimentation with older SDKs
    allow_noncanonical_tool_names: bool = False

    # Logging configuration
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    structured_logging: bool = True  # JSON format vs human-readable
    log_file: str | None = None  # Optional file path for logs
    
    def model_post_init(self, __context: Any) -> None:
        """Set derived paths after initialization."""
        if self.database_path is None:
            self.database_path = self.data_dir / "rlm.db"
        if self.blob_dir is None:
            self.blob_dir = self.data_dir / "blobs"
        if self.index_dir is None:
            self.index_dir = self.data_dir / "indexes"


def load_config(config_path: Path | None = None) -> ServerConfig:
    """Load server configuration from YAML file.
    
    Args:
        config_path: Path to config file. Defaults to ~/.rlm-mcp/config.yaml
        
    Returns:
        ServerConfig instance
    """
    if config_path is None:
        config_path = DEFAULT_DATA_DIR / "config.yaml"
    
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        return ServerConfig(**data)
    
    return ServerConfig()


def ensure_directories(config: ServerConfig) -> None:
    """Ensure all required directories exist."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    
    if config.blob_dir:
        config.blob_dir.mkdir(parents=True, exist_ok=True)
    
    if config.index_dir:
        config.index_dir.mkdir(parents=True, exist_ok=True)
    
    # Ensure database directory exists
    if config.database_path:
        config.database_path.parent.mkdir(parents=True, exist_ok=True)
