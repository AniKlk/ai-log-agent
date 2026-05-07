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
    top_error_signatures: list[str] = []


class AgentOutput(BaseModel):
    summary: str
    confirmation_codes: list[str] = []
    download_links: dict[str, str] = {}
    per_confirmation_code_summaries: dict[str, str] = {}
    key_findings: list[Finding] = []
    root_cause: str | None = None
    root_cause_confidence: Literal["confirmed", "probable", "uncertain"] | None = None
    timeline: list[TimelineEntry] = []
    app_insights_summary: AppInsightsSummary | None = None
    app_insights_logs: list[AppInsightsLogRow] = []
    source_summary: SourceSummary | None = None
    per_confirmation_code_source_summary: dict[str, SourceSummary] = {}
    tools_invoked: list[str] = []
    warnings: list[str | None] | None = None
