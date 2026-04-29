# Implementation Plan: AI Log Agent

**Branch**: `main` | **Date**: 2026-04-20 | **Spec**: `/specs/agent_spec.md`, `/specs/tool_spec.md`, `/specs/api_spec.md`, `/specs/architecture.md`, `/data_contracts.md`
**Input**: Feature specifications from `/specs/` and `/data_contracts.md`

## Summary

Build an AI-powered observability agent with four layers: a Next.js frontend for query input and result display, a FastAPI backend API, an agent orchestrator implementing an iterative Azure OpenAI tool-calling loop (observe→think→act→observe), and a tools layer that queries Azure Application Insights / Log Analytics via the Azure Monitor Query SDK. The agent autonomously decides which tools to invoke, correlates log data across sources, and returns structured root-cause analysis with cited evidence.

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript 5+ / Node 20+ (frontend)
**Primary Dependencies**: FastAPI, uvicorn, openai (Azure OpenAI SDK), pydantic, pydantic-settings, azure-monitor-query, azure-identity, httpx (backend); Next.js 14+, React 19+, Mantine UI v7+ (frontend)
**Storage**: N/A (stateless — logs fetched on-demand from Azure Application Insights / Log Analytics)
**Testing**: pytest + pytest-asyncio (backend); Vitest + React Testing Library (frontend)
**Target Platform**: Linux container (backend), Vercel / Node server (frontend)
**Project Type**: Web application (API + SPA)
**Performance Goals**: Agent response < 30s p95 for single confirmation code queries
**Constraints**: Azure OpenAI deployment only; no fine-tuned models; stateless sessions; must handle log payloads > 128k tokens via chunking; all timestamps ISO 8601 normalized; events ordered chronologically; errors separated from info events
**Scale/Scope**: Internal tool, single-tenant, ~50 concurrent users

## Constitution Check (Pre-Phase 0)

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| I | Agent-First Intelligence | ✅ PASS | Architecture specifies LLM-driven tool loop; agent decides tool order; correlates findings autonomously; not a summarizer |
| II | Tool-Based Data Access | ✅ PASS | 3 typed tools (`getLogsByConfirmationCode`, `queryKQL`, `getSessionTimeline`) with schema-defined I/O; no inline data access |
| III | Deterministic Data Retrieval | ✅ PASS | Explicit parameters (confirmationCode, KQL string); time-range bounds enforced; truncation surfaced explicitly |
| IV | Structured & Explainable Responses | ✅ PASS | AgentOutput schema: Summary, Key Findings (with evidence[]), Root Cause (with confidence), Timeline; all conclusions cite log entries |

**Constraints check**:
- Azure OpenAI: ✅ — architecture mandates Azure OpenAI deployment
- Tool-based agent loop: ✅ — iterative observe→think→act→observe cycle
- Large log support: ✅ — two-phase retrieval + 40k token cap per tool + continuation tokens (research.md R-001)
- No hallucination: ✅ — system prompt enforces evidence citation; Finding.evidence mandatory

**Data contracts check** (from `/data_contracts.md`):
- All timestamps normalized to ISO 8601: ✅ — enforced in Pydantic models
- Events ordered chronologically: ✅ — tools sort by timestamp before returning
- Errors clearly separated: ✅ — `LogsToolOutput` has separate `events[]` and `errors[]` fields

**GATE RESULT: PASS** — proceed to Phase 0.

## Project Structure

### Documentation

