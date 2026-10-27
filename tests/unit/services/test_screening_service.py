"""Unit tests for app.services.screening_service module.

Tests cover:
- Happy path execution of process_screening with PII redaction and LangGraph workflow.
- Idempotent duplicate check for completed screenings.
- Missing screening record handling.
- Stale and redelivered running screening task reclamation logic.
- Exception handling and error persistence when graph invocation fails.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models.screening import Screening
from app.privacy.pii_redaction import RedactionResult
from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    Recommendation,
    SkillMatch,
    SkillsAssessment,
)
from app.services.screening_service import process_screening

# Ensure app package is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

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


class TestScreeningService(unittest.TestCase):
    """Unit tests for process_screening business logic and workflow execution."""

    def setUp(self) -> None:
        """Set up test fixtures and default mock objects."""
        self.screening_id = uuid.uuid4()
        self.screening = Screening(
            id=self.screening_id,
            tenant_id="tenant-alpha",
            request_id="req-123",
            status="queued",
            application_text="Jane Doe, email: jane@example.com, 10 years Python experience.",
            job_title="Senior Python Engineer",
            required_skills=["Python", "FastAPI", "PostgreSQL"],
            attempt_count=0,
        )

        self.mock_settings = MagicMock()
        self.mock_settings.stale_processing_seconds = 900
        self.mock_settings.max_application_chars = 30000
        self.mock_settings.openai_model = "gpt-4o-mini"
        self.mock_settings.pii_redaction_enabled = True
        self.mock_settings.pii_redaction_policy = "baseline"

        self.mock_exp = ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.95,
            evidence=["10 years Python experience"],
            rationale="Demonstrated long tenure with Python.",
        )
        self.mock_skills = SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.90,
            evidence=["Python", "FastAPI"],
            gaps=[],
            rationale="Strong skill alignment.",
        )

    @patch("app.services.screening_service.TelemetryCallbackHandler")
    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.record_audit_event")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.redact_pii")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_happy_path(
        self,
        mock_session_local: MagicMock,
        mock_redact_pii: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_record_audit: AsyncMock,
        mock_get_settings: MagicMock,
        mock_cb_handler: MagicMock,
    ) -> None:
        """Verify successful end-to-end processing of a screening record."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_get_screening.return_value = self.screening

            mock_redact_pii.return_value = RedactionResult(
                text="[REDACTED_NAME], email: [REDACTED_EMAIL], 10 years Python experience.",
                counts={"email": 1, "labelled_name": 1},
                changed=True,
            )

            mock_graph = AsyncMock()
            mock_graph.ainvoke.return_value = {
                "experience_assessment": self.mock_exp,
                "skills_assessment": self.mock_skills,
                "recommendation": Recommendation.HR_REVIEW,
                "recommendation_reason": "Exceeds required qualifications.",
            }
            mock_create_graph.return_value = mock_graph

            await process_screening(self.screening_id)

            self.assertEqual(self.screening.status, "completed")
            self.assertEqual(self.screening.experience_level, "Senior-level")
            self.assertEqual(self.screening.skills_match, "Match")
            self.assertEqual(self.screening.recommendation, "hr_review")
            self.assertEqual(
                self.screening.recommendation_reason, "Exceeds required qualifications."
            )
            self.assertEqual(
                self.screening.redaction_summary, {"email": 1, "labelled_name": 1}
            )
            self.assertIsNone(self.screening.error_code)

            mock_record_audit.assert_awaited()
            completed_audit_call = [
                call
                for call in mock_record_audit.await_args_list
                if call.kwargs.get("action") == "screening.processing_completed"
            ]
            self.assertEqual(len(completed_audit_call), 1)

        asyncio.run(_test())

    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_missing_record_exits_gracefully(
        self,
        mock_session_local: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify process_screening exits cleanly when screening ID does not exist."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_get_screening.return_value = None

            await process_screening(self.screening_id)

            mock_create_graph.assert_not_called()

        asyncio.run(_test())

    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.record_pipeline_event")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_already_completed_is_idempotent(
        self,
        mock_session_local: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_record_pipeline_event: MagicMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify process_screening short-circuits idempotently if status is completed."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            self.screening.status = "completed"
            mock_get_screening.return_value = self.screening

            await process_screening(self.screening_id)

            mock_create_graph.assert_not_called()
            mock_record_pipeline_event.assert_called_once_with(
                "screening.duplicate", outcome="duplicate", stage="claim"
            )

        asyncio.run(_test())

    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.record_pipeline_event")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.redact_pii")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_running_active_task_ignored(
        self,
        mock_session_local: MagicMock,
        mock_redact_pii: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_record_pipeline_event: MagicMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify an active running task below stale threshold is ignored if allow_reclaim_running is False."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            self.screening.status = "running"
            self.screening.processing_started_at = datetime.now(UTC) - timedelta(
                seconds=30
            )
            mock_get_screening.return_value = self.screening

            await process_screening(self.screening_id, allow_reclaim_running=False)

            mock_create_graph.assert_not_called()
            mock_record_pipeline_event.assert_called_once_with(
                "screening.duplicate", outcome="duplicate", stage="claim"
            )

        asyncio.run(_test())

    @patch("app.services.screening_service.record_audit_event")
    @patch("app.services.screening_service.TelemetryCallbackHandler")
    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.record_pipeline_event")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.redact_pii")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_reclaims_stale_running_task(
        self,
        mock_session_local: MagicMock,
        mock_redact_pii: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_record_pipeline_event: MagicMock,
        mock_get_settings: MagicMock,
        mock_cb_handler: MagicMock,
        mock_record_audit: AsyncMock,
    ) -> None:
        """Verify stale running task exceeding stale threshold is reclaimed and re-executed."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            self.screening.status = "running"
            self.screening.processing_started_at = datetime.now(UTC) - timedelta(
                seconds=1200
            )
            mock_get_screening.return_value = self.screening

            mock_redact_pii.return_value = RedactionResult(
                text="text", counts={}, changed=False
            )
            mock_graph = AsyncMock()
            mock_graph.ainvoke.return_value = {
                "experience_assessment": self.mock_exp,
                "skills_assessment": self.mock_skills,
                "recommendation": Recommendation.HR_REVIEW,
                "recommendation_reason": "Valid",
            }
            mock_create_graph.return_value = mock_graph

            await process_screening(self.screening_id, allow_reclaim_running=False)

            self.assertEqual(self.screening.status, "completed")

        asyncio.run(_test())

    @patch("app.services.screening_service.get_settings")
    @patch("app.services.screening_service.record_audit_event")
    @patch("app.services.screening_service.get_screening")
    @patch("app.services.screening_service.create_screening_graph")
    @patch("app.services.screening_service.redact_pii")
    @patch("app.services.screening_service.AsyncSessionLocal")
    def test_process_screening_graph_failure_updates_status_and_logs_audit(
        self,
        mock_session_local: MagicMock,
        mock_redact_pii: MagicMock,
        mock_create_graph: MagicMock,
        mock_get_screening: AsyncMock,
        mock_record_audit: AsyncMock,
        mock_get_settings: MagicMock,
    ) -> None:
        """Verify exception during graph execution updates status to failed and logs failure audit event."""

        async def _test():
            mock_get_settings.return_value = self.mock_settings
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_get_screening.return_value = self.screening

            mock_redact_pii.return_value = RedactionResult(
                text="redacted", counts={"email": 1}, changed=True
            )

            mock_graph = AsyncMock()
            mock_graph.ainvoke.side_effect = RuntimeError("LLM API Timeout")
            mock_create_graph.return_value = mock_graph

            with self.assertRaises(RuntimeError):
                await process_screening(self.screening_id)

            self.assertEqual(self.screening.status, "failed")
            self.assertEqual(self.screening.error_code, "screening_processing_failed")
            self.assertEqual(self.screening.redaction_summary, {"email": 1})

            failed_audit_call = [
                call
                for call in mock_record_audit.await_args_list
                if call.kwargs.get("action") == "screening.processing_failed"
            ]
            self.assertEqual(len(failed_audit_call), 1)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
