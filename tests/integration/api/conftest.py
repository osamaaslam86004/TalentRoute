"""Global pytest fixtures for API integration test suite."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.security import Principal, get_principal
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def async_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an async SQLite database engine for test isolation."""
    # Use SQLite in-memory database with StaticPool for async test isolation
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(
    async_db_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional AsyncSession for test database operations."""
    async_session_factory = async_sessionmaker(
        async_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Bypass rate limit checks during integration testing."""
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        yield


@pytest.fixture
def mock_redis():
    """Provide a mocked Redis client for health check tests."""
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    with patch(
        "app.api.v1.endpoints.health.get_rate_limit_redis_client",
        return_value=client,
    ):
        yield client


@pytest.fixture
def principal_factory() -> Callable[..., Principal]:
    """Factory fixture to create customized auth Principals."""

    def _create_principal(
        subject: str = "test-user-001",
        tenant_id: str = "tenant_a",
        roles: set[str] | None = None,
        scopes: set[str] | None = None,
        auth_method: str = "oidc",
    ) -> Principal:
        return Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=frozenset(roles or {"recruiter"}),
            scopes=frozenset(scopes or set()),
            auth_method=auth_method,
        )

    return _create_principal


@pytest.fixture
def client_factory(
    db_session: AsyncSession,
) -> Callable[..., AsyncClient]:
    """Factory fixture generating AsyncClients with injected Principal dependencies."""

    def _create_client(principal: Principal | None = None) -> AsyncClient:
        async def _override_get_db_session():
            yield db_session

        async def _override_get_principal():
            if principal is None:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )
            return principal

        test_app = app
        test_app.dependency_overrides[get_db_session] = _override_get_db_session
        if principal is not None:
            test_app.dependency_overrides[get_principal] = _override_get_principal
        else:
            test_app.dependency_overrides.pop(get_principal, None)

        transport = ASGITransport(app=test_app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _create_client


@pytest_asyncio.fixture
async def recruiter_client_tenant_a(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="recruiter-a", tenant_id="tenant_a", roles={"recruiter"}
    )
    async with client_factory(principal) as client:
        yield client


@pytest_asyncio.fixture
async def reviewer_client_tenant_a(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="reviewer-a", tenant_id="tenant_a", roles={"reviewer"}
    )
    async with client_factory(principal) as client:
        yield client


@pytest_asyncio.fixture
async def auditor_client_tenant_a(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="auditor-a", tenant_id="tenant_a", roles={"auditor"}
    )
    async with client_factory(principal) as client:
        yield client


@pytest_asyncio.fixture
async def privacy_officer_client_tenant_a(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="privacy-a", tenant_id="tenant_a", roles={"privacy_officer"}
    )
    async with client_factory(principal) as client:
        yield client


@pytest_asyncio.fixture
async def compliance_officer_client_tenant_a(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="compliance-a", tenant_id="tenant_a", roles={"compliance_officer"}
    )
    async with client_factory(principal) as client:
        yield client


@pytest_asyncio.fixture
async def recruiter_client_tenant_b(
    client_factory: Callable[..., AsyncClient],
    principal_factory: Callable[..., Principal],
) -> AsyncGenerator[AsyncClient, None]:
    principal = principal_factory(
        subject="recruiter-b", tenant_id="tenant_b", roles={"recruiter"}
    )
    async with client_factory(principal) as client:
        yield client
