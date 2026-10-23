"""Unit tests for app.evaluation.pairwise_blinding.

Tests cover:
- Public response anonymization and filtering (_public_response).
- Blinded pair creation, deterministic hashing, and swapping (create_blinded_pairs).
- Review unblinding and preference judgment aggregation (score_blinded_judgments).
- Edge cases: duplicate/missing case IDs, context mismatches, missing mappings, and invalid winner codes.
"""

from __future__ import annotations

import unittest
from typing import Any

try:
    import pytest
except ImportError:
    pytest = None

from app.evaluation.pairwise_blinding import (
    _PRIVATE_FIELDS,
    _public_response,
    create_blinded_pairs,
    score_blinded_judgments,
)


class TestPublicResponseAnonymization(unittest.TestCase):
    """Test public response extraction and privacy filtering."""

    def test_public_response_strips_private_fields(self) -> None:
        """Verify sensitive metadata and operational fields are removed."""
        raw_row: dict[str, Any] = {
            "case_id": "case_101",
            "model_version": "gpt-4o-2024-05-13",
            "prompt_version": "v2.1",
            "provider": "openai",
            "provider_name": "OpenAI",
            "latency_ms": 420.5,
            "estimated_cost_usd": 0.0012,
            "judge_assessment": {"experience_level": "Senior-level"},
            "schema_valid": True,
            "schema_validity": True,
            # Allowed fields:
            "experience_level": "Senior-level",
            "skills_match": "Match",
            "recommendation": "hr_review",
            "experience_evidence": ["10 years Python experience"],
            "skills_evidence": ["Python", "FastAPI"],
            "skill_gaps": [],
            "experience_confidence": 0.95,
            "skills_confidence": 0.90,
        }

        public = _public_response(raw_row)

        # Ensure all private fields are excluded
        for private_key in _PRIVATE_FIELDS:
            self.assertNotIn(private_key, public)

        # Ensure allowed fields are preserved
        self.assertEqual(public["experience_level"], "Senior-level")
        self.assertEqual(public["skills_match"], "Match")
        self.assertEqual(public["recommendation"], "hr_review")
        self.assertEqual(public["experience_evidence"], ["10 years Python experience"])
        self.assertEqual(public["skills_evidence"], ["Python", "FastAPI"])
        self.assertEqual(public["skill_gaps"], [])
        self.assertEqual(public["experience_confidence"], 0.95)
        self.assertEqual(public["skills_confidence"], 0.90)

    def test_public_response_nested_prediction_object(self) -> None:
        """Verify handling when prediction payload is nested inside a 'prediction' key."""
        nested_row: dict[str, Any] = {
            "case_id": "case_102",
            "model_version": "candidate_a",
            "prediction": {
                "experience_level": "Mid-level",
                "skills_match": "Partial match",
                "recommendation": "recruiter_review",
                "model_version": "secret_inner_model",
                "latency_ms": 150.0,
            },
        }

        public = _public_response(nested_row)

        self.assertNotIn("model_version", public)
        self.assertNotIn("latency_ms", public)
        self.assertEqual(public["experience_level"], "Mid-level")
        self.assertEqual(public["skills_match"], "Partial match")
        self.assertEqual(public["recommendation"], "recruiter_review")

    def test_public_response_ignores_disallowed_custom_keys(self) -> None:
        """Verify unapproved arbitrary fields are excluded."""
        row: dict[str, Any] = {
            "experience_level": "Entry-level",
            "skills_match": "No match",
            "recommendation": "manual_review",
            "unapproved_secret": "sensitive_data",
            "applicant_name": "John Doe",
        }

        public = _public_response(row)

        self.assertNotIn("unapproved_secret", public)
        self.assertNotIn("applicant_name", public)
        self.assertEqual(set(public.keys()), {"experience_level", "skills_match", "recommendation"})


