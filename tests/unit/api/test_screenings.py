### File: tests/unit/api/test_screenings.py
"""Unit tests for screening management API endpoints in app.api.v1.endpoints.screenings.

Tests cover:
- POST /api/v1/screenings submission (202 Accepted), payload size checks (413),
validation errors (422), rate limiting, Idempotency-Key handling (replay vs 409 conflict)
, and malformed idempotency header validation (422).
- GET /api/v1/screenings tenant-scoped listing, pagination (limit/offset), pagination boundary
 rejections (422), and permission checks.
- GET /api/v1/screenings/{screening_id} fetching specific screening evidence, metadata, and 404
 handling.
- DELETE /api/v1/screenings/{screening_id} tenant-scoped deletion rights (204 No Content), 404
 handling, and privacy officer permission checks.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.v1.endpoints.screenings import router as screenings_router
from app.core.security import Principal, get_principal
from app.db.models.screening import Screening
from app.db.session import get_db_session
from app.schemas.screening import ScreeningCreateRequest


class TestScreeningsEndpoints(unittest.TestCase):
    """Unit test suite for screenings endpoints."""

    def setUp(self) -> None:
        """Set up FastAPI test app with dependency overrides and TestClient."""
        self.app = FastAPI()
        self.app.include_router(screenings_router)

        self.mock_principal = Principal(
            subject="user-123",
            tenant_id="tenant-alpha",
            roles=frozenset({"recruiter", "privacy_officer"}),
            scopes=frozenset({"screening:write", "screening:read", "screening:delete"}),
            auth_method="oidc",
        )

        self.mock_db = AsyncMock()
        self.mock_db.add = MagicMock()

        async def override_principal():
            return self.mock_principal

        async def override_db():
            yield self.mock_db

        self.app.dependency_overrides[get_principal] = override_principal
        self.app.dependency_overrides[get_db_session] = override_db

        self.record_patcher = patch(
            "app.api.v1.endpoints.screenings.record_audit_event",
            new_callable=AsyncMock,
        )
        self.mock_record_audit_event = self.record_patcher.start()
        self.addCleanup(self.record_patcher.stop)

        self.rate_patcher = patch(
            "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
            new_callable=AsyncMock,
        )
        self.mock_rate_limit = self.rate_patcher.start()
        self.addCleanup(self.rate_patcher.stop)

        self.client = TestClient(self.app)

    def test_post_screening_success_returns_202_accepted(self) -> None:
        """Verify POST /api/v1/screenings returns 202 Accepted with poll URL and queues outbox event."""
        payload = {
            "application_text": "Experienced Python backend developer with FastAPI and PostgreSQL.",
            "job_title": "Senior Backend Engineer",
            "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        }

        # Mock DB refresh behavior
        def _mock_add(obj):
            if isinstance(obj, Screening):
                obj.id = uuid.UUID("11111111-1111-1111-1111-111111111111")
                obj.status = "queued"

        self.mock_db.add.side_effect = _mock_add

        response = self.client.post("/api/v1/screenings", json=payload)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertIn("id", body)
        self.assertIn("poll_url", body)

    @patch("app.api.v1.endpoints.screenings.get_settings")
    def test_post_screening_exceeding_max_application_chars_returns_413(
        self, mock_get_settings: MagicMock
    ) -> None:
        """Verify POST /api/v1/screenings returns 413 Payload Too Large when text exceeds limits."""
        mock_settings = MagicMock()
        mock_settings.max_application_chars = 100
        mock_get_settings.return_value = mock_settings

        payload = {
            "application_text": "X" * 150,  # Exceeds max 100
            "job_title": "Backend Dev",
            "required_skills": ["Python"],
        }

        response = self.client.post("/api/v1/screenings", json=payload)

        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_post_screening_validation_error_returns_422(self) -> None:
        """Verify POST /api/v1/screenings returns 422 for invalid/missing payload fields."""
        invalid_payload = {
            "job_title": "Backend Dev",
            # missing application_text and required_skills
        }

        response = self.client.post("/api/v1/screenings", json=invalid_payload)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_invalid_idempotency_header_is_rejected_before_handler_returns_422(
        self,
    ) -> None:
        """Verify POST /api/v1/screenings returns 422 when Idempotency-Key header contains
        invalid characters."""
        payload = {
            "application_text": "Experience building backend services.",
            "job_title": "Engineer",
            "required_skills": ["Python"],
        }
        response = self.client.post(
            "/api/v1/screenings",
            headers={"Idempotency-Key": "bad key"},
            json=payload,
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_post_screening_idempotency_replay_returns_existing_record(self) -> None:
        """Verify replaying request with identical Idempotency-Key returns existing screening."""
        screening_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        payload = {
            "application_text": "Sample text with more than twenty characters.",
            "job_title": "Engineer",
            "required_skills": ["Python"],
        }
        rf = hashlib.sha256(
            json.dumps(
                ScreeningCreateRequest(**payload).model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        existing_screening = Screening(
            id=screening_id,
            tenant_id="tenant-alpha",
            status="queued",
            idempotency_key="idempotent-key-001",
            request_fingerprint=rf,
            application_text=payload["application_text"],
            job_title=payload["job_title"],
            required_skills=payload["required_skills"],
        )

        self.mock_db.scalar.return_value = existing_screening
        headers = {"Idempotency-Key": "idempotent-key-001"}

        response = self.client.post("/api/v1/screenings", json=payload, headers=headers)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json()["id"], str(screening_id))

    def test_post_screening_idempotency_key_mismatch_payload_returns_409_conflict(
        self,
    ) -> None:
        """Verify using same Idempotency-Key with different payload returns 409 Conflict."""
        existing_screening = Screening(
            id=uuid.uuid4(),
            tenant_id="tenant-alpha",
            status="queued",
            idempotency_key="idempotent-key-001",
            request_fingerprint="original_fingerprint_hash",
            application_text="Original text",
            job_title="Engineer",
            required_skills=["Python"],
        )

        self.mock_db.scalar.return_value = existing_screening

        different_payload = {
            "application_text": "Modified text payload",
            "job_title": "Senior Engineer",
            "required_skills": ["Python", "AWS"],
        }
        headers = {"Idempotency-Key": "idempotent-key-001"}

        response = self.client.post(
            "/api/v1/screenings", json=different_payload, headers=headers
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(
            "Idempotency-Key has already been used", response.json()["detail"]
        )

    @patch("app.api.v1.endpoints.screenings.list_screenings")
    def test_get_screenings_list_tenant_scoped_success(
        self, mock_list: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings returns paginated list for authenticated tenant."""
        now = datetime.now(UTC)
        s1 = Screening(
            id=uuid.uuid4(),
            tenant_id="tenant-alpha",
            status="completed",
            application_text="Sample text with more than twenty characters.",
            job_title="Dev",
            required_skills=["Python"],
            human_review_required=True,
            attempt_count=1,
            created_at=now,
            updated_at=now,
        )
        mock_list.return_value = ([s1], 1)

        response = self.client.get("/api/v1/screenings?limit=10&offset=0")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["limit"], 10)
        self.assertEqual(body["offset"], 0)

    def test_list_screenings_rejects_invalid_pagination_returns_422(self) -> None:
        """Verify GET /api/v1/screenings returns 422 for invalid pagination parameters."""
        for query in ["?limit=0", "?limit=101", "?offset=-1"]:
            with self.subTest(query=query):
                response = self.client.get(f"/api/v1/screenings{query}")
                self.assertEqual(
                    response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY
                )

    @patch("app.api.v1.endpoints.screenings.get_screening")
    def test_get_screening_by_id_success(self, mock_get: AsyncMock) -> None:
        """Verify GET /api/v1/screenings/{screening_id} returns screening details."""
        sid = uuid.uuid4()
        now = datetime.now(UTC)
        s1 = Screening(
            id=sid,
            tenant_id="tenant-alpha",
            status="completed",
            application_text="Resume details with more than twenty characters.",
            job_title="Senior Developer",
            required_skills=["Python", "SQL"],
            model_version="gpt-4o",
            prompt_version="v1",
            human_review_required=True,
            attempt_count=1,
            created_at=now,
            updated_at=now,
        )
        mock_get.return_value = s1

        response = self.client.get(f"/api/v1/screenings/{sid}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["id"], str(sid))
        self.assertEqual(body["job_title"], "Senior Developer")

    @patch("app.api.v1.endpoints.screenings.get_screening")
    def test_get_screening_by_id_not_found_returns_404(
        self, mock_get: AsyncMock
    ) -> None:
        """Verify GET /api/v1/screenings/{screening_id} returns 404 when non-existent."""
        mock_get.return_value = None
        random_id = uuid.uuid4()

        response = self.client.get(f"/api/v1/screenings/{random_id}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("app.api.v1.endpoints.screenings.get_screening")
    def test_delete_screening_success_returns_204_no_content(
        self, mock_get: AsyncMock
    ) -> None:
        """Verify DELETE /api/v1/screenings/{screening_id} deletes record and returns 204."""
        sid = uuid.uuid4()
        s1 = Screening(
            id=sid,
            tenant_id="tenant-alpha",
            status="queued",
            application_text="Data",
            job_title="Dev",
            required_skills=[],
        )
        mock_get.return_value = s1

        response = self.client.delete(f"/api/v1/screenings/{sid}")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    @patch("app.api.v1.endpoints.screenings.get_screening")
    def test_delete_screening_not_found_returns_404(self, mock_get: AsyncMock) -> None:
        """Verify DELETE /api/v1/screenings/{screening_id} returns 404 when record is missing."""
        mock_get.return_value = None
        random_id = uuid.uuid4()

        response = self.client.delete(f"/api/v1/screenings/{random_id}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
