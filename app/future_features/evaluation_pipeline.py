"""FUTURE FEATURE: offline LLM evaluation and drift monitoring.

Implementation brief:
- Keep a versioned, consented/appropriately licensed dataset with human-reviewed gold labels.
- Score schema validity, evidence grounding, reviewer agreement, latency, cost, and slice-based error rates.
- Compare candidate prompts/models with a blinded pairwise review; publish confidence intervals.
- Do not treat an LLM judge as ground truth or auto-update production prompts based only on judge scores.
- Add release gates and model/prompt version metadata before enabling routine evaluation.
"""
