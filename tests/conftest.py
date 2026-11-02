import io
import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.db.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.observability.context import clear_context
from app.observability.logging import _HANDLER_MARK, configure_logging

_FRAMEWORK_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_ENV_VARS = (
    "ENVIRONMENT", "LOG_FORMAT", "LOG_LEVEL", "SERVICE_NAME", "SERVICE_VERSION",
    "TELEMETRY_HASH_SALT", "ALLOW_LANGSMITH_TRACING", "API_KEY", "AUTH_MODE",
    "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL",
    *(
        f"{prefix}{flag}"
        for prefix in ("LANGSMITH_", "LANGCHAIN_")
        for flag in ("TRACING", "TRACING_V2", "TRACING_MODE", "OTEL_ENABLED", "OTEL_ONLY")
    ),
)  # fmt: skip


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    clear_context()
    get_settings.cache_clear()
    yield
    clear_context()
    get_settings.cache_clear()


@pytest.fixture
def make_settings():
    def build(**overrides) -> Settings:
        values = dict(overrides)
        if values.get("environment") == "production":
            values.setdefault(
                "api_key", "test-key"
            )  # production requires some auth config
        return Settings(**values)

    return build


@pytest.fixture
def configure(make_settings):
    """Install our handler writing to a StringIO, and fully restore logging state afterwards."""
    root = logging.getLogger()
    saved_level = root.level
    saved_framework = {
        name: (logging.getLogger(name).handlers[:], logging.getLogger(name).propagate)
        for name in _FRAMEWORK_LOGGERS
    }

    def _configure(role: str = "api", **overrides) -> io.StringIO:
        stream = io.StringIO()
        configure_logging(make_settings(**overrides), role=role, stream=stream)
        return stream

    yield _configure
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
    root.setLevel(saved_level)
    for name, (handlers, propagate) in saved_framework.items():
        framework_logger = logging.getLogger(name)
        framework_logger.handlers[:] = handlers
        framework_logger.propagate = propagate


@pytest.fixture
def mock_jwk_key() -> MagicMock:
    key_mock = MagicMock()
    key_mock.key = "mock_public_key_pem"
    return key_mock


@pytest.fixture
def mock_jwks_client(mock_jwk_key: MagicMock) -> MagicMock:
    client_mock = MagicMock()
    client_mock.get_signing_key_from_jwt.return_value = mock_jwk_key
    return client_mock


@pytest.fixture
def sample_jwt_claims() -> dict[str, Any]:
    return {
        "sub": "user-456",
        "iss": "https://auth.example.com/",
        "aud": "candidate-screening-api",
        "exp": 4102444800,  # Far future
        "iat": 1600000000,
        "tenant_id": "tenant-alpha",
        "roles": ["recruiter"],
        "scope": "screening:write screening:read",
    }


"""Global pytest configuration and fixtures for async database testing."""


@pytest_asyncio.fixture(scope="function")
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an in-memory SQLite async engine for test database schema setup."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated AsyncSession for database repository tests."""
    session_factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
        await session.rollback()