```text
specs/main/
├── plan.md              # This file
├── research.md          # Phase 0 output (complete)
├── data-model.md        # Phase 1 output (complete)
├── quickstart.md        # Phase 1 output (complete)
├── contracts/           # Phase 1 output (complete)
│   └── api-contract.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app: create_app(), CORS, health check
│   ├── config.py                # Settings (pydantic-settings): Azure OpenAI, Log Analytics
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py            # POST /analyze → run_agent_loop()
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # run_agent_loop(): iterative tool-calling loop
│   │   ├── prompt.py            # SYSTEM_PROMPT, TOOL_DEFINITIONS (OpenAI function schemas)
│   │   └── types.py             # AgentOutput, Finding, TimelineEntry (Pydantic)
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py          # ToolRegistry: name→handler dispatch + schema lookup
│   │   ├── base.py              # BaseTool abstract class (execute, schema)
│   │   ├── logs.py              # GetLogsByConfirmationCodeTool
│   │   ├── kql.py               # QueryKQLTool
│   │   └── timeline.py          # GetSessionTimelineTool
│   │
│   └── models/
│       ├── __init__.py
│       ├── request.py           # AnalyzeRequest
│       └── response.py          # AnalyzeResponse
│
├── tests/
│   ├── conftest.py              # Shared fixtures (mock Azure clients, test app)
│   ├── unit/
│   │   ├── test_orchestrator.py # Agent loop logic with mocked LLM
│   │   ├── test_tools.py        # Each tool with mocked Azure SDK
│   │   └── test_registry.py     # Tool dispatch
│   └── integration/
│       └── test_analyze.py      # POST /analyze end-to-end with mocked externals
│
├── pyproject.toml               # Dependencies, build config, pytest config
├── Dockerfile
└── .env.example                 # Template for required env vars

frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx           # Root layout with MantineProvider
│   │   ├── page.tsx             # Main page: QueryInput + AnalysisResult
│   │   └── providers.tsx        # Client-side providers wrapper
│   │
│   ├── components/
│   │   ├── QueryInput.tsx       # Text input + submit button
│   │   ├── AnalysisResult.tsx   # Renders full AgentOutput
│   │   ├── FindingCard.tsx      # Single finding with severity badge + evidence
│   │   └── Timeline.tsx         # Chronological event timeline
│   │
│   ├── services/
│   │   └── api.ts               # analyzeQuery(query) → POST /analyze
│   │
│   └── types/
│       └── index.ts             # TS mirrors of AgentOutput, Finding, TimelineEntry
│
├── tests/
│   └── components/
│       └── QueryInput.test.tsx
│
├── package.json
├── tsconfig.json
├── next.config.mjs
├── postcss.config.mjs
├── Dockerfile
└── .env.example                 # NEXT_PUBLIC_API_URL
```

**Structure Decision**: Web application (backend + frontend). Backend owns agent orchestration, tool execution, and the API. Frontend is a stateless query/response UI. Both independently deployable.

## Module Responsibilities

### Backend: `app/main.py`
- Create FastAPI application with lifespan handler
- Configure structured JSON logging at startup: `logging.basicConfig` with JSON formatter, level from `Settings.LOG_LEVEL`
- In lifespan `startup`: create `AsyncAzureOpenAI` client, `LogsQueryClient` (via `DefaultAzureCredential`), `ToolRegistry` (with injected `LogsQueryClient` + `workspace_id`), and `AgentOrchestrator` (with injected `AsyncAzureOpenAI` client + registry). Store on `app.state`.
- In lifespan `shutdown`: close HTTP clients
- Configure CORS middleware (origins from config)
- Mount API router
- `GET /health` returns `{"status": "healthy"}`

### Backend: `app/config.py`
- `Settings(BaseSettings)` loaded from env vars / `.env`
- Fields: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, `LOG_ANALYTICS_WORKSPACE_ID`, `CORS_ORIGINS`, `MAX_AGENT_ITERATIONS` (default 10), `TOOL_RESPONSE_MAX_TOKENS` (default 40000), `LOG_LEVEL` (default "INFO")

### Backend: `app/api/routes.py`
- `POST /analyze` accepts `AnalyzeRequest`, returns `AnalyzeResponse`
- Generates `request_id` (UUID), measures `duration_ms`
- Sets `request_id` on logging context (via `logging.LoggerAdapter` or contextvars) for correlation
- Logs request received (INFO) and response sent (INFO with `duration_ms`)
- Delegates to `orchestrator.run(query, request_id)`
- Returns 504 on timeout (60s)

### Backend: `app/agent/orchestrator.py` — The Agent Loop
- `AgentOrchestrator.__init__(self, client: AsyncAzureOpenAI, registry: ToolRegistry, settings: Settings)` — receives pre-configured clients via DI
- Core method: `async def run(self, query: str) -> AgentOutput`

