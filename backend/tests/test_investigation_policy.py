import json

from app.agent.investigation_policy import (
    analyze_tool_result,
    build_adaptive_system_message,
    build_investigation_profile,
    build_investigation_system_message,
)


def test_build_investigation_profile_detects_session_and_related_services() -> None:
    profile = build_investigation_profile(
        "Prometric candidate got disconnected on 0000000109097576 and then relogin failed"
    )

    assert profile.is_session_query is True
    assert profile.confirmation_codes == ["0000000109097576"]
    assert "disconnect" in profile.issue_types
    assert "login" in profile.issue_types
    assert "candidate-app" in profile.related_services
    assert "exam-sessions-api" in profile.related_services


def test_analyze_tool_result_recommends_wider_kql_search() -> None:
    profile = build_investigation_profile(
        "look for candidate app issues in the last few hours"
    )

    observation = analyze_tool_result(
        "queryKQL",
        json.dumps({"workspace": "proproctor", "timespan_days": 7}),
        json.dumps({"rows": [], "truncated": False}),
        profile,
    )

    assert observation is not None
    assert observation.status == "empty"
    assert "Expand the time range" in " ".join(observation.hints)
    assert "infrastructure workspace" in " ".join(observation.hints)


def test_adaptive_message_demands_blast_radius_when_issue_found() -> None:
    profile = build_investigation_profile(
        "exam sessions api timeout issue, how many candidates were affected?"
    )
    observation = analyze_tool_result(
        "queryKQL",
        json.dumps({"workspace": "proproctor", "timespan_days": 30}),
        json.dumps(
            {
                "rows": [{"_ResourceId": "app-proproctor-exam-sessions-api"}],
                "truncated": False,
            }
        ),
        profile,
    )

    assert observation is not None
    message = build_adaptive_system_message(profile, [observation], ["queryKQL"])

    assert message is not None
    assert "measure affected sessions" in message
    assert "candidate impact" in message


def test_system_message_includes_sessionlog_schema_guardrails_for_term_queries() -> None:
    profile = build_investigation_profile(
        "Investigate SessionLogType 11 application block and GW not terminate events"
    )

    message = build_investigation_system_message(profile)

    assert "SessionLogType values" in message
    assert "fatality 'not terminate'" in message


def test_query_cosmos_rows_for_teststatus_completed_returns_signal_observation() -> None:
    profile = build_investigation_profile("show me teststatus completed sessions")

    observation = analyze_tool_result(
        "queryCosmos",
        json.dumps({"container": "exam-session"}),
        json.dumps(
            {
                "rows": [
                    {
                        "ConfirmationCode": "0000000109097576",
                        "TestStatus": "Completed",
                    }
                ]
            }
        ),
        profile,
    )

    assert observation is not None
    assert observation.status == "signal"
    assert "TestStatus completed rows" in observation.summary
