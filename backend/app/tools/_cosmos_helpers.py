import logging
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient

logger = logging.getLogger(__name__)

# Private constants — never exposed in I/O, config, or schemas
_SESSION_DATABASE = "ExamSession"
_SESSION_CONTAINER = "exam-session"


async def resolve_exam_session_id(
    cosmos_client: CosmosClient,
    confirmation_code: str,
) -> tuple[str, dict]:
    """Resolve a ConfirmationCode to an ExamSessionId via the exam-session container.

    Returns (exam_session_id, session_record) or raises ValueError if not found.
    """
    database = cosmos_client.get_database_client(_SESSION_DATABASE)
    container = database.get_container_client(_SESSION_CONTAINER)

    query = (
        "SELECT TOP 1 * FROM c "
        "WHERE c.ConfirmationCode = @code "
        "ORDER BY c.CreatedDate DESC"
    )
    parameters: list[dict] = [{"name": "@code", "value": confirmation_code}]

    items = container.query_items(query=query, parameters=parameters)
    async for item in items:
        exam_session_id = (
            item.get("Id") or item.get("ExamSessionId") or item.get("examSessionId", "")
        )
        if exam_session_id:
            logger.info(
                "Resolved confirmation code to exam session",
                extra={"confirmationCode": confirmation_code},
            )
            return exam_session_id, item

    raise ValueError(f"No session found for confirmation code: {confirmation_code}")


def normalize_timestamp(value: object) -> str:
    """Normalize a timestamp value to ISO 8601 string."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt.astimezone(UTC).isoformat()
        except ValueError:
            return value
    return datetime.now(UTC).isoformat()
