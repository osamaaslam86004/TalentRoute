### File: tests/unit/api/test_audit.py

"""Offline audit endpoint tests."""

from __future__ import annotations

import uuid
import pytest
import unittest

from datetime import UTC, datetime

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.v1.endpoints.audit import router as audit_router
from app.main import app
from app.core.security import Principal, require_permission
from app.db.models.audit_event import AuditEvent
from app.db.session import get_db_session


@pytest.fixture
def client():
    session = AsyncMock()
    session.commit = AsyncMock()

    async def db_override():
        yield session

    app.dependency_overrides[get_db_session] = db_override

    def walk(nodes):
        for node in nodes:
            if node.call is not None:
                yield node.call
            yield from walk(node.dependencies)

    for route in app.routes:
        if hasattr(route, "dependant"):
            for dep in walk(route.dependant.dependencies):
                if getattr(dep, "__module__", "") == "app.core.security":
                    app.dependency_overrides[dep] = lambda: MagicMock(
                        tenant_id="tenant-test",
                        subject="auditor-test",
                        roles={"auditor"},
                        scopes=set(),
                    )
    yield TestClient(app), session
    app.dependency_overrides.clear()


def test_integrity_endpoint_returns_verification_and_records_audit(client):
    http, session = client
    result = {
        "tenant_id": "tenant-test",
        "valid": True,
        "event_count": 0,
        "head_sequence": 0,
        "first_failure_sequence": None,
        "failure_reason": None,
        "head_hash": "0" * 64,
    }
    with (
        patch(
            "app.api.v1.endpoints.audit.verify_audit_chain",
            new_callable=AsyncMock,
            return_value=result,
        ),
        patch(
            "app.api.v1.endpoints.audit.record_audit_event", new_callable=AsyncMock
        ) as record,
    ):
        response = http.get("/api/v1/audit-events/integrity")
    assert response.status_code == 200
    assert response.json()["valid"] is True
    record.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("query", ["?limit=0", "?limit=501", "?offset=-1"])
def test_audit_list_rejects_invalid_pagination(client, query):
    http, _ = client
    response = http.get("/api/v1/audit-events" + query)
    assert response.status_code == 422


def test_audit_list_rejects_overlong_action_filter(client):
    http, _ = client
    response = http.get("/api/v1/audit-events?action=" + "x" * 121)
    assert response.status_code == 422


def test_audit_list_returns_paged_empty_result(client):
    http, session = client
    with (
        patch(
            "app.api.v1.endpoints.audit.list_audit_events",
            new_callable=AsyncMock,
            return_value=([], 0),
        ),
        patch(
            "app.api.v1.endpoints.audit.record_audit_event", new_callable=AsyncMock
        ) as record,
    ):
        response = http.get(
            "/api/v1/audit-events?limit=25&offset=5&action=screening.created"
        )
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 25, "offset": 5, "count": 0}
    record.assert_awaited_once()
    session.commit.assert_awaited_once()


"""Unit tests for audit event logging and integrity verification endpoints in app.api.v1.endpoints.audit.

Tests cover:
- GET /api/v1/audit-events listing, pagination (limit/offset), action filtering, 
and auditor role permission requirements.
- GET /api/v1/audit-events/integrity verifying tamper-evident hash chain continuity 
and returning integrity status reports.
"""


class TestAuditEndpoints(unittest.TestCase):
    """Unit test suite for audit event and chain integrity endpoints."""

    def setUp(self) -> None:
        """Set up FastAPI test app with dependency overrides and TestClient."""
        self.app = FastAPI()
        self.app.include_router(audit_router)

        self.mock_principal = Principal(
            subject="auditor-789",
            tenant_id="tenant-alpha",
            roles=frozenset({"auditor"}),
            scopes=frozenset({"audit:read"}),
            auth_method="oidc",
        )

        self.mock_db = AsyncMock()

        async def override_principal():
            return self.mock_principal

        async def override_db():
            yield self.mock_db

        self.app.dependency_overrides[require_permission("audit:read")] = (
            override_principal
        )
        self.app.dependency_overrides[get_db_session] = override_db

        self.client = TestClient(self.app)

    @patch("app.api.v1.endpoints.audit.list_audit_events")
    def test_get_audit_events_success_returns_paginated_list(
        self, mock_list_audit_events: AsyncMock
    ) -> None:
        """Verify GET /api/v1/audit-events returns paginated audit event list."""
        event_id = uuid.uuid4()
        event_item = AuditEvent(
            id=event_id,
            tenant_id="tenant-alpha",
            sequence_number=1,
            action="screening.created",
            actor_hash="hashed_actor_123",
            resource_type="screening",
            resource_id="res-001",
            outcome="success",
            event_metadata={"status": "queued"},
            payload_hash="payload_sha256_hash",
            prev_event_hash="0000000000000000000000000000000000000000000000000000000000000000",
            event_hash="computed_event_hash_001",
            created_at=datetime.now(UTC),
        )
        mock_list_audit_events.return_value = ([event_item], 1)

        response = self.client.get("/api/v1/audit-events?limit=50&offset=0")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["id"], str(event_id))
        self.assertEqual(body["items"][0]["action"], "screening.created")

    @patch("app.api.v1.endpoints.audit.list_audit_events")
    def test_get_audit_events_filter_by_action(
        self, mock_list_audit_events: AsyncMock
    ) -> None:
        """Verify GET /api/v1/audit-events supports action filtering."""
        mock_list_audit_events.return_value = ([], 0)

        response = self.client.get("/api/v1/audit-events?action=screening.created")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_list_audit_events.assert_awaited_once_with(
            self.mock_db,
            tenant_id="tenant-alpha",
            limit=100,
            offset=0,
            action="screening.created",
        )

    @patch("app.api.v1.endpoints.audit.verify_audit_chain")
    def test_get_audit_integrity_valid_chain_returns_report(
        self, mock_verify_chain: AsyncMock
    ) -> None:
        """Verify GET /api/v1/audit-events/integrity returns valid audit chain status."""
        mock_verify_chain.return_value = {
            "valid": True,
            "tenant_id": "tenant-alpha",
            "event_count": 10,
            "last_verified_sequence": 10,
            "error_detail": None,
        }

        response = self.client.get("/api/v1/audit-events/integrity")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["event_count"], 10)
        self.assertEqual(body["tenant_id"], "tenant-alpha")

    @patch("app.api.v1.endpoints.audit.verify_audit_chain")
    def test_get_audit_integrity_corrupted_chain_returns_failure_report(
        self, mock_verify_chain: AsyncMock
    ) -> None:
        """Verify GET /api/v1/audit-events/integrity reports chain invalidity when hashes mismatch."""
        mock_verify_chain.return_value = {
            "valid": False,
            "tenant_id": "tenant-alpha",
            "event_count": 5,
            "last_verified_sequence": 3,
            "error_detail": "Hash mismatch at sequence 4",
        }

        response = self.client.get("/api/v1/audit-events/integrity")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["error_detail"], "Hash mismatch at sequence 4")


if __name__ == "__main__":
    unittest.main()
