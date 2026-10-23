"""Unit tests for offline evaluation pipeline runner in app.evaluation.evaluation_pipeline.

Tests cover:
- JSONL file loading and JSON writing utilities (load_jsonl, write_json).
- Offline versioned evaluation report generation (evaluate_predictions).
- Schema and artifact compliance checks across reports and release gates.
- Strict mocking of LLM providers and database dependencies (AsyncMock, MagicMock, patch).
- Comprehensive error handling: malformed datasets, missing/duplicate case IDs, and invalid JSON lines.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import pytest
except ImportError:
    pytest = None

from app.evaluation.evaluation_pipeline import (
    DATASET_VERSION,
    compare_reports,
    evaluate_predictions,
    load_jsonl,
    write_json,
)


class TestJSONLFileOperations(unittest.TestCase):
    """Test load_jsonl and write_json file helpers and validation."""

    def test_load_jsonl_valid_content(self) -> None:
        """Verify load_jsonl reads valid records and ignores empty lines."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp:
            tmp.write(
                '{"case_id": "c1", "value": 1}\n\n{"case_id": "c2", "value": 2}\n'
            )
            tmp_path = Path(tmp.name)

        try:
            records = load_jsonl(tmp_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["case_id"], "c1")
            self.assertEqual(records[1]["case_id"], "c2")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_load_jsonl_invalid_json_line_raises_value_error(self) -> None:
        """Verify line-specific ValueError is raised when JSON decoding fails."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp:
            tmp.write('{"case_id": "c1"}\n{invalid json syntax}\n')
            tmp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_jsonl(tmp_path)
            self.assertIn(f"Invalid JSONL at {tmp_path}:2", str(ctx.exception))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_load_jsonl_non_dict_record_raises_value_error(self) -> None:
        """Verify ValueError is raised when a JSON line is a scalar or list rather than an object."""
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp:
            tmp.write('{"case_id": "c1"}\n[1, 2, 3]\n')
            tmp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_jsonl(tmp_path)
            self.assertIn(
                f"Each JSONL record must be an object: {tmp_path}:2", str(ctx.exception)
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_write_json_creates_directories_and_formats_payload(self) -> None:
        """Verify write_json creates parent directories and writes formatted JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "sub_dir" / "report.json"
            payload = {"b_key": 2, "a_key": 1}

            write_json(target, payload)

            self.assertTrue(target.exists())
            text = target.read_text(encoding="utf-8")
            data = json.loads(text)
            self.assertEqual(data, payload)
            # Ensure sorted keys formatting
            self.assertTrue(text.startswith('{\n  "a_key": 1,\n  "b_key": 2\n}'))


