"""
Benign signal detection and context injection.

This module loads a catalog of known benign patterns from ProProctor services
and provides matching logic to automatically classify findings as benign,
reducing false-positive escalations to candidates.
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List, Set


@dataclass
class BenignMatch:
    """Result of matching a finding against a benign signal category."""

    category_id: str
    category_name: str
    matched_indicators: List[str]
    should_escalate_to_candidate: bool
    should_escalate_to_backend: bool
    requires_schema_context: bool
    interpretation: str


class BenignSignalCatalog:
    """Loads and matches findings against known benign patterns."""

    def __init__(self):
        """Load catalog from JSON file."""
        catalog_path = Path(__file__).parent / "benign_signals.json"
        with open(catalog_path, "r") as f:
            data = json.load(f)
        
        self.categories = data.get("benign_signal_categories", [])
        self.cross_cutting = data.get("cross_cutting_patterns", [])
        self.decision_tree = data.get("decision_tree", {})
        self._build_indicator_index()

    def _build_indicator_index(self) -> None:
        """Build lowercase indicator index for fast matching."""
        self.indicator_to_categories: Dict[str, List[str]] = {}
        for cat in self.categories:
            cat_id = cat.get("id")
            for indicator in cat.get("indicators", []):
                key = indicator.lower()
                if key not in self.indicator_to_categories:
                    self.indicator_to_categories[key] = []
                self.indicator_to_categories[key].append(cat_id)

    def match_finding(self, finding_text: str) -> Optional[BenignMatch]:
        """
        Match a finding against benign signal categories.

        Args:
            finding_text: Description of the finding to check

        Returns:
            BenignMatch if matched, else None
        """
        if not finding_text:
            return None

        finding_lower = finding_text.lower()
        matched_ids: Set[str] = set()
        matched_indicators: List[str] = []

        # Direct indicator matching
        for indicator, cat_ids in self.indicator_to_categories.items():
            if indicator in finding_lower:
                matched_ids.update(cat_ids)
                matched_indicators.append(indicator)

        # Regex pattern matching for common patterns
        patterns = [
            (r"twilio.*error", ["twilio_intermittent"]),
            (r"event.*processing.*error", ["event_processing_background"]),
            (r"exception.*to.*response.*mapper", ["exception_mapper_design"]),
            (r"console\.writeline|console output", ["metrics_console_logging"]),
            (r"catch\s*\(\s*exception", ["generic_catch_in_handlers"]),
            (r"application\s*block", ["application_block_not_failure"]),
            (r"ginger.*web|gw.*decision|not\s+terminate", ["gingerwebs_not_terminate"]),
        ]

        for pattern, cat_ids in patterns:
            if re.search(pattern, finding_lower):
                matched_ids.update(cat_ids)

        if not matched_ids:
            return None

        # Return first match with highest confidence
        for cat_id in matched_ids:
            cat = self._get_category(cat_id)
            if cat:
                return BenignMatch(
                    category_id=cat_id,
                    category_name=cat.get("name"),
                    matched_indicators=matched_indicators,
                    should_escalate_to_candidate=cat.get(
                        "should_escalate_to_candidate", False
                    ),
                    should_escalate_to_backend=cat.get(
                        "should_escalate_to_backend", False
                    ),
                    requires_schema_context=cat.get(
                        "requires_schema_context", False
                    ),
                    interpretation=cat.get("interpretation", ""),
                )

        return None

    def _get_category(self, cat_id: str) -> Optional[Dict]:
        """Get category by ID."""
        for cat in self.categories:
            if cat.get("id") == cat_id:
                return cat
        return None

    def get_context_injection(self, match: BenignMatch) -> str:
        """
        Generate context injection for LLM when a benign signal is detected.

        Args:
            match: BenignMatch instance

        Returns:
            System message fragment to inject into LLM context
        """
        if not match:
            return ""

        return f"""
## Benign Signal Classification

**Category:** {match.category_name}
**Interpretation:** {match.interpretation}
**Matched Indicators:** {', '.join(match.matched_indicators[:3])}

**Conclusion:** This finding matches a known benign signal pattern across ProProctor services.
- Escalate to candidate: {match.should_escalate_to_candidate}
- Escalate to backend/infrastructure team: {match.should_escalate_to_backend}
{f"- Note: Requires schema context (deny-list/allowlist rules) for full interpretation" if match.requires_schema_context else ""}

**Action:** Include this classification in your final summary.
Use natural language to explain why this signal is benign, e.g.:
"The {match.category_name.lower()} is expected and benign because {match.interpretation.lower()}"
""".strip()


# Global singleton
_catalog_instance: Optional[BenignSignalCatalog] = None


def get_catalog() -> BenignSignalCatalog:
    """Get or create the global benign signals catalog."""
    global _catalog_instance
    if _catalog_instance is None:
        _catalog_instance = BenignSignalCatalog()
    return _catalog_instance
