import json
import logging
import re

import pytest
from opentelemetry.sdk.trace import TracerProvider

from app.observability.context import bind_context
from app.observability.logging import _HANDLER_MARK, CONSOLE_FORMAT


def records(stream) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ---------------------------------------------------------------- JSON format
def test_json_line_carries_service_metadata_and_omits_unbound_context(configure):
    stream = configure(log_format="json", service_name="svc", service_version="9.9.9")
    logging.getLogger("demo").info("hello %s", "world")

    (line,) = records(stream)
    assert line["message"] == "hello world"
    assert line["level"] == "INFO" and line["logger"] == "demo"
    assert line["service"] == "svc" and line["version"] == "9.9.9"
    assert line["environment"] == "development" and line["role"] == "api"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["timestamp"])
    for absent in (
        "request_id",
        "tenant_id",
        "screening_id",
        "task_id",
        "actor_hash",
        "trace_id",
    ):
        assert absent not in line


def test_json_line_includes_bound_context_and_role(configure):
    stream = configure(role="worker", log_format="json")
    bind_context(request_id="req-1", tenant_id="acme", screening_id="s-1")
    logging.getLogger("demo").info("working")

    (line,) = records(stream)
    assert (line["request_id"], line["tenant_id"], line["screening_id"]) == (
        "req-1",
        "acme",
        "s-1",
    )
    assert line["role"] == "worker"


def test_only_allow_listed_extras_are_emitted(configure):
    stream = configure(log_format="json")
    logging.getLogger("demo").info(
        "done",
        extra={
            "duration_ms": 12.5,
            "event": "x.done",
            "application_text": "SENSITIVE-RESUME",
        },
    )

    assert "SENSITIVE-RESUME" not in stream.getvalue()
    (line,) = records(stream)
    assert line["duration_ms"] == 12.5 and line["event"] == "x.done"
    assert "application_text" not in line


def test_extra_cannot_spoof_correlation_fields(configure):
    stream = configure(log_format="json")
    bind_context(tenant_id="real-tenant")
    logging.getLogger("demo").info(
        "x", extra={"tenant_id": "evil", "request_id": "forged"}
    )

    (line,) = records(stream)
    assert line["tenant_id"] == "real-tenant"
    assert "request_id" not in line


def test_newlines_in_a_message_cannot_forge_a_second_record(configure):
    stream = configure(log_format="json")
    logging.getLogger("demo").info(
        "first\n" + '{"level":"CRITICAL","message":"forged"}'
    )
    assert len(records(stream)) == 1


# ---------------------------------------------------------------- exception scrubbing
class ProviderError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _log_failure(exc: Exception) -> None:
    try:
        raise exc
    except Exception:
        logging.getLogger("demo").error("processing failed", exc_info=True)


def test_production_json_replaces_exception_text_with_type_code_and_frames(configure):
    stream = configure(log_format="json", environment="production")
    _log_failure(
        ProviderError("echoed jane@example.com resume text", code="rate_limit_exceeded")
    )

    raw = stream.getvalue()
    assert "jane@example.com" not in raw and "resume text" not in raw
    (line,) = records(stream)
    assert line["exc_type"].endswith("ProviderError")
    assert line["exc_code"] == "rate_limit_exceeded"
    assert any(frame.endswith(":_log_failure") for frame in line["exc_frames"])
    assert "exception" not in line


def test_scrubbed_output_names_an_explicit_cause_type_but_not_its_message(configure):
    stream = configure(log_format="json", environment="staging")
    try:
        try:
            raise ValueError("inner SECRET-TOKEN")
        except ValueError as inner:
            raise RuntimeError("outer SECRET-TOKEN") from inner
    except RuntimeError:
        logging.getLogger("demo").error("failed", exc_info=True)

    assert "SECRET-TOKEN" not in stream.getvalue()
    (line,) = records(stream)
    assert line["exc_type"] == "RuntimeError" and line["exc_cause_type"] == "ValueError"


def test_development_json_keeps_the_full_traceback(configure):
    stream = configure(log_format="json", environment="development")
    _log_failure(ValueError("visible while developing"))

    (line,) = records(stream)
    assert "visible while developing" in line["exception"]
    assert "exc_type" not in line


# ---------------------------------------------------------------- console format
def test_console_output_is_identical_to_the_legacy_format_when_nothing_is_bound(
    configure,
):
    stream = configure()
    logging.getLogger("demo").info("hello")

    assert CONSOLE_FORMAT == "%(asctime)s %(levelname)s %(name)s %(message)s"
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO demo hello\n", stream.getvalue()
    )


def test_console_appends_context_on_the_message_line_before_any_traceback(configure):
    stream = configure()
    bind_context(request_id="req-1", screening_id="s-1")
    _log_failure(ValueError("boom"))

    first, *rest = stream.getvalue().splitlines()
    assert first.endswith("processing failed [request_id=req-1 screening_id=s-1]")
    assert any("Traceback" in line for line in rest)


def test_console_in_production_warns_that_exceptions_are_not_scrubbed(configure):
    stream = configure(environment="production")
    assert "LOG_FORMAT=console in production" in stream.getvalue()


# ---------------------------------------------------------------- configuration behaviour
def test_configure_is_idempotent_and_leaves_foreign_handlers_alone(configure):
    foreign = logging.NullHandler()
    root = logging.getLogger()
    root.addHandler(foreign)
    try:
        configure()
        configure()
        ours = [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_api_role_routes_uvicorn_loggers_through_root(configure):
    uvicorn_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger.addHandler(logging.NullHandler())
    uvicorn_logger.propagate = False

    stream = configure(role="api", log_format="json")
    uvicorn_logger.warning("lifecycle")

    assert uvicorn_logger.handlers == [] and uvicorn_logger.propagate is True
    (line,) = records(stream)
    assert line["logger"] == "uvicorn.error" and line["message"] == "lifecycle"


def test_console_mode_leaves_uvicorn_output_exactly_as_it_was(configure):
    uvicorn_logger = logging.getLogger("uvicorn.error")
    marker = logging.NullHandler()
    uvicorn_logger.addHandler(marker)
    uvicorn_logger.propagate = False

    configure(role="api", log_format="console")
    assert uvicorn_logger.handlers == [marker] and uvicorn_logger.propagate is False


def test_non_api_roles_do_not_touch_uvicorn_loggers(configure):
    uvicorn_logger = logging.getLogger("uvicorn.error")
    marker = logging.NullHandler()
    uvicorn_logger.addHandler(marker)
    uvicorn_logger.propagate = False

    configure(role="worker", log_format="json")
    assert uvicorn_logger.handlers == [marker] and uvicorn_logger.propagate is False


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("warning", logging.WARNING), ("DEBUG", logging.DEBUG), ("bogus", logging.INFO)],
)
def test_log_level_is_honoured_with_a_safe_fallback(configure, configured, expected):
    configure(log_level=configured)
    assert logging.getLogger().level == expected


# ---------------------------------------------------------------- trace correlation
def test_trace_and_span_ids_are_added_inside_an_active_span(configure):
    stream = configure(log_format="json")
    tracer = TracerProvider().get_tracer("test")  # deliberately not installed globally

    with tracer.start_as_current_span("work") as span:
        logging.getLogger("demo").info("inside")
    logging.getLogger("demo").info("outside")

    inside, outside = records(stream)
    context = span.get_span_context()
    assert inside["trace_id"] == format(context.trace_id, "032x")
    assert inside["span_id"] == format(context.span_id, "016x")
    assert "trace_id" not in outside
