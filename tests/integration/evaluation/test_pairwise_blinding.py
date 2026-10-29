"""Integration tests for app/evaluation/pairwise_blinding.py."""

from __future__ import annotations

import pytest

from app.evaluation.pairwise_blinding import (
    create_blinded_pairs,
    score_blinded_judgments,
)


class TestCreateBlindedPairs:
    """Test suite for anonymizing candidate responses and constructing public/private task pairs."""

    def test_create_blinded_pairs_success(self) -> None:
        """Verify creation of public review tasks and private identity mappings."""
        candidate_a = [
            {
                "case_id": "c1",
                "model_version": "model-a-v1",
                "prompt_version": "prompt-v1",
                "provider": "openai",
                "latency_ms": 500,
                "prediction": {
                    "experience_level": "Senior-level",
                    "skills_match": "Match",
                    "recommendation": "recruiter_review",
                    "experience_evidence": ["5 years experience"],
                    "skills_evidence": ["Python"],
                    "skill_gaps": [],
                    "experience_confidence": 0.9,
                    "skills_confidence": 0.9,
                },
            }
        ]
        candidate_b = [
            {
                "case_id": "c1",
                "model_version": "model-b-v2",
                "prompt_version": "prompt-v2",
                "provider": "anthropic",
                "latency_ms": 700,
                "prediction": {
                    "experience_level": "Mid-level",
                    "skills_match": "Partial match",
                    "recommendation": "hr_review",
                    "experience_evidence": ["3 years experience"],
                    "skills_evidence": ["JavaScript"],
                    "skill_gaps": ["Docker"],
                    "experience_confidence": 0.8,
                    "skills_confidence": 0.8,
                },
            }
        ]

        public_tasks, private_mapping = create_blinded_pairs(
            candidate_a=candidate_a,
            candidate_b=candidate_b,
            candidate_a_version="model-a-v1",
            candidate_b_version="model-b-v2",
            seed=42,
        )

        assert len(public_tasks) == 1
        assert len(private_mapping) == 1

        task = public_tasks[0]
        mapping = private_mapping[0]

        assert task["pair_id"] == mapping["pair_id"]
        assert mapping["case_id"] == "c1"

        # Verify private metadata is stripped from public tasks
        cand_a_public = task["candidate_a"]
        cand_b_public = task["candidate_b"]

        for public_resp in (cand_a_public, cand_b_public):
            assert "model_version" not in public_resp
            assert "prompt_version" not in public_resp
            assert "provider" not in public_resp
            assert "case_id" not in public_resp
            assert "latency_ms" not in public_resp
            assert "estimated_cost_usd" not in public_resp

        # Check allowed public fields are present
        assert "experience_level" in cand_a_public
        assert "skills_match" in cand_a_public
        assert "recommendation" in cand_a_public

    def test_create_blinded_pairs_with_review_context(self) -> None:
        """Verify handling of include_review_context when review_context is present and matching."""
        cand_a = [
            {
                "case_id": "c1",
                "review_context": "Job Title: Senior Engineer",
                "experience_level": "Senior-level",
            }
        ]
        cand_b = [
            {
                "case_id": "c1",
                "review_context": "Job Title: Senior Engineer",
                "experience_level": "Mid-level",
            }
        ]

        public_tasks, _ = create_blinded_pairs(
            candidate_a=cand_a,
            candidate_b=cand_b,
            candidate_a_version="v1",
            candidate_b_version="v2",
            include_review_context=True,
        )

        assert public_tasks[0]["review_context"] == "Job Title: Senior Engineer"

    def test_create_blinded_pairs_review_context_mismatch_raises(self) -> None:
        """Verify ValueError is raised if review_context differs between candidates for the same case_id."""
        cand_a = [{"case_id": "c1", "review_context": "Context A"}]
        cand_b = [{"case_id": "c1", "review_context": "Context B"}]

        with pytest.raises(ValueError, match="Review context mismatch for case_id=c1"):
            create_blinded_pairs(
                candidate_a=cand_a,
                candidate_b=cand_b,
                candidate_a_version="v1",
                candidate_b_version="v2",
                include_review_context=True,
            )

    def test_create_blinded_pairs_non_unique_case_id_raises(self) -> None:
        """Verify ValueError is raised when input rows have duplicate or non-string case_ids."""
        duplicate_a = [{"case_id": "c1"}, {"case_id": "c1"}]
        valid_b = [{"case_id": "c1"}]

        with pytest.raises(
            ValueError,
            match="Each pairwise prediction row must have a unique string case_id",
        ):
            create_blinded_pairs(
                candidate_a=duplicate_a,
                candidate_b=valid_b,
                candidate_a_version="v1",
                candidate_b_version="v2",
            )

    def test_create_blinded_pairs_no_shared_case_id_raises(self) -> None:
        """Verify ValueError is raised when candidates share no case_ids."""
        cand_a = [{"case_id": "c1"}]
        cand_b = [{"case_id": "c2"}]

        with pytest.raises(
            ValueError, match="Candidate files do not contain a shared case_id"
        ):
            create_blinded_pairs(
                candidate_a=cand_a,
                candidate_b=cand_b,
                candidate_a_version="v1",
                candidate_b_version="v2",
            )


class TestScoreBlindedJudgments:
    """Test suite for unblinding reviewer judgments and aggregating preference statistics."""

    def test_score_blinded_judgments_aggregation(self) -> None:
        """Verify unblinding completed tasks and aggregating model wins, ties, and missing mappings."""
        private_mapping = [
            {
                "pair_id": "pair-1",
                "case_id": "c1",
                "candidate_a_version": "model-v1",
                "candidate_b_version": "model-v2",
                "swap_applied": False,
            },
            {
                "pair_id": "pair-2",
                "case_id": "c2",
                "candidate_a_version": "model-v2",  # Swapped: candidate_a is model-v2
                "candidate_b_version": "model-v1",
                "swap_applied": True,
            },
            {
                "pair_id": "pair-3",
                "case_id": "c3",
                "candidate_a_version": "model-v1",
                "candidate_b_version": "model-v2",
                "swap_applied": False,
            },
            {
                "pair_id": "pair-4",
                "case_id": "c4",
                "candidate_a_version": "model-v1",
                "candidate_b_version": "model-v2",
                "swap_applied": False,
            },
        ]

        public_tasks = [
            {"pair_id": "pair-1", "judgment": {"winner": "a"}},  # Winner: model-v1
            {"pair_id": "pair-2", "judgment": {"winner": "a"}},  # Winner: model-v2
            {"pair_id": "pair-3", "judgment": {"winner": "tie"}},  # Tie
            {"pair_id": "pair-4", "judgment": {"winner": "both_bad"}},  # Both bad
            {"pair_id": "pair-unknown", "judgment": {"winner": "a"}},  # Missing mapping
            {"pair_id": "pair-1", "judgment": {"winner": "invalid_winner"}},  # Skipped
        ]

        summary = score_blinded_judgments(
            public_tasks=public_tasks,
            private_mapping=private_mapping,
        )

        assert summary["report_version"] == "blinded-pairwise-summary-v1"
        assert summary["completed_pairs"] == 4
        assert summary["ties_or_both_bad"] == 2
        assert summary["model_wins"] == {"model-v1": 1, "model-v2": 1}
        assert summary["mapping_missing_pair_ids"] == ["pair-unknown"]
