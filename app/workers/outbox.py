"""Celery outbox dispatcher: retries delivery without losing committed work."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update

from app.core.config import get_settings
from app.db.models.outbox_event import OutboxEvent
from app.db.session import AsyncSessionLocal
from app.observability.metrics import record_outbox_dispatch_lag, record_pipeline_event
from app.observability.pipeline import traced_stage
from app.observability.tracing import extract_trace_context
from app.core.rate_limit import get_rate_limit_redis_client
from app.workers.celery_app import celery_app
from app.workers.tasks import screen_candidate
from app.workers.telemetry import build_task_headers

logger = logging.getLogger(__name__)


async def _claim_batch(limit: int) -> list[tuple[str, str, str, dict, int]]:

    settings = get_settings()

    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=settings.outbox_lease_seconds)

    async with AsyncSessionLocal() as session:
        expired_result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.status == "dispatching",
                OutboxEvent.attempt_count >= settings.outbox_max_attempts,
                or_(
                    OutboxEvent.locked_at.is_(None),
                    OutboxEvent.locked_at < stale_before,
                ),
            )
            .values(
                status="dead", locked_at=None, last_error_code="dispatch_lease_expired"
            )
        )
        expired_count = max(0, int(expired_result.rowcount or 0))
        if expired_count:
            record_pipeline_event(
                "outbox.dead",
                outcome="dead",
                stage="outbox",
                status="dead",
                value=expired_count,
            )
        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.available_at <= now,
                or_(
                    OutboxEvent.status == "pending",
                    and_(
                        OutboxEvent.status == "dispatching",
                        or_(
                            OutboxEvent.locked_at.is_(None),
                            OutboxEvent.locked_at < stale_before,
                        ),
                    ),
                ),
                OutboxEvent.attempt_count < settings.outbox_max_attempts,
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        rows = list((await session.scalars(statement)).all())

        for row in rows:
            row.status = "dispatching"
            row.locked_at = now
            row.attempt_count += 1
            row.last_error_code = None

        await session.commit()

        if rows:
            record_pipeline_event(
                "outbox.claimed",
                outcome="success",
                stage="outbox",
                status="dispatching",
                value=len(rows),
            )
            try:
                redis_client = get_rate_limit_redis_client()
                await redis_client.set(
                    "dispatcher:heartbeat", datetime.now(UTC).isoformat(), ex=120
                )
            except Exception:
                pass

        return [
            (
                str(row.id),
                row.tenant_id,
                row.event_type,
                dict(row.payload),
                row.attempt_count,
            )
            for row in rows
        ]


async def _mark_dispatched(event_id: str) -> None:
    async with AsyncSessionLocal() as session:
        row = await session.get(OutboxEvent, uuid.UUID(event_id), with_for_update=True)

        if row is not None:
            row.status = "dispatched"
            row.dispatched_at = datetime.now(UTC)
            row.locked_at = None
            row.last_error_code = None
            await session.commit()
            record_pipeline_event(
                "outbox.dispatched",
                outcome="success",
                stage="outbox",
                status="dispatched",
            )


async def _mark_failed(event_id: str, attempt_count: int, exception_type: str) -> None:
    settings = get_settings()

    now = datetime.now(UTC)
    is_dead = attempt_count >= settings.outbox_max_attempts
    delay_seconds = min(2 ** min(attempt_count, 8), 300)

    async with AsyncSessionLocal() as session:
        row = await session.get(OutboxEvent, uuid.UUID(event_id), with_for_update=True)

        if row is not None:
            row.status = "dead" if is_dead else "pending"
            row.locked_at = None
            row.available_at = now + timedelta(seconds=delay_seconds)
            row.last_error_code = f"dispatch_{exception_type[:64]}"
            await session.commit()
            record_pipeline_event(
                "outbox.dead" if is_dead else "outbox.failed",
                outcome="dead" if is_dead else "failure",
                stage="outbox",
                error_type=exception_type[:64],
                status=row.status,
            )

    logger.error(
        "outbox dispatch failed event_id=%s attempt=%s terminal=%s error_type=%s",
        event_id,
        attempt_count,
        is_dead,
        exception_type,
    )


async def _dispatch_once_inner() -> dict[str, int]:
    settings = get_settings()

    batch = await _claim_batch(settings.outbox_batch_size)
    dispatched = 0
    failed = 0

    for event_id, tenant_id, event_type, payload, attempt_count in batch:
        try:
            if event_type != "screening.requested":
                raise ValueError("unsupported_event_type")

            screening_id = str(uuid.UUID(str(payload["screening_id"])))
            enqueued_at = payload.get("enqueued_at")
            queued_at: datetime | None = None

            if isinstance(enqueued_at, str):
                try:
                    parsed = datetime.fromisoformat(enqueued_at.replace("Z", "+00:00"))
                    queued_at = parsed if parsed.tzinfo is not None else None
                except ValueError:
                    queued_at = None
            headers = build_task_headers(
                payload, tenant_id=tenant_id, event_id=event_id
            )

            with traced_stage(
                "outbox.dispatch",
                screening_id=screening_id,
                context=extract_trace_context(payload),
            ):
                screen_candidate.apply_async(
                    args=[screening_id], task_id=event_id, headers=headers
                )

            if queued_at is not None:
                record_outbox_dispatch_lag(
                    max(0.0, (datetime.now(UTC) - queued_at).total_seconds())
                )
            await _mark_dispatched(event_id)
            dispatched += 1

        except Exception as exc:
            await _mark_failed(event_id, attempt_count, type(exc).__name__)
            failed += 1

    if batch:
        logger.info(
            "outbox batch complete claimed=%s dispatched=%s failed=%s",
            len(batch),
            dispatched,
            failed,
        )

    return {"claimed": len(batch), "dispatched": dispatched, "failed": failed}


async def _dispatch_once() -> dict[str, int]:
    """Dispatch one batch under a parent span; per-event spans are created by the inner loop."""
    with traced_stage("outbox.batch"):
        return await _dispatch_once_inner()


@celery_app.task(name="app.workers.outbox.dispatch_pending_outbox", queue="outbox")
def dispatch_pending_outbox() -> dict[str, int]:
    """Dispatch one bounded outbox batch. Celery Beat schedules this task."""

    return asyncio.run(_dispatch_once())
