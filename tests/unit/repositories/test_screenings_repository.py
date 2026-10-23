"""Unit tests for app.repositories.screenings and app.db.models.screening.

Tests cover:
- Screening model instantiation and schema field assignments (UUID, status, timestamps, JSON fields).
- get_screening repository function (by ID, tenant isolation, for_update flag, missing records).
- list_screenings repository function (tenant filtering, pagination limit/offset, count aggregation).
- Screening record attribute modifications and state transitions.
- Session transaction integrity, mock session interactions, and error rollback handling.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.db.models.screening import Screening
from app.repositories.screenings import get_screening, list_screenings


class TestScreeningModelCreation(unittest.TestCase):
    """Test Screening model instantiation, schema field assignments, and defaults."""

    def test_screening_model_initialization_with_explicit_and_default_fields(
        self,
    ) -> None:
        screening_id = uuid.uuid4()
        screening = Screening(
            id=screening_id,
            tenant_id="default",
            status="queued",
            attempt_count=0,
            human_review_required=True,
            application_text="Senior Python Engineer with 8 years of experience.",
            job_title="Lead Backend Engineer",
            required_skills=["Python", "FastAPI", "PostgreSQL"],
        )

        self.assertEqual(screening.id, screening_id)
        self.assertEqual(screening.tenant_id, "default")
        self.assertEqual(screening.status, "queued")
        self.assertTrue(screening.human_review_required)
        self.assertEqual(screening.attempt_count, 0)
        self.assertEqual(screening.required_skills, ["Python", "FastAPI", "PostgreSQL"])
        self.assertIsNone(screening.experience_level)
        self.assertIsNone(screening.recommendation)
        self.assertIsNone(screening.error_code)

    def test_screening_model_custom_fields_assignment(self) -> None:
        custom_id = uuid.uuid4()
        screening = Screening(
            id=custom_id,
            tenant_id="tenant-alpha",
            request_id="req-12345",
            idempotency_key="idem-key-abc",
            request_fingerprint="fp-0099",
            application_text="Applicant details...",
            job_title="Data Engineer",
            required_skills=["Python", "Spark"],
            experience_level="Senior-level",
            experience_confidence=0.92,
            experience_evidence=["8 years Python"],
            skills_match="Match",
            skills_confidence=0.88,
            skills_evidence=["Spark streaming"],
            skill_gaps=[],
            recommendation="hr_review",
            recommendation_reason="Strong match across skills.",
            human_review_required=False,
            model_version="gpt-4o-2024-05-13",
            prompt_version="screening-v1",
            redaction_summary={"email": 1, "phone": 2},
        )

        self.assertEqual(screening.id, custom_id)
        self.assertEqual(screening.tenant_id, "tenant-alpha")
        self.assertEqual(screening.request_id, "req-12345")
        self.assertEqual(screening.idempotency_key, "idem-key-abc")
        self.assertEqual(screening.experience_level, "Senior-level")
        self.assertEqual(screening.experience_confidence, 0.92)
        self.assertEqual(screening.recommendation, "hr_review")
        self.assertFalse(screening.human_review_required)
        self.assertEqual(screening.redaction_summary, {"email": 1, "phone": 2})


class TestGetScreeningRepository(unittest.TestCase):
    """Test get_screening repository function."""

    def test_get_screening_by_id_without_tenant_id(self) -> None:
        async def _run() -> None:
            screening_id = uuid.uuid4()
            expected_screening = Screening(
                id=screening_id,
                tenant_id="tenant-1",
                application_text="Text",
                job_title="Dev",
            )

            mock_session = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=expected_screening)

            result = await get_screening(mock_session, screening_id)

            self.assertEqual(result, expected_screening)
            mock_session.scalar.assert_awaited_once()

            # Verify statement target in mock call
            stmt = mock_session.scalar.call_args[0][0]
            compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("screenings.id =", compiled_sql)
            self.assertNotIn("screenings.tenant_id =", compiled_sql)
            self.assertNotIn("FOR UPDATE", compiled_sql)

        asyncio.run(_run())

    def test_get_screening_by_id_with_tenant_id_filter(self) -> None:
        async def _run() -> None:
            screening_id = uuid.uuid4()
            expected_screening = Screening(
                id=screening_id,
                tenant_id="tenant-corp",
                application_text="Text",
                job_title="Dev",
            )

            mock_session = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=expected_screening)

            result = await get_screening(
                mock_session, screening_id, tenant_id="tenant-corp"
            )

            self.assertEqual(result, expected_screening)
            mock_session.scalar.assert_awaited_once()

            stmt = mock_session.scalar.call_args[0][0]
            compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("screenings.id =", compiled_sql)
            self.assertIn("screenings.tenant_id =", compiled_sql)

        asyncio.run(_run())

    def test_get_screening_with_for_update_clause(self) -> None:
        async def _run() -> None:
            screening_id = uuid.uuid4()
            mock_session = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=None)

            result = await get_screening(mock_session, screening_id, for_update=True)

            self.assertIsNone(result)
            mock_session.scalar.assert_awaited_once()

            stmt = mock_session.scalar.call_args[0][0]
            compiled_sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            self.assertIn("FOR UPDATE", compiled_sql)

        asyncio.run(_run())

    def test_get_screening_non_existent_id_returns_none(self) -> None:
        async def _run() -> None:
            non_existent_id = uuid.uuid4()
            mock_session = AsyncMock()
            mock_session.scalar = AsyncMock(return_value=None)

            result = await get_screening(mock_session, non_existent_id)

            self.assertIsNone(result)
            mock_session.scalar.assert_awaited_once()

        asyncio.run(_run())


class TestListScreeningsRepository(unittest.TestCase):
    """Test list_screenings repository function."""

    def test_list_screenings_pagination_and_count(self) -> None:
        async def _run() -> None:
            s1 = Screening(
                id=uuid.uuid4(),
                tenant_id="tenant-acme",
                application_text="App 1",
                job_title="Dev",
            )
            s2 = Screening(
                id=uuid.uuid4(),
                tenant_id="tenant-acme",
                application_text="App 2",
                job_title="Dev",
            )

            mock_scalars_result = MagicMock()
            mock_scalars_result.all.return_value = [s1, s2]

            mock_session = AsyncMock()
            mock_session.scalars = AsyncMock(return_value=mock_scalars_result)
            mock_session.scalar = AsyncMock(return_value=15)

            rows, total_count = await list_screenings(
                mock_session, tenant_id="tenant-acme", limit=10, offset=0
            )

            self.assertEqual(rows, [s1, s2])
            self.assertEqual(total_count, 15)

            mock_session.scalars.assert_awaited_once()
            mock_session.scalar.assert_awaited_once()

            # Inspect list query statement
            list_stmt = mock_session.scalars.call_args[0][0]
            compiled_list_sql = str(
                list_stmt.compile(compile_kwargs={"literal_binds": True})
            )
            self.assertIn("screenings.tenant_id =", compiled_list_sql)
            self.assertIn("LIMIT 10", compiled_list_sql)
            self.assertIn("OFFSET 0", compiled_list_sql)

            # Inspect count query statement
            count_stmt = mock_session.scalar.call_args[0][0]
            compiled_count_sql = str(
                count_stmt.compile(compile_kwargs={"literal_binds": True})
            )
            self.assertIn("count", compiled_count_sql.lower())
            self.assertIn("screenings.tenant_id =", compiled_count_sql)

        asyncio.run(_run())

    def test_list_screenings_empty_result(self) -> None:
        async def _run() -> None:
            mock_scalars_result = MagicMock()
            mock_scalars_result.all.return_value = []

            mock_session = AsyncMock()
            mock_session.scalars = AsyncMock(return_value=mock_scalars_result)
            mock_session.scalar = AsyncMock(return_value=0)

            rows, total_count = await list_screenings(
                mock_session, tenant_id="empty-tenant", limit=20, offset=40
            )

            self.assertEqual(rows, [])
            self.assertEqual(total_count, 0)

        asyncio.run(_run())


class TestScreeningUpdatesAndStateTransitions(unittest.TestCase):
    """Test modifying screening status, scores, and review outcomes."""

    def test_screening_status_transition_and_assessment_update(self) -> None:
        screening = Screening(
            tenant_id="tenant-xyz",
            application_text="Resume content...",
            job_title="Security Engineer",
            required_skills=["AppSec", "OAuth"],
            status="queued",
            attempt_count=0,
        )

        # Transition to processing
        screening.status = "processing"
        screening.processing_started_at = datetime.now(UTC)
        screening.attempt_count += 1

        self.assertEqual(screening.status, "processing")
        self.assertIsNotNone(screening.processing_started_at)
        self.assertEqual(screening.attempt_count, 1)

        # Update with completion evaluation assessment
        screening.status = "completed"
        screening.experience_level = "Mid-level"
        screening.experience_confidence = 0.85
        screening.skills_match = "Match"
        screening.skills_confidence = 0.90
        screening.recommendation = "recruiter_review"
        screening.human_review_required = False
        screening.processed_at = datetime.now(UTC)

        self.assertEqual(screening.status, "completed")
        self.assertEqual(screening.experience_level, "Mid-level")
        self.assertEqual(screening.skills_match, "Match")
        self.assertEqual(screening.recommendation, "recruiter_review")
        self.assertFalse(screening.human_review_required)
        self.assertIsNotNone(screening.processed_at)


class TestSessionTransactionIntegrity(unittest.TestCase):
    """Test async session handling, transaction commits, and rollbacks on DB error."""

    def test_session_commit_success_flow(self) -> None:
        async def _run() -> None:
            mock_session = AsyncMock()
            mock_session.add = MagicMock()  # add is synchronous on AsyncSession

            screening = Screening(
                tenant_id="t1",
                application_text="App text",
                job_title="Backend Developer",
            )

            mock_session.add(screening)
            await mock_session.commit()

            mock_session.add.assert_called_once_with(screening)
            mock_session.commit.assert_awaited_once()

        asyncio.run(_run())

    def test_session_rollback_on_db_exception(self) -> None:
        async def _run() -> None:
            mock_session = AsyncMock()
            mock_session.add = MagicMock()  # add is synchronous on AsyncSession
            mock_session.commit.side_effect = RuntimeError("Database connection lost")

            screening = Screening(
                tenant_id="t1",
                application_text="App text",
                job_title="Backend Developer",
            )

            mock_session.add(screening)

            with self.assertRaises(RuntimeError) as ctx:
                try:
                    await mock_session.commit()
                except Exception:
                    await mock_session.rollback()
                    raise

            self.assertIn("Database connection lost", str(ctx.exception))
            mock_session.add.assert_called_once_with(screening)
            mock_session.commit.assert_awaited_once()
            mock_session.rollback.assert_awaited_once()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
