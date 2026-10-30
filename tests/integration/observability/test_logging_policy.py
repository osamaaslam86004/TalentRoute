"""Integration tests for structured logging, exception scrubbing, and privacy policy enforcement."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import pytest

from app.core.config import Settings
from app.observability.context import clear_context, context_scope
from app.observability.logging import ConsoleFormatter, ContextFilter, JsonFormatter
from app.observability.policy import (
    TelemetryPolicyError,
    actor_hash,
    enforce_langsmith_policy,
    exception_type_name,
    stable_error_code,
)


class CustomCodedException(Exception):
    """Custom exception class with a machine-readable error_code attribute."""

    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def test_json_formatter_correlation_fields() -> None:
    """Verify JsonFormatter incorporates static metadata and active correlation context."""
    formatter = JsonFormatter(
        service="screening-service",
        version="1.2.0",
        environment="development",
        role="api",
        scrub_exceptions=False,
    )

    clear_context()
    with context_scope(
        request_id="req-abc-123",
        tenant_id="tenant-xyz-789",
        screening_id="screen-456",
    ):
        logger = logging.getLogger("test.json.formatter")
        record = logger.makeRecord(
            name="test.json.formatter",
            level=logging.INFO,
            fn="test_file.py",
            lno=42,
            msg="Candidate screening initialized",
            args=(),
            exc_info=None,
        )

        filter_obj = ContextFilter()
        filter_obj.filter(record)

        formatted_str = formatter.format(record)
        data: dict[str, Any] = json.loads(formatted_str)

        assert data["service"] == "screening-service"
        assert data["version"] == "1.2.0"
        assert data["environment"] == "development"
        assert data["role"] == "api"
        assert data["level"] == "INFO"
        assert data["message"] == "Candidate screening initialized"
        assert data["request_id"] == "req-abc-123"
        assert data["tenant_id"] == "tenant-xyz-789"
        assert data["screening_id"] == "screen-456"

    clear_context()


def test_json_formatter_extra_allowlist_filtering() -> None:
    """Verify JsonFormatter filters unapproved extra fields according to policy."""
    formatter = JsonFormatter(
        service="screening-service",
        version="1.2.0",
        environment="development",
        role="api",
        scrub_exceptions=False,
    )

    logger = logging.getLogger("test.extra")
    record = logger.makeRecord(
        name="test.extra",
        level=logging.INFO,
        fn="test_file.py",
        lno=10,
        msg="HTTP request completed",
        args=(),
        exc_info=None,
        extra={
            "event": "http.request.completed",
            "status_code": 200,
            "duration_ms": 15.5,
            "outcome": "success",
            # Unapproved extra fields below
            "candidate_email": "jane.doe@example.com",
            "raw_resume_text": "Sensitive candidate experience details...",
            "secret_key": "super-secret-token",
        },
    )

    filter_obj = ContextFilter()
    filter_obj.filter(record)

    formatted_data: dict[str, Any] = json.loads(formatter.format(record))

    # Allowed extras present
    assert formatted_data["event"] == "http.request.completed"
    assert formatted_data["status_code"] == 200
    assert formatted_data["duration_ms"] == 15.5
    assert formatted_data["outcome"] == "success"

    # Forbidden extras dropped
    assert "candidate_email" not in formatted_data
    assert "raw_resume_text" not in formatted_data
    assert "secret_key" not in formatted_data


def test_json_formatter_exception_scrubbing_in_production() -> None:
    """Verify exception scrubbing in production mode removes raw messages and formats frame summaries."""
    formatter = JsonFormatter(
        service="screening-service",
        version="1.2.0",
        environment="production",
        role="api",
        scrub_exceptions=True,
    )

    try:
        raise CustomCodedException(
            "Database connection failed with credentials db_user:secret_pass",
            error_code="db_connection_timeout",
        )
    except CustomCodedException:
        exc_info = sys.exc_info()

    logger = logging.getLogger("test.scrub")
    record = logger.makeRecord(
        name="test.scrub",
        level=logging.ERROR,
        fn="test_file.py",
        lno=99,
        msg="Unhandled service exception",
        args=(),
        exc_info=exc_info,
    )

    formatted_data: dict[str, Any] = json.loads(formatter.format(record))

    # Must NOT contain raw exception string or credentials
    assert "exception" not in formatted_data
    assert "secret_pass" not in json.dumps(formatted_data)
    assert "db_user" not in json.dumps(formatted_data)

    # Must contain scrubbed exception metadata
    assert formatted_data["exc_type"].endswith("CustomCodedException")
    assert formatted_data["exc_code"] == "db_connection_timeout"
    assert isinstance(formatted_data["exc_frames"], list)
    assert len(formatted_data["exc_frames"]) > 0


def test_console_formatter_formatting() -> None:
    """Test ConsoleFormatter appends correlation context when active."""
    formatter = ConsoleFormatter()

    clear_context()
    with context_scope(request_id="req-console-123", tenant_id="tenant-456"):
        logger = logging.getLogger("test.console")
        record = logger.makeRecord(
            name="test.console",
            level=logging.INFO,
            fn="test_file.py",
            lno=1,
            msg="Task processing started",
            args=(),
            exc_info=None,
        )

        ContextFilter().filter(record)
        output = formatter.format(record)

        assert "Task processing started" in output
        assert "[request_id=req-console-123 tenant_id=tenant-456]" in output

    clear_context()


def test_actor_hash_security() -> None:
    """Verify actor_hash generates deterministic HMAC hashes when salt is provided and None without salt."""
    settings_salted = Settings(telemetry_hash_salt="secret-salt-32-chars-long-val")
    settings_unsalted = Settings(telemetry_hash_salt="")

    subject = "applicant@example.com"
    hash1 = actor_hash(subject, settings_salted)
    hash2 = actor_hash(subject, settings_salted)

    assert hash1 is not None
    assert len(hash1) == 16
    assert hash1 == hash2  # Deterministic

    # Returns None when salt or subject is missing to prevent unsalted hash dictionary attacks
    assert actor_hash(subject, settings_unsalted) is None
    assert actor_hash(None, settings_salted) is None


def test_exception_type_name_and_stable_error_code_helpers() -> None:
    """Test exception class naming and stable error code validation."""
    exc = ValueError("bad value")
    assert exception_type_name(exc) == "ValueError"

    coded_exc = CustomCodedException("msg", error_code="rate_limit_exceeded")
    assert stable_error_code(coded_exc) == "rate_limit_exceeded"

    # Reject non-code shaped strings (e.g., messages with spaces or special characters)
    uncoded_exc = CustomCodedException("msg", error_code="Invalid Code With Spaces!")
    assert stable_error_code(uncoded_exc) is None


def test_enforce_langsmith_policy_guard() -> None:
    """Verify enforce_langsmith_policy blocks startup in production if third-party tracing env vars are active."""
    prod_settings = Settings(environment="production", allow_langsmith_tracing=False)
    env_active = {"LANGCHAIN_TRACING_V2": "true", "LANGCHAIN_API_KEY": "ls-key-123"}

    with pytest.raises(
        TelemetryPolicyError, match="LangChain/LangSmith tracing is enabled"
    ):
        enforce_langsmith_policy(prod_settings, env_active)

    # Allowed when explicitly configured
    allowed_prod_settings = Settings(
        environment="production", allow_langsmith_tracing=True
    )
    flags = enforce_langsmith_policy(allowed_prod_settings, env_active)
    assert "LANGCHAIN_TRACING_V2" in flags

    # Allowed in development
    dev_settings = Settings(environment="development", allow_langsmith_tracing=False)
    flags_dev = enforce_langsmith_policy(dev_settings, env_active)
    assert "LANGCHAIN_TRACING_V2" in flags_dev
