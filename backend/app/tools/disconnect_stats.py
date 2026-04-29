"""
getDisconnectStats — paginated cross-container disconnect aggregation tool.

Fetches ALL sessions for a given client code from ExamSession/exam-session
(paginating through the full result set), then cross-references
ExamSession/session-log Entries for disconnect markers in a given time window.
Returns per-confirmation-code disconnect counts and summary totals.
"""
import logging
from collections import Counter

from azure.cosmos.aio import CosmosClient
from pydantic import BaseModel, Field

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class DisconnectStatsInput(BaseModel):
    client_code: str = Field(
        ...,
        description=(
            "Client code to filter sessions, e.g. 'LSAC'. "
            "Matched against Exam.ClientCode in ExamSession/exam-session."
        ),
    )
    start_date: str = Field(
        ...,
        description="ISO 8601 UTC start of window, e.g. '2026-04-08T00:00:00Z'.",
    )
    end_date: str = Field(
        ...,
        description="ISO 8601 UTC end of window, e.g. '2026-04-11T23:59:59Z'.",
    )
    min_disconnects: int = Field(
        2,
        description="Minimum number of disconnect events to include in results. Default 2.",
    )
    disconnect_keywords: list[str] = Field(
        default=["disconnect"],
        description=(
            "Substrings (case-insensitive) to match in session-log Entries.Metadata "
            "to identify a disconnect event. Default: ['disconnect']."
        ),
    )


class DisconnectStatsOutput(BaseModel):
    total_client_sessions: int
    total_disconnect_rows_in_window: int
    candidates_with_multiple_disconnects: int
    results: list[dict]  # [{confirmation_code, session_id, disconnect_count}]
    truncated: bool = False


class GetDisconnectStatsTool(BaseTool):
    name = "getDisconnectStats"
    description = (
        "Count candidates who experienced multiple disconnects within a date window for a given client. "
        "Step 1: fetch ALL ExamSession records where Exam.ClientCode = <client_code> (paginated, no item limit). "
        "Step 2: query ExamSession/session-log for disconnect metadata entries within the date window "
        "and cross-reference against the client's session IDs. "
        "Step 3: return candidates with >= min_disconnects events, sorted by disconnect count descending. "
        "Use this tool instead of queryCosmos for aggregate/batch disconnect analysis across a client."
    )
    input_model = DisconnectStatsInput

    def __init__(self, cosmos_client: CosmosClient) -> None:
        self._cosmos = cosmos_client

    async def execute(self, args: BaseModel) -> DisconnectStatsOutput:
        assert isinstance(args, DisconnectStatsInput)

        db = self._cosmos.get_database_client("ExamSession")
        exam_container = db.get_container_client("exam-session")
        slog_container = db.get_container_client("session-log")

        # ------------------------------------------------------------------
        # Step 1: paginate ALL sessions for the client (no max_item_count cap)
        # ------------------------------------------------------------------
        client_code_safe = args.client_code.replace("'", "''")
        q1 = (
            f"SELECT c.id, c.Id, c.ConfirmationCode "
            f"FROM c "
            f"WHERE IS_DEFINED(c.Exam.ClientCode) "
            f"AND c.Exam.ClientCode = '{client_code_safe}'"
        )

        session_map: dict[str, str | None] = {}  # session_id -> confirmation_code
        async for row in exam_container.query_items(query=q1):
            sid = row.get("Id") or row.get("id")
            if sid:
                session_map[sid] = row.get("ConfirmationCode")

        logger.info("getDisconnectStats: found %d sessions for client %s", len(session_map), args.client_code)

        if not session_map:
            return DisconnectStatsOutput(
                total_client_sessions=0,
                total_disconnect_rows_in_window=0,
                candidates_with_multiple_disconnects=0,
                results=[],
            )

        # ------------------------------------------------------------------
        # Step 2: query session-log disconnect entries in the time window
        # Build OR condition for keyword matching
        # ------------------------------------------------------------------
        start_safe = args.start_date.replace("'", "''")
        end_safe = args.end_date.replace("'", "''")

        keyword_conditions = " OR ".join(
            f"CONTAINS(LOWER(e.Metadata), '{kw.lower().replace(chr(39), chr(39)+chr(39))}')"
            for kw in args.disconnect_keywords
        )

        q2 = (
            "SELECT c.ExamSessionId AS ExamSessionId "
            "FROM c JOIN e IN c.Entries "
            "WHERE IS_DEFINED(c.ExamSessionId) "
            "AND IS_DEFINED(e.Metadata) "
            f"AND ({keyword_conditions}) "
            "AND IS_DEFINED(e.Timestamp) "
            f"AND e.Timestamp >= '{start_safe}' "
            f"AND e.Timestamp <= '{end_safe}'"
        )

        counts: Counter[str] = Counter()
        total_disconnect_rows = 0

        async for row in slog_container.query_items(query=q2):
            total_disconnect_rows += 1
            sid = row.get("ExamSessionId")
            if sid and sid in session_map:
                counts[sid] += 1

        logger.info(
            "getDisconnectStats: %d disconnect rows in window, %d matched client sessions",
            total_disconnect_rows,
            sum(counts.values()),
        )

        # ------------------------------------------------------------------
        # Step 3: filter and sort
        # ------------------------------------------------------------------
        results = [
            {
                "confirmation_code": session_map.get(sid) or "",
                "session_id": sid,
                "disconnect_count": cnt,
            }
            for sid, cnt in counts.items()
            if cnt >= args.min_disconnects
        ]
        results.sort(key=lambda r: (-r["disconnect_count"], r["confirmation_code"]))

        return DisconnectStatsOutput(
            total_client_sessions=len(session_map),
            total_disconnect_rows_in_window=total_disconnect_rows,
            candidates_with_multiple_disconnects=len(results),
            results=results,
        )
