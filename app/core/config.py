from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets must be injected by the deployment environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LangGraph Candidate Screening API"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    # `console` keeps the historical human-readable format; `json` emits one object per line.
    log_format: Literal["console", "json"] = "console"
    service_name: str = "candidate-screening"
    service_version: str | None = None  # defaults to the installed package version
    # Keyed-hash salt for actor identifiers in telemetry. Unset means actor identity is never emitted.
    telemetry_hash_salt: SecretStr | None = None
    # LangSmith/LangChain tracing exports prompts that contain applicant data to a third party.
    # Startup fails in staging/production while it is enabled, unless this is set deliberately.
    allow_langsmith_tracing: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # `auto` supports OIDC bearer tokens and a constant-time API-key fallback. Prefer OIDC in prod.
    auth_mode: Literal["auto", "api_key", "oidc"] = "auto"
    api_key: SecretStr | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "roles"
    oidc_scopes_claim: str = "scope"
    oidc_tenant_claim: str = "tenant_id"
    oidc_require_tenant_claim: bool = True
    oidc_algorithms: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["RS256"])

    database_url: str = "postgresql+asyncpg://screening:screening_dev_password@localhost:5432/screening"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=45.0, gt=0, le=180)

    max_application_chars: int = Field(default=30000, ge=100, le=100000)
    task_max_retries: int = Field(default=2, ge=0, le=5)
    screening_rate_limit_requests: int = Field(default=30, ge=1, le=10000)
    screening_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86400)
    rate_limit_fail_open: bool = False
    outbox_batch_size: int = Field(default=50, ge=1, le=500)
    outbox_max_attempts: int = Field(default=12, ge=1, le=100)
    outbox_lease_seconds: int = Field(default=120, ge=10, le=3600)
    stale_processing_seconds: int = Field(default=900, ge=60, le=86400)
    pii_redaction_enabled: bool = True
    pii_redaction_policy: Literal["baseline", "strict"] = "baseline"
    screening_rubric_version: str = "screening-rubric-v1"

    @field_validator("cors_origins", "oidc_algorithms", mode="before")
    @classmethod
    def parse_csv_lists(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("oidc_issuer", "oidc_audience", "oidc_jwks_url", mode="before")
    @classmethod
    def normalize_optional_urls(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def validate_security_configuration(self):
        if self.auth_mode == "oidc" and not all(
            (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        ):
            raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required for AUTH_MODE=oidc")

        if self.oidc_issuer or self.oidc_audience or self.oidc_jwks_url:
            if not all((self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)):
                raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL must be configured together")
            for name in ("oidc_issuer", "oidc_jwks_url"):
                parsed = urlparse(getattr(self, name) or "")
                if parsed.scheme not in ({"https"} if self.environment == "production" else {"http", "https"}):
                    raise ValueError(f"{name.upper()} must use HTTPS in production")

        allowed_algorithms = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if not self.oidc_algorithms or any(item not in allowed_algorithms for item in self.oidc_algorithms):
            raise ValueError("OIDC_ALGORITHMS must contain only supported asymmetric signing algorithms")

        if self.auth_mode == "api_key" and not self.api_key:
            raise ValueError("API_KEY is required when AUTH_MODE=api_key")
        if self.environment == "production":
            oidc_ready = bool(self.oidc_issuer and self.oidc_audience and self.oidc_jwks_url)
            if self.auth_mode == "oidc" and not oidc_ready:
                raise ValueError("Complete OIDC configuration is required in production")
            if self.auth_mode == "auto" and not (self.api_key or oidc_ready):
                raise ValueError("Configure OIDC or API_KEY for production authentication")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
