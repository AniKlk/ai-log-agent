import asyncio
import json
import logging
import re
import time
from collections.abc import Iterable

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
_CONFIRMATION_CODE_REGEX = re.compile(r"\b\d{16}\b")
_EXPORT_UNSUPPORTED_TEXT = (
    "Direct file export is not supported in this interface. "
    "Please contact your technical support team or system administrator for the export."
)


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
        discovered_confirmation_codes = set(_CONFIRMATION_CODE_REGEX.findall(query))
        requested_export_formats = self._detect_export_formats(query)
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
                    discovered_confirmation_codes.update(
                        _CONFIRMATION_CODE_REGEX.findall(result_content)
                    )
                    discovered_confirmation_codes.update(
                        self._extract_confirmation_codes_from_tool_result(tool_name, result_content)
                    )

                    result_for_llm = self._prepare_tool_result_for_llm(tool_name, result_content)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_for_llm,
                        }
                    )

            elif choice.finish_reason == "stop" and choice.message.content:
                output = self._parse_output(choice.message.content, tools_invoked)
                output.confirmation_codes = self._merge_confirmation_codes(
                    output,
                    discovered_confirmation_codes,
                )
                self._apply_export_response_fallback(output, requested_export_formats)
                return output

            else:
                logger.warning(
                    "Unexpected finish_reason: %s",
                    choice.finish_reason,
                    extra={"request_id": request_id},
                )
                if choice.message.content:
                    output = self._parse_output(choice.message.content, tools_invoked)
                    output.confirmation_codes = self._merge_confirmation_codes(
                        output,
                        discovered_confirmation_codes,
                    )
                    self._apply_export_response_fallback(output, requested_export_formats)
                    return output
                break

        logger.warning("Max iterations reached", extra={"request_id": request_id})
        return AgentOutput(
            summary="Analysis incomplete — maximum iterations reached.",
            confirmation_codes=sorted(discovered_confirmation_codes),
            download_links=self._download_links_for_formats(requested_export_formats),
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

    @staticmethod
    def _merge_confirmation_codes(output: AgentOutput, discovered_codes: set[str]) -> list[str]:
        merged = set(discovered_codes)
        merged.update(output.confirmation_codes or [])
        merged.update(output.per_confirmation_code_summaries.keys())
        merged.update(output.per_confirmation_code_source_summary.keys())
        merged.update(_CONFIRMATION_CODE_REGEX.findall(output.summary))
        if output.root_cause:
            merged.update(_CONFIRMATION_CODE_REGEX.findall(output.root_cause))
        for finding in output.key_findings:
            merged.update(_CONFIRMATION_CODE_REGEX.findall(finding.description))
            for evidence in finding.evidence:
                merged.update(_CONFIRMATION_CODE_REGEX.findall(evidence))
        for entry in output.timeline:
            merged.update(_CONFIRMATION_CODE_REGEX.findall(entry.event))
        for warning in output.warnings or []:
            if warning:
                merged.update(_CONFIRMATION_CODE_REGEX.findall(warning))
        return sorted(merged)

    @staticmethod
    def _extract_confirmation_codes_from_tool_result(tool_name: str, result_content: str) -> set[str]:
        extracted: set[str] = set()

        try:
            payload = json.loads(result_content)
        except Exception:
            return extracted

        def _walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    key_lower = key.lower()
                    if key_lower in {"confirmationcode", "confirmation_code"} and isinstance(value, str):
                        code = value.strip()
                        if code:
                            extracted.add(code)
                    elif (
                        key_lower in {"confirmationcodes", "confirmation_codes"}
                        and isinstance(value, Iterable)
                        and not isinstance(value, (str, bytes))
                    ):
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                extracted.add(item.strip())
                            elif isinstance(item, dict):
                                _walk(item)
                    else:
                        _walk(value)
                return

            if isinstance(node, list):
                for item in node:
                    _walk(item)
                return

            if isinstance(node, str):
                for match in _CONFIRMATION_CODE_REGEX.findall(node):
                    extracted.add(match)

        _walk(payload)

        if tool_name == "getSessionLogStats" and isinstance(payload, dict):
            for row in payload.get("results", []):
                if isinstance(row, dict):
                    code = str(row.get("confirmation_code", "")).strip()
                    if code:
                        extracted.add(code)

        return extracted

    @staticmethod
    def _prepare_tool_result_for_llm(tool_name: str, result_content: str) -> str:
        if tool_name != "getSessionLogStats":
            return result_content

        try:
            payload = json.loads(result_content)
        except Exception:
            return result_content

        if not isinstance(payload, dict):
            return result_content

        results = payload.get("results")
        if not isinstance(results, list):
            return result_content

        if len(results) <= 250:
            return result_content

        compact_results = []
        for row in results[:200]:
            if not isinstance(row, dict):
                continue
            compact_results.append(
                {
                    "confirmation_code": row.get("confirmation_code", ""),
                    "session_id": row.get("session_id", ""),
                    "hit_count": row.get("hit_count", 0),
                }
            )

        compact_payload = {
            "total_client_sessions": payload.get("total_client_sessions", 0),
            "active_client_sessions_in_window": payload.get("active_client_sessions_in_window", 0),
            "total_matching_rows_in_window": payload.get("total_matching_rows_in_window", 0),
            "candidates_with_hits": payload.get("candidates_with_hits", 0),
            "results_truncated_for_llm": True,
            "results_returned_to_llm": len(compact_results),
            "results_total_available": len(results),
            "results": compact_results,
        }

        return json.dumps(compact_payload, ensure_ascii=False)

    @staticmethod
    def _detect_export_formats(query: str) -> set[str]:
        normalized = query.lower()
        formats: set[str] = set()
        if "pdf" in normalized:
            formats.add("pdf")
        if any(token in normalized for token in ("excel", "xlsx", "xls", "spreadsheet")):
            formats.add("xlsx")
        return formats

    @staticmethod
    def _download_links_for_formats(formats: set[str]) -> dict[str, str]:
        links: dict[str, str] = {}
        if "pdf" in formats:
            links["Download PDF"] = "export://pdf"
        if "xlsx" in formats:
            links["Download Excel"] = "export://xlsx"
        return links

    @staticmethod
    def _sanitize_export_text(text: str | None) -> str | None:
        if not text:
            return text
        return text.replace(
            _EXPORT_UNSUPPORTED_TEXT,
            "Direct export is available via the provided download links.",
        )

    def _apply_export_response_fallback(self, output: AgentOutput, formats: set[str]) -> None:
        output.summary = self._sanitize_export_text(output.summary) or output.summary
        output.root_cause = self._sanitize_export_text(output.root_cause)
        output.per_confirmation_code_summaries = {
            code: self._sanitize_export_text(summary) or summary
            for code, summary in output.per_confirmation_code_summaries.items()
        }

        if output.warnings:
            filtered_warnings: list[str | None] = []
            for warning in output.warnings:
                if not warning:
                    filtered_warnings.append(warning)
                    continue
                lower_warning = warning.lower()
                if "export" in lower_warning and (
                    "not supported" in lower_warning
                    or "contact your technical support team" in lower_warning
                    or "system administrator" in lower_warning
                ):
                    continue
                filtered_warnings.append(self._sanitize_export_text(warning))
            output.warnings = filtered_warnings

        if formats:
            fallback_links = self._download_links_for_formats(formats)
            output.download_links = {**fallback_links, **(output.download_links or {})}
