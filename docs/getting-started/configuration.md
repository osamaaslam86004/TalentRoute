# Environment Setup & Configuration Reference

This reference documents all runtime settings, environment variables, validation rules, and security guards for the **LangGraph Candidate Screening Service** (`app.core.config.Settings`).

---

## Configuration Parameter Catalog

| Variable Name | Type | Default Value | Allowed Values / Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `APP_NAME` | `string` | `"LangGraph Candidate Screening API"` | Min length 1 | Application display name exposed in OpenAPI documentation. |
| `ENVIRONMENT` | `string` | `"development"` | `development`, `test`, `staging`, `production` | Deployment environment tier. Controls security enforcement, tracing ratios, and API docs visibility. |
| `LOG_LEVEL` | `string` | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | Logging severity threshold across system components. |
| `LOG_FORMAT` | `string` | `"console"` | `console`, `json` | Log output format. `json` emits one JSON object per line for structured log aggregators. |
| `SERVICE_NAME` | `string` | `"candidate-screening"` | Min length 1 | Service name reported in OpenTelemetry resource attributes. |
| `SERVICE_VERSION` | `string` | `None` | Version string | Installed application package version reported in telemetry. |
| `TELEMETRY_HASH_SALT` | `SecretStr` | `None` | Secret string | Keyed-hash salt for actor subject anonymization (`actor_hash`) in telemetry context. If unset, actor identity is omitted from spans/metrics. |
| `ALLOW_LANGSMITH_TRACING` | `boolean` | `False` | `True`, `False` | Explicit override to allow LangSmith prompt tracing in `staging` or `production`. Startup fails if LangSmith env vars are detected when this is `False`. |
| `OTEL_ENABLED` | `boolean` | `False` | `True`, `False` | Opt-in switch for OpenTelemetry metric and trace export to OTLP collector. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `string` | `"http://localhost:4318"` | Valid HTTP/HTTPS URL | Destination OTLP HTTP collector base endpoint. |
| `OTEL_TRACE_SAMPLE_RATIO` | `float` | `None` | `0.0` to `1.0` | Trace sampling ratio. `None` defaults to 100% in dev/test and 10% in staging/prod. |
| `CORS_ORIGINS` | `list[string]` | `[]` | CSV string or list | Allowed CORS origins for browser API access. |
| `AUTH_MODE` | `string` | `"auto"` | `auto`, `api_key`, `oidc` | Authentication strategy. `oidc` enforces Bearer tokens; `api_key` expects `X-API-Key`; `auto` supports both with dev fallback. |
| `API_KEY` | `SecretStr` | `None` | Secret string | Constant-time API key secret required when `AUTH_MODE=api_key` or in `auto` mode fallback. |
| `OIDC_ISSUER` | `string` | `None` | Valid URL | Expected OIDC token issuer (`iss` claim). |
| `OIDC_AUDIENCE` | `string` | `None` | String | Expected OIDC target audience (`aud` claim). |
| `OIDC_JWKS_URL` | `string` | `None` | Valid URL | Remote JWKS endpoint URL for fetching asymmetric signing keys (`RS256`, etc.). |
| `OIDC_ROLES_CLAIM` | `string` | `"roles"` | String | JWT claim key containing user role array or space-delimited string. |
| `OIDC_SCOPES_CLAIM` | `string` | `"scope"` | String | JWT claim key containing action scope array or space-delimited string (`scope` or `scp`). |
| `OIDC_TENANT_CLAIM` | `string` | `"tenant_id"` | String | JWT claim key containing multi-tenant identifier (`tenant_id`). |
| `OIDC_REQUIRE_TENANT_CLAIM` | `boolean` | `True` | `True`, `False` | When `True`, requests without a valid tenant claim receive `403 Forbidden`. |
| `OIDC_ALGORITHMS` | `list[string]` | `["RS256"]` | `RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512` | Permitted asymmetric JWT signing algorithms. |
| `DATABASE_URL` | `string` | `"postgresql+asyncpg://..."` | Async PostgreSQL URL | Connection URL for primary PostgreSQL database using `asyncpg` driver. |
| `REDIS_URL` | `string` | `"redis://localhost:6379/0"` | Redis URL | Redis database URL for submission rate limiting and readiness probes. |
| `CELERY_BROKER_URL` | `string` | `"redis://localhost:6379/1"` | Redis URL | Celery task message broker URL. |
| `CELERY_RESULT_BACKEND` | `string` | `"redis://localhost:6379/2"` | Redis URL | Celery task result storage backend URL. |
| `OPENAI_MODEL` | `string` | `"gpt-4o-mini"` | Model identifier string | OpenAI model name used for LangGraph assessment nodes. |
| `LLM_BASE_URL` | `string` | `None` | Valid URL | Optional base URL for OpenAI-compatible alternative endpoints. |
| `LLM_TIMEOUT_SECONDS` | `float` | `45.0` | `0.0 < x <= 180.0` | Maximum timeout in seconds for individual LLM provider API requests. |
| `MAX_APPLICATION_CHARS` | `integer` | `30000` | `100 <= x <= 100000` | Maximum allowed character length for candidate `application_text`. Requests exceeding this limit receive `413 Payload Too Large`. |
| `TASK_MAX_RETRIES` | `integer` | `2` | `0 <= x <= 5` | Maximum retry attempts for failed Celery worker tasks before failing permanently. |
| `SCREENING_RATE_LIMIT_REQUESTS` | `integer` | `30` | `1 <= x <= 10000` | Maximum allowed candidate submissions per rate-limit window per actor/tenant. |
| `SCREENING_RATE_LIMIT_WINDOW_SECONDS` | `integer` | `60` | `1 <= x <= 86400` | Time window in seconds for the submission rate limiter. |
| `RATE_LIMIT_FAIL_OPEN` | `boolean` | `False` | `True`, `False` | When `True`, rate limit checks pass if Redis is unreachable. When `False`, returns `503 Service Unavailable`. |
| `OUTBOX_BATCH_SIZE` | `integer` | `50` | `1 <= x <= 500` | Maximum outbox events claimed in a single Celery Beat dispatch cycle. |
| `OUTBOX_MAX_ATTEMPTS` | `integer` | `12` | `1 <= x <= 100` | Maximum dispatch attempts for an outbox event before moving it to `dead` status. |
| `OUTBOX_LEASE_SECONDS` | `integer` | `120` | `10 <= x <= 3600` | Lock lease duration in seconds for outbox dispatching records before treating them as stale. |
| `STALE_PROCESSING_SECONDS` | `integer` | `900` | `60 <= x <= 86400` | Duration in seconds after which a processing screening is eligible for reclamation. |
| `PII_REDACTION_ENABLED` | `boolean` | `True` | `True`, `False` | Global toggle for pre-provider PII minimization. |
| `PII_REDACTION_POLICY` | `string` | `"baseline"` | `baseline`, `strict` | PII minimization policy mode. `strict` adds unlabelled street address regex patterns. |
| `SCREENING_RUBRIC_VERSION` | `string` | `"screening-rubric-v1"` | Rubric identifier | Version string assigned to human reviewer feedback records. |

---

## Security & Policy Validation Rules

### 1. OIDC Configuration Validation
- If `AUTH_MODE=oidc`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL` are strictly required.
- In `production` environment, `OIDC_ISSUER` and `OIDC_JWKS_URL` **must** use the `https://` URL scheme.
- `OIDC_ALGORITHMS` is checked against allowed asymmetric algorithms (`RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512`). Symmetric algorithms (such as `HS256`) are forbidden for OIDC JWT tokens.

### 2. LangSmith Tracing Guard Policy
To prevent accidental leakage of applicant resume text to third-party tracing platforms, the application inspects process environment variables (`LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2`, etc.) on startup:
- In `staging` or `production` environments, if third-party tracing is detected and `ALLOW_LANGSMITH_TRACING` is `False`, startup **fails immediately** with a `TelemetryPolicyError`.
- In `development` or `test` environments, a warning log is recorded.

---

## Related Documents
- [Authentication & RBAC Reference](authentication.md)
- [Quickstart Guide](quickstart.md)
