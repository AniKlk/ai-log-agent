"""
Tests for schema knowledge module.
"""

import pytest
from app.agent.schema_knowledge import (
    get_container_info,
    get_all_valid_containers,
    get_query_hint,
    schema_summary_for_prompt,
    field_info_for_container,
    EXAM_SESSION_CONTAINER,
    SESSION_LOG_CONTAINER,
)


class TestSchemaKnowledge:
    """Test schema knowledge functions and data."""

    def test_exam_session_container_exists(self):
        """Verify exam-session container is properly defined."""
        container = get_container_info("ExamSession", "exam-session")
        assert container is not None
        assert container.database == "ExamSession"
        assert container.container == "exam-session"
        assert "ConfirmationCode" in container.fields
        assert "TestStatus" in container.fields
        assert "Status" in container.fields

    def test_session_log_container_exists(self):
        """Verify session-log container is properly defined."""
        container = get_container_info("ExamSession", "session-log")
        assert container is not None
        assert container.database == "ExamSession"
        assert container.container == "session-log"
        assert "Entries" in container.fields
        assert "ExamSessionId" in container.fields

    def test_chat_container_exists(self):
        """Verify chat container is properly defined."""
        container = get_container_info("ExamChat", "exam-chat")
        assert container is not None
        assert "ExamSessionId" in container.fields
        assert "Message" in container.fields

    def test_conference_container_exists(self):
        """Verify conference container is properly defined."""
        container = get_container_info("PPR.Conferences", "conference")
        assert container is not None
        assert "RoomName" in container.fields

    def test_assignment_container_exists(self):
        """Verify assignment container is properly defined."""
        container = get_container_info("Assignment", "assignment")
        assert container is not None
        assert "ExamSessionId" in container.fields

    def test_invalid_container_returns_none(self):
        """Invalid database/container combo should return None."""
        container = get_container_info("InvalidDB", "invalid-container")
        assert container is None

    def test_all_valid_containers_returns_list(self):
        """get_all_valid_containers should return list of all containers."""
        containers = get_all_valid_containers()
        assert len(containers) >= 5  # At least 5 containers
        database_container_pairs = [
            (c.database, c.container) for c in containers
        ]
        assert ("ExamSession", "exam-session") in database_container_pairs
        assert ("ExamSession", "session-log") in database_container_pairs
        assert ("ExamChat", "exam-chat") in database_container_pairs

    def test_query_hint_exists(self):
        """Query hints should be retrievable."""
        hint = get_query_hint("find_session_by_code")
        assert hint is not None
        assert "query" in hint
        assert "SELECT" in hint["query"]

    def test_schema_summary_generates_text(self):
        """Schema summary should generate readable guidance."""
        summary = schema_summary_for_prompt()
        assert summary
        assert "ExamSession" in summary
        assert "exam-session" in summary
        assert "session-log" in summary

    def test_field_info_for_exam_session(self):
        """Field info should describe exam-session fields."""
        info = field_info_for_container("ExamSession", "exam-session")
        assert info is not None
        assert "ConfirmationCode" in info
        assert "TestStatus" in info
        assert "Status" in info

    def test_field_info_for_invalid_container(self):
        """Field info for invalid container should return None."""
        info = field_info_for_container("Invalid", "invalid")
        assert info is None

    def test_exam_session_fields_have_descriptions(self):
        """All fields should have meaningful descriptions."""
        container = EXAM_SESSION_CONTAINER
        for field_name, field in container.fields.items():
            assert field.description
            assert field.type_hint
            assert len(field.description) > 10  # Meaningful description

    def test_session_log_type_field(self):
        """SessionLogType field should have proper documentation."""
        container = SESSION_LOG_CONTAINER
        assert "Entries[*].SessionLogType" in container.fields
        field = container.fields["Entries[*].SessionLogType"]
        assert "0=" in field.description  # Should explain codes
        assert "7=" in field.description

    def test_container_key_fields(self):
        """Key fields should be identified for each container."""
        container = get_container_info("ExamSession", "exam-session")
        assert len(container.key_fields) > 0
        assert "ConfirmationCode" in container.key_fields or "Id" in container.key_fields

    def test_query_patterns_available(self):
        """Containers should have query patterns."""
        container = get_container_info("ExamSession", "exam-session")
        assert len(container.query_patterns) > 0
        for pattern in container.query_patterns:
            assert "SELECT" in pattern.upper()

    def test_common_filters(self):
        """Containers should have common filter examples."""
        container = get_container_info("ExamSession", "exam-session")
        if container.common_filters:
            assert isinstance(container.common_filters, dict)


class TestSchemaGuidance:
    """Test that schema guidance is correctly formatted."""

    def test_schema_summary_includes_all_containers(self):
        """Schema summary should mention all major containers."""
        summary = schema_summary_for_prompt()
        assert "exam-session" in summary
        assert "session-log" in summary
        assert "exam-chat" in summary
        assert "conference" in summary
        assert "assignment" in summary

    def test_field_info_formatting(self):
        """Field info should be properly formatted."""
        info = field_info_for_container("ExamSession", "exam-session")
        assert "## Fields" in info
        assert "- **" in info  # Markdown list
        assert "(" in info  # Type in parentheses

    def test_query_hints_cover_common_scenarios(self):
        """Should have hints for common investigation scenarios."""
        hints = [
            "find_session_by_code",
            "find_disconnects",
            "find_test_completed",
            "check_applied_blocks",
        ]
        for hint_name in hints:
            hint = get_query_hint(hint_name)
            assert hint is not None, f"Missing hint: {hint_name}"
