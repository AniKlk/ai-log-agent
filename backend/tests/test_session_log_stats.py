from app.tools.session_log_stats import _normalize_disconnect_keywords


def test_auto_scope_defaults_broad_disconnect_phrase_to_candidate() -> None:
    assert _normalize_disconnect_keywords(
        ["disconnect issues"],
        "auto",
    ) == ["Candidate disconnected"]


def test_auto_scope_preserves_explicit_role_keyword() -> None:
    assert _normalize_disconnect_keywords(
        ["Proctor disconnected"],
        "auto",
    ) == ["Proctor disconnected"]


def test_all_scope_keeps_all_roles_for_broad_disconnect_query() -> None:
    assert _normalize_disconnect_keywords(
        ["disconnected multiple times"],
        "all",
    ) == ["disconnected"]


def test_readiness_scope_overrides_broad_disconnect_query() -> None:
    assert _normalize_disconnect_keywords(
        ["disconnect"],
        "readiness",
    ) == ["Readiness agent disconnected"]
