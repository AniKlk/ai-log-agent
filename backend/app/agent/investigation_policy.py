import json
import re
from dataclasses import dataclass, field

from app.agent.domain_knowledge import (
    canonicalize_issue_types,
    canonicalize_services,
    expand_related_services,
    mentions_platform,
    normalize_text,
)
from app.agent.operations_knowledge import build_knowledge_guidance
from app.agent.schema_knowledge import get_all_valid_containers, get_container_info

_CONFIRMATION_CODE_REGEX = re.compile(r"\b\d{16}\b")


@dataclass(slots=True)
class InvestigationProfile:
    original_query: str
    normalized_query: str
    confirmation_codes: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    related_services: list[str] = field(default_factory=list)
    issue_types: list[str] = field(default_factory=list)
    asks_for_blast_radius: bool = False
    mentions_platform: bool = False
    has_time_reference: bool = False

    @property
    def is_session_query(self) -> bool:
        return bool(self.confirmation_codes)

    @property
    def is_aggregate_impact_query(self) -> bool:
        """True for generic queries asking about count / prevalence across candidates."""
        if self.is_session_query:
            return False
        tokens = (
            "how many", "count", "affected", "prevalence", "all candidates",
            "multiple candidates", "how often", "frequency", "how frequent",
            "anyone else", "widespread", "other candidates", "other sessions",
        )
        return any(t in self.normalized_query for t in tokens)

    @property
    def generic_requires_both_workspaces(self) -> bool:
        """Generic service-health queries should always probe both workspaces."""
        return not self.is_session_query and bool(self.services or self.issue_types)

    @property
    def mandatory_tools(self) -> list[str]:
        if not self.is_session_query:
            return []
        return ["getSessionData", "getChatHistory", "getSessionTimeline"]


@dataclass(slots=True)
class ToolObservation:
    tool_name: str
    status: str
    summary: str
    hints: list[str] = field(default_factory=list)


def build_investigation_profile(
    query: str,
    conversation_history: list[dict] | None = None,
) -> InvestigationProfile:
    history_text = " ".join(
        str(message.get("content", ""))
        for message in (conversation_history or [])
        if isinstance(message, dict)
    )
    combined_text = f"{history_text} {query}".strip()
    normalized = normalize_text(combined_text)
    codes = list(dict.fromkeys(_CONFIRMATION_CODE_REGEX.findall(combined_text)))
    services = canonicalize_services(combined_text)
    issues = canonicalize_issue_types(combined_text)
    asks_for_blast_radius = (
        "impact" in issues
        or any(token in normalized for token in ("affected", "impact", "blast radius", "how many"))
    )
    has_time_reference = any(
        token in normalized
        for token in (
            "today",
            "yesterday",
            "last ",
            "between",
            "from ",
            "ago",
            "days",
            "hours",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
            "january",
            "february",
            "march",
        )
    )

    if codes and "login" not in issues and any(
        token in normalized for token in ("relogin", "login", "re-login")
    ):
        issues.append("login")
    if codes and "disconnect" not in issues and "disconnect" in normalized:
        issues.append("disconnect")

    related = expand_related_services(services)
    if codes and "candidate-app" not in services:
        related = list(dict.fromkeys([*related, "candidate-app", "exam-sessions-api"]))

    return InvestigationProfile(
        original_query=query,
        normalized_query=normalize_text(query),
        confirmation_codes=codes,
        services=services,
        related_services=related,
        issue_types=issues,
        asks_for_blast_radius=asks_for_blast_radius,
        mentions_platform=mentions_platform(combined_text),
        has_time_reference=has_time_reference,
    )


