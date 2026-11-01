# Blinded A/B Pairwise Review Reference

When comparing two competing model or prompt versions (e.g. Prompt v1 vs Prompt v2), the repository provides a **blinded A/B pairwise evaluation framework** (`app.evaluation.pairwise_blinding`).

---

## Pairwise Review Workflow

```
Candidate A Predictions               Candidate B Predictions
         │                                       │
         └───────────────────┬───────────────────┘
                             ▼
              Blinding Generator (create_pairwise_review.py)
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
 Public Review File (JSONL)        Private Key File (.private/)
 (Opaque Pair IDs, 50/50 Swap)    (Maps opaque pair IDs to true model IDs)
            │
            ▼
 Human Expert Preference Scoring
 (prefer_a / prefer_b / tie)
            │
            ▼
 Unblinding & Scoring Script (score_pairwise_review.py)
            │
            ▼
 Win Rate & Statistical Preference Summary
```

---

## Blinding & Privacy Isolation Mechanics

1. **Randomized Swapping:** For each test case, the script flips a coin (`seed`-based RNG) to assign Candidate A/B to Output 1 or Output 2.
2. **Opaque Pair Identifiers:** Public review tasks use anonymized pair UUIDs. Model names, prompt versions, and system identifiers are completely stripped.
3. **Restricted Mapping Files:** Secret assignment keys are written into restricted mapping files under `.private/` (e.g., `.private/pairwise_key_20261101.json`).

!!! security "Bias Elimination"
    Human reviewers view side-by-side outputs without knowing which candidate model generated which output, preventing reviewer bias toward specific model versions or prompt formats.

---

## CLI Execution Guide

### Step 1: Create Blinded Review Tasks
```bash
python -m app.scripts.create_pairwise_review   --candidate-a predictions/prompt_v1.jsonl   --candidate-b predictions/prompt_v2.jsonl   --candidate-a-version "screening-v1.0.0"   --candidate-b-version "screening-v1.1.0"   --output tasks/blinded_review_tasks.jsonl   --private-key-output .private/pairwise_key.json
```

### Step 2: Score Blinded Judgments
After reviewers record preferences (`prefer_a`, `prefer_b`, `tie`):

```bash
python -m app.scripts.score_pairwise_review   --judgments judgments/reviewer_scores.jsonl   --private-key .private/pairwise_key.json   --output reports/pairwise_results.json
```

---

## Output Summary Structure
The final report details aggregated win rates, loss rates, tie percentages, and statistically significant preference bounds for model release decisions.

---

## Related Documents
- [Offline Evaluation Framework](offline-evaluation.md)
- [LangGraph Pipeline Architecture](../architecture/langgraph-pipeline.md)
