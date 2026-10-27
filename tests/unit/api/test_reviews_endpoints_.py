"""Integration and unit tests for human review and appeal API endpoints in app.api.v1.endpoints.reviews.

Tests cover:
- POST /api/v1/screenings/{screening_id}/feedback:
    * Success (201 Created) submitting ReviewerFeedbackCreate with reviewer permissions.
    * Verification of ReviewerFeedbackRead schema, audit event 'review.feedback_submitted' emission.
    * 404 Not Found when screening_id does not exist.
    * 403 Forbidden when principal lacks 'review:write' permission.
    * 422 Unprocessable Entity for invalid reason codes or malformed payloads.
- GET /api/v1/screenings/{screening_id}/feedback:
    * Success (200 OK) with paginated list, audit event 'review.feedback_listed' emission.
    * Tenant isolation ensuring items match tenant_id and screening_id.
    * 404 Not Found when screening is missing.
- POST /api/v1/reviews/{feedback_id}/appeals:
    * Success (201 Created) with 'review:appeal' permission (compliance_officer), parent linkage, and 'review.appeal_recorded' audit log.
    * 404 Not Found when attempting to appeal non-existent original feedback.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.v1.endpoints.reviews import router as reviews_router
from app.core.security import Principal, get_principal
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.models.screening import Screening
from app.db.session import get_db_session

# Mock pydantic_settings or opentelemetry if not installed in sandbox
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


class TestReviewsEndpoints(unittest.TestCase):
    """Unit and integration test suite for review and appeal endpoints."""

    def setUp(self) -> None:
        """Set up FastAPI test app with dependency overrides and TestClient."""
        self.app = FastAPI()
        self.app.include_router(reviews_router)

        self.tenant_id = "tenant-alpha"
        self.reviewer_principal = Principal(
            subject="reviewer-101",
            tenant_id=self.tenant_id,
            roles=frozenset({"reviewer"}),
            scopes=frozenset({"review:write", "review:read"}),
            auth_method="oidc",
        )

        self.compliance_principal = Principal(
            subject="compliance-202",
            tenant_id=self.tenant_id,
            roles=frozenset({"compliance_officer"}),
            scopes=frozenset({"review:appeal", "review:read"}),
            auth_method="oidc",
        )

        self.mock_db = AsyncMock()

        async def override_principal():
            return self.reviewer_principal

        async def override_db():
            yield self.mock_db

        self.app.dependency_overrides[get_principal] = override_principal
        self.app.dependency_overrides[get_db_session] = override_db

        self.client = TestClient(self.app)

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.append_feedback")
    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_submit_feedback_success_returns_201_and_emits_audit_event(
        self,
        mock_get_screening: AsyncMock,
        mock_append_feedback: AsyncMock,
        mock_record_audit: AsyncMock,
    ) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback creates feedback, records audit event, and returns 201 Created."""
        screening_id = uuid.uuid4()
        screening = Screening(
            id=screening_id,
            tenant_id=self.tenant_id,
            status="completed",
            application_text="Applicant resume content",
            job_title="Senior Python Engineer",
            required_skills=["Python", "FastAPI"],
        )
        mock_get_screening.return_value = screening

        feedback_id = uuid.uuid4()
        created_feedback = ReviewerFeedback(
            id=feedback_id,
            screening_id=screening_id,
            tenant_id=self.tenant_id,
            reviewer_subject="reviewer-101",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="agree",
            reason_codes=["evidence_missing"],
            notes="Valid evidence missing.",
            rubric_version="screening-rubric-v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )
        mock_append_feedback.return_value = created_feedback

        payload = {
            "label": "agree",
            "reason_codes": ["evidence_missing"],
            "notes": "Valid evidence missing.",
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["id"], str(feedback_id))
        self.assertEqual(body["screening_id"], str(screening_id))
        self.assertEqual(body["tenant_id"], self.tenant_id)
        self.assertEqual(body["label"], "agree")
        self.assertEqual(body["reason_codes"], ["evidence_missing"])

        mock_record_audit.assert_awaited_once()
        _, audit_kwargs = mock_record_audit.call_args
        self.assertEqual(audit_kwargs["action"], "review.feedback_submitted")
        self.assertEqual(audit_kwargs["resource_type"], "reviewer_feedback")
        self.assertEqual(audit_kwargs["resource_id"], str(feedback_id))
        self.assertEqual(audit_kwargs["metadata"]["label"], "agree")

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_submit_feedback_screening_not_found_returns_404(
        self, mock_get_screening: AsyncMock, mock_record_audit: AsyncMock
    ) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 404 when screening_id is non-existent."""
        mock_get_screening.return_value = None
        missing_id = uuid.uuid4()

        payload = {
            "label": "disagree",
            "reason_codes": ["role_requirement_mismatch"],
        }

        response = self.client.post(
            f"/api/v1/screenings/{missing_id}/feedback", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["detail"], "Screening not found")

    def test_submit_feedback_permission_denied_returns_403(self) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 403 when principal lacks review:write permission."""
        forbidden_principal = Principal(
            subject="unauthorized-user",
            tenant_id=self.tenant_id,
            roles=frozenset(),
            scopes=frozenset(),
            auth_method="oidc",
        )

        async def override_forbidden_principal():
            return forbidden_principal

        self.app.dependency_overrides[get_principal] = override_forbidden_principal

        screening_id = uuid.uuid4()
        payload = {
            "label": "agree",
            "reason_codes": ["evidence_missing"],
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()["detail"], "Insufficient permissions")

    def test_submit_feedback_invalid_reason_code_returns_422(self) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 422 for unsupported reason code."""
        screening_id = uuid.uuid4()
        invalid_payload = {
            "label": "agree",
            "reason_codes": ["invalid_unsupported_reason_code"],
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=invalid_payload
        )

        self.assertEqual(response.status_code, 422)

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_list_feedback_success_returns_paginated_history_and_emits_audit(
        self, mock_get_screening: AsyncMock, mock_record_audit: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns paginated review history and emits review.feedback_listed audit event."""
        screening_id = uuid.uuid4()
        mock_get_screening.return_value = Screening(
            id=screening_id,
            tenant_id=self.tenant_id,
            status="completed",
            application_text="Test",
            job_title="Engineer",
            required_skills=[],
        )

        fb1 = ReviewerFeedback(
            id=uuid.uuid4(),
            screening_id=screening_id,
            tenant_id=self.tenant_id,
            reviewer_subject="reviewer-101",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="agree",
            reason_codes=["evidence_missing"],
            notes=None,
            rubric_version="screening-rubric-v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [fb1]
        self.mock_db.scalars.return_value = mock_scalars

        response = self.client.get(
            f"/api/v1/screenings/{screening_id}/feedback?limit=10&offset=0"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["label"], "agree")
        self.assertEqual(items[0]["tenant_id"], self.tenant_id)

        mock_record_audit.assert_awaited_once()
        _, audit_kwargs = mock_record_audit.call_args
        self.assertEqual(audit_kwargs["action"], "review.feedback_listed")
        self.assertEqual(audit_kwargs["resource_type"], "screening")
        self.assertEqual(audit_kwargs["resource_id"], str(screening_id))

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_list_feedback_screening_not_found_returns_404(
        self, mock_get_screening: AsyncMock, mock_record_audit: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns 404 when screening does not exist."""
        mock_get_screening.return_value = None
        missing_id = uuid.uuid4()

        response = self.client.get(f"/api/v1/screenings/{missing_id}/feedback")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.append_appeal_outcome")
    @patch("app.api.v1.endpoints.reviews.get_feedback_by_id")
    def test_record_appeal_outcome_success_returns_201_and_emits_audit(
        self,
        mock_get_feedback: AsyncMock,
        mock_append_appeal: AsyncMock,
        mock_record_audit: AsyncMock,
    ) -> None:
        """Verify POST /api/v1/reviews/{feedback_id}/appeals creates appeal outcome, links parent feedback, and emits review.appeal_recorded audit log."""

        async def override_compliance_principal():
            return self.compliance_principal

        self.app.dependency_overrides[get_principal] = override_compliance_principal

        feedback_id = uuid.uuid4()
        original_review = ReviewerFeedback(
            id=feedback_id,
            screening_id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            reviewer_subject="reviewer-101",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="disagree",
            reason_codes=["role_requirement_mismatch"],
            notes=None,
            rubric_version="screening-rubric-v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )
        mock_get_feedback.return_value = original_review

        appeal_id = uuid.uuid4()
        appeal_item = ReviewerFeedback(
            id=appeal_id,
            screening_id=original_review.screening_id,
            tenant_id=self.tenant_id,
            reviewer_subject="compliance-202",
            reviewer_roles=["compliance_officer"],
            record_type="appeal",
            label=None,
            reason_codes=[],
            notes="Appeal upheld upon investigation.",
            rubric_version="screening-rubric-v1",
            parent_feedback_id=feedback_id,
            appeal_outcome="upheld",
            created_at=datetime.now(UTC),
        )
        mock_append_appeal.return_value = appeal_item

        payload = {"outcome": "upheld", "notes": "Appeal upheld upon investigation."}

        response = self.client.post(
            f"/api/v1/reviews/{feedback_id}/appeals", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["id"], str(appeal_id))
        self.assertEqual(body["parent_feedback_id"], str(feedback_id))
        self.assertEqual(body["appeal_outcome"], "upheld")
        self.assertEqual(body["record_type"], "appeal")

        mock_record_audit.assert_awaited_once()
        _, audit_kwargs = mock_record_audit.call_args
        self.assertEqual(audit_kwargs["action"], "review.appeal_recorded")
        self.assertEqual(audit_kwargs["resource_type"], "reviewer_feedback")
        self.assertEqual(audit_kwargs["resource_id"], str(appeal_id))
        self.assertEqual(audit_kwargs["metadata"]["appeal_outcome"], "upheld")

    @patch("app.api.v1.endpoints.reviews.record_audit_event")
    @patch("app.api.v1.endpoints.reviews.get_feedback_by_id")
    def test_record_appeal_outcome_original_feedback_missing_returns_404(
        self, mock_get_feedback: AsyncMock, mock_record_audit: AsyncMock
    ) -> None:
        """Verify POST /api/v1/reviews/{feedback_id}/appeals returns 404 when original feedback is missing."""

        async def override_compliance_principal():
            return self.compliance_principal

        self.app.dependency_overrides[get_principal] = override_compliance_principal

        mock_get_feedback.return_value = None
        missing_feedback_id = uuid.uuid4()

        payload = {"outcome": "revised", "notes": "Parent feedback missing"}

        response = self.client.post(
            f"/api/v1/reviews/{missing_feedback_id}/appeals", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["detail"], "Reviewer feedback not found")


if __name__ == "__main__":
    unittest.main()
