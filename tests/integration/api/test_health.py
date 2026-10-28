"""Integration tests for Liveness and Readiness health probes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_liveness_endpoint() -> None:
    """Verify that /health/live returns HTTP 200 with status alive."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_readiness_endpoint_all_healthy(mock_redis: AsyncMock) -> None:
    """Verify that /health/ready returns HTTP 200 when DB and Redis are reachable."""
    mock_engine = AsyncMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    with patch("app.api.v1.endpoints.health.engine", mock_engine):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
            assert response.status_code == 200
            assert response.json() == {
                "status": "ready",
                "database": "ok",
                "redis": "ok",
            }


@pytest.mark.asyncio
async def test_readiness_endpoint_db_failure(mock_redis: AsyncMock) -> None:
    """Verify HTTP 503 status when database probe encounters an exception."""
    mock_engine = AsyncMock()
    mock_engine.connect.side_effect = RuntimeError("Database connection timeout")

    with patch("app.api.v1.endpoints.health.engine", mock_engine):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert data["database"] == "unavailable"
            assert data["redis"] == "ok"


@pytest.mark.asyncio
async def test_readiness_endpoint_redis_failure() -> None:
    """Verify HTTP 503 status when Redis client ping raises an exception."""
    mock_engine = AsyncMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    mock_redis_client = AsyncMock()
    mock_redis_client.ping.side_effect = ConnectionError("Redis unreachable")

    with (
        patch("app.api.v1.endpoints.health.engine", mock_engine),
        patch(
            "app.api.v1.endpoints.health.get_rate_limit_redis_client",
            return_value=mock_redis_client,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "not_ready"
            assert data["database"] == "ok"
            assert data["redis"] == "unavailable"
