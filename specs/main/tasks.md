# Tasks: AI Log Agent

**Input**: Design documents from `/specs/main/`
**Prerequisites**: plan.md (required), tool_spec.md (required for tools), research.md, data-model.md, contracts/

**Tests**: Not explicitly requested — test tasks omitted. Add via follow-up if needed.

**Organization**: Tasks grouped by user story. Each story is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Web app**: `backend/app/`, `frontend/src/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, env config

- [X] T001 Create backend project structure: `backend/app/__init__.py`, `backend/app/api/__init__.py`, `backend/app/agent/__init__.py`, `backend/app/tools/__init__.py`, `backend/app/models/__init__.py`
- [X] T002 Create `backend/pyproject.toml` with dependencies: fastapi, uvicorn, openai, pydantic, pydantic-settings, azure-monitor-query, azure-identity, azure-cosmos, httpx; dev deps: pytest, pytest-asyncio, ruff
- [X] T003 [P] Create `backend/.env.example` with all required env vars: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, LOG_ANALYTICS_WORKSPACE_ID, COSMOS_ENDPOINT, CORS_ORIGINS, MAX_AGENT_ITERATIONS, TOOL_RESPONSE_MAX_TOKENS, LOG_LEVEL
- [X] T004 [P] Create frontend project: run `npx create-next-app@latest frontend` with TypeScript, App Router, Tailwind CSS, src/ directory
- [X] T005 [P] Install frontend dependencies: `@mantine/core @mantine/hooks @mantine/notifications` in `frontend/`
- [X] T006 [P] Create `frontend/.env.example` with NEXT_PUBLIC_API_URL

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T007 Implement `backend/app/config.py` — `Settings(BaseSettings)` with fields: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, LOG_ANALYTICS_WORKSPACE_ID, COSMOS_ENDPOINT, CORS_ORIGINS (default "http://localhost:3000"), MAX_AGENT_ITERATIONS (default 10), TOOL_RESPONSE_MAX_TOKENS (default 40000), LOG_LEVEL (default "INFO"). Use `model_config = SettingsConfigDict(env_file=".env")`. Do NOT expose database or container names — keep them as private constants within tool modules.
- [X] T008 Implement request/response models in `backend/app/models/request.py` — `AnalyzeRequest(BaseModel)` with `query: str` (min_length=1, max_length=2000)
- [X] T009 [P] Implement agent output types in `backend/app/agent/types.py` — `Finding(BaseModel)` with description, severity (Literal["critical","warning","info"]), evidence (list[str]); `TimelineEntry(BaseModel)` with timestamp, event, severity; `AgentOutput(BaseModel)` with summary, key_findings, root_cause, root_cause_confidence (Literal["confirmed","probable","uncertain"] | None), timeline, tools_invoked, warnings
- [X] T010 [P] Implement response model in `backend/app/models/response.py` — `AnalyzeResponse(BaseModel)` with answer (AgentOutput), request_id (str), duration_ms (int)
- [X] T011 Implement tool I/O models in `backend/app/tools/models.py` — All Pydantic input/output models for 4 tools: `SessionDataInput(confirmationCode: str)`, `SessionDataOutput(events: list[LogEvent], errors: list[LogError], metadata: dict, truncated: bool, source_summary: dict)`, `ChatHistoryInput(confirmationCode: str)`, `ChatHistoryOutput(messages: list[ChatMessage], truncated: bool)`, `TimelineInput(confirmationCode: str)`, `TimelineOutput(timeline: list[TimelineEvent], truncated: bool)`, `KqlInput(query: str)`, `KqlOutput(rows: list[dict], truncated: bool)` and sub-models `LogEvent`, `LogError`, `ChatMessage`, `TimelineEvent`
- [X] T012 Implement `backend/app/tools/base.py` — `BaseTool(ABC)` with: `name: str`, `description: str`, `input_model: type[BaseModel]`, abstract `async execute(args: BaseModel) -> BaseModel`, and `schema() -> dict` method that auto-generates OpenAI function-calling JSON from `input_model.model_json_schema()` combined with `name` and `description`
- [X] T013 Implement `backend/app/tools/registry.py` — `ToolRegistry` class: `register(tool: BaseTool)`, `execute(name: str, args_json: str) -> str` (deserialize via `tool.input_model.model_validate_json()`, call `tool.execute()`, serialize via `.model_dump_json()`), `get_definitions() -> list[dict]` (collects `tool.schema()` from each registered tool)
- [X] T014 Implement `backend/app/api/routes.py` — FastAPI router with `POST /analyze`: generate request_id (uuid4), set request_id on logging context via contextvars, log request received, delegate to `orchestrator.run(query, request_id)`, measure duration_ms, log response sent, return AnalyzeResponse. Handle timeout with 504.
- [X] T015 Implement `backend/app/main.py` — `create_app()` factory: configure structured JSON logging (level from Settings.LOG_LEVEL), lifespan handler that on startup creates `AsyncAzureOpenAI` client, `LogsQueryClient` (via DefaultAzureCredential), `CosmosClient` (via DefaultAzureCredential), `ToolRegistry` (register all 4 tools with injected clients), `AgentOrchestrator` (with injected OpenAI client + registry + settings). Store on `app.state`. On shutdown close clients. Configure CORS middleware. Mount API router. Add `GET /health`.

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Session Data Analysis (Priority: P1) 🎯 MVP

**Goal**: User provides a confirmation code → agent fetches session data (App Insights + Cosmos DB), identifies errors/disconnections/anomalies, returns structured root cause analysis with cited evidence.

**Independent Test**: Send `POST /analyze` with `{"query": "What happened with confirmation code ABC123?"}` → receive structured `AnalyzeResponse` with summary, findings, root cause, timeline, tools_invoked including "getSessionData".

### Implementation for User Story 1

- [X] T016 [US1] Implement `backend/app/tools/session_data.py` — `GetSessionDataTool(BaseTool)`: name="getSessionData", input_model=SessionDataInput, description from tool_spec.md. Constructor receives `LogsQueryClient`, `workspace_id`, `CosmosClient` via DI. `execute()` method: (1) Query App Insights via KQL for customEvents + exceptions filtered by confirmationCode custom dimension with 7-day timespan, (2) Query Cosmos DB for session metadata by confirmationCode, (3) Merge results, (4) Internal normalization: normalize timestamps to ISO 8601, sort by timestamp, separate events/errors, build source_summary, apply 40k token cap with truncated flag. Database and container names are private constants inside this module — never exposed in I/O or config.
- [X] T017 [US1] Implement `backend/app/agent/prompt.py` — `SYSTEM_PROMPT` constant: role (expert observability engineer), responsibilities (understand queries, decide tools, correlate findings, identify disconnections/errors/anomalies, provide root cause), analysis rules (evidence-only, no assumptions, distinguish confirmed vs. hypothesized), output format (JSON matching AgentOutput schema with all fields). `get_tool_definitions()` delegates to `ToolRegistry.get_definitions()`.
- [X] T018 [US1] Implement `backend/app/agent/orchestrator.py` — `AgentOrchestrator` class: `__init__(self, client: AsyncAzureOpenAI, registry: ToolRegistry, settings: Settings)`. `async def run(self, query: str, request_id: str) -> AgentOutput`: build messages [system_prompt, user_query], iterate up to MAX_ITERATIONS: call openai chat.completions.create with tools from registry.get_definitions(), if tool_calls → for each call dispatch via registry.execute() with hybrid error handling (retry transient 429/503 with backoff 1s/2s/4s, pass non-transient errors as tool result), append tool results to messages, log each LLM call (model, tokens, finish_reason) and tool call (name, duration, success/error) with request_id; if stop → parse content as JSON, validate against AgentOutput, populate tools_invoked, return; if max iterations → return partial with warning.
- [X] T019 [US1] Wire US1 end-to-end: update `backend/app/main.py` lifespan to register `GetSessionDataTool` in the `ToolRegistry`, verify `POST /analyze` works with getSessionData tool calls through the full agent loop.

**Checkpoint**: MVP complete — user can query by confirmation code, agent fetches from App Insights + Cosmos DB, returns structured analysis

---

## Phase 4: User Story 2 — Session Timeline (Priority: P2)

**Goal**: Agent can request a unified chronological timeline that merges system events (App Insights) and chat messages (Cosmos DB) for a confirmation code.

**Independent Test**: Query asks "Show me the timeline for ABC123" → agent calls getSessionTimeline → response includes timeline entries with `source: "system" | "chat"` merged and sorted.

### Implementation for User Story 2

- [X] T020 [US2] Implement `backend/app/tools/timeline.py` — `GetSessionTimelineTool(BaseTool)`: name="getSessionTimeline", input_model=TimelineInput. Constructor receives `LogsQueryClient`, `workspace_id`, `CosmosClient` via DI. `execute()` method: (1) Query App Insights for system events by confirmationCode, (2) Query Cosmos DB for chat messages by confirmationCode, (3) Merge into unified timeline with source field ("system" | "chat"), (4) Normalize timestamps to ISO 8601, sort chronologically, apply 40k token cap. Database and container names are private constants — never exposed.
- [X] T021 [US2] Register `GetSessionTimelineTool` in `backend/app/main.py` lifespan ToolRegistry. Verify agent can call getSessionTimeline in the loop.

**Checkpoint**: Agent can produce unified timelines from both data sources

---

## Phase 5: User Story 3 — Chat History (Priority: P3)

**Goal**: Agent can retrieve and analyze candidate/proctor chat messages for a session.

**Independent Test**: Query "Show me the chat for ABC123" → agent calls getChatHistory → response includes chat messages with sender (candidate/proctor) sorted chronologically.

### Implementation for User Story 3

- [X] T022 [US3] Implement `backend/app/tools/chat_history.py` — `GetChatHistoryTool(BaseTool)`: name="getChatHistory", input_model=ChatHistoryInput. Constructor receives `CosmosClient` via DI. `execute()` method: (1) Query Cosmos DB for chat records by confirmationCode, (2) Map to ChatMessage models with timestamp, sender (candidate | proctor), message, (3) Normalize timestamps to ISO 8601, sort chronologically, apply 40k token cap. Database and container names are private constants — never exposed.
- [X] T023 [US3] Register `GetChatHistoryTool` in `backend/app/main.py` lifespan ToolRegistry. Verify agent can call getChatHistory in the loop.

**Checkpoint**: Agent can retrieve and analyze chat logs independently

---

## Phase 6: User Story 4 — Advanced KQL Queries (Priority: P4)

**Goal**: Agent can execute custom KQL queries against Log Analytics for advanced diagnostics.

**Independent Test**: Agent internally decides to run a custom KQL query (e.g., to drill into a specific time range) → calls queryKQL → receives structured rows.

### Implementation for User Story 4

- [X] T024 [US4] Implement `backend/app/tools/kql.py` — `QueryKQLTool(BaseTool)`: name="queryKQL", input_model=KqlInput. Constructor receives `LogsQueryClient`, `workspace_id` via DI. `execute()` method: (1) Pass raw KQL to `LogsQueryClient.query_workspace()` with explicit timespan, (2) Map result rows to list[dict], (3) Normalize timestamps in rows to ISO 8601, apply 40k token cap. No Cosmos DB integration needed for this tool.
- [X] T025 [US4] Register `QueryKQLTool` in `backend/app/main.py` lifespan ToolRegistry. Verify agent can call queryKQL in the loop.

**Checkpoint**: All 4 tools operational — full agent capability available

---

## Phase 7: User Story 5 — Frontend UI (Priority: P5)

**Goal**: User can enter a query in a web UI, submit it, and view the structured agent analysis (summary, findings, timeline, root cause).

**Independent Test**: Open `http://localhost:3000`, enter "What happened with ABC123?", click submit → loading spinner → structured response rendered with summary, finding cards, timeline, root cause section.

