"""Blind paired model/prompt outputs for human preference review.

The public artifact contains only opaque pair IDs and anonymized response bodies. The identity mapping
must be stored separately with restricted access. The routine assumes inputs were de-identified and
approved for reviewer use; it deliberately does not export application source text by default.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

_PRIVATE_FIELDS = {
    "model_version",
    "prompt_version",
    "provider",
    "provider_name",
    "case_id",
    "latency_ms",
    "estimated_cost_usd",
    "judge_assessment",
    "schema_valid",
    "schema_validity",
}


def _public_response(row: dict[str, Any]) -> dict[str, Any]:

    payload = row.get("prediction") if isinstance(row.get("prediction"), dict) else row

    allowed = {
        "experience_level",
        "skills_match",
        "recommendation",
        "experience_evidence",
        "skills_evidence",
        "skill_gaps",
        "experience_confidence",
        "skills_confidence",
    }

    return {key: value for key, value in payload.items() if key in allowed}


def create_blinded_pairs(
    candidate_a: list[dict[str, Any]],
    candidate_b: list[dict[str, Any]],
    *,
    candidate_a_version: str,
    candidate_b_version: str,
    seed: int | None = None,
    include_review_context: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return public review tasks and a private key mapping; input candidates must share case IDs."""

    a_by_id = {
        row.get("case_id"): row
        for row in candidate_a
        if isinstance(row.get("case_id"), str)
    }
    b_by_id = {
        row.get("case_id"): row
        for row in candidate_b
        if isinstance(row.get("case_id"), str)
    }

    if len(a_by_id) != len(candidate_a) or len(b_by_id) != len(candidate_b):
        raise ValueError(
            "Each pairwise prediction row must have a unique string case_id"
        )

    common_ids = sorted(set(a_by_id).intersection(b_by_id))

    if not common_ids:
        raise ValueError("Candidate files do not contain a shared case_id")

    rng = random.Random(seed)
    public: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []

    for case_id in common_ids:
        swap = bool(rng.getrandbits(1))
        model_a = candidate_b_version if swap else candidate_a_version
        model_b = candidate_a_version if swap else candidate_b_version
        row_a = a_by_id[case_id]
        row_b = b_by_id[case_id]
        output_a, output_b = _public_response(row_a), _public_response(row_b)
        if swap:
            output_a, output_b = output_b, output_a
        pair_id = str(uuid.uuid4())
        task: dict[str, Any] = {
            "pair_id": pair_id,
            "candidate_a": output_a,
            "candidate_b": output_b,
            "judgment": {"winner": None, "reason_code": None},
        }

        if include_review_context:
            context_a = row_a.get("review_context")
            context_b = row_b.get("review_context")

            if context_a and context_b and context_a != context_b:
                raise ValueError(f"Review context mismatch for case_id={case_id}")
            task["review_context"] = context_a or context_b or ""

        public.append(task)
        private.append(
            {
                "pair_id": pair_id,
                "case_id": case_id,
                "candidate_a_version": model_a,
                "candidate_b_version": model_b,
                "swap_applied": swap,
            }
        )

    return public, private


def score_blinded_judgments(
    public_tasks: list[dict[str, Any]], private_mapping: list[dict[str, Any]]
) -> dict[str, Any]:
    """Unblind a completed review and report aggregate preferences; never infer winner from model output."""

    mappings = {item["pair_id"]: item for item in private_mapping}
    model_wins: dict[str, int] = {}
    pair_results = 0
    ties = 0
    missing = []

    for task in public_tasks:
        pair_id = task.get("pair_id")
        mapping = mappings.get(pair_id)
        judgment = task.get("judgment") or {}
        winner = judgment.get("winner")

        if mapping is None:
            missing.append(pair_id)
            continue

        if winner not in {"a", "b", "tie", "both_bad"}:
            continue

        pair_results += 1
        if winner in {"tie", "both_bad"}:
            ties += 1
            continue

        version = mapping[f"candidate_{winner}_version"]
        model_wins[version] = model_wins.get(version, 0) + 1

    return {
        "report_version": "blinded-pairwise-summary-v1",
        "completed_pairs": pair_results,
        "ties_or_both_bad": ties,
        "model_wins": model_wins,
        "mapping_missing_pair_ids": missing,
        "notice": "Preference counts are reviewer signals, not a substitute for human-gold accuracy or fairness analysis.",
    }
