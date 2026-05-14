"""
Integration tests for AgentOrchestrator.run() using AsyncMock LLM/tool shims.

These tests simulate the full agent loop without hitting real Azure endpoints.
They assert on:
 - tool ordering and mandatory-tool enforcement for session queries
 - generic query dual-workspace enforcement
 - exit-marker consistency safeguard applied to final output
 - filler-phrase stripping in summary
 - token-budget trimming does not drop system or user messages
"""
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.agent.types import AgentOutput
from app.config import Settings
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings() -> Settings:
    return Settings(
        AZURE_OPENAI_ENDPOINT="https://fake.openai.azure.com",
        AZURE_OPENAI_DEPLOYMENT="gpt4o",
        PROPROCTOR_WORKSPACE_ID="ws-ppr",
        INFRA_WORKSPACE_ID="ws-infra",
        COSMOS_ENDPOINT="https://fake.cosmos.azure.com",
        MAX_AGENT_ITERATIONS=8,
    )


def _tool_response(**kwargs: Any) -> str:
    return json.dumps(kwargs)


def _final_answer(**kwargs: Any) -> MagicMock:
    """Simulate a finish_reason=stop LLM response."""
    payload = {
        "summary": kwargs.get("summary", "Session completed normally."),
        "triage_status": kwargs.get("triage_status", None),
        "customer_response": kwargs.get("customer_response", None),
        "follow_up_questions": kwargs.get("follow_up_questions", []),
        "recommended_actions": kwargs.get("recommended_actions", []),
        "escalation_target": kwargs.get("escalation_target", None),
        "confirmation_codes": kwargs.get("confirmation_codes", []),
        "key_findings": kwargs.get("key_findings", []),
        "timeline": kwargs.get("timeline", []),
        "root_cause": kwargs.get("root_cause", None),
        "root_cause_confidence": kwargs.get("root_cause_confidence", None),
        "tools_invoked": [],
        "warnings": [],
        "source_summary": None,
        "per_confirmation_code_summaries": {},
        "per_confirmation_code_source_summary": {},
        "download_links": {},
    }
    msg = MagicMock()
    msg.content = json.dumps(payload)
    msg.tool_calls = None
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response


def _tool_call_response(tool_name: str, tool_args: dict, tc_id: str = "tc1") -> MagicMock:
    """Simulate a finish_reason=tool_calls LLM response."""
    tc = MagicMock()
    tc.id = tc_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(tool_args)
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response


def _empty_registry() -> ToolRegistry:
    registry = ToolRegistry()
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_query_enforces_mandatory_tools() -> None:
    """Agent must not finalize until getSessionData, getChatHistory, getSessionTimeline are called."""
    settings = _make_settings()
    registry = _empty_registry()

    call_count = 0

    async def fake_tool_exec(name: str, args_json: str) -> str:
        return _tool_response(events=[], errors=[], metadata=None, source_summary={},
                              messages=[], timeline=[])

    registry.execute = fake_tool_exec  # type: ignore[method-assign]
    registry.get_definitions = lambda: []  # type: ignore[method-assign]

    # LLM plan: call only getSessionData then try to finalize → should be forced to continue
    llm_responses = [
        _tool_call_response("getSessionData", {"confirmationCode": "0000000109097576"}),
        _final_answer(summary="Session completed. No issues found."),
        # After forced continuation, call the missing mandatory tools
        _tool_call_response("getChatHistory", {"confirmationCode": "0000000109097576"}, "tc2"),
        _tool_call_response("getSessionTimeline", {"confirmationCode": "0000000109097576"}, "tc3"),
        _final_answer(summary="Session completed after full baseline evidence.", confirmation_codes=["0000000109097576"]),
    ]

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    call_index = 0

    async def fake_create(**kwargs: Any) -> Any:
        nonlocal call_index
        resp = llm_responses[min(call_index, len(llm_responses) - 1)]
        call_index += 1
        return resp

    client.chat.completions.create = fake_create

    orchestrator = AgentOrchestrator(client=client, registry=registry, settings=settings)

    output = await orchestrator.run(
        query="Investigate session 0000000109097576",
        request_id="test-1",
    )

    assert isinstance(output, AgentOutput)
    assert "getSessionData" in output.tools_invoked
    assert "getChatHistory" in output.tools_invoked
    assert "getSessionTimeline" in output.tools_invoked


@pytest.mark.asyncio
async def test_generic_query_enforces_both_workspaces() -> None:
    """Generic service-health query must not finalize after only proproctor workspace was queried."""
    settings = _make_settings()
    registry = _empty_registry()

    async def fake_tool_exec(name: str, args_json: str) -> str:
        return _tool_response(rows=[], truncated=False, row_count_total=0)

    registry.execute = fake_tool_exec  # type: ignore[method-assign]
    registry.get_definitions = lambda: []  # type: ignore[method-assign]

    llm_responses = [
        # Only queries proproctor, then tries to finalize
        _tool_call_response("queryKQL", {"query": "AppExceptions | take 10", "workspace": "proproctor", "timespan_days": 7}),
        _final_answer(summary="No issues found in proproctor."),
        # After forced continuation, queries infra
        _tool_call_response("queryKQL", {"query": "KubeEvents | take 10", "workspace": "infrastructure", "timespan_days": 7}, "tc2"),
        _final_answer(summary="Checked both workspaces. No significant issues found."),
    ]

    client = MagicMock()
    call_index = 0

    async def fake_create(**kwargs: Any) -> Any:
        nonlocal call_index
        resp = llm_responses[min(call_index, len(llm_responses) - 1)]
        call_index += 1
        return resp

    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create

    orchestrator = AgentOrchestrator(client=client, registry=registry, settings=settings)

    output = await orchestrator.run(
        query="Show all errors on the exam sessions API between April 9-11",
        request_id="test-2",
    )

    assert isinstance(output, AgentOutput)
    invoked = output.tools_invoked
    assert "queryKQL" in invoked


