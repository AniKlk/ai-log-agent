# Tool Specification (Cosmos + Logs Integrated)

---

## 🧠 Design Principles

* Tools are **intent-based**, not infrastructure-based
* Tools abstract away:

  * database names
  * container names
  * KQL queries
* All outputs must follow **data_contracts.md**
* Tools may internally query multiple sources (Cosmos + App Insights)

---

# 🔹 Tool: getSessionData

## Description

Fetch all session-related system data for a given confirmation code.

## Capabilities

* Retrieve system logs (App Insights)
* Retrieve session metadata (Cosmos DB — exam-session)
* Retrieve session logs (Cosmos DB — session-log)
* Retrieve conference/Twilio data (Cosmos DB — conference)
* Retrieve proctor assignment data (Cosmos DB — assignments)
* Identify lifecycle events:

  * session start
  * session end
  * disconnections
  * failures
  * proctor assignments
  * conference joins/leaves

## Input

{
"confirmationCode": "string"
}

## Output

{
"events": [
{
"timestamp": "ISO 8601 string",
"message": "string",
"type": "info | error | disconnect",
"source": "app-insights | session-log"
}
],
"errors": [
{
"timestamp": "ISO 8601 string",
"error": "string"
}
],
"metadata": {
"sessionId": "string",
"examSessionId": "string",
"status": "string"
},
"conference": {
"conferenceId": "string | null",
"events": [
  { "timestamp": "ISO 8601 string", "event": "string" }
]
},
"assignment": {
"proctorId": "string | null",
"assignedAt": "ISO 8601 string | null",
"status": "string | null"
},
"truncated": "boolean",
"source_summary": {
"app_insights_events": "number",
"cosmos_session_records": "number",
"cosmos_session_log_records": "number",
"cosmos_conference_records": "number",
"cosmos_assignment_records": "number"
}
}

---

# 🔹 Tool: getChatHistory

## Description

Fetch candidate and proctor chat messages for a session.

## Capabilities

* Retrieve chat logs from Cosmos DB
* Order messages chronologically
* Identify:

  * candidate messages
  * proctor messages

## Input

{
"confirmationCode": "string"
}

## Output

{
"messages": [
{
"timestamp": "ISO 8601 string",
"sender": "candidate | proctor",
"message": "string"
}
],
"truncated": "boolean"
}

---

# 🔹 Tool: getSessionTimeline

## Description

Return a unified chronological timeline combining system events and chat messages.

## Capabilities

* Merge:

  * system logs (App Insights)
  * chat messages (Cosmos DB)
* Normalize timestamps
* Sort all events chronologically

## Input

{
"confirmationCode": "string"
}

## Output

{
"timeline": [
{
"timestamp": "ISO 8601 string",
"event": "string",
"source": "system | chat"
}
],
"truncated": "boolean"
}

---

# 🔹 Tool: queryKQL

## Description

Execute advanced queries on Azure logs for diagnostics and analysis.

## Capabilities

* Run custom KQL queries
* Retrieve filtered log data
* Target either ProProctor (application) or Infrastructure workspace
* Used for:

  * debugging
  * deep investigations
  * infrastructure-level diagnostics

## Input

{
"query": "string",
"workspace": "proproctor | infrastructure (optional, default: proproctor)"
}

## Output

{
"rows": [],
"truncated": "boolean"
}

---

# 📊 Data Source Abstractions

## Session Data

* Represents system-level events
* Includes:

  * application logs
  * session lifecycle events

## Chat Data

* Represents communication between candidate and proctor
* Stored separately from system logs

## Timeline Data

* Unified view combining multiple data sources
* Ordered chronologically

---

# ⚠️ Constraints

* All timestamps must be ISO 8601
* All outputs must be normalized
* Tool must enforce token limits (~40k)
* Tool must return structured JSON only
* Tool must handle missing data gracefully

---

# 🗄️ Data Source Mapping

## Cosmos DB Containers

| Database | Container | Primary Key | Lookup Method |
|----------|-----------|-------------|---------------|
| ExamSession | exam-session | ConfirmationCode | Direct (entry point) |
| ExamSession | session-log | ExamSessionId | Chained (via exam-session) |
| ExamChat | exam-chat | ExamSessionId | Chained (via exam-session) |
| PPR.Conferences | conference | ExamSessionId | Chained (via exam-session) |
| Assignment | assignments | ExamSessionId | Chained (via exam-session) |

**Resolution chain**: ConfirmationCode → exam-session → ExamSessionId → {session-log, exam-chat, conference, assignments}

**Resolution implementation**: Shared async helper `_resolve_exam_session_id(cosmos_client, confirmation_code)` in `tools/_cosmos_helpers.py`. Each tool calls it internally. No cross-tool caching — each tool invocation resolves independently. Returns `(exam_session_id, session_metadata)` or raises if not found.

## App Insights / Log Analytics

| Workspace | Scope | Resources | Used By |
|-----------|-------|-----------|--------|
| ProProctor UAT | Application logs | app-proproctor-*-UAT (ResourceGroup: UAT, Subscription: Greyshore) | getSessionData, getSessionTimeline, getChatHistory, queryKQL (default) |
| Infrastructure | Infrastructure logs | Separate Log Analytics workspace | queryKQL (when workspace="infrastructure") |

---

# 📝 Clarifications

## Session 2026-04-20

- Q: Should conference (PPR.Conferences/conference) and assignment (Assignment/assignments) data be separate tools or folded into getSessionData? → A: Fold into getSessionData — conference + assignment data is session-scoped metadata the agent almost always needs for troubleshooting.
- Q: How should ExamSessionId resolution work across tools that need it (session-log, exam-chat, conference, assignments all keyed by ExamSessionId, not ConfirmationCode)? → A: Shared async helper `_resolve_exam_session_id` in `tools/_cosmos_helpers.py`; each tool calls it internally; no caching across tools.
- Q: How should dual App Insights workspaces (ProProctor UAT app logs vs. Infrastructure logs) be handled? → A: Default to ProProctor workspace for getSessionData/getSessionTimeline/getChatHistory. Add optional `workspace` parameter to queryKQL (default "proproctor", alternative "infrastructure") so the agent can target infra logs when needed. Config gets both workspace IDs.
- Q: How should ExamSession/session-log data (the actual session lifecycle events) be represented in getSessionData output? → A: Merge session-log records into the existing `events[]` array with a `source` field ("app-insights" | "session-log") to distinguish origin. The agent gets a single chronological event stream with full correlation power.
- Q: What is the config structure for multi-workspace and multi-database Cosmos? → A: Single COSMOS_ENDPOINT (all 4 databases under one Cosmos account). Rename LOG_ANALYTICS_WORKSPACE_ID → PROPROCTOR_WORKSPACE_ID. Add INFRA_WORKSPACE_ID for infrastructure logs.

---

# 🧠 Agent Guidance

* Use getSessionData for root cause analysis
* Use getChatHistory when user asks about communication
* Use getSessionTimeline for sequence of events
* Use queryKQL only for advanced or unspecified queries
