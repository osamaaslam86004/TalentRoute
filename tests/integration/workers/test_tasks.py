"""Integration tests for Celery task wrapper (app/workers/tasks.py)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks import screen_candidate


@pytest.fixture
def mock_celery_task_request():
    """Create a mock Celery task request context."""
    request = MagicMock()
    request.retries = 0
    request.delivery_info = {"redelivered": False}
    return request


@pytest.mark.asyncio
async def test_screen_candidate_task_execution(
    db_session, monkeypatch, session_factory, make_screening, mock_celery_task_request
) -> None:
    """Verify screen_candidate task executes screening pipeline and returns result dict."""
    screening = make_screening(status="queued")
    db_session.add(screening)
    await db_session.commit()

    mock_process = AsyncMock()
    monkeypatch.setattr("app.workers.tasks.process_screening", mock_process)

    mock_dispose = MagicMock()
    monkeypatch.setattr(
        "app.db.session.worker_engine.sync_engine.dispose", mock_dispose
    )

    task_instance = MagicMock()
    task_instance.request = mock_celery_task_request

    result = screen_candidate(task_instance, str(screening.id))

    assert result == {
        "screening_id": str(screening.id),
        "status": "processed",
    }
    mock_process.assert_awaited_once_with(
        uuid.UUID(str(screening.id)),
        allow_reclaim_running=False,
    )
    mock_dispose.assert_called()


@pytest.mark.asyncio
async def test_screen_candidate_task_retry_on_failure(
    db_session, monkeypatch, session_factory, make_screening, mock_celery_task_request
) -> None:
    """Verify that processing exceptions propagate out of task to trigger Celery retries."""
    screening = make_screening(status="queued")
    db_session.add(screening)
    await db_session.commit()

    mock_process = AsyncMock(side_effect=RuntimeError("LLM Provider Timeout"))
    monkeypatch.setattr("app.workers.tasks.process_screening", mock_process)

    mock_dispose = MagicMock()
    monkeypatch.setattr(
        "app.db.session.worker_engine.sync_engine.dispose", mock_dispose
    )

    task_instance = MagicMock()
    task_instance.request = mock_celery_task_request

    with pytest.raises(RuntimeError, match="LLM Provider Timeout"):
        screen_candidate(task_instance, str(screening.id))

    mock_process.assert_awaited_once()
    mock_dispose.assert_called()


@pytest.mark.asyncio
async def test_screen_candidate_redelivered_task_reclaim(
    db_session, monkeypatch, session_factory, make_screening
) -> None:
    """Verify redelivered tasks pass allow_reclaim_running=True to process_screening."""
    screening = make_screening(status="running")
    db_session.add(screening)
    await db_session.commit()

    mock_process = AsyncMock()
    monkeypatch.setattr("app.workers.tasks.process_screening", mock_process)

    task_instance = MagicMock()
    task_instance.request.delivery_info = {"redelivered": True}
    task_instance.request.retries = 1

    result = screen_candidate(task_instance, str(screening.id))

    assert result == {
        "screening_id": str(screening.id),
        "status": "processed",
    }
    mock_process.assert_awaited_once_with(
        uuid.UUID(str(screening.id)),
        allow_reclaim_running=True,
    )
