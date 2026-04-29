# AI Log Agent

An AI-powered observability tool for querying and analysing Azure logs (Cosmos DB + Log Analytics / App Insights) through a natural-language chat interface.

## What it does

- Ask questions in plain English about candidate sessions, disconnects, error patterns, and timeline events
- Agent orchestrates calls to Cosmos DB and Azure Log Analytics (KQL) automatically
- Results surface as structured findings, a visual timeline, and exportable reports (PDF / Excel)

## Architecture

```
frontend/   Next.js 14 + React 19 + Mantine UI v7
backend/    FastAPI + Azure OpenAI (tool-calling agent)
            ├── Cosmos DB  — session data, session-log entries
            └── Log Analytics — App Insights KQL queries
```

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.12+ |
| Node.js | 20+ |
| Azure CLI (`az`) | any recent |

You also need:
- An **Azure OpenAI** resource with a GPT-4o (or equivalent) deployment
- An **Azure Cosmos DB** account with the ProProctor containers (`ExamSession`, `session-log`, etc.)
- One or more **Log Analytics workspaces** linked to App Insights

## Quickstart

### 1. Clone the repo

```bash
git clone https://github.com/AniKlk/ai-log-agent.git
cd ai-log-agent
```

### 2. Configure environment variables

```bash
cp .env.example backend/.env
```

Open `backend/.env` and fill in all required values (Azure OpenAI endpoint, Cosmos endpoint, workspace IDs). See comments inside the file.

### 3. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is now available at `http://localhost:8000`.  
Swagger docs: `http://localhost:8000/docs`

#### Authentication

The backend uses **`DefaultAzureCredential`** for Azure OpenAI, Log Analytics, and optionally Cosmos DB.  
The simplest way to authenticate locally is:

```bash
az login
```

If you prefer key-based auth for Cosmos DB, set `COSMOS_KEY` in `.env`.

### 4. Frontend

```bash
cd frontend
cp .env.example .env.local   # defaults to http://localhost:8000 — change if backend is elsewhere
npm install
npm run dev
```

Open `http://localhost:3000`.

### 5. Docker (optional)

Run both services together with Docker Compose:

```bash
cp .env.example backend/.env   # fill in values first
docker compose up --build
```

- Frontend: `http://localhost:3000`
- Backend:  `http://localhost:8000`

Or build/run individually:

```bash
# Backend
docker build -t ai-log-agent-backend ./backend
docker run --env-file backend/.env -p 8000:8000 ai-log-agent-backend

# Frontend
docker build -t ai-log-agent-frontend ./frontend
docker run -e NEXT_PUBLIC_API_URL=http://localhost:8000 -p 3000:3000 ai-log-agent-frontend
```

## Project structure

```
backend/
  app/
    agent/        LLM orchestration & prompt
    api/          FastAPI routes
    models/       Request / response schemas
    tools/        Tool implementations (Cosmos, KQL, stats, timeline…)
  pyproject.toml
frontend/
  src/
    app/          Next.js pages & layout
    components/   UI components (Timeline, FindingCard, Sidebar…)
    services/     API client
    types/        Shared TypeScript types
specs/            Architecture & API specification docs
data_contracts.md Cosmos DB & Log Analytics data shape reference
```

## Available tools (agent)

| Tool | Description |
|------|-------------|
| `getSessionData` | Fetch full session record from Cosmos DB |
| `getSessionTimeline` | Build ordered event timeline for a confirmation code |
| `getChatHistory` | Retrieve chat log for a session |
| `queryKQL` | Run arbitrary KQL against Log Analytics / App Insights |
| `queryCosmos` | Run Cosmos DB SQL queries |
| `getSessionLogStats` | Paginated keyword aggregation across all sessions for a client code |

## Environment variables reference

See [.env.example](.env.example) for the full list with descriptions.

## License

Internal — Prometric use only.
