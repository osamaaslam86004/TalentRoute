"""Unit tests for ``app.audit.service`` using fully mocked ``AsyncSession`` objects.

No database is touched. SQL statements passed to the mocked session are inspected by compiling them
against the PostgreSQL dialect, which lets us assert tenant scoping, locking, ordering and
pagination without executing anything.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.audit import service
from app.audit.integrity import GENESIS_HASH, audit_hash_payload, calculate_event_hash
from app.audit.service import (
    list_audit_events,
    record_audit_event,
    safe_audit_metadata,
    verify_audit_chain,
)
from app.core.security import Principal

TENANT = "tenant-a"

# The audit metadata allow-list is a privacy control. Pinning it here means widening it requires a
# deliberate, reviewed change to this test as well.
EXPECTED_ALLOWED_METADATA_KEYS = {
    "status",
    "count",
    "limit",
    "offset",
    "prompt_version",
    "model_version",
    "rubric_version",
    "label",
    "reason_codes",
    "redacted_categories",
    "latency_ms",
    "cost_usd",
    "dataset_version",
    "report_id",
    "appeal_outcome",
    "attempt_count",
    "record_type",
    "schema_valid",
}


# --------------------------------------------------------------------------- helpers
def sql_of(statement, *, literal_binds: bool = True) -> str:
    """Compile a SQLAlchemy statement to a whitespace-normalised PostgreSQL string."""
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": literal_binds}
    )
    return " ".join(str(compiled).split())


def make_principal(**overrides) -> Principal:
    values = {
        "subject": "alice",
        "tenant_id": TENANT,
        "roles": frozenset({"reviewer", "auditor"}),
        "scopes": frozenset(),
        "auth_method": "oidc",
    }
    values.update(overrides)
    return Principal(**values)


def make_head(last_sequence: int = 0, last_hash: str = GENESIS_HASH) -> SimpleNamespace:
    return SimpleNamespace(last_sequence=last_sequence, last_hash=last_hash)


def make_session(*, head=None, rows=None, scalar_side_effect=None) -> AsyncMock:
    """A mocked AsyncSession wired for ``record_audit_event`` and ``verify_audit_chain``."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    if scalar_side_effect is not None:
        session.scalar = AsyncMock(side_effect=scalar_side_effect)
    else:
        session.scalar = AsyncMock(return_value=head)

    scalars_result = MagicMock()
    scalars_result.all.return_value = list(rows or [])
    session.scalars = AsyncMock(return_value=scalars_result)

    # ``async with session.begin_nested():`` must NOT swallow exceptions, so __aexit__ is falsy.
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)
    return session


def make_list_session(items, count) -> AsyncMock:
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = items
    session.scalars = AsyncMock(return_value=scalars_result)
    session.scalar = AsyncMock(return_value=count)
    return session


def recompute_hash(event) -> str:
    """Independently rebuild an event's hash from its persisted fields."""
    return calculate_event_hash(
        audit_hash_payload(
            event_id=str(event.id),
            occurred_at=event.occurred_at,
            tenant_id=event.tenant_id,
            actor_subject=event.actor_subject,
            actor_roles=event.actor_roles,
            request_id=event.request_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            metadata=event.event_metadata,
            sequence_no=event.sequence_no,
            previous_hash=event.previous_hash,
        )
    )


def build_chain(length: int = 3, tenant_id: str = TENANT) -> list[SimpleNamespace]:
    """Build a genuinely valid, hash-linked chain of event-like rows."""
    rows: list[SimpleNamespace] = []
    previous = GENESIS_HASH
    base = datetime(2026, 1, 1, tzinfo=UTC)

    for sequence_no in range(1, length + 1):
        row = SimpleNamespace(
            id=uuid.uuid4(),
            occurred_at=base + timedelta(seconds=sequence_no),
            tenant_id=tenant_id,
            actor_subject="alice",
            actor_roles=["auditor"],
            request_id=f"req-{sequence_no}",
            action="screening.read",
            resource_type="screening",
            resource_id=str(sequence_no),
            outcome="success",
            event_metadata={"count": sequence_no},
            sequence_no=sequence_no,
            previous_hash=previous,
        )
        row.event_hash = recompute_hash(row)
        rows.append(row)
        previous = row.event_hash

    return rows


