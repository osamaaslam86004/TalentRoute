"""Unit tests for asynchronous database session dependency injection and lifecycle management."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db


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
            async for session in get_db():
                raise SQLAlchemyError("Integrity constraint violation")

        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()