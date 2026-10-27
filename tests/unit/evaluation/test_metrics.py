"""Unit tests for evaluation metrics, schema validation, and calibration diagnostics in app.evaluation.evaluation_pipeline.

Tests cover:
- Bootstrap confidence interval calculation (_bootstrap_ci).
- Accuracy scoring across assessment fields (_accuracy).
- Lexical evidence grounding coverage proxy (_grounding_coverage).
- Confidence calibration diagnostics and ECE (_calibration_summary).
- Prediction schema validity and guardrail enforcement (_schema_valid).
- Release gate thresholding and ranking consistency in compare_reports.
"""

from __future__ import annotations

import unittest
from typing import Any

try:
    import pytest
except ImportError:
    pytest = None

from app.evaluation.evaluation_pipeline import (
    _accuracy,
    _bootstrap_ci,
    _calibration_summary,
    _grounding_coverage,
    _schema_valid,
    compare_reports,
)


class TestBootstrapCI(unittest.TestCase):
    """Test bootstrap confidence interval calculation."""

    def test_bootstrap_ci_standard_distribution(self) -> None:
        """Verify 95% confidence interval bounds for mixed boolean outcomes."""
        binary = [True, True, True, True, False, False, True, False, True, False]
        ci = _bootstrap_ci(binary, iterations=500, seed=42)

        self.assertIsNotNone(ci["low_95"])
        self.assertIsNotNone(ci["high_95"])
        assert ci["low_95"] is not None and ci["high_95"] is not None
        self.assertLessEqual(ci["low_95"], ci["high_95"])
        self.assertGreaterEqual(ci["low_95"], 0.0)
        self.assertLessEqual(ci["high_95"], 1.0)

    def test_bootstrap_ci_empty_input(self) -> None:
        """Verify empty input returns None for both low_95 and high_95 bounds."""
        ci = _bootstrap_ci([])
        self.assertEqual(ci, {"low_95": None, "high_95": None})

    def test_bootstrap_ci_all_true_or_all_false(self) -> None:
        """Verify degenerate distributions produce equal upper and lower bounds."""
        ci_true = _bootstrap_ci([True] * 10)
        self.assertEqual(ci_true, {"low_95": 1.0, "high_95": 1.0})

        ci_false = _bootstrap_ci([False] * 10)
        self.assertEqual(ci_false, {"low_95": 0.0, "high_95": 0.0})

    def test_bootstrap_ci_deterministic_seed(self) -> None:
        """Verify fixed seed yields reproducible confidence intervals."""
        binary = [True, False, True, True, False]
        ci1 = _bootstrap_ci(binary, iterations=200, seed=99)
        ci2 = _bootstrap_ci(binary, iterations=200, seed=99)
        self.assertEqual(ci1, ci2)


class TestAccuracyMetric(unittest.TestCase):
    """Test field-level accuracy computation."""

    def test_accuracy_calculation_correct_and_incorrect(self) -> None:
        """Verify exact match count, total count, and accuracy calculation."""
        records: list[tuple[dict[str, Any], dict[str, Any]]] = [
            (
                {"gold": {"experience_level": "Senior-level"}},
                {"experience_level": "Senior-level"},
            ),
            (
                {"gold": {"experience_level": "Mid-level"}},
                {"experience_level": "Mid-level"},
            ),
            (
                {"gold": {"experience_level": "Entry-level"}},
                {"experience_level": "Senior-level"},
            ),  # mismatch
        ]

        res = _accuracy(records, "experience_level", iterations=100)
        self.assertEqual(res["total"], 3)
        self.assertEqual(res["correct"], 2)
        self.assertEqual(res["accuracy"], 0.6667)
        self.assertIsNotNone(res["ci_95"]["low_95"])
        self.assertIsNotNone(res["ci_95"]["high_95"])

    def test_accuracy_empty_records(self) -> None:
        """Verify zero division guardrail when records list is empty."""
        res = _accuracy([], "experience_level")
        self.assertEqual(res["accuracy"], None)
        self.assertEqual(res["correct"], 0)
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["ci_95"], {"low_95": None, "high_95": None})

    def test_accuracy_missing_prediction_or_gold_fields(self) -> None:
        """Verify missing fields in gold or prediction are evaluated as incorrect."""
        records: list[tuple[dict[str, Any], dict[str, Any]]] = [
            ({"gold": {}}, {"experience_level": "Senior-level"}),
            ({"gold": {"experience_level": "Senior-level"}}, {}),
        ]

        res = _accuracy(records, "experience_level", iterations=100)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["correct"], 0)
        self.assertEqual(res["accuracy"], 0.0)


