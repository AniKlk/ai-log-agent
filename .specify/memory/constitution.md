<!--
  SYNC IMPACT REPORT
  Version change: N/A → 1.0.0 (initial ratification)
  Modified principles: None (initial creation)
  Added sections:
    - Core Principles (4 principles)
    - Constraints
    - Non-Goals
    - Success Criteria
    - Governance
  Removed sections: None
  Templates requiring updates:
    - .specify/templates/plan-template.md ✅ no changes needed
      (Constitution Check uses runtime placeholder)
    - .specify/templates/spec-template.md ✅ no changes needed
    - .specify/templates/tasks-template.md ✅ no changes needed
  Follow-up TODOs: None
-->

# AI Log Agent Constitution

## Purpose

Build an AI-powered observability agent that analyzes Azure logs
and provides actionable insights. The agent MUST act as an
autonomous decision-maker — fetching, correlating, and diagnosing
log data — not merely summarizing what it receives.

## Core Principles

### I. Agent-First Intelligence (NON-NEGOTIABLE)

The AI MUST operate as an autonomous reasoning agent, not a
passive summarizer. This means:

- The agent MUST formulate hypotheses and drive investigation
- The agent MUST decide which tools to invoke and in what order
- The agent MUST correlate findings across multiple data sources
- The agent MUST synthesize root cause analysis from raw evidence

**Rationale**: A summarizer restates input; an agent reasons over
it. The core value proposition depends on autonomous analytical
capability.

### II. Tool-Based Data Access (NON-NEGOTIABLE)

All external data MUST be accessed exclusively through defined
tool interfaces. No data fabrication or assumption is permitted.

- Every log query, API call, or data lookup MUST go through a
  registered tool
- The agent MUST NOT embed hardcoded data or inline lookups
- Tool interfaces MUST be typed, versioned, and independently
  testable
- Tool results MUST be the sole basis for agent conclusions

**Rationale**: Tool-mediated access enforces auditability,
testability, and prevents hallucination of data that was never
retrieved.

### III. Deterministic Data Retrieval

Data retrieval operations MUST produce consistent, reproducible
results given the same inputs.

- Identical queries MUST return identical result sets (within
  the bounds of underlying data freshness)
- Query parameters MUST be explicit — no implicit filters or
  hidden defaults
- Retrieval failures MUST surface as explicit errors, never
  silently degrade to partial results
- All queries MUST include time-range bounds to ensure
  reproducibility

**Rationale**: Determinism enables debugging, regression testing,
and user trust. Non-deterministic retrieval undermines every
downstream analysis.

### IV. Structured & Explainable Responses

Every agent response MUST be accurate, explainable, and
structured.

- Responses MUST use a consistent schema (structured JSON or
  well-defined Markdown sections)
- Every conclusion MUST cite the specific log entries or data
  points that support it
- The agent MUST distinguish between confirmed findings and
  inferred hypotheses
- Uncertainty MUST be stated explicitly — never masked with
  confident language

**Rationale**: Operators act on agent output in production
incident scenarios. Ambiguous or unstructured responses risk
misdiagnosis and delayed resolution.

## Constraints

- **Azure OpenAI**: All LLM inference MUST use an Azure OpenAI
  deployment. No direct OpenAI API or third-party LLM endpoints.
- **Tool-Based Agent Loop**: The system MUST implement an
  iterative tool-calling agent loop (observe → think → act →
  observe). Single-shot prompting is insufficient.
- **Large Log Support**: The system MUST handle log inputs that
  exceed single-context-window limits via chunking, summarization,
  or retrieval strategies.
- **No Hallucination**: The agent MUST NOT fabricate log entries,
  timestamps, error codes, or any data not present in tool
  results. When data is missing, the agent MUST state that
  explicitly.

## Non-Goals

The following are explicitly out of scope:

- **No Fine-Tuning**: The system MUST NOT depend on fine-tuned
  models. All behavior is driven by prompts, tools, and agent
  logic.
- **No Long-Term Memory**: The agent operates statelessly per
  session. No cross-session memory or conversation persistence.
- **No Real-Time Streaming**: The agent processes log snapshots,
  not live streams. No WebSocket, SignalR, or streaming ingestion.

## Success Criteria

A session is considered successful when all of the following hold:

- **SC-001**: User provides a confirmation code or natural
  language question and receives a response within a reasonable
  time bound.
- **SC-002**: The agent fetches the correct, complete log set
  for the given input via tool calls.
- **SC-003**: The agent identifies errors, disconnections, and
  anomalies present in the log data with zero false negatives
  on critical events.
- **SC-004**: The agent produces a root cause analysis that
  cites specific log evidence and distinguishes confirmed causes
  from hypotheses.

## Governance

This constitution supersedes all other project conventions and
practices for the AI Log Agent. All contributions MUST comply.

- **Compliance**: All PRs and code reviews MUST verify adherence
  to the four core principles.
- **Amendment Process**: Changes to this constitution require
  documented rationale, version bump, and updated impact report.
- **Versioning**: Constitution versions follow semantic versioning
  (MAJOR.MINOR.PATCH). Principle removals or redefinitions are
  MAJOR; additions are MINOR; clarifications are PATCH.
- **Review Cadence**: Constitution MUST be reviewed at each PI
  planning boundary or after significant architectural changes.

**Version**: 1.0.0 | **Ratified**: 2026-04-20 | **Last Amended**: 2026-04-20