def head_for(rows) -> SimpleNamespace:
    if not rows:
        return make_head()
    return make_head(last_sequence=len(rows), last_hash=rows[-1].event_hash)


async def record(session, **overrides):
    kwargs = {
        "principal": None,
        "tenant_id": TENANT,
        "request_id": "req-1",
        "action": "screening.created",
        "resource_type": "screening",
    }
    kwargs.update(overrides)
    return await record_audit_event(session, **kwargs)


@pytest.fixture
def lock_metric():
    with patch("app.audit.service.record_audit_head_lock_duration") as metric:
        yield metric


# =========================================================================== safe_audit_metadata
class TestSafeAuditMetadata:
    def test_allow_list_is_exactly_the_reviewed_set(self):
        assert service._ALLOWED_METADATA == EXPECTED_ALLOWED_METADATA_KEYS

    @pytest.mark.parametrize("value", [None, {}])
    def test_none_and_empty_input_return_an_empty_dict(self, value):
        assert safe_audit_metadata(value) == {}

    def test_returns_a_new_dict_and_does_not_mutate_input(self):
        original = {"status": "ok", "application_text": "secret"}
        snapshot = dict(original)
        result = safe_audit_metadata(original)
        assert result is not original
        assert original == snapshot

    @pytest.mark.parametrize("key", sorted(EXPECTED_ALLOWED_METADATA_KEYS))
    def test_every_allow_listed_key_is_accepted(self, key):
        assert safe_audit_metadata({key: "x"}) == {key: "x"}

    @pytest.mark.parametrize(
        "key",
        [
            "notes",
            "application_text",
            "prompt",
            "email",
            "api_key",
            "password",
            "token",
            "raw_response",
            "job_title",
            "tenant_id",
            "Status",  # keys are case-sensitive
            "",
        ],
    )
    def test_unlisted_and_sensitive_keys_are_dropped(self, key):
        assert safe_audit_metadata({key: "value"}) == {}

    def test_mixed_input_keeps_only_allow_listed_keys(self):
        result = safe_audit_metadata(
            {
                "status": "completed",
                "count": 3,
                "application_text": "PRIVATE",
                "notes": "PRIVATE",
            }
        )
        assert result == {"status": "completed", "count": 3}

    @pytest.mark.parametrize("key", ["status", "count", "label"])
    def test_none_values_are_dropped(self, key):
        assert safe_audit_metadata({key: None}) == {}

    def test_booleans_are_preserved_as_booleans(self):
        result = safe_audit_metadata({"schema_valid": True, "status": False})
        assert result == {"schema_valid": True, "status": False}
        assert result["schema_valid"] is True
        assert result["status"] is False

    @pytest.mark.parametrize("value", [0, 1, -5, 10**12])
    def test_integers_including_zero_and_negatives_are_kept(self, value):
        assert safe_audit_metadata({"count": value}) == {"count": value}

    def test_finite_floats_are_kept(self):
        assert safe_audit_metadata({"latency_ms": 12.5, "cost_usd": 0.0}) == {
            "latency_ms": 12.5,
            "cost_usd": 0.0,
        }

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_are_dropped(self, bad):
        assert safe_audit_metadata({"latency_ms": bad}) == {}

    def test_strings_are_capped_at_160_characters(self):
        assert safe_audit_metadata({"status": "x" * 160}) == {"status": "x" * 160}
        assert safe_audit_metadata({"status": "x" * 161}) == {"status": "x" * 160}
        assert len(safe_audit_metadata({"status": "x" * 10_000})["status"]) == 160

    def test_empty_string_is_kept(self):
        assert safe_audit_metadata({"status": ""}) == {"status": ""}

    def test_list_items_are_stringified_and_capped_at_80_characters(self):
        result = safe_audit_metadata({"reason_codes": ["a" * 200, "short"]})
        assert result == {"reason_codes": ["a" * 80, "short"]}

    def test_lists_are_capped_at_20_items(self):
        result = safe_audit_metadata({"reason_codes": [f"c{i}" for i in range(25)]})
        assert result["reason_codes"] == [f"c{i}" for i in range(20)]

    def test_list_items_that_are_not_str_or_int_are_filtered_out(self):
        result = safe_audit_metadata(
            {"reason_codes": ["keep", 7, 2.5, None, {"k": "v"}, ["nested"], object()]}
        )
        assert result == {"reason_codes": ["keep", "7"]}

    def test_tuples_are_converted_to_lists(self):
        assert safe_audit_metadata({"redacted_categories": ("email", "phone")}) == {
            "redacted_categories": ["email", "phone"]
        }

    def test_empty_list_is_preserved(self):
        assert safe_audit_metadata({"reason_codes": []}) == {"reason_codes": []}

    @pytest.mark.parametrize(
        "value",
        [
            {"nested": "dict"},
            {"a", "b"},
            b"bytes",
            object(),
            datetime(2026, 1, 1, tzinfo=UTC),
        ],
        ids=["dict", "set", "bytes", "object", "datetime"],
    )
    def test_unsupported_value_types_are_dropped(self, value):
        assert safe_audit_metadata({"status": value}) == {}

    def test_accepts_any_mapping_type(self):
        proxy = MappingProxyType({"status": "ok", "bogus": 1})
        assert isinstance(proxy, Mapping)
        assert safe_audit_metadata(proxy) == {"status": "ok"}


