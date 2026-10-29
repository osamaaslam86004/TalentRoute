"""Integration tests for app/evaluation/evaluation_pipeline.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.evaluation_pipeline import (
    compare_reports,
    evaluate_predictions,
    load_jsonl,
    write_json,
)


class TestJSONLAndJSONUtils:
    """Test suite for file loading, serialization, and error handling functions."""

    def test_load_jsonl_valid(self, tmp_path: Path) -> None:
        """Verify reading a well-formed JSONL evaluation dataset file."""
        file_path = tmp_path / "dataset.jsonl"
        content = (
            '{"case_id": "c1", "gold": {"experience_level": "Senior-level"}}\n'
            '\n'
            '{"case_id": "c2", "gold": {"experience_level": "Entry-level"}}\n'
        )
        file_path.write_text(content, encoding="utf-8")

        rows = load_jsonl(file_path)

        assert len(rows) == 2
        assert rows[0]["case_id"] == "c1"
        assert rows[1]["case_id"] == "c2"

    def test_load_jsonl_invalid_json_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        """Verify that malformed JSON lines raise a descriptive ValueError with line number."""
        file_path = tmp_path / "corrupted.jsonl"
        content = (
            '{"case_id": "c1"}\n'
            '{"case_id": "c2", BROKEN_JSON}\n'
        )
        file_path.write_text(content, encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid JSONL at .*corrupted.jsonl:2"):
            load_jsonl(file_path)

    def test_load_jsonl_non_dict_record_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        """Verify that non-dictionary JSON records raise a ValueError."""
        file_path = tmp_path / "scalar.jsonl"
        file_path.write_text('"just a string"\n', encoding="utf-8")

        with pytest.raises(
            ValueError, match="Each JSONL record must be an object"
        ):
            load_jsonl(file_path)

    def test_write_json(self, tmp_path: Path) -> None:
        """Verify write_json creates target directories and formats UTF-8 JSON correctly."""
        output_path = tmp_path / "nested" / "reports" / "report.json"
        payload = {"report_version": "v1", "accuracy": 0.95, "title": "Evaluation"}

        write_json(output_path, payload)

        assert output_path.exists()
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data == payload


class TestEvaluatePredictions:
    """Test suite for evaluation pipeline metrics computation and edge cases."""

    def test_evaluate_predictions_basic(
        self,
        sample_gold_dataset: list[dict[str, Any]],
        sample_prediction_rows: list[dict[str, Any]],
    ) -> None:
        """Verify evaluation metrics computation including schema validity, accuracies, and CI."""
        report = evaluate_predictions(
            gold_dataset=sample_gold_dataset,
            prediction_rows=sample_prediction_rows,
            model_version="gpt-4o-2024-08-06",
            prompt_version="v1.2.0",
            bootstrap_iterations=200,
        )

        assert report["report_version"] == "screening-evaluation-report-v1"
        assert report["sample_count"] == 3
        assert report["gold_count"] == 3
        assert report["prediction_count"] == 3
        assert report["missing_case_ids"] == []
        assert report["extra_case_ids"] == []
        assert report["model_version"] == "gpt-4o-2024-08-06"
        assert report["prompt_version"] == "v1.2.0"

        metrics = report["metrics"]
        assert metrics["schema_validity_rate"] == 1.0
        assert metrics["schema_valid_count"] == 3
        assert metrics["schema_valid_total"] == 3

        # Check accuracies
        exp_acc = metrics["experience_accuracy"]
        assert exp_acc["accuracy"] == 1.0
        assert exp_acc["correct"] == 3
        assert exp_acc["total"] == 3
        assert exp_acc["ci_95"]["low_95"] is not None
        assert exp_acc["ci_95"]["high_95"] is not None

        # Check evidence grounding proxy
        assert metrics["evidence_grounding_lexical_proxy"] is not None
        assert 0.0 <= metrics["evidence_grounding_lexical_proxy"] <= 1.0

        # Check operational metrics
        assert metrics["mean_latency_ms"] == 1000.0
        assert metrics["total_estimated_cost_usd"] == 0.012
        assert metrics["mean_estimated_cost_usd"] == 0.004

        # Check confidence calibration
        calibration = metrics["confidence_calibration"]
        assert "experience_confidence" in calibration
        assert "skills_confidence" in calibration
        exp_cal = calibration["experience_confidence"]
        assert exp_cal["sample_count"] == 3
        assert exp_cal["brier_score"] is not None
        assert exp_cal["expected_calibration_error"] is not None

    def test_evaluate_predictions_missing_and_extra_case_ids(
        self,
        sample_gold_dataset: list[dict[str, Any]],
        sample_prediction_rows: list[dict[str, Any]],
    ) -> None:
        """Verify detection and reporting of missing case IDs and extra prediction case IDs."""
        # Add extra gold row
        gold_extended = sample_gold_dataset + [
            {
                "case_id": "case-004-missing-pred",
                "gold": {
                    "experience_level": "Senior-level",
                    "skills_match": "Match",
                    "recommendation": "recruiter_review",
                },
            }
        ]
        # Add extra prediction row
        preds_extended = sample_prediction_rows + [
            {
                "case_id": "case-999-extra-pred",
                "prediction": {
                    "experience_level": "Entry-level",
                    "skills_match": "No match",
                    "recommendation": "hr_review",
                    "schema_valid": True,
                },
            }
        ]

        report = evaluate_predictions(
            gold_dataset=gold_extended,
            prediction_rows=preds_extended,
            bootstrap_iterations=100,
        )

        assert report["sample_count"] == 3
        assert report["missing_case_ids"] == ["case-004-missing-pred"]
        assert report["extra_case_ids"] == ["case-999-extra-pred"]

    def test_evaluate_predictions_invalid_gold_dataset_raises(
        self,
        sample_prediction_rows: list[dict[str, Any]],
    ) -> None:
        """Verify validation errors when gold dataset contains invalid structure or duplicates."""
        # Missing case_id in gold
        with pytest.raises(
            ValueError, match="Every gold row must have a non-empty case_id"
        ):
            evaluate_predictions(
                gold_dataset=[{"gold": {}}],
                prediction_rows=sample_prediction_rows,
            )

        # Duplicate case_id in gold
        duplicate_gold = [
            {"case_id": "c1", "gold": {}},
            {"case_id": "c1", "gold": {}},
        ]
        with pytest.raises(ValueError, match="Duplicate gold case_id: c1"):
            evaluate_predictions(
                gold_dataset=duplicate_gold,
                prediction_rows=sample_prediction_rows,
            )

        # Missing gold dictionary
        missing_gold_dict = [{"case_id": "c1"}]
        with pytest.raises(ValueError, match="Missing gold labels for case_id=c1"):
            evaluate_predictions(
                gold_dataset=missing_gold_dict,
                prediction_rows=sample_prediction_rows,
            )

    def test_evaluate_predictions_invalid_prediction_rows_raises(
        self,
        sample_gold_dataset: list[dict[str, Any]],
    ) -> None:
        """Verify validation errors when prediction rows contain duplicate or invalid case_ids."""
        # Duplicate prediction case_id
        duplicate_preds = [
            {"case_id": "c1", "prediction": {}},
            {"case_id": "c1", "prediction": {}},
        ]
        with pytest.raises(ValueError, match="Duplicate prediction case_id: c1"):
            evaluate_predictions(
                gold_dataset=sample_gold_dataset,
                prediction_rows=duplicate_preds,
            )

    def test_evaluate_predictions_agreements(
        self,
        sample_gold_dataset: list[dict[str, Any]],
        sample_prediction_rows: list[dict[str, Any]],
    ) -> None:
        """Verify agreement metrics computation for judge and human review assessments."""
        report = evaluate_predictions(
            gold_dataset=sample_gold_dataset,
            prediction_rows=sample_prediction_rows,
            bootstrap_iterations=100,
        )

        metrics = report["metrics"]
        assert metrics["human_gold_vs_llm_judge_agreement"] == 1.0
        assert metrics["model_vs_llm_judge_agreement"] == 1.0
        assert metrics["human_review_vs_model_agreement"] == 1.0


class TestCompareReportsReleaseGate:
    """Test suite for conservative release gate evaluation and threshold regression catching."""

    def test_compare_reports_passing(self, baseline_eval_report: dict[str, Any]) -> None:
        """Verify that comparing identical or improved candidate reports returns passed=True."""
        candidate_report = dict(baseline_eval_report)

        gate = compare_reports(baseline=baseline_eval_report, candidate=candidate_report)

        assert gate["passed"] is True
        assert gate["failures"] == []
        assert gate["gate_version"] == "screening-release-gate-v1"

    def test_compare_reports_accuracy_drop_failure(
        self, baseline_eval_report: dict[str, Any]
    ) -> None:
        """Verify catching accuracy drops exceeding max_accuracy_drop."""
        candidate = dict(baseline_eval_report)
        candidate["metrics"] = dict(baseline_eval_report["metrics"])
        # Drop experience accuracy from 0.90 to 0.80 (drop of 0.10 > max_accuracy_drop of 0.03)
        candidate["metrics"]["experience_accuracy"] = {"accuracy": 0.80}

        gate = compare_reports(
            baseline=baseline_eval_report,
            candidate=candidate,
            max_accuracy_drop=0.03,
        )

        assert gate["passed"] is False
        assert any(
            "experience_accuracy regressed from 0.900 to 0.800" in f
            for f in gate["failures"]
        )

    def test_compare_reports_schema_drop_failure(
        self, baseline_eval_report: dict[str, Any]
    ) -> None:
        """Verify catching schema validity regressions."""
        candidate = dict(baseline_eval_report)
        candidate["metrics"] = dict(baseline_eval_report["metrics"])
        candidate["metrics"]["schema_validity_rate"] = 0.95

        gate = compare_reports(
            baseline=baseline_eval_report,
            candidate=candidate,
            max_schema_drop=0.0,
        )

        assert gate["passed"] is False
        assert any(
            "schema validity regressed from 1.000 to 0.950" in f
            for f in gate["failures"]
        )

    def test_compare_reports_slice_accuracy_gap_failure(
        self, baseline_eval_report: dict[str, Any]
    ) -> None:
        """Verify catching slice accuracy gaps exceeding max_slice_accuracy_gap."""
        candidate = dict(baseline_eval_report)
        candidate["slices"] = {
            "slice_a": {
                "sample_count": 5,
                "experience_accuracy": 0.95,
                "skills_accuracy": 0.90,
                "recommendation_accuracy": 0.90,
            },
            "slice_b": {
                "sample_count": 5,
                "experience_accuracy": 0.60,  # Gap = 0.35 > max_slice_accuracy_gap of 0.20
                "skills_accuracy": 0.90,
                "recommendation_accuracy": 0.90,
            },
        }

        gate = compare_reports(
            baseline=baseline_eval_report,
            candidate=candidate,
            max_slice_accuracy_gap=0.20,
        )

        assert gate["passed"] is False
        assert any("candidate slice gap for experience_accuracy" in f for f in gate["failures"])

    def test_compare_reports_distribution_shift_failure(
        self, baseline_eval_report: dict[str, Any]
    ) -> None:
        """Verify catching prediction distribution shift exceeding max_distribution_shift."""
        candidate = dict(baseline_eval_report)
        candidate["prediction_distributions"] = {
            "experience_level": {
                "Entry-level": 10,
                "Mid-level": 0,
                "Senior-level": 0,
            },
            "skills_match": {"Match": 6, "Partial match": 3, "No match": 1},
            "recommendation": {"recruiter_review": 7, "hr_review": 3},
        }

        gate = compare_reports(
            baseline=baseline_eval_report,
            candidate=candidate,
            max_distribution_shift=0.25,
        )

        assert gate["passed"] is False
        assert any(
            "prediction distribution shift for experience_level exceeds 0.250" in f
            for f in gate["failures"]
        )

    def test_compare_reports_latency_and_cost_increase_failures(
        self, baseline_eval_report: dict[str, Any]
    ) -> None:
        """Verify catching mean latency and cost increases above allowed thresholds."""
        candidate = dict(baseline_eval_report)
        candidate["metrics"] = dict(baseline_eval_report["metrics"])
        candidate["metrics"]["mean_latency_ms"] = 1500.0  # 50% increase > 25% threshold
        candidate["metrics"]["mean_estimated_cost_usd"] = 0.008  # 100% increase > 25% threshold

        gate = compare_reports(
            baseline=baseline_eval_report,
            candidate=candidate,
            max_latency_increase=0.25,
            max_cost_increase=0.25,
        )

        assert gate["passed"] is False
        assert any("mean latency increased by 50.0%" in f for f in gate["failures"])
        assert any("mean estimated cost increased by 100.0%" in f for f in gate["failures"])
