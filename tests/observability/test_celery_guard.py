import importlib
import sys

import pytest

from app.observability.policy import TelemetryPolicyError

MODULE = "app.workers.celery_app"


@pytest.fixture
def import_celery_app(monkeypatch):
    def _import():
        sys.modules.pop(MODULE, None)
        return importlib.import_module(MODULE)

    yield _import
    sys.modules.pop(MODULE, None)


def test_worker_and_beat_refuse_to_start_in_production_with_tracing_enabled(
    monkeypatch, import_celery_app
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(TelemetryPolicyError):
        import_celery_app()


def test_celery_app_imports_normally_without_the_flag(monkeypatch, import_celery_app):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "test-key")
    assert import_celery_app().celery_app.main == "candidate_screening"


def test_celery_app_imports_in_production_when_tracing_is_explicitly_allowed(
    monkeypatch, import_celery_app
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("ALLOW_LANGSMITH_TRACING", "true")
    assert import_celery_app().celery_app is not None
