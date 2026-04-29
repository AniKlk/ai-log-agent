# Architecture

## Components

### Frontend

* Next.js UI
* Input + response display

### Backend

* FastAPI
* Agent orchestrator

### Agent Layer

* Handles LLM + tool loop

### Tools Layer

* Azure log queries
* KQL execution

### AI Layer

* Azure OpenAI deployment

---

## Flow

User → API → Agent → LLM
→ Tool Call → Azure Logs
→ LLM Analysis → Response

---

## Key Design Decisions

* LLM controls tool usage
* Backend executes tools
* Structured outputs improve reasoning
