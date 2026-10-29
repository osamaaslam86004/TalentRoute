"""Concurrency test for audit chain head lock contention and gapless sequence allocation."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.audit.service import record_audit_event, verify_audit_chain
from app.core.security import Principal
from app.db.models.audit_event import AuditEvent


@pytest.mark.asyncio
async def test_audit_chain_head_lock_contention(perf_session_factory, tenant_a: str):
    """Concurrently invoke record_audit_event across 30 workers for the same tenant."""
    concurrency_count = 30
    durations: list[float] = []

    async def _audit_worker(worker_idx: int) -> None:
        principal = Principal(
            subject=f"worker-{worker_idx:02d}",
            tenant_id=tenant_a,
            roles=frozenset({"service"}),
            scopes=frozenset({"screening:write"}),
            auth_method="oidc",
        )
        start_time = time.perf_counter()
        async with perf_session_factory() as session:
            await record_audit_event(
                session,
                principal=principal,
                tenant_id=tenant_a,
                request_id=f"req-perf-{worker_idx}",
                action="screening.concurrent_test",
                resource_type="screening",
                resource_id=f"res-{worker_idx}",
                metadata={"status": "queued", "count": worker_idx},
            )
            await session.commit()
        durations.append(time.perf_counter() - start_time)

    # Launch 30 concurrent workers recording audit events for the same tenant
    tasks = [_audit_worker(i) for i in range(concurrency_count)]
    await asyncio.gather(*tasks)

    # Verify audit chain integrity
    async with perf_session_factory() as session:
        # Check database rows
        statement = (
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_a)
            .order_by(AuditEvent.sequence_no.asc())
        )
        events = list((await session.scalars(statement)).all())

        assert len(events) == concurrency_count, (
            f"Expected {concurrency_count} events, found {len(events)}"
        )

        # Verify gapless sequence numbers 1..N
        sequences = [e.sequence_no for e in events]
        expected_sequences = list(range(1, concurrency_count + 1))
        assert sequences == expected_sequences, (
            f"Sequence gap or reordering detected: {sequences}"
        )

        # Run verification routine
        verification = await verify_audit_chain(session, tenant_id=tenant_a)
        assert verification["valid"] is True, (
            f"Audit chain verification failed: {verification}"
        )
        assert verification["event_count"] == concurrency_count

    # Calculate p95 duration
    durations.sort()
    p95_idx = int(0.95 * len(durations))
    p95_duration = durations[p95_idx]
    assert p95_duration > 0.0
