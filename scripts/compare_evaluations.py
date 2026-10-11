#!/usr/bin/env python3
"""Compare baseline/candidate evaluation reports and return non-zero if a release gate fails."""

import argparse
import json
from pathlib import Path

from app.future_features.evaluation_pipeline import compare_reports, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", default="reports/release-gate.json")
    parser.add_argument("--max-accuracy-drop", type=float, default=0.03)
    parser.add_argument("--max-schema-drop", type=float, default=0.0)
    parser.add_argument("--max-slice-accuracy-gap", type=float, default=0.20)
    parser.add_argument("--max-distribution-shift", type=float, default=0.25)
    parser.add_argument("--max-latency-increase", type=float, default=0.25)
    parser.add_argument("--max-cost-increase", type=float, default=0.25)
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    report = compare_reports(
        baseline,
        candidate,
        max_accuracy_drop=args.max_accuracy_drop,
        max_schema_drop=args.max_schema_drop,
        max_slice_accuracy_gap=args.max_slice_accuracy_gap,
        max_distribution_shift=args.max_distribution_shift,
        max_latency_increase=args.max_latency_increase,
        max_cost_increase=args.max_cost_increase,
    )
    write_json(args.output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
