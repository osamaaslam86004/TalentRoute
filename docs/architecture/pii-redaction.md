# PII Redaction Engine Reference

The **PII Redaction Engine** (`app.privacy.pii_redaction`) performs pre-provider PII minimization on candidate applications before text is transmitted to external LLM provider APIs.

---

## Architectural Privacy Isolation Model

```
[ Raw Candidate Application Text ]
           │
           ├──► Saved intact in internal PostgreSQL DB (For authorized human recruiter view)
           │
           └──► Pre-Provider PII Engine (redact_pii)
                      │
                      ├── Baseline / Strict Policy Regex Filtering
                      │
                      └──► [ Redacted Application Text ] ──► OpenAI Provider API
```

!!! security "Privacy Isolation Guarantee"
    Raw unredacted application text remains securely stored inside the internal PostgreSQL database for human recruiter review. Third-party LLM APIs receive **only** sanitized text containing tokenized replacement placeholders (such as `[REDACTED_EMAIL]`, `[REDACTED_PHONE]`).

---

## Redaction Rule Categories

Rules are applied sequentially: field labels are sanitized first, followed by unlabelled structured identifiers.

| Category Name | Regex Pattern Scope | Replacement Placeholder | Policy Mode |
| :--- | :--- | :--- | :--- |
| `email` | Standard email RFC syntax (`[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}`) | `[REDACTED_EMAIL]` | Baseline & Strict |
| `labelled_name` | Labels (`candidate:`, `applicant:`, `full name:`, `name:`) followed by text | `\1[REDACTED_NAME]` | Baseline & Strict |
| `self_identified_name` | Phrase `my name is <Name>` | `\1[REDACTED_NAME]` | Baseline & Strict |
| `phone` | International and national phone number formats | `[REDACTED_PHONE]` | Baseline & Strict |
| `government_id` | SSN, Passport, Tax ID, National ID, Employee ID patterns | `[REDACTED_ID]` | Baseline & Strict |
| `labelled_address` | Labels (`home address:`, `address:`) followed by text | `\1[REDACTED_ADDRESS]` | Baseline & Strict |
| `street_address` | Street numbers with suffixes (Street, St, Road, Rd, Avenue, Ave, Blvd, Lane, Dr, Way) | `[REDACTED_ADDRESS]` | **Strict Only** |

---

## Policy Modes (`PII_REDACTION_POLICY`)

### 1. Baseline Policy (`PII_REDACTION_POLICY=baseline`)
- Redacts direct structured identifiers: email addresses, phone numbers, government/national IDs, labelled candidate names, and labelled addresses.
- Preserves general narrative resume flow while stripping direct contact points.

### 2. Strict Policy (`PII_REDACTION_POLICY=strict`)
- Includes all baseline rules plus unlabelled `street_address` regex heuristics.
- Recommended for strict compliance regimes requiring aggressive address masking.

---

## Result Data Structure & Zero-Leakage Telemetry

The redaction function returns a `RedactionResult` dataclass:

```python
@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    counts: dict[str, int]
    changed: bool

    @property
    def categories(self) -> list[str]:
        return sorted(category for category, count in self.counts.items() if count > 0)
```

- **Database Metadata:** Replacement counts are stored as JSON in `screening.redaction_summary` (e.g., `{"email": 2, "phone": 1}`).
- **Telemetry Safeguards:** OpenTelemetry spans and metric counters record **only** category count metrics (`redaction_category`). Raw candidate text strings and redacted match values are **never** logged or attached to metric labels.

---

## Related Documents
- [Environment Setup & Configuration Reference](../getting-started/configuration.md)
- [System Architecture Overview](overview.md)
- [Telemetry & Observability Reference](../observability/telemetry.md)
