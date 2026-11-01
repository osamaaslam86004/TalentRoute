# Human Reviews & Appeals API Reference

To maintain strict human oversight, human recruiters submit feedback labels and compliance officers process appeals (`/api/v1/screenings/.../feedback` and `/api/v1/reviews/.../appeals`).

---

## Immutability Architecture

!!! info "Append-Only Auditability"
    Human feedback records are **immutable**. Corrections or appeals never overwrite original feedback entries; instead, appeals attach append-only outcome records to maintain an un-altered legal record.

---

## 1. Submit Reviewer Feedback (`POST /api/v1/screenings/{screening_id}/feedback`)

Submits human recruiter assessment labels and standardized reason codes.

- **Permission Required:** `review:write` (`reviewer` role)
- **Status Code:** `200 OK`

### Request Body (`ReviewerFeedbackCreate`)
```json
{
  "label": "match",
  "reason_codes": [
    "STRONG_SYSTEMS_BACKGROUND",
    "QUALIFIED_EXPERIENCE"
  ],
  "notes": "Candidate exhibits strong experience with async python services."
}
```

### Response Payload (`ReviewerFeedbackRead`)
```json
{
  "id": "9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
  "screening_id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8",
  "reviewer_subject": "reviewer-user-404",
  "label": "match",
  "reason_codes": ["STRONG_SYSTEMS_BACKGROUND", "QUALIFIED_EXPERIENCE"],
  "notes": "Candidate exhibits strong experience with async python services.",
  "rubric_version": "screening-rubric-v1",
  "appeal_outcome": null,
  "created_at": "2026-11-01T05:00:00.000000Z"
}
```

---

## 2. List Screening Feedback (`GET /api/v1/screenings/{screening_id}/feedback`)

Retrieves historical feedback entries for a screening record.

- **Permission Required:** `review:read` (`reviewer`, `auditor`)
- **Query Parameters:** `limit` (default `50`), `offset` (default `0`)

---

## 3. Submit Review Appeal (`POST /api/v1/reviews/{feedback_id}/appeals`)

Appends a compliance appeal outcome to an existing feedback record.

- **Permission Required:** `review:appeal` (`compliance_officer` role)

### Request Body (`AppealCreate`)
```json
{
  "appeal_outcome": "upheld",
  "reason_summary": "Compliance review confirms feedback label meets standard guidelines."
}
```

### Appeal Outcome Vocabularies
- `upheld`: Initial reviewer decision confirmed.
- `overturned`: Decision overturned following secondary review.
- `dismissed`: Appeal request dismissed due to procedural grounds.

---

## Related Documents
- [Screenings API Reference](screenings.md)
- [Audit API Reference](audit.md)
