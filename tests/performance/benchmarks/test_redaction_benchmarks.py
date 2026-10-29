"""Pytest-benchmark performance tests for PII redaction execution speed and memory allocations."""

from __future__ import annotations

from app.privacy.pii_redaction import redact_pii

# Sample 5,000 character application text containing structured PII
SAMPLE_TEXT_FRAGMENT = (
    "Built high-performance microservices using Python, FastAPI, SQLAlchemy and Redis. "
)
SAMPLE_APPLICATION_TEXT = (
    "Application for Senior Engineering Role.\n"
    "Name: John Doe, Email: john.doe.candidate@example.com, Phone: +1 (555) 019-2834.\n"
    "Address: 123 Innovation Way, Suite 400, San Francisco, CA 94105.\n"
    "Government ID: SSN # 987-65-4321, Tax ID: TAX-99887766.\n"
    "Experience:\n" + (SAMPLE_TEXT_FRAGMENT * 30)
)


def test_redaction_baseline_benchmark(benchmark):
    """Benchmark baseline PII redaction speed over 5,000 character sample text."""
    result = benchmark(
        redact_pii,
        SAMPLE_APPLICATION_TEXT,
        enabled=True,
        policy="baseline",
    )
    assert result.changed is True
    assert "email" in result.counts


def test_redaction_strict_benchmark(benchmark):
    """Benchmark strict policy PII redaction speed over 5,000 character sample text."""
    result = benchmark(
        redact_pii,
        SAMPLE_APPLICATION_TEXT,
        enabled=True,
        policy="strict",
    )
    assert result.changed is True
    assert "street_address" in result.counts or "labelled_address" in result.counts


def test_redaction_disabled_benchmark(benchmark):
    """Benchmark disabled PII redaction pass-through latency."""
    result = benchmark(
        redact_pii,
        SAMPLE_APPLICATION_TEXT,
        enabled=False,
        policy="baseline",
    )
    assert result.changed is False
    assert result.counts == {}
