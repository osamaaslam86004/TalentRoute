# content of tests/scripts/test_score_pairwise_review.py
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

from app.scripts.score_pairwise_review import main


class TestScorePairwiseReviewCLI(unittest.TestCase):
    """Unit tests for app/scripts/score_pairwise_review.py CLI."""

    def test_score_pairwise_review_success(self) -> None:
        """Verify score_pairwise_review CLI reads inputs, scores judgments, and writes summary report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            public_file = tmp_path / "public_review.json"
            private_file = tmp_path / "private_map.json"
            output_file = tmp_path / "summary.json"

            public_content = {"tasks": [{"pair_id": "p1", "judgment": {"winner": "a"}}]}
            private_content = {
                "private_mapping": [
                    {
                        "pair_id": "p1",
                        "candidate_a_version": "v1.0",
                        "candidate_b_version": "v2.0",
                    }
                ]
            }

            public_file.write_text(json.dumps(public_content), encoding="utf-8")
            private_file.write_text(json.dumps(private_content), encoding="utf-8")

            test_args = [
                "score_pairwise_review.py",
                "--public-review",
                str(public_file),
                "--private-map",
                str(private_file),
                "--output",
                str(output_file),
            ]

            fake_summary = {
                "report_version": "blinded-pairwise-summary-v1",
                "completed_pairs": 1,
                "ties_or_both_bad": 0,
                "model_wins": {"v1.0": 1},
                "mapping_missing_pair_ids": [],
            }

            with (
                patch("sys.argv", test_args),
                patch(
                    "app.scripts.score_pairwise_review.score_blinded_judgments",
                    return_value=fake_summary,
                ) as mock_score,
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_file.exists())
            written_data = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(written_data, fake_summary)

            mock_score.assert_called_once_with(
                public_content["tasks"],
                private_content["private_mapping"],
            )

    def test_score_pairwise_review_missing_input_file_raises(self) -> None:
        """Verify FileNotFoundError when public or private input JSON file does not exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            missing_public = tmp_path / "missing_public.json"
            private_file = tmp_path / "private_map.json"
            private_file.write_text("{}", encoding="utf-8")

            test_args = [
                "score_pairwise_review.py",
                "--public-review",
                str(missing_public),
                "--private-map",
                str(private_file),
            ]

            with patch("sys.argv", test_args), self.assertRaises(FileNotFoundError):
                main()


if __name__ == "__main__":
    unittest.main()
