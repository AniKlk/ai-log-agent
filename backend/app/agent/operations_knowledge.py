import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_KNOWLEDGE_PATH = Path(__file__).with_name("operations_knowledge.json")
_KB_ID_REGEX = re.compile(r"\bPRT\d{4}\b", re.IGNORECASE)


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


def _extract_kb_ids_from_text(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {match.upper() for match in _KB_ID_REGEX.findall(value)}
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found |= _extract_kb_ids_from_text(item)
        return found
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found |= _extract_kb_ids_from_text(item)
        return found
    return set()


def select_kb_articles(
    issue_types: Iterable[str],
    normalized_query: str,
    selected_playbooks: list[dict] | None = None,
    selected_workflows: list[dict] | None = None,
) -> list[dict]:
    kb_by_id = {
        str(article.get("id", "")).upper(): article
        for article in get_kb_articles()
        if str(article.get("id", "")).strip()
    }
    if not kb_by_id:
        return []

    issue_set = {issue.strip().lower() for issue in issue_types if issue and issue.strip()}
    query = normalized_query.lower()
    selected_ids: set[str] = set()

    # 1) Explicit KB mentions in the user query.
    selected_ids |= {match.upper() for match in _KB_ID_REGEX.findall(query)}

    # 2) KB references already encoded in matched workflows/playbooks.
    for workflow in selected_workflows or []:
        selected_ids |= _extract_kb_ids_from_text(workflow)
    for playbook in selected_playbooks or []:
        selected_ids |= _extract_kb_ids_from_text(playbook)

    # 3) Query asks for KB/help article and matches article semantics.
    kb_intent = any(
        token in query
        for token in (
            "kb",
            "knowledge base",
            "knowledge article",
            "which article",
            "what article",
            "what kb",
            "which kb",
            "refer",
            "salesforce",
        )
    )
    if kb_intent:
        for article in kb_by_id.values():
            title = str(article.get("title", "")).lower()
            use_when = str(article.get("use_when", "")).lower()
            if any(term in query for term in (title, use_when)):
                selected_ids.add(str(article.get("id", "")).upper())

        # Domain keywords mapped to known KBs.
        if any(token in query for token in ("ghost task", "requeue", "duplicate task")):
            selected_ids |= {"PRT0933", "PRT0849"}
        if any(token in query for token in ("servicebus", "queue", "chat delay", "message delivery")):
            selected_ids.add("PRT0810")
        if any(token in query for token in ("twilio", "room sid", "reservation", "task state")):
            selected_ids.add("PRT0920")
        if any(token in query for token in ("okta", "proctor language", "system log", "auth")):
            selected_ids.add("PRT0842")

    # 4) Issue-type fallback mapping.
    if "duplicate_task_requeue" in issue_set:
        selected_ids |= {"PRT0933", "PRT0849"}
    if "kill_session_failure" in issue_set:
        selected_ids.add("PRT0849")
    if "chat_issue" in issue_set:
        selected_ids.add("PRT0810")

    ordered_ids = sorted(selected_ids)
    return [kb_by_id[kb_id] for kb_id in ordered_ids if kb_id in kb_by_id]


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

    selected_kb_articles = select_kb_articles(
        issue_types,
        normalized_query,
        selected_playbooks=selected_playbooks,
        selected_workflows=selected_workflows,
    )
    if selected_kb_articles:
        kb_preview = "; ".join(
            f"{article.get('id')} ({article.get('title')})"
            for article in selected_kb_articles[:3]
        )
        lines.append("KB references: " + kb_preview)

    false_positives = get_known_false_positives()
    if false_positives and any(
        issue in normalized_query
        for issue in ("disconnect", "timeout", "assignment", "security", "twilio", "pod")
    ):
        guidance = str(false_positives[0].get("guidance", "")).strip()
        if guidance:
            lines.append("False-positive guardrail: " + guidance)

    return lines