class TestGroundingCoverage(unittest.TestCase):
    """Test lexical evidence grounding proxy calculation."""

    def test_grounding_coverage_full_and_partial_overlap(self) -> None:
        """Verify token overlap percentage calculation against source text."""
        source = "Candidate demonstrates 5 years of Python development and FastAPI backend experience."
        evidence = ["Python development", "FastAPI experience"]

        coverage = _grounding_coverage(source, evidence)
        self.assertIsNotNone(coverage)
        assert coverage is not None
        self.assertGreater(coverage, 0.5)
        self.assertLessEqual(coverage, 1.0)

    def test_grounding_coverage_empty_or_short_evidence_returns_none(self) -> None:
        """Verify empty evidence or tokens with length < 2 return None."""
        self.assertIsNone(_grounding_coverage("Source text here", []))
        self.assertIsNone(_grounding_coverage("Source text here", ["a", "b", "1"]))

    def test_grounding_coverage_no_overlap(self) -> None:
        """Verify zero overlap returns 0.0."""
        source = "Java Spring Boot developer with Oracle DB experience"
        evidence = ["Python FastAPI"]

        coverage = _grounding_coverage(source, evidence)
        self.assertEqual(coverage, 0.0)

    def test_grounding_coverage_handles_non_string_evidence_items(self) -> None:
        """Verify non-string elements inside evidence list are safely skipped."""
        source = "Python development experience"
        evidence = [123, None, True, "Python experience"]

        coverage = _grounding_coverage(source, evidence)
        self.assertEqual(coverage, 1.0)


class TestCalibrationSummary(unittest.TestCase):
    """Test confidence calibration, Brier score, and Expected Calibration Error (ECE)."""

    def test_calibration_summary_valid_points(self) -> None:
        """Verify calibration metrics calculation for valid confidence float inputs."""
        records: list[tuple[dict[str, Any], dict[str, Any]]] = [
            (
                {"gold": {"skills_match": "Match"}},
                {"skills_match": "Match", "skills_confidence": 0.9},
            ),
            (
                {"gold": {"skills_match": "Match"}},
                {"skills_match": "Match", "skills_confidence": 0.8},
            ),
            (
                {"gold": {"skills_match": "Match"}},
                {"skills_match": "No match", "skills_confidence": 0.7},
            ),
            (
                {"gold": {"skills_match": "Match"}},
                {"skills_match": "No match", "skills_confidence": 0.2},
            ),
        ]

        summary = _calibration_summary(
            records,
            label_field="skills_match",
            confidence_field="skills_confidence",
            bins_count=5,
        )

        self.assertEqual(summary["sample_count"], 4)
        self.assertEqual(summary["mean_confidence"], 0.65)
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertIsNotNone(summary["brier_score"])
        self.assertIsNotNone(summary["expected_calibration_error"])
        self.assertGreater(len(summary["bins"]), 0)

    def test_calibration_summary_filters_invalid_confidences(self) -> None:
        """Verify boolean, string, None, and out-of-range confidence scores are skipped."""
        records: list[tuple[dict[str, Any], dict[str, Any]]] = [
            ({"gold": {"f": "A"}}, {"f": "A", "conf": True}),  # boolean
            ({"gold": {"f": "A"}}, {"f": "A", "conf": "0.9"}),  # string
            ({"gold": {"f": "A"}}, {"f": "A", "conf": None}),  # None
            ({"gold": {"f": "A"}}, {"f": "A", "conf": -0.1}),  # out of bounds
            ({"gold": {"f": "A"}}, {"f": "A", "conf": 1.5}),  # out of bounds
        ]

        summary = _calibration_summary(
            records, label_field="f", confidence_field="conf"
        )
        self.assertEqual(summary["sample_count"], 0)
        self.assertIsNone(summary["mean_confidence"])
        self.assertIsNone(summary["brier_score"])
        self.assertEqual(summary["bins"], [])

    def test_calibration_summary_boundary_confidence_values(self) -> None:
        """Verify handling of boundary values 0.0 and 1.0 (specifically upper bin inclusion)."""
        records: list[tuple[dict[str, Any], dict[str, Any]]] = [
            ({"gold": {"f": "A"}}, {"f": "A", "conf": 0.0}),
            ({"gold": {"f": "A"}}, {"f": "A", "conf": 1.0}),
        ]

        summary = _calibration_summary(
            records, label_field="f", confidence_field="conf", bins_count=2
        )
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["mean_confidence"], 0.5)


