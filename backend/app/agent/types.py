from typing import Literal

from pydantic import BaseModel


class Finding(BaseModel):
    description: str
    severity: Literal["critical", "warning", "info"]
    evidence: list[str]


class TimelineEntry(BaseModel):
    timestamp: str | None = None
    event: str
    severity: Literal["critical", "warning", "info"] | None = None


class SourceSummary(BaseModel):
    app_insights_events: int = 0
    infra_events: int = 0
    cosmos_session_records: int = 0
    cosmos_session_log_records: int = 0
    cosmos_conference_records: int = 0
    cosmos_assignment_records: int = 0


class AgentOutput(BaseModel):
    summary: str
    per_confirmation_code_summaries: dict[str, str] = {}
    key_findings: list[Finding] = []
    root_cause: str | None = None
    root_cause_confidence: Literal["confirmed", "probable", "uncertain"] | None = None
    timeline: list[TimelineEntry] = []
    source_summary: SourceSummary | None = None
    per_confirmation_code_source_summary: dict[str, SourceSummary] = {}
    tools_invoked: list[str] = []
    warnings: list[str | None] | None = None
