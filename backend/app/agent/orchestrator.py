import asyncio
import json
import logging
import time

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.agent.prompt import SYSTEM_PROMPT
from app.agent.types import AgentOutput
from app.config import Settings
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503}
_MAX_TOOL_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]
_MAX_LLM_RETRIES = 2


class AgentOrchestrator:
    def __init__(
        self,
        client: AsyncOpenAI,
        registry: ToolRegistry,
        settings: Settings,
    ) -> None:
        self._client = client
        self._registry = registry
        self._settings = settings

    async def run(
        self,
        query: str,
        request_id: str,
        conversation_history: list[dict] | None = None,
    ) -> AgentOutput:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Inject prior conversation for follow-up context
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})  # type: ignore[arg-type]

        messages.append({"role": "user", "content": query})

        tools_invoked: list[str] = []
        tool_definitions = self._registry.get_definitions()

        for iteration in range(self._settings.MAX_AGENT_ITERATIONS):
            logger.info(
                "Agent iteration %d",
                iteration + 1,
                extra={"request_id": request_id},
            )

            response = await self._call_llm_with_retry(messages, tool_definitions, request_id)
            choice = response.choices[0]

            logger.info(
                "LLM response",
                extra={
                    "request_id": request_id,
                    "finish_reason": choice.finish_reason,
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                    "completion_tokens": response.usage.completion_tokens if response.usage else None,
                },
            )

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Append assistant message with tool_calls
                messages.append(choice.message)  # type: ignore[arg-type]

                for tool_call in choice.message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = tool_call.function.arguments

                    logger.info(
                        "Tool call: %s args=%s",
                        tool_name,
                        tool_args[:500],
                    )

                    if tool_name not in tools_invoked:
                        tools_invoked.append(tool_name)

                    result_content = await self._execute_tool_with_retry(
                        tool_name, tool_args, request_id
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_content,
                        }
                    )

            elif choice.finish_reason == "stop" and choice.message.content:
                return self._parse_output(choice.message.content, tools_invoked)

            else:
                logger.warning(
                    "Unexpected finish_reason: %s",
                    choice.finish_reason,
                    extra={"request_id": request_id},
                )
                if choice.message.content:
                    return self._parse_output(choice.message.content, tools_invoked)
                break

        logger.warning("Max iterations reached", extra={"request_id": request_id})
        return AgentOutput(
            summary="Analysis incomplete — maximum iterations reached.",
            tools_invoked=tools_invoked,
            warnings=["Max agent iterations reached. Results may be partial."],
        )

    async def _call_llm_with_retry(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict],
        request_id: str,
    ):
        last_error: Exception | None = None
        for attempt in range(_MAX_LLM_RETRIES + 1):
            try:
                return await self._client.chat.completions.create(
                    model=self._settings.AZURE_OPENAI_DEPLOYMENT,
                    messages=messages,
                    tools=tools,  # type: ignore[arg-type]
                    temperature=0,
                )
            except Exception as e:
                last_error = e
                status_code = getattr(e, "status_code", None)
                if status_code in _TRANSIENT_STATUS_CODES and attempt < _MAX_LLM_RETRIES:
                    wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                    logger.warning(
                        "LLM transient error (attempt %d), retrying in %ds",
                        attempt + 1,
                        wait,
                        extra={"request_id": request_id},
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
        raise last_error  # type: ignore[misc]

    async def _execute_tool_with_retry(
        self, name: str, args_json: str, request_id: str
    ) -> str:
        start = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(_MAX_TOOL_RETRIES + 1):
            try:
                result = await self._registry.execute(name, args_json)
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "Tool executed",
                    extra={
                        "request_id": request_id,
                        "tool": name,
                        "duration_ms": duration_ms,
                        "success": True,
                    },
                )
                return result
            except Exception as e:
                last_error = e
                status_code = getattr(e, "status_code", None)
                is_transient = status_code in _TRANSIENT_STATUS_CODES or isinstance(
                    e, (asyncio.TimeoutError, ConnectionError)
                )

                if is_transient and attempt < _MAX_TOOL_RETRIES:
                    wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                    logger.warning(
                        "Tool %s transient error (attempt %d), retrying in %ds",
                        name,
                        attempt + 1,
                        wait,
                        extra={"request_id": request_id},
                    )
                    await asyncio.sleep(wait)
                else:
                    # Non-transient or exhausted retries — pass error to LLM
                    duration_ms = int((time.monotonic() - start) * 1000)
                    error_type = type(last_error).__name__
                    error_msg = str(last_error)
                    logger.error(
                        "Tool %s failed: %s",
                        name,
                        error_msg,
                        extra={
                            "request_id": request_id,
                            "tool": name,
                            "duration_ms": duration_ms,
                        },
                    )
                    return json.dumps({"error": f"{error_type}: {error_msg}"})

        # Should not reach here, but safety fallback
        return json.dumps({"error": f"Tool {name} failed after retries"})

    @staticmethod
    def _parse_output(content: str, tools_invoked: list[str]) -> AgentOutput:
        text = content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            first_newline = text.find("\n")
            text = text[first_newline + 1 :] if first_newline != -1 else text[3:]
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            data = json.loads(text)
            # Normalize string "null" values to actual None
            for key in ("root_cause", "root_cause_confidence"):
                if data.get(key) == "null":
                    data[key] = None
            output = AgentOutput.model_validate(data)
            output.tools_invoked = tools_invoked
            # Filter out null values from warnings list
            if output.warnings:
                output.warnings = [w for w in output.warnings if w is not None]
            return output
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse agent output as JSON: %s", e)
            # Try to extract JSON from the content
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                    output = AgentOutput.model_validate(data)
                    output.tools_invoked = tools_invoked
                    return output
                except Exception:
                    pass
            return AgentOutput(
                summary=content[:1000],
                tools_invoked=tools_invoked,
                warnings=[f"Output parsing failed: {type(e).__name__}"],
            )
