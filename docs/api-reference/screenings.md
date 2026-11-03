# Screenings API Endpoint Reference

The `/api/v1/screenings` endpoint manages candidate application submissions, asynchronous evaluation status polling, listing, and GDPR/CCPA data erasure.

---

## Endpoint Summary

| HTTP Method | Path | Permission Required | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/screenings` | `screening:write` | Submit a candidate application for async screening. |
| `GET` | `/api/v1/screenings` | `screening:read` | List candidate screening records for the tenant. |
| `GET` | `/api/v1/screenings/{screening_id}` | `screening:read` | Retrieve detailed screening results and status. |
| `DELETE` | `/api/v1/screenings/{screening_id}` | `screening:delete` | Hard delete a screening record (GDPR/CCPA compliance). |

---

## 1. Submit Screening Application (`POST /api/v1/screenings`)

Submits candidate application text for asynchronous background evaluation.

- **HTTP Status Code:** `202 Accepted`
- **RBAC Roles Allowed:** `recruiter`, `service`, `admin`

### Request Headers
```http
POST /api/v1/screenings HTTP/1.1
Host: api.screening.example.com
Authorization: Bearer <OIDC_JWT_TOKEN>
Content-Type: application/json
Idempotency-Key: submission-2026-11-01-001
```

### Request Body Schema (`ScreeningCreateRequest`)

| Field Name | Type | Required | Validation Rules | Description |
| :--- | :--- | :--- | :--- | :--- |
| `application_text` | `string` | Yes | Min 20, max 1,000,000 chars (also constrained by `MAX_APPLICATION_CHARS`) | Full raw text of candidate resume or application submission. Stripped of leading/trailing whitespace. |
| `job_title` | `string` | No | Default `"Python Developer"`, min 2, max 160 chars | Target job title for skill evaluation. |
| `required_skills` | `list[string]` | No | Default `["Python"]`, min 1, max 30 items, each 1-500 chars | List of required technical or domain skill names. Automatically deduplicated. |

#### Request Payload Example
```json
{
  "job_title": "Senior Python Developer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "AsyncIO"],
  "application_text": "Senior Software Engineer with 6 years experience in Python, FastAPI, PostgreSQL, and AsyncIO microservices..."
}
```

### Response Payload (`ScreeningAccepted`)
```json
{
  "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "status": "queued",
  "poll_url": "http://api.screening.example.com/api/v1/screenings/a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
}
```

---

## Idempotency & Rate Limit Behavior

### Idempotency Key Processing (`Idempotency-Key`)
1. Calculates SHA-256 fingerprint of `ScreeningCreateRequest` body.
2. If `Idempotency-Key` exists for tenant:
   - If payload fingerprint matches: returns `HTTP 202 Accepted` with existing record ID and status.
   - If payload fingerprint differs: raises `HTTP 409 Conflict` ("Idempotency-Key has already been used with a different request").

### Limit Enforcement
- **Payload Character Limit (`413`):** If `len(application_text) > MAX_APPLICATION_CHARS` (default 30,000), returns `413 Payload Too Large`.
- **Submission Rate Limiting (`429`):** Evaluated against Redis quota (`SCREENING_RATE_LIMIT_REQUESTS` per `SCREENING_RATE_LIMIT_WINDOW_SECONDS`). Exceeding quota returns `429 Too Many Requests` with `Retry-After` header.

---

## 2. List Screenings (`GET /api/v1/screenings`)

Retrieves paginated screening records for the authenticated tenant.

- **HTTP Status Code:** `200 OK`
- **RBAC Roles Allowed:** `recruiter`, `reviewer`, `auditor`, `privacy_officer`, `admin`

### Query Parameters

| Parameter | Type | Default | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `limit` | `integer` | `50` | Min 1, max 100 | Number of records to return per page. |
| `offset` | `integer` | `0` | Min 0 | Pagination offset count. |

### Response Payload (`ScreeningList`)
```json
{
  "items": [
    {
      "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
      "status": "completed",
      "job_title": "Senior Python Developer",
      "required_skills": ["Python", "FastAPI"],
      "experience_level": "Senior-level",
      "experience_confidence": 0.95,
      "skills_match": "Match",
      "skills_confidence": 0.92,
      "recommendation": "hr_review",
      "recommendation_reason": "Skills evidence supports prioritizing a human HR review; no hiring decision has been made.",
      "human_review_required": true,
      "model_version": "gpt-4o-mini",
      "prompt_version": "screening-v1.0.0",
      "attempt_count": 1,
      "created_at": "2026-11-02T12:00:00Z",
      "updated_at": "2026-11-02T12:00:15Z"
    }
  ],
  "limit": 50,
  "offset": 0,
  "count": 1
}
```

---

## 3. Get Screening Details (`GET /api/v1/screenings/{screening_id}`)

Retrieves complete assessment details and processing status for a single screening.

- **HTTP Status Code:** `200 OK`
- **RBAC Roles Allowed:** `recruiter`, `reviewer`, `auditor`, `privacy_officer`, `admin`

### Response Payload (`ScreeningRead`)
```json
{
  "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "status": "completed",
  "job_title": "Senior Python Developer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL"],
  "experience_level": "Senior-level",
  "experience_confidence": 0.95,
  "experience_evidence": ["Software Engineer with 6 years experience in Python"],
  "skills_match": "Match",
  "skills_confidence": 0.92,
  "skills_evidence": ["6 years experience building FastAPI microservices"],
  "skill_gaps": [],
  "recommendation": "hr_review",
  "recommendation_reason": "Skills evidence supports prioritizing a human HR review; no hiring decision has been made.",
  "human_review_required": true,
  "model_version": "gpt-4o-mini",
  "prompt_version": "screening-v1.0.0",
  "redaction_summary": {"email": 1, "phone": 1},
  "error_code": null,
  "attempt_count": 1,
  "created_at": "2026-11-02T12:00:00Z",
  "updated_at": "2026-11-02T12:00:15Z",
  "processed_at": "2026-11-02T12:00:15Z"
}
```

---

## 4. Delete Screening Record (`DELETE /api/v1/screenings/{screening_id}`)

Hard deletes a candidate screening record to comply with GDPR/CCPA data erasure requests.

- **HTTP Status Code:** `204 No Content`
- **RBAC Roles Allowed:** `privacy_officer`, `admin`
- **Audit Action Logged:** `screening.deleted`

---

## Related Documents
- [API Reference Overview](overview.md)
- [Reviewer Feedback API Reference](reviews.md)
- [Audit API Reference](audit.md)
