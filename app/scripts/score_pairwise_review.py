#!/usr/bin/env python3
"""Summarize completed blinded pairwise judgments using the restricted mapping."""

import argparse
import json
from pathlib import Path

from app.evaluation.evaluation_pipeline import write_json
from app.evaluation.pairwise_blinding import score_blinded_judgments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--public-review", required=True, help="Completed public review JSON")
    parser.add_argument("--private-map", required=True, help="Restricted identity map JSON")
    parser.add_argument("--output", default="reports/pairwise-summary.json")

    args = parser.parse_args()

    public_data = json.loads(Path(args.public_review).read_text(encoding="utf-8"))
    private_data = json.loads(Path(args.private_map).read_text(encoding="utf-8"))

    report = score_blinded_judgments(public_data["tasks"], private_data["private_mapping"])

    write_json(args.output, report)
    print(f"Pairwise summary written to {args.output}; completed={report['completed_pairs']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
