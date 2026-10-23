"""Offline, versioned evaluation for model/prompt changes; intentionally makes no provider calls.

Gold labels must be human-reviewed and consented/appropriately licensed. This pipeline treats the
human-labelled set as the reference and reports LLM-judge agreement as a diagnostic, never ground
truth. The evidence-grounding metric is a lexical proxy, not a semantic guarantee. No production
prompt is auto-promoted from this report.

Prediction JSONL rows may contain either top-level assessment fields or a nested `prediction` object:
`case_id`, `experience_level`, `skills_match`, `recommendation`, `experience_evidence`,
`skills_evidence`, optional `latency_ms`, `estimated_cost_usd`, `model_version`, `prompt_version`,
and optionally `judge_assessment` for human-vs-judge comparison.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

DATASET_VERSION = "candidate-screening-eval-v1"
EXPERIENCE_VALUES = {
    "Entry-level",
    "Mid-level",
    "Senior-level",
    "Insufficient evidence",
}
SKILL_VALUES = {"Match", "Partial match", "No match", "Insufficient evidence"}
RECOMMENDATION_VALUES = {"hr_review", "recruiter_review", "manual_review"}
_TOKEN_RE = re.compile(r"[a-z0-9+#.]{2,}", re.I)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc

            if not isinstance(row, dict):
                raise ValueError(f"Each JSONL record must be an object: {path}:{line_number}")

            rows.append(row)

    return rows


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prediction_payload(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("prediction")

    return nested if isinstance(nested, dict) else row


def _schema_valid(prediction: dict[str, Any]) -> bool:

    if prediction.get("schema_valid", True) is False:
        return False

    if prediction.get("experience_level") not in EXPERIENCE_VALUES:
        return False

    if prediction.get("skills_match") not in SKILL_VALUES:
        return False

    if prediction.get("recommendation") not in RECOMMENDATION_VALUES:
        return False

    for field, max_items in (("experience_evidence", 8), ("skills_evidence", 12)):
        values = prediction.get(field)
        if not isinstance(values, list) or len(values) > max_items:
            return False
        if any(not isinstance(value, str) or len(value) > 500 for value in values):
            return False

    skill_gaps = prediction.get("skill_gaps", [])

    if not isinstance(skill_gaps, list) or len(skill_gaps) > 20:
        return False

    for field in ("experience_confidence", "skills_confidence"):
        if field in prediction:
            value = prediction[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                return False

    return True


def _bootstrap_ci(
    binary_values: list[bool], *, iterations: int = 1000, seed: int = 29
) -> dict[str, float | None]:

    if not binary_values:
        return {"low_95": None, "high_95": None}

    rng = random.Random(seed)
    n = len(binary_values)
    means = []

    for _ in range(max(100, iterations)):
        sample = [binary_values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    low = means[int(0.025 * (len(means) - 1))]
    high = means[int(0.975 * (len(means) - 1))]

    return {"low_95": round(low, 4), "high_95": round(high, 4)}


def _accuracy(
    records: list[tuple[dict[str, Any], dict[str, Any]]],
    field: str,
    *,
    iterations: int = 1000,
) -> dict[str, Any]:

    comparisons = [
        prediction.get(field) == gold.get("gold", {}).get(field) for gold, prediction in records
    ]
    successes = sum(comparisons)

    return {
        "accuracy": round(successes / len(comparisons), 4) if comparisons else None,
        "correct": successes,
        "total": len(comparisons),
        "ci_95": _bootstrap_ci(comparisons, iterations=iterations),
    }


def _grounding_coverage(source: str, evidence: Iterable[Any]) -> float | None:

    evidence_text = " ".join(item for item in evidence if isinstance(item, str)).strip()
    evidence_tokens = set(token.lower() for token in _TOKEN_RE.findall(evidence_text))

    if not evidence_tokens:
        return None

    source_tokens = set(token.lower() for token in _TOKEN_RE.findall(source))

    return (
        len(evidence_tokens.intersection(source_tokens)) / len(evidence_tokens)
        if source_tokens
        else 0.0
    )


def _calibration_summary(
    records: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    label_field: str,
    confidence_field: str,
    bins_count: int = 5,
) -> dict[str, Any]:
    points: list[tuple[float, bool]] = []

    for gold, prediction in records:
        confidence = prediction.get(confidence_field)

        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            continue
        points.append(
            (
                float(confidence),
                prediction.get(label_field) == gold.get("gold", {}).get(label_field),
            )
        )

    if not points:
        return {
            "sample_count": 0,
            "mean_confidence": None,
            "accuracy": None,
            "brier_score": None,
            "expected_calibration_error": None,
            "bins": [],
        }

    bins: list[dict[str, Any]] = []
    ece = 0.0

    for bin_index in range(bins_count):
        lower = bin_index / bins_count
        upper = (bin_index + 1) / bins_count
        values = [
            (confidence, correct)
            for confidence, correct in points
            if lower <= confidence < upper or (bin_index == bins_count - 1 and confidence == 1)
        ]

        if not values:
            continue

        mean_conf = fmean(confidence for confidence, _ in values)
        accuracy = sum(correct for _, correct in values) / len(values)
        ece += len(values) / len(points) * abs(mean_conf - accuracy)
        bins.append(
            {
                "lower_bound": lower,
                "upper_bound": upper,
                "sample_count": len(values),
                "mean_confidence": round(mean_conf, 4),
                "accuracy": round(accuracy, 4),
            }
        )

    return {
        "sample_count": len(points),
        "mean_confidence": round(fmean(confidence for confidence, _ in points), 4),
        "accuracy": round(sum(correct for _, correct in points) / len(points), 4),
        "brier_score": round(
            fmean((confidence - float(correct)) ** 2 for confidence, correct in points),
            4,
        ),
        "expected_calibration_error": round(ece, 4),
        "bins": bins,
    }


def evaluate_predictions(
    gold_dataset: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    *,
    model_version: str | None = None,
    prompt_version: str | None = None,
    dataset_version: str = DATASET_VERSION,
    bootstrap_iterations: int = 1000,
) -> dict[str, Any]:
    """Produce an inspectable evaluation report from a human-gold dataset and candidate outputs."""

    gold_by_id: dict[str, dict[str, Any]] = {}

    for item in gold_dataset:
        case_id = item.get("case_id")

        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every gold row must have a non-empty case_id")

        if case_id in gold_by_id:
            raise ValueError(f"Duplicate gold case_id: {case_id}")

        if not isinstance(item.get("gold"), dict):
            raise ValueError(f"Missing gold labels for case_id={case_id}")

        gold_by_id[case_id] = item

    predictions_by_id: dict[str, dict[str, Any]] = {}

    for item in prediction_rows:
        case_id = item.get("case_id")

        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every prediction row must have a non-empty case_id")

        if case_id in predictions_by_id:
            raise ValueError(f"Duplicate prediction case_id: {case_id}")
        predictions_by_id[case_id] = item

    missing = sorted(set(gold_by_id).difference(predictions_by_id))
    extra = sorted(set(predictions_by_id).difference(gold_by_id))
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    raw_grounding: list[float] = []
    latency: list[float] = []
    cost: list[float] = []
    schema_validity: list[bool] = []
    human_judge_match: list[bool] = []
    model_judge_match: list[bool] = []
    human_model_match: list[bool] = []
    all_models: set[str] = set()
    all_prompts: set[str] = set()

    for case_id in sorted(set(gold_by_id).intersection(predictions_by_id)):
        gold = gold_by_id[case_id]
        raw = predictions_by_id[case_id]
        pred = _prediction_payload(raw)
        records.append((gold, pred))
        schema_validity.append(_schema_valid(pred))
        source_text = str(gold.get("source_text", ""))
        experience_evidence = pred.get("experience_evidence", [])
        skills_evidence = pred.get("skills_evidence", [])

        if not isinstance(experience_evidence, list):
            experience_evidence = []

        if not isinstance(skills_evidence, list):
            skills_evidence = []
        coverage = _grounding_coverage(source_text, experience_evidence + skills_evidence)

        if coverage is not None:
            raw_grounding.append(coverage)

        try:
            if raw.get("latency_ms", pred.get("latency_ms")) is not None:
                latency.append(max(0.0, float(raw.get("latency_ms", pred.get("latency_ms")))))
            if raw.get("estimated_cost_usd", pred.get("estimated_cost_usd")) is not None:
                cost.append(
                    max(
                        0.0,
                        float(raw.get("estimated_cost_usd", pred.get("estimated_cost_usd"))),
                    )
                )

        except (ValueError, TypeError):
            # Malformed operational metrics are skipped; assessment schema validity still captures output validity.
            pass

        mv = raw.get("model_version", pred.get("model_version"))
        pv = raw.get("prompt_version", pred.get("prompt_version"))

        if isinstance(mv, str) and mv:
            all_models.add(mv)

        if isinstance(pv, str) and pv:
            all_prompts.add(pv)

        human_review = raw.get("human_review_assessment", pred.get("human_review_assessment"))

        if isinstance(human_review, dict):
            for field in ("experience_level", "skills_match", "recommendation"):
                if field in human_review and field in pred:
                    human_model_match.append(human_review[field] == pred[field])

        judge = raw.get("judge_assessment", pred.get("judge_assessment"))

        if isinstance(judge, dict):
            for field in ("experience_level", "skills_match", "recommendation"):
                if field in gold.get("gold", {}) and field in judge:
                    human_judge_match.append(judge[field] == gold["gold"][field])
                if field in pred and field in judge:
                    model_judge_match.append(pred[field] == judge[field])

    fields = {
        field: _accuracy(records, field, iterations=bootstrap_iterations)
        for field in ("experience_level", "skills_match", "recommendation")
    }
    slice_groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)

    for record in records:
        item = record[0]
        slice_name = str(item.get("slice", "unspecified"))[:80]
        slice_groups[slice_name].append(record)

    slices = {
        slice_name: {
            "sample_count": len(group),
            "experience_accuracy": _accuracy(
                group, "experience_level", iterations=bootstrap_iterations
            )["accuracy"],
            "skills_accuracy": _accuracy(group, "skills_match", iterations=bootstrap_iterations)[
                "accuracy"
            ],
            "recommendation_accuracy": _accuracy(
                group, "recommendation", iterations=bootstrap_iterations
            )["accuracy"],
        }
        for slice_name, group in sorted(slice_groups.items())
    }

    prediction_distributions = {
        field: dict(
            sorted(Counter(str(pred.get(field, "<missing>")) for _, pred in records).items())
        )
        for field in ("experience_level", "skills_match", "recommendation")
    }

    calibration = {
        "experience_confidence": _calibration_summary(
            records,
            label_field="experience_level",
            confidence_field="experience_confidence",
        ),
        "skills_confidence": _calibration_summary(
            records, label_field="skills_match", confidence_field="skills_confidence"
        ),
    }

    valid_count = sum(schema_validity)
    metrics = {
        "schema_validity_rate": (
            round(valid_count / len(schema_validity), 4) if schema_validity else None
        ),
        "schema_valid_count": valid_count,
        "schema_valid_total": len(schema_validity),
        "experience_accuracy": fields["experience_level"],
        "skills_accuracy": fields["skills_match"],
        "recommendation_accuracy": fields["recommendation"],
        "evidence_grounding_lexical_proxy": (
            round(fmean(raw_grounding), 4) if raw_grounding else None
        ),
        "mean_latency_ms": round(fmean(latency), 2) if latency else None,
        "p95_latency_ms": (
            round(
                sorted(latency)[min(len(latency) - 1, math.ceil(0.95 * len(latency)) - 1)],
                2,
            )
            if latency
            else None
        ),
        "total_estimated_cost_usd": round(sum(cost), 6) if cost else None,
        "mean_estimated_cost_usd": round(fmean(cost), 6) if cost else None,
        "human_gold_vs_llm_judge_agreement": (
            round(sum(human_judge_match) / len(human_judge_match), 4) if human_judge_match else None
        ),
        "model_vs_llm_judge_agreement": (
            round(sum(model_judge_match) / len(model_judge_match), 4) if model_judge_match else None
        ),
        "human_review_vs_model_agreement": (
            round(sum(human_model_match) / len(human_model_match), 4) if human_model_match else None
        ),
        "confidence_calibration": calibration,
    }

    return {
        "report_version": "screening-evaluation-report-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_version": dataset_version,
        "sample_count": len(records),
        "gold_count": len(gold_by_id),
        "prediction_count": len(predictions_by_id),
        "missing_case_ids": missing,
        "extra_case_ids": extra,
        "model_version": model_version
        or (", ".join(sorted(all_models)) if all_models else "unspecified"),
        "prompt_version": prompt_version
        or (", ".join(sorted(all_prompts)) if all_prompts else "unspecified"),
        "metrics": metrics,
        "slices": slices,
        "prediction_distributions": prediction_distributions,
        "limitations": [
            "Human-labelled gold is the reference; an LLM judge is diagnostic only.",
            "Evidence grounding uses lexical overlap and does not prove semantic support.",
            "Only non-sensitive, review-approved slices should be included in the evaluation dataset.",
            "Reports are advisory release evidence; no production prompt/model is automatically promoted.",
        ],
    }


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_accuracy_drop: float = 0.03,
    max_schema_drop: float = 0.0,
    max_slice_accuracy_gap: float = 0.20,
    max_distribution_shift: float = 0.25,
    max_latency_increase: float = 0.25,
    max_cost_increase: float = 0.25,
) -> dict[str, Any]:
    """Evaluate a conservative release gate against a chosen report; does not deploy anything."""

    failures: list[str] = []

    if candidate.get("dataset_version") != baseline.get("dataset_version"):
        failures.append("baseline and candidate reports use different dataset versions")

    if candidate.get("missing_case_ids"):
        failures.append(
            f"candidate report is missing {len(candidate['missing_case_ids'])} gold cases"
        )

    if candidate.get("extra_case_ids"):
        failures.append(
            f"candidate report contains {len(candidate['extra_case_ids'])} unknown cases"
        )

    base_metrics = baseline.get("metrics", {})
    new_metrics = candidate.get("metrics", {})

    base_schema = base_metrics.get("schema_validity_rate")
    new_schema = new_metrics.get("schema_validity_rate")

    if isinstance(base_schema, (int, float)) and isinstance(new_schema, (int, float)):
        if new_schema < base_schema - max_schema_drop:
            failures.append(f"schema validity regressed from {base_schema:.3f} to {new_schema:.3f}")

    for key in ("experience_accuracy", "skills_accuracy", "recommendation_accuracy"):
        before = base_metrics.get(key, {}).get("accuracy")
        after = new_metrics.get(key, {}).get("accuracy")
        if (
            isinstance(before, (int, float))
            and isinstance(after, (int, float))
            and after < before - max_accuracy_drop
        ):
            failures.append(f"{key} regressed from {before:.3f} to {after:.3f}")

    slices = candidate.get("slices", {})

    for field in ("experience_accuracy", "skills_accuracy", "recommendation_accuracy"):
        values = [
            item.get(field)
            for item in slices.values()
            if isinstance(item.get(field), (int, float)) and item.get("sample_count", 0) >= 2
        ]
        if len(values) >= 2 and max(values) - min(values) > max_slice_accuracy_gap:
            failures.append(f"candidate slice gap for {field} exceeds {max_slice_accuracy_gap:.3f}")

    distribution_shift: dict[str, float] = {}
    base_distributions = baseline.get("prediction_distributions", {})
    new_distributions = candidate.get("prediction_distributions", {})

    for field in ("experience_level", "skills_match", "recommendation"):
        before = base_distributions.get(field, {})
        after = new_distributions.get(field, {})
        total_before = sum(before.values())
        total_after = sum(after.values())

        if total_before and total_after:
            labels = set(before).union(after)
            distance = 0.5 * sum(
                abs(before.get(label, 0) / total_before - after.get(label, 0) / total_after)
                for label in labels
            )
            distribution_shift[field] = round(distance, 4)
            if distance > max_distribution_shift:
                failures.append(
                    f"prediction distribution shift for {field} exceeds {max_distribution_shift:.3f}"
                )

    for metric_key, threshold, label in (
        ("mean_latency_ms", max_latency_increase, "mean latency"),
        ("mean_estimated_cost_usd", max_cost_increase, "mean estimated cost"),
    ):
        before = base_metrics.get(metric_key)
        after = new_metrics.get(metric_key)

        if isinstance(before, (int, float)) and before > 0 and isinstance(after, (int, float)):
            increase = (after - before) / before
            if increase > threshold:
                failures.append(f"{label} increased by {increase:.1%}, above {threshold:.1%}")

    return {
        "gate_version": "screening-release-gate-v1",
        "baseline_model_version": baseline.get("model_version", "unknown"),
        "candidate_model_version": candidate.get("model_version", "unknown"),
        "baseline_prompt_version": baseline.get("prompt_version", "unknown"),
        "candidate_prompt_version": candidate.get("prompt_version", "unknown"),
        "passed": not failures,
        "failures": failures,
        "distribution_shift": distribution_shift,
        "thresholds": {
            "max_accuracy_drop": max_accuracy_drop,
            "max_schema_drop": max_schema_drop,
            "max_slice_accuracy_gap": max_slice_accuracy_gap,
            "max_distribution_shift": max_distribution_shift,
            "max_latency_increase": max_latency_increase,
            "max_cost_increase": max_cost_increase,
        },
        "manual_approval_required": True,
        "notice": "A passing report is not proof of fairness, safety, or production readiness.",
    }