```text
ALGORITHM:
1. messages = [system_prompt, {"role": "user", "content": query}]
2. iteration = 0
3. WHILE iteration < MAX_ITERATIONS:
   a. response = await openai_client.chat.completions.create(
        model=deployment, messages=messages, tools=TOOL_DEFINITIONS
      )
   b. choice = response.choices[0]
   c. IF choice.finish_reason == "tool_calls":
        i.  Append assistant message (with tool_calls) to messages
        ii. FOR EACH tool_call in choice.message.tool_calls:
            - TRY: Dispatch via ToolRegistry.execute(name, arguments)
              - On transient error (429, 503, timeout):
                  Retry with exponential backoff (max 3 retries, 1s/2s/4s)
                  If still failing after retries → pass error to LLM as tool result
              - On non-transient error (400, 404, SDK exception, invalid KQL):
                  Pass error to LLM as tool result: {"error": "<error_type>: <message>"}
            - Append {"role": "tool", "tool_call_id": id, "content": result_json_or_error}
        iii. iteration += 1
   d. ELSE IF choice.finish_reason == "stop":
        i.  Parse choice.message.content as JSON → validate against AgentOutput
        ii. Populate tools_invoked from collected tool names
        iii. RETURN AgentOutput
   e. ELSE (unexpected finish_reason or LLM API error):
        i.  On transient LLM error (429, 500, timeout): retry with backoff (max 2 retries)
        ii. On persistent LLM failure: return partial AgentOutput with warning
4. IF max iterations exceeded:
   - Return partial AgentOutput with warning "Max iterations reached"
5. ERROR CLASSIFICATION:
   - Transient (retry silently): HTTP 429, 503, connection timeout, Azure throttling
   - Non-transient (pass to LLM): HTTP 400, 404, invalid KQL syntax, auth failures, schema validation errors
```

### Backend: `app/agent/prompt.py`
- `SYSTEM_PROMPT`: instructs the model per `agent_spec.md` — role, analysis rules, output format (JSON matching AgentOutput schema)
- `get_tool_definitions()`: delegates to `ToolRegistry.get_definitions()` — schemas are auto-generated from tool Pydantic input models, NOT manually maintained here

### Backend: `app/agent/types.py`
- Pydantic models: `Finding`, `TimelineEntry`, `AgentOutput` (from data-model.md)

### Backend: `app/tools/base.py`
- `BaseTool(ABC)`: abstract with `name: str`, `description: str`, `input_model: type[BaseModel]`, `async execute(args: BaseModel) -> BaseModel`
- `schema() -> dict`: auto-generates OpenAI function-calling JSON schema from `input_model.model_json_schema()`, combined with `name` and `description`. Guarantees LLM schema matches validation code.
- Constructor receives injected dependencies (e.g., `LogsQueryClient`, `workspace_id`) — no internal client creation

### Backend: `app/tools/registry.py`
- `ToolRegistry`: dict mapping tool name → BaseTool instance
- `execute(name, args_json)` → deserialize args via tool's `input_model.model_validate_json()`, call `tool.execute()`, serialize result via `.model_dump_json()`
- `get_definitions()` → collects `tool.schema()` from each registered tool → list of OpenAI function schemas (auto-generated, not hand-written)

### Backend: `app/tools/logs.py` — `GetLogsByConfirmationCodeTool`
- Input: `{"confirmationCode": str}`
- Constructs KQL: query `customEvents` and `exceptions` filtered by `confirmationCode` custom dimension
- Calls `LogsQueryClient.query_workspace(workspace_id, query, timespan)`
- **Internal normalization** (before returning):
  - Normalizes all timestamps to ISO 8601
  - Sorts events + errors by timestamp (chronological)
  - Separates events and errors into distinct lists
  - Applies 40k token cap; sets `truncated=True` + `continuation_token` if exceeded
- Returns `LogsToolOutput` Pydantic model

### Backend: `app/tools/kql.py` — `QueryKQLTool`
- Input: `{"query": str}`
- Passes raw KQL to `LogsQueryClient.query_workspace()`
- **Internal normalization**: normalizes timestamps to ISO 8601, applies token cap
- Returns `KqlToolOutput` Pydantic model