class TestSchemaValid(unittest.TestCase):
    """Test assessment schema validation guardrails."""

    def setUp(self) -> None:
        self.valid_prediction: dict[str, Any] = {
            "experience_level": "Senior-level",
            "skills_match": "Match",
            "recommendation": "hr_review",
            "experience_evidence": ["10 years in software"],
            "skills_evidence": ["Python", "AWS"],
            "skill_gaps": ["Kubernetes"],
            "experience_confidence": 0.9,
            "skills_confidence": 0.85,
            "schema_valid": True,
        }

    def test_schema_valid_returns_true_for_valid_payload(self) -> None:
        """Verify compliant prediction payload passes schema validation."""
        self.assertTrue(_schema_valid(self.valid_prediction))

    def test_schema_valid_schema_valid_flag_false_fails(self) -> None:
        """Verify explicit schema_valid=False causes validation failure."""
        pred = dict(self.valid_prediction, schema_valid=False)
        self.assertFalse(_schema_valid(pred))

    def test_schema_valid_invalid_enum_values_fail(self) -> None:
        """Verify unapproved enum strings for experience, skills, or recommendation fail."""
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, experience_level="Executive"))
        )
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, skills_match="Perfect"))
        )
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, recommendation="auto_hire"))
        )

    def test_schema_valid_evidence_length_limits(self) -> None:
        """Verify evidence lists exceeding item count limits fail validation."""
        too_much_exp = dict(
            self.valid_prediction, experience_evidence=["item"] * 9
        )  # max 8
        too_much_skills = dict(
            self.valid_prediction, skills_evidence=["item"] * 13
        )  # max 12

        self.assertFalse(_schema_valid(too_much_exp))
        self.assertFalse(_schema_valid(too_much_skills))

    def test_schema_valid_evidence_string_length_and_type_limits(self) -> None:
        """Verify evidence strings over 500 characters or non-string items fail validation."""
        long_str = "x" * 501
        long_item_pred = dict(self.valid_prediction, experience_evidence=[long_str])
        non_string_pred = dict(self.valid_prediction, skills_evidence=[123])

        self.assertFalse(_schema_valid(long_item_pred))
        self.assertFalse(_schema_valid(non_string_pred))

    def test_schema_valid_skill_gaps_limits(self) -> None:
        """Verify skill_gaps length limit (>20) or invalid type fails validation."""
        too_many_gaps = dict(self.valid_prediction, skill_gaps=["gap"] * 21)
        non_list_gaps = dict(self.valid_prediction, skill_gaps="not a list")

        self.assertFalse(_schema_valid(too_many_gaps))
        self.assertFalse(_schema_valid(non_list_gaps))

    def test_schema_valid_confidence_type_and_range_limits(self) -> None:
        """Verify confidence values must be numeric, non-boolean, and between 0.0 and 1.0."""
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, experience_confidence=True))
        )
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, skills_confidence="0.9"))
        )
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, experience_confidence=-0.1))
        )
        self.assertFalse(
            _schema_valid(dict(self.valid_prediction, skills_confidence=1.05))
        )