def _build_schema_guidance(profile: InvestigationProfile) -> str | None:
    """
    Build container-specific schema guidance based on investigation profile.
    
    Returns schema guidance to inject into system message, or None if no specific guidance applies.
    """
    guidance_lines = []
    
    # Session-specific queries should reference exam-session and session-log
    if profile.is_session_query:
        guidance_lines.append(
            "- For this session query, primary containers: ExamSession/exam-session (status, metadata) "
            "and ExamSession/session-log (lifecycle events)."
        )
        guidance_lines.append(
            "- Query session-log using JOIN on Entries array and filter by SessionLogType codes "
            "(7=Disconnect, 8=Disconnect-NoRelaunch, 11=SessionCompleted, 13=LockdownViolation)."
        )
    
    # Generic queries mentioning tests/completion
    if not profile.is_session_query:
        normalized = profile.normalized_query

        if (
            "teststatus" in normalized
            or "test status" in normalized
            or any(token in normalized for token in ("completed", "not completed", "did not complete", "incomplete", "not finished", "anything but completed"))
        ):
            guidance_lines.append(
                "- Completion intent: use ExamSession/exam-session status fields with effective precedence "
                "TestStatus -> Status. Interpret 'not completed' as any effective status except Completed."
            )

        if any(token in normalized for token in ("disconnect", "disconnection", "reconnect", "lost connection")):
            guidance_lines.append(
                "- Connectivity intent: use ExamSession/session-log Entries.SessionLogType in (7, 8) for disconnect/reconnect lifecycle evidence."
            )

        if any(token in normalized for token in ("browser closed", "app closed", "exit", "exited")):
            guidance_lines.append(
                "- Exit intent: use SessionLogType=9 in session-log and corroborate with candidate-app App Insights exit markers."
            )

        if any(s in profile.services for s in ("chat", "proctor", "communication")) or any(token in normalized for token in ("candidate message", "proctor message", "chat")):
            guidance_lines.append(
                "- Chat intent: query ExamChat/exam-chat by ExamSessionId and use FromUserRole to distinguish Candidate vs Proctor messages."
            )

        if any(s in profile.services for s in ("conference", "video", "twilio")) or any(token in normalized for token in ("room", "waiting room", "conference")):
            guidance_lines.append(
                "- Conference intent: query PPR.Conferences/conference and map RoomStatus values (Active, Completed, Failed)."
            )

        if "assignment" in profile.services or "proctor" in profile.services or any(token in normalized for token in ("assigned", "unassigned", "cancelled", "canceled")):
            guidance_lines.append(
                "- Assignment intent: query Assignment/assignment and map AssignmentStatus values (Assigned, Active, Completed, Cancelled)."
            )

        if "client" in normalized:
            guidance_lines.append(
                "- Client filtering: treat user input as code OR name using Exam.ClientCode OR Exam.ClientName (case-insensitive)."
            )
    
    # Specific container references based on issue types
    if "application-block" in profile.issue_types:
        guidance_lines.append(
            "- ApplicationBlock events: check session-log Entries where SessionLogType=11 "
            "to interpret deny-list enforcement timing and context."
        )
    
    return "\n".join([f"  {line}" for line in guidance_lines]) if guidance_lines else None


