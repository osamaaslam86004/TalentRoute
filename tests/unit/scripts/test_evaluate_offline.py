# content of tests/scripts/test_evaluate_offline.py
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

from app.scripts.evaluate_offline import main


class TestEvaluateOfflineCLI(unittest.TestCase):
    """Unit tests for app/scripts/evaluate_offline.py CLI."""

    def test_evaluate_offline_success(self) -> None:
        """Verify evaluate_offline CLI runs successfully and writes report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dataset_file = tmp_path / "gold.jsonl"
            predictions_file = tmp_path / "preds.jsonl"
            output_file = tmp_path / "report.json"

            dataset_file.write_text(
                '{"case_id": "c1", "gold": {"experience_level": "Senior-level"}}\n',
                encoding="utf-8",
            )
            predictions_file.write_text(
                '{"case_id": "c1", "experience_level": "Senior-level"}\n',
                encoding="utf-8",
            )

            test_args = [
                "evaluate_offline.py",
                "--dataset",
                str(dataset_file),
                "--predictions",
                str(predictions_file),
                "--output",
                str(output_file),
                "--model-version",
                "gpt-4o",
                "--prompt-version",
                "v1.2",
            ]

            fake_report = {
                "report_version": "screening-evaluation-report-v1",
                "sample_count": 1,
                "missing_case_ids": [],
                "extra_case_ids": [],
                "metrics": {},
            }

            with (
                patch("sys.argv", test_args),
                patch(
                    "app.scripts.evaluate_offline.evaluate_predictions",
                    return_value=fake_report,
                ) as mock_eval,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_file.exists())
            written_data = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(written_data, fake_report)

            mock_eval.assert_called_once_with(
                [{"case_id": "c1", "gold": {"experience_level": "Senior-level"}}],
                [{"case_id": "c1", "experience_level": "Senior-level"}],
                model_version="gpt-4o",
                prompt_version="v1.2",
                dataset_version="candidate-screening-eval-v1",
                bootstrap_iterations=1000,
            )

    def test_evaluate_offline_bootstrap_iterations_clamping(self) -> None:
        """Verify --bootstrap-iterations is bounded between 100 and 10000."""
        cases = [
            (10, 100),  # Clamped up to min 100
            (500, 500),  # Kept within range
            (20000, 10000),  # Clamped down to max 10000
        ]

        for input_iterations, expected_iterations in cases:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                dataset_file = tmp_path / "gold.jsonl"
                predictions_file = tmp_path / "preds.jsonl"
                output_file = tmp_path / "report.json"

                dataset_file.write_text(
                    '{"case_id": "c1", "gold": {}}\n', encoding="utf-8"
                )
                predictions_file.write_text('{"case_id": "c1"}\n', encoding="utf-8")

                test_args = [
                    "evaluate_offline.py",
                    "--dataset",
                    str(dataset_file),
                    "--predictions",
                    str(predictions_file),
                    "--output",
                    str(output_file),
                    "--bootstrap-iterations",
                    str(input_iterations),
                ]

                fake_report = {
                    "sample_count": 1,
                    "missing_case_ids": [],
                    "extra_case_ids": [],
                }

                with (
                    patch("sys.argv", test_args),
                    patch(
                        "app.scripts.evaluate_offline.evaluate_predictions",
                        return_value=fake_report,
                    ) as mock_eval,
                ):
                    exit_code = main()

                self.assertEqual(exit_code, 0)
                _, kwargs = mock_eval.call_args
                self.assertEqual(kwargs["bootstrap_iterations"], expected_iterations)


if __name__ == "__main__":
    unittest.main()