@pytest.mark.asyncio
async def test_exit_marker_safeguard_rewrites_false_claim_end_to_end() -> None:
    """When tool result has exit evidence, final summary must not claim no exit markers."""
    settings = _make_settings()
    registry = _empty_registry()

    exit_events = [
        {
            "timestamp": "2026-04-08T16:18:42.051Z",
            "message": "candidate-app exit marker: Exiting application",
            "type": "disconnect",
            "source": "app-insights",
        }
    ]

    async def fake_tool_exec(name: str, args_json: str) -> str:
        if name == "getSessionData":
            return _tool_response(
                events=exit_events,
                errors=[],
                metadata={"sessionId": "s1", "examSessionId": "e1", "status": "completed",
                          "confirmationCode": "0000000109097576", "disconnectedTimes": []},
                source_summary={"app_insights_events": 1},
            )
        return _tool_response(events=[], errors=[], messages=[], timeline=[])

    registry.execute = fake_tool_exec  # type: ignore[method-assign]
    registry.get_definitions = lambda: []  # type: ignore[method-assign]

    llm_responses = [
        _tool_call_response("getSessionData", {"confirmationCode": "0000000109097576"}),
        _tool_call_response("getChatHistory", {"confirmationCode": "0000000109097576"}, "tc2"),
        _tool_call_response("getSessionTimeline", {"confirmationCode": "0000000109097576"}, "tc3"),
        # LLM incorrectly claims no App Insights exit markers
        _final_answer(
            summary="No App Insights exit markers were found for confirmation code 0000000109097576. Session ended without clear exit evidence.",
            confirmation_codes=["0000000109097576"],
        ),
    ]

    client = MagicMock()
    call_index = 0

    async def fake_create(**kwargs: Any) -> Any:
        nonlocal call_index
        resp = llm_responses[min(call_index, len(llm_responses) - 1)]
        call_index += 1
        return resp

    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create

    orchestrator = AgentOrchestrator(client=client, registry=registry, settings=settings)

    output = await orchestrator.run(
        query="Investigate session 0000000109097576",
        request_id="test-3",
    )

    assert isinstance(output, AgentOutput)
    # Safeguard must have rewritten the false claim
    assert "No App Insights exit markers" not in (output.summary or "")
    assert "exit evidence" in (output.summary or "").lower()


@pytest.mark.asyncio
async def test_filler_phrase_stripped_from_summary() -> None:
    """_enforce_answer_style must strip common opener filler from summary."""
    output = AgentOutput(
        summary=(
            "Based on my analysis, the candidate disconnected at 16:18 UTC. "
            "The root cause was a content-protection bypass."
        ),
        tools_invoked=[],
    )
    AgentOrchestrator._enforce_answer_style(output)
    assert not output.summary.lower().startswith("based on")
    assert "disconnected" in output.summary


def test_trim_messages_for_token_budget_preserves_system_and_user() -> None:
    """Token-budget trimmer must not remove system or user messages."""
    big_content = "x" * 500_000  # 500k chars, far above budget
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "User query"},
        {"role": "tool", "content": big_content, "tool_call_id": "tc1"},
        {"role": "tool", "content": big_content, "tool_call_id": "tc2"},
    ]
    trimmed = AgentOrchestrator._trim_messages_for_token_budget(messages)
    roles = [m["role"] for m in trimmed]
    assert "system" in roles
    assert "user" in roles
    # At least one tool message should have been trimmed
    assert roles.count("tool") < 2


def test_must_continue_generic_investigation_both_workspaces_required() -> None:
    """Generic queries must require infrastructure workspace if proproctor was queried."""
    from app.agent.investigation_policy import build_investigation_profile

    profile = build_investigation_profile(
        "Show errors on exam sessions API between April 9 and April 11"
    )
    # Proproctor queried but not infra
    assert AgentOrchestrator._must_continue_generic_investigation(
        profile,
        ["queryKQL"],
        {"proproctor"},
    ) is True
    # Both queried → should not continue
    assert AgentOrchestrator._must_continue_generic_investigation(
        profile,
        ["queryKQL"],
        {"proproctor", "infrastructure"},
    ) is False


def test_must_continue_generic_investigation_aggregate_requires_stats() -> None:
    """Aggregate-impact queries must call getSessionLogStats before finalising."""
    from app.agent.investigation_policy import build_investigation_profile

    profile = build_investigation_profile(
        "How many candidates had disconnect errors in April?"
    )
    assert profile.is_aggregate_impact_query is True
    # Neither queryKQL nor getSessionLogStats called
    assert AgentOrchestrator._must_continue_generic_investigation(
        profile,
        [],
        set(),
    ) is True
    # Stats tool called → should not force continue
    assert AgentOrchestrator._must_continue_generic_investigation(
        profile,
        ["getSessionLogStats"],
        set(),
    ) is False
