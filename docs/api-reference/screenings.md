# Screenings API Endpoint Reference

The `/api/v1/screenings` resource manages candidate application submissions, background evaluations, and data retention deletion.

---

## 1. Submit Screening Application (`POST /api/v1/screenings`)

Submits a candidate resume/application for asynchronous screening.

- **Permission Required:** `screening:write` (`recruiter`, `service`, `admin`)
- **Status Code:** `202 Accepted`

### Request Headers
```http
POST /api/v1/screenings HTTP/1.1
Host: api.screening.example.com
Authorization: Bearer <JWT>
Content-Type: application/json
Idempotency-Key: submission-req-2026-9901
```

### Request Body (`ScreeningCreateRequest`)
```json
{
  "application_text": "Experienced Software Engineer with 6+ years in Python, AsyncIO, PostgreSQL, and AWS...",
  "job_title": "Senior Backend Platform Engineer",
  "required_skills": [
    "Python",
    "FastAPI",
    "PostgreSQL",
    "Distributed Systems"
  ]
}
```

### Response Payload (`ScreeningAccepted`)
```json
{
  "id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8",
  "status": "pending",
  "created_at": "2026-11-01T04:50:00.123456Z"
}
```

### Special Error Behaviors
- **`413 Payload Too Large`:** Returned if `len(application_text) > MAX_APPLICATION_CHARS`.
- **`409 Conflict`:** Returned if an `Idempotency-Key` is re-sent with a modified request payload fingerprint. Re-sending identical payloads returns the original `202 Accepted` object.

---

## 2. List Screenings (`GET /api/v1/screenings`)

Retrieves paginated screening records for the authenticated tenant.

- **Permission Required:** `screening:read`
- **Query Parameters:**
  - `limit` (`int`, default `50`, min `1`, max `100`)
  - `offset` (`int`, default `0`, min `0`)

### Response Payload (`ScreeningList`)
```json
{
  "items": [
    {
      "id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8",
      "status": "processed",
      "job_title": "Senior Backend Platform Engineer",
      "recommendation": "hr_review",
      "created_at": "2026-11-01T04:50:00.123456Z"
    }
  ],
  "limit": 50,
  "offset": 0,
  "count": 1
}
```

---

## 3. Get Screening Detail (`GET /api/v1/screenings/{screening_id}`)

Retrieves full screening evaluation details, structured assessments, and rationale.

- **Permission Required:** `screening:read`

### Response Payload (`ScreeningRead`)
```json
{
  "id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8",
  "tenant_id": "tenant-corp-alpha",
  "status": "processed",
  "job_title": "Senior Backend Platform Engineer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Distributed Systems"],
  "recommendation": "hr_review",
  "recommendation_reason": "Skills evidence supports prioritizing a human HR review; no hiring decision has been made.",
  "human_review_required": true,
  "experience_assessment": {
    "level": "Senior-level",
    "confidence": 0.88,
    "evidence": ["6+ years in Python, AsyncIO, PostgreSQL"],
    "rationale": "Explicit work history confirms senior-level engineering experience."
  },
  "skills_assessment": {
    "match": "Match",
    "confidence": 0.92,
    "evidence": ["Python", "PostgreSQL", "FastAPI"],
    "gaps": [],
    "rationale": "Direct evidence present for all essential backend role skills."
  },
  "prompt_version": "screening-prompts-v1.1.0",
  "rubric_version": "screening-rubric-v1",
  "created_at": "2026-11-01T04:50:00.123456Z",
  "updated_at": "2026-11-01T04:50:12.654321Z"
}
```

---

## 4. Delete Screening (`DELETE /api/v1/screenings/{screening_id}`)

Hard deletes screening records for privacy erasure compliance (GDPR/CCPA).

- **Permission Required:** `screening:delete` (`privacy_officer` role)
- **Status Code:** `204 No Content`
- **Audit Action Emitted:** `screening.deleted`

---

## Related Documents
- [API Overview](overview.md)
- [Human Reviews API](reviews.md)