### Implementation for User Story 5

- [X] T026 [P] [US5] Create shared TypeScript types in `frontend/src/types/index.ts` — mirror backend models: `Finding` (description, severity, evidence), `TimelineEntry` (timestamp, event, severity), `AgentOutput` (summary, key_findings, root_cause, root_cause_confidence, timeline, tools_invoked, warnings), `AnalyzeResponse` (answer, request_id, duration_ms)
- [X] T027 [P] [US5] Implement API service in `frontend/src/services/api.ts` — `analyzeQuery(query: string): Promise<AnalyzeResponse>` function: POST to `${NEXT_PUBLIC_API_URL}/analyze` with JSON body, handle network errors and timeouts, throw typed errors
- [X] T028 [US5] Implement `frontend/src/app/providers.tsx` — client-side MantineProvider wrapper with theme configuration
- [X] T029 [US5] Implement `frontend/src/app/layout.tsx` — root layout importing providers.tsx, global styles, Mantine CSS
- [X] T030 [US5] Implement `frontend/src/components/QueryInput.tsx` — Mantine TextInput + Button, manages local state (query string, loading boolean, error), calls analyzeQuery on submit, passes result up via onResult callback, shows loading spinner during request, shows error notification on failure
- [X] T031 [P] [US5] Implement `frontend/src/components/FindingCard.tsx` — Mantine Card displaying a single Finding: severity badge (critical=red, warning=yellow, info=blue), description text, collapsible evidence list
- [X] T032 [P] [US5] Implement `frontend/src/components/Timeline.tsx` — Mantine Timeline component rendering TimelineEntry[] chronologically with severity-colored indicators
- [X] T033 [US5] Implement `frontend/src/components/AnalysisResult.tsx` — renders full AgentOutput: summary section, key_findings mapped to FindingCard components, root cause with confidence badge, Timeline component, warnings alert if present, tools_invoked as subtle metadata
- [X] T034 [US5] Implement `frontend/src/app/page.tsx` — main page composing QueryInput and AnalysisResult, manages state (result: AgentOutput | null), passes onResult from QueryInput to AnalysisResult display
- [X] T035 [US5] Configure `frontend/next.config.mjs` — set up API rewrites for development proxy if needed, configure Mantine transpile packages

