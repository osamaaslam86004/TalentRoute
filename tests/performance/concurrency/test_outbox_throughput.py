"""Benchmark and concurrency tests for transactional outbox batch claiming throughput."""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import patch

import pytest

from app.db.models.outbox_event import OutboxEvent
from app.workers.outbox import _claim_batch


@pytest.mark.asyncio
async def test_outbox_batch_claim_throughput_and_skip_locked(
    perf_session_factory, tenant_a: str
):
    """Seed 200 pending outbox events and test concurrent batch claiming with skip_locked=True."""
    event_count = 200

    # Seed pending outbox events
    async with perf_session_factory() as session:
        for i in range(event_count):
            event = OutboxEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_a,
                event_type="screening.requested",
                aggregate_id=str(uuid.uuid4()),
                payload={
                    "screening_id": str(uuid.uuid4()),
                    "request_id": f"req-outbox-{i}",
                },
                status="pending",
            )
            session.add(event)
        await session.commit()

    claimed_batches: list[list[tuple[str, str, str, dict, int]]] = []

    async def _worker_claim():
        # Patch AsyncSessionLocal in workers.outbox to use test session factory
        with patch("app.workers.outbox.AsyncSessionLocal", perf_session_factory):
            batch = await _claim_batch(limit=50)
            claimed_batches.append(batch)

    start_time = time.perf_counter()
    # Launch 5 concurrent dispatcher worker claims
    tasks = [_worker_claim() for _ in range(5)]
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start_time

    total_claimed = sum(len(b) for b in claimed_batches)
    assert total_claimed == event_count, (
        f"Expected {event_count} claimed events, got {total_claimed}"
    )

    # Verify zero duplicate claims across workers (skip_locked guarantee)
    all_claimed_ids = [item[0] for batch in claimed_batches for item in batch]
    assert len(all_claimed_ids) == len(set(all_claimed_ids)), (
        "Duplicate outbox event claim detected across workers!"
    )

    # Throughput calculation
    throughput = total_claimed / max(elapsed, 0.001)
    assert throughput > 0.0
