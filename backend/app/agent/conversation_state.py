import json
import re
from dataclasses import dataclass, field

from app.agent.domain_knowledge import canonicalize_issue_types, canonicalize_services

_CONFIRMATION_CODE_REGEX = re.compile(r"\b\d{16}\b")


@dataclass(slots=True)
class ConversationState:
    confirmation_codes: list[str] = field(default_factory=list)
    services_checked: list[str] = field(default_factory=list)
    tools_already_used: list[str] = field(default_factory=list)
    prior_root_causes: list[str] = field(default_factory=list)
    prior_warnings: list[str] = field(default_factory=list)
    prior_findings: list[str] = field(default_factory=list)
    prior_issue_types: list[str] = field(default_factory=list)
    empty_or_inconclusive_areas: list[str] = field(default_factory=list)


def _append_unique(target: list[str], values: list[str] | tuple[str, ...]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def build_conversation_state(conversation_history: list[dict] | None = None) -> ConversationState:
    state = ConversationState()
    for message in conversation_history or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))

        _append_unique(
            state.confirmation_codes,
            list(dict.fromkeys(_CONFIRMATION_CODE_REGEX.findall(content))),
        )
        _append_unique(state.prior_issue_types, canonicalize_issue_types(content))
        _append_unique(state.services_checked, canonicalize_services(content))

        if role != "assistant":
            continue

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            if "no rows" in content.lower() or "insufficient" in content.lower():
                _append_unique(state.empty_or_inconclusive_areas, [content[:160]])
            continue

        if not isinstance(payload, dict):
            continue

        tools_invoked = payload.get("tools_invoked") or []
        if isinstance(tools_invoked, list):
            _append_unique(state.tools_already_used, [str(tool) for tool in tools_invoked])

        root_cause = payload.get("root_cause")
        if isinstance(root_cause, str) and root_cause.strip():
            _append_unique(state.prior_root_causes, [root_cause.strip()])

        warnings = payload.get("warnings") or []
        if isinstance(warnings, list):
            _append_unique(
                state.prior_warnings,
                [str(warning).strip() for warning in warnings if str(warning).strip()],
            )

        findings = payload.get("key_findings") or []
        if isinstance(findings, list):
            finding_descriptions = []
            for finding in findings:
                if isinstance(finding, dict):
                    description = str(finding.get("description", "")).strip()
                    if description:
                        finding_descriptions.append(description)
            _append_unique(state.prior_findings, finding_descriptions)

        summary = str(payload.get("summary", "")).strip()
        if summary and any(
            token in summary.lower()
            for token in ("insufficient", "no evidence", "no rows", "partial")
        ):
            _append_unique(state.empty_or_inconclusive_areas, [summary])

    return state


def build_state_system_message(state: ConversationState) -> str | None:
    if not any(
        (
            state.confirmation_codes,
            state.services_checked,
            state.tools_already_used,
            state.prior_root_causes,
            state.prior_warnings,
            state.prior_findings,
            state.empty_or_inconclusive_areas,
        )
    ):
        return None

    lines = ["Recovered investigation state from prior turns:"]
    if state.confirmation_codes:
        lines.append(
            "- Previously discussed confirmation codes: "
            f"{', '.join(state.confirmation_codes)}"
        )
    if state.tools_already_used:
        lines.append(f"- Tools already used: {', '.join(state.tools_already_used)}")
    if state.services_checked:
        lines.append(
            "- Services already inspected or discussed: "
            f"{', '.join(state.services_checked)}"
        )
    if state.prior_issue_types:
        lines.append(f"- Prior issue themes: {', '.join(state.prior_issue_types)}")
    if state.prior_root_causes:
        lines.append(f"- Earlier root-cause hypotheses: {' | '.join(state.prior_root_causes[:3])}")
    if state.prior_findings:
        lines.append(f"- Earlier findings to preserve: {' | '.join(state.prior_findings[:3])}")
    if state.prior_warnings:
        lines.append(f"- Earlier warnings/data gaps: {' | '.join(state.prior_warnings[:3])}")
    if state.empty_or_inconclusive_areas:
        lines.append(
            "- Areas that were previously empty or inconclusive: "
            f"{' | '.join(state.empty_or_inconclusive_areas[:3])}"
        )
    lines.append(
        "- Reuse prior progress. Do not restart blindly; continue from the latest gaps, "
        "widen searches where prior attempts were empty, and preserve valid earlier findings."
    )
    return "\n".join(lines)
