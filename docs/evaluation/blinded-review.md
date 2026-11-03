# Blinded A/B Pairwise Review Guide

The **Blinded Pairwise Evaluation Framework** (`app.evaluation.pairwise_blinding`, `app.scripts.create_pairwise_review`, and `app.scripts.score_pairwise_review`) eliminates human reviewer bias when evaluating competing prompt or model versions.

---

## Pairwise Blinding Workflow

```
Candidate Model A Outputs            Candidate Model B Outputs
          │                                      │
          └───────────────────┬──────────────────┘
                              ▼
           Blinding Script (create_pairwise_review.py)
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
 Public Review Tasks (JSON)        Private Mapping File (.private/)
 (Opaque Pair UUIDs, 50/50 Swap)  (Restricted file permission 0600)
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

## Blinding Mechanics & Privacy Controls

1. **Randomized Side Assignment:** For each evaluation case, a seed-based coin flip assigns Candidate A and Candidate B to `output_1` or `output_2`.
2. **Metadata Stripping:** Model names, prompt versions, latency metrics, and cost details are stripped from public review tasks.
3. **Restricted Mapping Storage:** Mapping keys linking pair UUIDs to true model identities are written to restricted directories under `.private/` with strict file permissions (`chmod 0600`).

---

## CLI Execution Guide

### Step 1: Create Blinded Review Tasks
```bash
python -m app.scripts.create_pairwise_review   --candidate-a predictions/prompt_v1_outputs.jsonl   --candidate-b predictions/prompt_v2_outputs.jsonl   --candidate-a-version "prompt-v1.0.0"   --candidate-b-version "prompt-v2.0.0"   --public-output reports/public_blinded_tasks.json   --private-map-output .private/pairwise_map_key.json
```

### Step 2: Score Blinded Review Judgments
After human reviewers submit preferences (`prefer_a`, `prefer_b`, `tie`), execute the unblinding summary script:

```bash
python -m app.scripts.score_pairwise_review   --public-review judgments/completed_reviewer_scores.json   --private-map .private/pairwise_map_key.json   --output reports/pairwise_summary_results.json
```

---

## Summary Report Metrics
The output summary report calculates win rates, loss rates, tie percentages, and statistical confidence bounds to inform model release decisions.

---

## Related Documents
- [Offline Evaluation Framework Reference](offline-evaluation.md)
- [LangGraph Screening Pipeline Architecture](../architecture/langgraph-pipeline.md)
