"""Integration tests for evaluation CLI scripts."""

import json
import sys
from pathlib import Path

import pytest

from app.scripts import (
    compare_evaluations,
    create_pairwise_review,
    evaluate_offline,
    score_pairwise_review,
)


def test_evaluate_offline_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test evaluate_offline script execution against gold dataset and predictions."""
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "predictions.jsonl"
    output_path = tmp_path / "report.json"

    gold_rows = [
        {
            "case_id": "case-001",
            "gold": {
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "hr_review",
            },
            "source_text": "Senior Python developer with 8 years experience in FastAPI.",
        },
        {
            "case_id": "case-002",
            "gold": {
                "experience_level": "Mid-level",
                "skills_match": "Partial match",
                "recommendation": "recruiter_review",
            },
            "source_text": "Mid-level developer with 3 years Python experience.",
        },
    ]

    pred_rows = [
        {
            "case_id": "case-001",
            "experience_level": "Senior-level",
            "skills_match": "Match",
            "recommendation": "hr_review",
            "model_version": "gpt-4o-mini",
            "prompt_version": "v1.0",
        },
        {
            "case_id": "case-002",
            "experience_level": "Mid-level",
            "skills_match": "Partial match",
            "recommendation": "recruiter_review",
            "model_version": "gpt-4o-mini",
            "prompt_version": "v1.0",
        },
    ]

    gold_path.write_text(
        "\n".join(json.dumps(row) for row in gold_rows) + "\n", encoding="utf-8"
    )
    pred_path.write_text(
        "\n".join(json.dumps(row) for row in pred_rows) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_offline",
            "--dataset",
            str(gold_path),
            "--predictions",
            str(pred_path),
            "--output",
            str(output_path),
            "--bootstrap-iterations",
            "100",
        ],
    )

    exit_code = evaluate_offline.main()
    assert exit_code == 0
    assert output_path.exists()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["sample_count"] == 2
    assert report["missing_case_ids"] == []
    assert report["extra_case_ids"] == []
    assert report["metrics"]["experience_accuracy"]["accuracy"] == 1.0


def test_compare_evaluations_release_gate_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test compare_evaluations returning exit code 0 when release gate passes."""
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "release-gate.json"

    baseline_data = {
        "model_version": "baseline-v1",
        "prompt_version": "p1",
        "metrics": {
            "schema_validity_rate": 1.0,
            "experience_accuracy": {"accuracy": 0.90},
            "skills_accuracy": {"accuracy": 0.85},
            "recommendation_accuracy": {"accuracy": 0.88},
            "mean_latency_ms": 1000.0,
            "mean_estimated_cost_usd": 0.01,
        },
        "prediction_distributions": {
            "experience_level": {"Senior-level": 10},
            "skills_match": {"Match": 10},
            "recommendation": {"hr_review": 10},
        },
    }

    candidate_data = {
        "model_version": "candidate-v2",
        "prompt_version": "p2",
        "metrics": {
            "schema_validity_rate": 1.0,
            "experience_accuracy": {"accuracy": 0.89},
            "skills_accuracy": {"accuracy": 0.85},
            "recommendation_accuracy": {"accuracy": 0.87},
            "mean_latency_ms": 1050.0,
            "mean_estimated_cost_usd": 0.01,
        },
        "prediction_distributions": {
            "experience_level": {"Senior-level": 10},
            "skills_match": {"Match": 10},
            "recommendation": {"hr_review": 10},
        },
        "slices": {},
    }

    baseline_path.write_text(json.dumps(baseline_data), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate_data), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_evaluations",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = compare_evaluations.main()
    assert exit_code == 0
    assert output_path.exists()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["failures"] == []