**Checkpoint**: Full user-facing application operational end-to-end

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, configuration, deployment readiness

- [X] T036 [P] Create `backend/Dockerfile` — Python 3.12-slim base, install deps from pyproject.toml, copy app/, expose port 8000, CMD uvicorn
- [X] T037 [P] Create `frontend/Dockerfile` — Node 20-alpine base, install deps, build Next.js, expose port 3000, CMD next start
- [X] T038 [P] Update `backend/.env.example` with all finalized env vars including comments describing each
- [X] T039 Run `quickstart.md` validation — verify all setup steps work end-to-end from clean clone

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3–7)**: All depend on Foundational phase completion
  - US1 (Phase 3) MUST complete first — it implements the orchestrator that all other stories depend on
  - US2 (Phase 4), US3 (Phase 5), US4 (Phase 6) can proceed in parallel after US1
  - US5 (Phase 7) can proceed in parallel with US2–US4 (only needs the API contract, not specific tools)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational (Phase 2) — implements core orchestrator + first tool
- **US2 (P2)**: Depends on US1 (orchestrator must exist) — adds timeline tool
- **US3 (P3)**: Depends on US1 (orchestrator must exist) — adds chat tool
- **US4 (P4)**: Depends on US1 (orchestrator must exist) — adds KQL tool
- **US5 (P5)**: Depends on Foundational (Phase 2) for types — can parallelize with US2–US4

