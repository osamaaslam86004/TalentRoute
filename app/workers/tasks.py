import asyncio
import logging
import threading
import uuid
from types import SimpleNamespace
from typing import Any

from app.core.config import get_settings
from app.services.screening_service import process_screening
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


def _invoke_process_screening(
    screening_id: str, *, allow_reclaim_running: bool
) -> None:
    coroutine = process_screening(
        uuid.UUID(screening_id),
        allow_reclaim_running=allow_reclaim_running,
    )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coroutine)
        return

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # pragma: no cover - test helper only
            error["exc"] = exc

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]


@celery_app.task(
    bind=True,
    name="app.workers.tasks.screen_candidate",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.task_max_retries,
)
def _screen_candidate_task(self, screening_id: str) -> dict[str, str]:
    """Process one persisted screening record. Retries are bounded in Celery configuration."""
    request = getattr(self, "request", None)
    delivery_info = getattr(request, "delivery_info", {}) or {}
    allow_reclaim_running = bool(delivery_info.get("redelivered"))

    try:
        _invoke_process_screening(
            screening_id,
            allow_reclaim_running=allow_reclaim_running,
        )
    except Exception as exc:
        if request is not None:
            logger.warning(
                "screening task will retry/fail id=%s retries=%s exception_type=%s",
                screening_id,
                getattr(request, "retries", 0),
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


class _ScreenCandidateProxy:
    """Wrap the Celery task so it works both as a bound task and as a plain function in tests."""

    def __init__(self, task: Any) -> None:
        self._task = task

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        if not args:
            raise TypeError(
                "screen_candidate() missing required positional argument: 'screening_id'"
            )

        original = getattr(self._task, "_orig_run", None)
        if original is None:
            return self._task.run(*args, **kwargs)

        if hasattr(args[0], "request"):
            if len(args) < 2:
                raise TypeError(
                    "screen_candidate() missing required positional argument: 'screening_id'"
                )
            task_self = args[0]
            screening_id = args[1]
            if hasattr(original, "__func__"):
                return original.__func__(task_self, screening_id, **kwargs)
            return original(task_self, screening_id, **kwargs)

        task_self = SimpleNamespace(
            request=SimpleNamespace(delivery_info={}, retries=0),
        )
        screening_id = args[0]
        if hasattr(original, "__func__"):
            return original.__func__(task_self, screening_id, **kwargs)
        return original(task_self, screening_id, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._task, item)


screen_candidate = _ScreenCandidateProxy(_screen_candidate_task)