# =========================================================================== record_audit_event
class TestRecordAuditEvent:
    async def test_first_event_starts_at_sequence_one_linked_to_genesis(
        self, lock_metric
    ):
        head = make_head()
        session = make_session(head=head)

        event = await record(session)

        assert event.sequence_no == 1
        assert event.previous_hash == GENESIS_HASH
        assert event.tenant_id == TENANT
        assert re.fullmatch(r"[0-9a-f]{64}", event.event_hash)

    async def test_sequence_and_previous_hash_continue_from_the_head(self, lock_metric):
        session = make_session(head=make_head(last_sequence=41, last_hash="a" * 64))

        event = await record(session)

        assert event.sequence_no == 42
        assert event.previous_hash == "a" * 64

    async def test_head_is_advanced_to_the_new_event(self, lock_metric):
        head = make_head(last_sequence=4, last_hash="c" * 64)
        session = make_session(head=head)

        event = await record(session)

        assert head.last_sequence == 5
        assert head.last_hash == event.event_hash

    async def test_event_hash_matches_an_independent_recomputation(self, lock_metric):
        session = make_session(head=make_head(last_sequence=2, last_hash="d" * 64))

        event = await record(
            session,
            principal=make_principal(),
            resource_id="res-1",
            metadata={"status": "completed", "count": 3},
        )

        assert event.event_hash == recompute_hash(event)

    async def test_event_is_added_to_the_session_but_not_committed(self, lock_metric):
        session = make_session(head=make_head())

        event = await record(session)

        session.add.assert_called_once_with(event)
        session.commit.assert_not_awaited()

    async def test_genesis_head_is_initialised_idempotently_before_locking(
        self, lock_metric
    ):
        session = make_session(head=make_head())

        await record(session)

        session.execute.assert_awaited_once()
        statement, params = session.execute.await_args.args
        normalised = " ".join(str(statement).split())
        assert "INSERT INTO audit_chain_heads" in normalised
        assert "ON CONFLICT (tenant_id) DO NOTHING" in normalised
        assert params == {"tenant_id": TENANT, "genesis_hash": GENESIS_HASH}

    async def test_operations_run_in_order_init_then_lock_then_add(self, lock_metric):
        session = make_session(head=make_head())

        await record(session)

        order = [
            c[0] for c in session.mock_calls if c[0] in {"execute", "scalar", "add"}
        ]
        assert order == ["execute", "scalar", "add"]

    async def test_chain_head_is_selected_with_a_row_lock_for_this_tenant(
        self, lock_metric
    ):
        session = make_session(head=make_head())

        await record(session)

        sql = sql_of(session.scalar.await_args.args[0])
        assert "FROM audit_chain_heads" in sql
        assert f"audit_chain_heads.tenant_id = '{TENANT}'" in sql
        assert sql.endswith("FOR UPDATE")

    async def test_consecutive_events_form_a_linked_chain(self, lock_metric):
        head = make_head()
        session = make_session(head=head)

        events = [await record(session, action=f"step.{i}") for i in range(3)]

        assert [e.sequence_no for e in events] == [1, 2, 3]
        assert events[0].previous_hash == GENESIS_HASH
        assert events[1].previous_hash == events[0].event_hash
        assert events[2].previous_hash == events[1].event_hash
        assert head.last_sequence == 3
        assert head.last_hash == events[2].event_hash
        assert len({e.event_hash for e in events}) == 3

    async def test_round_trip_events_verify_as_a_valid_chain(self, lock_metric):
        head = make_head()
        write_session = make_session(head=head)
        events = [await record(write_session, resource_id=str(i)) for i in range(4)]

        verify_session = make_session(head=head, rows=events)
        result = await verify_audit_chain(verify_session, tenant_id=TENANT)

        assert result["valid"] is True
        assert result["event_count"] == 4
        assert result["head_hash"] == events[-1].event_hash

    # ---- actor resolution
    async def test_defaults_to_service_worker_identity_without_a_principal(
        self, lock_metric
    ):
        event = await record(make_session(head=make_head()), principal=None)

        assert event.actor_subject == "service:worker"
        assert event.actor_roles == ["service"]

    async def test_principal_supplies_subject_and_sorted_roles(self, lock_metric):
        principal = make_principal(
            subject="bob", roles=frozenset({"zeta", "alpha", "mid"})
        )

        event = await record(make_session(head=make_head()), principal=principal)

        assert event.actor_subject == "bob"
        assert event.actor_roles == ["alpha", "mid", "zeta"]

    async def test_explicit_actor_fields_are_used_without_a_principal(
        self, lock_metric
    ):
        event = await record(
            make_session(head=make_head()),
            principal=None,
            actor_subject="service:outbox-recovery",
            actor_roles=["service", "operator"],
        )

        assert event.actor_subject == "service:outbox-recovery"
        assert sorted(event.actor_roles) == ["operator", "service"]

    async def test_empty_actor_roles_fall_back_to_service(self, lock_metric):
        event = await record(
            make_session(head=make_head()), principal=None, actor_roles=[]
        )

        assert event.actor_roles == ["service"]

    async def test_explicit_actor_subject_takes_precedence_over_the_principal_subject(
        self, lock_metric
    ):
        event = await record(
            make_session(head=make_head()),
            principal=make_principal(subject="alice"),
            actor_subject="delegate",
        )

        assert event.actor_subject == "delegate"
        assert event.actor_roles == [
            "auditor",
            "reviewer",
        ]  # roles still come from the principal

    # ---- field normalisation
    async def test_default_outcome_is_success_and_custom_outcome_is_kept(
        self, lock_metric
    ):
        session = make_session(head=make_head())

        assert (await record(session)).outcome == "success"
        assert (await record(session, outcome="failure")).outcome == "failure"

    async def test_string_fields_are_truncated_to_their_column_widths(
        self, lock_metric
    ):
        event = await record(
            make_session(head=make_head()),
            principal=make_principal(subject="s" * 400),
            request_id="r" * 200,
            action="a" * 300,
            resource_type="t" * 200,
            resource_id="i" * 300,
            outcome="o" * 100,
        )

        assert event.actor_subject is not None
        assert len(event.actor_subject) == 255

        assert event.request_id is not None
        assert len(event.request_id) == 64

        assert event.action is not None
        assert len(event.action) == 120

        assert event.resource_type is not None
        assert len(event.resource_type) == 80

        assert event.resource_id is not None
        assert len(event.resource_id) == 128

        assert event.outcome is not None
        assert len(event.outcome) == 40

    async def test_blank_request_and_resource_ids_become_none(self, lock_metric):
        session = make_session(head=make_head())

        for blank in (None, ""):
            event = await record(session, request_id=blank, resource_id=blank)
            assert event.request_id is None
            assert event.resource_id is None

    async def test_tenant_id_is_truncated_consistently_everywhere(self, lock_metric):
        long_tenant = "t" * 200
        session = make_session(head=make_head())

        event = await record(session, tenant_id=long_tenant)

        assert event.tenant_id == "t" * 128
        assert session.execute.await_args.args[1]["tenant_id"] == "t" * 128
        lock_sql = sql_of(session.scalar.await_args.args[0])
        assert "t" * 128 in lock_sql
        assert "t" * 129 not in lock_sql
        assert event.event_hash == recompute_hash(event)

    async def test_occurred_at_is_timezone_aware_and_current(self, lock_metric):
        before = datetime.now(UTC)
        event = await record(make_session(head=make_head()))
        after = datetime.now(UTC)

        assert event.occurred_at.tzinfo is not None
        assert before <= event.occurred_at <= after

    async def test_each_event_gets_a_fresh_uuid(self, lock_metric):
        session = make_session(head=make_head())

        first = await record(session)
        second = await record(session)

        assert isinstance(first.id, uuid.UUID)
        assert first.id != second.id

    # ---- metadata handling
    async def test_metadata_is_filtered_through_the_allow_list_before_persisting(
        self, lock_metric
    ):
        event = await record(
            make_session(head=make_head()),
            metadata={
                "status": "completed",
                "application_text": "TOP-SECRET RESUME",
                "notes": "free text",
                "count": 2,
            },
        )

        assert event.event_metadata == {"status": "completed", "count": 2}
        assert "TOP-SECRET" not in repr(event.event_metadata)
        assert event.event_hash == recompute_hash(
            event
        )  # hash covers sanitised metadata only

    async def test_missing_metadata_is_stored_as_an_empty_dict(self, lock_metric):
        event = await record(make_session(head=make_head()), metadata=None)

        assert event.event_metadata == {}

    # ---- lock timing metric
    async def test_lock_duration_is_recorded_as_success(self, lock_metric):
        await record(make_session(head=make_head()))

        lock_metric.assert_called_once()
        args, kwargs = lock_metric.call_args
        assert isinstance(args[0], float) and args[0] >= 0.0
        assert kwargs == {"outcome": "success"}

    async def test_lock_failure_is_recorded_and_the_error_propagates(self, lock_metric):
        session = make_session(scalar_side_effect=RuntimeError("Lock timeout"))

        with pytest.raises(RuntimeError, match="Lock timeout"):
            await record(session)

        lock_metric.assert_called_once()
        assert lock_metric.call_args.kwargs == {"outcome": "failure"}
        session.add.assert_not_called()

    async def test_missing_head_row_raises_and_nothing_is_added(self, lock_metric):
        session = make_session(head=None)

        with pytest.raises(RuntimeError, match="Unable to allocate audit chain head"):
            await record(session)

        session.add.assert_not_called()


