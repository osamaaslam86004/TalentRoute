from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.audit.integrity import GENESIS_HASH
from app.audit.service import record_audit_event, verify_audit_chain
from app.observability import metrics
from app.observability.metrics import (
    initialize_metrics,
    record_audit_head_lock_duration,
    shutdown_metrics,
)


def test_record_audit_head_lock_duration_noop_when_disabled():
    # When metrics are disabled or not initialized, calling this should be a safe no-op
    shutdown_metrics()
    record_audit_head_lock_duration(0.042, outcome="success")
    record_audit_head_lock_duration(0.015, outcome="failure")


def test_record_audit_head_lock_duration_records_histogram(make_settings):
    shutdown_metrics()
    settings = make_settings(
        otel_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318"
    )
    initialize_metrics(settings)
    try:
        assert metrics._audit_head_lock_duration is not None
        with patch.object(metrics._audit_head_lock_duration, "record") as mock_record:
            record_audit_head_lock_duration(0.125, outcome="success")
            mock_record.assert_called_once_with(0.125, {"outcome": "success"})

        with patch.object(metrics._audit_head_lock_duration, "record") as mock_record:
            record_audit_head_lock_duration(0.5, outcome="failure")
            mock_record.assert_called_once_with(0.5, {"outcome": "failure"})
    finally:
        shutdown_metrics()


@pytest.mark.asyncio
async def test_record_audit_event_records_lock_duration():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.add = MagicMock()
    mock_head = MagicMock(last_sequence=1, last_hash="abc" * 20)
    mock_session.scalar = AsyncMock(return_value=mock_head)

    with patch("app.audit.service.record_audit_head_lock_duration") as mock_metric:
        await record_audit_event(
            mock_session,
            principal=None,
            tenant_id="tenant-test",
            request_id="req-1",
            action="screening.completed",
            resource_type="screening",
            resource_id="123",
        )
        assert mock_metric.called
        args, kwargs = mock_metric.call_args
        assert args[0] >= 0.0
        assert kwargs.get("outcome") == "success"


@pytest.mark.asyncio
async def test_record_audit_event_records_failure_on_lock_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=RuntimeError("Lock timeout"))

    with patch("app.audit.service.record_audit_head_lock_duration") as mock_metric:
        with pytest.raises(RuntimeError, match="Lock timeout"):
            await record_audit_event(
                mock_session,
                principal=None,
                tenant_id="tenant-test",
                request_id="req-1",
                action="screening.completed",
                resource_type="screening",
                resource_id="123",
            )
        assert mock_metric.called
        args, kwargs = mock_metric.call_args
        assert args[0] >= 0.0
        assert kwargs.get("outcome") == "failure"


@pytest.mark.asyncio
async def test_verify_audit_chain_records_lock_duration():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_head = MagicMock(last_sequence=0, last_hash=GENESIS_HASH)
    mock_session.scalar = AsyncMock(return_value=mock_head)
    mock_session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )
    mock_session.begin_nested = MagicMock()
    mock_session.begin_nested.return_value.__aenter__ = AsyncMock()
    mock_session.begin_nested.return_value.__aexit__ = AsyncMock()

    with patch("app.audit.service.record_audit_head_lock_duration") as mock_metric:
        res = await verify_audit_chain(mock_session, tenant_id="tenant-test")
        assert res["valid"] is True
        assert mock_metric.called
        args, kwargs = mock_metric.call_args
        assert args[0] >= 0.0
        assert kwargs.get("outcome") == "success"
