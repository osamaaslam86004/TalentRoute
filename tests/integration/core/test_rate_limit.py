"""Unit and integration tests for app/core/rate_limit.py Redis Lua rate limiter."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, status
from redis.exceptions import ConnectionError, TimeoutError

from app.core.config import Settings
from app.core.rate_limit import (
    _INCREMENT_SCRIPT,
    close_rate_limit_client,
    enforce_screening_rate_limit,
)
from app.core.security import Principal


@pytest.fixture
def mock_request() -> Request:
    """Mock FastAPI request instance for rate limiting tests."""
    request = MagicMock(spec=Request)
    request.url.path = "/api/v1/screenings"
    return request


@pytest.fixture
def rate_limit_principal() -> Principal:
    """Mock Principal for rate limiting tests."""
    return Principal(
        subject="recruiter-123",
        tenant_id="tenant-acme",
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write"}),
        auth_method="oidc",
    )


class TestScreeningRateLimiter:
    """Test suite covering Redis Lua rate limiting, thresholds, and fail-open/fail-closed modes."""

    @pytest.mark.asyncio
    async def test_successful_rate_limit_within_threshold(
        self, mock_request: Request, rate_limit_principal: Principal
    ) -> None:
        """Verify requests within allowed threshold execute without raising HTTPException."""
        mock_redis = AsyncMock()
        # Mock Lua script eval returning [current_count, remaining_ttl]
        mock_redis.eval.return_value = [1, 60]

        settings = Settings(
            screening_rate_limit_requests=30,
            screening_rate_limit_window_seconds=60,
        )

        with (
            patch("app.core.rate_limit.get_settings", return_value=settings),
            patch("app.core.rate_limit._redis_client", return_value=mock_redis),
            patch("app.core.rate_limit.record_rate_limit_decision") as mock_metrics,
        ):
            await enforce_screening_rate_limit(mock_request, rate_limit_principal)

            mock_metrics.assert_called_once_with("allowed")
            mock_redis.eval.assert_called_once()
            # Verify SHA256 key generation format
            identity = "tenant-acme:recruiter-123:/api/v1/screenings"
            expected_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            expected_key = f"rate-limit:screening-create:{expected_digest}"
            args = mock_redis.eval.call_args[0]
            assert args[0] == _INCREMENT_SCRIPT
            assert args[1] == 1
            assert args[2] == expected_key
            assert args[3] == 60

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises_429(
        self, mock_request: Request, rate_limit_principal: Principal
    ) -> None:
        """Verify requests exceeding maximum threshold raise HTTP 429 with Retry-After header."""
        mock_redis = AsyncMock()
        # Return count 31 exceeding threshold 30, with 45s remaining TTL
        mock_redis.eval.return_value = [31, 45]

        settings = Settings(
            screening_rate_limit_requests=30,
            screening_rate_limit_window_seconds=60,
        )

        with (
            patch("app.core.rate_limit.get_settings", return_value=settings),
            patch("app.core.rate_limit._redis_client", return_value=mock_redis),
            patch("app.core.rate_limit.record_rate_limit_decision") as mock_metrics,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_screening_rate_limit(mock_request, rate_limit_principal)

            assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            assert exc_info.value.detail == "Screening submission rate limit exceeded"
            assert exc_info.value.headers == {"Retry-After": "45"}
            mock_metrics.assert_called_once_with("limited")

    @pytest.mark.asyncio
    async def test_redis_failure_fail_closed_mode(
        self, mock_request: Request, rate_limit_principal: Principal
    ) -> None:
        """Verify Redis backend failure in fail-closed mode raises HTTP 503 Service Unavailable."""
        mock_redis = AsyncMock()
        mock_redis.eval.side_effect = ConnectionError("Redis connection refused")

        settings = Settings(rate_limit_fail_open=False)

        with (
            patch("app.core.rate_limit.get_settings", return_value=settings),
            patch("app.core.rate_limit._redis_client", return_value=mock_redis),
            patch("app.core.rate_limit.record_rate_limit_decision") as mock_metrics,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_screening_rate_limit(mock_request, rate_limit_principal)

            assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert (
                exc_info.value.detail
                == "Submission rate limiter is temporarily unavailable"
            )
            assert exc_info.value.headers == {"Retry-After": "5"}
            mock_metrics.assert_called_once_with("backend_error_closed")

    @pytest.mark.asyncio
    async def test_redis_failure_fail_open_mode(
        self, mock_request: Request, rate_limit_principal: Principal
    ) -> None:
        """Verify Redis backend failure in fail-open mode allows request through quietly."""
        mock_redis = AsyncMock()
        mock_redis.eval.side_effect = TimeoutError("Redis socket timeout")

        settings = Settings(rate_limit_fail_open=True)

        with (
            patch("app.core.rate_limit.get_settings", return_value=settings),
            patch("app.core.rate_limit._redis_client", return_value=mock_redis),
            patch("app.core.rate_limit.record_rate_limit_decision") as mock_metrics,
        ):
            # Must not raise an exception
            await enforce_screening_rate_limit(mock_request, rate_limit_principal)
            mock_metrics.assert_called_once_with("backend_error_open")

    @pytest.mark.asyncio
    async def test_close_rate_limit_client() -> None:
        """Verify close_rate_limit_client closes Redis connection and clears cache."""
        mock_redis = AsyncMock()
        with patch(
            "app.core.rate_limit._redis_client", return_value=mock_redis
        ) as mock_get_client:
            await close_rate_limit_client()
            mock_redis.aclose.assert_called_once()
