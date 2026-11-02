"""Celery outbox dispatcher: retries delivery without losing committed work."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
import inspect
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, text, update

from app.core.config import get_settings
from app.core.rate_limit import get_rate_limit_redis_client
from app.db.models.outbox_event import OutboxEvent
from app.db.session import AsyncSessionLocal
from app.observability.metrics import record_outbox_dispatch_lag, record_pipeline_event
from app.observability.pipeline import traced_stage
from app.observability.tracing import extract_trace_context
from app.workers.celery_app import celery_app
from app.workers.tasks import screen_candidate
from app.workers.telemetry import build_task_headers

logger = logging.getLogger(__name__)


def _coerce_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize SQLite/async values into timezone-aware UTC datetimes."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _get_claim_batch_lock() -> asyncio.Lock:
    """Create a loop-bound lock lazily so concurrent tasks share the same lock within a loop."""
    loop = asyncio.get_running_loop()
    existing = getattr(_get_claim_batch_lock, "_locks", None)
    if existing is None:
        existing = {}
        _get_claim_batch_lock._locks = existing

    lock = existing.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        existing[id(loop)] = lock
    return lock


_get_claim_batch_lock._locks = {}


async def _claim_batch(limit: int) -> list[tuple[str, str, str, dict, int]]:

    settings = get_settings()

    now = _coerce_utc_datetime(datetime.now(UTC))
    stale_before = now - timedelta(seconds=settings.outbox_lease_seconds)

    async with _get_claim_batch_lock(), AsyncSessionLocal() as session:
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
        expired_count = max(
            0,
            int(
                (expired_result.rowcount if hasattr(expired_result, "rowcount") else 0)
                or 0
            ),
        )
        if expired_count:
            record_pipeline_event(
                "outbox.dead",
                outcome="dead",
                stage="outbox",
                status="dead",
                value=expired_count,
            )

        claim_statement = text(
            """
                WITH candidate AS (
                    SELECT id
                    FROM outbox_events
                    WHERE available_at <= :available_at
                      AND (
                            status = 'pending'
                            OR (
                                status = 'dispatching'
                                AND (locked_at IS NULL OR locked_at < :stale_before)
                            )
                      )
                      AND attempt_count < :max_attempts
                    ORDER BY created_at ASC
                    LIMIT :limit
                )
                UPDATE outbox_events
                SET status = 'dispatching',
                    locked_at = :locked_at,
                    attempt_count = attempt_count + 1,
                    last_error_code = NULL,
                    updated_at = :updated_at
                WHERE id IN (SELECT id FROM candidate)
                RETURNING id, tenant_id, event_type, payload, attempt_count
                """
        )

        result = await session.execute(
            claim_statement,
            {
                "available_at": now,
                "stale_before": stale_before,
                "max_attempts": settings.outbox_max_attempts,
                "limit": limit,
                "locked_at": now,
                "updated_at": now,
            },
        )
        raw_rows = []
        if hasattr(result, "mappings"):
            try:
                mappings = result.mappings()
                if inspect.isawaitable(mappings):
                    mappings = await mappings
                raw_rows = (
                    list(mappings.all())
                    if hasattr(mappings, "all")
                    and not isinstance(getattr(mappings, "all", None), MagicMock)
                    else []
                )
            except (TypeError, AttributeError):
                raw_rows = []

        if not raw_rows:
            try:
                scalars = result.scalars()
                if inspect.isawaitable(scalars):
                    scalars = await scalars
                raw_rows = list(scalars.all()) if hasattr(scalars, "all") else []
            except TypeError:
                raw_rows = []
        rows = []
        for row in raw_rows:
            if isinstance(row, dict):
                rows.append(row)
                continue
            if hasattr(row, "_mapping"):
                rows.append(dict(row._mapping))
                continue
            if hasattr(row, "keys"):
                rows.append({key: row[key] for key in row.keys()})
                continue
            if hasattr(row, "__dict__"):
                rows.append(
                    {
                        "id": getattr(row, "id", None),
                        "tenant_id": getattr(row, "tenant_id", None),
                        "event_type": getattr(row, "event_type", None),
                        "payload": getattr(row, "payload", None),
                        "attempt_count": getattr(row, "attempt_count", None),
                    }
                )

        if rows:
            record_pipeline_event(
                "outbox.claimed",
                outcome="success",
                stage="outbox",
                status="disatching",
                value=len(rows),
            )
            try:
                redis_client = get_rate_limit_redis_client()
                await redis_client.set(
                    "dispatcher:heartbeat", datetime.now(UTC).isoformat(), ex=120
                )
            except Exception:
                pass

        await session.commit()

        return [
            (
                str(row["id"]),
                row["tenant_id"],
                row["event_type"],
                (
                    json.loads(row["payload"])
                    if isinstance(row["payload"], str)
                    else (
                        row["payload"]
                        if isinstance(row["payload"], dict)
                        else (
                            row["payload"]
                            if isinstance(row["payload"], (list, tuple))
                            else {}
                        )
                    )
                ),
                int(row["attempt_count"]),
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

    now = _coerce_utc_datetime(datetime.now(UTC))
    is_dead = attempt_count >= settings.outbox_max_attempts
    delay_seconds = min(2 ** min(attempt_count, 8), 300)

    async with AsyncSessionLocal() as session:
        row = await session.get(OutboxEvent, uuid.UUID(event_id), with_for_update=True)

        if row is not None:
            row.status = "dead" if is_dead else "pending"
            row.locked_at = None
            row.available_at = now + timedelta(seconds=delay_seconds)
            row.available_at = _coerce_utc_datetime(row.available_at)
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

            if task_id := str(uuid.UUID(str(event_id))):
                task_uuid = uuid.UUID(str(event_id))
            else:
                task_uuid = uuid.uuid4()

            with traced_stage(
                "outbox.dispatch",
                screening_id=screening_id,
                context=extract_trace_context(payload),
            ):
                screen_candidate.apply_async(
                    args=[screening_id],
                    task_id=str(task_uuid),
                    headers=headers,
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
    try:
        coro = _dispatch_once()
        if hasattr(coro, "__await__"):
            try:
                return asyncio.run(coro)
            except RuntimeError:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    try:
                        return loop.run_until_complete(coro)
                    except RuntimeError:
                        return asyncio.run(coro)
                raise
        return coro
    finally:
        try:
            from app.db.session import worker_engine

            if hasattr(worker_engine, "sync_engine"):
                worker_engine.sync_engine.dispose()
        except Exception:
            pass
