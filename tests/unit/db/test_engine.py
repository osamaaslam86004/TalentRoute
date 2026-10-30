"""Unit tests for asynchronous engine configuration, pooling parameters, and connection error handling."""

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from app.db import session
from app.db.session import check_database_health


@pytest.mark.asyncio
async def test_engine_initialization_parameters():
    """Verify that the async engine is created with correct pool configuration and database URL from settings."""
    mock_settings = MagicMock()
    mock_settings.database_url = (
        "postgresql+asyncpg://screening:secret@db:5432/screening"
    )

    with (
        patch("app.core.config.get_settings", return_value=mock_settings),
        patch("sqlalchemy.ext.asyncio.create_async_engine") as mock_create_engine,
        patch("app.observability.db_pool.register_db_pool_listeners"),
    ):
        # Reload the session module to trigger module-level engine creation with mock settings
        importlib.reload(session)

        mock_create_engine.assert_any_call(
            mock_settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
        )

    importlib.reload(session)


@pytest.mark.asyncio
async def test_database_health_check_success():
    """Verify health check returns True when connection probe succeeds."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    is_healthy = await check_database_health(mock_engine)

    assert is_healthy is True
    mock_conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_health_check_handles_asyncpg_interface_error():
    """Verify that connection interface errors (e.g. down backend) are gracefully handled and return False."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.execute.side_effect = asyncpg.InterfaceError("connection failed")
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    is_healthy = await check_database_health(mock_engine)

    assert is_healthy is False
