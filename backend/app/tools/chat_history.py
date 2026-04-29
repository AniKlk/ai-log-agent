import json
import logging
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient
from pydantic import BaseModel

from app.tools._cosmos_helpers import normalize_timestamp, resolve_exam_session_id
from app.tools.base import BaseTool
from app.tools.models import ChatHistoryInput, ChatHistoryOutput, ChatMessage

logger = logging.getLogger(__name__)

# Private constants — never exposed in I/O, config, or schemas
_COSMOS_DATABASE = "ExamChat"
_COSMOS_CHAT_CONTAINER = "exam-chat"
_TOKEN_ESTIMATE_CHARS = 4


class GetChatHistoryTool(BaseTool):
    name = "getChatHistory"
    description = (
        "Fetch candidate and proctor chat messages for one or more sessions. "
        "Returns chronologically ordered chat messages with sender attribution."
    )
    input_model = ChatHistoryInput

    def __init__(self, cosmos_client: CosmosClient) -> None:
        self._cosmos_client = cosmos_client

    async def execute(self, args: BaseModel) -> ChatHistoryOutput:
        assert isinstance(args, ChatHistoryInput)
        confirmation_codes = args.get_confirmation_codes()

        logger.info(
            "getChatHistory called",
            extra={"confirmationCodes": confirmation_codes},
        )

        messages: list[ChatMessage] = []
        is_multi = len(confirmation_codes) > 1

        for confirmation_code in confirmation_codes:
            # Resolve ConfirmationCode → ExamSessionId
            try:
                exam_session_id, _ = await resolve_exam_session_id(
                    self._cosmos_client, confirmation_code
                )
            except ValueError:
                logger.warning("No session found for %s", confirmation_code)
                continue

            try:
                database = self._cosmos_client.get_database_client(_COSMOS_DATABASE)
                container = database.get_container_client(_COSMOS_CHAT_CONTAINER)

                query = "SELECT * FROM c WHERE c.ExamSessionId = @esid"
                parameters: list[dict] = [{"name": "@esid", "value": exam_session_id}]

                items = container.query_items(query=query, parameters=parameters)
                async for item in items:
                    # Document has a nested Entries array with chat messages
                    entries = item.get("Entries") or []
                    for entry in entries:
                        ts = normalize_timestamp(entry.get("TimeStamp") or entry.get("Timestamp"))
                        # Role: 0 = candidate, 1 = proctor
                        role_val = entry.get("Role", 0)
                        sender: str = "proctor" if role_val == 1 else "candidate"
                        message_text = entry.get("Message", "")
                        if not message_text:
                            continue
                        if is_multi:
                            message_text = f"[{confirmation_code}] {message_text}"
                        messages.append(
                            ChatMessage(
                                timestamp=ts,
                                sender=sender,
                                message=message_text,
                            )
                        )
            except Exception:
                logger.exception("Cosmos DB chat query failed for %s", confirmation_code)

        messages.sort(key=lambda message: message.timestamp)

        # Apply token cap
        messages, truncated = self._apply_token_cap(messages)

        return ChatHistoryOutput(messages=messages, truncated=truncated)

    def _apply_token_cap(
        self, messages: list[ChatMessage]
    ) -> tuple[list[ChatMessage], bool]:
        from app.config import Settings

        max_chars = Settings().TOOL_RESPONSE_MAX_TOKENS * _TOKEN_ESTIMATE_CHARS

        serialized = json.dumps([m.model_dump() for m in messages])
        if len(serialized) <= max_chars:
            return messages, False

        truncated_messages: list[ChatMessage] = []
        current_chars = 0
        for msg in messages:
            msg_json = msg.model_dump_json()
            if current_chars + len(msg_json) > max_chars:
                break
            truncated_messages.append(msg)
            current_chars += len(msg_json)

        return truncated_messages, True
