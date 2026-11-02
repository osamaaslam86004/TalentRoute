"""Fixtures for repository integration tests."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.db.models  # noqa: F401 - Ensure models are registered
from app.db.base import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a shared SQLite engine for repository integration tests."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    db_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session for repository tests."""
    connection = await db_engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session

    await transaction.rollback()
    await connection.close()


@pytest.fixture
def sample_pii_texts() -> dict[str, str]:
    return {
        "email_and_phone": "Please contact me at jane.doe@example.com or +1 (555) 019-2834.",
        "gov_id": "My SSN: 123-45-6789 and Passport Number: AB1234567.",
        "labelled_name": "Applicant: Jane Smith\nFull Name: Johnathan Doe",
        "self_identified_name": "Hello, my name is Alex Rivera and I am applying for the role.",
        "labelled_address": "Home Address: 742 Evergreen Terrace, Springfield, OR 97477",
        "unlabelled_street_address": "I live at 123 Main Street, Suite 400, Austin, TX 78701.",
        "clean_text": "Experienced Python developer with 5 years in FastAPI and SQLAlchemy.",
    }