def build_investigation_system_message(profile: InvestigationProfile) -> str:
    lines = [
        "Adaptive investigation posture for this request:",
        f"- Normalized intent: {profile.normalized_query or profile.original_query.lower()}",
        f"- Session query: {'yes' if profile.is_session_query else 'no'}",
    ]
    if profile.confirmation_codes:
        lines.append(f"- Confirmation codes: {', '.join(profile.confirmation_codes)}")
    if profile.issue_types:
        lines.append(f"- Likely issue types: {', '.join(profile.issue_types)}")
    if profile.services:
        lines.append(f"- Directly mentioned services: {', '.join(profile.services)}")
    if profile.related_services:
        lines.append(
            "- Adjacent services to inspect if first pass is empty: "
            f"{', '.join(profile.related_services)}"
        )

    knowledge_guidance = build_knowledge_guidance(
        services=[*profile.services, *profile.related_services],
        issue_types=profile.issue_types,
        normalized_query=profile.normalized_query,
    )
    if knowledge_guidance:
        lines.append("- Encoded operational knowledge loaded for this investigation:")
        for hint in knowledge_guidance[:6]:
            lines.append(f"  - {hint}")

    lines.extend(
        [
            "- Investigate in loops: exact match -> widen time window -> inspect adjacent "
            "services -> correlate infrastructure -> quantify candidate impact.",
            "- Do not stop after one empty search. If the first query finds nothing, "
            "widen the date range or inspect a neighboring service/workspace.",
            "- If you confirm a real service or infra problem, estimate blast radius "
            "before finalizing by counting affected sessions/candidates.",
        ]
    )

    # Inject schema-aware container guidance for this investigation
    schema_guidance = _build_schema_guidance(profile)
    if schema_guidance:
        lines.append("\n## Schema-Aware Query Guidance:")
        lines.append(schema_guidance)

    if profile.is_session_query:
        lines.append(
            "- Baseline evidence is mandatory before finalizing: getSessionData, "
            "getChatHistory, getSessionTimeline."
        )
    if (
        not profile.is_session_query
        and "teststatus" in profile.normalized_query
        and "completed" in profile.normalized_query
    ):
        lines.append(
            "- For TestStatus completed lookups, query Cosmos ExamSession/exam-session first "
            "with top-level TestStatus filter and no extra client filter unless user asked for one: "
            "SELECT TOP 500 c.ConfirmationCode, c.Exam.StartTimeLocal, c.Exam.StopTimeLocal, "
            "c.TestStatus FROM c WHERE LOWER(c.TestStatus) = 'completed'."
        )
        lines.append(
            "- If this returns rows, treat it as authoritative and do NOT conclude no sessions found."
        )
    if (
        not profile.is_session_query
        and "completed" in profile.normalized_query
        and any(token in profile.normalized_query for token in ("client", "between", "from", "date"))
    ):
        lines.append(
            "- For completed exams by client and date range, use case-insensitive client matching and "
            "completion-aware date filtering (CompletedDate first, CreatedDate fallback): "
            "SELECT TOP 500 c.ConfirmationCode, c.Status, c.TestStatus, c.Exam.ClientName, c.CreatedDate, c.CompletedDate "
            "FROM c WHERE (LOWER(c.Exam.ClientName) = LOWER('CLIENT_VALUE') OR LOWER(c.Exam.ClientCode) = LOWER('CLIENT_VALUE')) "
            "AND (LOWER(c.Status) = 'completed' OR LOWER(c.TestStatus) = 'completed') "
            "AND ((IS_DEFINED(c.CompletedDate) AND c.CompletedDate >= 'START' AND c.CompletedDate <= 'END') "
            "OR (NOT IS_DEFINED(c.CompletedDate) AND c.CreatedDate >= 'START' AND c.CreatedDate <= 'END'))."
        )
    if (
        not profile.is_session_query
        and any(token in profile.normalized_query for token in ("not completed", "did not complete", "not finish", "incomplete", "anything but completed", "anything but complete"))
        and any(token in profile.normalized_query for token in ("client", "between", "from", "date", "status"))
    ):
        lines.append(
            "- For this request, call getExamStatusCounts first to compute deterministic not-completed "
            "counts and status breakdown from exam-session."
        )
        lines.append(
            "- For not-completed exams by client/date, use undefined-safe status logic instead of "
            "NOT(LOWER(Status)=completed OR LOWER(TestStatus)=completed). "
            "Use: (NOT IS_DEFINED(c.Status) OR LOWER(c.Status) != 'completed') "
            "AND (NOT IS_DEFINED(c.TestStatus) OR LOWER(c.TestStatus) != 'completed')."
        )
        lines.append(
            "- Interpret 'not completed' as any effective status except Completed; when counting by status, "
            "use effective status precedence TestStatus -> Status -> Unknown."
        )

    if any(issue in profile.issue_types for issue in ("application-block", "gw-error", "twilio-benign")):
        lines.append(
            "- Session-log schema guardrail: interpret SessionLogType codes with domain semantics "
            "(7=Disconnect, 8=Reconnect, 9=SecurityViolation, 11=ApplicationBlock, "
            "13=AIThreatAlert, 14=AICheckIn)."
        )
    if "application-block" in profile.issue_types:
        lines.append(
            "- ApplicationBlock (SessionLogType 11) indicates deny-list enforcement in candidate app; "
            "differentiate pre-launch vs launch messages and avoid labeling all blocks as exam failure."
        )
    if "gw-error" in profile.issue_types:
        lines.append(
            "- GingerWebs events with fatality 'not terminate' are informational; escalate only when "
            "fatality/termination indicates terminate or session termination evidence exists."
        )
    if "twilio-benign" in profile.issue_types:
        lines.append(
            "- Twilio 'task.deleted' and 'AcceptTask Error: Could not accept reservation' can be "
            "benign/intermittent; verify assignment/conference state before concluding candidate impact."
        )
    
    # App Insights and Infrastructure specific guidance
    normalized = profile.normalized_query
    if any(token in normalized for token in ("error", "exception", "crash", "failed", "failure", "warn")):
        lines.append(
            "- App Insights intent: use AppExceptions table for exceptions, AppTraces for logs with keywords. "
            "Use _ResourceId (NOT AppRoleName) as primary service identifier."
        )
    if any(token in normalized for token in ("gingerweb", "system check", "readiness", "face", "detect", "gw ")):
        lines.append(
            "- GingerWebs queries: search AppTraces in candidate-app workspace (proproctor) for "
            "'system check', 'readiness', 'face', 'detect', 'GW error' keywords. "
            "Check for 'terminate' vs 'not terminate' to assess impact."
        )
    if any(token in normalized for token in ("lockdown", "process", "blocked app", "unauthorized")):
        lines.append(
            "- Lockdown/process-monitor intent: query candidate-app logs in proproctor workspace "
            "for 'lockdown', 'Ipc', 'blocked', 'deny-list', 'unauthorized application', 'failed to kill process', 'failed to kill app', and 'failed to kill' keywords. "
            "When present, extract and report the app/process name that failed to terminate."
        )
    if any(token in normalized for token in ("pod", "crash", "restart", "container", "kubernetes", "infra", "oom", "memory", "security", "lockdown", "bypass", "unauthorized")):
        lines.append(
            "- Infrastructure intent: use infrastructure workspace (KubeEvents, ContainerLogV2, KubePodInventory). "
            "Search for pod status (CrashLoopBackOff, Pending, Failed) and resource pressure (OOMKilling, MemoryPressure)."
        )
    if any(token in normalized for token in ("performance", "slow", "timeout", "latency", "duration")):
        lines.append(
            "- Performance intent: use AppRequests (TimeTaken field) and AppDependencies (DurationMs field) "
            "from proproctor workspace to correlate slow responses with dependency timeouts."
        )

    if not profile.has_time_reference:
        lines.append(
            "- If a broad service issue is suspected and no explicit date range is "
            "given, prefer a reasonable recent window first, then expand if needed."
        )
    return "\n".join(lines)


