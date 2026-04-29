import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.monitor.query import LogsQueryStatus
from azure.monitor.query.aio import LogsQueryClient
from pydantic import BaseModel

from app.tools.base import BaseTool
from app.tools.models import KqlInput, KqlOutput

logger = logging.getLogger(__name__)

_DEFAULT_TIMESPAN_DAYS = 7
_TOKEN_ESTIMATE_CHARS = 4


class QueryKQLTool(BaseTool):
    name = "queryKQL"
    description = (
        "Execute advanced KQL queries against Azure Log Analytics for diagnostics and analysis. "
        "Use for debugging, deep investigations, or custom filtered queries. "
        "Target either application logs (proproctor, default) or infrastructure logs."
    )
    input_model = KqlInput

    def __init__(
        self,
        logs_client: LogsQueryClient,
        proproctor_workspace_id: str,
        infra_workspace_id: str,
    ) -> None:
        self._logs_client = logs_client
        self._workspace_ids = {
            "proproctor": proproctor_workspace_id,
            "infrastructure": infra_workspace_id,
        }

    async def execute(self, args: BaseModel) -> KqlOutput:
        assert isinstance(args, KqlInput)
        kql_query = args.query
        workspace_key = args.workspace
        timespan_days = args.timespan_days

        workspace_id = self._workspace_ids.get(workspace_key, self._workspace_ids["proproctor"])

        logger.info(
            "queryKQL called",
            extra={"query": kql_query, "workspace": workspace_key, "timespan_days": timespan_days},
        )

        rows: list[dict[str, Any]] = []

        try:
            response = await self._logs_client.query_workspace(
                workspace_id=workspace_id,
                query=kql_query,
                timespan=timedelta(days=timespan_days),
            )

            if response.status == LogsQueryStatus.SUCCESS and response.tables:
                table = response.tables[0]
                columns = [c.name if hasattr(c, 'name') else str(c) for c in table.columns]
                for row in table.rows:
                    record = dict(zip(columns, row))
                    # Normalize datetime values
                    for key, value in record.items():
                        if isinstance(value, datetime):
                            record[key] = value.astimezone(UTC).isoformat()
                    rows.append(record)
        except Exception:
            logger.exception("KQL query execution failed")
            raise

        # Apply token cap
        rows, truncated = self._apply_token_cap(rows)

        logger.info("queryKQL returned %d rows (truncated=%s)", len(rows), truncated)

        return KqlOutput(rows=rows, truncated=truncated)

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
