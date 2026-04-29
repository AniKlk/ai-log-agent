"""
getSessionLogStats — paginated client-scoped session-log keyword aggregation.

Steps:
  1. Fetch ALL ExamSession records for a given client code (paginated, no cap).
  2. Query ExamSession/session-log Entries.Metadata for rows matching ANY of the
     supplied keywords within a date window.
  3. Return per-confirmation-code hit counts and aggregate totals.

Works for any keyword: errors, disconnects, specific messages, etc.
"""
import logging
from collections import Counter

from azure.cosmos.aio import CosmosClient
from pydantic import BaseModel, Field

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class SessionLogStatsInput(BaseModel):
    client_code: str = Field(
        ...,
        description=(
            "Client code to scope the search, e.g. 'LSAC'. "
            "Matched against Exam.ClientCode in ExamSession/exam-session."
        ),
    )
    start_date: str = Field(
        ...,
        description="ISO 8601 UTC start of the search window, e.g. '2026-04-08T00:00:00Z'.",
    )
    end_date: str = Field(
        ...,
        description="ISO 8601 UTC end of the search window, e.g. '2026-04-11T23:59:59Z'.",
    )
    keywords: list[str] = Field(
        ...,
        description=(
            "One or more substrings to search for in session-log Entries.Metadata "
            "(case-insensitive). All keywords are OR'd together. "
            "Examples: ['unauthorised app', 'unauthorized app'], ['disconnect'], "
            "['system check failed'], ['error']."
        ),
    )
    min_hits: int = Field(
        1,
        description=(
            "Minimum number of matching entries for a session to be included in results. "
            "Use 1 (default) for 'any occurrence', 2+ for 'multiple occurrences'."
        ),
    )
    include_metadata_samples: bool = Field(
        False,
        description=(
            "If true, include up to 3 sample Metadata strings per session in results "
            "so the LLM can describe what the entries actually say."
        ),
    )


class SessionLogStatsOutput(BaseModel):
    total_client_sessions: int
    active_client_sessions_in_window: int
    total_matching_rows_in_window: int
    candidates_with_hits: int
    results: list[dict]   # [{confirmation_code, session_id, hit_count, samples?}]
    truncated: bool = False


class GetSessionLogStatsTool(BaseTool):
    name = "getSessionLogStats"
    description = (
        "Search session-log Entries.Metadata for any keyword or error pattern for a given client "
        "within a date window. Returns how many candidates matched and how often, with optional "
        "sample metadata strings. "
        "Use this for questions like: "
        "'how many LSAC candidates had unauthorised app errors between X and Y?', "
        "'how many candidates saw system check failures?', "
        "'count disconnect events for client ABC in April'. "
        "Paginates through ALL client sessions — never limited to a small item cap. "
        "Parameters: client_code, start_date, end_date, keywords (list), min_hits, include_metadata_samples."
    )
    input_model = SessionLogStatsInput

    def __init__(self, cosmos_client: CosmosClient) -> None:
        self._cosmos = cosmos_client

    async def execute(self, args: BaseModel) -> SessionLogStatsOutput:
        assert isinstance(args, SessionLogStatsInput)

        db = self._cosmos.get_database_client("ExamSession")
        exam_container = db.get_container_client("exam-session")
        slog_container = db.get_container_client("session-log")

        # ------------------------------------------------------------------
        # Step 1: paginate ALL sessions for the client
        # ------------------------------------------------------------------
        client_code_safe = args.client_code.replace("'", "''")
        q1 = (
            f"SELECT c.id, c.Id, c.ConfirmationCode "
            f"FROM c "
            f"WHERE IS_DEFINED(c.Exam.ClientCode) "
            f"AND c.Exam.ClientCode = '{client_code_safe}'"
        )

        session_map: dict[str, str | None] = {}
        async for row in exam_container.query_items(query=q1):
            sid = row.get("Id") or row.get("id")
            if sid:
                session_map[sid] = row.get("ConfirmationCode")

        logger.info(
            "getSessionLogStats: %d sessions for client %s",
            len(session_map), args.client_code,
        )

        if not session_map:
            return SessionLogStatsOutput(
                total_client_sessions=0,
                active_client_sessions_in_window=0,
                total_matching_rows_in_window=0,
                candidates_with_hits=0,
                results=[],
            )

        # ------------------------------------------------------------------
        # Step 2: query session-log for keyword matches in the window
        # ------------------------------------------------------------------
        start_safe = args.start_date.replace("'", "''")
        end_safe = args.end_date.replace("'", "''")

        keyword_conditions = " OR ".join(
            f"CONTAINS(LOWER(e.Metadata), '{kw.lower().replace(chr(39), chr(39)*2)}')"
            for kw in args.keywords
        )

        # Fetch Metadata text when samples are requested; otherwise just the session id
        if args.include_metadata_samples:
            select_fields = "c.ExamSessionId AS ExamSessionId, e.Metadata AS Metadata"
        else:
            select_fields = "c.ExamSessionId AS ExamSessionId"

        q2 = (
            f"SELECT {select_fields} "
            "FROM c JOIN e IN c.Entries "
            "WHERE IS_DEFINED(c.ExamSessionId) "
            "AND IS_DEFINED(e.Metadata) "
            f"AND ({keyword_conditions}) "
            "AND IS_DEFINED(e.Timestamp) "
            f"AND e.Timestamp >= '{start_safe}' "
            f"AND e.Timestamp <= '{end_safe}'"
        )

        counts: Counter[str] = Counter()
        samples: dict[str, list[str]] = {}
        total_rows = 0
        active_sessions_in_window: set[str] = set()

        async for row in slog_container.query_items(query=q2):
            total_rows += 1
            sid = row.get("ExamSessionId")
            if sid and sid in session_map:
                active_sessions_in_window.add(sid)
                counts[sid] += 1
                if args.include_metadata_samples:
                    meta = row.get("Metadata", "")
                    if meta and len(samples.get(sid, [])) < 3:
                        samples.setdefault(sid, []).append(meta)

        logger.info(
            "getSessionLogStats: %d matching rows, %d unique client sessions hit",
            total_rows, len(counts),
        )

        # ------------------------------------------------------------------
        # Step 3: filter by min_hits and sort
        # ------------------------------------------------------------------
        results = []
        for sid, cnt in counts.items():
            if cnt < args.min_hits:
                continue
            entry: dict = {
                "confirmation_code": session_map.get(sid) or "",
                "session_id": sid,
                "hit_count": cnt,
            }
            if args.include_metadata_samples and sid in samples:
                entry["metadata_samples"] = samples[sid]
            results.append(entry)

        results.sort(key=lambda r: (-r["hit_count"], r["confirmation_code"]))

        return SessionLogStatsOutput(
            total_client_sessions=len(session_map),
            active_client_sessions_in_window=len(active_sessions_in_window),
            total_matching_rows_in_window=total_rows,
            candidates_with_hits=len(results),
            results=results,
        )
