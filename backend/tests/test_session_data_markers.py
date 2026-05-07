from app.tools.models import LogEvent
from app.tools.session_data import GetSessionDataTool


def test_security_marker_counts_as_exit_evidence() -> None:
    events = [
        LogEvent(
            timestamp="2026-05-05T16:30:00Z",
            message="candidate-app security marker: Content protection bypassed",
            type="disconnect",
            source="app-insights",
        )
    ]

    markers = GetSessionDataTool._find_marker_timestamps(events, "exit")

    assert markers == [("2026-05-05T16:30:00Z", markers[0][1])]


def test_lockdown_exit_window_counts_as_exit_evidence() -> None:
    events = [
        LogEvent(
            timestamp="2026-05-05T16:31:00Z",
            message="candidate-app exit marker: Exit lockdown window",
            type="disconnect",
            source="app-insights",
        )
    ]

    markers = GetSessionDataTool._find_marker_timestamps(events, "exit")

    assert len(markers) == 1
    assert markers[0][0] == "2026-05-05T16:31:00Z"


def test_candidate_exited_phrase_counts_as_exit_evidence() -> None:
    events = [
        LogEvent(
            timestamp="2026-05-05T16:32:00Z",
            message="candidate-app exit marker: Candidate exited from application",
            type="disconnect",
            source="app-insights",
        )
    ]

    markers = GetSessionDataTool._find_marker_timestamps(events, "exit")

    assert len(markers) == 1
    assert markers[0][0] == "2026-05-05T16:32:00Z"
