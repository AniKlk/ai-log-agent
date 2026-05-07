import asyncio
import json
import logging
import re
import time
from collections.abc import Iterable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.agent.benign_signals import get_catalog
from app.agent.conversation_state import build_conversation_state, build_state_system_message
from app.agent.hypothesis import HypothesisState
from app.agent.investigation_policy import (
    InvestigationProfile,
    analyze_tool_result,
    build_adaptive_system_message,
    build_investigation_profile,
    build_investigation_system_message,
)

# Maximum total characters across all messages before trimming old tool results.
# Roughly 80% of a 128k-token context window at 4 chars/token.
_CONTEXT_BUDGET_CHARS = 400_000
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.types import AgentOutput, Finding
from app.config import Settings
from app.tools.registry import ToolRegistry

_NO_APP_INSIGHTS_EXIT_CLAIM = re.compile(r"\bno\s+app\s+insights\s+exit\s+markers?\b", re.IGNORECASE)
_EXIT_EVIDENCE_MARKERS = (
    "candidate-app exit marker",
    "candidate-app security marker",
    "app-insights exit marker rollup",
    "candidate exited",
    "candidate exit",
    "exiting application",
    "exiting app",
    "exit lockdown window",
    "ipc server action received: exit",
    "content protection bypassed",
    "lockdown bypass detected",
)

