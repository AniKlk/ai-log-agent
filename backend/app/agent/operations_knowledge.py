import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_KNOWLEDGE_PATH = Path(__file__).with_name("operations_knowledge.json")


@lru_cache(maxsize=1)
def load_operations_knowledge() -> dict:
    with _KNOWLEDGE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_service_catalog() -> list[dict]:
    return list(load_operations_knowledge().get("service_catalog", []))


def get_event_taxonomy() -> list[dict]:
    return list(load_operations_knowledge().get("event_taxonomy", []))


def get_playbooks() -> list[dict]:
    return list(load_operations_knowledge().get("troubleshooting_playbooks", []))


def get_known_false_positives() -> list[dict]:
    return list(load_operations_knowledge().get("known_false_positives", []))


def get_architecture_relationships() -> list[dict]:
    return list(load_operations_knowledge().get("architecture_relationships", []))


def get_ingestion_templates() -> dict:
    return dict(load_operations_knowledge().get("ingestion_templates", {}))


def get_standard_troubleshooting_process() -> list[dict]:
    return list(load_operations_knowledge().get("standard_troubleshooting_process", []))


def get_deterministic_issue_workflows() -> list[dict]:
    return list(load_operations_knowledge().get("deterministic_issue_workflows", []))


def get_aat_escalation_sop() -> dict:
    return dict(load_operations_knowledge().get("aat_escalation_sop", {}))


def get_kb_articles() -> list[dict]:
    return list(load_operations_knowledge().get("kb_articles", []))


def get_kb_article(article_id: str) -> dict | None:
    for article in get_kb_articles():
        if str(article.get("id", "")).upper() == article_id.upper():
            return article
    return None


def select_services(service_names: Iterable[str]) -> list[dict]:
    requested = {name.strip().lower() for name in service_names if name and name.strip()}
    if not requested:
        return []

    selected: list[dict] = []
    for service in get_service_catalog():
        canonical = str(service.get("service", "")).lower()
        if canonical in requested:
            selected.append(service)
    return selected


def select_playbooks(issue_types: Iterable[str], normalized_query: str) -> list[dict]:
    issue_set = {issue.strip().lower() for issue in issue_types if issue and issue.strip()}
    query = normalized_query.lower()
    selected: list[dict] = []

    for playbook in get_playbooks():
        tags = {str(tag).lower() for tag in playbook.get("tags", [])}
        symptom = str(playbook.get("symptom", "")).lower()
        if tags & issue_set or any(tag in query for tag in tags) or symptom in query:
            selected.append(playbook)

    return selected


def select_event_taxonomy(issue_types: Iterable[str], normalized_query: str) -> list[dict]:
    issue_set = {issue.strip().lower() for issue in issue_types if issue and issue.strip()}
    query = normalized_query.lower()
    selected: list[dict] = []

    for event in get_event_taxonomy():
        aliases = [str(alias).lower() for alias in event.get("aliases", [])]
        event_type = str(event.get("event_type", "")).lower()
        if event_type in issue_set:
            selected.append(event)
            continue
        if any(alias in query for alias in aliases):
            selected.append(event)
            continue
        if any(issue in event_type for issue in issue_set):
            selected.append(event)

    return selected


def select_issue_workflows(issue_types: Iterable[str], normalized_query: str) -> list[dict]:
    issue_set = {issue.strip().lower() for issue in issue_types if issue and issue.strip()}
    query = normalized_query.lower()
    selected: list[dict] = []

    for workflow in get_deterministic_issue_workflows():
        issue_type = str(workflow.get("issue_type", "")).lower()
        phrases = [str(value).lower() for value in workflow.get("when_query_contains", [])]
        if issue_type in issue_set or any(phrase in query for phrase in phrases):
            selected.append(workflow)

    return selected


def build_knowledge_guidance(
    services: Iterable[str], issue_types: Iterable[str], normalized_query: str
) -> list[str]:
    lines: list[str] = []

    process_steps = get_standard_troubleshooting_process()
    if process_steps:
        process_preview = ", ".join(
            f"{step.get('step')}.{step.get('name')}"
            for step in process_steps[:3]
        )
        lines.append(
            "Mandatory troubleshooting process: " + process_preview
        )

    selected_services = select_services(services)
    if selected_services:
        service_lines = []
        for service in selected_services[:3]:
            service_lines.append(
                f"{service.get('service')} -> workspace={service.get('workspace')} "
                f"resource_hint={service.get('resource_id_contains')} tables={', '.join(service.get('common_tables', [])[:3])}"
            )
        lines.append(
            "Operational service catalog for this request: " + "; ".join(service_lines)
        )

    selected_events = select_event_taxonomy(issue_types, normalized_query)
    if selected_events:
        event_lines = []
        for event in selected_events[:2]:
            event_lines.append(
                f"{event.get('event_type')} -> signals={'; '.join(event.get('primary_signals', [])[:2])}"
            )
        lines.append("Event taxonomy guidance: " + " | ".join(event_lines))

    selected_playbooks = select_playbooks(issue_types, normalized_query)
    if selected_playbooks:
        playbook = selected_playbooks[0]
        lines.append(
            "Troubleshooting playbook: "
            f"symptom='{playbook.get('symptom')}', checks={'; '.join(playbook.get('signals_to_check', [])[:3])}, "
            f"escalate_to={playbook.get('escalation_target')}"
        )

    selected_workflows = select_issue_workflows(issue_types, normalized_query)
    if selected_workflows:
        workflow = selected_workflows[0]
        checks = "; ".join(workflow.get("required_checks", [])[:3])
        lines.append(
            "Deterministic issue workflow: "
            f"{workflow.get('issue_type')} -> checks={checks}"
        )
        guardrail = str(workflow.get("guardrail", "")).strip()
        if guardrail:
            lines.append("Workflow guardrail: " + guardrail)

    false_positives = get_known_false_positives()
    if false_positives and any(
        issue in normalized_query
        for issue in ("disconnect", "timeout", "assignment", "security", "twilio", "pod")
    ):
        guidance = str(false_positives[0].get("guidance", "")).strip()
        if guidance:
            lines.append("False-positive guardrail: " + guidance)

    return lines
