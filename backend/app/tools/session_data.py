import json
import logging
from datetime import datetime, timedelta, timezone
import math

from azure.cosmos.aio import CosmosClient
from azure.monitor.query import LogsQueryStatus
from azure.monitor.query.aio import LogsQueryClient
from pydantic import BaseModel

from app.tools._cosmos_helpers import normalize_timestamp, resolve_exam_session_id
from app.tools.base import BaseTool
from app.tools.models import (
    AssignmentData,
    CandidateData,
    ConferenceData,
    ExamData,
    LogError,
    LogEvent,
    SessionDataInput,
    SessionDataOutput,
    SessionMetadata,
    SourceSummary,
    SystemCheckData,
)

logger = logging.getLogger(__name__)

# Private constants — never exposed in I/O, config, or schemas
_SESSION_LOG_DATABASE = "ExamSession"
_SESSION_LOG_CONTAINER = "session-log"
_CONFERENCE_DATABASE = "PPR.Conferences"
_CONFERENCE_CONTAINER = "conference"
_ASSIGNMENT_DATABASE = "Assignment"
_ASSIGNMENT_CONTAINER = "assignment"
_DEFAULT_TIMESPAN_DAYS = 30
_MAX_TIMESPAN_DAYS = 730
_TOKEN_ESTIMATE_CHARS = 4
_LIFECYCLE_MISMATCH_MINUTES = 5
_LIFECYCLE_MATCH_TOLERANCE_MINUTES = 2