class TestReleaseGateThresholds(unittest.TestCase):
    """Test report-to-report release gate checks in compare_reports."""

    def setUp(self) -> None:
        self.base_report: dict[str, Any] = {
            "dataset_version": "eval-v1",
            "model_version": "v1.0",
            "prompt_version": "prompt-v1",
            "missing_case_ids": [],
            "extra_case_ids": [],
            "metrics": {
                "schema_validity_rate": 1.0,
                "experience_accuracy": {"accuracy": 0.90},
                "skills_accuracy": {"accuracy": 0.88},
                "recommendation_accuracy": {"accuracy": 0.85},
                "mean_latency_ms": 200.0,
                "mean_estimated_cost_usd": 0.010,
            },
            "slices": {
                "engineering": {"sample_count": 10, "experience_accuracy": 0.90},
                "sales": {"sample_count": 10, "experience_accuracy": 0.88},
            },
            "prediction_distributions": {
                "experience_level": {"Senior-level": 10, "Mid-level": 10},
                "skills_match": {"Match": 15, "Partial match": 5},
                "recommendation": {"hr_review": 20},
            },
        }

    def test_compare_reports_passing_gate(self) -> None:
        """Verify passing gate when candidate report meets or exceeds all criteria."""
        candidate = dict(self.base_report, model_version="v2.0")
        gate = compare_reports(self.base_report, candidate)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["failures"], [])

    def test_compare_reports_accuracy_regression_fails(self) -> None:
        """Verify gate fails when accuracy drops beyond max_accuracy_drop threshold."""
        candidate = dict(self.base_report, model_version="v2.0")
        candidate["metrics"] = dict(
            self.base_report["metrics"],
            experience_accuracy={"accuracy": 0.80},  # drop of 0.10 > max 0.03
        )

        gate = compare_reports(self.base_report, candidate, max_accuracy_drop=0.03)
        self.assertFalse(gate["passed"])
        self.assertTrue(
            any("experience_accuracy regressed" in f for f in gate["failures"])
        )

    def test_compare_reports_schema_validity_drop_fails(self) -> None:
        """Verify gate fails when schema validity rate drops."""
        candidate = dict(self.base_report, model_version="v2.0")
        candidate["metrics"] = dict(
            self.base_report["metrics"], schema_validity_rate=0.95
        )

        gate = compare_reports(self.base_report, candidate, max_schema_drop=0.0)
        self.assertFalse(gate["passed"])
        self.assertTrue(any("schema validity regressed" in f for f in gate["failures"]))

    def test_compare_reports_slice_gap_exceeded_fails(self) -> None:
        """Verify gate fails when accuracy gap across slices exceeds threshold."""
        candidate = dict(self.base_report, model_version="v2.0")
        candidate["slices"] = {
            "engineering": {"sample_count": 10, "experience_accuracy": 0.95},
            "sales": {
                "sample_count": 10,
                "experience_accuracy": 0.60,
            },  # gap 0.35 > 0.20
        }

        gate = compare_reports(self.base_report, candidate, max_slice_accuracy_gap=0.20)
        self.assertFalse(gate["passed"])
        self.assertTrue(
            any(
                "candidate slice gap for experience_accuracy" in f
                for f in gate["failures"]
            )
        )

    def test_compare_reports_distribution_shift_exceeded_fails(self) -> None:
        """Verify gate fails when output label distribution shifts significantly."""
        candidate = dict(self.base_report, model_version="v2.0")
        candidate["prediction_distributions"] = {
            "experience_level": {"Senior-level": 0, "Mid-level": 20},  # complete flip
            "skills_match": {"Match": 15, "Partial match": 5},
            "recommendation": {"hr_review": 20},
        }

        gate = compare_reports(self.base_report, candidate, max_distribution_shift=0.25)
        self.assertFalse(gate["passed"])
        self.assertTrue(
            any(
                "prediction distribution shift for experience_level" in f
                for f in gate["failures"]
            )
        )

    def test_compare_reports_latency_and_cost_increase_fails(self) -> None:
        """Verify gate fails when mean latency or cost increase exceeds thresholds."""
        candidate = dict(self.base_report, model_version="v2.0")
        candidate["metrics"] = dict(
            self.base_report["metrics"],
            mean_latency_ms=350.0,  # 75% increase > 25% threshold
            mean_estimated_cost_usd=0.020,  # 100% increase > 25% threshold
        )

        gate = compare_reports(
            self.base_report,
            candidate,
            max_latency_increase=0.25,
            max_cost_increase=0.25,
        )
        self.assertFalse(gate["passed"])
        self.assertTrue(any("mean latency increased" in f for f in gate["failures"]))
        self.assertTrue(
            any("mean estimated cost increased" in f for f in gate["failures"])
        )


if __name__ == "__main__":
    unittest.main()