class TestCreateBlindedPairs(unittest.TestCase):
    """Test create_blinded_pairs function for pairing, swapping, and determinism."""

    def setUp(self) -> None:
        self.candidate_a: list[dict[str, Any]] = [
            {
                "case_id": "case_001",
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "hr_review",
                "model_version": "v1.0",
            },
            {
                "case_id": "case_002",
                "experience_level": "Entry-level",
                "skills_match": "No match",
                "recommendation": "manual_review",
                "model_version": "v1.0",
            },
        ]
        self.candidate_b: list[dict[str, Any]] = [
            {
                "case_id": "case_001",
                "experience_level": "Mid-level",
                "skills_match": "Partial match",
                "recommendation": "recruiter_review",
                "model_version": "v2.0",
            },
            {
                "case_id": "case_002",
                "experience_level": "Entry-level",
                "skills_match": "Match",
                "recommendation": "recruiter_review",
                "model_version": "v2.0",
            },
        ]

    def test_create_blinded_pairs_basic_structure(self) -> None:
        """Verify returned public tasks and private mapping structures."""
        public, private = create_blinded_pairs(
            self.candidate_a,
            self.candidate_b,
            candidate_a_version="v1.0",
            candidate_b_version="v2.0",
            seed=12345,
        )

        self.assertEqual(len(public), 2)
        self.assertEqual(len(private), 2)

        for task, mapping in zip(public, private, strict=True):
            self.assertEqual(task["pair_id"], mapping["pair_id"])
            self.assertIn("candidate_a", task)
            self.assertIn("candidate_b", task)
            self.assertEqual(task["judgment"], {"winner": None, "reason_code": None})
            self.assertIn(mapping["case_id"], ["case_001", "case_002"])
            self.assertIn(mapping["swap_applied"], [True, False])

    def test_create_blinded_pairs_deterministic_with_seed(self) -> None:
        """Verify identical seed produces identical swap decisions and mapping outputs."""
        public1, private1 = create_blinded_pairs(
            self.candidate_a,
            self.candidate_b,
            candidate_a_version="v1.0",
            candidate_b_version="v2.0",
            seed=42,
        )
        public2, private2 = create_blinded_pairs(
            self.candidate_a,
            self.candidate_b,
            candidate_a_version="v1.0",
            candidate_b_version="v2.0",
            seed=42,
        )

        # Swaps and version maps should match exactly
        swaps1 = [item["swap_applied"] for item in private1]
        swaps2 = [item["swap_applied"] for item in private2]
        self.assertEqual(swaps1, swaps2)

        for p1, p2 in zip(public1, public2, strict=True):
            self.assertEqual(p1["candidate_a"], p2["candidate_a"])
            self.assertEqual(p1["candidate_b"], p2["candidate_b"])

    def test_create_blinded_pairs_swapping_mechanics(self) -> None:
        """Verify candidate output order flips correctly when swap is applied."""
        public, private = create_blinded_pairs(
            self.candidate_a,
            self.candidate_b,
            candidate_a_version="v1.0",
            candidate_b_version="v2.0",
            seed=1,
        )

        for task, mapping in zip(public, private, strict=True):
            case_id = mapping["case_id"]
            orig_a = next(r for r in self.candidate_a if r["case_id"] == case_id)
            orig_b = next(r for r in self.candidate_b if r["case_id"] == case_id)

            if mapping["swap_applied"]:
                self.assertEqual(mapping["candidate_a_version"], "v2.0")
                self.assertEqual(mapping["candidate_b_version"], "v1.0")
                self.assertEqual(task["candidate_a"]["experience_level"], orig_b["experience_level"])
                self.assertEqual(task["candidate_b"]["experience_level"], orig_a["experience_level"])
            else:
                self.assertEqual(mapping["candidate_a_version"], "v1.0")
                self.assertEqual(mapping["candidate_b_version"], "v2.0")
                self.assertEqual(task["candidate_a"]["experience_level"], orig_a["experience_level"])
                self.assertEqual(task["candidate_b"]["experience_level"], orig_b["experience_level"])

    def test_create_blinded_pairs_with_review_context(self) -> None:
        """Verify pre-approved review context is included when requested."""
        cand_a = [dict(self.candidate_a[0], review_context="Role: Senior Python Dev")]
        cand_b = [dict(self.candidate_b[0], review_context="Role: Senior Python Dev")]

        public, _ = create_blinded_pairs(
            cand_a,
            cand_b,
            candidate_a_version="v1.0",
            candidate_b_version="v2.0",
            include_review_context=True,
        )

        self.assertEqual(public[0]["review_context"], "Role: Senior Python Dev")

    def test_create_blinded_pairs_review_context_mismatch_raises(self) -> None:
        """Verify mismatch in review context for the same case raises ValueError."""
        cand_a = [dict(self.candidate_a[0], review_context="Role: Frontend")]
        cand_b = [dict(self.candidate_b[0], review_context="Role: Backend")]

        with self.assertRaises(ValueError) as ctx:
            create_blinded_pairs(
                cand_a,
                cand_b,
                candidate_a_version="v1.0",
                candidate_b_version="v2.0",
                include_review_context=True,
            )
        self.assertIn("Review context mismatch for case_id=case_001", str(ctx.exception))

    def test_create_blinded_pairs_missing_or_non_string_case_id_raises(self) -> None:
        """Verify invalid or non-string case_id raises ValueError."""
        invalid_cand_a = [{"case_id": 123, "experience_level": "Senior-level"}]
        invalid_cand_b = [{"case_id": "123", "experience_level": "Senior-level"}]

        with self.assertRaises(ValueError) as ctx:
            create_blinded_pairs(
                invalid_cand_a,  # type: ignore[arg-type]
                invalid_cand_b,
                candidate_a_version="v1.0",
                candidate_b_version="v2.0",
            )
        self.assertIn("Each pairwise prediction row must have a unique string case_id", str(ctx.exception))

    def test_create_blinded_pairs_duplicate_case_id_raises(self) -> None:
        """Verify duplicate case_id in input raises ValueError."""
        dup_a = [self.candidate_a[0], self.candidate_a[0]]  # both case_001

        with self.assertRaises(ValueError) as ctx:
            create_blinded_pairs(
                dup_a,
                self.candidate_b,
                candidate_a_version="v1.0",
                candidate_b_version="v2.0",
            )
        self.assertIn("Each pairwise prediction row must have a unique string case_id", str(ctx.exception))

    def test_create_blinded_pairs_no_shared_case_ids_raises(self) -> None:
        """Verify exception when candidates share no case IDs."""
        a = [{"case_id": "case_100", "experience_level": "Mid-level"}]
        b = [{"case_id": "case_200", "experience_level": "Mid-level"}]

        with self.assertRaises(ValueError) as ctx:
            create_blinded_pairs(
                a,
                b,
                candidate_a_version="v1.0",
                candidate_b_version="v2.0",
            )
        self.assertIn("Candidate files do not contain a shared case_id", str(ctx.exception))


