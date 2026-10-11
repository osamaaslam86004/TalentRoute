"""Append-only audit event creation and tenant-scoped querying helpers.

Only explicitly allow-listed metadata fields are accepted. Never pass applicant text, raw prompts,
provider responses, credentials, free-form reviewer notes, or unmatched request payloads here.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.db.models.audit_chain_head import AuditChainHead
from app.db.models.audit_event import AuditEvent
from app.future_features.audit_integrity import GENESIS_HASH, audit_hash_payload, calculate_event_hash

_ALLOWED_METADATA = {
    "status", "count", "limit", "offset", "prompt_version", "model_version", "rubric_version",
    "label", "reason_codes", "redacted_categories", "latency_ms", "cost_usd", "dataset_version",
    "report_id", "appeal_outcome", "attempt_count", "record_type", "schema_valid",
}


def safe_audit_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project supplied metadata onto a small allow-list and cap values before persistence."""
    safe: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key not in _ALLOWED_METADATA or value is None:
            continue
        if isinstance(value, bool | int):
            safe[key] = value
        elif isinstance(value, float) and math.isfinite(value):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = value[:160]
        elif isinstance(value, list | tuple):
            safe[key] = [str(item)[:80] for item in value[:20] if isinstance(item, str | int)]
    return safe


async def record_audit_event(
    session: AsyncSession,
    *,
    principal: Principal | None,
    tenant_id: str,
    request_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    outcome: str = "success",
    metadata: Mapping[str, Any] | None = None,
    actor_subject: str | None = None,
    actor_roles: list[str] | None = None,
) -> AuditEvent:
    """Append an event and extend the tenant hash chain under a locked chain-head row."""
    tenant_id = tenant_id[:128]
    await session.execute(
        text("""
            INSERT INTO audit_chain_heads (tenant_id, last_sequence, last_hash, updated_at)
            VALUES (:tenant_id, 0, :genesis_hash, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO NOTHING
        """),
        {"tenant_id": tenant_id, "genesis_hash": GENESIS_HASH},
    )
    head = await session.scalar(
        select(AuditChainHead)
        .where(AuditChainHead.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if head is None:
        raise RuntimeError("Unable to allocate audit chain head")

    event_id = uuid.uuid4()
    occurred_at = datetime.now(timezone.utc)
    subject = (actor_subject or (principal.subject if principal else "service:worker"))[:255]
    roles = sorted(principal.roles) if principal else (actor_roles or ["service"])
    request_id = (request_id or "")[:64] or None
    action = action[:120]
    resource_type = resource_type[:80]
    resource_id = (resource_id or "")[:128] or None
    outcome = outcome[:40]
    sanitized_metadata = safe_audit_metadata(metadata)
    sequence_no = head.last_sequence + 1
    previous_hash = head.last_hash
    hash_payload = audit_hash_payload(
        event_id=str(event_id),
        occurred_at=occurred_at,
        tenant_id=tenant_id,
        actor_subject=subject,
        actor_roles=roles,
        request_id=request_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        metadata=sanitized_metadata,
        sequence_no=sequence_no,
        previous_hash=previous_hash,
    )
    event_hash = calculate_event_hash(hash_payload)
    event = AuditEvent(
        id=event_id,
        occurred_at=occurred_at,
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        previous_hash=previous_hash,
        event_hash=event_hash,
        actor_subject=subject,
        actor_roles=roles,
        request_id=request_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        event_metadata=sanitized_metadata,
    )
    head.last_sequence = sequence_no
    head.last_hash = event_hash
    session.add(event)
    return event


async def verify_audit_chain(session: AsyncSession, *, tenant_id: str) -> dict[str, Any]:
    """Verify event sequence, predecessor links, row hashes and stored chain head."""
    from app.db.models.audit_chain_head import AuditChainHead

    # Lock the head before reading rows so concurrent appends cannot produce a false mismatch between
    # the event-list snapshot and the head snapshot under PostgreSQL READ COMMITTED isolation.
    await session.execute(
        text("""
            INSERT INTO audit_chain_heads (tenant_id, last_sequence, last_hash, updated_at)
            VALUES (:tenant_id, 0, :genesis_hash, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO NOTHING
        """),
        {"tenant_id": tenant_id, "genesis_hash": GENESIS_HASH},
    )
    head = await session.scalar(
        select(AuditChainHead)
        .where(AuditChainHead.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    rows = list((await session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.sequence_no.asc())
    )).all())
    previous_hash = GENESIS_HASH
    expected_sequence = 1
    first_failure: int | None = None
    reason: str | None = None
    for event in rows:
        if event.sequence_no != expected_sequence:
            first_failure = event.sequence_no
            reason = "sequence_gap_or_reordering"
            break
        if event.previous_hash != previous_hash:
            first_failure = event.sequence_no
            reason = "previous_hash_mismatch"
            break
        payload = audit_hash_payload(
            event_id=str(event.id),
            occurred_at=event.occurred_at,
            tenant_id=event.tenant_id,
            actor_subject=event.actor_subject,
            actor_roles=event.actor_roles,
            request_id=event.request_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            metadata=event.event_metadata,
            sequence_no=event.sequence_no,
            previous_hash=event.previous_hash,
        )
        if calculate_event_hash(payload) != event.event_hash:
            first_failure = event.sequence_no
            reason = "event_hash_mismatch"
            break
        previous_hash = event.event_hash
        expected_sequence += 1

    expected_head_sequence = len(rows)
    if reason is None and head is not None and (
        head.last_sequence != expected_head_sequence or head.last_hash != previous_hash
    ):
        first_failure = expected_head_sequence + 1
        reason = "chain_head_mismatch"
    elif reason is None and head is None and rows:
        first_failure = 1
        reason = "chain_head_missing"

    return {
        "tenant_id": tenant_id,
        "valid": reason is None,
        "event_count": len(rows),
        "head_sequence": head.last_sequence if head is not None else 0,
        "first_failure_sequence": first_failure,
        "failure_reason": reason,
        "head_hash": head.last_hash if head is not None else GENESIS_HASH,
    }


async def list_audit_events(
    session: AsyncSession, *, tenant_id: str, limit: int = 100, offset: int = 0, action: str | None = None
) -> tuple[list[AuditEvent], int]:
    from sqlalchemy import func

    filters = [AuditEvent.tenant_id == tenant_id]
    if action:
        filters.append(AuditEvent.action == action)
    items = list((await session.scalars(
        select(AuditEvent).where(*filters).order_by(AuditEvent.occurred_at.desc()).limit(limit).offset(offset)
    )).all())
    count = int(await session.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0)
    return items, count
