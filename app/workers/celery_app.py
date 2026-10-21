from celery import Celery

from app.core.config import get_settings
from app.observability.policy import enforce_langsmith_policy

settings = get_settings()
# Worker and beat both import this module; refuse to start with third-party LLM tracing enabled.
enforce_langsmith_policy(settings)
celery_app = Celery(
    "candidate_screening",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks", "app.workers.outbox", "app.workers.pipeline_stats"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=180,
    task_soft_time_limit=150,
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "dispatch-screening-outbox-every-10-seconds": {
            "task": "app.workers.outbox.dispatch_pending_outbox",
            "schedule": 10.0,
        },
        "collect-pipeline-stats-every-60-seconds": {
            "task": "app.workers.pipeline_stats.collect_stats_task",
            "schedule": 60.0,
        },
    },
)

import app.observability.celery_signals
