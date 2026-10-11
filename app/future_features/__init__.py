"""Implemented enterprise controls and offline model-quality tooling.

- oidc_auth/security: verified OIDC and role/scope based permissions.
- audit_events/audit_integrity: append-only, hash-chained privacy-safe audit records and verification.
- pii_redaction: configurable pre-provider PII minimization.
- reviewer_feedback: append-only reviewer/appeal history.
- evaluation_pipeline/pairwise_blinding: versioned offline evaluations and blinded comparisons.
"""
