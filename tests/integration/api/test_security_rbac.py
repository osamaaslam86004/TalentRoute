"""Integration tests for RBAC Permission matrix and Authorization boundaries."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

from app.core.security import Principal


@pytest.mark.asyncio
async def test_unauthenticated_requests_fail(
    client_factory: Callable[..., AsyncClient],
) -> None:
    """Verify that unauthenticated HTTP requests return HTTP 401 Unauthorized."""
    unauth_client = client_factory(principal=None)
    async with unauth_client as client:
        res1 = await client.get("/api/v1/screenings")
        assert res1.status_code == 401

        res2 = await client.post("/api/v1/screenings", json={})
        assert res2.status_code == 401

        res3 = await client.get("/api/v1/audit-events/integrity")
        assert res3.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_screening_write"),
    [
        ("recruiter", 202),
        ("service", 202),
        ("reviewer", 403),
        ("auditor", 403),
        ("privacy_officer", 403),
        ("compliance_officer", 403),
    ],
)
async def test_rbac_screening_write_permissions(
    role: str,
    expected_screening_write: int,
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> None:
    """Verify screening:write permission boundaries across distinct roles."""
    principal = principal_factory(subject=f"test-{role}", roles={role})
    async with client_factory(principal) as client:
        res = await client.post(
            "/api/v1/screenings",
            json={
                "application_text": "RBAC screening write permissions test payload.",
                "job_title": "Systems Developer",
                "required_skills": ["Python"],
            },
        )
        assert res.status_code == expected_screening_write


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("privacy_officer", 404),  # Allowed access, returns 404 for random UUID
        ("recruiter", 403),
        ("reviewer", 403),
        ("auditor", 403),
    ],
)
async def test_rbac_screening_delete_permissions(
    role: str,
    expected_status: int,
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> None:
    """Verify screening:delete permission limits (restricted strictly to privacy_officer)."""
    principal = principal_factory(subject=f"test-{role}", roles={role})
    async with client_factory(principal) as client:
        import uuid

        res = await client.delete(f"/api/v1/screenings/{uuid.uuid4()}")
        assert res.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("auditor", 200),
        ("recruiter", 403),
        ("reviewer", 403),
        ("privacy_officer", 403),
    ],
)
async def test_rbac_audit_read_permissions(
    role: str,
    expected_status: int,
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> None:
    """Verify audit:read permission limits (restricted strictly to auditor)."""
    principal = principal_factory(subject=f"test-{role}", roles={role})
    async with client_factory(principal) as client:
        res = await client.get("/api/v1/audit-events/integrity")
        assert res.status_code == expected_status
