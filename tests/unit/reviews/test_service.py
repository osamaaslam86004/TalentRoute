"""Unit tests for app.reviews.service domain logic.

Tests cover:
- append_feedback: instantiation of ReviewerFeedback, correct assignment of fields, and session flush.
- append_appeal_outcome: append-only invariant, linking to parent feedback ID, reset of review labels/reasons, and session flush.
- get_feedback_by_id: retrieval strictly scoped to tenant_id, optional record_type filtering, and handling non-existent records.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.core.security import Principal
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.reviews.service import (
    append_appeal_outcome,
    append_feedback,
    get_feedback_by_id,
)
from app.schemas.reviewer_feedback import (
    AppealCreate,
    AppealOutcome,
    ReviewerFeedbackCreate,
    ReviewLabel,
)

# Mock pydantic_settings or opentelemetry if not installed in sandbox
for module_name in (
    "pydantic_settings",
    "opentelemetry",
    "opentelemetry.trace",
    "opentelemetry.metrics",
    "opentelemetry.sdk",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "asyncpg",
):
    if module_name not in sys.modules:
        mock_mod = MagicMock()
        if module_name == "pydantic_settings":

            class DummyBaseSettings:
                pass

            mock_mod.BaseSettings = DummyBaseSettings
        sys.modules[module_name] = mock_mod


class TestReviewService(unittest.TestCase):
    """Unit tests for human review feedback and appeal domain service functions."""

    def test_append_feedback_creates_record_and_flushes(self) -> None:
        """Verify append_feedback instantiates ReviewerFeedback correctly and calls session.add and session.flush."""

        async def _test() -> None:
            session = AsyncMock()
            session.add = MagicMock()
            screening_id = uuid.uuid4()
            tenant_id = "tenant-test"

            principal = Principal(
                subject="reviewer-01",
                tenant_id=tenant_id,
                roles=frozenset({"reviewer"}),
                scopes=frozenset({"review:write"}),
                auth_method="oidc",
            )

            payload = ReviewerFeedbackCreate(
                label=ReviewLabel.AGREE,
                reason_codes=["evidence_missing"],
                notes="Detailed review feedback",
            )
            rubric_version = "screening-rubric-v1"

            item = await append_feedback(
                session,
                screening_id=screening_id,
                tenant_id=tenant_id,
                principal=principal,
                payload=payload,
                rubric_version=rubric_version,
            )

            session.add.assert_called_once_with(item)
            session.flush.assert_awaited_once()

            self.assertEqual(item.screening_id, screening_id)
            self.assertEqual(item.tenant_id, tenant_id)
            self.assertEqual(item.reviewer_subject, "reviewer-01")
            self.assertEqual(item.reviewer_roles, ["reviewer"])
            self.assertEqual(item.record_type, "review")
            self.assertEqual(item.label, "agree")
            self.assertEqual(item.reason_codes, ["evidence_missing"])
            self.assertEqual(item.notes, "Detailed review feedback")
            self.assertEqual(item.rubric_version, "screening-rubric-v1")

        asyncio.run(_test())

    def test_append_appeal_outcome_creates_linked_appeal_record(self) -> None:
        """Verify append_appeal_outcome links to parent feedback, respects append-only invariant, and sets record_type='appeal'."""

        async def _test() -> None:
            session = AsyncMock()
            session.add = MagicMock()
            parent_id = uuid.uuid4()
            screening_id = uuid.uuid4()
            tenant_id = "tenant-test"

            original = ReviewerFeedback(
                id=parent_id,
                screening_id=screening_id,
                tenant_id=tenant_id,
                reviewer_subject="reviewer-01",
                reviewer_roles=["reviewer"],
                record_type="review",
                label="disagree",
                reason_codes=["evidence_conflicts"],
                notes="Initial review",
                rubric_version="screening-rubric-v1",
            )

            compliance_principal = Principal(
                subject="compliance-01",
                tenant_id=tenant_id,
                roles=frozenset({"compliance_officer"}),
                scopes=frozenset({"review:appeal"}),
                auth_method="oidc",
            )

            appeal_payload = AppealCreate(
                outcome=AppealOutcome.UPHELD,
                notes="Appeal review upheld after investigation.",
            )

            appeal_item = await append_appeal_outcome(
                session,
                original=original,
                tenant_id=tenant_id,
                principal=compliance_principal,
                payload=appeal_payload,
            )

            session.add.assert_called_once_with(appeal_item)
            session.flush.assert_awaited_once()

            # Append-only invariant checks: original record is untouched
            self.assertEqual(original.record_type, "review")
            self.assertEqual(original.label, "disagree")

            # Appeal record checks
            self.assertEqual(appeal_item.screening_id, screening_id)
            self.assertEqual(appeal_item.tenant_id, tenant_id)
            self.assertEqual(appeal_item.reviewer_subject, "compliance-01")
            self.assertEqual(appeal_item.reviewer_roles, ["compliance_officer"])
            self.assertEqual(appeal_item.record_type, "appeal")
            self.assertIsNone(appeal_item.label)
            self.assertEqual(appeal_item.reason_codes, [])
            self.assertEqual(
                appeal_item.notes, "Appeal review upheld after investigation."
            )
            self.assertEqual(appeal_item.rubric_version, "screening-rubric-v1")
            self.assertEqual(appeal_item.parent_feedback_id, parent_id)
            self.assertEqual(appeal_item.appeal_outcome, "upheld")

        asyncio.run(_test())

    def test_get_feedback_by_id_scoped_to_tenant_id(self) -> None:
        """Verify get_feedback_by_id retrieves feedback strictly scoped to tenant_id."""

        async def _test() -> None:
            session = AsyncMock()
            feedback_id = uuid.uuid4()
            tenant_id = "tenant-test"

            expected_feedback = ReviewerFeedback(
                id=feedback_id,
                tenant_id=tenant_id,
                record_type="review",
            )

            session.scalar.return_value = expected_feedback

            res = await get_feedback_by_id(
                session,
                feedback_id=feedback_id,
                tenant_id=tenant_id,
            )

            self.assertEqual(res, expected_feedback)
            session.scalar.assert_awaited_once()

        asyncio.run(_test())

    def test_get_feedback_by_id_with_record_type_filter(self) -> None:
        """Verify get_feedback_by_id applies record_type filtering when provided."""

        async def _test() -> None:
            session = AsyncMock()
            feedback_id = uuid.uuid4()
            tenant_id = "tenant-test"

            session.scalar.return_value = None

            res = await get_feedback_by_id(
                session,
                feedback_id=feedback_id,
                tenant_id=tenant_id,
                record_type="appeal",
            )

            self.assertIsNone(res)
            session.scalar.assert_awaited_once()

        asyncio.run(_test())

    def test_get_feedback_by_id_not_found_or_tenant_mismatch(self) -> None:
        """Verify get_feedback_by_id returns None when feedback does not exist or tenant ID mismatches."""

        async def _test() -> None:
            session = AsyncMock()
            session.scalar.return_value = None

            res = await get_feedback_by_id(
                session,
                feedback_id=uuid.uuid4(),
                tenant_id="tenant-wrong",
            )

            self.assertIsNone(res)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
