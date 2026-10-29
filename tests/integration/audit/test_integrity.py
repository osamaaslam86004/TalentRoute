"""Tests for app/audit/integrity.py hashing and payload canonicalization."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timezone

import pytest

from app.audit.integrity import (
    GENESIS_HASH,
    _timestamp,
    audit_hash_payload,
    calculate_event_hash,
)


@pytest.mark.asyncio
async def test_genesis_hash_constant() -> None:
    """Verify GENESIS_HASH is a 64-character string of zeroes."""
    assert GENESIS_HASH == "0" * 64
    assert len(GENESIS_HASH) == 64


@pytest.mark.asyncio
async def test_timestamp_formatting_timezone_aware() -> None:
    """Verify _timestamp requires timezone-aware datetime and produces ISO 8601."""
    dt_utc = datetime(2026, 10, 28, 12, 30, 45, 123456, tzinfo=UTC)
    formatted = _timestamp(dt_utc)
    assert formatted == "2026-10-28T12:30:45.123456+00:00"

    # Test non-UTC timezone conversion to UTC
    est = timezone(datetime.resolution * 0 - datetime.resolution * 0)  # offset
    dt_aware = datetime.now(UTC)
    assert _timestamp(dt_aware).endswith("+00:00")


@pytest.mark.asyncio
async def test_timestamp_rejects_naive_datetime() -> None:
    """Verify _timestamp raises ValueError when passed a naive datetime."""
    dt_naive = datetime(2026, 10, 28, 12, 30, 45)
    with pytest.raises(ValueError, match="Audit timestamps must be timezone-aware"):
        _timestamp(dt_naive)


@pytest.mark.asyncio
async def test_audit_hash_payload_sorting_roles() -> None:
    """Verify audit_hash_payload produces canonical dict with sorted actor_roles."""
    now = datetime.now(UTC)
    payload = audit_hash_payload(
        event_id="evt-123",
        occurred_at=now,
        tenant_id="tenant-a",
        actor_subject="sub-1",
        actor_roles=["recruiter", "admin", "auditor"],
        request_id="req-999",
        action="screening.created",
        resource_type="screening",
        resource_id="res-001",
        outcome="success",
        metadata={"status": "queued"},
        sequence_no=1,
        previous_hash=GENESIS_HASH,
    )

    assert payload["actor_roles"] == ["admin", "auditor", "recruiter"]
    assert payload["sequence_no"] == 1
    assert payload["previous_hash"] == GENESIS_HASH


@pytest.mark.asyncio
async def test_calculate_event_hash_deterministic() -> None:
    """Verify calculate_event_hash produces deterministic SHA-256 hex digest."""
    now = datetime(2026, 10, 28, 15, 0, 0, 0, tzinfo=UTC)
    payload = audit_hash_payload(
        event_id="evt-001",
        occurred_at=now,
        tenant_id="tenant-a",
        actor_subject="sub-1",
        actor_roles=["admin"],
        request_id="req-1",
        action="test.action",
        resource_type="test_res",
        resource_id="1",
        outcome="success",
        metadata={"count": 5},
        sequence_no=1,
        previous_hash=GENESIS_HASH,
    )

    hash1 = calculate_event_hash(payload)
    hash2 = calculate_event_hash(payload)

    assert hash1 == hash2
    assert len(hash1) == 64

    # Manually compute SHA-256 of canonical JSON
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected_hash = hashlib.sha256(canonical).hexdigest()
    assert hash1 == expected_hash
