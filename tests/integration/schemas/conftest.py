"""Integration test fixtures for schema validation, ORM round-trips, and endpoint payloads."""

from collections.abc import AsyncGenerator
import asyncio
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.security import Principal, get_principal
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(
    test_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def mock_principal() -> Principal:
    return Principal(
        subject="usr-test-schema-001",
        tenant_id="tenant-schema-test",
        roles=frozenset(
            {
                "recruiter",
                "reviewer",
                "auditor",
                "privacy_officer",
                "compliance_officer",
                "admin",
            }
        ),
        scopes=frozenset({"*"}),
        auth_method="api_key",
    )


@pytest_asyncio.fixture(scope="function")
async def async_client(
    db_session: AsyncSession,
    mock_principal: Principal,
) -> AsyncGenerator[AsyncClient, None]:
    async def _get_test_db():
        yield db_session

    async def _get_test_principal():
        return mock_principal

    app.dependency_overrides[get_db_session] = _get_test_db
    app.dependency_overrides[get_principal] = _get_test_principal

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()