# =========================================================================== verify_audit_chain
class TestVerifyAuditChain:
    async def test_empty_chain_with_genesis_head_is_valid(self, lock_metric):
        session = make_session(head=make_head(), rows=[])

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result == {
            "tenant_id": TENANT,
            "valid": True,
            "event_count": 0,
            "head_sequence": 0,
            "first_failure_sequence": None,
            "failure_reason": None,
            "head_hash": GENESIS_HASH,
        }

    async def test_intact_chain_is_valid(self, lock_metric):
        rows = build_chain(3)
        session = make_session(head=head_for(rows), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is True
        assert result["event_count"] == 3
        assert result["head_sequence"] == 3
        assert result["head_hash"] == rows[-1].event_hash
        assert result["first_failure_sequence"] is None
        assert result["failure_reason"] is None

    async def test_single_event_chain_is_valid(self, lock_metric):
        rows = build_chain(1)
        result = await verify_audit_chain(
            make_session(head=head_for(rows), rows=rows), tenant_id=TENANT
        )
        assert result["valid"] is True

    # ---- sequence problems
    async def test_missing_middle_event_is_reported_as_a_sequence_gap(
        self, lock_metric
    ):
        rows = build_chain(3)
        tampered = [rows[0], rows[2]]  # event 2 deleted
        session = make_session(head=head_for(rows), rows=tampered)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "sequence_gap_or_reordering"
        assert result["first_failure_sequence"] == 3

    async def test_reordered_events_are_detected(self, lock_metric):
        rows = build_chain(3)
        session = make_session(head=head_for(rows), rows=[rows[1], rows[0], rows[2]])

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "sequence_gap_or_reordering"
        assert result["first_failure_sequence"] == 2

    async def test_chain_that_does_not_start_at_one_is_detected(self, lock_metric):
        rows = build_chain(3)
        session = make_session(
            head=head_for(rows), rows=rows[1:]
        )  # first event deleted

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "sequence_gap_or_reordering"
        assert result["first_failure_sequence"] == 2

    async def test_duplicate_sequence_numbers_are_detected(self, lock_metric):
        rows = build_chain(2)
        session = make_session(head=head_for(rows), rows=[rows[0], rows[0], rows[1]])

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "sequence_gap_or_reordering"
        assert result["first_failure_sequence"] == 1

    # ---- hash-link problems
    async def test_broken_previous_hash_link_is_detected(self, lock_metric):
        rows = build_chain(3)
        rows[1].previous_hash = "f" * 64
        session = make_session(head=head_for(rows), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "previous_hash_mismatch"
        assert result["first_failure_sequence"] == 2

    async def test_first_event_must_link_to_genesis(self, lock_metric):
        rows = build_chain(2)
        rows[0].previous_hash = "1" * 64
        session = make_session(head=head_for(rows), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "previous_hash_mismatch"
        assert result["first_failure_sequence"] == 1

    @pytest.mark.parametrize(
        ("field", "new_value"),
        [
            ("action", "screening.deleted"),
            ("actor_subject", "mallory"),
            ("actor_roles", ["admin"]),
            ("outcome", "failure"),
            ("resource_id", "other"),
            ("event_metadata", {"count": 999}),
            ("occurred_at", datetime(2030, 1, 1, tzinfo=UTC)),
        ],
    )
    async def test_tampering_with_event_content_is_detected(
        self, field, new_value, lock_metric
    ):
        rows = build_chain(3)
        setattr(rows[1], field, new_value)
        session = make_session(head=head_for(rows), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "event_hash_mismatch"
        assert result["first_failure_sequence"] == 2

    async def test_overwritten_stored_hash_is_detected(self, lock_metric):
        rows = build_chain(3)
        rows[2].event_hash = "0" * 64
        session = make_session(head=head_for(build_chain(3)), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "event_hash_mismatch"
        assert result["first_failure_sequence"] == 3

    async def test_only_the_first_failure_is_reported(self, lock_metric):
        rows = build_chain(4)
        rows[1].action = "tampered-early"
        rows[3].action = "tampered-late"
        session = make_session(head=head_for(rows), rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["first_failure_sequence"] == 2

    # ---- chain head problems
    async def test_head_sequence_ahead_of_events_is_a_head_mismatch(self, lock_metric):
        rows = build_chain(3)
        head = make_head(last_sequence=5, last_hash=rows[-1].event_hash)
        session = make_session(head=head, rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "chain_head_mismatch"
        assert result["first_failure_sequence"] == 4  # one past the last verified event
        assert result["head_sequence"] == 5  # reports the stored head, not the recount

    async def test_head_sequence_behind_events_is_a_head_mismatch(self, lock_metric):
        rows = build_chain(3)
        head = make_head(last_sequence=2, last_hash=rows[1].event_hash)
        session = make_session(head=head, rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "chain_head_mismatch"
        assert result["first_failure_sequence"] == 4

    async def test_head_hash_not_matching_the_last_event_is_a_head_mismatch(
        self, lock_metric
    ):
        rows = build_chain(3)
        head = make_head(last_sequence=3, last_hash="e" * 64)
        session = make_session(head=head, rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "chain_head_mismatch"
        assert result["head_hash"] == "e" * 64

    async def test_non_genesis_head_with_no_events_is_a_head_mismatch(
        self, lock_metric
    ):
        head = make_head(last_sequence=2, last_hash="a" * 64)
        session = make_session(head=head, rows=[])

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "chain_head_mismatch"
        assert result["first_failure_sequence"] == 1

    async def test_events_without_a_head_row_report_head_missing(self, lock_metric):
        rows = build_chain(2)
        session = make_session(head=None, rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is False
        assert result["failure_reason"] == "chain_head_missing"
        assert result["first_failure_sequence"] == 1
        assert result["head_sequence"] == 0
        assert result["head_hash"] == GENESIS_HASH

    async def test_no_head_and_no_events_is_valid(self, lock_metric):
        session = make_session(head=None, rows=[])

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["valid"] is True
        assert result["event_count"] == 0
        assert result["head_hash"] == GENESIS_HASH

    async def test_event_level_failure_takes_precedence_over_head_mismatch(
        self, lock_metric
    ):
        rows = build_chain(3)
        rows[0].action = "tampered"
        bad_head = make_head(last_sequence=99, last_hash="9" * 64)
        session = make_session(head=bad_head, rows=rows)

        result = await verify_audit_chain(session, tenant_id=TENANT)

        assert result["failure_reason"] == "event_hash_mismatch"
        assert result["first_failure_sequence"] == 1

    # ---- query / transaction behaviour
    async def test_verification_runs_in_a_repeatable_read_nested_transaction(
        self, lock_metric
    ):
        session = make_session(head=make_head(), rows=[])

        await verify_audit_chain(session, tenant_id=TENANT)

        session.begin_nested.assert_called_once_with()
        first_statement = session.execute.await_args_list[0].args[0]
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in str(first_statement)

    async def test_genesis_head_is_initialised_with_the_unmodified_tenant_id(
        self, lock_metric
    ):
        session = make_session(head=make_head(), rows=[])

        await verify_audit_chain(session, tenant_id=TENANT)

        statement, params = session.execute.await_args_list[1].args
        assert "ON CONFLICT (tenant_id) DO NOTHING" in str(statement)
        assert params == {"tenant_id": TENANT, "genesis_hash": GENESIS_HASH}

    async def test_head_is_locked_and_events_are_read_in_sequence_order_for_the_tenant(
        self, lock_metric
    ):
        session = make_session(head=make_head(), rows=[])

        await verify_audit_chain(session, tenant_id=TENANT)

        head_sql = sql_of(session.scalar.await_args.args[0])
        assert head_sql.endswith("FOR UPDATE")
        assert f"audit_chain_heads.tenant_id = '{TENANT}'" in head_sql

        events_sql = sql_of(session.scalars.await_args.args[0])
        assert f"audit_events.tenant_id = '{TENANT}'" in events_sql
        assert "ORDER BY audit_events.sequence_no ASC" in events_sql

    async def test_result_echoes_the_requested_tenant(self, lock_metric):
        session = make_session(head=make_head(), rows=[])

        result = await verify_audit_chain(session, tenant_id="another-tenant")

        assert result["tenant_id"] == "another-tenant"

    async def test_nothing_is_written_or_committed_during_verification(
        self, lock_metric
    ):
        rows = build_chain(2)
        session = make_session(head=head_for(rows), rows=rows)

        await verify_audit_chain(session, tenant_id=TENANT)

        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    # ---- lock timing metric
    async def test_lock_duration_is_recorded_as_success(self, lock_metric):
        await verify_audit_chain(
            make_session(head=make_head(), rows=[]), tenant_id=TENANT
        )

        lock_metric.assert_called_once()
        args, kwargs = lock_metric.call_args
        assert args[0] >= 0.0
        assert kwargs == {"outcome": "success"}

    async def test_lock_failure_is_recorded_and_propagates_out_of_the_transaction(
        self, lock_metric
    ):
        session = make_session(scalar_side_effect=RuntimeError("deadlock detected"))

        with pytest.raises(RuntimeError, match="deadlock detected"):
            await verify_audit_chain(session, tenant_id=TENANT)

        assert lock_metric.call_args.kwargs == {"outcome": "failure"}
        session.scalars.assert_not_awaited()


# =========================================================================== list_audit_events
class TestListAuditEvents:
    async def test_returns_items_and_total_count(self):
        items = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        session = make_list_session(items, count=57)

        result_items, count = await list_audit_events(session, tenant_id=TENANT)

        assert result_items == items
        assert count == 57

    async def test_items_are_returned_as_a_list(self):
        session = make_list_session(
            (SimpleNamespace(id=1),), count=1
        )  # a tuple from the driver

        items, _ = await list_audit_events(session, tenant_id=TENANT)

        assert isinstance(items, list)

    async def test_empty_result_returns_empty_list_and_zero(self):
        items, count = await list_audit_events(
            make_list_session([], 0), tenant_id=TENANT
        )

        assert items == []
        assert count == 0

    async def test_missing_count_is_coerced_to_zero(self):
        _, count = await list_audit_events(
            make_list_session([], None), tenant_id=TENANT
        )

        assert count == 0
        assert isinstance(count, int)

    async def test_defaults_are_limit_100_offset_0_newest_first(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT)

        sql = sql_of(session.scalars.await_args.args[0])
        assert "LIMIT 100" in sql
        assert "OFFSET 0" in sql
        assert "ORDER BY audit_events.occurred_at DESC" in sql

    async def test_limit_and_offset_are_applied_for_pagination(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT, limit=10, offset=30)

        sql = sql_of(session.scalars.await_args.args[0])
        assert "LIMIT 10" in sql
        assert "OFFSET 30" in sql

    async def test_both_queries_are_scoped_to_the_tenant(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id="tenant-a")

        page_sql = sql_of(session.scalars.await_args.args[0])
        count_sql = sql_of(session.scalar.await_args.args[0])
        assert "audit_events.tenant_id = 'tenant-a'" in page_sql
        assert "audit_events.tenant_id = 'tenant-a'" in count_sql

    async def test_other_tenants_never_appear_in_the_queries(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id="tenant-b", action="screening.read")

        for statement in (
            session.scalars.await_args.args[0],
            session.scalar.await_args.args[0],
        ):
            sql = sql_of(statement)
            assert "tenant-b" in sql
            assert "tenant-a" not in sql

    async def test_tenant_value_is_a_bound_parameter_not_interpolated_sql(self):
        hostile = "x' OR '1'='1"
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=hostile)

        compiled = session.scalars.await_args.args[0].compile(
            dialect=postgresql.dialect()
        )
        assert hostile not in str(compiled)
        assert hostile in compiled.params.values()

    async def test_action_filter_is_applied_to_page_and_count_queries(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT, action="screening.read")

        for statement in (
            session.scalars.await_args.args[0],
            session.scalar.await_args.args[0],
        ):
            sql = sql_of(statement)
            assert "audit_events.action = 'screening.read'" in sql
            assert f"audit_events.tenant_id = '{TENANT}'" in sql

    @pytest.mark.parametrize("action", [None, ""])
    async def test_no_action_filter_without_an_action(self, action):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT, action=action)

        for statement in (
            session.scalars.await_args.args[0],
            session.scalar.await_args.args[0],
        ):
            assert "audit_events.action =" not in sql_of(statement)

    async def test_count_query_counts_rows_rather_than_loading_them(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT, limit=5, offset=5)

        count_sql = sql_of(session.scalar.await_args.args[0])
        assert "count(*)" in count_sql
        assert "FROM audit_events" in count_sql
        # The count is the full total, so pagination must not constrain it.
        assert "LIMIT" not in count_sql
        assert "OFFSET" not in count_sql

    async def test_each_query_is_issued_exactly_once(self):
        session = make_list_session([], 0)

        await list_audit_events(session, tenant_id=TENANT)

        session.scalars.assert_awaited_once()
        session.scalar.assert_awaited_once()
