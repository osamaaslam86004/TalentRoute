from __future__ import annotations

from sqlalchemy.pool import NullPool

from app.db.session import (
    AsyncSessionLocal,
    WorkerAsyncSessionLocal,
    configure_worker_db,
    create_worker_engine,
    worker_engine,
)
from app.observability.celery_signals import cleanup_task_db_connections


def test_worker_engine_uses_nullpool():
    assert isinstance(worker_engine.sync_engine.pool, NullPool)
    engine = create_worker_engine("postgresql+asyncpg://user:pass@localhost/test_db")
    assert isinstance(engine.sync_engine.pool, NullPool)


def test_configure_worker_db_binds_session_to_worker_engine():
    configure_worker_db()
    assert AsyncSessionLocal.kw.get("bind") == worker_engine
    assert WorkerAsyncSessionLocal.kw.get("bind") == worker_engine


def test_cleanup_task_db_connections_disposes_cleanly():
    # Calling cleanup hook must be safe and idempotent
    cleanup_task_db_connections()
    worker_engine.sync_engine.dispose()
