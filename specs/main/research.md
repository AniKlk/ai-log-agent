# Research: AI Log Agent

**Date**: 2026-04-20
**Input**: Constitution check unknowns, technology choices, integration patterns

## R-001: Large Log Chunking Strategy

**Context**: Constitution requires handling log inputs exceeding single-context-window limits. Azure OpenAI models (GPT-4o) have 128k token context windows, but log payloads for long sessions can exceed this.

### Decision: Two-Phase Retrieval with Selective Expansion

1. **Initial retrieval**: `getLogsByConfirmationCode` returns a summarized
   event list (timestamps, event type, severity, truncated message). This
   fits within context for most sessions.
2. **Selective expansion**: If the summarized list exceeds 80k tokens, the
   agent receives a paginated view (first N events + last N events + error
   events). The agent can then request specific time-range slices via
   `queryKQL` to drill into areas of interest.
3. **Tool-level truncation**: Each tool response is capped at 40k tokens.
   If raw results exceed this, the tool returns a truncated payload with a
   `truncated: true` flag and a `continuationToken` for follow-up calls.

### Rationale

- Keeps the agent loop deterministic — the agent always sees a consistent
  shape of data
- Avoids blind summarization that could hide critical error events
- Aligns with Constitution Principle III (Deterministic Data Retrieval) by
  using explicit pagination parameters
- Aligns with Constitution Principle II (Tool-Based Data Access) — chunking
  is handled at the tool boundary, not via prompt engineering

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Pre-summarize all logs before agent sees them | Violates Principle I — agent can't drive investigation if data is pre-filtered |
| Embed all logs in a single prompt | Exceeds context window; unreliable with very long sessions |
| RAG with vector DB | Over-engineered for structured log data; adds infrastructure complexity without clear benefit |

---

## R-002: Azure OpenAI Tool-Calling Agent Loop Pattern

**Context**: The agent must implement an iterative tool-calling loop using Azure OpenAI's function calling API.

### Decision: Native Azure OpenAI Function Calling with Manual Loop

Use the `openai` Python SDK with Azure configuration. Implement a manual
agent loop (not LangChain or Semantic Kernel) to maintain full control
over the observe→think→act→observe cycle.

**Loop structure**:
```
1. Build messages array: [system_prompt, user_query]
2. Call Azure OpenAI chat.completions.create(tools=TOOL_DEFINITIONS)
3. If response contains tool_calls:
   a. For each tool_call: dispatch to tool registry, execute, collect result
   b. Append assistant message (with tool_calls) + tool result messages
   c. Go to step 2
4. If response is a content message (no tool_calls):
   a. Parse structured output
   b. Return to user
5. Safety: max 10 iterations; if exceeded, return partial results with warning
```

### Rationale

- Direct SDK usage avoids framework abstractions that obscure the agent loop
- Manual loop gives full control over iteration limits, error handling, and
  observability (logging each step)
- Aligns with Constitution Principle I — the LLM autonomously decides tool
  invocations; the loop merely dispatches
- Azure OpenAI's native function calling is stable, well-documented, and
  supports parallel tool calls

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| LangChain AgentExecutor | Heavy abstraction; harder to debug; unnecessary dependency for 3 tools |
| Semantic Kernel | .NET-first; Python support less mature; over-engineered for this scope |
| AutoGen | Multi-agent framework; we need single-agent with tool loop, not multi-agent conversations |

---

## R-003: Azure Application Insights / Log Analytics Integration

**Context**: Tools must query Azure Application Insights and Log Analytics for session logs.

### Decision: Azure Monitor Query SDK (`azure-monitor-query`)

Use the `azure-monitor-query` Python SDK for KQL queries against Log
Analytics workspaces. Use `azure-identity` for authentication via
`DefaultAzureCredential` (supports managed identity in production,
`az login` for local dev).

**Key implementation details**:
- `getLogsByConfirmationCode`: Executes a KQL query filtering
  `customEvents` and `exceptions` by `confirmationCode` custom dimension
- `queryKQL`: Passes raw KQL to `LogsQueryClient.query_workspace()`
- `getSessionTimeline`: KQL query ordering events by `timestamp` for a
  given confirmation code
- All queries include explicit time-range parameters (default: last 7 days)
- Results are mapped to typed Pydantic models before returning to the agent

### Rationale

- Official Azure SDK; well-maintained; supports async
- `DefaultAzureCredential` aligns with Prometric security standards
  (managed identity, no secrets in code)
- KQL is the native query language for Application Insights — no
  translation layer needed

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| REST API directly | SDK handles auth, pagination, retries; raw REST adds boilerplate |
| Application Insights REST API (v1) | Deprecated in favor of Azure Monitor Query |
| Exporting logs to a separate DB | Adds infrastructure; violates simplicity; unnecessary for query-only workload |

---

## R-004: FastAPI Backend Design

**Context**: Backend serves as the API layer and agent orchestrator.

### Decision: FastAPI with async endpoints

- Single `POST /analyze` endpoint accepts `{ "query": "string" }`
- Endpoint delegates to `agent.orchestrator.run_agent_loop()`
- `pydantic-settings` for configuration (Azure OpenAI endpoint, deployment
  name, Log Analytics workspace ID)
- `httpx.AsyncClient` for any outbound HTTP (if needed beyond SDK)
- CORS middleware configured for frontend origin
- Health check endpoint `GET /health`

### Rationale

- FastAPI is async-native, which matters for the agent loop (multiple
  sequential Azure API calls per request)
- Pydantic models enforce typed request/response contracts
- Minimal framework overhead for a single-endpoint API

---

## R-005: Next.js Frontend Design

**Context**: Frontend provides query input and displays structured agent responses.

### Decision: Next.js 14 App Router + Mantine UI v7

- Single-page layout with query input form and response display
- Mantine UI for consistent, accessible components (per Prometric standards)
- Client-side fetch to `POST /analyze`
- Response rendered as structured sections: Summary, Key Findings,
  Root Cause, Timeline
- Loading state with skeleton/spinner during agent processing
- Error handling for timeouts and API errors

### Rationale

- Next.js App Router is the current standard for React applications
- Mantine UI aligns with Prometric Design System requirements
- Simple SPA pattern — no SSR needed for an internal tool

---

## R-006: Structured Agent Output Schema

**Context**: Constitution Principle IV requires structured, explainable responses.

### Decision: Typed Pydantic Response Model

```python
class Finding(BaseModel):
    description: str
    severity: Literal["critical", "warning", "info"]
    evidence: list[str]  # specific log entries cited

class TimelineEntry(BaseModel):
    timestamp: str
    event: str
    severity: Literal["critical", "warning", "info"] | None = None

class AgentOutput(BaseModel):
    summary: str
    key_findings: list[Finding]
    root_cause: str | None  # None if not identifiable
    root_cause_confidence: Literal["confirmed", "probable", "uncertain"] | None
    timeline: list[TimelineEntry]
    tools_invoked: list[str]  # audit trail of tools called
    warnings: list[str] | None = None  # e.g., truncated data
```

The agent's system prompt instructs it to return JSON matching this schema.
The orchestrator parses and validates the final response.

### Rationale

- Typed models ensure every response is structurally valid
- `evidence` field on findings directly supports the "cite specific log
  entries" requirement
- `root_cause_confidence` distinguishes confirmed vs. hypothesized causes
- `tools_invoked` provides auditability per Principle II
