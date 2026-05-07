import json

from app.agent.conversation_state import build_conversation_state
from app.agent.domain_knowledge import get_issue_aliases, get_service_aliases
from app.agent.investigation_policy import build_investigation_profile
from app.agent.orchestrator import AgentOrchestrator


def test_session_queries_require_baseline_tools_before_finalize() -> None:
    profile = build_investigation_profile("Investigate 0000000109097576")

    assert AgentOrchestrator._must_continue_investigation(profile, ["getSessionData"]) is True
    assert (
        AgentOrchestrator._must_continue_investigation(
            profile,
            ["getSessionData", "getChatHistory", "getSessionTimeline"],
        )
        is False
    )


def test_missing_tool_message_lists_expected_tools() -> None:
    profile = build_investigation_profile("Investigate 0000000109097576")

    message = AgentOrchestrator._build_missing_tool_message(profile, ["getSessionData"])

    assert "getChatHistory" in message
    assert "getSessionTimeline" in message
    assert "adjacent services" in message


def test_prior_turn_tools_count_as_progress() -> None:
    profile = build_investigation_profile("Continue investigating 0000000109097576")
    state = build_conversation_state(
        [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "summary": "Checked the baseline session sources.",
                        "tools_invoked": [
                            "getSessionData",
                            "getChatHistory",
                            "getSessionTimeline",
                        ],
                    }
                ),
            }
        ]
    )

    assert (
        AgentOrchestrator._must_continue_investigation(
            profile,
            list(state.tools_already_used),
        )
        is False
    )


def test_externalized_rules_are_available() -> None:
    service_aliases = get_service_aliases()
    issue_aliases = get_issue_aliases()

    assert "candidate-app" in service_aliases
    assert "prometric app" in service_aliases["candidate-app"]
    assert "disconnect" in issue_aliases


def test_exit_evidence_extraction_from_session_data_result() -> None:
    tool_result = json.dumps(
        {
            "events": [
                {
                    "timestamp": "2026-04-08T16:18:42.051Z",
                    "message": "candidate-app exit marker: Exiting application",
                    "type": "disconnect",
                    "source": "app-insights",
                }
            ]
        }
    )

    evidence = AgentOrchestrator._extract_app_insights_exit_evidence(
        "getSessionData",
        tool_result,
    )

    assert evidence
    assert any("exiting application" in item.lower() for item in evidence)


def test_exit_safeguard_rewrites_false_no_exit_claim() -> None:
    rewritten = AgentOrchestrator._rewrite_no_exit_claim_with_evidence(
        "No App Insights exit markers were found for confirmation code 0000000109097576.",
        ["2026-04-08T16:18:42.051Z candidate-app exit marker: Exiting application"],
    )

    assert rewritten is not None
    assert "App Insights exit evidence detected" in rewritten
    assert "No App Insights exit markers" not in rewritten
