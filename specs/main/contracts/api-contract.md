# API Contract: AI Log Agent

**Version**: 1.0.0
**Base URL**: `http://localhost:8000`
**Protocol**: HTTP/JSON (REST)

---

## POST /analyze

Accepts a user query and returns structured agent analysis.

### Request

**Content-Type**: `application/json`

```json
{
  "query": "string"
}
```

| Field | Type | Required | Constraints | Example |
|-------|------|----------|-------------|---------|
| query | string | yes | 1–2000 chars | `"What happened with confirmation code ABC123?"` |

### Response (200 OK)

**Content-Type**: `application/json`

```json
{
  "answer": {
    "summary": "string",
    "key_findings": [
      {
        "description": "string",
        "severity": "critical | warning | info",
        "evidence": ["string"]
      }
    ],
    "root_cause": "string | null",
    "root_cause_confidence": "confirmed | probable | uncertain | null",
    "timeline": [
      {
        "timestamp": "string (ISO 8601)",
        "event": "string",
        "severity": "critical | warning | info | null"
      }
    ],
    "tools_invoked": ["string"],
    "warnings": ["string"] | null
  },
  "request_id": "string (UUID)",
  "duration_ms": 0
}
```

### Error Responses

#### 422 Validation Error

```json
{
  "detail": [
    {
      "loc": ["body", "query"],
      "msg": "string",
      "type": "string"
    }
  ]
}
```

#### 500 Internal Server Error

```json
{
  "detail": "string"
}
```

#### 504 Gateway Timeout

Returned when the agent loop exceeds the maximum allowed time (60s).

```json
{
  "detail": "Agent processing timed out"
}
```

---

## GET /health

Health check endpoint.

### Response (200 OK)

```json
{
  "status": "healthy"
}
```

---

## Tool Contracts (Internal)

These are internal tool interfaces invoked by the agent — not exposed as HTTP endpoints.

### getLogsByConfirmationCode

```json
// Input
{ "confirmationCode": "string" }

// Output
{
  "events": [
    { "timestamp": "ISO 8601", "message": "string", "type": "string" }
  ],
  "errors": [
    { "timestamp": "ISO 8601", "error": "string" }
  ],
  "truncated": false,
  "continuation_token": null
}
```

### queryKQL

```json
// Input
{ "query": "string" }

// Output
{
  "rows": [ { "column": "value" } ],
  "truncated": false
}
```

### getSessionTimeline

```json
// Input
{ "confirmationCode": "string" }

// Output
{
  "timeline": [
    { "timestamp": "ISO 8601", "event": "string" }
  ],
  "truncated": false
}
```

---

## CORS

- Allowed origins: configurable via `CORS_ORIGINS` env var
- Default development: `http://localhost:3000`
- Methods: `POST`, `GET`, `OPTIONS`
- Headers: `Content-Type`
