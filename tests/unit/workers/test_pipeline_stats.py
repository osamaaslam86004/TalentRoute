"""Unit tests for app.workers.pipeline_stats module.

Tests cover:
- collect_pipeline_statistics gathering outbox status counts, oldest pending event age, stale running screenings, DB pool metrics, and Redis queue length.
- Outbox dispatched event pruning query.
- Metrics gauge recording (record_pipeline_gauges) and event logging.
- collect_stats_task Celery beat task execution and worker_engine disposal in finally block.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.pipeline_stats import collect_pipeline_statistics, collect_stats_task

# Ensure app package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Setup celery mock
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
    "redis",
    "redis.asyncio",
    "redis.exceptions",
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


class TestPipelineStats(unittest.TestCase):
    """Unit tests for pipeline statistics collector."""

    @patch("app.workers.pipeline_stats.record_pipeline_gauges")
    @patch("app.workers.pipeline_stats.record_pipeline_event")
    @patch("app.workers.pipeline_stats.get_rate_limit_redis_client")
    @patch("app.workers.pipeline_stats.engine")
    @patch("app.workers.pipeline_stats.AsyncSessionLocal")
    def test_collect_pipeline_statistics_success(
        self,
        mock_session_local: MagicMock,
        mock_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_record_event: MagicMock,
        mock_record_gauges: MagicMock,
    ) -> None:
        """Verify collect_pipeline_statistics gathers DB and Redis metrics correctly."""

        async def _test():
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session

            # Mock status counts result
            mock_counts_res = MagicMock()
            mock_counts_res.all.return_value = [
                ("pending", 5),
                ("dispatching", 1),
                ("dead", 2),
            ]

            # Setup execute returns
            mock_session.execute.side_effect = [MagicMock(), mock_counts_res]

            # Mock scalar results: oldest pending created_at, stale running count
            oldest_dt = datetime.now(UTC) - timedelta(seconds=120)
            mock_session.scalar.side_effect = [oldest_dt, 3]

            # Mock pool stats
            mock_pool = MagicMock()
            mock_pool.checkedout.return_value = 4
            mock_pool.checkedin.return_value = 16
            mock_engine.sync_engine.pool = mock_pool

            # Mock Redis stats
            mock_redis = AsyncMock()
            mock_redis.llen.return_value = 12
            mock_redis.get.return_value = "2026-10-27T01:00:00Z"
            mock_get_redis.return_value = mock_redis

            stats = await collect_pipeline_statistics()

            self.assertEqual(stats["outbox_pending"], 5)
            self.assertEqual(stats["outbox_dispatching"], 1)
            self.assertEqual(stats["outbox_dead"], 2)
            self.assertAlmostEqual(
                stats["outbox_oldest_pending_age_seconds"], 120, delta=5
            )
            self.assertEqual(stats["screenings_stale_running"], 3)
            self.assertEqual(stats["db_pool_in_use"], 4)
            self.assertEqual(stats["db_pool_available"], 16)
            self.assertEqual(stats["redis_celery_queue_length"], 12)
            self.assertEqual(stats["dispatcher_heartbeat_present"], 1)

            mock_record_event.assert_called_once_with(
                "pipeline.stats_collected", outcome="success", stage="outbox"
            )
            mock_record_gauges.assert_called_once_with(stats)

        asyncio.run(_test())

    @patch("app.workers.pipeline_stats.record_pipeline_gauges")
    @patch("app.workers.pipeline_stats.record_pipeline_event")
    @patch("app.workers.pipeline_stats.get_rate_limit_redis_client")
    @patch("app.workers.pipeline_stats.engine")
    @patch("app.workers.pipeline_stats.AsyncSessionLocal")
    def test_collect_pipeline_statistics_no_pending_events(
        self,
        mock_session_local: MagicMock,
        mock_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_record_event: MagicMock,
        mock_record_gauges: MagicMock,
    ) -> None:
        """Verify oldest pending age is 0.0 when no pending outbox events exist."""

        async def _test():
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session

            mock_counts_res = MagicMock()
            mock_counts_res.all.return_value = []
            mock_session.execute.side_effect = [MagicMock(), mock_counts_res]

            mock_session.scalar.side_effect = [None, 0]

            mock_engine.sync_engine.pool = MagicMock()
            mock_redis = AsyncMock()
            mock_redis.llen.return_value = 0
            mock_redis.get.return_value = None
            mock_get_redis.return_value = mock_redis

            stats = await collect_pipeline_statistics()

            self.assertEqual(stats["outbox_pending"], 0)
            self.assertEqual(stats["outbox_oldest_pending_age_seconds"], 0.0)
            self.assertEqual(stats["dispatcher_heartbeat_present"], 0)

        asyncio.run(_test())

    @patch("app.db.session.worker_engine")
    @patch("app.workers.pipeline_stats.collect_pipeline_statistics")
    def test_collect_stats_task_celery_wrapper(
        self,
        mock_collect_stats: AsyncMock,
        mock_worker_engine: MagicMock,
    ) -> None:
        """Verify collect_stats_task runs collect_pipeline_statistics and disposes worker_engine."""
        mock_collect_stats.return_value = {"outbox_pending": 0}

        res = collect_stats_task()

        self.assertEqual(res, {"outbox_pending": 0})
        mock_worker_engine.sync_engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
