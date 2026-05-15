from app.agent.operations_knowledge import (
    build_knowledge_guidance,
    get_deterministic_issue_workflows,
    get_ingestion_templates,
    get_standard_troubleshooting_process,
    select_kb_articles,
    select_event_taxonomy,
    select_issue_workflows,
    select_playbooks,
)


def test_select_playbooks_matches_issue_tag() -> None:
    selected = select_playbooks(
        ["audio"],
        "candidate audio issue during exam, one-way audio reported",
    )

    assert selected
    assert selected[0]["symptom"] == "candidate audio issues"


def test_select_event_taxonomy_matches_alias_phrase() -> None:
    selected = select_event_taxonomy(
        [],
        "we saw readiness agent disconnected and need triage",
    )

    assert any(item.get("event_type") == "readiness_agent_disconnect" for item in selected)


def test_select_issue_workflow_matches_timeout_query() -> None:
    selected = select_issue_workflows(
        [],
        "exam-sessions api timeout and 5xx spike observed",
    )

    assert selected
    assert selected[0].get("issue_type") == "backend_timeout"


def test_build_knowledge_guidance_includes_process_and_workflow() -> None:
    guidance = build_knowledge_guidance(
        services=["exam-sessions-api"],
        issue_types=["backend_timeout"],
        normalized_query="backend timeout and dependency timeout in exam sessions api",
    )

    joined = " ".join(guidance)
    assert "Mandatory troubleshooting process" in joined
    assert "Deterministic issue workflow" in joined
    assert "Workflow guardrail" in joined


def test_ingestion_templates_and_process_are_available() -> None:
    templates = get_ingestion_templates()
    process = get_standard_troubleshooting_process()
    workflows = get_deterministic_issue_workflows()

    assert "service_catalog_template" in templates
    assert process
    assert process[0].get("step") == 1
    assert workflows


def test_select_kb_articles_for_duplicate_task_issue_type() -> None:
    selected = select_kb_articles(
        issue_types=["duplicate_task_requeue"],
        normalized_query="user reports duplicate tasks and asked what kb to refer",
    )

    ids = {str(item.get("id")) for item in selected}
    assert "PRT0933" in ids
    assert "PRT0849" in ids


def test_build_knowledge_guidance_includes_kb_references_for_kb_query() -> None:
    guidance = build_knowledge_guidance(
        services=[],
        issue_types=["chat_issue"],
        normalized_query="chat messages delayed, which kb should i refer to",
    )

    joined = " ".join(guidance)
    assert "KB references:" in joined
    assert "PRT0810" in joined
