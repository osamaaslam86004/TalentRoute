# Versioned Offline Evaluation Framework

To evaluate prompt tweaks, rubric changes, and model upgrades without risking production performance, the repository includes an **offline evaluation framework** (`app.evaluation.evaluation_pipeline`).

---

## Evaluation Architecture

```
[ Human Gold Dataset ] (JSONL)         [ Model Predictions ] (JSONL)
        │                                        │
        └──────────────────┬─────────────────────┘
                           ▼
             Offline Evaluation Pipeline (evaluate_offline.py)
                           │
                           ├── 1. Exact Label Agreement %
                           ├── 2. Bootstrap 95% Confidence Intervals
                           ├── 3. Lexical Grounding Proxy Score
                           └── 4. Calibration (ECE & Brier Score)
                           │
                           ▼
             Evaluation Report JSON (reports/evaluation-candidate.json)
```

!!! note "Air-Gapped Execution"
    Offline evaluation scripts make **zero** network or API provider calls. They operate strictly on saved predictions JSONL files to guarantee reproducible evaluations.

---

## Core Evaluation Metrics

### 1. Exact Label Agreement Accuracy
Measures agreement between model predictions and human gold reference labels across three targets:
- `experience_level` (`Entry-level`, `Mid-level`, `Senior-level`, `Insufficient evidence`)
- `skills_match` (`Match`, `Partial match`, `No match`, `Insufficient evidence`)
- `recommendation` (`hr_review`, `recruiter_review`, `manual_review`)

### 2. Bootstrap 95% Confidence Intervals
Applies non-parametric bootstrap resampling (default 1000 iterations, range 100–10000) to compute robust confidence intervals (`ci_95_lower`, `ci_95_upper`) for accuracy metrics.

### 3. Lexical Grounding Proxy Score
Computes token-overlap matching (`_TOKEN_RE`) between candidate resume text and model-extracted evidence quotes to evaluate hallucination risk.

### 4. Confidence Calibration Diagnostics
- **Expected Calibration Error (ECE):** Evaluates how well model confidence scores correspond to actual prediction accuracy.
- **Brier Score:** Mean squared difference between predicted probability and binary accuracy outcomes.

---

## Running Offline Evaluation CLI

=== "Execute Evaluation"
    ```bash
    python -m app.scripts.evaluate_offline       --dataset datasets/evaluation_v1.jsonl       --predictions predictions/model_candidate.jsonl       --output reports/evaluation-candidate.json       --bootstrap-iterations 1000
    ```

=== "Compare Candidate vs Baseline (Release Gate)"
    ```bash
    python -m app.scripts.compare_evaluations       --baseline reports/evaluation-baseline.json       --candidate reports/evaluation-candidate.json
    ```

---

## Related Documents
- [LangGraph Pipeline Architecture](../architecture/langgraph-pipeline.md)
- [Blinded Review Guide](blinded-review.md)
