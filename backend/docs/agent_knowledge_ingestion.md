# Agent Knowledge Ingestion Guide

Use this guide to add service documentation, architecture notes, and troubleshooting runbooks into the AI support analyst knowledge layer.

## Source Files

- Main knowledge file: `backend/app/agent/operations_knowledge.json`
- Loader and selectors: `backend/app/agent/operations_knowledge.py`

## Ingestion Workflow

1. Gather source material from service owners:
- service purpose and ownership
- App Insights workspace/resource identifiers
- correlation IDs and operation names
- known false positives
- troubleshooting runbooks

2. Populate templates in `ingestion_templates`:
- `service_catalog_template`
- `event_taxonomy_template`
- `playbook_template`

3. Add entries to:
- `service_catalog`
- `event_taxonomy`
- `troubleshooting_playbooks`
- `known_false_positives`
- `architecture_relationships`
- `deterministic_issue_workflows`

4. Keep process consistency:
- Align new workflows with `standard_troubleshooting_process`
- Ensure each new issue workflow has deterministic checks and a guardrail

## Data Quality Rules

1. Use deterministic signals first. Avoid vague or inferred-only rules.
2. Include exact table or container references where possible.
3. Add at least one guardrail for each workflow to prevent false root-cause claims.
4. Keep service/resource names consistent with existing aliases.
5. Prefer candidate-safe wording for end-user visible responses.

## Example PR Checklist

1. Added/updated service catalog entries for changed services.
2. Added/updated issue taxonomy aliases for user language.
3. Added/updated troubleshooting playbook with escalation target.
4. Added/updated deterministic issue workflow and guardrail.
5. Added/updated tests in `backend/tests/test_operations_knowledge.py`.