def analyze_tool_result(
    tool_name: str,
    tool_args: str,
    result_content: str,
    profile: InvestigationProfile,
) -> ToolObservation | None:
    try:
        args = json.loads(tool_args) if tool_args else {}
    except json.JSONDecodeError:
        args = {}

    try:
        payload = json.loads(result_content)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    if "error" in payload:
        return ToolObservation(
            tool_name=tool_name,
            status="error",
            summary=f"{tool_name} returned an error.",
            hints=[
                "Do not stop here. Adjust the query shape, range, or target service "
                "and continue the investigation.",
            ],
        )

    if tool_name == "getSessionData":
        source_summary = payload.get("source_summary") or {}
        hints: list[str] = []
        app_insights_events = int(source_summary.get("app_insights_events", 0) or 0)
        infra_events = int(source_summary.get("infra_events", 0) or 0)
        if app_insights_events == 0:
            hints.append(
                "No App Insights evidence was returned. Run a wider candidate-app "
                "fallback query in the proproctor workspace before concluding the "
                "signal is absent."
            )
        if infra_events == 0 and any(
            issue in profile.issue_types
            for issue in ("disconnect", "infra", "backend")
        ):
            hints.append(
                "Infra signal is sparse. If symptoms suggest failures or pressure, "
                "inspect infrastructure workspace and related pods before ruling infra out."
            )
        if hints:
            return ToolObservation(
                tool_name=tool_name,
                status="partial",
                summary="Session baseline evidence is incomplete across one or more sources.",
                hints=hints,
            )
        return None

    if tool_name == "queryKQL":
        rows = payload.get("rows") or []
        truncated = bool(payload.get("truncated", False))
        row_count_total = int(payload.get("row_count_total", len(rows)))
        workspace = str(args.get("workspace", "proproctor"))
        timespan_days = args.get("timespan_days", 7)
        hints: list[str] = []
        if truncated:
            hints.append(
                f"KQL results were truncated ({len(rows)} of {row_count_total} rows returned). "
                "Aggregate with summarize count()/dcount() instead of scanning raw rows "
                "to avoid missing events outside the token window."
            )
        if not rows:
            hints.append(
                f"{tool_name} returned no rows in {workspace}. Expand the time range "
                f"beyond {timespan_days} days or pivot to a neighboring service."
            )
            if workspace == "proproctor":
                hints.append(
                    "If app logs stay empty, inspect the infrastructure workspace for "
                    "correlated pod or container issues."
                )
            if profile.related_services:
                hints.append(
                    f"Check adjacent services next: {', '.join(profile.related_services)}."
                )
            return ToolObservation(
                tool_name=tool_name,
                status="empty",
                summary=f"No KQL results came back from the {workspace} workspace.",
                hints=hints,
            )

        if profile.asks_for_blast_radius or any(
            issue in profile.issue_types
            for issue in ("backend", "infra", "disconnect")
        ):
            return ToolObservation(
                tool_name=tool_name,
                status="signal",
                summary=(
                    "KQL returned evidence. If this indicates a real issue, measure "
                    "affected sessions before finalizing."
                ),
                hints=[
                    "Estimate blast radius with getSessionLogStats or a unique-session "
                    "count if the finding appears systemic.",
                ],
            )
        return None

    if tool_name == "queryCosmos":
        rows = payload.get("rows") or []
        container = str(args.get("container", ""))
        if rows:
            if (
                not profile.is_session_query
                and "teststatus" in profile.normalized_query
                and "completed" in profile.normalized_query
                and container == "exam-session"
            ):
                return ToolObservation(
                    tool_name=tool_name,
                    status="signal",
                    summary="Cosmos exam-session returned TestStatus completed rows.",
                    hints=[
                        "Use these rows as primary evidence and avoid adding extra constraints "
                        "that would contradict the returned dataset.",
                    ],
                )
            return None
        # Zero-result aggregate queries need systematic diagnosis
        hints = ["No Cosmos rows were returned. Do not assume absence yet."]
        
        # Detect aggregate count queries (completed exams for client in date range)
        query_text = str(args.get("query", "")).lower()
        is_count_by_client_date = (
            "completed" in profile.normalized_query
            and any(term in profile.normalized_query for term in ["client", "between", "from", "date"])
            and "count(" not in query_text  # Already tried aggregation
        )
        is_not_completed_by_client_date = (
            any(term in profile.normalized_query for term in ["not completed", "did not complete", "not finish", "incomplete", "anything but completed", "anything but complete"])
            and any(term in profile.normalized_query for term in ["client", "between", "from", "date", "status"])
            and "count(" not in query_text
        )
        
        if is_count_by_client_date and container == "exam-session":
            hints.append(
                "Zero results on a client+date+completed query suggests a filter mismatch. "
                "Run this diagnostic sequence to isolate the problem:"
            )
            hints.append(
                "Step 1: SELECT TOP 100 c.ConfirmationCode, c.CreatedDate, c.CompletedDate FROM c "
                "WHERE (IS_DEFINED(c.CompletedDate) OR IS_DEFINED(c.CreatedDate)) ORDER BY c.CreatedDate DESC"
            )
            hints.append(
                "  → Shows if rows have completion/creation timestamps at all"
            )
            hints.append(
                "Step 2: SELECT TOP 100 c.ConfirmationCode, c.Exam.ClientName FROM c "
                "WHERE LOWER(c.Exam.ClientName) = LOWER('CLIENT_VALUE') OR LOWER(c.Exam.ClientCode) = LOWER('CLIENT_VALUE')"
            )
            hints.append(
                "  → Verifies client rows exist by name OR client code"
            )
            hints.append(
                "Step 3: SELECT TOP 100 c.ConfirmationCode, c.TestStatus FROM c WHERE LOWER(c.TestStatus) = 'completed' ORDER BY c.CreatedDate DESC"
            )
            hints.append(
                "  → Shows if completed records exist (ignoring date/client filters)"
            )
            hints.append(
                "Step 4: Combine all filters with CompletedDate-first logic and CreatedDate fallback"
            )
            hints.append(
                "  SELECT TOP 500 c.ConfirmationCode, c.Status, c.TestStatus, c.Exam.ClientName, c.CreatedDate, c.CompletedDate FROM c "
                "WHERE (LOWER(c.Exam.ClientName) = LOWER('CLIENT_VALUE') OR LOWER(c.Exam.ClientCode) = LOWER('CLIENT_VALUE')) "
                "AND (LOWER(c.Status) = 'completed' OR LOWER(c.TestStatus) = 'completed') "
                "AND ((IS_DEFINED(c.CompletedDate) AND c.CompletedDate >= 'START' AND c.CompletedDate <= 'END') "
                "OR (NOT IS_DEFINED(c.CompletedDate) AND c.CreatedDate >= 'START' AND c.CreatedDate <= 'END'))"
            )

        if is_not_completed_by_client_date and container == "exam-session":
            hints.append(
                "Zero results on not-completed queries are often caused by undefined status fields. "
                "Use this undefined-safe filter:" 
            )
            hints.append(
                "SELECT TOP 2000 c.ConfirmationCode, c.Status, c.TestStatus, c.Exam.ClientName, c.CreatedDate FROM c "
                "WHERE (LOWER(c.Exam.ClientName) = LOWER('CLIENT_VALUE') OR LOWER(c.Exam.ClientCode) = LOWER('CLIENT_VALUE')) "
                "AND c.CreatedDate >= 'START' AND c.CreatedDate <= 'END' "
                "AND (NOT IS_DEFINED(c.Status) OR LOWER(c.Status) != 'completed') "
                "AND (NOT IS_DEFINED(c.TestStatus) OR LOWER(c.TestStatus) != 'completed') "
                "ORDER BY c.CreatedDate DESC"
            )
            hints.append(
                "For count-per-status, project Status/TestStatus rows and aggregate in analysis."
            )
            hints.append(
                "When aggregating, use effective status precedence: TestStatus first, then Status, then Unknown."
            )
        
        if (
            not profile.is_session_query
            and "teststatus" in profile.normalized_query
            and "completed" in profile.normalized_query
            and container == "exam-session"
        ):
            hints.append(
                "For completed test lookups, rerun exam-session with top-level TestStatus filter "
                "first and avoid extra client constraints: "
                "SELECT TOP 500 c.ConfirmationCode, c.Exam.StartTimeLocal, "
                "c.Exam.StopTimeLocal, c.TestStatus FROM c WHERE LOWER(c.TestStatus) = 'completed'."
            )
        if container == "session-log":
            hints.append(
                "Try exam-session for the parent record or run a broad schema inspection "
                "query against session-log to confirm field shape."
            )
        elif container == "exam-session":
            hints.append(
                "If exam-session is empty, use it first to obtain ExamSessionId values, "
                "then query session-log or chat/conference containers."
            )
        return ToolObservation(
            tool_name=tool_name,
            status="empty",
            summary=f"No Cosmos rows came back from {container or 'the requested container'}.",
            hints=hints,
        )

    if tool_name == "getSessionLogStats":
        hits = int(payload.get("candidates_with_hits", 0) or 0)
        if hits > 0:
            return None
        return ToolObservation(
            tool_name=tool_name,
            status="empty",
            summary="Blast-radius aggregation returned zero affected sessions.",
            hints=[
                "If that seems inconsistent, broaden keywords to include close synonyms "
                "or widen the date window before concluding there was no candidate impact."
            ],
        )

    return None


