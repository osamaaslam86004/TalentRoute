"""Shared E2E test fixtures, FastAPI test client setup, and security principal overrides.

Design
------
Each client fixture carries its own ``Principal`` object.  Rather than overwriting
a single global ``app.dependency_overrides[get_principal]`` (which races when
multiple client fixtures coexist in the same test), we store the current principal
in a ``contextvars.ContextVar``.  A *single* app-level override reads from that
var, so every request picks up the principal that was placed there *immediately
before the call* by the ``AsyncClient`` subclass below.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient, Request, Response
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

# ---------------------------------------------------------------------------
# Per-request principal context var
# ---------------------------------------------------------------------------

_current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "_current_principal", default=None
)


# ---------------------------------------------------------------------------
# Global app-level dependency overrides (set once, never cleared mid-test)
# ---------------------------------------------------------------------------

_e2e_session_holder: dict[str, AsyncSession | None] = {"session": None}


async def _override_get_db():
    session = _e2e_session_holder["session"]
    if session is None:
        raise RuntimeError("e2e DB session not initialised")
    yield session


async def _override_get_principal():
    principal = _current_principal.get()
    if principal is None:
        # No principal set → unauthenticated → reject.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


# Install the overrides once at module level so they survive the entire session.
app.dependency_overrides[get_db_session] = _override_get_db
app.dependency_overrides[get_principal] = _override_get_principal


# ---------------------------------------------------------------------------
# Async HTTPX client that sets the context-var before each request
# ---------------------------------------------------------------------------


class PrincipalClient(AsyncClient):
    """AsyncClient that injects a fixed ``Principal`` into every request."""

    def __init__(self, principal: Principal | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._principal = principal

    async def send(self, request: Request, **kwargs: Any) -> Response:  # type: ignore[override]
        token = _current_principal.set(self._principal)
        try:
            return await super().send(request, **kwargs)
        finally:
            _current_principal.reset(token)


# ---------------------------------------------------------------------------
# Event loop (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Database engine & session (session-scoped engine, function-scoped session)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def e2e_engine() -> AsyncGenerator[AsyncEngine, None]:
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


@pytest_asyncio.fixture
async def e2e_db_session(
    e2e_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    async_session_factory = async_sessionmaker(
        e2e_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as session:
        _e2e_session_holder["session"] = session
        yield session
        _e2e_session_holder["session"] = None


# ---------------------------------------------------------------------------
# Principal fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def principal_recruiter_tenant_a() -> Principal:
    return Principal(
        subject="recruiter-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write", "screening:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_reviewer_tenant_a() -> Principal:
    return Principal(
        subject="reviewer-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"reviewer"}),
        scopes=frozenset({"screening:read", "review:write", "review:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_auditor_tenant_a() -> Principal:
    return Principal(
        subject="auditor-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"auditor"}),
        scopes=frozenset({"screening:read", "review:read", "audit:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_privacy_officer_tenant_a() -> Principal:
    return Principal(
        subject="privacy-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"privacy_officer"}),
        scopes=frozenset({"screening:read", "screening:delete"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_compliance_officer_tenant_a() -> Principal:
    return Principal(
        subject="compliance-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"compliance_officer"}),
        scopes=frozenset({"review:appeal"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_tenant_b() -> Principal:
    return Principal(
        subject="recruiter-e2e-user-b",
        tenant_id="tenant-e2e-b",
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write", "screening:read"}),
        auth_method="oidc",
    )


# ---------------------------------------------------------------------------
# Client fixtures — each wraps PrincipalClient; no teardown override clearing
# ---------------------------------------------------------------------------


def _make_client(principal: Principal | None) -> PrincipalClient:
    return PrincipalClient(
        principal=principal,
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


@pytest_asyncio.fixture
async def recruiter_client_tenant_a(
    e2e_db_session: AsyncSession,
    principal_recruiter_tenant_a: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_recruiter_tenant_a) as client:
        yield client


@pytest_asyncio.fixture
async def reviewer_client_tenant_a(
    e2e_db_session: AsyncSession,
    principal_reviewer_tenant_a: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_reviewer_tenant_a) as client:
        yield client


@pytest_asyncio.fixture
async def auditor_client_tenant_a(
    e2e_db_session: AsyncSession,
    principal_auditor_tenant_a: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_auditor_tenant_a) as client:
        yield client


@pytest_asyncio.fixture
async def privacy_officer_client_tenant_a(
    e2e_db_session: AsyncSession,
    principal_privacy_officer_tenant_a: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_privacy_officer_tenant_a) as client:
        yield client


@pytest_asyncio.fixture
async def compliance_officer_client_tenant_a(
    e2e_db_session: AsyncSession,
    principal_compliance_officer_tenant_a: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_compliance_officer_tenant_a) as client:
        yield client


@pytest_asyncio.fixture
async def tenant_b_client(
    e2e_db_session: AsyncSession,
    principal_tenant_b: Principal,
) -> AsyncGenerator[PrincipalClient, None]:
    async with _make_client(principal_tenant_b) as client:
        yield client


@pytest_asyncio.fixture
async def unauthenticated_client(
    e2e_db_session: AsyncSession,
) -> AsyncGenerator[PrincipalClient, None]:
    # principal=None → _override_get_principal raises HTTP 401
    async with _make_client(None) as client:
        yield client


# ---------------------------------------------------------------------------
# Autouse rate-limit mock — avoids external Redis dependency in all e2e tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_rate_limit():
    """Patch the Redis-backed rate limiter to a no-op for all e2e tests."""
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        yield
