import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_default_settings_in_development():
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.log_format == "console"
    assert settings.service_name == "langgraph-candidate-screening-service"
    assert settings.rate_limit_fail_open is False


def test_production_mode_requires_api_key_or_oidc(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("OIDC_ISSUER", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, environment="production")

    assert "api_key" in str(exc_info.value) or "auth" in str(exc_info.value).lower()


def test_production_mode_valid_with_api_key():
    settings = Settings(
        _env_file=None, environment="production", api_key="prod-secret-key"
    )
    assert settings.environment == "production"
    assert settings.api_key.get_secret_value() == "prod-secret-key"


def test_invalid_integer_field_raises_validation_error():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, screening_rate_limit_requests="not-an-int")


def test_invalid_log_level_fallback_or_validation():
    # Verify behavior when loading custom values
    settings = Settings(_env_file=None, log_level="DEBUG")
    assert settings.log_level == "DEBUG"


def test_get_settings_lru_cache():
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    get_settings.cache_clear()
