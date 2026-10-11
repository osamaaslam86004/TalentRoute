import asyncio
import logging
import uuid

from app.core.config import get_settings
from app.services.screening_service import process_screening
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(
    bind=True,
    name="app.workers.tasks.screen_candidate",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.task_max_retries,
)
def screen_candidate(self, screening_id: str) -> dict[str, str]:
    """Process one persisted screening record. Retries are bounded in Celery configuration."""
    try:
        delivery_info = self.request.delivery_info or {}
        allow_reclaim_running = bool(delivery_info.get("redelivered"))

        asyncio.run(
            process_screening(
                uuid.UUID(screening_id),
                allow_reclaim_running=allow_reclaim_running,
            )
        )
    except Exception as exc:
        logger.warning(
            "screening task will retry/fail id=%s retries=%s exception_type=%s",
            screening_id,
            self.request.retries,
            type(exc).__name__,
        )
        raise
    return {"screening_id": screening_id, "status": "processed"}
