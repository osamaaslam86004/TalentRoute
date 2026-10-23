### File: tests/unit/api/test_reviews.py
### File: tests/unit/api/test_reviews.py
"""Unit tests for human review and appeal endpoints in app.api.v1.endpoints.reviews.

Tests cover:
- POST /api/v1/screenings/{screening_id}/feedback submitting reviewer labels and reason codes (201 Created), 404 for missing screening, and 422 for invalid label/reason codes/UUID.
- GET /api/v1/screenings/{screening_id}/feedback retrieving audit and review history and 422 for invalid pagination/UUID.
- POST /api/v1/reviews/{feedback_id}/appeals recording appeal outcomes (pending, upheld, revised), 404 for missing feedback, and 422 for invalid outcome/UUID.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.v1.endpoints.reviews import router as reviews_router
from app.core.security import Principal, require_permission
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.models.screening import Screening
from app.db.session import get_db_session


class TestReviewsAndAppealsEndpoints(unittest.TestCase):
    """Unit test suite for human review feedback and appeal endpoints."""

    def setUp(self) -> None:
        """Set up FastAPI test app with dependency overrides and TestClient."""
        self.app = FastAPI()
        self.app.include_router(reviews_router)

        self.mock_principal = Principal(
            subject="reviewer-456",
            tenant_id="tenant-alpha",
            roles=frozenset({"reviewer", "compliance_officer"}),
            scopes=frozenset({"review:write", "review:read", "review:appeal"}),
            auth_method="oidc",
        )

        self.mock_db = AsyncMock()

        async def override_principal():
            return self.mock_principal

        async def override_db():
            yield self.mock_db

        self.app.dependency_overrides[require_permission("review:write")] = (
            override_principal
        )
        self.app.dependency_overrides[require_permission("review:read")] = (
            override_principal
        )
        self.app.dependency_overrides[require_permission("review:appeal")] = (
            override_principal
        )
        self.app.dependency_overrides[get_db_session] = override_db

        self.client = TestClient(self.app)

    @patch("app.api.v1.endpoints.reviews.append_feedback")
    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_submit_feedback_success_returns_201_created(
        self, mock_get_screening: AsyncMock, mock_append_feedback: AsyncMock
    ) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback appends feedback and returns 201."""
        screening_id = uuid.uuid4()
        screening = Screening(
            id=screening_id,
            tenant_id="tenant-alpha",
            status="completed",
            application_text="Sample text",
            job_title="Dev",
            required_skills=[],
        )
        mock_get_screening.return_value = screening

        feedback_id = uuid.uuid4()
        feedback_item = ReviewerFeedback(
            id=feedback_id,
            screening_id=screening_id,
            tenant_id="tenant-alpha",
            reviewer_subject="reviewer-456",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="agree",
            reason_codes=["evidence_missing"],
            notes="Checked evidence.",
            rubric_version="screening-rubric-v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )
        mock_append_feedback.return_value = feedback_item

        payload = {
            "label": "agree",
            "reason_codes": ["evidence_missing"],
            "notes": "Checked evidence.",
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["id"], str(feedback_id))
        self.assertEqual(body["label"], "agree")
        self.assertEqual(body["reason_codes"], ["evidence_missing"])

    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_submit_feedback_screening_not_found_returns_404(
        self, mock_get_screening: AsyncMock
    ) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 404 when screening missing."""
        mock_get_screening.return_value = None
        random_id = uuid.uuid4()

        payload = {
            "label": "disagree",
            "reason_codes": ["role_requirement_mismatch"],
        }

        response = self.client.post(
            f"/api/v1/screenings/{random_id}/feedback", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_submit_feedback_invalid_label_returns_422(self) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 422 for unsupported label."""
        screening_id = uuid.uuid4()
        invalid_payload = {
            "label": "not-a-label",
            "reason_codes": [],
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=invalid_payload
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_submit_feedback_invalid_reason_code_returns_422(self) -> None:
        """Verify POST /api/v1/screenings/{screening_id}/feedback returns 422 for unsupported reason code."""
        screening_id = uuid.uuid4()
        invalid_payload = {
            "label": "agree",
            "reason_codes": ["unsupported_reason_code_xyz"],
        }

        response = self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback", json=invalid_payload
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_feedback_rejects_invalid_screening_uuid_returns_422(self) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns 422 when screening_id is not a valid UUID."""
        response = self.client.get("/api/v1/screenings/not-a-uuid/feedback")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_list_feedback_success_returns_history(
        self, mock_get_screening: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns reviewer history list."""
        screening_id = uuid.uuid4()
        mock_get_screening.return_value = Screening(
            id=screening_id,
            tenant_id="tenant-alpha",
            status="completed",
            application_text="T",
            job_title="J",
            required_skills=[],
        )

        fb1 = ReviewerFeedback(
            id=uuid.uuid4(),
            screening_id=screening_id,
            tenant_id="tenant-alpha",
            reviewer_subject="reviewer-1",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="agree",
            reason_codes=["evidence_missing"],
            notes=None,
            rubric_version="v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )

        # Mock scalars query return
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [fb1]
        self.mock_db.scalars.return_value = mock_scalars

        response = self.client.get(f"/api/v1/screenings/{screening_id}/feedback")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["label"], "agree")

    @patch("app.api.v1.endpoints.reviews.get_screening")
    def test_list_feedback_screening_not_found_returns_404(
        self, mock_get_screening: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns 404 when screening missing."""
        mock_get_screening.return_value = None
        random_id = uuid.uuid4()

        response = self.client.get(f"/api/v1/screenings/{random_id}/feedback")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_feedback_rejects_invalid_pagination_returns_422(self) -> None:
        """Verify GET /api/v1/screenings/{screening_id}/feedback returns 422 for invalid limit
        or offset query parameters."""
        screening_id = uuid.uuid4()
        for query in ["?limit=0", "?limit=101", "?offset=-1"]:
            with self.subTest(query=query):
                response = self.client.get(
                    f"/api/v1/screenings/{screening_id}/feedback{query}"
                )
                self.assertEqual(
                    response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY
                )

    @patch("app.api.v1.endpoints.reviews.append_appeal_outcome")
    @patch("app.api.v1.endpoints.reviews.get_feedback_by_id")
    def test_record_appeal_outcome_success_returns_201_created(
        self, mock_get_feedback: AsyncMock, mock_append_appeal: AsyncMock
    ) -> None:
        """Verify POST /api/v1/reviews/{feedback_id}/appeals appends appeal record."""
        feedback_id = uuid.uuid4()
        original_fb = ReviewerFeedback(
            id=feedback_id,
            screening_id=uuid.uuid4(),
            tenant_id="tenant-alpha",
            reviewer_subject="reviewer-1",
            reviewer_roles=["reviewer"],
            record_type="review",
            label="disagree",
            reason_codes=["role_requirement_mismatch"],
            notes=None,
            rubric_version="v1",
            parent_feedback_id=None,
            appeal_outcome=None,
            created_at=datetime.now(UTC),
        )
        mock_get_feedback.return_value = original_fb

        appeal_id = uuid.uuid4()
        appeal_item = ReviewerFeedback(
            id=appeal_id,
            screening_id=original_fb.screening_id,
            tenant_id="tenant-alpha",
            reviewer_subject="reviewer-456",
            reviewer_roles=["compliance_officer"],
            record_type="appeal",
            label=None,
            reason_codes=[],
            notes="Appeal upheld after review.",
            rubric_version="v1",
            parent_feedback_id=feedback_id,
            appeal_outcome="upheld",
            created_at=datetime.now(UTC),
        )
        mock_append_appeal.return_value = appeal_item

        payload = {"outcome": "upheld", "notes": "Appeal upheld after review."}

        response = self.client.post(
            f"/api/v1/reviews/{feedback_id}/appeals", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["id"], str(appeal_id))
        self.assertEqual(body["appeal_outcome"], "upheld")
        self.assertEqual(body["record_type"], "appeal")

    @patch("app.api.v1.endpoints.reviews.get_feedback_by_id")
    def test_record_appeal_outcome_feedback_not_found_returns_404(
        self, mock_get_feedback: AsyncMock
    ) -> None:
        """Verify POST /api/v1/reviews/{feedback_id}/appeals returns 404 when original review is missing."""
        mock_get_feedback.return_value = None
        random_id = uuid.uuid4()

        payload = {"outcome": "revised", "notes": "Missing parent feedback"}

        response = self.client.post(
            f"/api/v1/reviews/{random_id}/appeals", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_record_appeal_outcome_invalid_outcome_returns_422(self) -> None:
        """Verify POST /api/v1/reviews/{feedback_id}/appeals returns 422 for invalid appeal outcome."""
        feedback_id = uuid.uuid4()
        payload = {"outcome": "not-valid", "notes": "appeal note"}

        response = self.client.post(
            f"/api/v1/reviews/{feedback_id}/appeals", json=payload
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)


if __name__ == "__main__":
    unittest.main()
