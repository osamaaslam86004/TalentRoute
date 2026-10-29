"""Integration tests for AuditEvent and AuditChainHead database models."""

import uuid
from datetime import UTC, datetime
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.audit.integrity import GENESIS_HASH
from app.db.models.audit_chain_head import AuditChainHead
from app.db.models.audit_event import AuditEvent


@pytest.mark.asyncio
async def test_audit_event_persistence_and_bigint_sequence(
    db_session: AsyncSession,
) -> None:
    """Test append-only event persistence with BigInteger sequence numbers."""
    event_id = uuid.uuid4()
    large_sequence = 9_007_199_254_740_991  # Standard JavaScript MAX_SAFE_INTEGER test
    now = datetime.now(UTC)

    event = AuditEvent(
        id=event_id,
        occurred_at=now,
        tenant_id="tenant-audit-1",
        sequence_no=large_sequence,
        previous_hash=GENESIS_HASH,
        event_hash="a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef",
        actor_subject="usr_sec_officer_01",
        actor_roles=["auditor", "security_admin"],
        request_id="req-audit-999",
        action="screening.deleted",
        resource_type="screening",
        resource_id=str(uuid.uuid4()),
        outcome="success",
        event_metadata={"reason": "GDPR erasure request", "requested_by": "privacy_team"},
    )

    db_session.add(event)
    await db_session.flush()

    retrieved = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.id == event_id)
    )
    assert retrieved is not None
    assert retrieved.sequence_no == large_sequence
    assert retrieved.actor_roles == ["auditor", "security_admin"]
    assert retrieved.event_metadata["reason"] == "GDPR erasure request"


@pytest.mark.asyncio
async def test_audit_event_unique_tenant_sequence_constraint(
    db_session: AsyncSession,
) -> None:
    """Verify uq_audit_events_tenant_sequence_no prevents duplicate sequence numbers."""
    tenant = "tenant-audit-2"
    e1 = AuditEvent(
        tenant_id=tenant,
        sequence_no=1,
        previous_hash=GENESIS_HASH,
        event_hash="hash1" + "0" * 59,
        actor_subject="system",
        actor_roles=["service"],
        action="test.event_1",
        resource_type="test",
    )
    db_session.add(e1)
    await db_session.flush()

    e2 = AuditEvent(
        tenant_id=tenant,
        sequence_no=1,  # Duplicate sequence number
        previous_hash="hash1" + "0" * 59,
        event_hash="hash2" + "0" * 59,
        actor_subject="system",
        actor_roles=["service"],
        action="test.event_2",
        resource_type="test",
    )
    db_session.add(e2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_audit_chain_head_genesis_defaults_and_isolation(
    db_session: AsyncSession,
) -> None:
    """Test AuditChainHead defaults, primary key isolation, and sequence locking."""
    head_a = AuditChainHead(tenant_id="tenant-chain-A")
    head_b = AuditChainHead(tenant_id="tenant-chain-B")

    db_session.add_all([head_a, head_b])
    await db_session.flush()

    assert head_a.last_sequence == 0
    assert head_a.last_hash == GENESIS_HASH
    assert head_b.last_sequence == 0
    assert head_b.last_hash == GENESIS_HASH

    # Test atomic row-level update simulation with for_update lock
    stmt = (
        select(AuditChainHead)
        .where(AuditChainHead.tenant_id == "tenant-chain-A")
        .with_for_update()
    )
    locked_head = await db_session.scalar(stmt)
    assert locked_head is not None

    locked_head.last_sequence += 1
    locked_head.last_hash = "new_hash_" + "0" * 56
    await db_session.flush()

    retrieved_a = await db_session.scalar(
        select(AuditChainHead).where(AuditChainHead.tenant_id == "tenant-chain-A")
    )
    retrieved_b = await db_session.scalar(
        select(AuditChainHead).where(AuditChainHead.tenant_id == "tenant-chain-B")
    )

    assert retrieved_a.last_sequence == 1
    assert retrieved_b.last_sequence == 0  # Tenant B uninfluenced
