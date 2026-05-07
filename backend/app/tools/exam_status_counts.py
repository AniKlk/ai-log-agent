import logging
from collections import Counter
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient
from pydantic import BaseModel, Field

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ExamStatusCountsInput(BaseModel):
    client_value: str = Field(
        ...,
        description=(
            "Client identifier to match against Exam.ClientCode OR Exam.ClientName "
            "(case-insensitive), e.g. 'LSAC'."
        ),
    )
    start_date: str = Field(
        ...,
        description="UTC start date/time (ISO 8601). Date-only values like 2026-04-01 are accepted.",
    )
    end_date: str = Field(
        ...,
        description="UTC end date/time (ISO 8601). Date-only values like 2026-05-06 are accepted.",
    )
    include_confirmation_codes: bool = Field(
        True,
        description="Whether to include example confirmation codes per status bucket.",
    )
    max_confirmation_codes_per_status: int = Field(
        25,
        ge=1,
        le=200,
        description="Maximum example confirmation codes returned per status.",
    )


class ExamStatusCountsOutput(BaseModel):
    client_value: str
    start_date: str
    end_date: str
    total_client_sessions: int
    sessions_in_window: int
    counts_by_status: dict[str, int]
    not_completed_total: int
    not_completed_counts_by_status: dict[str, int]
    confirmation_codes_by_status: dict[str, list[str]] = Field(default_factory=dict)
    not_completed_confirmation_codes_by_status: dict[str, list[str]] = Field(default_factory=dict)
    truncated: bool = False


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    if len(text) == 10:
        text = f"{text}T00:00:00Z"

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt


def _normalize_status(row: dict) -> str:
    test_status = str(row.get("TestStatus") or "").strip()
    if test_status:
        return test_status

    status = str(row.get("Status") or "").strip()
    if status:
        return status

    return "Unknown"


def _dates_from_row(row: dict) -> list[datetime]:
    exam_obj = row.get("Exam") or {}
    date_candidates = [
        row.get("CompletedDate"),
        row.get("CreatedDate"),
        exam_obj.get("StartTimeLocal"),
        exam_obj.get("StopTimeLocal"),
    ]

    parsed: list[datetime] = []
    for candidate in date_candidates:
        dt = _parse_iso(candidate)
        if dt is not None:
            parsed.append(dt)
    return parsed


class GetExamStatusCountsTool(BaseTool):
    name = "getExamStatusCounts"
    description = (
        "Return deterministic exam status counts for a client across a date range from "
        "ExamSession/exam-session. Matches client by ClientCode OR ClientName (case-insensitive), "
        "computes effective status as TestStatus -> Status -> Unknown, and returns both overall "
        "counts and not-completed counts (anything except Completed)."
    )
    input_model = ExamStatusCountsInput

    def __init__(self, cosmos_client: CosmosClient) -> None:
        self._cosmos = cosmos_client

    async def execute(self, args: BaseModel) -> ExamStatusCountsOutput:
        assert isinstance(args, ExamStatusCountsInput)

        start_dt = _parse_iso(args.start_date)
        end_dt = _parse_iso(args.end_date)
        if start_dt is None or end_dt is None:
            raise ValueError("start_date and end_date must be valid ISO 8601 values")
        if end_dt < start_dt:
            raise ValueError("end_date must be greater than or equal to start_date")

        db = self._cosmos.get_database_client("ExamSession")
        exam_container = db.get_container_client("exam-session")

        client_safe = args.client_value.replace("'", "''").lower()
        query = (
            "SELECT c.ConfirmationCode, c.Status, c.TestStatus, c.CreatedDate, c.CompletedDate, c.Exam "
            "FROM c "
            "WHERE (IS_DEFINED(c.Exam.ClientName) AND LOWER(c.Exam.ClientName) = '"
            + client_safe
            + "') "
            "OR (IS_DEFINED(c.Exam.ClientCode) AND LOWER(c.Exam.ClientCode) = '"
            + client_safe
            + "')"
        )

        total_client_sessions = 0
        sessions_in_window = 0
        counts_by_status: Counter[str] = Counter()
        not_completed_counts_by_status: Counter[str] = Counter()
        confirmation_codes_by_status: dict[str, list[str]] = {}
        not_completed_codes_by_status: dict[str, list[str]] = {}

        async for row in exam_container.query_items(query=query):
            total_client_sessions += 1

            row_dates = _dates_from_row(row)
            if not row_dates:
                continue

            if not any(start_dt <= dt <= end_dt for dt in row_dates):
                continue

            sessions_in_window += 1
            effective_status = _normalize_status(row)
            effective_status_key = effective_status if effective_status else "Unknown"
            counts_by_status[effective_status_key] += 1

            code = str(row.get("ConfirmationCode") or "")
            if args.include_confirmation_codes:
                codes_bucket = confirmation_codes_by_status.setdefault(effective_status_key, [])
                if code and len(codes_bucket) < args.max_confirmation_codes_per_status:
                    codes_bucket.append(code)

            if effective_status_key.lower() != "completed":
                not_completed_counts_by_status[effective_status_key] += 1
                if args.include_confirmation_codes:
                    nc_bucket = not_completed_codes_by_status.setdefault(effective_status_key, [])
                    if code and len(nc_bucket) < args.max_confirmation_codes_per_status:
                        nc_bucket.append(code)

        not_completed_total = sum(not_completed_counts_by_status.values())

        logger.info(
            "getExamStatusCounts: client=%s total_client_sessions=%d sessions_in_window=%d not_completed_total=%d",
            args.client_value,
            total_client_sessions,
            sessions_in_window,
            not_completed_total,
        )

        return ExamStatusCountsOutput(
            client_value=args.client_value,
            start_date=args.start_date,
            end_date=args.end_date,
            total_client_sessions=total_client_sessions,
            sessions_in_window=sessions_in_window,
            counts_by_status=dict(counts_by_status),
            not_completed_total=not_completed_total,
            not_completed_counts_by_status=dict(not_completed_counts_by_status),
            confirmation_codes_by_status=confirmation_codes_by_status if args.include_confirmation_codes else {},
            not_completed_confirmation_codes_by_status=(
                not_completed_codes_by_status if args.include_confirmation_codes else {}
            ),
            truncated=False,
        )
