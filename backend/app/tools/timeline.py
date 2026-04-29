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
from app.tools.models import TimelineEvent, TimelineInput, TimelineOutput

logger = logging.getLogger(__name__)

# Private constants — never exposed in I/O, config, or schemas
_COSMOS_CHAT_DATABASE = "ExamChat"
_COSMOS_CHAT_CONTAINER = "exam-chat"
_SESSION_LOG_DATABASE = "ExamSession"
_SESSION_LOG_CONTAINER = "session-log"
_DEFAULT_TIMESPAN_DAYS = 30
_MAX_TIMESPAN_DAYS = 730
_TOKEN_ESTIMATE_CHARS = 4


class GetSessionTimelineTool(BaseTool):
    name = "getSessionTimeline"
    description = (
        "Return a unified chronological timeline combining system events, infrastructure "
        "events (KubeEvents, container logs, pod inventory), session lifecycle events, "
        "and chat messages for one or more confirmation codes. Merges App Insights logs, "
        "infra workspace logs, session logs, and chat messages into a single sorted "
        "timeline with source attribution."
    )
    input_model = TimelineInput

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

    async def execute(self, args: BaseModel) -> TimelineOutput:
        assert isinstance(args, TimelineInput)
        confirmation_codes = args.get_confirmation_codes()

        logger.info(
            "getSessionTimeline called",
            extra={"confirmationCodes": confirmation_codes},
        )

        timeline: list[TimelineEvent] = []
        is_multi = len(confirmation_codes) > 1

        for confirmation_code in confirmation_codes:
            # Resolve ConfirmationCode → ExamSessionId
            try:
                exam_session_id, session_record = await resolve_exam_session_id(
                    self._cosmos_client, confirmation_code
                )
            except ValueError:
                logger.warning("No session found for %s", confirmation_code)
                continue

            system_events = await self._query_system_events(
                confirmation_code,
                exam_session_id,
                session_record,
            )
            infra_events = await self._query_infra_events(exam_session_id, session_record)
            session_log_events = await self._query_session_log(exam_session_id)
            chat_events = await self._query_chat_events(exam_session_id)

            current_timeline = system_events + infra_events + session_log_events + chat_events
            if is_multi:
                for event in current_timeline:
                    event.event = f"[{confirmation_code}] {event.event}"

            timeline.extend(current_timeline)

        # Merge and sort chronologically
        timeline.sort(key=lambda event: event.timestamp)

        # Apply token cap
        timeline, truncated = self._apply_token_cap(timeline)

        return TimelineOutput(timeline=timeline, truncated=truncated)

    async def _query_system_events(
        self, confirmation_code: str, exam_session_id: str, session_record: dict
    ) -> list[TimelineEvent]:
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
            "| project timestamp=TimeGenerated, event=Name; "
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
            "| project timestamp=TimeGenerated, event=coalesce(OperationName, Message); "
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
            "| project timestamp=TimeGenerated, event=coalesce(ProblemId, OuterMessage); "
            "let backend_timeout_traces = AppTraces "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Message has 'timeout' or Message has 'timed out' or Message has 'cosmos' "
            "| project timestamp=TimeGenerated, event=strcat('backend timeout trace: ', Message); "
            "let request_failures = AppRequests "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or ResultCode in ('408', '429', '500', '502', '503', '504') "
            "| project timestamp=TimeGenerated, event=strcat('request failure ', Name, ' result=', ResultCode, ' durationMs=', tostring(DurationMs)); "
            "let dependency_failures = AppDependencies "
            "{time_filter} "
            "| where _ResourceId contains 'app-proproctor-exam-sessions-api' "
            "| where Success == false or Name has 'cosmos' or Target has 'cosmos' "
            "| where Name has 'timeout' or Target has 'timeout' or tostring(Data) has 'timeout' or Success == false "
            "| project timestamp=TimeGenerated, event=strcat('dependency failure ', Name, ' target=', tostring(Target), ' result=', tostring(ResultCode)); "
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
            "| project timestamp=TimeGenerated, event=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker: ', Message), iff(Message has 'exit' or Message has 'exiting app' or Message has 'exiting application' or Message has 'quit app' or Message has 'close app', strcat('candidate-app exit marker: ', Message), strcat('candidate-app lifecycle: ', Message))); "
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
            "| project timestamp=TimeGenerated, event=iff(Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application' or Name has 'login', strcat('candidate-app login marker: ', Name), iff(Name has 'exit' or Name has 'exiting app' or Name has 'exiting application' or Name has 'quit app' or Name has 'close app', strcat('candidate-app exit marker: ', Name), strcat('candidate-app lifecycle event: ', Name))); "
            "let candidate_exit_probe_traces = AppTraces "
            "{candidate_time_filter} "
            "| where (Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app' or Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application') "
            "  and (tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc or tostring(Properties) has cc or Message has cc or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode))) "
            "| project timestamp=TimeGenerated, event=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker (probe): ', Message), strcat('candidate-app exit marker (probe): ', Message)); "
            "let candidate_exit_probe_events = AppEvents "
            "{candidate_time_filter} "
            "| where (Name has 'exit' or Name has 'exiting' or Name has 'quit app' or Name has 'close app' or Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application') "
            "  and (tostring(column_ifexists('customDimensions', dynamic({{}}))) has cc or tostring(Properties) has cc or tostring(Name) has cc or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).AppMode)) or isnotempty(tostring(column_ifexists('customDimensions', dynamic({{}})).appMode))) "
            "| project timestamp=TimeGenerated, event=iff(Name has 'set confirmation code' or Name has 'confirmation code set' or Name has 'logged into application' or Name has 'login', strcat('candidate-app login marker (probe): ', Name), strcat('candidate-app exit marker (probe): ', Name)); "
            "let candidate_role_probe_traces = AppTraces "
            "{candidate_time_filter} "
            "| where * has cc "
            "| extend role=tostring(column_ifexists('AppRoleName', '')) "
            "| where role contains 'web' or role == 'null' or isempty(role) "
            "| where Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app' or Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' "
            "| project timestamp=TimeGenerated, event=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker (role-probe): ', Message), strcat('candidate-app exit marker (role-probe): ', Message)); "
            "let candidate_role_probe_direct = AppTraces "
            "| where TimeGenerated >= ago(30d) "
            "| where * has cc "
            "| extend role=tostring(column_ifexists('AppRoleName', '')) "
            "| where role contains 'web' or role == 'null' or isempty(role) "
            "| where Message has 'exit' or Message has 'exiting' or Message has 'quit app' or Message has 'close app' or Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' "
            "| project timestamp=TimeGenerated, event=iff(Message has 'set confirmation code' or Message has 'confirmation code set' or Message has 'logged into application' or Message has 'login', strcat('candidate-app login marker (direct-role-probe): ', Message), strcat('candidate-app exit marker (direct-role-probe): ', Message)) "
            "| take 2000; "
            "union events, traces, errors, backend_timeout_traces, request_failures, dependency_failures, candidate_lifecycle_traces, candidate_lifecycle_events, candidate_exit_probe_traces, candidate_exit_probe_events, candidate_role_probe_traces, candidate_role_probe_direct "
            "| order by timestamp asc"
        ).format(
            code=confirmation_code.replace("'", "''"),
            esid=exam_session_id.replace("'", "''"),
            time_filter=time_filter,
            candidate_time_filter=candidate_time_filter or time_filter,
        )

        events: list[TimelineEvent] = []
        try:
            response = await self._logs_client.query_workspace(
                workspace_id=self._proproctor_workspace_id,
                query=kql,
                timespan=self._compute_session_timespan(session_record),
            )
            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    record = dict(zip(columns, row))
                    ts = normalize_timestamp(record.get("timestamp"))
                    events.append(
                        TimelineEvent(
                            timestamp=ts,
                            event=record.get("event", ""),
                            source="system",
                        )
                    )
        except Exception:
            logger.exception("App Insights timeline query failed for %s", confirmation_code)

        return events

    async def _query_infra_events(self, exam_session_id: str, session_record: dict) -> list[TimelineEvent]:
        """Query KubeEvents, ContainerLogV2, KubePodInventory from infra workspace."""
        session_start = session_record.get("CreatedDate") or session_record.get("CreatedAt")
        session_end = session_record.get("CompletedDate") or session_record.get("UpdatedDate")

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
            "| project timestamp=TimeGenerated, "
            "  event=strcat(Reason, ': ', Message, ' [', Name, '/', Namespace, ']'); "
            "let container_logs = ContainerLogV2 "
            "{time_filter} "
            "| where LogMessage has esid "
            "| project timestamp=TimeGenerated, "
            "  event=strcat('[', ContainerName, '/', PodName, '] ', LogMessage); "
            "let pod_inv = KubePodInventory "
            "{time_filter} "
            "| where Name in (related_pods) "
            "| where PodStatus in ('Failed', 'Unknown', 'Pending') "
            "  or ContainerStatusReason in ('OOMKilled', 'CrashLoopBackOff', 'Error', 'ContainerCannotRun', 'ImagePullBackOff') "
            "| project timestamp=TimeGenerated, "
            "  event=strcat('Pod ', Name, ' status=', PodStatus, ' reason=', ContainerStatusReason, ' [', Namespace, ']'); "
            "union kube_events, container_logs, pod_inv "
            "| order by timestamp asc "
            "| take 200"
        ).format(esid=esid_safe, time_filter=time_filter)

        events: list[TimelineEvent] = []
        try:
            response = await self._logs_client.query_workspace(
                workspace_id=self._infra_workspace_id,
                query=kql,
                timespan=self._compute_session_timespan(session_record),
            )
            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    record = dict(zip(columns, row))
                    ts = normalize_timestamp(record.get("timestamp"))
                    events.append(
                        TimelineEvent(
                            timestamp=ts,
                            event=record.get("event", ""),
                            source="infra",
                        )
                    )
        except Exception:
            logger.exception("Infra workspace timeline query failed")

        return events

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

    async def _query_session_log(self, exam_session_id: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        try:
            database = self._cosmos_client.get_database_client(_SESSION_LOG_DATABASE)
            container = database.get_container_client(_SESSION_LOG_CONTAINER)

            query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
            parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

            items = container.query_items(query=query, parameters=parameters)
            async for item in items:
                # Document has nested Entries array
                for entry in (item.get("Entries") or []):
                    ts = normalize_timestamp(entry.get("Timestamp"))
                    metadata = entry.get("Metadata") or ""
                    role = entry.get("Role", "")
                    identity = entry.get("Identity", "")
                    event_desc = f"[{role}/{identity}] {metadata}" if identity else metadata
                    events.append(
                        TimelineEvent(
                            timestamp=ts,
                            event=event_desc,
                            source="session-log",
                        )
                    )
        except Exception:
            logger.exception("Session log timeline query failed")

        return events

    async def _query_chat_events(self, exam_session_id: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        try:
            database = self._cosmos_client.get_database_client(_COSMOS_CHAT_DATABASE)
            container = database.get_container_client(_COSMOS_CHAT_CONTAINER)

            query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
            parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

            items = container.query_items(query=query, parameters=parameters)
            async for item in items:
                # Document has nested Entries array
                for entry in (item.get("Entries") or []):
                    ts = normalize_timestamp(entry.get("TimeStamp") or entry.get("Timestamp"))
                    role_val = entry.get("Role", 0)
                    sender = "proctor" if role_val == 1 else "candidate"
                    message = entry.get("Message", "")
                    if not message:
                        continue
                    events.append(
                        TimelineEvent(
                            timestamp=ts,
                            event=f"[{sender}] {message}",
                            source="chat",
                        )
                    )
        except Exception:
            logger.exception("Cosmos DB chat timeline query failed")

        return events

    def _apply_token_cap(
        self, timeline: list[TimelineEvent]
    ) -> tuple[list[TimelineEvent], bool]:
        from app.config import Settings

        max_chars = Settings().TOOL_RESPONSE_MAX_TOKENS * _TOKEN_ESTIMATE_CHARS

        serialized = json.dumps([e.model_dump() for e in timeline])
        if len(serialized) <= max_chars:
            return timeline, False

        truncated_timeline: list[TimelineEvent] = []
        current_chars = 0
        for event in timeline:
            event_json = event.model_dump_json()
            if current_chars + len(event_json) > max_chars:
                break
            truncated_timeline.append(event)
            current_chars += len(event_json)

        return truncated_timeline, True
