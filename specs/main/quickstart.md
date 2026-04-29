# Quickstart: AI Log Agent

## Prerequisites

- Python 3.12+
- Node.js 20+
- Azure subscription with:
  - Azure OpenAI deployment (GPT-4o recommended)
  - Application Insights / Log Analytics workspace
- Azure CLI (`az login` for local authentication)

## 1. Clone & Setup

```bash
git clone <repo-url>
cd ai-log-agent
```

## 2. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your Azure values:
#   AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
#   AZURE_OPENAI_DEPLOYMENT=gpt-4o
#   AZURE_OPENAI_API_VERSION=2024-12-01-preview
#   PROPROCTOR_WORKSPACE_ID=<your-proproctor-workspace-guid>
#   INFRA_WORKSPACE_ID=<your-infra-workspace-guid>
#   COSMOS_ENDPOINT=https://<your-account>.documents.azure.com:443/
#   CORS_ORIGINS=http://localhost:3000
```

## 3. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local:
#   NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 4. Run Locally

**Terminal 1 — Backend**:
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — Frontend**:
```bash
cd frontend
npm run dev
```

Open `http://localhost:3000` in your browser.

## 5. Test It

Enter a confirmation code or question in the UI, e.g.:
- `"What happened with confirmation code ABC123?"`
- `"Show me errors in the last hour"`

## 6. Run Tests

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm test
```

## 7. Verify Constitution Compliance

After any change, verify:

| Principle | How to Verify |
|-----------|--------------|
| I. Agent-First | Agent drives tool calls autonomously; no hardcoded analysis paths |
| II. Tool-Based Access | All Azure queries go through `app/tools/`; no inline API calls |
| III. Deterministic Retrieval | Same confirmation code → same tool results (within data freshness) |
| IV. Structured Responses | Response matches `AgentOutput` Pydantic schema; findings cite evidence |

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| AZURE_OPENAI_ENDPOINT | yes | Azure OpenAI resource endpoint |
| AZURE_OPENAI_DEPLOYMENT | yes | Model deployment name |
| AZURE_OPENAI_API_VERSION | yes | API version string |
| LOG_ANALYTICS_WORKSPACE_ID | yes | Log Analytics workspace GUID |
| CORS_ORIGINS | no | Comma-separated allowed origins (default: `http://localhost:3000`) |
| MAX_AGENT_ITERATIONS | no | Max tool-calling loop iterations (default: 10) |
| TOOL_RESPONSE_MAX_TOKENS | no | Max tokens per tool response (default: 40000) |