### Within Each User Story

- Models before tools
- Tools before registration
- Core implementation before integration

### Parallel Opportunities

- T003, T004, T005, T006 can all run in parallel (Setup phase)
- T008, T009, T010 can run in parallel (Foundational — independent model files)
- US2, US3, US4 can run in parallel after US1 completes
- US5 frontend work (T026–T035) can run in parallel with US2–US4 backend work
- T031, T032 (FindingCard, Timeline) can run in parallel
- T036, T037, T038 can run in parallel (Polish phase)

---

## Parallel Example: After US1 Completes

```text
Worker A (Backend):       T020 → T021 (US2 Timeline)
Worker B (Backend):       T022 → T023 (US3 Chat)
Worker C (Backend):       T024 → T025 (US4 KQL)
Worker D (Frontend):      T026 → T027 → T028 → T029 → T030 → T031/T032 → T033 → T034 → T035
```

---

## Implementation Strategy

### MVP Scope
- **Phase 1 + Phase 2 + Phase 3 (US1)** = Minimum viable agent
- User can POST to `/analyze`, agent calls `getSessionData` (querying both App Insights + Cosmos DB), returns structured root cause analysis
- This alone satisfies all 4 constitution success criteria (SC-001 through SC-004)

### Incremental Delivery
1. **MVP**: Setup + Foundation + US1 → working agent with session data tool
2. **+Timeline**: US2 → agent can produce unified chronological views
3. **+Chat**: US3 → agent can analyze candidate/proctor communication
4. **+KQL**: US4 → agent can run advanced diagnostic queries
5. **+Frontend**: US5 → full web UI (can ship earlier if backend API is stable)
6. **+Deploy**: Polish → Dockerfiles, final documentation

### Key Architectural Decisions Embedded in Tasks
- **DI via lifespan** (T015): All SDK clients created once, injected into tools/orchestrator
- **Auto-generated schemas** (T012): BaseTool.schema() derives from Pydantic input_model — no manual schema maintenance
- **Internal Cosmos/AppInsights** (T016, T020, T022): Database/container names are private constants inside each tool module — never in config, I/O, or schemas
- **Hybrid error handling** (T018): Transient errors retried silently; non-transient passed to LLM
- **Structured JSON logging** (T015): Per-module loggers with request_id correlation