### Backend: `app/tools/timeline.py` — `GetSessionTimelineTool`
- Input: `{"confirmationCode": str}`
- KQL query ordering all events by timestamp for the confirmation code
- **Internal normalization**: normalizes timestamps to ISO 8601, ensures chronological order, applies token cap
- Returns `TimelineToolOutput` Pydantic model

### Frontend: `src/services/api.ts`
- `analyzeQuery(query: string): Promise<AnalyzeResponse>` — POST to backend
- Handles loading state, timeout errors, network errors

### Frontend: `src/components/QueryInput.tsx`
- Mantine `TextInput` + `Button`
- Calls `analyzeQuery` on submit — manages loading/error state

### Frontend: `src/components/AnalysisResult.tsx`
- Renders `AgentOutput`: summary, findings list, root cause, timeline, warnings
- Uses `FindingCard` and `Timeline` sub-components

### Frontend: `src/components/FindingCard.tsx`
- Mantine `Card` with severity badge (critical=red, warning=yellow, info=blue)
- Lists evidence entries

### Frontend: `src/components/Timeline.tsx`
- Mantine `Timeline` component rendering chronological events with severity indicators

## Data Flow

```text
┌─────────┐    POST /analyze     ┌──────────┐
│ Next.js  │ ──────────────────► │ FastAPI   │
│ Frontend │                     │ routes.py │
└─────────┘                     └────┬─────┘
                                     │
                                     ▼
                              ┌──────────────┐
                              │ orchestrator │
                              │ .py          │
                              └──────┬───────┘
                                     │
                          ┌──────────┼──────────┐
                          ▼          ▼          ▼
                   ┌──────────┐ ┌────────┐ ┌──────────┐
                   │ Azure    │ │ Azure  │ │ Azure    │
                   │ OpenAI   │ │ OpenAI │ │ OpenAI   │
                   │ (iter 1) │ │(iter 2)│ │ (iter N) │
                   └────┬─────┘ └───┬────┘ └────┬─────┘
                        │           │            │
                   tool_calls  tool_calls   content (final)
                        │           │            │
                        ▼           ▼            │
                   ┌─────────┐ ┌─────────┐      │
                   │ Tool    │ │ Tool    │      │
                   │Registry │ │Registry │      │
                   └────┬────┘ └────┬────┘      │
                        │           │            │
                        ▼           ▼            │
                 ┌────────────────────────┐      │
                 │ Azure Monitor Query    │      │
                 │ (Log Analytics /       │      │
                 │  App Insights via KQL) │      │
                 └────────────────────────┘      │
                                                 ▼
                                          ┌──────────────┐
                                          │ AgentOutput  │
                                          │ (validated)  │
                                          └──────┬───────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │AnalyzeResponse│
                                          │ + request_id │
                                          │ + duration_ms│
                                          └──────────────┘
```

## Key Functions to Implement

### Backend Core

| Function | File | Signature | Purpose |
|----------|------|-----------|---------|
| `create_app` | `app/main.py` | `() -> FastAPI` | App factory with CORS, router, lifespan |
| `get_settings` | `app/config.py` | `() -> Settings` | Cached settings from env |
| `analyze` | `app/api/routes.py` | `async (req: AnalyzeRequest) -> AnalyzeResponse` | API endpoint handler |
| `run_agent_loop` | `app/agent/orchestrator.py` | `async (query: str, settings: Settings) -> AgentOutput` | Iterative tool-calling agent loop |
| `build_system_prompt` | `app/agent/prompt.py` | `() -> str` | Agent system prompt from agent_spec |
| `get_tool_definitions` | `app/agent/prompt.py` | `() -> list[dict]` | OpenAI function schemas from tool_spec |

### Tools

| Function | File | Signature | Purpose |
|----------|------|-----------|---------|
| `ToolRegistry.register` | `app/tools/registry.py` | `(tool: BaseTool) -> None` | Register a tool by name |
| `ToolRegistry.execute` | `app/tools/registry.py` | `async (name: str, args: str) -> str` | Dispatch + execute + serialize |
| `GetLogsByConfirmationCodeTool.execute` | `app/tools/logs.py` | `async (args: LogsToolInput) -> LogsToolOutput` | KQL query for session logs |
| `QueryKQLTool.execute` | `app/tools/kql.py` | `async (args: KqlToolInput) -> KqlToolOutput` | Raw KQL execution |
| `GetSessionTimelineTool.execute` | `app/tools/timeline.py` | `async (args: TimelineToolInput) -> TimelineToolOutput` | Ordered session timeline |

