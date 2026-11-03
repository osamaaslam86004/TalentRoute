# Human Reviewer Feedback & Appeals API Reference

The `/api/v1/screenings/{screening_id}/feedback` and `/api/v1/reviews/{feedback_id}/appeals` endpoints manage human recruiter feedback and compliance appeal determinations (`app.api.v1.endpoints.reviews` and `app.reviews.service`).

---

## Endpoint Summary

| HTTP Method | Path | Permission Required | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/screenings/{screening_id}/feedback` | `review:write` | Submit human reviewer feedback for a completed screening. |
| `GET` | `/api/v1/screenings/{screening_id}/feedback` | `review:read` | List historical human feedback records for a screening. |
| `POST` | `/api/v1/reviews/{feedback_id}/appeals` | `review:appeal` | Record an appeal determination for a human feedback record. |

---

## 1. Submit Human Reviewer Feedback (`POST /api/v1/screenings/{screening_id}/feedback`)

Records a qualified human reviewer's evaluation of candidate screening outputs.

- **HTTP Status Code:** `201 Created`
- **RBAC Roles Allowed:** `reviewer`, `admin`

### Request Body Schema (`ReviewerFeedbackCreate`)

| Field Name | Type | Required | Validation Rules | Description |
| :--- | :--- | :--- | :--- | :--- |
| `label` | `string` | Yes | `agree`, `disagree`, `insufficient_evidence`, `override_recommendation` | Human reviewer assessment decision label (`ReviewLabel` enum). |
| `reason_codes` | `list[string]` | Yes | Min 1, max 8 items | List of standardized reason codes explaining the feedback decision. |
| `notes` | `string \| null` | No | Max 2000 chars | Optional text commentary provided by the human reviewer. |

#### Permitted `reason_codes` Values
- `evidence_missing`
- `evidence_conflicts`
- `role_requirement_mismatch`
- `redaction_review_needed`
- `policy_or_accessibility_concern`
- `rubric_ambiguity`
- `other`

#### Request Payload Example
```json
{
  "label": "agree",
  "reason_codes": ["role_requirement_mismatch"],
  "notes": "Verified candidate possesses 6 years of senior FastAPI experience."
}
```

### Response Payload (`ReviewerFeedbackRead`)
```json
{
  "id": "c3d4e5f6-a7b8-9c0d-1e2f-3a4b5c6d7e8f",
  "tenant_id": "default",
  "screening_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "reviewer_subject": "recruiter-user-123",
  "reviewer_roles": ["reviewer"],
  "record_type": "review",
  "original_feedback_id": null,
  "label": "agree",
  "reason_codes": ["role_requirement_mismatch"],
  "notes": "Verified candidate possesses 6 years of senior FastAPI experience.",
  "appeal_outcome": null,
  "appeal_notes": null,
  "rubric_version": "screening-rubric-v1",
  "created_at": "2026-11-02T13:00:00Z"
}
```

---

## 2. List Feedback History (`GET /api/v1/screenings/{screening_id}/feedback`)

Retrieves historical feedback and appeal records associated with a screening ID, ordered descending by creation date.

- **HTTP Status Code:** `200 OK`
- **RBAC Roles Allowed:** `reviewer`, `auditor`, `admin`

### Query Parameters

| Parameter | Type | Default | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `limit` | `integer` | `50` | Min 1, max 100 | Number of feedback records per page. |
| `offset` | `integer` | `0` | Min 0 | Pagination offset count. |

---

## 3. Record Appeal Outcome (`POST /api/v1/reviews/{feedback_id}/appeals`)

Appends a formal compliance appeal outcome to an existing reviewer feedback record.

- **HTTP Status Code:** `201 Created`
- **RBAC Roles Allowed:** `compliance_officer`, `admin`

!!! note "Append-Only Immutable Ledger Mechanics"
    Appeal determinations **never** overwrite or alter the original reviewer feedback record. Instead, `append_appeal_outcome` writes a new `ReviewerFeedback` database row with `record_type="appeal"` and `original_feedback_id=feedback_id`.

### Request Body Schema (`AppealCreate`)

| Field Name | Type | Required | Validation Rules | Description |
| :--- | :--- | :--- | :--- | :--- |
| `outcome` | `string` | Yes | `upheld`, `revised` | Appeal determination outcome (`AppealOutcome` enum). |
| `notes` | `string \| null` | No | Max 2000 chars | Commentary explaining the appeal decision. |

#### Request Payload Example
```json
{
  "outcome": "upheld",
  "notes": "Compliance review confirmed initial reviewer feedback was justified."
}
```

### Response Payload (`ReviewerFeedbackRead`)
```json
{
  "id": "e5f6a7b8-c9d0-1e2f-3a4b-5c6d7e8f9a0b",
  "tenant_id": "default",
  "screening_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "reviewer_subject": "compliance-officer-456",
  "reviewer_roles": ["compliance_officer"],
  "record_type": "appeal",
  "original_feedback_id": "c3d4e5f6-a7b8-9c0d-1e2f-3a4b5c6d7e8f",
  "label": "agree",
  "reason_codes": ["role_requirement_mismatch"],
  "notes": "Verified candidate possesses 6 years of senior FastAPI experience.",
  "appeal_outcome": "upheld",
  "appeal_notes": "Compliance review confirmed initial reviewer feedback was justified.",
  "rubric_version": "screening-rubric-v1",
  "created_at": "2026-11-02T14:00:00Z"
}
```

---

## Related Documents
- [Screenings API Endpoint Reference](screenings.md)
- [Audit API Endpoint Reference](audit.md)
