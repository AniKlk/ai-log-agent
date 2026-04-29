from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# --- Sub-models ---


class LogEvent(BaseModel):
    timestamp: str
    message: str
    type: Literal["info", "error", "disconnect"]
    source: Literal["app-insights", "session-log", "infra"]


class LogError(BaseModel):
    timestamp: str
    error: str


class ChatMessage(BaseModel):
    timestamp: str
    sender: Literal["candidate", "proctor"]
    message: str


class TimelineEvent(BaseModel):
    timestamp: str
    event: str
    source: Literal["system", "chat", "session-log", "infra"]


class ConferenceData(BaseModel):
    conferenceId: str | None = None
    conferenceUri: str | None = None
    events: list[dict[str, str]] = []


class AssignmentData(BaseModel):
    proctorId: str | None = None
    assignedAt: str | None = None
    status: str | None = None
    workers: list[dict[str, str]] = []


class CandidateData(BaseModel):
    candidateId: str | None = None
    firstName: str | None = None
    lastName: str | None = None


class ExamData(BaseModel):
    examName: str | None = None
    examId: str | None = None
    clientName: str | None = None
    deliveryMode: str | None = None


class SystemCheckData(BaseModel):
    status: str | None = None
    events: list[dict[str, str]] = []


class SourceSummary(BaseModel):
    app_insights_events: int = 0
    infra_events: int = 0
    cosmos_session_records: int = 0
    cosmos_session_log_records: int = 0
    cosmos_conference_records: int = 0
    cosmos_assignment_records: int = 0


class SessionMetadata(BaseModel):
    sessionId: str
    examSessionId: str
    status: str
    confirmationCode: str | None = None
    workstationId: str | None = None
    site: str | None = None
    relaunchCount: int | None = None
    locked: bool | None = None
    disconnectedTimes: list[dict[str, str]] = []
    candidate: CandidateData | None = None
    exam: ExamData | None = None
    systemCheck: SystemCheckData | None = None


# --- Tool Inputs ---


class ConfirmationCodeInput(BaseModel):
    confirmationCode: str | None = Field(
        default=None,
        description="Single session confirmation code",
    )
    confirmationCodes: list[str] | None = Field(
        default=None,
        description="One or more session confirmation codes",
    )

    @model_validator(mode="after")
    def validate_confirmation_codes(self) -> "ConfirmationCodeInput":
        single = (self.confirmationCode or "").strip()
        multi = [code.strip() for code in (self.confirmationCodes or []) if code and code.strip()]

        codes: list[str] = []
        if single:
            codes.append(single)
        codes.extend(multi)

        # Keep user-provided order but deduplicate.
        deduped = list(dict.fromkeys(codes))
        if not deduped:
            raise ValueError("Provide confirmationCode or confirmationCodes")

        self.confirmationCode = deduped[0]
        self.confirmationCodes = deduped
        return self

    def get_confirmation_codes(self) -> list[str]:
        return list(self.confirmationCodes or [])


class SessionDataInput(ConfirmationCodeInput):
    pass


class ChatHistoryInput(ConfirmationCodeInput):
    pass


class TimelineInput(ConfirmationCodeInput):
    pass


class KqlInput(BaseModel):
    query: str = Field(..., description="KQL query string to execute")
    workspace: Literal["proproctor", "infrastructure"] = Field(
        "proproctor",
        description="Target workspace: proproctor (application logs) or infrastructure",
    )
    timespan_days: int = Field(
        7,
        description="Number of days to query. Default 7. Use larger values (e.g. 30) for broader searches or when the user specifies a date range.",
    )


# --- Tool Outputs ---


class SessionDataOutput(BaseModel):
    events: list[LogEvent] = []
    errors: list[LogError] = []
    metadata: SessionMetadata | None = None
    conference: ConferenceData | None = None
    assignment: AssignmentData | None = None
    truncated: bool = False
    source_summary: SourceSummary = Field(default_factory=SourceSummary)
    per_confirmation_code_source_summary: dict[str, SourceSummary] = Field(default_factory=dict)


class ChatHistoryOutput(BaseModel):
    messages: list[ChatMessage] = []
    truncated: bool = False


class TimelineOutput(BaseModel):
    timeline: list[TimelineEvent] = []
    truncated: bool = False


class KqlOutput(BaseModel):
    rows: list[dict[str, Any]] = []
    truncated: bool = False


class CosmosQueryInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Cosmos DB SQL query. Must be a SELECT query (read-only). "
            "Use parameterized queries with @param syntax when filtering by user input."
        ),
    )
    database: Literal[
        "ExamSession", "ExamChat", "PPR.Conferences", "Assignment"
    ] = Field(
        ...,
        description=(
            "Target Cosmos DB database. "
            "ExamSession: exam-session (session records with Status, ExamDisconnectedTimes, Candidate, Exam, SystemCheck) "
            "and session-log (lifecycle events like SessionStarted, SessionCompleted, Disconnected) containers. "
            "ExamChat: exam-chat (candidate/proctor chat messages). "
            "PPR.Conferences: conference (video conference records). "
            "Assignment: assignment (proctor assignment records)."
        ),
    )
    container: Literal[
        "exam-session", "session-log", "exam-chat", "conference", "assignment"
    ] = Field(
        ...,
        description=(
            "Target container within the database. "
            "Must match the database: ExamSession → exam-session or session-log, "
            "ExamChat → exam-chat, PPR.Conferences → conference, Assignment → assignment."
        ),
    )
    parameters: list[dict[str, str]] = Field(
        default=[],
        description='Query parameters as [{"name": "@param", "value": "val"}]. Use for user-provided values.',
    )
    max_items: int = Field(
        100,
        description="Maximum number of items to return. Default 100.",
    )


class CosmosQueryOutput(BaseModel):
    rows: list[dict[str, Any]] = []
    truncated: bool = False