class GetSessionDataTool(BaseTool):
    name = "getSessionData"
    description = (
        "Fetch all session-related system data for one or more confirmation codes. "
        "Retrieves system logs from App Insights, infrastructure logs (KubeEvents, "
        "container logs, pod inventory) from the infra workspace, session metadata, "
        "session lifecycle events, conference data, and proctor assignment data. "
        "Identifies lifecycle events: session start, session end, disconnections, "
        "failures, proctor assignments, conference joins/leaves, pod restarts, "
        "container errors."
    )
    input_model = SessionDataInput

    def __init__(
        self,
        logs_client: LogsQueryClient,
        proproctor_workspace_id: str,
        infra_workspace_id: str,
        cosmos_client: CosmosClient,
    ) -> None:
        self._logs_client = logs_client
        self._proproctor_workspace_id = proproctor_workspace_id
        self._infra_workspace_id = infra_workspace_id
        self._cosmos_client = cosmos_client

    async def execute(self, args: BaseModel) -> SessionDataOutput:
        assert isinstance(args, SessionDataInput)
        confirmation_codes = args.get_confirmation_codes()

        logger.info(
            "getSessionData called",
            extra={"confirmationCodes": confirmation_codes},
        )

        is_multi = len(confirmation_codes) > 1

        all_events: list[LogEvent] = []
        all_errors: list[LogError] = []
        metadata: SessionMetadata | None = None
        conference: ConferenceData | None = None
        assignment: AssignmentData | None = None
        source_summary = SourceSummary()
        per_confirmation_code_source_summary: dict[str, SourceSummary] = {}

        for confirmation_code in confirmation_codes:
            result = await self._execute_single(confirmation_code)

            session_events = result.events
            session_errors = result.errors
            if is_multi:
                for event in session_events:
                    event.message = f"[{confirmation_code}] {event.message}"
                for error in session_errors:
                    error.error = f"[{confirmation_code}] {error.error}"

            all_events.extend(session_events)
            all_errors.extend(session_errors)

            per_confirmation_code_source_summary[confirmation_code] = result.source_summary

            source_summary.app_insights_events += result.source_summary.app_insights_events
            source_summary.infra_events += result.source_summary.infra_events
            source_summary.cosmos_session_records += result.source_summary.cosmos_session_records
            source_summary.cosmos_session_log_records += result.source_summary.cosmos_session_log_records
            source_summary.cosmos_conference_records += result.source_summary.cosmos_conference_records
            source_summary.cosmos_assignment_records += result.source_summary.cosmos_assignment_records

            # Keep detailed metadata only for single-session responses.
            if not is_multi:
                metadata = result.metadata
                conference = result.conference
                assignment = result.assignment

        all_events.sort(key=lambda event: event.timestamp)
        all_errors.sort(key=lambda error: error.timestamp)

        all_events, all_errors, truncated = self._apply_token_cap(all_events, all_errors)

        return SessionDataOutput(
            events=all_events,
            errors=all_errors,
            metadata=metadata,
            conference=conference,
            assignment=assignment,
            truncated=truncated,
            source_summary=source_summary,
            per_confirmation_code_source_summary=per_confirmation_code_source_summary,
        )

    async def _execute_single(self, confirmation_code: str) -> SessionDataOutput:
        # Step 1: Resolve ConfirmationCode → ExamSessionId
        try:
            exam_session_id, session_record = await resolve_exam_session_id(
                self._cosmos_client, confirmation_code
            )
        except ValueError:
            logger.warning("No session found for %s", confirmation_code)
            return SessionDataOutput()

        metadata = self._build_metadata(exam_session_id, session_record)

        # Extract embedded conference data from the session record
        embedded_conference = self._extract_embedded_conference(session_record)

        # Step 2: Query supplementary data sources
        ai_events, ai_errors, ai_count = await self._query_app_insights(
            confirmation_code,
            exam_session_id,
            session_record,
        )
        role_probe_events, role_probe_count = await self._query_candidate_role_probe(
            confirmation_code,
            session_record,
        )
        if role_probe_events:
            ai_events.extend(role_probe_events)

        # De-duplicate merged App Insights events from different probe paths.
        deduped_ai_events: list[LogEvent] = []
        seen_event_keys: set[tuple[str, str, str, str]] = set()
        for event in ai_events:
            key = (event.timestamp, event.message, event.type, event.source)
            if key in seen_event_keys:
                continue
            seen_event_keys.add(key)
            deduped_ai_events.append(event)
        ai_events = deduped_ai_events

        # Emit explicit lifecycle rollups so downstream analysis cannot miss
        # App Insights-only exit/login markers.
        ai_exit_timestamps = sorted(
            {
                event.timestamp
                for event in ai_events
                if event.source == "app-insights"
                and (
                    "candidate-app exit marker" in event.message.lower()
                    or "exiting" in event.message.lower()
                    or "exit lockdown window" in event.message.lower()
                    or "ipc server action received: exit" in event.message.lower()
                )
            }
        )
        if ai_exit_timestamps:
            preview = ", ".join(ai_exit_timestamps[:20])
            ai_events.append(
                LogEvent(
                    timestamp=ai_exit_timestamps[0],
                    message=(
                        "app-insights exit marker rollup: "
                        f"count={len(ai_exit_timestamps)} timestamps=[{preview}]"
                    ),
                    type="disconnect",
                    source="app-insights",
                )
            )

        ai_login_timestamps = sorted(
            {
                event.timestamp
                for event in ai_events
                if event.source == "app-insights"
                and (
                    "candidate-app login marker" in event.message.lower()
                    or "set confirmation code" in event.message.lower()
                    or "logged into application" in event.message.lower()
                )
            }
        )
        if ai_login_timestamps:
            preview = ", ".join(ai_login_timestamps[:20])
            ai_events.append(
                LogEvent(
                    timestamp=ai_login_timestamps[0],
                    message=(
                        "app-insights login marker rollup: "
                        f"count={len(ai_login_timestamps)} timestamps=[{preview}]"
                    ),
                    type="info",
                    source="app-insights",
                )
            )

        infra_events, infra_errors, infra_count = await self._query_infra_logs(
            exam_session_id, session_record
        )
        sl_events, sl_count = await self._query_session_log(exam_session_id)
        conference_extra, conf_count = await self._query_conference(exam_session_id)
        assignment, assign_count = await self._query_assignments(exam_session_id)

        # Merge embedded conference with container conference data
        conference = embedded_conference
        if conference_extra and conference_extra.events:
            if conference is None:
                conference = conference_extra
            else:
                conference.events.extend(conference_extra.events)

        # Step 3: Merge events and sort chronologically
        all_events = sorted(ai_events + infra_events + sl_events, key=lambda e: e.timestamp)
        all_errors = ai_errors + infra_errors

        self._append_lifecycle_correlation_flags(
            confirmation_code,
            ai_events,
            sl_events,
            all_events,
            all_errors,
        )
        all_events.sort(key=lambda event: event.timestamp)
        all_errors.sort(key=lambda error: error.timestamp)

        return SessionDataOutput(
            events=all_events,
            errors=all_errors,
            metadata=metadata,
            conference=conference,
            assignment=assignment,
            truncated=False,
            source_summary=SourceSummary(
                app_insights_events=ai_count + role_probe_count,
                infra_events=infra_count,
                cosmos_session_records=1,
                cosmos_session_log_records=sl_count,
                cosmos_conference_records=conf_count,
                cosmos_assignment_records=assign_count,
            ),
        )

    async def _query_candidate_role_probe(
        self, confirmation_code: str, session_record: dict
    ) -> tuple[list[LogEvent], int]:
        """Direct candidate-app AppTraces probe using AppRoleName filter and high max rows."""
        session_start = session_record.get("CreatedDate") or session_record.get("CreatedAt")
        session_end = session_record.get("CompletedDate") or session_record.get("UpdatedDate")
        probe_time_filter = "| where TimeGenerated >= ago(365d)"
        if session_start:
            ts = normalize_timestamp(str(session_start))
            probe_time_filter = f"| where TimeGenerated >= datetime('{ts}') - 7d"
            if session_end:
                ts_end = normalize_timestamp(str(session_end))
                probe_time_filter += f" and TimeGenerated <= datetime('{ts_end}') + 7d"

        probe_kql = (
            "let cc = '{code}'; "
            "AppTraces "
            "{probe_time_filter} "
            "| where * has cc "
            "| extend role=tostring(column_ifexists('AppRoleName', '')) "
            "| where role contains 'web' or role == 'null' or isempty(role) "
            "| extend msg=tostring(column_ifexists('Message', '')) "
            "| where msg has 'exit' or msg has 'exiting' or msg has 'quit app' or msg has 'close app' or msg has 'set confirmation code' or msg has 'confirmation code set' or msg has 'logged into application' "
            "| project timestamp=TimeGenerated, message=iff(msg has 'set confirmation code' or msg has 'confirmation code set' or msg has 'logged into application' or msg has 'login', strcat('candidate-app login marker (direct-role-probe): ', msg), strcat('candidate-app exit marker (direct-role-probe): ', msg)), type=iff(msg has 'exit' or msg has 'exiting' or msg has 'quit app' or msg has 'close app', 'disconnect', 'info') "
            "| order by timestamp asc "
            "| take 2000"
        ).format(
            code=confirmation_code.replace("'", "''"),
            probe_time_filter=probe_time_filter,
        )

        events: list[LogEvent] = []
        count = 0
        try:
            response = await self._logs_client.query_workspace(
                workspace_id=self._proproctor_workspace_id,
                query=probe_kql,
                timespan=self._compute_session_timespan(session_record),
            )
            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    count += 1
                    record = dict(zip(columns, row))
                    events.append(
                        LogEvent(
                            timestamp=normalize_timestamp(record.get("timestamp")),
                            message=record.get("message", ""),
                            type=record.get("type", "info"),
                            source="app-insights",
                        )
                    )
        except Exception:
            logger.exception("Candidate role probe query failed for %s", confirmation_code)

        return events, count

    @staticmethod
    def _parse_iso_ts(value: str) -> datetime | None:
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    @classmethod
    def _compute_session_timespan(cls, session_record: dict) -> timedelta:
        session_start = session_record.get("CreatedDate") or session_record.get("CreatedAt")
        if not session_start:
            return timedelta(days=_DEFAULT_TIMESPAN_DAYS)

        start_dt = cls._parse_iso_ts(normalize_timestamp(str(session_start)))
        if start_dt is None:
            return timedelta(days=_DEFAULT_TIMESPAN_DAYS)

        now_utc = datetime.now(timezone.utc)
        delta_days = max(1, math.ceil((now_utc - start_dt).total_seconds() / 86400.0) + 14)
        bounded_days = min(_MAX_TIMESPAN_DAYS, max(_DEFAULT_TIMESPAN_DAYS, delta_days))
        return timedelta(days=bounded_days)

    @classmethod
    def _find_marker_timestamps(
        cls,
        events: list[LogEvent],
        marker_type: str,
    ) -> list[tuple[str, datetime]]:
        marker_rows: list[tuple[str, datetime]] = []
        for event in events:
            msg = (event.message or "").lower()
            is_login = (
                "set confirmation code" in msg
                or "confirmation code set" in msg
                or "logged into application" in msg
                or " login" in msg
                or msg.startswith("login")
            )
            is_exit = (
                "exiting" in msg
                or "exit app" in msg
                or "exiting app" in msg
                or "exiting application" in msg
                or "quit app" in msg
                or "close app" in msg
            )
            if marker_type == "login" and not is_login:
                continue
            if marker_type == "exit" and not is_exit:
                continue

            parsed = cls._parse_iso_ts(event.timestamp)
            if parsed:
                marker_rows.append((event.timestamp, parsed))
        return marker_rows

    @classmethod
    def _append_lifecycle_correlation_flags(
        cls,
        confirmation_code: str,
        app_insights_events: list[LogEvent],
        session_log_events: list[LogEvent],
        all_events: list[LogEvent],
        all_errors: list[LogError],
    ) -> None:
        for marker_type in ("login", "exit"):
            ai_markers = cls._find_marker_timestamps(app_insights_events, marker_type)
            sl_markers = cls._find_marker_timestamps(session_log_events, marker_type)

            if ai_markers and not sl_markers:
                for ai_ts, _ in ai_markers:
                    all_events.append(
                        LogEvent(
                            timestamp=ai_ts,
                            message=(
                                f"lifecycle correlation warning ({confirmation_code}): "
                                f"app-insights-only {marker_type} marker at {ai_ts}; "
                                "missing in cosmos session-log"
                            ),
                            type="error",
                            source="app-insights",
                        )
                    )
                    all_errors.append(
                        LogError(
                            timestamp=ai_ts,
                            error=(
                                f"missing session-log lifecycle marker: {marker_type} "
                                f"(app-insights timestamp {ai_ts})"
                            ),
                        )
                    )
                continue

            if sl_markers and not ai_markers:
                for sl_ts, _ in sl_markers:
                    all_events.append(
                        LogEvent(
                            timestamp=sl_ts,
                            message=(
                                f"lifecycle correlation warning ({confirmation_code}): "
                                f"cosmos-only {marker_type} marker at {sl_ts}; "
                                "missing in app-insights"
                            ),
                            type="error",
                            source="session-log",
                        )
                    )
                    all_errors.append(
                        LogError(
                            timestamp=sl_ts,
                            error=(
                                f"missing app-insights lifecycle marker: {marker_type} "
                                f"(session-log timestamp {sl_ts})"
                            ),
                        )
                    )
                continue

            if not ai_markers or not sl_markers:
                continue

            # Evaluate every App Insights marker against nearest Cosmos marker.
            for ai_ts, ai_dt in ai_markers:
                nearest_sl: tuple[str, datetime] | None = None
                nearest_gap_seconds: float | None = None
                for sl_ts, sl_dt in sl_markers:
                    gap_seconds = abs((ai_dt - sl_dt).total_seconds())
                    if nearest_gap_seconds is None or gap_seconds < nearest_gap_seconds:
                        nearest_gap_seconds = gap_seconds
                        nearest_sl = (sl_ts, sl_dt)

                if nearest_sl is None or nearest_gap_seconds is None:
                    continue

                gap_minutes = nearest_gap_seconds / 60.0
                if gap_minutes <= _LIFECYCLE_MATCH_TOLERANCE_MINUTES:
                    continue

                if gap_minutes >= _LIFECYCLE_MISMATCH_MINUTES:
                    message = (
                        f"lifecycle correlation red flag ({confirmation_code}): "
                        f"app-insights {marker_type} marker at {ai_ts} has no close cosmos match; "
                        f"nearest cosmos marker at {nearest_sl[0]} (gap={gap_minutes:.1f} minutes)"
                    )
                    all_events.append(
                        LogEvent(
                            timestamp=ai_ts,
                            message=message,
                            type="error",
                            source="app-insights",
                        )
                    )
                    all_errors.append(LogError(timestamp=ai_ts, error=message))

            # Evaluate every Cosmos marker against nearest App Insights marker.
            for sl_ts, sl_dt in sl_markers:
                nearest_ai: tuple[str, datetime] | None = None
                nearest_gap_seconds: float | None = None
                for ai_ts, ai_dt in ai_markers:
                    gap_seconds = abs((sl_dt - ai_dt).total_seconds())
                    if nearest_gap_seconds is None or gap_seconds < nearest_gap_seconds:
                        nearest_gap_seconds = gap_seconds
                        nearest_ai = (ai_ts, ai_dt)

                if nearest_ai is None or nearest_gap_seconds is None:
                    continue

                gap_minutes = nearest_gap_seconds / 60.0
                if gap_minutes <= _LIFECYCLE_MATCH_TOLERANCE_MINUTES:
                    continue

                if gap_minutes >= _LIFECYCLE_MISMATCH_MINUTES:
                    message = (
                        f"lifecycle correlation warning ({confirmation_code}): "
                        f"cosmos {marker_type} marker at {sl_ts} has no close app-insights match; "
                        f"nearest app-insights marker at {nearest_ai[0]} (gap={gap_minutes:.1f} minutes)"
                    )
                    all_events.append(
                        LogEvent(
                            timestamp=sl_ts,
                            message=message,
                            type="error",
                            source="session-log",
                        )
                    )
                    all_errors.append(LogError(timestamp=sl_ts, error=message))

    @staticmethod
    def _build_metadata(exam_session_id: str, rec: dict) -> SessionMetadata:
        candidate_raw = rec.get("Candidate") or {}
        candidate = CandidateData(
            candidateId=candidate_raw.get("Id") or candidate_raw.get("CandidateId"),
            firstName=candidate_raw.get("FirstName"),
            lastName=candidate_raw.get("LastName"),
        ) if candidate_raw else None

        exam_raw = rec.get("Exam") or {}
        exam = ExamData(
            examName=exam_raw.get("ExamName") or exam_raw.get("Name"),
            examId=exam_raw.get("ExamId") or exam_raw.get("Id"),
            clientName=exam_raw.get("ClientName"),
            deliveryMode=exam_raw.get("DeliveryMode"),
        ) if exam_raw else None

        sc_raw = rec.get("SystemCheck") or {}
        system_check = SystemCheckData(
            status=sc_raw.get("Status"),
        ) if sc_raw else None

        disconnect_times = []
        for dt in (rec.get("ExamDisconnectedTimes") or []):
            entry: dict[str, str] = {}
            if isinstance(dt, dict):
                for k, v in dt.items():
                    entry[k] = str(v) if v else ""
            else:
                entry["timestamp"] = str(dt)
            disconnect_times.append(entry)

        return SessionMetadata(
            sessionId=rec.get("Id", ""),
            examSessionId=exam_session_id,
            status=rec.get("Status", "unknown"),
            confirmationCode=rec.get("ConfirmationCode"),
            workstationId=rec.get("WorkstationId"),
            site=str(rec.get("Site")) if rec.get("Site") else None,
            relaunchCount=rec.get("RelaunchCount"),
            locked=rec.get("Locked"),
            disconnectedTimes=disconnect_times,
            candidate=candidate,
            exam=exam,
            systemCheck=system_check,
        )

    @staticmethod
    def _extract_embedded_conference(rec: dict) -> ConferenceData | None:
        conf = rec.get("Conference")
        if not conf:
            return None
        return ConferenceData(
            conferenceId=conf.get("Id") or conf.get("ConferenceId"),
            conferenceUri=conf.get("Uri") or conf.get("ConferenceUri"),
        )

    async def _query_app_insights(
        self, confirmation_code: str, exam_session_id: str, session_record: dict
    ) -> tuple[list[LogEvent], list[LogError], int]:
        session_start = session_record.get("CreatedDate") or session_record.get("CreatedAt")
        session_end = session_record.get("CompletedDate") or session_record.get("UpdatedDate")

        time_filter = ""
        candidate_time_filter = "| where TimeGenerated >= ago(365d)"
        if session_start:
            ts = normalize_timestamp(str(session_start))
            time_filter = f"| where TimeGenerated >= datetime('{ts}') - 1h"
            candidate_time_filter = f"| where TimeGenerated >= datetime('{ts}') - 7d"
            if session_end:
                ts_end = normalize_timestamp(str(session_end))
                time_filter += f" and TimeGenerated <= datetime('{ts_end}') + 1h"
                candidate_time_filter += f" and TimeGenerated <= datetime('{ts_end}') + 7d"

        kql = (
            "let cc = '{code}'; "
            "let esid = '{esid}'; "
            "let events = AppEvents "
            "{time_filter} "
            "| where Properties.ExamSessionId == esid "
            "  or Properties.examSessionId == esid "
            "  or tostring(Properties.ConfirmationCode) == cc "
            "  or tostring(Properties.confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
            "  or tostring(Properties) has cc "
            "| extend evt_message = coalesce(tostring(Properties.message), tostring(Name)) "
            "| project timestamp=TimeGenerated, name=Name, message=evt_message, type=iff(evt_message has 'warning' or evt_message has 'warn' or evt_message has 'error' or evt_message has 'fail' or evt_message has 'exception' or Name has 'warning' or Name has 'error' or Name has 'fail', 'error', 'info'), errorDetail=iff(evt_message has 'warning' or evt_message has 'warn' or evt_message has 'error' or evt_message has 'fail' or evt_message has 'exception' or Name has 'warning' or Name has 'error' or Name has 'fail', evt_message, ''); "
            "let traces = AppTraces "
            "{time_filter} "
            "| where Properties.ExamSessionId == esid "
            "  or Properties.examSessionId == esid "
            "  or tostring(Properties.ConfirmationCode) == cc "
            "  or tostring(Properties.confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
            "  or tostring(Properties) has cc "
            "| project timestamp=TimeGenerated, name=OperationName, message=Message, type=iff(toint(column_ifexists('SeverityLevel', int(0))) >= 2 or Message has 'warning' or Message has 'warn' or Message has 'error' or Message has 'fail' or Message has 'exception', 'error', 'info'), errorDetail=iff(toint(column_ifexists('SeverityLevel', int(0))) >= 2 or Message has 'warning' or Message has 'warn' or Message has 'error' or Message has 'fail' or Message has 'exception', Message, ''); "
            "let errors = AppExceptions "
            "{time_filter} "
            "| where Properties.ExamSessionId == esid "
            "  or Properties.examSessionId == esid "
            "  or tostring(Properties.ConfirmationCode) == cc "
            "  or tostring(Properties.confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}})).confirmationCode) == cc "
            "  or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
            "  or tostring(Properties) has cc "
            "| project timestamp=TimeGenerated, name=ProblemId, message=OuterMessage, type='error', errorDetail=InnermostMessage; "
            "let backend_timeout_traces = AppTraces "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Message has 'timeout' or Message has 'timed out' or Message has 'cosmos' "
            "| project timestamp=TimeGenerated, name=OperationName, message=Message, type='error', errorDetail='backend-timeout-trace'; "
            "let request_failures = AppRequests "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or ResultCode in ('408', '429', '500', '502', '503', '504') "
            "| project timestamp=TimeGenerated, name=Name, message=strcat('request failure result=', ResultCode, ' durationMs=', tostring(DurationMs), ' ', tostring(Url)), type='error', errorDetail='request-failure'; "
            "let dependency_failures = AppDependencies "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or Name has 'cosmos' or Target has 'cosmos' "
            "| where Name has 'timeout' or Target has 'timeout' or tostring(Data) has 'timeout' or Success == false "
            "| project timestamp=TimeGenerated, name=Name, message=strcat('dependency failure target=', tostring(Target), ' result=', tostring(ResultCode), ' durationMs=', tostring(DurationMs)), type='error', errorDetail=tostring(Data); "
            "let req_total = toscalar(AppRequests "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| summarize count()); "
            "let req_failed = toscalar(AppRequests "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or ResultCode in ('408', '429', '500', '502', '503', '504') "
            "| summarize count()); "
            "let dep_total = toscalar(AppDependencies "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| summarize count()); "
            "let dep_failed = toscalar(AppDependencies "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or Name has 'timeout' or Target has 'timeout' or tostring(Data) has 'timeout' "
            "| summarize count()); "
            "let timeout_trace_count = toscalar(AppTraces "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Message has 'timeout' or Message has 'timed out' "
            "| summarize count()); "
            "let backend_check_summary = print timestamp=now(), name='backend-check-summary', message=strcat('backend checks executed for app-proproctor-exam-sessions-api: requests_total=', tostring(req_total), ', request_failures=', tostring(req_failed), ', dependencies_total=', tostring(dep_total), ', dependency_failures_or_timeouts=', tostring(dep_failed), ', timeout_traces=', tostring(timeout_trace_count)), type='info', errorDetail=''; "
            "let candidate_lifecycle_traces = AppTraces "
            "{candidate_time_filter} "
            "| where _ResourceId contains 'app-proproctor-candidate-app' "
            "   or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) "
            "   or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode)) "
            "| where ( "
            "    ((Properties.ExamSessionId == esid "
            "      or Properties.examSessionId == esid "
            "      or tostring(Properties.ConfirmationCode) == cc "
            "      or tostring(Properties.confirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}})).confirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
            "      or Message has cc) "
            "     and (Message has 'set confirmation code' "
            "       or Message has 'confirmation code set' "
            "       or Message has 'logged into application' "
            "       or Message has 'login' "
            "       or Message has 'start' "
            "       or Message has 'launch')) "
            "    or Message has 'exit' "
            "    or Message has 'exiting app' "
            "    or Message has 'exiting application' "
            "    or Message has 'quit app' "
            "    or Message has 'close app' "
            "  ) "
            "| project timestamp=TimeGenerated, name=OperationName, message=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker: ', Message), iff(Message has 'exit' or Message has 'exiting app' or Message has 'exiting application' or Message has 'quit app' or Message has 'close app', strcat('candidate-app exit marker: ', Message), strcat('candidate-app lifecycle: ', Message))), type=iff(Message has 'exit' or Message has 'exiting app' or Message has 'exiting application' or Message has 'quit app' or Message has 'close app', 'disconnect', 'info'), errorDetail=''; "
            "let candidate_lifecycle_events = AppEvents "
            "{candidate_time_filter} "
            "| where _ResourceId contains 'app-proproctor-candidate-app' "
            "   or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) "
            "   or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode)) "
            "| where ( "
            "    ((Properties.ExamSessionId == esid "
            "      or Properties.examSessionId == esid "
            "      or tostring(Properties.ConfirmationCode) == cc "
            "      or tostring(Properties.confirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}})).ConfirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}})).confirmationCode) == cc "
            "      or tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc "
            "      or tostring(Properties.message) has cc) "
            "     and (Name has 'set confirmation code' "
            "       or Name has 'confirmation code set' "
            "       or Name has 'logged into application' "
            "       or Name has 'login' "
            "       or Name has 'start' "
            "       or Name has 'launch')) "
            "    or Name has 'exit' "
            "    or Name has 'exiting app' "
            "    or Name has 'exiting application' "
            "    or Name has 'quit app' "
            "    or Name has 'close app' "
            "  ) "
            "| project timestamp=TimeGenerated, name=Name, message=iff(Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application' or Name has 'login', strcat('candidate-app login marker: ', Name), iff(Name has 'exit' or Name has 'exiting app' or Name has 'exiting application' or Name has 'quit app' or Name has 'close app', strcat('candidate-app exit marker: ', Name), strcat('candidate-app lifecycle event: ', Name))), type=iff(Name has 'exit' or Name has 'exiting app' or Name has 'exiting application' or Name has 'quit app' or Name has 'close app', 'disconnect', 'info'), errorDetail=''; "
            "let candidate_exit_probe_traces = AppTraces "
            "{candidate_time_filter} "
            "| where (Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app' or Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application') "
            "  and (tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc or tostring(Properties) has cc or Message has cc or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode))) "
            "| project timestamp=TimeGenerated, name=OperationName, message=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker (probe): ', Message), strcat('candidate-app exit marker (probe): ', Message)), type=iff(Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app', 'disconnect', 'info'), errorDetail=''; "
            "let candidate_exit_probe_events = AppEvents "
            "{candidate_time_filter} "
            "| where (Name has 'exit' or Name has 'exiting' or Name has 'quit app' or Name has 'close app' or Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application') "
            "  and (tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc or tostring(Properties) has cc or tostring(Name) has cc or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode))) "
            "| project timestamp=TimeGenerated, name=Name, message=iff(Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application' or Name has 'login', strcat('candidate-app login marker (probe): ', Name), strcat('candidate-app exit marker (probe): ', Name)), type=iff(Name has 'exit' or Name has 'exiting' or Name has 'quit app' or Name has 'close app', 'disconnect', 'info'), errorDetail=''; "
            "let candidate_role_probe_traces = AppTraces "
            "{candidate_time_filter} "
            "| where * has cc "
            "| extend role=tostring(column_ifexists('AppRoleName', '')) "
            "| where role contains 'web' or role == 'null' or isempty(role) "
            "| where Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app' or Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' "
            "| project timestamp=TimeGenerated, name=OperationName, message=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker (role-probe): ', Message), strcat('candidate-app exit marker (role-probe): ', Message)), type=iff(Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app', 'disconnect', 'info'), errorDetail=''; "
            "union events, traces, errors, backend_timeout_traces, request_failures, dependency_failures, backend_check_summary, candidate_lifecycle_traces, candidate_lifecycle_events, candidate_exit_probe_traces, candidate_exit_probe_events, candidate_role_probe_traces "
            "| order by timestamp asc"
        ).format(
            code=confirmation_code.replace("'", "''"),
            esid=exam_session_id.replace("'", "''"),
            time_filter=time_filter,
            candidate_time_filter=candidate_time_filter or time_filter,
        )

        timespan = self._compute_session_timespan(session_record)
        events: list[LogEvent] = []
        errors: list[LogError] = []
        total_count = 0

        try:
            response = await self._logs_client.query_workspace(
                workspace_id=self._proproctor_workspace_id,
                query=kql,
                timespan=timespan,
            )

            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    total_count += 1
                    record = dict(zip(columns, row))
                    ts = normalize_timestamp(record.get("timestamp"))
                    event_type = record.get("type", "info")

                    if event_type == "error":
                        errors.append(
                            LogError(
                                timestamp=ts,
                                error=record.get("errorDetail") or record.get("message", ""),
                            )
                        )
                    else:
                        events.append(
                            LogEvent(
                                timestamp=ts,
                                message=record.get("message") or record.get("name", ""),
                                type=event_type,
                                source="app-insights",
                            )
                        )
        except Exception:
            logger.exception("App Insights query failed for %s", confirmation_code)

        # Fallback for legacy App Insights schema where rows are in Traces table
        # and candidate exit/login rows may not carry confirmation code directly.
        legacy_kql = (
            "let cc = '{code}'; "
            "let anchors = AppTraces "
            "| where TimeGenerated >= ago(365d) "
            "| extend msg = tostring(column_ifexists('Message', '')) "
            "| extend cd = tostring(column_ifexists('customDimensions', dynamic({{}}))) "
            "| extend props = tostring(column_ifexists('Properties', dynamic({{}}))) "
            "| extend sid = coalesce(tostring(column_ifexists('session_Id', '')), tostring(column_ifexists('SessionId', ''))) "
            "| extend opid = coalesce(tostring(column_ifexists('operation_Id', '')), tostring(column_ifexists('OperationId', ''))) "
            "| where cd has cc or props has cc or msg has cc "
            "| summarize by sid, opid; "
            "AppTraces "
            "| where TimeGenerated >= ago(365d) "
            "| extend ts = TimeGenerated "
            "| extend msg = tostring(column_ifexists('Message', '')) "
            "| extend op = coalesce(tostring(column_ifexists('OperationName', '')), tostring(column_ifexists('operation_Name', '')), tostring(column_ifexists('operationName', ''))) "
            "| extend sid = coalesce(tostring(column_ifexists('session_Id', '')), tostring(column_ifexists('SessionId', ''))) "
            "| extend opid = coalesce(tostring(column_ifexists('operation_Id', '')), tostring(column_ifexists('OperationId', ''))) "
            "| extend sev = toint(coalesce(column_ifexists('SeverityLevel', int(0)), column_ifexists('severityLevel', int(0)))) "
            "| where (isnotempty(sid) and sid in (anchors | where isnotempty(sid) | project sid)) "
            "   or (isnotempty(opid) and opid in (anchors | where isnotempty(opid) | project opid)) "
            "| where msg has 'exit' or msg has 'exiting' or msg has 'quit app' or msg has 'close app' or msg has 'set confirmation code' or msg has 'confirmation code set' or msg has 'logged into application' "
            "| project timestamp=ts, name=op, message=iff(msg has 'set confirmation code' or msg has 'confirmation code set' or msg has 'logged into application' or msg has 'login', strcat('candidate-app login marker (legacy): ', msg), strcat('candidate-app exit marker (legacy): ', msg)), type=iff(msg has 'exit' or msg has 'exiting' or msg has 'quit app' or msg has 'close app' or sev >= 2, 'disconnect', 'info'), errorDetail='' "
            "| order by timestamp asc"
        ).format(code=confirmation_code.replace("'", "''"))

        try:
            legacy_response = await self._logs_client.query_workspace(
                workspace_id=self._proproctor_workspace_id,
                query=legacy_kql,
                timespan=timespan,
            )

            if legacy_response.status == LogsQueryStatus.SUCCESS and legacy_response.tables:
                table = legacy_response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    total_count += 1
                    record = dict(zip(columns, row))
                    ts = normalize_timestamp(record.get("timestamp"))
                    events.append(
                        LogEvent(
                            timestamp=ts,
                            message=record.get("message") or record.get("name", ""),
                            type=record.get("type", "info"),
                            source="app-insights",
                        )
                    )
        except Exception:
            logger.exception("Legacy App Insights fallback query failed for %s", confirmation_code)

        return events, errors, total_count

    async def _query_infra_logs(
        self, exam_session_id: str, session_record: dict
    ) -> tuple[list[LogEvent], list[LogError], int]:
        """Query KubeEvents, ContainerLogV2, and KubePodInventory from the infra workspace."""
        # Determine time window from session record
        session_start = session_record.get("CreatedDate") or session_record.get("CreatedAt")
        session_end = session_record.get("CompletedDate") or session_record.get("UpdatedDate")

        # Build time filter — use session window with padding, or fall back to timespan
        time_filter = ""
        if session_start:
            ts = normalize_timestamp(str(session_start))
            time_filter = f"| where TimeGenerated >= datetime('{ts}') - 1h"
            if session_end:
                ts_end = normalize_timestamp(str(session_end))
                time_filter += f" and TimeGenerated <= datetime('{ts_end}') + 1h"

        esid_safe = exam_session_id.replace("'", "''")

        kql = (
            "let esid = '{esid}'; "
            "let related_pods = ContainerLogV2 "
            "{time_filter} "
            "| where LogMessage has esid "
            "| summarize by PodName; "
            "let kube_events = KubeEvents "
            "{time_filter} "
            "| where Name in (related_pods) "
            "| where Reason in ('Failed', 'BackOff', 'Unhealthy', 'FailedScheduling', "
            "'Killing', 'OOMKilling', 'Evicted', 'Warning', 'Error', 'Created', 'Started', 'Pulled') "
            "| project timestamp=TimeGenerated, name=Reason, "
            "  message=strcat(Reason, ': ', Message, ' [', Name, '/', Namespace, ']'), "
            "  type=iff(Reason in ('Failed', 'BackOff', 'Unhealthy', 'OOMKilling', 'Evicted', 'Killing'), 'error', 'info'), "
            "  source='KubeEvents'; "
            "let container_logs = ContainerLogV2 "
            "{time_filter} "
            "| where LogMessage has esid "
            "| project timestamp=TimeGenerated, name=ContainerName, "
            "  message=strcat('[', ContainerName, '/', PodName, '] ', LogMessage), "
            "  type=iff(LogLevel in ('error', 'ERROR', 'Error'), 'error', 'info'), "
            "  source='ContainerLog'; "
            "let pod_inv = KubePodInventory "
            "{time_filter} "
            "| where Name in (related_pods) "
            "| where PodStatus in ('Failed', 'Unknown', 'Pending') "
            "  or ContainerStatusReason in ('OOMKilled', 'CrashLoopBackOff', 'Error', 'ContainerCannotRun', 'ImagePullBackOff') "
            "| project timestamp=TimeGenerated, name=Name, "
            "  message=strcat('Pod ', Name, ' status=', PodStatus, ' reason=', ContainerStatusReason, ' [', Namespace, ']'), "
            "  type='error', "
            "  source='PodInventory'; "
            "union kube_events, container_logs, pod_inv "
            "| order by timestamp asc "
            "| take 200"
        ).format(esid=esid_safe, time_filter=time_filter)

        events: list[LogEvent] = []
        errors: list[LogError] = []
        total_count = 0

        try:
            response = await self._logs_client.query_workspace(
                workspace_id=self._infra_workspace_id,
                query=kql,
                timespan=timedelta(days=_DEFAULT_TIMESPAN_DAYS),
            )

            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    total_count += 1
                    record = dict(zip(columns, row))
                    ts = normalize_timestamp(record.get("timestamp"))
                    event_type = record.get("type", "info")

                    if event_type == "error":
                        errors.append(
                            LogError(timestamp=ts, error=record.get("message", ""))
                        )
                    else:
                        events.append(
                            LogEvent(
                                timestamp=ts,
                                message=record.get("message", ""),
                                type="info",
                                source="infra",
                            )
                        )
        except Exception:
            logger.exception("Infra workspace query failed")

        logger.info("Infra query returned %d events", total_count)
        return events, errors, total_count

    async def _query_session_log(
        self, exam_session_id: str
    ) -> tuple[list[LogEvent], int]:
        events: list[LogEvent] = []
        count = 0

        try:
            database = self._cosmos_client.get_database_client(_SESSION_LOG_DATABASE)
            container = database.get_container_client(_SESSION_LOG_CONTAINER)

            query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
            parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

            items = container.query_items(query=query, parameters=parameters)
            async for item in items:
                # Document has a nested Entries array with session log events
                entries = item.get("Entries") or []
                for entry in entries:
                    count += 1
                    ts = normalize_timestamp(entry.get("Timestamp"))
                    msg = entry.get("Metadata") or entry.get("Message") or ""
                    role = entry.get("Role", "")
                    identity = entry.get("Identity", "")
                    if identity and msg:
                        msg = f"[{role}/{identity}] {msg}"
                    event_type_raw = str(msg).lower()
                    event_type: str = "info"
                    if "lockdown bypass detected" in event_type_raw:
                        event_type = "error"
                        msg = f"CRITICAL RED FLAG: {msg}"
                    elif "error" in event_type_raw or "fail" in event_type_raw or "warning" in event_type_raw or "warn" in event_type_raw:
                        event_type = "error"
                    elif "disconnect" in event_type_raw:
                        event_type = "disconnect"
                    events.append(
                        LogEvent(timestamp=ts, message=msg, type=event_type, source="session-log")
                    )
        except Exception:
            logger.exception("Session log query failed for exam session")

        return events, count

    async def _query_conference(
        self, exam_session_id: str
    ) -> tuple[ConferenceData | None, int]:
        count = 0

        try:
            database = self._cosmos_client.get_database_client(_CONFERENCE_DATABASE)
            container = database.get_container_client(_CONFERENCE_CONTAINER)

            query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
            parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

            items = container.query_items(query=query, parameters=parameters)
            async for item in items:
                count += 1
                conference_id = item.get("Id")
                status = item.get("Status")
                room_sid = item.get("RoomSid")
                channel_sid = item.get("ChannelSid")
                return ConferenceData(
                    conferenceId=conference_id,
                    events=[
                        {"status": str(status or ""), "roomSid": str(room_sid or ""), "channelSid": str(channel_sid or "")},
                    ],
                ), count
        except Exception:
            logger.exception("Conference query failed for exam session")

        return None, 0

    async def _query_assignments(
        self, exam_session_id: str
    ) -> tuple[AssignmentData | None, int]:
        count = 0
        all_workers: list[dict[str, str]] = []
        overall_status: str | None = None

        try:
            database = self._cosmos_client.get_database_client(_ASSIGNMENT_DATABASE)
            container = database.get_container_client(_ASSIGNMENT_CONTAINER)

            query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
            parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

            items = container.query_items(query=query, parameters=parameters)
            async for item in items:
                count += 1
                if overall_status is None:
                    overall_status = item.get("Status")
                workers = item.get("Workers") or []
                for w in workers:
                    all_workers.append({
                        "workerName": w.get("WorkerName", ""),
                        "status": w.get("Status", ""),
                        "updatedAt": w.get("UpdatedDateTime", ""),
                    })
        except Exception:
            logger.exception("Assignment query failed for exam session")

        if count == 0:
            return None, 0

        # Find the last accepted worker as the "assigned proctor"
        accepted = [w for w in all_workers if "accepted" in w.get("status", "")]
        last_accepted = accepted[-1] if accepted else None

        return AssignmentData(
            proctorId=last_accepted["workerName"] if last_accepted else None,
            assignedAt=last_accepted["updatedAt"] if last_accepted else None,
            status=overall_status,
            workers=all_workers,
        ), count

    def _apply_token_cap(
        self, events: list[LogEvent], errors: list[LogError]
    ) -> tuple[list[LogEvent], list[LogError], bool]:
        from app.config import Settings

        max_tokens = Settings().TOOL_RESPONSE_MAX_TOKENS
        max_chars = max_tokens * _TOKEN_ESTIMATE_CHARS

        serialized = json.dumps(
            {"events": [e.model_dump() for e in events], "errors": [e.model_dump() for e in errors]}
        )

        if len(serialized) <= max_chars:
            return events, errors, False

        # Keep errors (higher priority) + truncate events
        error_json = json.dumps([e.model_dump() for e in errors])
        remaining_chars = max_chars - len(error_json) - 100  # buffer for structure

        truncated_events: list[LogEvent] = []
        current_chars = 0
        for event in events:
            event_json = event.model_dump_json()
            if current_chars + len(event_json) > remaining_chars:
                break
            truncated_events.append(event)
            current_chars += len(event_json)

        return truncated_events, errors, True
