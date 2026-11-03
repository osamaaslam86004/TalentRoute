# Offline Evaluation Framework Reference

The **Offline Evaluation Framework** (`app.evaluation.evaluation_pipeline` and `app.scripts.evaluate_offline`) provides versioned, benchmark evaluations for prompt or model modifications without executing live LLM provider calls.

---

## Offline Evaluation Philosophy & Compliance

!!! note "Benchmark Guidelines"
    - **No Live Provider Calls:** Evaluation pipelines consume pre-computed prediction JSONL datasets, preventing unexpected provider costs or API latency during CI/CD runs.
    - **Human Gold Labels as Reference:** Human recruiter evaluations serve as the ground truth reference. LLM-judge outputs are treated strictly as diagnostic proxies.
    - **Lexical Evidence Grounding:** Evidence quote grounding is measured using a lexical token-overlap score between extracted quote strings and application text.
    - **No Auto-Promotion:** Evaluation reports inform engineering release decisions; production prompts are never auto-promoted without human review.

---

## Evaluation Dataset Format (`JSONL`)

Input prediction records must conform to the benchmark JSONL schema:

```json
{
  "case_id": "case-2026-001",
  "application_text": "Experienced Python engineer with 6 years experience building FastAPI services...",
  "job_title": "Senior Python Developer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL"],
  "gold_experience_level": "Senior-level",
  "gold_skills_match": "Match",
  "gold_recommendation": "hr_review",
  "prediction": {
    "experience_level": "Senior-level",
    "experience_confidence": 0.95,
    "experience_evidence": ["6 years experience building FastAPI services"],
    "skills_match": "Match",
    "skills_confidence": 0.92,
    "skills_evidence": ["Python engineer with 6 years experience"],
    "recommendation": "hr_review"
  }
}
```

---

## Metric Definitions

1. **Schema Validity Rate:** Percentage of candidate predictions adhering strictly to required Pydantic output types and confidence ranges (0.0 to 1.0).
2. **Categorical Accuracy & F1 Scores:** Measured independently for `experience_level`, `skills_match`, and `recommendation` against gold human labels.
3. **Evidence Grounding Proxy Score:** Measures verbatim token overlap between extracted evidence quote strings and candidate application text to detect hallucinated evidence quotes.
4. **Confidence Calibration (ECE & Brier Score):**
   - **Expected Calibration Error (ECE):** Measures alignment between model confidence probability buckets and actual categorical precision.
   - **Brier Score:** Mean squared error between model confidence scores and binary correctness.

---

## CLI Execution Guide

### 1. Run Offline Evaluation Benchmark
```bash
python -m app.scripts.evaluate_offline   --dataset datasets/gold_candidate_eval_v1.jsonl   --predictions predictions/model_candidate_v2.jsonl   --output reports/eval_candidate_v2.json   --bootstrap-iterations 1000
```

### 2. Compare Model Candidates against Baseline (CI Release Gate)
```bash
python -m app.scripts.compare_evaluations   --baseline reports/eval_baseline_v1.json   --candidate reports/eval_candidate_v2.json   --output reports/eval_comparison.json   --max-accuracy-drop 0.02   --max-schema-drop 0.00
```

Release gate returns exit code `0` if assertions pass, or code `2` if candidate performance regresses beyond configured thresholds.

---

## Related Documents
- [Blinded A/B Pairwise Review Reference](blinded-review.md)
- [LangGraph Screening Pipeline Architecture](../architecture/langgraph-pipeline.md)
