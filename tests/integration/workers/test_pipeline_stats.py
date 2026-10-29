"""Integration tests for pipeline statistics collector (app/workers/pipeline_stats.py)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.outbox_event import OutboxEvent
from app.db.models.screening import Screening
from app.workers.pipeline_stats import (
    collect_pipeline_statistics,
    collect_stats_task,
)


@pytest.mark.asyncio
async def test_collect_pipeline_statistics(db_session: AsyncSession):
    """Verify statistics collection across outbox backlog, screenings, and Redis metrics."""
    now = datetime.now(UTC)
    old_cutoff = now - timedelta(days=10)
    recent_cutoff = now - timedelta(days=1)

    # 1. Seed outbox events
    old_dispatched = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="dispatched",
        dispatched_at=old_cutoff,
        created_at=old_cutoff,
    )
    recent_dispatched = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="dispatched",
        dispatched_at=recent_cutoff,
        created_at=recent_cutoff,
    )
    pending_1 = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="pending",
        created_at=now - timedelta(seconds=300),
    )
    pending_2 = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="pending",
        created_at=now - timedelta(seconds=60),
    )
    dispatching_1 = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="dispatching",
        created_at=now - timedelta(seconds=30),
    )
    dead_1 = OutboxEvent(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="dead",
        created_at=now - timedelta(seconds=120),
    )
    db_session.add_all([
        old_dispatched,
        recent_dispatched,
        pending_1,
        pending_2,
        dispatching_1,
        dead_1,
    ])

    # 2. Seed screenings
    screening_running_1 = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        status="running",
        application_text="Sample candidate application text",
        job_title="Software Engineer",
        required_skills=["Python", "FastAPI"],
        processing_started_at=now - timedelta(seconds=1200),
    )
    screening_running_2 = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        status="running",
        application_text="Sample candidate application text",
        job_title="Backend Developer",
        required_skills=["Python"],
        processing_started_at=now - timedelta(seconds=1500),
    )
    screening_completed = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        status="completed",
        application_text="Sample candidate application text",
        job_title="Frontend Developer",
        required_skills=["React"],
    )
    db_session.add_all([
        screening_running_1,
        screening_running_2,
        screening_completed,
    ])
    await db_session.commit()

    # 3. Mock Redis client
    mock_redis = AsyncMock()
    mock_redis.llen.return_value = 7
    mock_redis.get.return_value = "2026-10-29T00:00:00Z"

    with patch(
        "app.workers.pipeline_stats.get_rate_limit_redis_client",
        return_value=mock_redis,
    ):
        stats = await collect_pipeline_statistics()

    # 4. Verify assertions
    assert stats["outbox_pending"] == 2
    assert stats["outbox_dispatching"] == 1
    assert stats["outbox_dead"] == 1
    assert stats["screenings_stale_running"] == 2
    assert stats["redis_celery_queue_length"] == 7
    assert stats["dispatcher_heartbeat_present"] == 1
    assert stats["outbox_oldest_pending_age_seconds"] >= 250.0

    # 5. Verify old dispatched event was pruned (>7 days)
    old_pruned = await db_session.get(OutboxEvent, old_dispatched.id)
    assert old_pruned is None

    recent_kept = await db_session.get(OutboxEvent, recent_dispatched.id)
    assert recent_kept is not None


@pytest.mark.asyncio
async def test_collect_pipeline_statistics_redis_failure(
    db_session: AsyncSession,
):
    """Verify statistics collection gracefully degrades on Redis failures."""
    mock_redis = AsyncMock()
    mock_redis.llen.side_effect = RuntimeError("Redis connection error")

    with patch(
        "app.workers.pipeline_stats.get_rate_limit_redis_client",
        return_value=mock_redis,
    ):
        stats = await collect_pipeline_statistics()

    assert "redis_celery_queue_length" not in stats or stats.get(
        "dispatcher_heartbeat_present"
    ) == 0
    assert stats["outbox_pending"] == 0
    assert stats["outbox_dead"] == 0


def test_collect_stats_task_wrapper():
    """Verify Celery task wrapper executes collect_pipeline_statistics synchronously."""
    with (
        patch(
            "app.workers.pipeline_stats.collect_pipeline_statistics",
            new_callable=AsyncMock,
        ) as mock_collect,
        patch("app.db.session.worker_engine") as mock_engine,
    ):
        mock_collect.return_value = {
            "outbox_pending": 0,
            "outbox_dispatching": 0,
            "outbox_dead": 0,
            "screenings_stale_running": 0,
            "redis_celery_queue_length": 0,
            "dispatcher_heartbeat_present": 1,
        }
        res = collect_stats_task()
        assert res["dispatcher_heartbeat_present"] == 1
        assert mock_collect.called
        assert mock_engine.sync_engine.dispose.called
