import pytest

from app.observability import policy
from app.observability.policy import (
    ALLOWED_METRIC_LABELS,
    FORBIDDEN_METRIC_LABELS,
    TelemetryPolicyError,
    active_langsmith_flags,
    actor_hash,
    enforce_langsmith_policy,
    exception_type_name,
    filter_span_attributes,
    sanitize_metric_labels,
    stable_error_code,
)


# ---------------------------------------------------------------- metric labels
def test_allow_list_never_contains_a_forbidden_label():
    assert not ALLOWED_METRIC_LABELS & FORBIDDEN_METRIC_LABELS


def test_unknown_and_high_cardinality_labels_are_dropped():
    cleaned = sanitize_metric_labels(
        {
            "route": "/api/v1/screenings/{screening_id}",
            "tenant_id": "acme",
            "job_title": "x",
        }
    )
    assert cleaned == {"route": "/api/v1/screenings/{screening_id}"}


def test_strict_mode_raises_so_new_labels_need_review():
    with pytest.raises(ValueError, match="tenant_id"):
        sanitize_metric_labels({"tenant_id": "acme"}, strict=True)
    with pytest.raises(ValueError, match="brand_new"):
        sanitize_metric_labels({"brand_new": "x"}, strict=True)


def test_label_values_are_bounded_stringified_and_none_is_skipped():
    cleaned = sanitize_metric_labels(
        {"outcome": "x" * 500, "decision": True, "reason": None}
    )
    assert len(cleaned["outcome"]) == 64
    assert cleaned["decision"] == "true"
    assert "reason" not in cleaned


# ---------------------------------------------------------------- span attributes
@pytest.mark.parametrize(
    "key",
    [
        "http.url", "http.target", "url.full", "url.query", "db.statement", "db.query.text",
        "exception.message", "exception.stacktrace", "http.request.header.authorization",
        "http.request.body.size", "db.query.parameter.1", "messaging.message.body",
    ],
)  # fmt: skip
def test_content_bearing_span_attributes_are_denied(key):
    assert not policy.is_allowed_span_attribute(key)


def test_span_attributes_allow_listed_only_and_values_bounded():
    cleaned = filter_span_attributes(
        {
            "http.route": "/x/{id}",
            "http.status_code": 200,
            "app.stage": "redact",
            "db.statement": "INSERT INTO screenings VALUES (...)",
            "custom.thing": "nope",
            "app.long": "y" * 1000,
            "app.nan": float("nan"),
            "app.list": ["a", 1, None, object()],
            "app.obj": object(),
        }
    )
    assert cleaned["http.route"] == "/x/{id}"
    assert cleaned["http.status_code"] == 200
    assert len(cleaned["app.long"]) == 256
    assert cleaned["app.list"] == ["a", 1]
    assert not {"db.statement", "custom.thing", "app.nan", "app.obj"} & set(cleaned)


def test_span_strict_mode_raises():
    with pytest.raises(ValueError, match="db.statement"):
        filter_span_attributes({"db.statement": "SELECT 1"}, strict=True)


# ---------------------------------------------------------------- actor hashing
def test_actor_hash_is_none_without_a_salt(make_settings):
    assert actor_hash("alice@example.com", make_settings()) is None


def test_actor_hash_is_keyed_stable_short_and_does_not_contain_the_subject(
    make_settings,
):
    a = make_settings(telemetry_hash_salt="salt-one")
    b = make_settings(telemetry_hash_salt="salt-two")
    first = actor_hash("alice@example.com", a)
    assert first == actor_hash("alice@example.com", a)
    assert first != actor_hash("alice@example.com", b)
    assert first != actor_hash("bob@example.com", a)
    assert first is not None
    assert len(first) == 16 and "alice" not in first


def test_empty_salt_or_subject_yields_none(make_settings):
    assert actor_hash("alice", make_settings(telemetry_hash_salt="")) is None
    assert actor_hash("", make_settings(telemetry_hash_salt="s")) is None
    assert actor_hash(None, make_settings(telemetry_hash_salt="s")) is None


