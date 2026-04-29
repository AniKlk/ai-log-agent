# Data Model: AI Log Agent

**Date**: 2026-04-20
**Input**: `research.md`, `tool_spec.md`, `api_spec.md`, `agent_spec.md`

## Entities

### AnalyzeRequest

The inbound user query to the API.

| Field | Type | Required | Validation | Description |
|-------|------|----------|------------|-------------|
| query | string | yes | min 1 char, max 2000 chars | User's natural language question or confirmation code |

---

### AnalyzeResponse

The API response envelope.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| answer | AgentOutput | yes | Structured agent analysis result |
| request_id | string | yes | UUID for tracing |
| duration_ms | int | yes | Total processing time |

---

### AgentOutput

The structured output produced by the agent loop.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| summary | string | yes | Short explanation of what happened |
| key_findings | list[Finding] | yes | Important events discovered (may be empty) |
| root_cause | string \| null | no | Most likely root cause, null if not identifiable |
| root_cause_confidence | "confirmed" \| "probable" \| "uncertain" \| null | no | Confidence level of root cause |
| timeline | list[TimelineEntry] | yes | Ordered sequence of session events (may be empty) |
| tools_invoked | list[string] | yes | Audit trail of tool names called during analysis |
| warnings | list[string] \| null | no | Data truncation or partial-result warnings |

---

### Finding

A single diagnostic finding with evidence.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| description | string | yes | What was found |
| severity | "critical" \| "warning" \| "info" | yes | Severity classification |
| evidence | list[string] | yes | Specific log entries or data points supporting this finding |

---

### TimelineEntry

A single event in the session timeline.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | string (ISO 8601) | yes | When the event occurred |
| event | string | yes | Description of what happened |
| severity | "critical" \| "warning" \| "info" \| null | no | Severity if applicable |

---

## Tool Input/Output Models

### LogsToolInput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| confirmationCode | string | yes | Session confirmation code |

### LogsToolOutput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| events | list[LogEvent] | yes | Session events |
| errors | list[LogError] | yes | Session errors |
| truncated | bool | no | True if results were truncated |
| continuation_token | string \| null | no | Token for fetching next page |

### LogEvent

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | string (ISO 8601) | yes | Event timestamp |
| message | string | yes | Event message |
| type | string | yes | Event type/category |

### LogError

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | string (ISO 8601) | yes | Error timestamp |
| error | string | yes | Error message/details |

---

### KqlToolInput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| query | string | yes | KQL query string |

### KqlToolOutput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| rows | list[dict] | yes | Query result rows |
| truncated | bool | no | True if results were truncated |

---

### TimelineToolInput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| confirmationCode | string | yes | Session confirmation code |

### TimelineToolOutput

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timeline | list[TimelineEvent] | yes | Ordered timeline events |
| truncated | bool | no | True if results were truncated |

### TimelineEvent

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | string (ISO 8601) | yes | Event timestamp |
| event | string | yes | Event description |

---

## Relationships

```text
AnalyzeRequest ──► Agent Loop ──► AnalyzeResponse
                       │
                       ├── calls ──► getLogsByConfirmationCode
                       │                 └── returns LogsToolOutput
                       ├── calls ──► queryKQL
                       │                 └── returns KqlToolOutput
                       └── calls ──► getSessionTimeline
                                         └── returns TimelineToolOutput
                       │
                       └── produces ──► AgentOutput
                                          ├── has many ──► Finding
                                          └── has many ──► TimelineEntry
```

## State Transitions

The agent loop has the following states:

```text
IDLE ──► PROCESSING ──► CALLING_TOOL ──► PROCESSING ──► ... ──► COMPLETE
                                                                    │
                                                              (max 10 iterations)
                                                                    │
                                                              ──► PARTIAL_RESULT
                                                                  (with warning)
```

No persistent state — each request is fully independent (per Constitution: no long-term memory).
