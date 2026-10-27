# content of tests/scripts/test_requeue_dead_outbox.py
from __future__ import annotations

import asyncio
import sys
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import pytest
except ImportError:
    pytest = None

from app.db.models.outbox_event import OutboxEvent
from app.scripts.requeue_dead_outbox import main, requeue_dead_events

# Handle missing optional dependencies in minimal test runtime
for module_name in (
    "pydantic_settings",
    "opentelemetry",
    "opentelemetry.trace",
    "opentelemetry.metrics",
    "opentelemetry.sdk",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "asyncpg",
):
    if module_name not in sys.modules:
        mock_mod = MagicMock()
        if module_name == "pydantic_settings":

            class DummyBaseSettings:
                pass

            mock_mod.BaseSettings = DummyBaseSettings
        sys.modules[module_name] = mock_mod


class TestRequeueDeadOutboxCLI(unittest.TestCase):
    """Unit tests for app/scripts/requeue_dead_outbox.py CLI."""

    def test_requeue_single_event_success(self) -> None:
        """Verify single dead-letter event is requeued by UUID."""

        async def _test() -> None:
            event_id = uuid.uuid4()
            dead_event = OutboxEvent(
                id=event_id,
                tenant_id="tenant-alpha",
                event_type="screening.requested",
                status="dead",
                attempt_count=5,
                locked_at=datetime.now(UTC),
                dispatched_at=datetime.now(UTC),
                last_error_code="TIMEOUT",
            )

            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [dead_event]

            mock_session = AsyncMock()
            mock_session.scalars.return_value = mock_scalars

            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session

            with (
                patch(
                    "app.scripts.requeue_dead_outbox.AsyncSessionLocal",
                    return_value=mock_session_cm,
                ),
                patch(
                    "app.scripts.requeue_dead_outbox.record_audit_event"
                ) as mock_record_audit,
            ):
                count = await requeue_dead_events(
                    event_id=event_id, all_dead=False, limit=100
                )

            self.assertEqual(count, 1)
            self.assertEqual(dead_event.status, "pending")
            self.assertEqual(dead_event.attempt_count, 0)
            self.assertIsNone(dead_event.locked_at)
            self.assertIsNone(dead_event.dispatched_at)
            self.assertIsNone(dead_event.last_error_code)
            mock_session.commit.assert_awaited_once()
            mock_record_audit.assert_awaited_once_with(
                mock_session,
                principal=None,
                tenant_id="tenant-alpha",
                request_id=None,
                action="outbox.requeued",
                resource_type="outbox_event",
                resource_id=str(event_id),
                metadata={"attempt_count": 5},
                actor_subject="service:outbox-recovery",
                actor_roles=["service"],
            )

        asyncio.run(_test())

    def test_requeue_all_dead_batch_success(self) -> None:
        """Verify batch requeueing of dead events up to limit."""

        async def _test() -> None:
            e1 = OutboxEvent(
                id=uuid.uuid4(), tenant_id="t1", status="dead", attempt_count=2
            )
            e2 = OutboxEvent(
                id=uuid.uuid4(), tenant_id="t1", status="dead", attempt_count=3
            )

            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [e1, e2]

            mock_session = AsyncMock()
            mock_session.scalars.return_value = mock_scalars

            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session

            with (
                patch(
                    "app.scripts.requeue_dead_outbox.AsyncSessionLocal",
                    return_value=mock_session_cm,
                ),
                patch("app.scripts.requeue_dead_outbox.record_audit_event"),
            ):
                count = await requeue_dead_events(
                    event_id=None, all_dead=True, limit=50
                )

            self.assertEqual(count, 2)
            self.assertEqual(e1.status, "pending")
            self.assertEqual(e2.status, "pending")
            mock_session.commit.assert_awaited_once()

        asyncio.run(_test())

    def test_requeue_non_existent_dead_event_raises_value_error(self) -> None:
        """Verify ValueError when specified event_id is not found in dead state."""

        async def _test() -> None:
            event_id = uuid.uuid4()

            mock_scalars = MagicMock()
            mock_scalars.all.return_value = []

            mock_session = AsyncMock()
            mock_session.scalars.return_value = mock_scalars

            mock_session_cm = AsyncMock()
            mock_session_cm.__aenter__.return_value = mock_session

            with patch(
                "app.scripts.requeue_dead_outbox.AsyncSessionLocal",
                return_value=mock_session_cm,
            ):
                with self.assertRaises(ValueError) as ctx:
                    await requeue_dead_events(
                        event_id=event_id, all_dead=False, limit=100
                    )
                self.assertIn(
                    "event id was not found in dead-letter state", str(ctx.exception)
                )

        asyncio.run(_test())

    def test_requeue_main_cli_single_event_success(self) -> None:
        """Verify CLI main() invocation with --event-id."""
        event_id_str = str(uuid.uuid4())
        test_args = ["requeue_dead_outbox.py", "--event-id", event_id_str]

        with (
            patch("sys.argv", test_args),
            patch("app.scripts.requeue_dead_outbox.setup_observability"),
            patch(
                "app.scripts.requeue_dead_outbox.requeue_dead_events", return_value=1
            ) as mock_requeue,
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        mock_requeue.assert_called_once_with(
            event_id=uuid.UUID(event_id_str),
            all_dead=False,
            limit=100,
        )

    def test_requeue_main_cli_all_dead_success(self) -> None:
        """Verify CLI main() invocation with --all-dead and --limit."""
        test_args = ["requeue_dead_outbox.py", "--all-dead", "--limit", "50"]

        with (
            patch("sys.argv", test_args),
            patch("app.scripts.requeue_dead_outbox.setup_observability"),
            patch(
                "app.scripts.requeue_dead_outbox.requeue_dead_events", return_value=10
            ) as mock_requeue,
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        mock_requeue.assert_called_once_with(
            event_id=None,
            all_dead=True,
            limit=50,
        )

    def test_requeue_main_cli_missing_mutually_exclusive_flags_raises(self) -> None:
        """Verify SystemExit when neither --event-id nor --all-dead is provided."""
        test_args = ["requeue_dead_outbox.py"]

        with patch("sys.argv", test_args), self.assertRaises(SystemExit):
            main()

    def test_requeue_main_cli_invalid_uuid_raises(self) -> None:
        """Verify SystemExit when --event-id is not a valid UUID string."""
        test_args = ["requeue_dead_outbox.py", "--event-id", "not-a-valid-uuid"]

        with patch("sys.argv", test_args), self.assertRaises(SystemExit):
            main()

    def test_requeue_main_cli_invalid_limit_bounds_raises(self) -> None:
        """Verify SystemExit when --limit is outside 1-1000 range."""
        for invalid_limit in (0, 1001, -5):
            test_args = [
                "requeue_dead_outbox.py",
                "--all-dead",
                "--limit",
                str(invalid_limit),
            ]

            with patch("sys.argv", test_args), self.assertRaises(SystemExit):
                main()

    def test_requeue_main_cli_value_error_from_requeue_raises_system_exit(self) -> None:
        """Verify SystemExit when requeue_dead_events raises ValueError."""
        event_id_str = str(uuid.uuid4())
        test_args = ["requeue_dead_outbox.py", "--event-id", event_id_str]

        with (
            patch("sys.argv", test_args),
            patch("app.scripts.requeue_dead_outbox.setup_observability"),
            patch(
                "app.scripts.requeue_dead_outbox.requeue_dead_events",
                side_effect=ValueError("event id was not found in dead-letter state"),
            ),
            self.assertRaises(SystemExit),
        ):
            main()


if __name__ == "__main__":
    unittest.main()
