"""Tests for structured logging infrastructure."""

import io
import json
import logging
from datetime import datetime

import pytest

from rlm_mcp.logging_config import (
    StructuredFormatter,
    StructuredLogger,
    correlation_id_var,
    configure_logging,
)


def test_structured_formatter_basic():
    """Test that StructuredFormatter emits valid JSON."""
    # Create logger with StringIO handler
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test_basic")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Emit a log
    logger.info("Test message")

    # Parse JSON output
    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    # Verify structure
    assert log_data["level"] == "INFO"
    assert log_data["logger"] == "test_basic"
    assert log_data["message"] == "Test message"
    assert "timestamp" in log_data

    # Verify timestamp format (ISO 8601 with Z suffix)
    assert log_data["timestamp"].endswith("Z")
    datetime.fromisoformat(log_data["timestamp"].rstrip("Z"))  # Should parse


def test_structured_formatter_with_correlation_id():
    """Test that correlation IDs are included when set."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test_correlation")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Set correlation ID
    test_correlation_id = "test-correlation-123"
    correlation_id_var.set(test_correlation_id)

    try:
        logger.info("Test with correlation")

        output = log_stream.getvalue()
        log_data = json.loads(output.strip())

        assert log_data["correlation_id"] == test_correlation_id
    finally:
        correlation_id_var.set(None)


def test_structured_formatter_without_correlation_id():
    """Test that correlation_id field is omitted when not set."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test_no_correlation")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Ensure correlation ID is not set
    correlation_id_var.set(None)

    logger.info("Test without correlation")

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert "correlation_id" not in log_data


def test_structured_formatter_with_extra_fields():
    """Test that extra fields like session_id, operation are included."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test_extra")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Create record with extra fields
    record = logger.makeRecord(
        logger.name,
        logging.INFO,
        "(test)",
        0,
        "Test with extras",
        (),
        None
    )
    record.session_id = "session-123"
    record.operation = "rlm.test.operation"
    record.duration_ms = 42
    record.extra = {"custom_field": "custom_value"}

    logger.handle(record)

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["session_id"] == "session-123"
    assert log_data["operation"] == "rlm.test.operation"
    assert log_data["duration_ms"] == 42
    assert log_data["extra"] == {"custom_field": "custom_value"}


def test_structured_formatter_with_exception():
    """Test that exceptions are formatted correctly."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    logger = logging.getLogger("test_exception")
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)

    try:
        raise ValueError("Test error")
    except ValueError:
        logger.exception("Error occurred")

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "ERROR"
    assert log_data["message"] == "Error occurred"
    assert "exc_info" in log_data
    assert "ValueError: Test error" in log_data["exc_info"]


def test_structured_logger_info():
    """Test StructuredLogger.info() method."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    test_logger = StructuredLogger("test_info")
    test_logger.logger.setLevel(logging.INFO)
    test_logger.logger.addHandler(handler)

    test_logger.info(
        "Operation started",
        session_id="session-456",
        operation="rlm.test.start",
        custom_key="custom_value"
    )

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "INFO"
    assert log_data["message"] == "Operation started"
    assert log_data["session_id"] == "session-456"
    assert log_data["operation"] == "rlm.test.start"
    assert log_data["extra"]["custom_key"] == "custom_value"


def test_structured_logger_warning():
    """Test StructuredLogger.warning() method."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    test_logger = StructuredLogger("test_warning")
    test_logger.logger.setLevel(logging.WARNING)
    test_logger.logger.addHandler(handler)

    test_logger.warning("Warning message", session_id="session-789")

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "WARNING"
    assert log_data["message"] == "Warning message"


def test_structured_logger_error():
    """Test StructuredLogger.error() method."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    test_logger = StructuredLogger("test_error")
    test_logger.logger.setLevel(logging.ERROR)
    test_logger.logger.addHandler(handler)

    test_logger.error(
        "Error occurred",
        session_id="session-error",
        operation="rlm.test.fail",
        duration_ms=100,
        error="Something went wrong"
    )

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "ERROR"
    assert log_data["message"] == "Error occurred"
    assert log_data["session_id"] == "session-error"
    assert log_data["operation"] == "rlm.test.fail"
    assert log_data["duration_ms"] == 100
    assert log_data["extra"]["error"] == "Something went wrong"


def test_structured_logger_debug():
    """Test StructuredLogger.debug() method."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredFormatter())

    test_logger = StructuredLogger("test_debug")
    test_logger.logger.setLevel(logging.DEBUG)
    test_logger.logger.addHandler(handler)

    test_logger.debug("Debug message")

    output = log_stream.getvalue()
    log_data = json.loads(output.strip())

    assert log_data["level"] == "DEBUG"
    assert log_data["message"] == "Debug message"


def test_configure_logging_structured():
    """Test configure_logging with structured=True."""
    # Configure with structured JSON format
    configure_logging(log_level="INFO", structured=True)

    # Get root logger (configure_logging sets up "rlm_mcp" root)
    root_logger = logging.getLogger("rlm_mcp")

    # Verify it's configured
    assert root_logger.level == logging.INFO
    assert len(root_logger.handlers) > 0

    # Verify formatter is StructuredFormatter
    handler = root_logger.handlers[0]
    assert isinstance(handler.formatter, StructuredFormatter)


def test_configure_logging_human_readable():
    """Test configure_logging with structured=False."""
    # Configure with human-readable format
    configure_logging(log_level="DEBUG", structured=False)

    # Get root logger (configure_logging sets up "rlm_mcp" root)
    root_logger = logging.getLogger("rlm_mcp")

    # Verify it's configured
    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) > 0

    # Verify formatter is standard Formatter (not StructuredFormatter)
    handler = root_logger.handlers[0]
    assert not isinstance(handler.formatter, StructuredFormatter)
    assert isinstance(handler.formatter, logging.Formatter)


def test_configure_logging_with_file(tmp_path):
    """Test configure_logging writes to log file."""
    log_file = tmp_path / "test.log"

    # Configure with file output
    configure_logging(
        log_level="INFO",
        structured=True,
        log_file=str(log_file)
    )

    # Get logger and emit log
    logger = logging.getLogger("rlm_mcp.file_test")
    logger.info("Test file logging")

    # Verify file was created and contains JSON
    assert log_file.exists()
    log_content = log_file.read_text()
    log_data = json.loads(log_content.strip())

    assert log_data["message"] == "Test file logging"
    assert log_data["level"] == "INFO"


def test_correlation_id_isolation():
    """Test that correlation IDs don't leak between operations."""
    # Set correlation ID
    correlation_id_var.set("test-123")
    assert correlation_id_var.get() == "test-123"

    # Clear it
    correlation_id_var.set(None)
    assert correlation_id_var.get() is None

    # Verify it stays cleared
    assert correlation_id_var.get() is None
