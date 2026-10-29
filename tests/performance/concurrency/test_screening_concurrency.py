"""Concurrency integration tests for idempotency races, rate limiter pressure, and DB connection pooling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.rate_limit import enforce_screening_rate_limit
from app.core.security import Principal
from app.db.models.screening import Screening
from app.db.session import get_db_session
from app.main import app


@pytest.mark.asyncio
async def test_idempotency_race_condition(perf_session_factory, tenant_a: str):
    """Fire 50 simultaneous requests with the exact same Idempotency-Key and payload."""
    shared_key = f"race-key-{asyncio.get_event_loop().time()}"
    payload = {
        "application_text": (
            "Senior Backend Engineer with 10 years experience building distributed "
            "Python microservices, Redis caching, and async database integrations."
        ),
        "job_title": "Principal Architect",
        "required_skills": ["Python", "FastAPI", "Redis", "PostgreSQL"],
    }

    async def _override_get_db():
        async with perf_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={
            "X-API-Key": "dev-key",
            "Idempotency-Key": shared_key,
            "Content-Type": "application/json",
        },
    ) as client:
        # Fire 50 simultaneous requests concurrently
        tasks = [client.post("/api/v1/screenings", json=payload) for _ in range(50)]
        responses = await asyncio.gather(*tasks, return_exceptions=False)

    app.dependency_overrides.clear()

    # Assertions
    status_codes = [r.status_code for r in responses]

    # Every response must be either 202 Accepted (initial or idempotency replay) or 409 Conflict (race lost)
    for code in status_codes:
        assert code in (202, 409), f"Unexpected HTTP status code: {code}"

    # Verify database state: exactly ONE screening record exists with this idempotency key
    async with perf_session_factory() as session:
        statement = select(Screening).where(
            Screening.tenant_id == "default",
            Screening.idempotency_key == shared_key,
        )
        records = list((await session.scalars(statement)).all())
        assert len(records) == 1, f"Expected 1 record in DB, found {len(records)}"


@pytest.mark.asyncio
async def test_rate_limiter_concurrency_pressure(
    principal_recruiter_tenant_a: Principal,
):
    """Simulate concurrent pressure on Redis rate limiter script to verify atomic threshold enforcement."""
    mock_request = AsyncMock()
    mock_request.url.path = "/api/v1/screenings"

    # Mock Redis eval return values simulating incrementing counters
    call_counter = 0

    async def _mock_eval(*args, **kwargs):
        nonlocal call_counter
        call_counter += 1
        # Allow up to 30 requests, then exceed limit
        return [call_counter, 60]

    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(side_effect=_mock_eval)

    with patch("app.core.rate_limit._redis_client", return_value=mock_redis):
        tasks = [
            enforce_screening_rate_limit(mock_request, principal_recruiter_tenant_a)
            for _ in range(50)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    allowed = [r for r in results if r is None]
    limited = [
        r
        for r in results
        if isinstance(r, Exception) and getattr(r, "status_code", None) == 429
    ]

    assert len(allowed) == 30, f"Expected 30 allowed requests, got {len(allowed)}"
    assert len(limited) == 20, f"Expected 20 rate-limited requests, got {len(limited)}"


@pytest.mark.asyncio
async def test_db_pool_saturation_resilience(perf_session_factory):
    """Simulate high database query concurrency matching/exceeding pool limits to ensure zero deadlocks."""

    async def _worker_query(worker_id: int) -> int:
        async with perf_session_factory() as session:
            statement = select(Screening).where(
                Screening.tenant_id == f"tenant-{worker_id}"
            )
            result = await session.scalars(statement)
            return len(list(result.all()))

    # Launch 50 concurrent database operations
    tasks = [_worker_query(i) for i in range(50)]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    assert len(results) == 50
    assert all(r == 0 for r in results)
