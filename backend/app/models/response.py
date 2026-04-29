from pydantic import BaseModel

from app.agent.types import AgentOutput


class AnalyzeResponse(BaseModel):
    answer: AgentOutput
    request_id: str
    duration_ms: int