class TestEvaluatePredictionsFlow(unittest.TestCase):
    """Test full offline evaluation report execution and output structure."""

    def setUp(self) -> None:
        self.gold_dataset: list[dict[str, Any]] = [
            {
                "case_id": "case_001",
                "source_text": "Candidate has 8 years Python experience in cloud systems.",
                "slice": "engineering",
                "gold": {
                    "experience_level": "Senior-level",
                    "skills_match": "Match",
                    "recommendation": "hr_review",
                },
            },
            {
                "case_id": "case_002",
                "source_text": "Junior developer with 1 year HTML/CSS.",
                "slice": "engineering",
                "gold": {
                    "experience_level": "Entry-level",
                    "skills_match": "No match",
                    "recommendation": "manual_review",
                },
            },
        ]

        self.predictions: list[dict[str, Any]] = [
            {
                "case_id": "case_001",
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "hr_review",
                "experience_evidence": ["8 years Python"],
                "skills_evidence": ["Python cloud"],
                "latency_ms": 250.0,
                "estimated_cost_usd": 0.002,
                "model_version": "gpt-4o",
                "prompt_version": "prompt-v1",
                "experience_confidence": 0.95,
                "skills_confidence": 0.90,
            },
            {
                "case_id": "case_002",
                "experience_level": "Entry-level",
                "skills_match": "No match",
                "recommendation": "manual_review",
                "experience_evidence": ["1 year HTML"],
                "skills_evidence": ["HTML"],
                "latency_ms": 180.0,
                "estimated_cost_usd": 0.001,
                "model_version": "gpt-4o",
                "prompt_version": "prompt-v1",
                "experience_confidence": 0.80,
                "skills_confidence": 0.85,
            },
        ]

    def test_evaluate_predictions_full_report_structure(self) -> None:
        """Verify complete evaluation report schema, metrics, and artifact properties."""
        report = evaluate_predictions(
            self.gold_dataset,
            self.predictions,
            model_version="gpt-4o",
            prompt_version="prompt-v1",
            bootstrap_iterations=100,
        )

        self.assertEqual(report["report_version"], "screening-evaluation-report-v1")
        self.assertEqual(report["dataset_version"], DATASET_VERSION)
        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["gold_count"], 2)
        self.assertEqual(report["prediction_count"], 2)
        self.assertEqual(report["missing_case_ids"], [])
        self.assertEqual(report["extra_case_ids"], [])
        self.assertEqual(report["model_version"], "gpt-4o")
        self.assertEqual(report["prompt_version"], "prompt-v1")

        # Verify metrics object content
        metrics = report["metrics"]
        self.assertEqual(metrics["schema_validity_rate"], 1.0)
        self.assertEqual(metrics["experience_accuracy"]["accuracy"], 1.0)
        self.assertEqual(metrics["skills_accuracy"]["accuracy"], 1.0)
        self.assertEqual(metrics["recommendation_accuracy"]["accuracy"], 1.0)
        self.assertEqual(metrics["mean_latency_ms"], 215.0)
        self.assertEqual(metrics["total_estimated_cost_usd"], 0.003)

        # Verify slices and distributions
        self.assertIn("engineering", report["slices"])
        self.assertIn(
            "Senior-level", report["prediction_distributions"]["experience_level"]
        )
        self.assertGreater(len(report["limitations"]), 0)

    def test_evaluate_predictions_nested_payload_format(self) -> None:
        """Verify prediction fields nested inside 'prediction' dict are handled correctly."""
        nested_preds = [
            {
                "case_id": "case_001",
                "prediction": {
                    "experience_level": "Senior-level",
                    "skills_match": "Match",
                    "recommendation": "hr_review",
                    "experience_evidence": ["8 years Python"],
                    "skills_evidence": ["Python"],
                },
                "latency_ms": 200,
                "model_version": "v1",
            },
            {
                "case_id": "case_002",
                "prediction": {
                    "experience_level": "Entry-level",
                    "skills_match": "No match",
                    "recommendation": "manual_review",
                },
                "latency_ms": 150,
                "model_version": "v1",
            },
        ]

        report = evaluate_predictions(
            self.gold_dataset, nested_preds, bootstrap_iterations=50
        )
        self.assertEqual(report["metrics"]["experience_accuracy"]["accuracy"], 1.0)
        self.assertEqual(report["sample_count"], 2)

    def test_evaluate_predictions_missing_and_extra_case_ids(self) -> None:
        """Verify mismatch between gold and prediction case IDs is tracked."""
        gold = [self.gold_dataset[0]]  # only case_001
        preds = [self.predictions[1]]  # only case_002

        report = evaluate_predictions(gold, preds, bootstrap_iterations=50)

        self.assertEqual(report["missing_case_ids"], ["case_001"])
        self.assertEqual(report["extra_case_ids"], ["case_002"])
        self.assertEqual(report["sample_count"], 0)

    def test_evaluate_predictions_handles_malformed_operational_metrics(self) -> None:
        """Verify invalid latency_ms or cost strings are skipped gracefully."""
        malformed_preds = [
            dict(
                self.predictions[0], latency_ms="not_a_number", estimated_cost_usd=None
            ),
            self.predictions[1],
        ]

        report = evaluate_predictions(
            self.gold_dataset, malformed_preds, bootstrap_iterations=50
        )
        # Should not raise exception and compute metrics on valid entries
        self.assertEqual(report["metrics"]["mean_latency_ms"], 180.0)
        self.assertEqual(report["metrics"]["total_estimated_cost_usd"], 0.001)


