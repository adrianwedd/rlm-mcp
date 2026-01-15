"""Structured logging configuration for RLM-MCP.

Provides JSON-formatted logs with correlation IDs and rich context for
production observability.
"""

import contextvars
import json
import logging
from datetime import datetime
from typing import Any

# Context variable for correlation ID tracking across async operations
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'correlation_id', default=None
)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Outputs logs in JSON format with timestamp, level, logger name, message,
    and optional context fields like session_id, operation, correlation_id.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if set
        correlation_id = correlation_id_var.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id

        # Add extra fields if present
        if hasattr(record, "session_id"):
            log_data["session_id"] = record.session_id
        if hasattr(record, "operation"):
            log_data["operation"] = record.operation
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "extra"):
            log_data["extra"] = record.extra

        # Add error info if present
        if record.exc_info:
            log_data["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class StructuredLogger:
    """Helper for structured logging with context fields.

    Wraps standard Python logger to make it easy to add structured fields
    like session_id, operation, duration_ms, etc.
    """

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def log_operation(
        self,
        level: str,
        message: str,
        session_id: str | None = None,
        operation: str | None = None,
        duration_ms: int | None = None,
        **extra: Any
    ) -> None:
        """Log with structured fields.

        Args:
            level: Log level (INFO, WARNING, ERROR, etc.)
            message: Log message
            session_id: Optional session ID
            operation: Optional operation name (e.g., "rlm.search.query")
            duration_ms: Optional operation duration in milliseconds
            **extra: Additional fields to include in log
        """
        # Convert level string to logging constant
        log_level = getattr(logging, level.upper())

        # Create log record
        record = self.logger.makeRecord(
            self.logger.name,
            log_level,
            "(structured)",
            0,
            message,
            (),
            None
        )

        # Attach structured fields
        if session_id:
            record.session_id = session_id
        if operation:
            record.operation = operation
        if duration_ms is not None:
            record.duration_ms = duration_ms
        if extra:
            record.extra = extra

        # Emit log
        self.logger.handle(record)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log at INFO level."""
        self.log_operation("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log at WARNING level."""
        self.log_operation("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log at ERROR level."""
        self.log_operation("ERROR", message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log at DEBUG level."""
        self.log_operation("DEBUG", message, **kwargs)


def configure_logging(
    log_level: str = "INFO",
    structured: bool = True,
    log_file: str | None = None
) -> None:
    """Configure application logging.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR)
        structured: If True, use JSON formatter; if False, use human-readable
        log_file: Optional file path to write logs to
    """
    root_logger = logging.getLogger("rlm_mcp")
    root_logger.setLevel(log_level.upper())

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()

    if structured:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        )

    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)

        if structured:
            file_handler.setFormatter(StructuredFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )

        root_logger.addHandler(file_handler)
