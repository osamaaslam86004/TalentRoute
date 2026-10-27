"""Unit tests for asynchronous database session dependency injection and lifecycle management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.db.session import (
    configure_worker_db,
    create_worker_engine,
    get_db,
    get_db_session,
    get_worker_session,
)


@pytest.mark.asyncio
async def test_get_db_success():
    """Verify that get_db yields an active session, commits upon success, and closes properly."""
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)

    with patch("app.db.session.async_session_maker", mock_session_maker):
        async for session in get_db():
            assert session == mock_session
            # Simulate operations
            pass

        # Verify transaction lifecycle calls
        mock_session.commit.assert_awaited_once()
        mock_session.close.assert_awaited_once()
        mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_db_exception_triggers_rollback():
    """Verify that get_db rolls back the transaction and closes session when an exception occurs."""
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)

    with patch("app.db.session.async_session_maker", mock_session_maker):
        with pytest.raises(ValueError, match="Database transaction error"):
            async for session in get_db():
                assert session == mock_session
                raise ValueError("Database transaction error")

        # Verify rollback and close are executed, but commit is skipped
        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()
        mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_db_sqlalchemy_error_rollback():
    """Verify rollback behavior when encountering a SQLAlchemy error."""
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)

    with patch("app.db.session.async_session_maker", mock_session_maker):
        with pytest.raises(SQLAlchemyError):
            async for _session in get_db():
                raise SQLAlchemyError("Integrity constraint violation")

        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_session_success():
    """Verify that get_db_session yields an active session from AsyncSessionLocal."""
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)

    with patch("app.db.session.AsyncSessionLocal", mock_session_maker):
        async for session in get_db_session():
            assert session == mock_session


@pytest.mark.asyncio
async def test_get_worker_session_success():
    """Verify that get_worker_session yields an active session from WorkerAsyncSessionLocal."""
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)

    with patch("app.db.session.WorkerAsyncSessionLocal", mock_session_maker):
        async for session in get_worker_session():
            assert session == mock_session


def test_create_worker_engine_uses_nullpool():
    """Verify that create_worker_engine initializes an async engine using NullPool."""
    with patch("app.db.session.create_async_engine") as mock_create_engine:
        test_url = "sqlite+aiosqlite:///:memory:"
        create_worker_engine(test_url)
        mock_create_engine.assert_called_once_with(
            test_url,
            poolclass=NullPool,
        )


def test_configure_worker_db_updates_globals():
    """Verify that configure_worker_db rebinds AsyncSessionLocal to the worker engine."""
    with patch("app.db.session.AsyncSessionLocal") as mock_session_maker:
        configure_worker_db()
        mock_session_maker.configure.assert_called_once()
