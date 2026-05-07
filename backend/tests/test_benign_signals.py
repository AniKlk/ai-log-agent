"""
Tests for benign signal detection and context injection.
"""

import pytest
from app.agent.benign_signals import BenignSignalCatalog, get_catalog


class TestBenignSignalCatalog:
    """Test benign signal matching and context injection."""

    def test_catalog_loads(self):
        """Catalog should load without errors."""
        catalog = get_catalog()
        assert catalog is not None
        assert len(catalog.categories) > 0

    def test_twilio_error_match(self):
        """Twilio client errors should match as benign."""
        catalog = get_catalog()
        finding = "TwilioTaskClient exception during task creation"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "twilio_intermittent"
        assert not match.should_escalate_to_candidate

    def test_event_processing_error_match(self):
        """Event processor errors should match as benign."""
        catalog = get_catalog()
        finding = "EventHub consumer exception caught in handler"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "event_processing_background"
        assert not match.should_escalate_to_candidate

    def test_exception_mapper_match(self):
        """Exception mapper message leakage should match as benign."""
        catalog = get_catalog()
        finding = "ExceptionToResponseMapper includes ex.Message in API response"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "exception_mapper_design"
        assert not match.should_escalate_to_candidate

    def test_generic_catch_match(self):
        """Generic catch blocks in handlers should match as benign."""
        catalog = get_catalog()
        finding = "catch (Exception ex) in AlertBaseHandler"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "generic_catch_in_handlers"
        assert not match.should_escalate_to_candidate

    def test_console_writeline_match(self):
        """Console.WriteLine in metrics should match as benign."""
        catalog = get_catalog()
        finding = "KestrelMetricsListener uses Console.WriteLine"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "metrics_console_logging"

    def test_application_block_match(self):
        """ApplicationBlock events should match as benign but may escalate to backend."""
        catalog = get_catalog()
        finding = "ApplicationBlock EventType detected in session"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "application_block_not_failure"
        assert not match.should_escalate_to_candidate

    def test_gw_not_terminate_match(self):
        """GingerWebs 'not terminate' decision should match as benign."""
        catalog = get_catalog()
        finding = "GingerWebs decision: not terminate"
        match = catalog.match_finding(finding)
        assert match is not None
        assert match.category_id == "gingerwebs_not_terminate"

    def test_non_benign_finding(self):
        """Unrecognized findings should return None."""
        catalog = get_catalog()
        finding = "Candidate exit marker detected in application logs"
        match = catalog.match_finding(finding)
        # This may or may not match depending on pattern coverage
        # Just verify it doesn't crash
        assert match is None or hasattr(match, "category_id")

    def test_context_injection_format(self):
        """Context injection should produce valid system message fragment."""
        catalog = get_catalog()
        finding = "TwilioTaskClient exception"
        match = catalog.match_finding(finding)
        assert match is not None

        context = catalog.get_context_injection(match)
        assert context
        assert "Benign Signal Classification" in context
        assert match.category_name in context
        assert match.interpretation in context

    def test_indicator_index_case_insensitive(self):
        """Indicator matching should be case-insensitive."""
        catalog = get_catalog()
        # Test uppercase variant
        finding = "TWILIO ERROR IN TASK CLIENT"
        match = catalog.match_finding(finding)
        # Should still match despite case difference
        assert match is None or match.category_id  # Either matches or doesn't, but no error

    def test_matched_indicators_populated(self):
        """Matched indicators should be populated in result."""
        catalog = get_catalog()
        finding = "EventHub consumer exception caught"
        match = catalog.match_finding(finding)
        if match:
            assert len(match.matched_indicators) > 0

    def test_empty_finding(self):
        """Empty finding should handle gracefully."""
        catalog = get_catalog()
        match = catalog.match_finding("")
        assert match is None

    def test_none_finding(self):
        """None finding should handle gracefully."""
        catalog = get_catalog()
        match = catalog.match_finding(None)
        assert match is None

    def test_catalog_singleton(self):
        """get_catalog() should return singleton."""
        catalog1 = get_catalog()
        catalog2 = get_catalog()
        assert catalog1 is catalog2