class TestScoreBlindedJudgments(unittest.TestCase):
    """Test score_blinded_judgments for unblinding and win calculation."""

    def test_score_blinded_judgments_unblinding_success(self) -> None:
        """Verify correct aggregation of winner votes back to original model versions."""
        private_mapping = [
            {
                "pair_id": "p1",
                "case_id": "case_001",
                "candidate_a_version": "model_v1",
                "candidate_b_version": "model_v2",
                "swap_applied": False,
            },
            {
                "pair_id": "p2",
                "case_id": "case_002",
                "candidate_a_version": "model_v2",  # swapped
                "candidate_b_version": "model_v1",
                "swap_applied": True,
            },
            {
                "pair_id": "p3",
                "case_id": "case_003",
                "candidate_a_version": "model_v1",
                "candidate_b_version": "model_v2",
                "swap_applied": False,
            },
            {
                "pair_id": "p4",
                "case_id": "case_004",
                "candidate_a_version": "model_v1",
                "candidate_b_version": "model_v2",
                "swap_applied": False,
            },
        ]

        public_tasks = [
            {"pair_id": "p1", "judgment": {"winner": "a", "reason_code": "better_evidence"}},  # wins model_v1
            {"pair_id": "p2", "judgment": {"winner": "a", "reason_code": "clearer_summary"}},  # wins model_v2 (swapped)
            {"pair_id": "p3", "judgment": {"winner": "tie"}},
            {"pair_id": "p4", "judgment": {"winner": "both_bad"}},
        ]

        result = score_blinded_judgments(public_tasks, private_mapping)

        self.assertEqual(result["report_version"], "blinded-pairwise-summary-v1")
        self.assertEqual(result["completed_pairs"], 4)
        self.assertEqual(result["ties_or_both_bad"], 2)
        self.assertEqual(result["model_wins"], {"model_v1": 1, "model_v2": 1})
        self.assertEqual(result["mapping_missing_pair_ids"], [])

    def test_score_blinded_judgments_unmapped_pair_ids(self) -> None:
        """Verify tasks with pair_ids missing from private mapping are tracked."""
        private_mapping = [
            {
                "pair_id": "p1",
                "case_id": "case_001",
                "candidate_a_version": "v1",
                "candidate_b_version": "v2",
            }
        ]
        public_tasks = [
            {"pair_id": "p1", "judgment": {"winner": "a"}},
            {"pair_id": "p_ghost", "judgment": {"winner": "b"}},
        ]

        result = score_blinded_judgments(public_tasks, private_mapping)

        self.assertEqual(result["completed_pairs"], 1)
        self.assertEqual(result["mapping_missing_pair_ids"], ["p_ghost"])

    def test_score_blinded_judgments_skips_invalid_or_missing_winner_codes(self) -> None:
        """Verify invalid or unpopulated winner fields are skipped."""
        private_mapping = [
            {
                "pair_id": "p1",
                "case_id": "case_001",
                "candidate_a_version": "v1",
                "candidate_b_version": "v2",
            }
        ]
        public_tasks = [
            {"pair_id": "p1", "judgment": {"winner": "invalid_winner"}},
            {"pair_id": "p1", "judgment": {}},  # winner None
        ]

        result = score_blinded_judgments(public_tasks, private_mapping)

        self.assertEqual(result["completed_pairs"], 0)
        self.assertEqual(result["model_wins"], {})

    def test_score_blinded_judgments_empty_input(self) -> None:
        """Verify handling of empty task and mapping lists."""
        result = score_blinded_judgments([], [])

        self.assertEqual(result["completed_pairs"], 0)
        self.assertEqual(result["ties_or_both_bad"], 0)
        self.assertEqual(result["model_wins"], {})
        self.assertEqual(result["mapping_missing_pair_ids"], [])


if __name__ == "__main__":
    unittest.main()
