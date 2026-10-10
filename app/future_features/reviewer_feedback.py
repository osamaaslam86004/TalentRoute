"""FUTURE FEATURE: reviewer feedback and quality measurement.

Implementation brief:
- Capture reviewer labels and structured reasons separately from model suggestions.
- Require reviewer identity/role, timestamps, versioned rubrics, and optional appeal outcomes.
- Do not automatically fine-tune or change routing from feedback; require governed evaluation first.
- Build a blinded evaluation set, calibration analysis, subgroup fairness review, and drift checks.
- Tests should verify permissions, immutable history, and separation of model output from final decisions.
"""
