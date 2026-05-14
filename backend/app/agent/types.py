from typing import Literal

from pydantic import BaseModel, Field


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


class AppInsightsLogRow(BaseModel):
    timestamp: str | None = None
    type: Literal["info", "warning", "error", "disconnect"]
    message: str


class AppInsightsSummary(BaseModel):
    total_events: int = 0
    info_events: int = 0
    error_events: int = 0
    disconnect_events: int = 0
    marker_events: int = 0
    non_marker_events: int = 0
    error_records: int = 0
    top_error_signatures: list[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
    summary: str
    triage_status: Literal["resolved", "monitoring", "needs_more_data", "escalate"] | None = None
    customer_response: str | None = None
    follow_up_questions: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    escalation_target: str | None = None
    confirmation_codes: list[str] = Field(default_factory=list)
    download_links: dict[str, str] = Field(default_factory=dict)
    per_confirmation_code_summaries: dict[str, str] = Field(default_factory=dict)
    key_findings: list[Finding] = Field(default_factory=list)
    root_cause: str | None = None
    root_cause_confidence: Literal["confirmed", "probable", "uncertain"] | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    app_insights_summary: AppInsightsSummary | None = None
    app_insights_logs: list[AppInsightsLogRow] = Field(default_factory=list)
    source_summary: SourceSummary | None = None
    per_confirmation_code_source_summary: dict[str, SourceSummary] = Field(default_factory=dict)
    tools_invoked: list[str] = Field(default_factory=list)
    warnings: list[str | None] | None = None
