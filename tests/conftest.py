import io
import logging

import pytest

from app.core.config import Settings, get_settings
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
