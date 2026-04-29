import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import CosmosClient
from pydantic import BaseModel

from app.tools.base import BaseTool
from app.tools.models import CosmosQueryInput, CosmosQueryOutput

logger = logging.getLogger(__name__)

_TOKEN_ESTIMATE_CHARS = 4

# Allowed database → container mappings
_VALID_CONTAINERS: dict[str, set[str]] = {
    "ExamSession": {"exam-session", "session-log"},
    "ExamChat": {"exam-chat"},
    "PPR.Conferences": {"conference"},
    "Assignment": {"assignment"},
}

# Block write operations
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|UPSERT|REPLACE|CREATE|DROP|ALTER|TRUNCATE)\b",
    re.IGNORECASE,
)

_ORDER_BY_PATTERN = re.compile(r"\s+ORDER\s+BY\s+.+$", re.IGNORECASE | re.DOTALL)


class QueryCosmosTool(BaseTool):
    name = "queryCosmos"
    description = (
        "Execute read-only Cosmos DB SQL queries against ProProctor databases. "
        "Use for time-range searches across session data, disconnections, lifecycle events, "
        "chat messages, conference records, and proctor assignments. "
        "Supports parameterized queries for safe filtering. "
        "Available databases/containers: "
        "ExamSession/exam-session (session records — Status, ExamDisconnectedTimes, Candidate, Exam, RelaunchCount, ConfirmationCode), "
        "ExamSession/session-log (lifecycle events — Entries[] array with Type like SessionStarted, Disconnected, etc.), "
        "ExamChat/exam-chat (chat messages), "
        "PPR.Conferences/conference (video conference data), "
        "Assignment/assignment (proctor assignments)."
    )
    input_model = CosmosQueryInput

    def __init__(self, cosmos_client: CosmosClient) -> None:
        self._cosmos_client = cosmos_client

    @staticmethod
    def _sanitize_correlated_order_by(query: str) -> str:
        return _ORDER_BY_PATTERN.sub("", query).strip()

    async def execute(self, args: BaseModel) -> CosmosQueryOutput:
        assert isinstance(args, CosmosQueryInput)

        # Validate database/container combination
        valid_containers = _VALID_CONTAINERS.get(args.database)
        if valid_containers is None or args.container not in valid_containers:
            raise ValueError(
                f"Invalid database/container: {args.database}/{args.container}. "
                f"Valid: {_VALID_CONTAINERS}"
            )

        # Block write operations
        if _WRITE_PATTERN.search(args.query):
            raise ValueError("Only SELECT queries are allowed. Write operations are blocked.")

        # Detect @param placeholders without parameters
        param_refs = re.findall(r'@\w+', args.query)
        if param_refs and not args.parameters:
            raise ValueError(
                f"Query references parameters {param_refs} but no 'parameters' array was provided. "
                f"Either provide the parameters array or use inline literal values in the query."
            )

        logger.info(
            "queryCosmos called",
            extra={
                "query": args.query,
                "database": args.database,
                "container": args.container,
                "max_items": args.max_items,
            },
        )

        database = self._cosmos_client.get_database_client(args.database)
        container = database.get_container_client(args.container)

        async def run_query(query_text: str) -> list[dict[str, Any]]:
            result_rows: list[dict[str, Any]] = []
            items = container.query_items(
                query=query_text,
                parameters=args.parameters or None,
                max_item_count=args.max_items,
                partition_key=None,
            )
            count = 0
            async for item in items:
                if count >= args.max_items:
                    break
                # Remove Cosmos metadata fields
                item.pop("_rid", None)
                item.pop("_self", None)
                item.pop("_etag", None)
                item.pop("_attachments", None)
                item.pop("_ts", None)
                # Normalize datetime values
                for key, value in item.items():
                    if isinstance(value, datetime):
                        item[key] = value.astimezone(UTC).isoformat()
                result_rows.append(item)
                count += 1
            return result_rows

        rows: list[dict[str, Any]] = []
        try:
            rows = await run_query(args.query)
        except Exception as exc:
            err = str(exc)
            if "Order-by over correlated collections is not supported" in err:
                fallback_query = self._sanitize_correlated_order_by(args.query)
                if fallback_query != args.query:
                    logger.warning(
                        "Retrying Cosmos query without ORDER BY due to correlated collection limitation"
                    )
                    rows = await run_query(fallback_query)
                else:
                    logger.exception("Cosmos query execution failed")
                    raise
            else:
                logger.exception("Cosmos query execution failed")
                raise

        # Apply token cap
        rows, truncated = self._apply_token_cap(rows)

        logger.info("queryCosmos returned %d rows (truncated=%s)", len(rows), truncated)

        return CosmosQueryOutput(rows=rows, truncated=truncated)

    def _apply_token_cap(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        from app.config import Settings

        max_chars = Settings().TOOL_RESPONSE_MAX_TOKENS * _TOKEN_ESTIMATE_CHARS

        serialized = json.dumps(rows, default=str)
        if len(serialized) <= max_chars:
            return rows, False

        truncated_rows: list[dict[str, Any]] = []
        current_chars = 0
        for row in rows:
            row_json = json.dumps(row, default=str)
            if current_chars + len(row_json) > max_chars:
                break
            truncated_rows.append(row)
            current_chars += len(row_json)

        return truncated_rows, True
