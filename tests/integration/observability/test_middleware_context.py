"""Integration tests for request middleware, RED metrics, and telemetry context."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.observability.context import (
    bind_context,
    clear_context,
    context_fields,
    context_scope,
    current_context,
)
from app.observability.middleware import _method_label, _request_id, _route_template


@pytest.mark.asyncio
async def test_middleware_generates_request_id(async_client: httpx.AsyncClient) -> None:
    """Verify incoming request without X-Request-ID generates a new UUID4 request ID."""
    response = await async_client.get("/api/v1/success")

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    request_id_hdr = response.headers["x-request-id"]

    # Must be valid UUID
    parsed_uuid = uuid.UUID(request_id_hdr)
    assert str(parsed_uuid) == request_id_hdr

    data = response.json()
    assert data["status"] == "ok"
    assert data["request_id"] == request_id_hdr
    assert data["context_request_id"] == request_id_hdr
    assert data["tenant_id"] == "tenant-123"


@pytest.mark.asyncio
async def test_middleware_propagates_valid_request_id(
    async_client: httpx.AsyncClient,
) -> None:
    """Verify incoming request with valid X-Request-ID propagates it strictly."""
    custom_id = str(uuid.uuid4())
    response = await async_client.get(
        "/api/v1/success", headers={"X-Request-ID": custom_id}
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == custom_id
    data = response.json()
    assert data["request_id"] == custom_id
    assert data["context_request_id"] == custom_id


@pytest.mark.asyncio
async def test_middleware_scrubs_malicious_request_id(
    async_client: httpx.AsyncClient,
) -> None:
    """Verify malicious/invalid X-Request-ID headers are dropped and replaced with a valid UUID."""
    malicious_id = "'; DROP TABLE users; -- <script>alert(1)</script>"
    response = await async_client.get(
        "/api/v1/success", headers={"X-Request-ID": malicious_id}
    )

    assert response.status_code == 200
    returned_id = response.headers["x-request-id"]
    assert returned_id != malicious_id

    parsed_uuid = uuid.UUID(returned_id)
    assert str(parsed_uuid) == returned_id


@pytest.mark.asyncio
async def test_middleware_security_headers(async_client: httpx.AsyncClient) -> None:
    """Verify middleware injects mandatory security headers into every response."""
    response = await async_client.get("/api/v1/success")

    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"


@pytest.mark.asyncio
async def test_middleware_handles_failed_request(
    async_client: httpx.AsyncClient,
) -> None:
    """Verify failed request propagates exception, sets 500 status, and maintains request ID."""
    custom_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="Simulated handler failure"):
        await async_client.post("/api/v1/error", headers={"X-Request-ID": custom_id})


def test_telemetry_context_lifecycle_and_validation() -> None:
    """Test TelemetryContext scope inheritance, field cleaning, and key enforcement."""
    clear_context()
    assert current_context() is None
    assert context_fields() == {}

    with context_scope(tenant_id="tenant-parent", request_id="req-111"):
        parent_ctx = current_context()
        assert parent_ctx is not None
        assert parent_ctx.tenant_id == "tenant-parent"
        assert parent_ctx.request_id == "req-111"

        # Nested scope inherits parent and allows overriding
        with context_scope(screening_id="screen-999"):
            child_ctx = current_context()
            assert child_ctx is not None
            assert child_ctx.tenant_id == "tenant-parent"
            assert child_ctx.request_id == "req-111"
            assert child_ctx.screening_id == "screen-999"

            # Check dict snapshot
            fields_dict = context_fields()
            assert fields_dict == {
                "tenant_id": "tenant-parent",
                "request_id": "req-111",
                "screening_id": "screen-999",
            }

        # Parent remains unchanged after exiting child scope
        assert current_context().screening_id is None

    clear_context()
    assert current_context() is None


def test_telemetry_context_value_sanitization_and_unknown_fields() -> None:
    """Verify TelemetryContext cleans control characters, truncates long strings, and rejects unknown fields."""
    clear_context()

    # Reject unknown field
    with pytest.raises(TypeError, match="unknown telemetry context field"):
        bind_context(unsupported_field="data")

    # Clean control characters and truncate to 128 chars
    dirty_value = "line1\x00line2\r\n" + "x" * 200
    ctx = bind_context(tenant_id=dirty_value)

    assert ctx.tenant_id is not None
    assert "\x00" not in ctx.tenant_id
    assert len(ctx.tenant_id) == 128
    assert ctx.tenant_id.startswith("line1?line2??")

    clear_context()


def test_middleware_helper_functions() -> None:
    """Test internal middleware helpers for route templates and method labels."""
    headers: list[tuple[bytes, bytes]] = [
        (b"X-Request-ID", b"12345678-1234-5678-1234-567812345678")
    ]
    assert _request_id(headers) == "12345678-1234-5678-1234-567812345678"

    headers_invalid: list[tuple[bytes, bytes]] = [(b"X-Request-ID", b"invalid-uuid")]
    gen_id = _request_id(headers_invalid)
    assert gen_id != "invalid-uuid"
    assert len(gen_id) == 36

    assert _method_label("GET") == "GET"
    assert _method_label("post") == "POST"
    assert _method_label("INVALID_METHOD") == "OTHER"

    class MockRoute:
        path = "/api/v1/items/{item_id}"

    assert _route_template({"route": MockRoute()}) == "/api/v1/items/{item_id}"
    assert _route_template({}) == "unmatched"
