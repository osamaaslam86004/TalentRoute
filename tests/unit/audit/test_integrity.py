"""Unit tests for ``app.audit.integrity``: timestamp normalisation, payload shape, event hashing."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.audit.integrity import (
    GENESIS_HASH,
    _timestamp,
    audit_hash_payload,
    calculate_event_hash,
)

EXPECTED_PAYLOAD_KEYS = {
    "id",
    "occurred_at",
    "tenant_id",
    "actor_subject",
    "actor_roles",
    "request_id",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "metadata",
    "sequence_no",
    "previous_hash",
}


def make_payload_kwargs(**overrides):
    """Valid keyword arguments for ``audit_hash_payload``; override any field per test."""
    values = {
        "event_id": "6f1d2c1e-0000-4000-8000-000000000001",
        "occurred_at": datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        "tenant_id": "tenant-a",
        "actor_subject": "alice",
        "actor_roles": ["reviewer", "auditor"],
        "request_id": "req-1",
        "action": "screening.read",
        "resource_type": "screening",
        "resource_id": "abc-123",
        "outcome": "success",
        "metadata": {"status": "completed", "count": 2},
        "sequence_no": 7,
        "previous_hash": "a" * 64,
    }
    values.update(overrides)
    return values


# --------------------------------------------------------------------------- GENESIS_HASH
def test_genesis_hash_is_64_zero_hex_characters():
    assert GENESIS_HASH == "0" * 64
    assert len(GENESIS_HASH) == 64


# --------------------------------------------------------------------------- _timestamp
class TestTimestamp:
    def test_utc_value_is_formatted_with_microseconds_and_offset(self):
        value = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
        assert _timestamp(value) == "2026-01-02T03:04:05.123456+00:00"

    def test_zero_microseconds_are_still_emitted(self):
        """``timespec='microseconds'`` keeps the width stable so hashes are reproducible."""
        value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        assert _timestamp(value) == "2026-01-02T03:04:05.000000+00:00"

    def test_non_utc_offset_is_converted_to_utc(self):
        plus_five = timezone(timedelta(hours=5))
        value = datetime(2026, 1, 2, 8, 4, 5, 1, tzinfo=plus_five)
        assert _timestamp(value) == "2026-01-02T03:04:05.000001+00:00"

    def test_negative_offset_rolls_the_date_forward(self):
        minus_eight = timezone(timedelta(hours=-8))
        value = datetime(2026, 12, 31, 20, 0, 0, tzinfo=minus_eight)
        assert _timestamp(value) == "2027-01-01T04:00:00.000000+00:00"

    def test_equivalent_instants_in_different_zones_serialise_identically(self):
        utc_value = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        offset_value = datetime(
            2026, 6, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))
        )
        assert _timestamp(utc_value) == _timestamp(offset_value)

    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            _timestamp(datetime(2026, 1, 2, 3, 4, 5))

    def test_output_matches_iso_8601_shape(self):
        result = _timestamp(datetime.now(UTC))
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00", result
        )


# --------------------------------------------------------------------------- audit_hash_payload
class TestAuditHashPayload:
    def test_contains_exactly_the_expected_keys(self):
        payload = audit_hash_payload(**make_payload_kwargs())
        assert set(payload) == EXPECTED_PAYLOAD_KEYS

    def test_values_are_mapped_to_the_right_keys(self):
        kwargs = make_payload_kwargs()
        payload = audit_hash_payload(**kwargs)

        assert payload["id"] == kwargs["event_id"]
        assert payload["tenant_id"] == "tenant-a"
        assert payload["actor_subject"] == "alice"
        assert payload["request_id"] == "req-1"
        assert payload["action"] == "screening.read"
        assert payload["resource_type"] == "screening"
        assert payload["resource_id"] == "abc-123"
        assert payload["outcome"] == "success"
        assert payload["metadata"] == {"status": "completed", "count": 2}
        assert payload["sequence_no"] == 7
        assert payload["previous_hash"] == "a" * 64

    def test_occurred_at_is_serialised_via_timestamp_helper(self):
        kwargs = make_payload_kwargs()
        payload = audit_hash_payload(**kwargs)
        assert payload["occurred_at"] == _timestamp(kwargs["occurred_at"])
        assert isinstance(payload["occurred_at"], str)

    def test_actor_roles_are_sorted_for_canonical_ordering(self):
        payload = audit_hash_payload(
            **make_payload_kwargs(actor_roles=["zeta", "alpha", "mid"])
        )
        assert payload["actor_roles"] == ["alpha", "mid", "zeta"]

    def test_actor_roles_input_list_is_not_mutated(self):
        roles = ["zeta", "alpha"]
        audit_hash_payload(**make_payload_kwargs(actor_roles=roles))
        assert roles == ["zeta", "alpha"]

    def test_role_order_does_not_change_the_resulting_hash(self):
        first = audit_hash_payload(**make_payload_kwargs(actor_roles=["a", "b"]))
        second = audit_hash_payload(**make_payload_kwargs(actor_roles=["b", "a"]))
        assert calculate_event_hash(first) == calculate_event_hash(second)

    def test_empty_roles_are_preserved_as_an_empty_list(self):
        assert (
            audit_hash_payload(**make_payload_kwargs(actor_roles=[]))["actor_roles"]
            == []
        )

    @pytest.mark.parametrize("field", ["request_id", "resource_id"])
    def test_optional_fields_accept_none(self, field):
        payload = audit_hash_payload(**make_payload_kwargs(**{field: None}))
        assert payload[field] is None

    def test_empty_metadata_is_kept_as_an_empty_dict(self):
        assert audit_hash_payload(**make_payload_kwargs(metadata={}))["metadata"] == {}

    def test_naive_occurred_at_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            audit_hash_payload(**make_payload_kwargs(occurred_at=datetime(2026, 1, 1)))

    def test_payload_is_json_serialisable(self):
        payload = audit_hash_payload(**make_payload_kwargs())
        assert json.loads(json.dumps(payload)) == payload

    def test_keyword_only_signature(self):
        with pytest.raises(TypeError):
            audit_hash_payload("positional")  # type: ignore[call-arg]

    def test_missing_required_argument_raises(self):
        kwargs = make_payload_kwargs()
        del kwargs["previous_hash"]
        with pytest.raises(TypeError):
            audit_hash_payload(**kwargs)


# --------------------------------------------------------------------------- calculate_event_hash
class TestCalculateEventHash:
    def test_returns_64_character_lowercase_hex_digest(self):
        digest = calculate_event_hash(audit_hash_payload(**make_payload_kwargs()))
        assert re.fullmatch(r"[0-9a-f]{64}", digest)

    def test_matches_an_independently_computed_sha256_of_canonical_json(self):
        payload = {"b": 1, "a": [1, 2], "c": {"z": None, "y": "x"}}
        canonical = '{"a":[1,2],"b":1,"c":{"y":"x","z":null}}'
        assert (
            calculate_event_hash(payload)
            == hashlib.sha256(canonical.encode()).hexdigest()
        )

    def test_is_deterministic(self):
        payload = audit_hash_payload(**make_payload_kwargs())
        assert calculate_event_hash(payload) == calculate_event_hash(dict(payload))

    def test_key_insertion_order_is_irrelevant(self):
        forward = {"a": 1, "b": 2, "c": {"x": 1, "y": 2}}
        backward = {"c": {"y": 2, "x": 1}, "b": 2, "a": 1}
        assert calculate_event_hash(forward) == calculate_event_hash(backward)

    def test_non_ascii_is_hashed_as_utf8_not_escaped(self):
        payload = {"actor_subject": "zoë-日本"}
        expected = hashlib.sha256('{"actor_subject":"zoë-日本"}'.encode()).hexdigest()
        assert calculate_event_hash(payload) == expected

    def test_separators_are_compact(self):
        """A pretty-printed encoding would hash differently, so lock the compact form in."""
        pretty = hashlib.sha256(
            json.dumps({"a": 1}, sort_keys=True).encode()
        ).hexdigest()
        assert calculate_event_hash({"a": 1}) != pretty

    @pytest.mark.parametrize(
        ("field", "new_value"),
        [
            ("event_id", "6f1d2c1e-0000-4000-8000-0000000000ff"),
            ("occurred_at", datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC)),
            ("tenant_id", "tenant-b"),
            ("actor_subject", "mallory"),
            ("actor_roles", ["admin"]),
            ("request_id", "req-2"),
            ("action", "screening.deleted"),
            ("resource_type", "audit_chain"),
            ("resource_id", "other"),
            ("outcome", "failure"),
            ("metadata", {"status": "tampered"}),
            ("sequence_no", 8),
            ("previous_hash", "b" * 64),
        ],
    )
    def test_changing_any_field_changes_the_hash(self, field, new_value):
        baseline = calculate_event_hash(audit_hash_payload(**make_payload_kwargs()))
        changed = calculate_event_hash(
            audit_hash_payload(**make_payload_kwargs(**{field: new_value}))
        )
        assert changed != baseline

    def test_nested_metadata_changes_are_detected(self):
        a = calculate_event_hash({"metadata": {"reason_codes": ["x", "y"]}})
        b = calculate_event_hash({"metadata": {"reason_codes": ["y", "x"]}})
        assert a != b  # list order is significant even though dict key order is not

    def test_none_and_missing_values_hash_differently(self):
        assert calculate_event_hash({"request_id": None}) != calculate_event_hash({})

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_numbers_are_rejected(self, bad):
        with pytest.raises(ValueError):
            calculate_event_hash({"metadata": {"latency_ms": bad}})

    def test_non_serialisable_values_raise_type_error(self):
        with pytest.raises(TypeError):
            calculate_event_hash({"metadata": {"obj": object()}})

    def test_same_instant_in_different_zones_yields_the_same_hash(self):
        utc_payload = audit_hash_payload(
            **make_payload_kwargs(occurred_at=datetime(2026, 1, 2, 3, 0, tzinfo=UTC))
        )
        offset_payload = audit_hash_payload(
            **make_payload_kwargs(
                occurred_at=datetime(
                    2026, 1, 2, 8, 0, tzinfo=timezone(timedelta(hours=5))
                )
            )
        )
        assert calculate_event_hash(utc_payload) == calculate_event_hash(offset_payload)

    def test_chained_hashes_diverge_when_an_earlier_link_changes(self):
        """Altering event 1 must ripple into event 2 because previous_hash feeds the payload."""

        def chain(first_action: str) -> str:
            first_hash = calculate_event_hash(
                audit_hash_payload(
                    **make_payload_kwargs(
                        sequence_no=1, previous_hash=GENESIS_HASH, action=first_action
                    )
                )
            )
            return calculate_event_hash(
                audit_hash_payload(
                    **make_payload_kwargs(sequence_no=2, previous_hash=first_hash)
                )
            )

        assert chain("screening.read") != chain("screening.deleted")
