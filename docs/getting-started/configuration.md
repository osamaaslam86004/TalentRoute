# Runtime Configuration Reference

This document provides a comprehensive reference for configuring the **LangGraph Candidate Screening Service**. Configuration is managed using Pydantic Settings (`app.core.config.Settings`), reading runtime settings from environment variables or a local `.env` file.

!!! note "Secrets Management"
    In production deployments, sensitive configuration parameters (such as `API_KEY` and `OPENAI_API_KEY`) should be injected securely via external secret providers or environment variables.

---

## Environment Variables Reference

| Variable Name | Type | Default Value | Description & Validation Rules |
| :--- | :--- | :--- | :--- |
| `APP_NAME` | `str` | `"LangGraph Candidate Screening API"` | Service application name. |
| `ENVIRONMENT` | `enum` | `"development"` | Environment phase: `development`, `test`, `staging`, `production`. Strict checks enforced in production. |
| `LOG_LEVEL` | `str` | `"INFO"` | Standard logging verbosity level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `LOG_FORMAT` | `enum` | `"console"` | Options: `console` (human-readable) or `json` (single structured JSON object per line). |
| `SERVICE_NAME` | `str` | `"candidate-screening"` | OpenTelemetry resource service name. |
| `SERVICE_VERSION` | `str | None` | `None` | Service version identifier; defaults to installed package version if omitted. |
| `TELEMETRY_HASH_SALT` | `SecretStr | None` | `None` | Keyed HMAC salt for masking actor subjects in telemetry. Unset disables actor subject emission. |
| `ALLOW_LANGSMITH_TRACING` | `bool` | `False` | Opt-in flag for third-party LangSmith tracing. Startup fails in staging/prod if enabled without explicit override. |
| `OTEL_ENABLED` | `bool` | `False` | Opt-in flag to enable OpenTelemetry trace and metric exporters. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `str` | `"http://localhost:4318"` | Target OTLP collector gRPC/HTTP endpoint. |
| `OTEL_TRACE_SAMPLE_RATIO` | `float | None` | `None` | Trace sampling ratio (`0.0` to `1.0`). Defaults to `1.0` in dev/test, `0.1` in staging/prod. |
| `CORS_ORIGINS` | `list[str]` | `[]` | Comma-separated list of allowed CORS origins. Parsed into Python lists. |
| `AUTH_MODE` | `enum` | `"auto"` | Mode: `auto`, `oidc`, `api_key`. Controls authentication requirement. |
| `API_KEY` | `SecretStr | None` | `None` | Shared key for API key authentication (`X-API-Key` header). Required when `AUTH_MODE=api_key`. |
| `OIDC_ISSUER` | `str | None` | `None` | OpenID Connect issuer URL. |
| `OIDC_AUDIENCE` | `str | None` | `None` | Expected OIDC token audience claim (`aud`). |
| `OIDC_JWKS_URL` | `str | None` | `None` | Remote JWKS URI for fetching signing public keys. |
| `OIDC_ROLES_CLAIM` | `str` | `"roles"` | JWT claim key containing principal roles. |
| `OIDC_SCOPES_CLAIM` | `str` | `"scope"` | JWT claim key containing permission scopes. |
| `OIDC_TENANT_CLAIM` | `str` | `"tenant_id"` | JWT claim key indicating tenant context. |
| `OIDC_REQUIRE_TENANT_CLAIM` | `bool` | `True` | Requires valid `tenant_id` claim in JWT if `True`. |
| `OIDC_ALGORITHMS` | `list[str]` | `["RS256"]` | Allowed asymmetric JWT signing algorithms (e.g., `RS256,ES256`). |
| `DATABASE_URL` | `str` | `postgresql+asyncpg://...` | PostgreSQL connection string using `asyncpg` async driver. |
| `REDIS_URL` | `str` | `"redis://localhost:6379/0"` | Redis URI for rate limiting and cache storage (DB 0). |
| `CELERY_BROKER_URL` | `str` | `"redis://localhost:6379/1"` | Redis URI for Celery message broker (DB 1). |
| `CELERY_RESULT_BACKEND` | `str` | `"redis://localhost:6379/2"` | Redis URI for Celery task result backend (DB 2). |
| `OPENAI_API_KEY` | `SecretStr | None` | `None` | OpenAI API provider authentication key. |
| `OPENAI_MODEL` | `str` | `"gpt-4o-mini"` | Pinned LLM model ID for screening pipelines. |
| `LLM_BASE_URL` | `str | None` | `None` | Optional base URL for custom OpenAI-compatible endpoints. |
| `LLM_TIMEOUT_SECONDS` | `float` | `45.0` | Timeout threshold in seconds for LLM invocations (1 to 180s). |
| `MAX_APPLICATION_CHARS` | `int` | `30000` | Maximum character length allowed for candidate application input (100 to 100,000). |
| `TASK_MAX_RETRIES` | `int` | `2` | Task retry cap for Celery background workers. |
| `SCREENING_RATE_LIMIT_REQUESTS` | `int` | `30` | Maximum submissions allowed per rate-limit window (1 to 10,000). |
| `SCREENING_RATE_LIMIT_WINDOW_SECONDS` | `int` | `60` | Duration of rate-limit window in seconds (1 to 86,400s). |
| `RATE_LIMIT_FAIL_OPEN` | `bool` | `False` | If `True`, allows API requests when Redis rate-limiter is down. |
| `OUTBOX_BATCH_SIZE` | `int` | `50` | Maximum outbox events processed per dispatch pass (1 to 500). |
| `OUTBOX_MAX_ATTEMPTS` | `int` | `12` | Attempt threshold before moving outbox event to `dead` state (1 to 100). |
| `OUTBOX_LEASE_SECONDS` | `int` | `120` | Duration of lock lease on claimed outbox events (10 to 3,600s). |
| `STALE_PROCESSING_SECONDS` | `int` | `900` | Inactivity threshold to flag stuck screening jobs (60 to 86,400s). |
| `PII_REDACTION_ENABLED` | `bool` | `True` | Toggles pre-provider PII minimization regex engine. |
| `PII_REDACTION_POLICY` | `enum` | `"baseline"` | Options: `baseline` (structured fields) or `strict` (includes street address heuristics). |
| `SCREENING_RUBRIC_VERSION` | `str` | `"screening-rubric-v1"` | Rubric version string tracked across reviewer feedback records. |