def build_adaptive_system_message(
    profile: InvestigationProfile,
    observations: list[ToolObservation],
    tools_invoked: list[str],
) -> str | None:
    if not observations:
        return None

    unique_hints: list[str] = []
    for observation in observations:
        for hint in observation.hints:
            if hint not in unique_hints:
                unique_hints.append(hint)

    lines = ["Adaptive investigation feedback:"]
    for observation in observations[:4]:
        lines.append(f"- {observation.summary}")
    for hint in unique_hints[:6]:
        lines.append(f"- Next best move: {hint}")

    if profile.is_session_query:
        missing = [tool for tool in profile.mandatory_tools if tool not in tools_invoked]
        if missing:
            lines.append(
                "- Do not finalize yet. Missing mandatory baseline tools: "
                f"{', '.join(missing)}."
            )

    if profile.asks_for_blast_radius or profile.is_aggregate_impact_query:
        lines.append(
            "- Before finalizing any confirmed service issue, state whether candidate "
            "impact was measured and cite the count or explain why it remains unverified."
        )
        if "getSessionLogStats" not in tools_invoked:
            lines.append(
                "- This is an aggregate-impact query. Use getSessionLogStats with the "
                "appropriate client_code, date range, and keywords to count affected "
                "candidates accurately. Raw KQL scans often miss paginated Cosmos data."
            )

    if profile.generic_requires_both_workspaces:
        queried_workspaces = {
            str(obs.tool_name).split(":")[-1]
            for obs in observations
            if obs.tool_name.startswith("queryKQL")
        }
        _ = queried_workspaces  # workspace tracking is done in orchestrator; hint is here
        lines.append(
            "- For service-health investigations: query BOTH workspaces (proproctor for "
            "App Insights, infrastructure for Kubernetes events) before concluding."
        )

    return "\n".join(lines)
