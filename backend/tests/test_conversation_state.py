import json

from app.agent.conversation_state import build_conversation_state, build_state_system_message


def test_build_conversation_state_recovers_prior_progress() -> None:
    history = [
        {"role": "user", "content": "Investigate 0000000109097576"},
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "summary": "No evidence yet from candidate app. Data is partial.",
                    "root_cause": "Probable backend timeout in exam sessions api.",
                    "tools_invoked": ["getSessionData", "queryKQL"],
                    "key_findings": [
                        {
                            "description": "exam sessions api timed out",
                            "severity": "warning",
                            "evidence": [],
                        }
                    ],
                    "warnings": ["Insufficient App Insights evidence in first pass."],
                }
            ),
        },
    ]

    state = build_conversation_state(history)

    assert state.confirmation_codes == ["0000000109097576"]
    assert "getSessionData" in state.tools_already_used
    assert "queryKQL" in state.tools_already_used
    assert "exam-sessions-api" in state.services_checked
    assert state.prior_root_causes
    assert state.empty_or_inconclusive_areas


def test_build_state_system_message_includes_prior_gaps() -> None:
    history = [
        {
            "role": "assistant",
            "content": "No rows came back from candidate app and evidence is insufficient.",
        }
    ]

    state = build_conversation_state(history)
    message = build_state_system_message(state)

    assert message is not None
    assert "previously empty or inconclusive" in message
    assert "continue from the latest gaps" in message