### Frontend Core

| Function | File | Signature | Purpose |
|----------|------|-----------|---------|
| `analyzeQuery` | `services/api.ts` | `(query: string) => Promise<AnalyzeResponse>` | HTTP client |
| `QueryInput` | `components/QueryInput.tsx` | `React.FC` | Query form with loading state |
| `AnalysisResult` | `components/AnalysisResult.tsx` | `React.FC<{data: AgentOutput}>` | Full result renderer |
| `FindingCard` | `components/FindingCard.tsx` | `React.FC<{finding: Finding}>` | Single finding display |
| `Timeline` | `components/Timeline.tsx` | `React.FC<{entries: TimelineEntry[]}>` | Chronological timeline |

## Constitution Check (Post-Phase 1 Design)

*Re-evaluation after full design complete.*

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| I | Agent-First Intelligence | ✅ PASS | `orchestrator.py` implements iterative loop; LLM autonomously selects tools; `AgentOutput.tools_invoked` audit trail; no hardcoded analysis paths |
| II | Tool-Based Data Access | ✅ PASS | 3 tools with typed Pydantic I/O models; `ToolRegistry` dispatches by name; all Azure queries in `app/tools/`; `BaseTool` enforces interface |
| III | Deterministic Data Retrieval | ✅ PASS | Explicit params; `truncated` + `continuation_token`; time-range bounds; errors surface explicitly, no silent degradation |
| IV | Structured & Explainable Responses | ✅ PASS | `AgentOutput` schema; `Finding.evidence` cites log entries; `root_cause_confidence` separates confirmed/probable/uncertain; `warnings` for truncation |

**Data contracts compliance** (from `/data_contracts.md`):
- ISO 8601 timestamps: ✅ — Pydantic models enforce string format, tools normalize before return
- Chronological ordering: ✅ — tools sort by timestamp; timeline tool orders by default
- Error separation: ✅ — `LogsToolOutput` has distinct `events[]` and `errors[]` fields

**GATE RESULT: PASS** — no violations. Ready for task generation.

## Clarifications

### Session 2026-04-20

- Q: When a tool call fails (Azure API timeout, 429, invalid KQL, SDK exception), what should the orchestrator do? → A: Hybrid — orchestrator retries transient errors (429, 503) silently with exponential backoff; passes non-transient errors to the LLM as tool results so the agent can adapt (retry differently, try another tool, or produce partial analysis citing the failure).
- Q: Should there be injectable client abstractions wrapping Azure SDKs (LogsQueryClient, AsyncAzureOpenAI)? → A: Inject pre-configured SDK client instances via constructor (DI). No wrapper layer. Clients created once in main.py lifespan, passed to tools/orchestrator. Tests pass mocks through constructors.
- Q: What logging strategy should the system use? → A: Standard Python `logging` with structured JSON output. Per-module loggers via `logging.getLogger(__name__)`. All entries include `request_id` for correlation. Log: each LLM call (model, token usage, finish_reason), each tool dispatch (tool name, args summary, duration_ms, success/error), errors with stack traces. No dedicated logging module needed.
- Q: How are tool schemas for the LLM derived — manually written or auto-generated? → A: Auto-generated from each tool's Pydantic input model via `model_json_schema()`. Each BaseTool has an `input_model` property and a `schema()` method. ToolRegistry.get_definitions() collects from all tools. No manual schema files — guarantees the LLM sees exactly what the code validates.
- Q: Where should data normalization happen — timestamp normalization, sorting, token-cap truncation? → A: Each tool normalizes internally in its `execute()` method before returning the Pydantic model. Timestamp ISO 8601 normalization, chronological sorting, and token-cap truncation are all co-located with the data source logic. The registry remains a thin dispatch layer with no post-processing.

## Complexity Tracking

No constitution violations — table not required.