---

## Validation & Business Rules

The `Settings` model applies strict Pydantic field and model validators (`app.core.config`):

1. **OIDC Parameter Co-dependency:** `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL` must all be specified together.
2. **HTTPS Enforcement:** In `production` environment, `OIDC_ISSUER` and `OIDC_JWKS_URL` must use secure `https://` schemes.
3. **Algorithm Security Check:** `OIDC_ALGORITHMS` accepts only valid asymmetric signing algorithms: `RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512`.
4. **Production Authentication Check:** In `production`, `AUTH_MODE=oidc` requires complete OIDC configuration, while `AUTH_MODE=auto` requires valid OIDC settings or an `API_KEY`.
5. **CSV List Parsing:** `CORS_ORIGINS` and `OIDC_ALGORITHMS` support comma-separated string formatting in `.env` files.

---

## Connection URL Formats

=== "PostgreSQL Connection URL"
    ```ini
    DATABASE_URL=postgresql+asyncpg://user:password@hostname:5432/dbname
    ```

=== "Redis Multi-DB Layout"
    ```ini
    # Database 0: Cache & Rate Limiter
    REDIS_URL=redis://hostname:6379/0

    # Database 1: Celery Broker
    CELERY_BROKER_URL=redis://hostname:6379/1

    # Database 2: Celery Results
    CELERY_RESULT_BACKEND=redis://hostname:6379/2
    ```

---

## Related Documents
- [Authentication Reference](authentication.md)
- [Architecture Overview](../architecture/overview.md)