# ---------------------------------------------------------------- exception scrubbing
class ProviderError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def test_exception_type_name_excludes_the_message():
    assert exception_type_name(ValueError("jane@example.com")) == "ValueError"
    assert exception_type_name(ProviderError("secret")).endswith("ProviderError")
    assert "secret" not in exception_type_name(ProviderError("secret"))


@pytest.mark.parametrize(
    "code", ["rate_limit_exceeded", "context_length_exceeded", "invalid_api_key"]
)
def test_code_shaped_provider_codes_are_kept(code):
    assert stable_error_code(ProviderError("m", code=code)) == code


@pytest.mark.parametrize(
    "code",
    [
        "Your resume says jane@example.com",
        "Mixed_Case",
        429,
        None,
        "x" * 80,
        "has space",
    ],
)
def test_free_text_numbers_and_oversized_codes_are_rejected(code):
    assert stable_error_code(ProviderError("m", code=code)) is None


# ---------------------------------------------------------------- LangSmith guard
@pytest.mark.parametrize(
    "variable",
    [
        "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2",
        "LANGSMITH_OTEL_ENABLED", "LANGSMITH_OTEL_ONLY", "LANGCHAIN_TRACING",
    ],
)  # fmt: skip
@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " on "])
def test_tracing_flags_are_detected(variable, value):
    assert active_langsmith_flags({variable: value}) == [variable]


@pytest.mark.parametrize("mode", ["otel", "hybrid", "OTEL"])
def test_export_modes_are_detected_but_the_default_mode_is_not(mode):
    assert active_langsmith_flags({"LANGSMITH_TRACING_MODE": mode}) == [
        "LANGSMITH_TRACING_MODE"
    ]
    assert active_langsmith_flags({"LANGSMITH_TRACING_MODE": "langsmith"}) == []


@pytest.mark.parametrize("value", ["false", "0", "", "no", "off"])
def test_disabled_values_and_an_api_key_alone_are_not_flags(value):
    assert (
        active_langsmith_flags({"LANGSMITH_TRACING": value, "LANGSMITH_API_KEY": "k"})
        == []
    )


def test_guard_blocks_production_startup_and_names_variables_without_values(
    make_settings,
):
    env = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "ls-secret-key"}
    with pytest.raises(TelemetryPolicyError) as raised:
        enforce_langsmith_policy(make_settings(environment="production"), env)
    assert "LANGSMITH_TRACING" in str(raised.value)
    assert "ls-secret-key" not in str(raised.value)


def test_guard_blocks_staging_too(make_settings):
    with pytest.raises(TelemetryPolicyError):
        enforce_langsmith_policy(
            make_settings(environment="staging"), {"LANGSMITH_TRACING": "1"}
        )


def test_guard_allows_production_when_explicitly_permitted_but_still_warns(
    make_settings, caplog
):
    settings = make_settings(environment="production", allow_langsmith_tracing=True)
    with caplog.at_level("WARNING"):
        flags = enforce_langsmith_policy(settings, {"LANGSMITH_TRACING": "true"})
    assert flags == ["LANGSMITH_TRACING"]
    assert "allowed=True" in caplog.text


def test_guard_only_warns_in_development(make_settings, caplog):
    with caplog.at_level("WARNING"):
        flags = enforce_langsmith_policy(
            make_settings(), {"LANGCHAIN_TRACING_V2": "true"}
        )
    assert flags == ["LANGCHAIN_TRACING_V2"]
    assert "allowed=False" in caplog.text


def test_guard_is_silent_when_nothing_is_enabled(make_settings, caplog):
    with caplog.at_level("WARNING"):
        assert (
            enforce_langsmith_policy(make_settings(environment="production"), {}) == []
        )
    assert caplog.text == ""
