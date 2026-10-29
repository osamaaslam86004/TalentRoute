"""Integration tests for app/audit/service.py audit event recording and helpers."""

from __future__ import annotations

import math
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.integrity import GENESIS_HASH
from app.audit.service import (
    list_audit_events,
    record_audit_event,
    safe_audit_metadata,
)
from app.core.security import Principal
from app.db.models.audit_chain_head import AuditChainHead
from app.db.models.audit_event import AuditEvent


@pytest.mark.asyncio
async def test_safe_audit_metadata_filtering() -> None:
    """Verify safe_audit_metadata filters unallowed keys and truncates values."""
    unfiltered: dict[str, Any] = {
        "status": "queued",
        "count": 42,
        "latency_ms": 120.5,
        "schema_valid": True,
        "unallowed_secret": "password123",
        "free_form_notes": "applicant details here",
        "label": "a" * 200,  # exceeds 160 chars
        "reason_codes": ["code1", "b" * 100],  # item exceeds 80 chars
        "nan_value": float("nan"),
    }

    sanitized = safe_audit_metadata(unfiltered)

    assert "unallowed_secret" not in sanitized
    assert "free_form_notes" not in sanitized
    assert "nan_value" not in sanitized
    assert sanitized["status"] == "queued"
    assert sanitized["count"] == 42
    assert sanitized["latency_ms"] == 120.5
    assert sanitized["schema_valid"] is True
    assert len(sanitized["label"]) == 160
    assert sanitized["reason_codes"][0] == "code1"
    assert len(sanitized["reason_codes"][1]) == 80


@pytest.mark.asyncio
async def test_safe_audit_metadata_none_or_empty() -> None:
    """Verify safe_audit_metadata handles None and empty input safely."""
    assert safe_audit_metadata(None) == {}
    assert safe_audit_metadata({}) == {}


@pytest.mark.asyncio
async def test_record_first_audit_event_genesis(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify recording the first event for a tenant initializes GENESIS_HASH and sequence 1."""
    event = await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id=principal_tenant_a.tenant_id,
        request_id="req-init-1",
        action="screening.created",
        resource_type="screening",
        resource_id="screen-001",
        metadata={"status": "queued"},
    )
    await db_session.commit()

    assert event.sequence_no == 1
    assert event.previous_hash == GENESIS_HASH
    assert event.tenant_id == "tenant-a"
    assert event.actor_subject == principal_tenant_a.subject
    assert len(event.event_hash) == 64

    # Verify AuditChainHead row state
    head = await db_session.scalar(
        select(AuditChainHead).where(AuditChainHead.tenant_id == "tenant-a")
    )
    assert head is not None
    assert head.last_sequence == 1
    assert head.last_hash == event.event_hash


@pytest.mark.asyncio
async def test_record_subsequent_audit_events_chaining(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify multiple events chain previous_hash to preceding event_hash sequentially."""
    event1 = await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id=principal_tenant_a.tenant_id,
        request_id="req-1",
        action="screening.created",
        resource_type="screening",
        resource_id="s1",
    )
    await db_session.commit()

    event2 = await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id=principal_tenant_a.tenant_id,
        request_id="req-2",
        action="screening.read",
        resource_type="screening",
        resource_id="s1",
    )
    await db_session.commit()

    assert event2.sequence_no == 2
    assert event2.previous_hash == event1.event_hash

    head = await db_session.scalar(
        select(AuditChainHead).where(AuditChainHead.tenant_id == "tenant-a")
    )
    assert head is not None
    assert head.last_sequence == 2
    assert head.last_hash == event2.event_hash


@pytest.mark.asyncio
async def test_record_audit_event_fallback_without_principal(
    db_session: AsyncSession,
) -> None:
    """Verify record_audit_event works with explicit subject and roles when principal is None."""
    event = await record_audit_event(
        db_session,
        principal=None,
        tenant_id="tenant-system",
        request_id=None,
        action="system.maintenance",
        resource_type="system",
        actor_subject="service:worker",
        actor_roles=["system_admin"],
    )
    await db_session.commit()

    assert event.actor_subject == "service:worker"
    assert event.actor_roles == ["system_admin"]
    assert event.sequence_no == 1


@pytest.mark.asyncio
async def test_list_audit_events_pagination_and_filtering(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify list_audit_events filters by tenant_id, action, and handles pagination."""
    # Seed events for tenant-a
    actions = ["screening.created", "screening.read", "screening.created", "review.submitted"]
    for idx, act in enumerate(actions, start=1):
        await record_audit_event(
            db_session,
            principal=principal_tenant_a,
            tenant_id="tenant-a",
            request_id=f"req-{idx}",
            action=act,
            resource_type="screening",
        )
    await db_session.commit()

    # List all events for tenant-a
    items, total = await list_audit_events(db_session, tenant_id="tenant-a", limit=10, offset=0)
    assert total == 4
    assert len(items) == 4

    # Filter by action
    created_items, created_count = await list_audit_events(
        db_session, tenant_id="tenant-a", action="screening.created", limit=10, offset=0
    )
    assert created_count == 2
    assert len(created_items) == 2
    assert all(item.action == "screening.created" for item in created_items)

    # Test offset and limit
    paged_items, paged_count = await list_audit_events(
        db_session, tenant_id="tenant-a", limit=2, offset=1
    )
    assert paged_count == 4
    assert len(paged_items) == 2