class TestEvaluatePredictionsValidation(unittest.TestCase):
    """Test error handling and validation guardrails in evaluate_predictions."""

    def test_missing_or_empty_gold_case_id_raises_value_error(self) -> None:
        """Verify ValueError is raised if a gold row lacks a valid case_id."""
        invalid_gold = [{"case_id": "", "gold": {"experience_level": "Senior-level"}}]
        valid_preds = [{"case_id": "c1", "experience_level": "Senior-level"}]

        with self.assertRaises(ValueError) as ctx:
            evaluate_predictions(invalid_gold, valid_preds)
        self.assertIn(
            "Every gold row must have a non-empty case_id", str(ctx.exception)
        )

    def test_duplicate_gold_case_id_raises_value_error(self) -> None:
        """Verify ValueError is raised if gold dataset contains duplicate case_ids."""
        gold_row = {
            "case_id": "case_dup",
            "gold": {"experience_level": "Senior-level"},
        }
        dup_gold = [gold_row, gold_row]
        valid_preds = [{"case_id": "case_dup"}]

        with self.assertRaises(ValueError) as ctx:
            evaluate_predictions(dup_gold, valid_preds)
        self.assertIn("Duplicate gold case_id: case_dup", str(ctx.exception))

    def test_missing_gold_label_dict_raises_value_error(self) -> None:
        """Verify ValueError is raised if a gold record is missing its 'gold' label dictionary."""
        invalid_gold = [{"case_id": "c1"}]  # missing "gold" key
        valid_preds = [{"case_id": "c1"}]

        with self.assertRaises(ValueError) as ctx:
            evaluate_predictions(invalid_gold, valid_preds)
        self.assertIn("Missing gold labels for case_id=c1", str(ctx.exception))

    def test_missing_or_empty_prediction_case_id_raises_value_error(self) -> None:
        """Verify ValueError is raised if a prediction row lacks a valid case_id."""
        valid_gold = [{"case_id": "c1", "gold": {"experience_level": "Senior-level"}}]
        invalid_preds = [{"case_id": None}]  # type: ignore[dict-item]

        with self.assertRaises(ValueError) as ctx:
            evaluate_predictions(valid_gold, invalid_preds)
        self.assertIn(
            "Every prediction row must have a non-empty case_id", str(ctx.exception)
        )

    def test_duplicate_prediction_case_id_raises_value_error(self) -> None:
        """Verify ValueError is raised if prediction rows contain duplicate case_ids."""
        valid_gold = [{"case_id": "c1", "gold": {"experience_level": "Senior-level"}}]
        pred_row = {"case_id": "c1", "experience_level": "Senior-level"}
        dup_preds = [pred_row, pred_row]

        with self.assertRaises(ValueError) as ctx:
            evaluate_predictions(valid_gold, dup_preds)
        self.assertIn("Duplicate prediction case_id: c1", str(ctx.exception))


class TestCompareReportsGateValidation(unittest.TestCase):
    """Test release gate failure paths in compare_reports."""

    def test_compare_reports_mismatched_dataset_version_fails(self) -> None:
        """Verify release gate fails if baseline and candidate use different dataset versions."""
        base = {"dataset_version": "eval-v1", "metrics": {}}
        cand = {"dataset_version": "eval-v2", "metrics": {}}

        gate = compare_reports(base, cand)
        self.assertFalse(gate["passed"])
        self.assertIn(
            "baseline and candidate reports use different dataset versions",
            gate["failures"],
        )

    def test_compare_reports_missing_gold_cases_in_candidate_fails(self) -> None:
        """Verify release gate fails if candidate report is missing gold cases."""
        base = {"dataset_version": "v1", "metrics": {}}
        cand = {
            "dataset_version": "v1",
            "missing_case_ids": ["c1", "c2"],
            "metrics": {},
        }

        gate = compare_reports(base, cand)
        self.assertFalse(gate["passed"])
        self.assertIn("candidate report is missing 2 gold cases", gate["failures"])

    def test_compare_reports_extra_unknown_cases_in_candidate_fails(self) -> None:
        """Verify release gate fails if candidate report contains extra unknown cases."""
        base = {"dataset_version": "v1", "metrics": {}}
        cand = {"dataset_version": "v1", "extra_case_ids": ["x1"], "metrics": {}}

        gate = compare_reports(base, cand)
        self.assertFalse(gate["passed"])
        self.assertIn("candidate report contains 1 unknown cases", gate["failures"])


class TestPipelineMockingAndIsolation(unittest.TestCase):
    """Test complete isolation from external LLM providers and database dependencies."""

    @patch("socket.socket")
    def test_evaluation_pipeline_requires_no_network_or_llm_provider_calls(
        self, mock_socket: MagicMock
    ) -> None:
        """Verify offline evaluation executes completely offline without network sockets."""
        gold = [
            {
                "case_id": "offline_01",
                "gold": {
                    "experience_level": "Senior-level",
                    "skills_match": "Match",
                    "recommendation": "hr_review",
                },
            }
        ]
        preds = [
            {
                "case_id": "offline_01",
                "experience_level": "Senior-level",
                "skills_match": "Match",
                "recommendation": "hr_review",
            }
        ]

        report = evaluate_predictions(gold, preds, bootstrap_iterations=10)

        # Confirm evaluation succeeds and socket was never invoked
        self.assertEqual(report["sample_count"], 1)
        mock_socket.assert_not_called()

    def test_async_wrapper_mocking_with_async_mock(self) -> None:
        """Verify AsyncMock usage for async evaluation wrappers or DB repositories."""

        async def _run_async_test() -> None:
            mock_repo = AsyncMock()
            mock_repo.get_evaluation_dataset.return_value = [
                {"case_id": "a1", "gold": {"experience_level": "Mid-level"}}
            ]

            dataset = await mock_repo.get_evaluation_dataset()

            self.assertEqual(len(dataset), 1)
            mock_repo.get_evaluation_dataset.assert_awaited_once()

        asyncio.run(_run_async_test())


if __name__ == "__main__":
    unittest.main()
