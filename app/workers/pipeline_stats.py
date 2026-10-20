"""Periodic background stats collection for outbox backlog, queues, and worker health."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.rate_limit import get_rate_limit_redis_client
from app.db.models.outbox_event import OutboxEvent
from app.db.models.screening import Screening
from app.db.session import AsyncSessionLocal
from app.observability.metrics import record_pipeline_event
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def collect_pipeline_statistics() -> dict[str, int | float]:
    """Query non-terminal backlog states, screening ages, and Redis queue depth."""
    now = datetime.now(UTC)
    stats: dict[str, int | float] = {}

    async with AsyncSessionLocal() as session:
        outbox_counts = dict(
            (
                await session.execute(
                    select(OutboxEvent.status, func.count(OutboxEvent.id))
                    .where(OutboxEvent.status.in_(["pending", "dispatching", "dead"]))
                    .group_by(OutboxEvent.status)
                )
            ).all()
        )
        stats["outbox_pending"] = outbox_counts.get("pending", 0)
        stats["outbox_dispatching"] = outbox_counts.get("dispatching", 0)
        stats["outbox_dead"] = outbox_counts.get("dead", 0)

        oldest_pending = await session.scalar(
            select(func.min(OutboxEvent.created_at)).where(
                OutboxEvent.status == "pending"
            )
        )
        if oldest_pending:
            stats["outbox_oldest_pending_age_seconds"] = (
                now - oldest_pending.astimezone(UTC)
            ).total_seconds()
        else:
            stats["outbox_oldest_pending_age_seconds"] = 0.0

        stale_running_count = await session.scalar(
            select(func.count(Screening.id)).where(Screening.status == "running")
        )
        stats["screenings_stale_running"] = stale_running_count or 0

    try:
        redis_client = get_rate_limit_redis_client()
        queue_len = await redis_client.llen("celery")
        stats["redis_celery_queue_length"] = queue_len
    except Exception as exc:
        logger.warning(
            "failed to check redis queue length error_type=%s", type(exc).__name__
        )
        stats["redis_celery_queue_length"] = -1

    record_pipeline_event("pipeline.stats_collected", outcome="success", stage="outbox")
    logger.info("pipeline stats collected: %s", stats)
    return stats


@celery_app.task(name="app.workers.pipeline_stats.collect_stats_task")
def collect_stats_task() -> dict[str, int | float]:
    """Celery beat task wrapper for pipeline stats collection."""
    import asyncio

    return asyncio.run(collect_pipeline_statistics())
