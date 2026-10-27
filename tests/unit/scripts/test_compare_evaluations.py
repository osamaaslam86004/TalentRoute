# content of tests/scripts/test_compare_evaluations.py
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import pytest
except ImportError:
    pytest = None

from app.scripts.compare_evaluations import main


class TestCompareEvaluationsCLI(unittest.TestCase):
    """Unit tests for app/scripts/compare_evaluations.py CLI."""

    def test_compare_evaluations_success_returns_0(self) -> None:
        """Verify exit code 0 when release gate passes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_file = tmp_path / "baseline.json"
            candidate_file = tmp_path / "candidate.json"
            output_file = tmp_path / "release-gate.json"

            baseline_file.write_text(
                json.dumps({"dataset_version": "v1", "metrics": {}}), encoding="utf-8"
            )
            candidate_file.write_text(
                json.dumps({"dataset_version": "v1", "metrics": {}}), encoding="utf-8"
            )

            test_args = [
                "compare_evaluations.py",
                "--baseline",
                str(baseline_file),
                "--candidate",
                str(candidate_file),
                "--output",
                str(output_file),
            ]

            fake_gate_report = {
                "passed": True,
                "gate_version": "screening-release-gate-v1",
                "failures": [],
            }

            with (
                patch("sys.argv", test_args),
                patch(
                    "app.scripts.compare_evaluations.compare_reports",
                    return_value=fake_gate_report,
                ) as mock_compare,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_file.exists())
            written_data = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(written_data, fake_gate_report)
            mock_compare.assert_called_once()

    def test_compare_evaluations_failed_gate_returns_2(self) -> None:
        """Verify exit code 2 when release gate fails."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_file = tmp_path / "baseline.json"
            candidate_file = tmp_path / "candidate.json"
            output_file = tmp_path / "release-gate.json"

            baseline_file.write_text(
                json.dumps({"dataset_version": "v1"}), encoding="utf-8"
            )
            candidate_file.write_text(
                json.dumps({"dataset_version": "v1"}), encoding="utf-8"
            )

            test_args = [
                "compare_evaluations.py",
                "--baseline",
                str(baseline_file),
                "--candidate",
                str(candidate_file),
                "--output",
                str(output_file),
            ]

            fake_gate_report = {
                "passed": False,
                "gate_version": "screening-release-gate-v1",
                "failures": ["experience_accuracy regressed"],
            }

            with (
                patch("sys.argv", test_args),
                patch(
                    "app.scripts.compare_evaluations.compare_reports",
                    return_value=fake_gate_report,
                ),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 2)
            self.assertTrue(output_file.exists())
            written_data = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(written_data, fake_gate_report)

    def test_compare_evaluations_custom_cli_overrides(self) -> None:
        """Verify CLI flags are correctly mapped to compare_reports parameters."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            baseline_file = tmp_path / "baseline.json"
            candidate_file = tmp_path / "candidate.json"
            output_file = tmp_path / "out.json"

            baseline_file.write_text("{}", encoding="utf-8")
            candidate_file.write_text("{}", encoding="utf-8")

            test_args = [
                "compare_evaluations.py",
                "--baseline",
                str(baseline_file),
                "--candidate",
                str(candidate_file),
                "--output",
                str(output_file),
                "--max-accuracy-drop",
                "0.05",
                "--max-schema-drop",
                "0.01",
                "--max-slice-accuracy-gap",
                "0.15",
                "--max-distribution-shift",
                "0.20",
                "--max-latency-increase",
                "0.30",
                "--max-cost-increase",
                "0.30",
            ]

            fake_gate_report = {"passed": True, "failures": []}

            with (
                patch("sys.argv", test_args),
                patch(
                    "app.scripts.compare_evaluations.compare_reports",
                    return_value=fake_gate_report,
                ) as mock_compare,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            mock_compare.assert_called_once_with(
                {},
                {},
                max_accuracy_drop=0.05,
                max_schema_drop=0.01,
                max_slice_accuracy_gap=0.15,
                max_distribution_shift=0.20,
                max_latency_increase=0.30,
                max_cost_increase=0.30,
            )

    def test_compare_evaluations_missing_file_raises_error(self) -> None:
        """Verify FileNotFoundError when baseline or candidate path does not exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            missing_baseline = tmp_path / "non_existent.json"
            candidate_file = tmp_path / "candidate.json"
            candidate_file.write_text("{}", encoding="utf-8")

            test_args = [
                "compare_evaluations.py",
                "--baseline",
                str(missing_baseline),
                "--candidate",
                str(candidate_file),
            ]

            with patch("sys.argv", test_args), self.assertRaises(FileNotFoundError):
                main()

    def test_compare_evaluations_invalid_json_raises_error(self) -> None:
        """Verify json.JSONDecodeError when baseline file contains malformed JSON."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            invalid_baseline = tmp_path / "invalid.json"
            candidate_file = tmp_path / "candidate.json"

            invalid_baseline.write_text("invalid json syntax", encoding="utf-8")
            candidate_file.write_text("{}", encoding="utf-8")

            test_args = [
                "compare_evaluations.py",
                "--baseline",
                str(invalid_baseline),
                "--candidate",
                str(candidate_file),
            ]

            with patch("sys.argv", test_args), self.assertRaises(json.JSONDecodeError):
                main()


if __name__ == "__main__":
    unittest.main()
