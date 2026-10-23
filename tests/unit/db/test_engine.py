"""Unit tests for asynchronous engine configuration, pooling parameters, and connection error handling."""
import pytest
from unittest.mock import patch, MagicMock
import asyncpg
from sqlalchemy.exc import OperationalError

from app.db.session import create_db_engine, check_database_health


@pytest.mark.asyncio
async def test_engine_initialization_parameters():
    """Verify that the async engine is created with correct pool configuration and database URL from settings."""
    mock_settings = MagicMock()
    mock_settings.DATABASE_URL = "postgresql+asyncpg://screening:secret@db:5432/screening"
    mock_settings.DB_POOL_SIZE = 10
    mock_settings.DB_MAX_OVERFLOW = 20
    mock_settings.DB_POOL_TIMEOUT = 30
    mock_settings.DB_ECHO = False

    with patch("app.db.session.create_async_engine") as mock_create_engine:
        create_db_engine(mock_settings)

        mock_create_engine.assert_called_once_with(
            mock_settings.DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            echo=False,
            pool_pre_ping=True
        )


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