"""Privacy and cardinality policy for everything this service emits as telemetry.

This service processes job applications, so the default stance is *allow-list, never deny-list*:
nothing reaches a metric label, span attribute or structured-log field unless it is named here.

Telemetry must never break the request path. The ``sanitize_*`` / ``filter_*`` helpers therefore
drop what they do not recognise and only raise when called with ``strict=True`` (used by tests, so
a new label has to be added here deliberately and shows up in code review).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import re
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class TelemetryPolicyError(RuntimeError):
    """Raised at startup when the runtime configuration would violate the telemetry policy."""


# --------------------------------------------------------------------------------------------
# Metric labels: bounded cardinality only. Every addition needs a reviewer to confirm the value
# set is small and fixed (a template, an enum, an exception class), never user- or tenant-derived.
# --------------------------------------------------------------------------------------------
ALLOWED_METRIC_LABELS: frozenset[str] = frozenset(
    {
        "route",  # FastAPI route *template*, never the raw path
        "method",
        "status_class",  # 2xx / 4xx / 5xx
        "event_type",
        "outcome",
        "stage",
        "model",
        "operation",
        "error_type",  # exception class name, never its message
        "redaction_category",
        "reason",  # fixed vocabularies, e.g. auth failure reason
        "decision",  # fixed vocabularies, e.g. rate-limit decision
        "status",  # fixed vocabularies, e.g. outbox row status
        "queue",
    }
)

# Never valid as metric labels, even if someone adds them to the allow-list by mistake
# (see test_policy). They belong on logs/spans, where cardinality is not a cost problem.
FORBIDDEN_METRIC_LABELS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "actor",
        "actor_hash",
        "subject",
        "screening_id",
        "request_id",
        "task_id",
        "job_title",
        "required_skills",
        "path",
        "url",
        "application_text",
    }
)

_MAX_LABEL_VALUE = 64
_MAX_ATTRIBUTE_VALUE = 256
_MAX_SEQUENCE_ITEMS = 20

# --------------------------------------------------------------------------------------------
# Structured-log extras: ``logger.info("...", extra={...})`` fields that may be emitted.
# Anything else passed via ``extra`` is dropped by the JSON formatter.
# --------------------------------------------------------------------------------------------
ALLOWED_LOG_EXTRA_FIELDS: frozenset[str] = frozenset(
    {
        "event",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "outcome",
        "stage",
        "error_type",
        "error_code",
        "count",
        "attempt",
    }
)

# --------------------------------------------------------------------------------------------
# Span attributes. Exact keys plus namespaced prefixes, with an explicit deny list that wins.
# Raw URLs, headers, SQL text and exception messages are the usual ways content leaks into traces.
# --------------------------------------------------------------------------------------------
ALLOWED_SPAN_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "http.scheme",
        "http.flavor",
        "db.system",
        "db.system.name",
        "db.operation",
        "db.operation.name",
        "db.name",
        "db.namespace",
        "db.sql.table",
        "db.collection.name",
        "error.type",
        "exception.type",
    }
)
ALLOWED_SPAN_ATTRIBUTE_PREFIXES: tuple[str, ...] = (
    "app.",
    "messaging.",
    "celery.",
    "net.",
    "network.",
    "server.",
    "code.",
    "thread.",
)
DENIED_SPAN_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "http.url",
        "http.target",
        "url.full",
        "url.query",
        "db.statement",
        "db.query.text",
        "exception.message",
        "exception.stacktrace",
        "messaging.message.body",
        "messaging.message.payload",
    }
)
DENIED_SPAN_ATTRIBUTE_PREFIXES: tuple[str, ...] = (
    "http.request.header.",
    "http.response.header.",
    "http.request.body",
    "db.query.parameter.",
    "db.statement.",
)


def _bool_or_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sanitize_metric_labels(labels: Mapping[str, Any], *, strict: bool = False) -> dict[str, str]:
    """Keep only allow-listed labels and bound their values.

    With ``strict=True`` an unknown or forbidden label raises ``ValueError``; otherwise it is dropped.
    """

    result: dict[str, str] = {}
    for key, value in labels.items():
        if key in FORBIDDEN_METRIC_LABELS or key not in ALLOWED_METRIC_LABELS:
            if strict:
                raise ValueError(f"metric label {key!r} is not allowed")
            continue
        if value is None:
            continue
        result[key] = _bool_or_str(value)[:_MAX_LABEL_VALUE]
    return result


def is_allowed_span_attribute(key: str) -> bool:
    if key in DENIED_SPAN_ATTRIBUTE_KEYS or key.startswith(DENIED_SPAN_ATTRIBUTE_PREFIXES):
        return False
    return key in ALLOWED_SPAN_ATTRIBUTE_KEYS or key.startswith(ALLOWED_SPAN_ATTRIBUTE_PREFIXES)


def _scalar(value: Any) -> str | bool | int | float | None:
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:_MAX_ATTRIBUTE_VALUE]
    return None


def filter_span_attributes(
    attributes: Mapping[str, Any], *, strict: bool = False
) -> dict[str, Any]:
    """Keep allow-listed span attributes with OTel-valid, bounded values."""

    result: dict[str, Any] = {}
    for key, value in attributes.items():
        if not is_allowed_span_attribute(key):
            if strict:
                raise ValueError(f"span attribute {key!r} is not allowed")
            continue
        if isinstance(value, list | tuple):
            items = [_scalar(item) for item in value[:_MAX_SEQUENCE_ITEMS]]
            cleaned = [item for item in items if item is not None]
            if cleaned:
                result[key] = cleaned
            continue
        scalar = _scalar(value)
        if scalar is not None:
            result[key] = scalar
    return result


# --------------------------------------------------------------------------------------------
# Identity and errors
# --------------------------------------------------------------------------------------------
def actor_hash(subject: str | None, settings: Settings | None = None) -> str | None:
    """Keyed, truncated hash of an actor identifier for correlation on logs and spans.

    Returns ``None`` when no salt is configured: an unsalted hash of an email-like subject is
    trivially reversible by dictionary, so the safe default is to emit no actor identity at all.
    """

    secret = (settings or get_settings()).telemetry_hash_salt
    salt = secret.get_secret_value() if secret else ""
    if not subject or not salt:
        return None
    return hmac.new(salt.encode("utf-8"), subject.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def exception_type_name(exc: BaseException | type[BaseException]) -> str:
    """Class name, module-qualified outside builtins. Never includes the exception message."""

    cls = exc if isinstance(exc, type) else type(exc)
    name = cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"
    return name[:120]


def stable_error_code(exc: BaseException) -> str | None:
    """A provider's machine-readable code (e.g. ``rate_limit_exceeded``), if it is code-shaped.

    Only short lowercase tokens are accepted; free text, numbers and mixed-case strings are ignored
    so an exception cannot smuggle message content through this field.
    """

    for attribute in ("error_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
            return value
    return None


# --------------------------------------------------------------------------------------------
# LangSmith / LangChain tracing guard
# --------------------------------------------------------------------------------------------
# langchain-openai depends on langsmith. Setting these variables makes LangChain ship full prompts
# (the application text) and responses to a third party, and the OTel modes route them into whatever
# tracer provider is installed. Names and semantics were checked against the langsmith client source
# (``get_env_var`` resolves each name under both prefixes). langsmith itself only treats ``true`` and
# ``1`` as on; this guard deliberately also matches ``yes``/``on``, because a false positive costs a
# config tweak while a false negative leaks applicant data. These variables are not part of this
# service's settings, so they are read from the process environment, which is what langsmith consults.
_LANGSMITH_PREFIXES = ("LANGSMITH_", "LANGCHAIN_")
_LANGSMITH_BOOLEAN_FLAGS = ("TRACING", "TRACING_V2", "OTEL_ENABLED", "OTEL_ONLY")
_LANGSMITH_EXPORT_MODES = frozenset({"otel", "hybrid"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def active_langsmith_flags(env: Mapping[str, str] | None = None) -> list[str]:
    """Names (never values) of environment variables that enable LangSmith/LangChain tracing."""

    env = os.environ if env is None else env
    active: list[str] = []
    for prefix in _LANGSMITH_PREFIXES:
        for flag in _LANGSMITH_BOOLEAN_FLAGS:
            name = prefix + flag
            if env.get(name, "").strip().lower() in _TRUTHY:
                active.append(name)
        mode_name = prefix + "TRACING_MODE"
        if env.get(mode_name, "").strip().lower() in _LANGSMITH_EXPORT_MODES:
            active.append(mode_name)
    return active


def enforce_langsmith_policy(
    settings: Settings | None = None, env: Mapping[str, str] | None = None
) -> list[str]:
    """Refuse to start in staging/production with third-party tracing on, unless explicitly allowed.

    Elsewhere it logs a warning. Returns the active variable names.
    """

    settings = settings or get_settings()
    flags = active_langsmith_flags(env)
    if not flags:
        return []

    names = ", ".join(flags)
    guarded = settings.environment in {"staging", "production"}
    if guarded and not settings.allow_langsmith_tracing:
        raise TelemetryPolicyError(
            f"LangChain/LangSmith tracing is enabled ({names}). It sends prompts containing "
            "applicant data to a third party. Unset it, or set ALLOW_LANGSMITH_TRACING=true after "
            "a data-processing review."
        )

    logger.warning(
        "LangChain/LangSmith tracing is enabled (%s); prompts and applicant text leave this "
        "system. allowed=%s",
        names,
        settings.allow_langsmith_tracing,
    )
    return flags
