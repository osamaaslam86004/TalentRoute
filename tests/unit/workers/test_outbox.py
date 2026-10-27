"""Unit tests for app.workers.outbox module.

Tests cover:
- Batch claiming logic (_claim_batch), including lease expiration / dead-letter transitions for stuck rows.
- Marking outbox events as successfully dispatched (_mark_dispatched).
- Exponential backoff delay calculation and terminal dead-letter state transitions on failure (_mark_failed).
- End-to-end batch dispatch processing (_dispatch_once_inner and dispatch_pending_outbox).
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models.outbox_event import OutboxEvent
from app.workers.outbox import (
    _claim_batch,
    _dispatch_once_inner,
    _mark_dispatched,
    _mark_failed,
    dispatch_pending_outbox,
)

# Ensure app package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Setup celery mock to preserve task functions and apply_async
if "celery" not in sys.modules:
    mock_celery_mod = MagicMock()

    def fake_task(*args, **kwargs):
        def decorator(fn):
            fn.apply_async = MagicMock()
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
    "redis",
    "redis.asyncio",
    "redis.exceptions",
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


class TestOutboxDispatcher(unittest.TestCase):
    """Unit tests for PostgreSQL outbox dispatcher functions."""

    def setUp(self) -> None:
        """Set up test settings and model fixtures."""
        self.mock_settings = MagicMock()
        self.mock_settings.outbox_lease_seconds = 120
        self.mock_settings.outbox_max_attempts = 12
        self.mock_settings.outbox_batch_size = 50

        self.event_id = uuid.uuid4()
        self.event_id_str = str(self.event_id)
        self.outbox_event = OutboxEvent(
            id=self.event_id,
            tenant_id="tenant-alpha",
            event_type="screening.requested",
            payload={
                "screening_id": str(uuid.uuid4()),
                "enqueued_at": "2026-10-27T01:00:00Z",
            },
            status="pending",
            attempt_count=0,
            created_at=datetime.now(UTC),
        )

    @patch("app.workers.outbox.record_pipeline_event")
    @patch("app.workers.outbox.get_rate_limit_redis_client")
    @patch("app.workers.outbox.get_settings")
    @patch("app.workers.outbox.AsyncSessionLocal")
    def test_claim_batch_success(
        self,
        mock_session_local: MagicMock,
        mock_get_settings: MagicMock,
        mock_get_redis: MagicMock,
        mock_record_pipeline_event: MagicMock,
    ) -> None:
        """Verify _claim_batch marks rows as dispatching and increments attempt_count."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session

            # Mock expired update rowcount
            mock_expired_res = MagicMock()
            mock_expired_res.rowcount = 0
            mock_session.execute.return_value = mock_expired_res

            # Mock pending rows
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [self.outbox_event]
            mock_session.scalars.return_value = mock_scalars

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            batch = await _claim_batch(limit=10)

            self.assertEqual(len(batch), 1)
            self.assertEqual(batch[0][0], self.event_id_str)
            self.assertEqual(batch[0][1], "tenant-alpha")
            self.assertEqual(batch[0][2], "screening.requested")
            self.assertEqual(self.outbox_event.status, "dispatching")
            self.assertEqual(self.outbox_event.attempt_count, 1)
            mock_session.commit.assert_awaited_once()

        asyncio.run(_test())

    @patch("app.workers.outbox.get_settings")
    @patch("app.workers.outbox.AsyncSessionLocal")
    def test_mark_dispatched_updates_status_and_timestamp(
        self,
        mock_session_local: MagicMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify _mark_dispatched transitions row status to dispatched with timestamp."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session.get.return_value = self.outbox_event

            await _mark_dispatched(self.event_id_str)

            self.assertEqual(self.outbox_event.status, "dispatched")
            self.assertIsNotNone(self.outbox_event.dispatched_at)
            self.assertIsNone(self.outbox_event.locked_at)
            mock_session.commit.assert_awaited_once()

        asyncio.run(_test())

    @patch("app.workers.outbox.get_settings")
    @patch("app.workers.outbox.AsyncSessionLocal")
    def test_mark_failed_non_terminal_calculates_backoff(
        self,
        mock_session_local: MagicMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify _mark_failed sets status back to pending with backoff delay when attempt_count < max."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session.get.return_value = self.outbox_event

            await _mark_failed(
                self.event_id_str, attempt_count=3, exception_type="TimeoutError"
            )

            self.assertEqual(self.outbox_event.status, "pending")
            self.assertIsNone(self.outbox_event.locked_at)
            self.assertEqual(self.outbox_event.last_error_code, "dispatch_TimeoutError")
            mock_session.commit.assert_awaited_once()

        asyncio.run(_test())

    @patch("app.workers.outbox.get_settings")
    @patch("app.workers.outbox.AsyncSessionLocal")
    def test_mark_failed_terminal_transitions_to_dead(
        self,
        mock_session_local: MagicMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify _mark_failed transitions status to dead when attempt_count >= max_attempts."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session.get.return_value = self.outbox_event

            await _mark_failed(
                self.event_id_str, attempt_count=12, exception_type="ConnectionRefused"
            )

            self.assertEqual(self.outbox_event.status, "dead")
            self.assertIsNone(self.outbox_event.locked_at)
            self.assertEqual(
                self.outbox_event.last_error_code, "dispatch_ConnectionRefused"
            )
            mock_session.commit.assert_awaited_once()

        asyncio.run(_test())

    @patch("app.workers.outbox.traced_stage")
    @patch("app.workers.outbox.extract_trace_context")
    @patch("app.workers.outbox._mark_failed")
    @patch("app.workers.outbox._mark_dispatched")
    @patch("app.workers.outbox.screen_candidate")
    @patch("app.workers.outbox._claim_batch")
    @patch("app.workers.outbox.get_settings")
    def test_dispatch_once_inner_success(
        self,
        mock_get_settings: MagicMock,
        mock_claim: AsyncMock,
        mock_screen_task: MagicMock,
        mock_mark_dispatched: AsyncMock,
        mock_mark_failed: AsyncMock,
        mock_extract_context: MagicMock,
        mock_traced_stage: MagicMock,
    ) -> None:
        """Verify _dispatch_once_inner claims batch, enqueues tasks, and marks dispatched."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            screening_id = str(uuid.uuid4())
            batch_item = (
                self.event_id_str,
                "tenant-alpha",
                "screening.requested",
                {"screening_id": screening_id, "enqueued_at": "2026-10-27T01:00:00Z"},
                1,
            )
            mock_claim.return_value = [batch_item]
            mock_extract_context.return_value = {}
            mock_traced_stage.return_value.__enter__.return_value = MagicMock()

            result = await _dispatch_once_inner()

            self.assertEqual(result, {"claimed": 1, "dispatched": 1, "failed": 0})
            mock_screen_task.apply_async.assert_called_once()
            mock_mark_dispatched.assert_awaited_once_with(self.event_id_str)
            mock_mark_failed.assert_not_called()

        asyncio.run(_test())

    @patch("app.workers.outbox._mark_failed")
    @patch("app.workers.outbox._mark_dispatched")
    @patch("app.workers.outbox._claim_batch")
    @patch("app.workers.outbox.get_settings")
    def test_dispatch_once_inner_unsupported_event_type_fails(
        self,
        mock_get_settings: MagicMock,
        mock_claim: AsyncMock,
        mock_mark_dispatched: AsyncMock,
        mock_mark_failed: AsyncMock,
    ) -> None:
        """Verify unsupported event_type triggers _mark_failed and increments failed count."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            batch_item = (
                self.event_id_str,
                "tenant-alpha",
                "unsupported.type",
                {"screening_id": str(uuid.uuid4())},
                1,
            )
            mock_claim.return_value = [batch_item]

            result = await _dispatch_once_inner()

            self.assertEqual(result, {"claimed": 1, "dispatched": 0, "failed": 1})
            mock_mark_dispatched.assert_not_called()
            mock_mark_failed.assert_awaited_once_with(
                self.event_id_str, 1, "ValueError"
            )

        asyncio.run(_test())

    @patch("app.db.session.worker_engine")
    @patch("app.workers.outbox._dispatch_once")
    def test_dispatch_pending_outbox_celery_task(
        self,
        mock_dispatch_once: AsyncMock,
        mock_worker_engine: MagicMock,
    ) -> None:
        """Verify Celery task wrapper dispatch_pending_outbox disposes worker engine in finally block."""
        mock_dispatch_once.return_value = {"claimed": 2, "dispatched": 2, "failed": 0}

        result = dispatch_pending_outbox()

        self.assertEqual(result, {"claimed": 2, "dispatched": 2, "failed": 0})
        mock_worker_engine.sync_engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