def test_compare_evaluations_release_gate_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test compare_evaluations returning exit code 2 when accuracy regresses."""
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output_path = tmp_path / "release-gate.json"

    baseline_data = {
        "model_version": "baseline-v1",
        "prompt_version": "p1",
        "metrics": {
            "schema_validity_rate": 1.0,
            "experience_accuracy": {"accuracy": 0.90},
            "skills_accuracy": {"accuracy": 0.85},
            "recommendation_accuracy": {"accuracy": 0.88},
        },
    }

    candidate_data = {
        "model_version": "candidate-v2",
        "prompt_version": "p2",
        "metrics": {
            "schema_validity_rate": 1.0,
            "experience_accuracy": {"accuracy": 0.70},
            "skills_accuracy": {"accuracy": 0.85},
            "recommendation_accuracy": {"accuracy": 0.88},
        },
    }

    baseline_path.write_text(json.dumps(baseline_data), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate_data), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_evaluations",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--output",
            str(output_path),
            "--max-accuracy-drop",
            "0.03",
        ],
    )

    exit_code = compare_evaluations.main()
    assert exit_code == 2
    assert output_path.exists()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert len(report["failures"]) >= 1
    assert "experience_accuracy regressed" in report["failures"][0]


def test_create_pairwise_review_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test create_pairwise_review script creating public review and private map files."""
    cand_a_path = tmp_path / "cand_a.jsonl"
    cand_b_path = tmp_path / "cand_b.jsonl"
    public_path = tmp_path / "public_review.json"
    private_path = tmp_path / "private_map.json"

    cand_a_rows = [
        {
            "case_id": "case-100",
            "experience_level": "Senior-level",
            "skills_match": "Match",
            "recommendation": "hr_review",
            "model_version": "gpt-4o",
            "prompt_version": "p1",
        }
    ]
    cand_b_rows = [
        {
            "case_id": "case-100",
            "experience_level": "Mid-level",
            "skills_match": "Partial match",
            "recommendation": "recruiter_review",
            "model_version": "claude-3-5",
            "prompt_version": "p2",
        }
    ]

    cand_a_path.write_text(
        "\n".join(json.dumps(r) for r in cand_a_rows) + "\n", encoding="utf-8"
    )
    cand_b_path.write_text(
        "\n".join(json.dumps(r) for r in cand_b_rows) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_pairwise_review",
            "--candidate-a",
            str(cand_a_path),
            "--candidate-b",
            str(cand_b_path),
            "--candidate-a-version",
            "gpt-4o",
            "--candidate-b-version",
            "claude-3-5",
            "--public-output",
            str(public_path),
            "--private-map-output",
            str(private_path),
            "--seed",
            "42",
        ],
    )

    exit_code = create_pairwise_review.main()
    assert exit_code == 0
    assert public_path.exists()
    assert private_path.exists()

    public_data = json.loads(public_path.read_text(encoding="utf-8"))
    assert len(public_data["tasks"]) == 1
    task = public_data["tasks"][0]
    assert "candidate_a" in task
    assert "candidate_b" in task
    assert "model_version" not in task["candidate_a"]
    assert "prompt_version" not in task["candidate_a"]

    private_data = json.loads(private_path.read_text(encoding="utf-8"))
    assert len(private_data["private_mapping"]) == 1
    mapping = private_data["private_mapping"][0]
    assert mapping["case_id"] == "case-100"

    assert private_path.stat().st_mode & 0o777 == 0o600


def test_score_pairwise_review_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test score_pairwise_review script unblinding and aggregating reviewer preferences."""
    public_path = tmp_path / "public.json"
    private_path = tmp_path / "private.json"
    output_path = tmp_path / "summary.json"

    public_data = {
        "review_version": "blinded-pairwise-v1",
        "tasks": [
            {
                "pair_id": "pair-001",
                "candidate_a": {"experience_level": "Senior-level"},
                "candidate_b": {"experience_level": "Mid-level"},
                "judgment": {"winner": "a", "reason_code": "better_grounding"},
            }
        ],
    }

    private_data = {
        "private_mapping": [
            {
                "pair_id": "pair-001",
                "case_id": "case-100",
                "candidate_a_version": "gpt-4o",
                "candidate_b_version": "claude-3-5",
                "swap_applied": False,
            }
        ]
    }

    public_path.write_text(json.dumps(public_data), encoding="utf-8")
    private_path.write_text(json.dumps(private_data), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score_pairwise_review",
            "--public-review",
            str(public_path),
            "--private-map",
            str(private_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = score_pairwise_review.main()
    assert exit_code == 0
    assert output_path.exists()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["completed_pairs"] == 1
    assert report["model_wins"]["gpt-4o"] == 1
    assert report["mapping_missing_pair_ids"] == []
