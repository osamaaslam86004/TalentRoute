"""Integration tests for app/audit/service.py audit chain verification and tamper detection."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import (
    record_audit_event,
    verify_audit_chain,
)
from app.core.security import Principal
from app.db.models.audit_chain_head import AuditChainHead
from app.db.models.audit_event import AuditEvent


@pytest.mark.asyncio
async def test_verify_audit_chain_clean_chain(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify an untampered audit chain returns valid=True with correct event count."""
    for i in range(3):
        await record_audit_event(
            db_session,
            principal=principal_tenant_a,
            tenant_id="tenant-a",
            request_id=f"req-{i}",
            action="screening.created",
            resource_type="screening",
            metadata={"count": i},
        )
    await db_session.commit()

    res = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res["valid"] is True
    assert res["event_count"] == 3
    assert res["head_sequence"] == 3
    assert res["first_failure_sequence"] is None
    assert res["failure_reason"] is None


@pytest.mark.asyncio
async def test_verify_audit_chain_tamper_previous_hash_mismatch(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify modifying an event's previous_hash triggers previous_hash_mismatch failure."""
    for i in range(2):
        await record_audit_event(
            db_session,
            principal=principal_tenant_a,
            tenant_id="tenant-a",
            request_id=f"req-{i}",
            action="screening.created",
            resource_type="screening",
        )
    await db_session.commit()

    # Tamper with the previous_hash of event sequence 2
    events = (
        await db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == "tenant-a")
            .order_by(AuditEvent.sequence_no.asc())
        )
    ).all()
    event2 = events[1]
    event2.previous_hash = "f" * 64
    await db_session.commit()

    res = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res["valid"] is False
    assert res["first_failure_sequence"] == 2
    assert res["failure_reason"] == "previous_hash_mismatch"


@pytest.mark.asyncio
async def test_verify_audit_chain_tamper_event_hash_mismatch(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify altering an event payload post-commit triggers event_hash_mismatch failure."""
    await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id="tenant-a",
        request_id="req-1",
        action="screening.created",
        resource_type="screening",
        outcome="success",
    )
    await db_session.commit()

    # Tamper with action without recomputing event_hash
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.tenant_id == "tenant-a")
    )
    assert event is not None
    event.action = "screening.deleted"  # altered payload
    await db_session.commit()

    res = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res["valid"] is False
    assert res["first_failure_sequence"] == 1
    assert res["failure_reason"] == "event_hash_mismatch"


@pytest.mark.asyncio
async def test_verify_audit_chain_tamper_sequence_gap(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify a gap or reordering in sequence numbers triggers sequence_gap_or_reordering."""
    for i in range(3):
        await record_audit_event(
            db_session,
            principal=principal_tenant_a,
            tenant_id="tenant-a",
            request_id=f"req-{i}",
            action="screening.created",
            resource_type="screening",
        )
    await db_session.commit()

    # Change sequence_no of event 2 to 5
    events = (
        await db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == "tenant-a")
            .order_by(AuditEvent.sequence_no.asc())
        )
    ).all()
    events[1].sequence_no = 5
    await db_session.commit()

    res = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res["valid"] is False
    assert res["first_failure_sequence"] == 5 or res["first_failure_sequence"] == 2
    assert res["failure_reason"] in {"sequence_gap_or_reordering", "previous_hash_mismatch"}


@pytest.mark.asyncio
async def test_verify_audit_chain_tamper_chain_head_mismatch(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
) -> None:
    """Verify tampering with AuditChainHead triggers chain_head_mismatch."""
    await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id="tenant-a",
        request_id="req-1",
        action="screening.created",
        resource_type="screening",
    )
    await db_session.commit()

    # Tamper with stored chain head last_sequence
    head = await db_session.scalar(
        select(AuditChainHead).where(AuditChainHead.tenant_id == "tenant-a")
    )
    assert head is not None
    head.last_sequence = 99
    await db_session.commit()

    res = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res["valid"] is False
    assert res["failure_reason"] == "chain_head_mismatch"


@pytest.mark.asyncio
async def test_multi_tenant_audit_isolation(
    db_session: AsyncSession,
    principal_tenant_a: Principal,
    principal_tenant_b: Principal,
) -> None:
    """Verify Tenant A's audit chain and verification are completely isolated from Tenant B."""
    # Seed Tenant A events
    await record_audit_event(
        db_session,
        principal=principal_tenant_a,
        tenant_id="tenant-a",
        request_id="req-a-1",
        action="screening.created",
        resource_type="screening",
    )
    await db_session.commit()

    # Seed Tenant B events
    await record_audit_event(
        db_session,
        principal=principal_tenant_b,
        tenant_id="tenant-b",
        request_id="req-b-1",
        action="review.submitted",
        resource_type="review",
    )
    await db_session.commit()

    # Verify Tenant A chain
    res_a = await verify_audit_chain(db_session, tenant_id="tenant-a")
    assert res_a["valid"] is True
    assert res_a["event_count"] == 1
    assert res_a["tenant_id"] == "tenant-a"

    # Verify Tenant B chain
    res_b = await verify_audit_chain(db_session, tenant_id="tenant-b")
    assert res_b["valid"] is True
    assert res_b["event_count"] == 1
    assert res_b["tenant_id"] == "tenant-b"
