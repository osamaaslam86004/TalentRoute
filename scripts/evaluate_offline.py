#!/usr/bin/env python3
"""Evaluate saved model outputs against a versioned human-gold dataset; no network calls."""

import argparse

from app.future_features.evaluation_pipeline import evaluate_predictions, load_jsonl, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/evaluation_v1.jsonl")
    parser.add_argument("--predictions", required=True, help="JSONL export; one model output per case_id")
    parser.add_argument("--output", default="reports/evaluation-candidate.json")
    parser.add_argument("--model-version")
    parser.add_argument("--prompt-version")
    parser.add_argument("--dataset-version", default="candidate-screening-eval-v1")
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    args = parser.parse_args()
    report = evaluate_predictions(
        load_jsonl(args.dataset),
        load_jsonl(args.predictions),
        model_version=args.model_version,
        prompt_version=args.prompt_version,
        dataset_version=args.dataset_version,
        bootstrap_iterations=max(100, min(args.bootstrap_iterations, 10000)),
    )
    write_json(args.output, report)
    print(f"Evaluation report written to {args.output}")
    print(f"Cases scored: {report['sample_count']}; missing: {len(report['missing_case_ids'])}; extra: {len(report['extra_case_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
