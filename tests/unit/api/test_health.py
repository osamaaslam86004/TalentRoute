### File: tests/unit/api/test_health.py
"""Unit tests for health check endpoints in app.api.v1.endpoints.health.

Tests cover:
- GET /health/live liveness check (200 OK regardless of DB/Redis status).
- GET /health/ready readiness check under healthy (200 OK) and unhealthy (503 Service Unavailable) DB/Redis states.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.health import router as health_router


class TestHealthEndpoints(unittest.TestCase):
    """Unit test suite for health liveness and readiness endpoints."""

    def setUp(self) -> None:
        """Set up isolated FastAPI test app with health router."""
        self.app = FastAPI()
        self.app.include_router(health_router)
        self.client = TestClient(self.app)

    def test_get_health_live_success(self) -> None:
        """Verify GET /health/live returns 200 OK with status 'alive'."""
        response = self.client.get("/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    @patch("app.api.v1.endpoints.health.get_rate_limit_redis_client")
    @patch("app.api.v1.endpoints.health.engine")
    def test_get_health_ready_all_components_healthy(
        self, mock_engine: MagicMock, mock_redis_factory: MagicMock
    ) -> None:
        """Verify GET /health/ready returns 200 OK when DB and Redis are healthy."""
        # Mock DB connection execute
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = None
        mock_engine.connect.return_value.__aenter__.return_value = mock_conn

        # Mock Redis client ping
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis_factory.return_value = mock_redis

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "database": "ok", "redis": "ok"},
        )

    @patch("app.api.v1.endpoints.health.get_rate_limit_redis_client")
    @patch("app.api.v1.endpoints.health.engine")
    def test_get_health_ready_database_down_returns_503(
        self, mock_engine: MagicMock, mock_redis_factory: MagicMock
    ) -> None:
        """Verify GET /health/ready returns 503 when DB connection fails."""
        # Mock DB connection failure
        mock_engine.connect.side_effect = Exception("DB Connection Refused")

        # Mock Redis healthy
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True
        mock_redis_factory.return_value = mock_redis

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "not_ready", "database": "unavailable", "redis": "ok"},
        )
        mock_redis.ping.assert_awaited_once()

    @patch("app.api.v1.endpoints.health.get_rate_limit_redis_client")
    @patch("app.api.v1.endpoints.health.engine")
    def test_get_health_ready_redis_down_returns_503(
        self, mock_engine: MagicMock, mock_redis_factory: MagicMock
    ) -> None:
        """Verify GET /health/ready returns 503 when Redis ping fails."""
        # Mock DB healthy
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__.return_value = mock_conn

        # Mock Redis failure
        mock_redis_factory.side_effect = Exception("Redis Unreachable")

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "not_ready", "database": "ok", "redis": "unavailable"},
        )

    @patch("app.api.v1.endpoints.health.get_rate_limit_redis_client")
    @patch("app.api.v1.endpoints.health.engine")
    def test_get_health_ready_all_components_down_returns_503(
        self, mock_engine: MagicMock, mock_redis_factory: MagicMock
    ) -> None:
        """Verify GET /health/ready returns 503 when both DB and Redis fail."""
        mock_engine.connect.side_effect = Exception("DB Fail")
        mock_redis_factory.side_effect = Exception("Redis Fail")

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "not_ready", "database": "unavailable", "redis": "unavailable"},
        )


if __name__ == "__main__":
    unittest.main()
