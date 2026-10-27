# content of tests/scripts/test_create_pairwise_review.py
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

from app.scripts.create_pairwise_review import main


class TestCreatePairwiseReviewCLI(unittest.TestCase):
    """Unit tests for app/scripts/create_pairwise_review.py CLI."""

    def test_create_pairwise_review_success(self) -> None:
        """Verify successful execution of create_pairwise_review CLI."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cand_a_file = tmp_path / "cand_a.jsonl"
            cand_b_file = tmp_path / "cand_b.jsonl"
            public_out = tmp_path / "public.json"
            private_out = tmp_path / "private_map.json"

            cand_a_file.write_text(
                '{"case_id": "c1", "experience_level": "Senior-level"}\n',
                encoding="utf-8",
            )
            cand_b_file.write_text(
                '{"case_id": "c1", "experience_level": "Mid-level"}\n', encoding="utf-8"
            )

            test_args = [
                "create_pairwise_review.py",
                "--candidate-a",
                str(cand_a_file),
                "--candidate-b",
                str(cand_b_file),
                "--candidate-a-version",
                "v1.0",
                "--candidate-b-version",
                "v2.0",
                "--public-output",
                str(public_out),
                "--private-map-output",
                str(private_out),
            ]

            with patch("sys.argv", test_args):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(public_out.exists())
            self.assertTrue(private_out.exists())

            public_data = json.loads(public_out.read_text(encoding="utf-8"))
            private_data = json.loads(private_out.read_text(encoding="utf-8"))

            self.assertEqual(public_data["review_version"], "blinded-pairwise-v1")
            self.assertEqual(len(public_data["tasks"]), 1)
            self.assertEqual(len(private_data["private_mapping"]), 1)

            file_mode = oct(private_out.stat().st_mode & 0o777)
            self.assertEqual(file_mode, "0o600")

    def test_create_pairwise_review_flags_seed_and_review_context(self) -> None:
        """Verify --seed and --include-review-context flags are passed to create_blinded_pairs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cand_a_file = tmp_path / "cand_a.jsonl"
            cand_b_file = tmp_path / "cand_b.jsonl"
            public_out = tmp_path / "public.json"
            private_out = tmp_path / "private_map.json"

            cand_a_file.write_text(
                '{"case_id": "c1", "review_context": "Senior Dev Role"}\n',
                encoding="utf-8",
            )
            cand_b_file.write_text(
                '{"case_id": "c1", "review_context": "Senior Dev Role"}\n',
                encoding="utf-8",
            )

            test_args = [
                "create_pairwise_review.py",
                "--candidate-a",
                str(cand_a_file),
                "--candidate-b",
                str(cand_b_file),
                "--candidate-a-version",
                "v1.0",
                "--candidate-b-version",
                "v2.0",
                "--public-output",
                str(public_out),
                "--private-map-output",
                str(private_out),
                "--seed",
                "42",
                "--include-review-context",
            ]

            with patch("sys.argv", test_args):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            public_data = json.loads(public_out.read_text(encoding="utf-8"))
            self.assertIn("review_context", public_data["tasks"][0])
            self.assertEqual(
                public_data["tasks"][0]["review_context"], "Senior Dev Role"
            )

    def test_create_pairwise_review_malformed_jsonl_raises_value_error(self) -> None:
        """Verify ValueError when candidate JSONL file contains invalid syntax."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cand_a_file = tmp_path / "cand_a.jsonl"
            cand_b_file = tmp_path / "cand_b.jsonl"

            cand_a_file.write_text("invalid json line\n", encoding="utf-8")
            cand_b_file.write_text('{"case_id": "c1"}\n', encoding="utf-8")

            test_args = [
                "create_pairwise_review.py",
                "--candidate-a",
                str(cand_a_file),
                "--candidate-b",
                str(cand_b_file),
                "--candidate-a-version",
                "v1.0",
                "--candidate-b-version",
                "v2.0",
            ]

            with patch("sys.argv", test_args):
                with self.assertRaises(ValueError) as ctx:
                    main()
                self.assertIn("Invalid JSONL", str(ctx.exception))

    def test_create_pairwise_review_mismatched_case_ids_raises_value_error(
        self,
    ) -> None:
        """Verify ValueError when candidate JSONL files share no case IDs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cand_a_file = tmp_path / "cand_a.jsonl"
            cand_b_file = tmp_path / "cand_b.jsonl"

            cand_a_file.write_text('{"case_id": "case_100"}\n', encoding="utf-8")
            cand_b_file.write_text('{"case_id": "case_200"}\n', encoding="utf-8")

            test_args = [
                "create_pairwise_review.py",
                "--candidate-a",
                str(cand_a_file),
                "--candidate-b",
                str(cand_b_file),
                "--candidate-a-version",
                "v1.0",
                "--candidate-b-version",
                "v2.0",
            ]

            with patch("sys.argv", test_args):
                with self.assertRaises(ValueError) as ctx:
                    main()
                self.assertIn(
                    "Candidate files do not contain a shared case_id",
                    str(ctx.exception),
                )


if __name__ == "__main__":
    unittest.main()
