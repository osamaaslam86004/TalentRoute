# PII Redaction Engine Reference

The **PII Redaction Engine** (`app.privacy.pii_redaction`) performs pre-provider PII minimization before candidate application text is transmitted to third-party LLM APIs.

---

## Redaction Architecture & Isolation

```
[ Candidate Application Text ] 
           │
           ├──► Saved intact to PostgreSQL DB (Protected internal human view)
           │
           └──► Pre-Provider PII Engine (pii_redaction.py)
                      │
                      ├── Baseline / Strict Regex Minimization
                      │
                      └──► [ Redacted Text ] ──► OpenAI API (LLM Assessment)
```

!!! security "Privacy Isolation Guarantee"
    Raw un-redacted application text remains securely stored in the internal PostgreSQL database for human recruiter review. Third-party LLM providers receive **only** sanitized text containing tokenized replacement placeholders.

---

## Redaction Rule Categories

Rules are applied sequentially: field labels are stripped first, followed by unlabelled structured identifiers.

| Category | Regex Pattern Description | Replacement Token | Policy Mode |
| :--- | :--- | :--- | :--- |
| `email` | Standard email RFC patterns (`[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}`) | `[REDACTED_EMAIL]` | Baseline & Strict |
| `labelled_name` | Labels (`candidate:`, `applicant:`, `full name:`, `name:`) followed by name string | `[REDACTED_NAME]` | Baseline & Strict |
| `self_identified_name` | Phrase `my name is <Name>` | `[REDACTED_NAME]` | Baseline & Strict |
| `phone` | International and national phone number formats | `[REDACTED_PHONE]` | Baseline & Strict |
| `government_id` | SSN, Passport, Tax ID, National ID patterns | `[REDACTED_ID]` | Baseline & Strict |
| `labelled_address` | Labels (`home address:`, `address:`) followed by text | `[REDACTED_ADDRESS]` | Baseline & Strict |
| `street_address` | Numeric street numbers followed by standard street suffixes (St, Ave, Blvd, etc.) | `[REDACTED_ADDRESS]` | **Strict Only** |

---

## Redaction Policy Modes

### Baseline Policy (`PII_REDACTION_POLICY=baseline`)
- Redacts explicit structured identifiers, email addresses, phone numbers, government IDs, and labelled names/addresses.
- Preserves resume flow while stripping obvious direct identifiers.

### Strict Policy (`PII_REDACTION_POLICY=strict`)
- Includes all baseline rules plus unlabelled `street_address` regex heuristics.
- Recommended for high-privacy compliance environments.

---

## Telemetry & Metrics Safeguards

The redaction engine outputs a `RedactionResult` object:

```python
@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    counts: dict[str, int]
    changed: bool
```

- **Aggregated Telemetry:** Metric counter `redaction_category` tracks replacement counts per category (e.g., `email: 2, phone: 1`).
- **Zero-Data Leakage:** Raw redacted text strings and matched values are **never** recorded in OpenTelemetry span attributes, metrics, or logs.

---

## Related Documents
- [Configuration Reference](../getting-started/configuration.md)
- [Telemetry & Observability Reference](../observability/telemetry.md)
