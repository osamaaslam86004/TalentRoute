#!/usr/bin/env python3
"""Create blinded A/B review tasks and a separate private model-identity map."""

import argparse
import os
from pathlib import Path

from app.evaluation.evaluation_pipeline import load_jsonl, write_json
from app.evaluation.pairwise_blinding import create_blinded_pairs


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--candidate-a", required=True, help="JSONL outputs for candidate A"
    )
    parser.add_argument(
        "--candidate-b", required=True, help="JSONL outputs for candidate B"
    )
    parser.add_argument("--candidate-a-version", required=True)
    parser.add_argument("--candidate-b-version", required=True)
    parser.add_argument("--public-output", default="reports/blinded-review.json")
    parser.add_argument(
        "--private-map-output", default=".private/blinded-review-map.json"
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--include-review-context",
        action="store_true",
        help="Include only pre-approved `review_context` inputs",
    )

    args = parser.parse_args()

    public, private = create_blinded_pairs(
        load_jsonl(args.candidate_a),
        load_jsonl(args.candidate_b),
        candidate_a_version=args.candidate_a_version,
        candidate_b_version=args.candidate_b_version,
        seed=args.seed,
        include_review_context=args.include_review_context,
    )

    write_json(
        args.public_output, {"review_version": "blinded-pairwise-v1", "tasks": public}
    )

    private_output = Path(args.private_map_output)
    private_output.parent.mkdir(parents=True, exist_ok=True)
    private_output.write_text(
        __import__("json").dumps(
            {"review_version": "blinded-pairwise-v1", "private_mapping": private},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # Keep this file out of version control and accessible only to the evaluation owner.
    # On Windows, chmod may be effectively a no-op, so set the mode when supported and
    # do not fail if the platform enforces a different permission model.
    try:
        if os.name != "nt":
            os.chmod(private_output, 0o600)
    except (AttributeError, NotImplementedError, OSError):
        pass
    print(
        f"Created {len(public)} blinded tasks. Keep the private mapping separate from reviewers."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
