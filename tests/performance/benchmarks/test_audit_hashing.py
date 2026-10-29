"""Pytest-benchmark performance tests for canonical JSON formatting and SHA-256 event hashing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.audit.integrity import GENESIS_HASH, audit_hash_payload, calculate_event_hash


@pytest.fixture
def sample_audit_payload():
    """Return a pre-constructed audit hash payload dict."""
    return audit_hash_payload(
        event_id="550e8400-e29b-41d4-a716-446655440000",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-perf-benchmark",
        actor_subject="recruiter-perf-user",
        actor_roles=["recruiter", "service"],
        request_id="req-benchmark-12345",
        action="screening.created",
        resource_type="screening",
        resource_id="sc-99887766",
        outcome="success",
        metadata={
            "status": "queued",
            "model_version": "gpt-4o-mini",
            "prompt_version": "v1.1.0",
            "count": 10,
        },
        sequence_no=100,
        previous_hash=GENESIS_HASH,
    )


def test_audit_hash_payload_benchmark(benchmark):
    """Benchmark audit_hash_payload creation speed."""
    now = datetime.now(UTC)
    result = benchmark(
        audit_hash_payload,
        event_id="550e8400-e29b-41d4-a716-446655440000",
        occurred_at=now,
        tenant_id="tenant-perf-benchmark",
        actor_subject="recruiter-perf-user",
        actor_roles=["recruiter", "service"],
        request_id="req-benchmark-12345",
        action="screening.created",
        resource_type="screening",
        resource_id="sc-99887766",
        outcome="success",
        metadata={"status": "queued", "count": 10},
        sequence_no=100,
        previous_hash=GENESIS_HASH,
    )
    assert result["sequence_no"] == 100


def test_calculate_event_hash_benchmark(benchmark, sample_audit_payload):
    """Benchmark SHA-256 canonical event hash calculation speed."""
    event_hash = benchmark(calculate_event_hash, sample_audit_payload)
    assert len(event_hash) == 64


def test_audit_chain_hashing_end_to_end_benchmark(benchmark):
    """Benchmark full payload construction and SHA-256 hashing pipeline."""
    now = datetime.now(UTC)

    def _pipeline():
        payload = audit_hash_payload(
            event_id="550e8400-e29b-41d4-a716-446655440000",
            occurred_at=now,
            tenant_id="tenant-perf-benchmark",
            actor_subject="recruiter-perf-user",
            actor_roles=["recruiter", "service"],
            request_id="req-benchmark-12345",
            action="screening.created",
            resource_type="screening",
            resource_id="sc-99887766",
            outcome="success",
            metadata={"status": "queued"},
            sequence_no=1,
            previous_hash=GENESIS_HASH,
        )
        return calculate_event_hash(payload)

    event_hash = benchmark(_pipeline)
    assert len(event_hash) == 64
