"""Unit tests for app.workers.telemetry module.

Tests cover:
- build_task_headers copying and sanitizing approved telemetry correlation metadata.
- Exclusion of unapproved arbitrary outbox payload keys.
- Handling of missing, empty, or oversized metadata values.
"""

from __future__ import annotations

import os
import sys
import unittest

from app.workers.telemetry import build_task_headers

# Ensure app package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import pytest
except ImportError:
    pytest = None


class TestWorkerTelemetry(unittest.TestCase):
    """Unit tests for worker telemetry metadata sanitization and task header construction."""

    def test_build_task_headers_copies_approved_keys(self) -> None:
        """Verify that tenant_id, outbox_event_id, and allowed correlation payload keys are included."""
        payload = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "request_id": "req-xyz-987",
            "enqueued_at": "2026-10-27T01:00:00Z",
            # Unapproved arbitrary payload keys:
            "candidate_name": "Sensitive Person",
            "application_text": "Private resume details",
            "screening_id": "screen-123",
        }

        headers = build_task_headers(
            payload,
            tenant_id="tenant-alpha",
            event_id="outbox-event-456",
        )

        self.assertEqual(headers["tenant_id"], "tenant-alpha")
        self.assertEqual(headers["outbox_event_id"], "outbox-event-456")
        self.assertEqual(
            headers["traceparent"],
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        )
        self.assertEqual(headers["request_id"], "req-xyz-987")
        self.assertEqual(headers["enqueued_at"], "2026-10-27T01:00:00Z")

        # Verify arbitrary sensitive payload keys are excluded from headers
        self.assertNotIn("candidate_name", headers)
        self.assertNotIn("application_text", headers)
        self.assertNotIn("screening_id", headers)

    def test_build_task_headers_truncates_and_filters_invalid_metadata(self) -> None:
        """Verify tenant_id and event_id truncation at 128 chars and skipping non-string or oversized optional keys."""
        long_tenant = "t" * 150
        long_event = "e" * 150
        oversized_trace = "x" * 300  # Exceeds 256 char limit

        payload = {
            "traceparent": oversized_trace,
            "request_id": 12345,  # Non-string
            "enqueued_at": "",  # Empty string
        }

        headers = build_task_headers(
            payload, tenant_id=long_tenant, event_id=long_event
        )

        self.assertEqual(len(headers["tenant_id"]), 128)
        self.assertEqual(len(headers["outbox_event_id"]), 128)
        self.assertNotIn("traceparent", headers)
        self.assertNotIn("request_id", headers)
        self.assertNotIn("enqueued_at", headers)


if __name__ == "__main__":
    unittest.main()