_APP_INSIGHTS_MARKER_PATTERNS = (
    "candidate-app login marker",
    "candidate-app exit marker",
    "candidate-app security marker",
    "app-insights exit marker rollup",
    "app-insights login marker rollup",
)
_MAX_APP_INSIGHTS_VISIBILITY_LOGS = 300
_FAILED_KILL_PROCESS_REGEX = re.compile(
    r"failed\s+to\s+kill(?:\s+(?:app|process))?\s*[\"'`]?(?P<name>[A-Za-z0-9_.\- ]+)",
    re.IGNORECASE,
)

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
        profile = build_investigation_profile(query, conversation_history)
        state = build_conversation_state(conversation_history)
        discovered_confirmation_codes = set(_CONFIRMATION_CODE_REGEX.findall(query))
        app_insights_exit_evidence: list[str] = []
        app_insights_logs: list[dict[str, str | None]] = []
        app_insights_summary: dict[str, object] | None = None
        blocked_process_names: list[str] = []
        workspaces_queried: set[str] = set()
        hypothesis = HypothesisState()
        requested_export_formats = self._detect_export_formats(query)
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": build_investigation_system_message(profile)},
        ]
        state_message = build_state_system_message(state)
        if state_message:
            messages.append({"role": "system", "content": state_message})

        # Inject prior conversation for follow-up context
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})  # type: ignore[arg-type]

        messages.append({"role": "user", "content": query})

        tools_invoked: list[str] = list(state.tools_already_used)
        tool_definitions = self._registry.get_definitions()

        for iteration in range(self._settings.MAX_AGENT_ITERATIONS):
            logger.info(
                "Agent iteration %d",
                iteration + 1,
                extra={"request_id": request_id},
            )

            messages = self._trim_messages_for_token_budget(messages)
            messages = self._ensure_tool_call_message_integrity(messages)
            response = await self._call_llm_with_retry(messages, tool_definitions, request_id)
            choice = response.choices[0]

            logger.info(
                "LLM response",
                extra={
                    "request_id": request_id,
                    "finish_reason": choice.finish_reason,
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                    "completion_tokens": (
                        response.usage.completion_tokens if response.usage else None
                    ),
                },
            )

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                # Append assistant message with tool_calls
                messages.append(choice.message)  # type: ignore[arg-type]
                observations = []

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
                    app_insights_exit_evidence.extend(
                        self._extract_app_insights_exit_evidence(tool_name, result_content)
                    )
                    blocked_process_names = self._merge_blocked_process_names(
                        blocked_process_names,
                        self._extract_blocked_process_names(result_content),
                    )
                    ai_logs, ai_summary = self._extract_app_insights_visibility_payload(
                        tool_name,
                        result_content,
                    )
                    if ai_logs:
                        app_insights_logs = self._merge_app_insights_logs(
                            app_insights_logs,
                            ai_logs,
                        )
                    if ai_summary:
                        app_insights_summary = self._merge_app_insights_summary(
                            app_insights_summary,
                            ai_summary,
                        )
                    # Track which KQL workspaces have been queried
                    if tool_name == "queryKQL":
                        try:
                            _ws = json.loads(tool_args).get("workspace", "proproctor")
                            workspaces_queried.add(str(_ws))
                        except Exception:
                            pass
                    # Feed hypothesis tracker
                    try:
                        hypothesis.record_tool(tool_name, json.loads(result_content))
                    except Exception:
                        pass
                    discovered_confirmation_codes.update(
                        _CONFIRMATION_CODE_REGEX.findall(result_content)
                    )
                    discovered_confirmation_codes.update(
                        self._extract_confirmation_codes_from_tool_result(tool_name, result_content)
                    )

                    result_for_llm = self._prepare_tool_result_for_llm(tool_name, result_content)
                    observation = analyze_tool_result(tool_name, tool_args, result_content, profile)
                    if observation is not None:
                        observations.append(observation)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_for_llm,
                        }
                    )

                adaptive_note = build_adaptive_system_message(profile, observations, tools_invoked)
                if adaptive_note:
                    messages.append({"role": "system", "content": adaptive_note})
                # Inject hypothesis state when enough tools have run
                if hypothesis.should_inject(iteration):
                    hyp_msg = hypothesis.to_system_message()
                    if hyp_msg:
                        messages.append({"role": "system", "content": hyp_msg})

            elif choice.finish_reason == "stop" and choice.message.content:
                if self._must_continue_investigation(profile, tools_invoked):
                    messages.append({"role": "assistant", "content": choice.message.content})
                    messages.append(
                        {
                            "role": "system",
                            "content": self._build_missing_tool_message(profile, tools_invoked),
                        }
                    )
                    continue
                if self._must_continue_generic_investigation(
                    profile, tools_invoked, workspaces_queried
                ):
                    messages.append({"role": "assistant", "content": choice.message.content})
                    messages.append(
                        {
                            "role": "system",
                            "content": self._build_generic_continuation_message(
                                profile, tools_invoked, workspaces_queried
                            ),
                        }
                    )
                    continue
                output = self._parse_output(choice.message.content, tools_invoked)
                output.confirmation_codes = self._merge_confirmation_codes(
                    output,
                    discovered_confirmation_codes,
                )
                self._apply_app_insights_visibility(output, app_insights_logs, app_insights_summary)
                self._apply_blocked_process_context(output, blocked_process_names)
                self._apply_exit_marker_consistency_safeguard(output, app_insights_exit_evidence)
                self._enforce_answer_style(output)
                self._apply_export_response_fallback(output, requested_export_formats)
                return output

            else:
                logger.warning(
                    "Unexpected finish_reason: %s",
                    choice.finish_reason,
                    extra={"request_id": request_id},
                )
                if choice.message.content:
                    if self._must_continue_investigation(profile, tools_invoked):
                        messages.append({"role": "assistant", "content": choice.message.content})
                        messages.append(
                            {
                                "role": "system",
                                "content": self._build_missing_tool_message(profile, tools_invoked),
                            }
                        )
                        continue
                    output = self._parse_output(choice.message.content, tools_invoked)
                    output.confirmation_codes = self._merge_confirmation_codes(
                        output,
                        discovered_confirmation_codes,
                    )
                    self._apply_app_insights_visibility(output, app_insights_logs, app_insights_summary)
                    self._apply_blocked_process_context(output, blocked_process_names)
                    self._apply_exit_marker_consistency_safeguard(
                        output,
                        app_insights_exit_evidence,
                    )
                    self._enforce_answer_style(output)
                    self._apply_export_response_fallback(output, requested_export_formats)
                    self._apply_benign_signal_context(output)
                    return output
                break

        logger.warning("Max iterations reached", extra={"request_id": request_id})
        return AgentOutput(
            summary="Analysis incomplete — maximum iterations reached.",
            confirmation_codes=sorted(discovered_confirmation_codes),
            download_links=self._download_links_for_formats(requested_export_formats),
            app_insights_summary=app_insights_summary,
            app_insights_logs=app_insights_logs,
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
                preflight_messages = self._preflight_openai_messages(messages)
                return await self._client.chat.completions.create(
                    model=self._settings.AZURE_OPENAI_DEPLOYMENT,
                    messages=preflight_messages,
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
    def _extract_confirmation_codes_from_tool_result(
        tool_name: str,
        result_content: str,
    ) -> set[str]:
        extracted: set[str] = set()

        try:
            payload = json.loads(result_content)
        except Exception:
            return extracted

        def _walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    key_lower = key.lower()
                    if key_lower in {
                        "confirmationcode",
                        "confirmation_code",
                    } and isinstance(value, str):
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
    def _contains_no_app_insights_exit_claim(text: str | None) -> bool:
        return bool(text and _NO_APP_INSIGHTS_EXIT_CLAIM.search(text))

    @staticmethod
    def _normalize_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def _collect_exit_evidence_from_node(
        cls,
        node: object,
        evidence: list[str],
        seen: set[str],
    ) -> None:
        if isinstance(node, dict):
            msg = cls._normalize_text(node.get("message") or node.get("event") or node.get("name"))
            source = cls._normalize_text(node.get("source")).lower()
            timestamp = cls._normalize_text(node.get("timestamp") or node.get("time"))
            msg_lower = msg.lower()
            has_marker = any(marker in msg_lower for marker in _EXIT_EVIDENCE_MARKERS)
            source_looks_ai = "app-insights" in source or source == "appinsights"

            if has_marker and (source_looks_ai or "candidate-app" in msg_lower):
                snippet = f"{timestamp} {msg}".strip()
                if snippet and snippet not in seen:
                    seen.add(snippet)
                    evidence.append(snippet[:280])

            for value in node.values():
                cls._collect_exit_evidence_from_node(value, evidence, seen)
            return

        if isinstance(node, list):
            for item in node:
                cls._collect_exit_evidence_from_node(item, evidence, seen)
            return

        if isinstance(node, str):
            msg_lower = node.lower()
            if any(marker in msg_lower for marker in _EXIT_EVIDENCE_MARKERS):
                snippet = node.strip()[:280]
                if snippet and snippet not in seen:
                    seen.add(snippet)
                    evidence.append(snippet)

    @classmethod
    def _extract_app_insights_exit_evidence(cls, tool_name: str, result_content: str) -> list[str]:
        if tool_name not in {
            "getSessionData",
            "getSessionTimeline",
            "runKql",
            "getKql",
            "queryKql",
        }:
            return []

        try:
            payload = json.loads(result_content)
        except Exception:
            return []

        evidence: list[str] = []
        seen: set[str] = set()
        cls._collect_exit_evidence_from_node(payload, evidence, seen)
        return evidence

    @staticmethod
    def _extract_app_insights_visibility_payload(
        tool_name: str,
        result_content: str,
    ) -> tuple[list[dict[str, str | None]], dict[str, object] | None]:
        if tool_name != "getSessionData":
            return [], None

        try:
            payload = json.loads(result_content)
        except Exception:
            return [], None

        if not isinstance(payload, dict):
            return [], None

        events = payload.get("events") or []
        errors = payload.get("errors") or []
        if not isinstance(events, list) or not isinstance(errors, list):
            return [], None

        logs: list[dict[str, str | None]] = []
        info_events = 0
        error_events = 0
        disconnect_events = 0
        marker_events = 0

        for event in events:
            if not isinstance(event, dict):
                continue
            source = str(event.get("source", "")).lower()
            if source != "app-insights":
                continue

            timestamp = event.get("timestamp")
            type_value = str(event.get("type", "info")).lower()
            if type_value not in {"info", "warning", "error", "disconnect"}:
                type_value = "info"
            message = str(event.get("message", "")).strip()
            if not message:
                continue

            logs.append(
                {
                    "timestamp": str(timestamp) if timestamp else None,
                    "type": type_value,
                    "message": message,
                }
            )

            if type_value == "info":
                info_events += 1
            elif type_value == "warning":
                info_events += 1
            elif type_value == "error":
                error_events += 1
            else:
                disconnect_events += 1

            lowered = message.lower()
            if any(marker in lowered for marker in _APP_INSIGHTS_MARKER_PATTERNS):
                marker_events += 1

        top_error_signatures: list[str] = []
        seen_errors: set[str] = set()
        for error in errors:
            if not isinstance(error, dict):
                continue
            signature = str(error.get("error", "")).strip()
            if not signature or signature in seen_errors:
                continue
            seen_errors.add(signature)
            top_error_signatures.append(signature[:220])
            if len(top_error_signatures) >= 5:
                break

        logs.sort(
            key=lambda row: (
                0 if row.get("type") == "error" else 1,
                0
                if not any(marker in str(row.get("message", "")).lower() for marker in _APP_INSIGHTS_MARKER_PATTERNS)
                else 1,
                str(row.get("timestamp") or ""),
            ),
            reverse=True,
        )

        summary = {
            "total_events": len(logs),
            "info_events": info_events,
            "error_events": error_events,
            "disconnect_events": disconnect_events,
            "marker_events": marker_events,
            "non_marker_events": max(len(logs) - marker_events, 0),
            "error_records": len(errors),
            "top_error_signatures": top_error_signatures,
        }

        return logs[:_MAX_APP_INSIGHTS_VISIBILITY_LOGS], summary

    @staticmethod
    def _merge_app_insights_logs(
        existing: list[dict[str, str | None]],
        incoming: list[dict[str, str | None]],
    ) -> list[dict[str, str | None]]:
        merged: list[dict[str, str | None]] = []
        seen: set[tuple[str | None, str | None, str]] = set()
        for row in [*existing, *incoming]:
            key = (row.get("timestamp"), row.get("type"), str(row.get("message", "")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        merged.sort(
            key=lambda row: (
                0 if row.get("type") == "error" else 1,
                0
                if not any(marker in str(row.get("message", "")).lower() for marker in _APP_INSIGHTS_MARKER_PATTERNS)
                else 1,
                str(row.get("timestamp") or ""),
            ),
            reverse=True,
        )
        return merged[:_MAX_APP_INSIGHTS_VISIBILITY_LOGS]

    @staticmethod
    def _merge_app_insights_summary(
        existing: dict[str, object] | None,
        incoming: dict[str, object],
    ) -> dict[str, object]:
        if not existing:
            return dict(incoming)

        merged = dict(existing)
        numeric_keys = (
            "total_events",
            "info_events",
            "error_events",
            "disconnect_events",
            "marker_events",
            "non_marker_events",
            "error_records",
        )
        for key in numeric_keys:
            merged[key] = int(existing.get(key, 0) or 0) + int(incoming.get(key, 0) or 0)

        existing_errors = [str(item) for item in existing.get("top_error_signatures", []) or []]
        incoming_errors = [str(item) for item in incoming.get("top_error_signatures", []) or []]
        merged_errors = list(dict.fromkeys([*existing_errors, *incoming_errors]))[:5]
        merged["top_error_signatures"] = merged_errors
        return merged

    @staticmethod
    def _apply_app_insights_visibility(
        output: AgentOutput,
        logs: list[dict[str, str | None]],
        summary: dict[str, object] | None,
    ) -> None:
        if logs:
            output.app_insights_logs = logs
        if summary:
            output.app_insights_summary = summary  # type: ignore[assignment]

    @classmethod
    def _extract_blocked_process_names(cls, result_content: str) -> list[str]:
        try:
            payload = json.loads(result_content)
        except Exception:
            payload = result_content

        names: list[str] = []
        seen: set[str] = set()

        def _walk(node) -> None:
            if isinstance(node, dict):
                for value in node.values():
                    _walk(value)
                return
            if isinstance(node, list):
                for item in node:
                    _walk(item)
                return
            if not isinstance(node, str):
                return

            for match in _FAILED_KILL_PROCESS_REGEX.finditer(node):
                name = (match.group("name") or "").strip().strip(". ")
                name = re.split(r'\s*\{|\s*\[|\s*\(', name, maxsplit=1)[0].strip()
                if not name:
                    continue
                lowered = name.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                names.append(name)

        _walk(payload)
        return names

    @staticmethod
    def _merge_blocked_process_names(existing: list[str], incoming: list[str]) -> list[str]:
        return list(dict.fromkeys([*existing, *incoming]))

    @classmethod
    def _apply_blocked_process_context(
        cls,
        output: AgentOutput,
        blocked_process_names: list[str],
    ) -> None:
        if not blocked_process_names:
            return

        names_text = ", ".join(blocked_process_names)
        summary_note = f" App Insights identified the failed-to-close app/process name(s): {names_text}."
        if names_text.lower() not in (output.summary or "").lower():
            output.summary = (output.summary.rstrip() + summary_note).strip()

        finding_text = (
            f"App Insights candidate-app telemetry identified unauthorized app/process termination failures involving {names_text}."
        )
        if not any(names_text.lower() in finding.description.lower() for finding in output.key_findings):
            output.key_findings.insert(
                0,
                Finding(
                    description=finding_text,
                    severity="warning",
                    evidence=[f"App Insights message pattern matched failed-to-kill signal for: {names_text}."],
                ),
            )

        if output.root_cause and names_text.lower() not in output.root_cause.lower():
            output.root_cause = output.root_cause.rstrip() + f" App Insights identified the app/process name(s): {names_text}."

    @classmethod
    def _rewrite_no_exit_claim_with_evidence(
        cls,
        text: str | None,
        evidence: list[str],
    ) -> str | None:
        if not text or not cls._contains_no_app_insights_exit_claim(text):
            return text

        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        filtered = [
            sentence
            for sentence in sentences
            if not cls._contains_no_app_insights_exit_claim(sentence)
        ]

        preview = "; ".join(evidence[:3])
        injected = f"App Insights exit evidence detected: {preview}."

        if filtered:
            return f"{injected} {' '.join(filtered)}".strip()
        return injected

    @classmethod
    def _apply_exit_marker_consistency_safeguard(
        cls,
        output: AgentOutput,
        evidence: list[str],
    ) -> None:
        if not evidence:
            return

        output.summary = cls._rewrite_no_exit_claim_with_evidence(output.summary, evidence) or output.summary
        output.root_cause = cls._rewrite_no_exit_claim_with_evidence(output.root_cause, evidence)

        output.per_confirmation_code_summaries = {
            code: cls._rewrite_no_exit_claim_with_evidence(summary, evidence) or summary
            for code, summary in output.per_confirmation_code_summaries.items()
        }

        if output.warnings:
            output.warnings = [
                cls._rewrite_no_exit_claim_with_evidence(warning, evidence)
                if warning
                else warning
                for warning in output.warnings
            ]

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

    @staticmethod
    def _must_continue_investigation(
        profile: InvestigationProfile,
        tools_invoked: list[str],
    ) -> bool:
        if not profile.is_session_query:
            return False
        return any(tool not in tools_invoked for tool in profile.mandatory_tools)

    @staticmethod
    def _must_continue_generic_investigation(
        profile: InvestigationProfile,
        tools_invoked: list[str],
        workspaces_queried: set[str],
    ) -> bool:
        """Enforce both-workspace and stats-tool completion for generic queries."""
        if profile.is_session_query:
            return False
        if not profile.generic_requires_both_workspaces:
            return False
        if "queryKQL" in tools_invoked:
            # Both workspaces must be queried
            if "proproctor" not in workspaces_queried or "infrastructure" not in workspaces_queried:
                return True
        if profile.is_aggregate_impact_query and "getSessionLogStats" not in tools_invoked:
            return True
        return False

    @staticmethod
    def _build_missing_tool_message(
        profile: InvestigationProfile,
        tools_invoked: list[str],
    ) -> str:
        missing = [tool for tool in profile.mandatory_tools if tool not in tools_invoked]
        if not missing:
            return (
                "Do not finalize yet. Continue investigating with wider ranges, adjacent services, "
                "or impact validation if evidence is still incomplete."
            )

        return (
            "You attempted to conclude before collecting the mandatory baseline evidence for a "
            f"session investigation. Call these missing tools first: {', '.join(missing)}. "
            "After that, continue adaptively: if one source is empty, widen the range, inspect "
            "adjacent services, and confirm candidate impact before finalizing systemic issues."
        )

    @staticmethod
    def _build_generic_continuation_message(
        profile: InvestigationProfile,
        tools_invoked: list[str],
        workspaces_queried: set[str],
    ) -> str:
        parts: list[str] = []
        if "queryKQL" in tools_invoked:
            missing_ws = [
                ws for ws in ("proproctor", "infrastructure") if ws not in workspaces_queried
            ]
            if missing_ws:
                parts.append(
                    f"You have not yet queried the {', '.join(missing_ws)} workspace(s). "
                    "Service-health investigations require evidence from BOTH proproctor "
                    "(App Insights) and infrastructure (Kubernetes) workspaces before concluding."
                )
        if profile.is_aggregate_impact_query and "getSessionLogStats" not in tools_invoked:
            parts.append(
                "This query asks about candidate impact at scale. You MUST call "
                "getSessionLogStats with the relevant client_code, date window, and keywords "
                "to count affected candidates accurately before finalising."
            )
        return (
            " ".join(parts)
            or "Continue investigating — gather more evidence before finalising."
        )

    @staticmethod
    def _msg_content(msg: object) -> str:
        """Safely extract text content from a dict or a ChatCompletionMessage object."""
        if isinstance(msg, dict):
            return str(msg.get("content") or "")
        # ChatCompletionMessage (Pydantic model from OpenAI SDK)
        content = getattr(msg, "content", None)
        return str(content) if content else ""

    @staticmethod
    def _msg_role(msg: object) -> str:
        if isinstance(msg, dict):
            return str(msg.get("role", ""))
        return str(getattr(msg, "role", ""))

    @staticmethod
    def _has_tool_calls(msg: object) -> bool:
        if isinstance(msg, dict):
            return bool(msg.get("tool_calls"))
        return bool(getattr(msg, "tool_calls", None))

    @staticmethod
    def _tool_call_ids(msg: object) -> list[str]:
        ids: list[str] = []
        if isinstance(msg, dict):
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    ids.append(str(tc_id))
            return ids

        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            tc_id = getattr(tc, "id", None)
            if tc_id:
                ids.append(str(tc_id))
        return ids

    @staticmethod
    def _msg_tool_call_id(msg: object) -> str | None:
        if isinstance(msg, dict):
            tc_id = msg.get("tool_call_id")
            return str(tc_id) if tc_id else None
        tc_id = getattr(msg, "tool_call_id", None)
        return str(tc_id) if tc_id else None

    @classmethod
    def _ensure_tool_call_message_integrity(cls, messages: list) -> list:
        """Ensure every assistant tool_call has matching tool responses in sequence."""
        repaired: list = []
        i = 0
        inserted = 0

        while i < len(messages):
            msg = messages[i]
            repaired.append(msg)

            if not (cls._msg_role(msg) == "assistant" and cls._has_tool_calls(msg)):
                i += 1
                continue

            required_ids = cls._tool_call_ids(msg)
            seen_ids: set[str] = set()

            j = i + 1
            while j < len(messages) and cls._msg_role(messages[j]) == "tool":
                tool_msg = messages[j]
                repaired.append(tool_msg)
                tool_call_id = cls._msg_tool_call_id(tool_msg)
                if tool_call_id:
                    seen_ids.add(tool_call_id)
                j += 1

            for missing_id in required_ids:
                if missing_id in seen_ids:
                    continue
                repaired.append(
                    {
                        "role": "tool",
                        "tool_call_id": missing_id,
                        "content": json.dumps(
                            {
                                "error": (
                                    "Tool response missing due to context compaction; "
                                    "continue with available evidence."
                                )
                            }
                        ),
                    }
                )
                inserted += 1

            i = j

        if inserted:
            logger.warning(
                "Repaired message integrity by inserting %d missing tool responses",
                inserted,
            )
        return repaired

    @classmethod
    def _drop_orphan_tool_messages(cls, messages: list) -> list:
        """Drop tool-role messages not tied to the immediately preceding assistant tool call."""
        cleaned: list = []
        expected_ids: set[str] = set()
        removed = 0

        for msg in messages:
            role = cls._msg_role(msg)
            if role == "assistant" and cls._has_tool_calls(msg):
                cleaned.append(msg)
                expected_ids = set(cls._tool_call_ids(msg))
                continue

            if role == "tool":
                tool_call_id = cls._msg_tool_call_id(msg)
                if not tool_call_id or tool_call_id not in expected_ids:
                    removed += 1
                    continue
                cleaned.append(msg)
                expected_ids.discard(tool_call_id)
                continue

            cleaned.append(msg)
            expected_ids = set()

        if removed:
            logger.warning("Dropped %d orphan tool messages during preflight", removed)
        return cleaned

    @classmethod
    def _preflight_openai_messages(cls, messages: list) -> list:
        """Final safety pass before OpenAI call to enforce tool-call message protocol."""
        ensured = cls._ensure_tool_call_message_integrity(messages)
        return cls._drop_orphan_tool_messages(ensured)

    @classmethod
    def _trim_messages_for_token_budget(
        cls,
        messages: list,
    ) -> list:
        """Trim message history without breaking assistant/tool call sequencing."""
        total = sum(len(cls._msg_content(m)) for m in messages)
        if total <= _CONTEXT_BUDGET_CHARS:
            return messages

        trimmed = list(messages)
        removed_chars = 0
        removed_msgs = 0

        # Pass 1: drop oldest non-essential text messages (keep tool_call and tool chain intact)
        idx = 0
        while (
            idx < len(trimmed)
            and total - removed_chars > _CONTEXT_BUDGET_CHARS
        ):
            msg = trimmed[idx]
            role = cls._msg_role(msg)
            if role in {"user", "assistant", "system"} and not cls._has_tool_calls(msg):
                removed_chars += len(cls._msg_content(msg))
                removed_msgs += 1
                trimmed.pop(idx)
                continue
            idx += 1

        # Pass 2: if still over budget, drop oldest complete tool exchange blocks
        while total - removed_chars > _CONTEXT_BUDGET_CHARS:
            block_start = None
            for i, msg in enumerate(trimmed):
                if cls._msg_role(msg) == "assistant" and cls._has_tool_calls(msg):
                    block_start = i
                    break

            if block_start is None:
                break

            required_ids = set(cls._tool_call_ids(trimmed[block_start]))
            block_indices = [block_start]
            seen_ids: set[str] = set()
            j = block_start + 1
            while j < len(trimmed) and cls._msg_role(trimmed[j]) == "tool":
                block_indices.append(j)
                tool_call_id = cls._msg_tool_call_id(trimmed[j])
                if tool_call_id:
                    seen_ids.add(tool_call_id)
                if required_ids and required_ids.issubset(seen_ids):
                    j += 1
                    break
                j += 1

            if required_ids and not required_ids.issubset(seen_ids):
                break

            for remove_idx in reversed(block_indices):
                removed_chars += len(cls._msg_content(trimmed[remove_idx]))
                removed_msgs += 1
                trimmed.pop(remove_idx)

        logger.info(
            "Token budget trim: removed %d chars across %d messages (%d→%d)",
            removed_chars,
            removed_msgs,
            total,
            total - removed_chars,
        )
        return trimmed

    _FILLER_PATTERNS = re.compile(
        r"^(based on (my |the |an? )?analysis[,.]?\s*|"
        r"after (thoroughly |carefully )?reviewing[^,]*[,.]?\s*|"
        r"i (have )?(thoroughly |carefully )?investigated[^.]*\.\s*|"
        r"here('s| is) (a |the |my |an? )?(detailed |comprehensive |complete )?"
        r"(summary|analysis|report|overview)[^.]*\.\s*)",
        re.IGNORECASE,
    )

    @classmethod
    def _enforce_answer_style(cls, output: "AgentOutput") -> None:
        """Strip filler openers from summary and ensure root cause speaks to evidence."""
        if output.summary:
            cleaned = cls._FILLER_PATTERNS.sub("", output.summary).strip()
            # Capitalise first letter if we stripped a filler opener
            if cleaned and cleaned[0].islower():
                cleaned = cleaned[0].upper() + cleaned[1:]
            output.summary = cleaned or output.summary

    @classmethod
    def _apply_benign_signal_context(cls, output: "AgentOutput") -> None:
        """
        Check findings against known benign signal patterns and enrich output with context.

        This post-processing step automatically detects benign patterns (e.g., Twilio timeouts,
        event processing errors, exception mappers) that often cause false-positive escalations,
        and adds interpretation guidance to the summary and warnings.
        """
        catalog = get_catalog()
        benign_matches: list[tuple[str, str]] = []  # (finding_text, interpretation)

        # Check summary for benign patterns
        if output.summary:
            match = catalog.match_finding(output.summary)
            if match:
                benign_matches.append(
                    (output.summary[:100], match.interpretation)
                )

        # Check key findings for benign patterns
        for finding in output.key_findings:
            if finding.description:
                match = catalog.match_finding(finding.description)
                if match:
                    benign_matches.append(
                        (finding.description[:100], match.interpretation)
                    )

        # If benign signals detected, inject clarifying context
        if benign_matches:
            benign_note = (
                "\n\n**Benign Signal Classification:**\n"
                "The analysis identified findings that match known benign patterns from ProProctor services:\n"
            )
            for _, interpretation in benign_matches[:3]:  # Limit to top 3
                benign_note += f"- {interpretation}\n"
            benign_note += (
                "\nThese signals are expected, non-critical infrastructure patterns "
                "and do not indicate a candidate or exam platform issue."
            )

            # Append to summary if not already mentioned
            if "benign" not in output.summary.lower():
                output.summary = output.summary.rstrip() + benign_note

            # Add warning to track benign classification
            if not output.warnings:
                output.warnings = []
            output.warnings.append(
                f"Benign signal classification applied: {len(benign_matches)} known patterns detected."
            )
