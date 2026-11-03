import asyncio
import logging
import uuid

from app.core.config import get_settings
from app.services.screening_service import process_screening
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


def _run_async(coro_fn_or_coro):
    """Safely run a coroutine or callable whether an event loop is already running or not."""
    res = coro_fn_or_coro() if callable(coro_fn_or_coro) else coro_fn_or_coro
    if not asyncio.iscoroutine(res):
        return res
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, res).result()
    else:
        return asyncio.run(res)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.screen_candidate",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.task_max_retries,
)
def screen_candidate(self, *args, **kwargs) -> dict[str, str]:
    """Process one persisted screening record. Retries are bounded in Celery configuration."""
    if len(args) >= 2:
        task_obj = args[0]
        screening_id = str(args[1])
    elif len(args) == 1:
        task_obj = self
        screening_id = str(args[0])
    elif "screening_id" in kwargs:
        task_obj = self
        screening_id = str(kwargs["screening_id"])
    else:
        raise ValueError("screening_id is required")

    request_obj = getattr(task_obj, "request", None) or getattr(self, "request", None)
    delivery_info = getattr(request_obj, "delivery_info", None) or {}
    allow_reclaim_running = bool(delivery_info.get("redelivered"))
    retries = getattr(request_obj, "retries", 0)

    try:
        _run_async(
            process_screening(
                uuid.UUID(screening_id),
                allow_reclaim_running=allow_reclaim_running,
            )
        )
    except Exception as exc:
        logger.warning(
            "screening task will retry/fail id=%s retries=%s exception_type=%s",
            screening_id,
            retries,
            type(exc).__name__,
        )
        raise
    finally:
        try:
            from app.db.session import worker_engine

            worker_engine.sync_engine.dispose()
        except Exception:
            pass
    return {"screening_id": screening_id, "status": "processed"}
