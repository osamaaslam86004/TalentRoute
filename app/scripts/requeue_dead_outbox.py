"""Safely requeue dead-letter outbox events after an operator fixes the cause.

This command never retries all dead letters unless --all-dead is explicit. It
logs only event identifiers and counts; payload data is intentionally excluded.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models.outbox_event import OutboxEvent
from app.db.session import AsyncSessionLocal
from app.audit.service import record_audit_event

logger = logging.getLogger("outbox.requeue")


async def requeue_dead_events(
    *, event_id: uuid.UUID | None, all_dead: bool, limit: int
) -> int:

    async with AsyncSessionLocal() as session:
        statement = select(OutboxEvent).where(OutboxEvent.status == "dead")

        if event_id is not None:
            statement = statement.where(OutboxEvent.id == event_id)

        statement = statement.order_by(OutboxEvent.created_at.asc()).limit(
            1 if event_id else limit
        )
        rows = list(
            (await session.scalars(statement.with_for_update(skip_locked=True))).all()
        )

        if event_id is not None and not rows:
            raise ValueError("event id was not found in dead-letter state")

        if not all_dead and event_id is None:
            raise ValueError("choose --event-id or explicitly pass --all-dead")

        now = datetime.now(timezone.utc)

        for row in rows:
            prior_attempts = row.attempt_count
            row.status = "pending"
            row.attempt_count = 0
            row.available_at = now
            row.locked_at = None
            row.dispatched_at = None
            row.last_error_code = None

            await record_audit_event(
                session,
                principal=None,
                tenant_id=row.tenant_id,
                request_id=None,
                action="outbox.requeued",
                resource_type="outbox_event",
                resource_id=str(row.id),
                metadata={"attempt_count": prior_attempts},
                actor_subject="service:outbox-recovery",
                actor_roles=["service"],
            )

        await session.commit()

        for row in rows:
            logger.info("requeued outbox event id=%s", row.id)

        return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--event-id", help="Requeue exactly one dead-letter event UUID")
    choice.add_argument(
        "--all-dead",
        action="store_true",
        help="Requeue dead-letter events up to --limit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum events to requeue with --all-dead (1-1000)",
    )
    args = parser.parse_args()

    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")

    try:
        event_id = uuid.UUID(args.event_id) if args.event_id else None

    except ValueError:
        parser.error("--event-id must be a UUID")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    try:
        changed = asyncio.run(
            requeue_dead_events(
                event_id=event_id, all_dead=args.all_dead, limit=args.limit
            )
        )
    except ValueError as exc:
        parser.error(str(exc))

    logger.info("requeue complete count=%s", changed)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
