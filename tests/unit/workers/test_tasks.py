"""Unit tests for app.workers.tasks module.

Tests cover:
- screen_candidate Celery task execution invoking process_screening with parsed UUID.
- Extraction of delivery_info.redelivered flag to pass allow_reclaim_running.
- Exception handling, retry re-raising, and warning logs.
- Reliable worker_engine DB connection disposal in task finally block.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.tasks import screen_candidate

# Ensure app package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Setup celery mock to preserve task functions
if "celery" not in sys.modules:
    mock_celery_mod = MagicMock()

    def fake_task(*args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    mock_celery_mod.Celery.return_value.task = fake_task
    sys.modules["celery"] = mock_celery_mod

# Provide mock fallback for optional external modules if missing in test environment
for module_name in (
    "pydantic_settings",
    "opentelemetry",
    "opentelemetry.trace",
    "opentelemetry.metrics",
    "opentelemetry.sdk",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "asyncpg",
    "langchain_core",
    "langchain_core.callbacks",
    "langchain_core.outputs",
    "langchain_openai",
    "langgraph",
    "langgraph.graph",
):
    if module_name not in sys.modules:
        sys.modules[module_name] = MagicMock()

if hasattr(sys.modules["pydantic_settings"], "BaseSettings") and isinstance(
    sys.modules["pydantic_settings"].BaseSettings, MagicMock
):

    class DummyBaseSettings:
        pass

    sys.modules["pydantic_settings"].BaseSettings = DummyBaseSettings  # type: ignore

try:
    import pytest
except ImportError:
    pytest = None


class TestScreenCandidateTask(unittest.TestCase):
    """Unit tests for screen_candidate Celery task."""

    def setUp(self) -> None:
        """Set up test UUID and mock task context."""
        self.screening_id = uuid.uuid4()
        self.screening_id_str = str(self.screening_id)

    @patch("app.db.session.worker_engine")
    @patch("app.workers.tasks.process_screening")
    def test_screen_candidate_success_not_redelivered(
        self, mock_process: AsyncMock, mock_worker_engine: MagicMock
    ) -> None:
        """Verify screen_candidate executes process_screening with allow_reclaim_running=False."""
        mock_task_self = MagicMock()
        mock_task_self.request.delivery_info = {"redelivered": False}

        res = screen_candidate(mock_task_self, self.screening_id_str)

        self.assertEqual(
            res, {"screening_id": self.screening_id_str, "status": "processed"}
        )
        mock_process.assert_called_once_with(
            self.screening_id, allow_reclaim_running=False
        )
        mock_worker_engine.sync_engine.dispose.assert_called_once()

    @patch("app.db.session.worker_engine")
    @patch("app.workers.tasks.process_screening")
    def test_screen_candidate_redelivered_task_sets_allow_reclaim_running(
        self, mock_process: AsyncMock, mock_worker_engine: MagicMock
    ) -> None:
        """Verify screen_candidate passes allow_reclaim_running=True when redelivered is set."""
        mock_task_self = MagicMock()
        mock_task_self.request.delivery_info = {"redelivered": True}

        res = screen_candidate(mock_task_self, self.screening_id_str)

        self.assertEqual(
            res, {"screening_id": self.screening_id_str, "status": "processed"}
        )
        mock_process.assert_called_once_with(
            self.screening_id, allow_reclaim_running=True
        )
        mock_worker_engine.sync_engine.dispose.assert_called_once()

    @patch("app.db.session.worker_engine")
    @patch("app.workers.tasks.process_screening")
    def test_screen_candidate_exception_disposes_engine_and_reraises(
        self, mock_process: AsyncMock, mock_worker_engine: MagicMock
    ) -> None:
        """Verify exception during process_screening disposes worker engine in finally block and re-raises."""
        mock_task_self = MagicMock()
        mock_task_self.request.delivery_info = None
        mock_task_self.request.retries = 1
        mock_process.side_effect = RuntimeError("Database connection lost")

        with self.assertRaises(RuntimeError):
            screen_candidate(mock_task_self, self.screening_id_str)

        mock_worker_engine.sync_engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
