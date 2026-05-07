import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).with_name("investigation_rules.json")


@lru_cache(maxsize=1)
def load_rules() -> dict:
    with _RULES_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_platform_aliases() -> tuple[str, ...]:
    return tuple(load_rules().get("platform_aliases", []))


def get_service_aliases() -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(value)
        for key, value in load_rules().get("service_aliases", {}).items()
    }


def get_service_dependencies() -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(value)
        for key, value in load_rules().get("service_dependencies", {}).items()
    }


def get_issue_aliases() -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(value)
        for key, value in load_rules().get("issue_aliases", {}).items()
    }


def normalize_text(text: str) -> str:
    normalized = text.lower().replace("_", " ").replace("-", " ")
    for token in (",", ".", ";", ":", "?", "!", "(", ")", "[", "]", "{", "}"):
        normalized = normalized.replace(token, " ")
    return " ".join(normalized.split())


def canonicalize_services(text: str) -> list[str]:
    normalized = normalize_text(text)
    services: list[str] = []
    for canonical, aliases in get_service_aliases().items():
        if any(alias in normalized for alias in aliases):
            services.append(canonical)
    return services


def canonicalize_issue_types(text: str) -> list[str]:
    normalized = normalize_text(text)
    issues: list[str] = []
    for canonical, aliases in get_issue_aliases().items():
        if any(alias in normalized for alias in aliases):
            issues.append(canonical)
    return issues


def expand_related_services(services: Iterable[str]) -> list[str]:
    related: list[str] = []
    dependencies = get_service_dependencies()
    for service in services:
        for dependency in dependencies.get(service, ()):  # pragma: no branch
            if dependency not in related:
                related.append(dependency)
    return related


def mentions_platform(text: str) -> bool:
    normalized = normalize_text(text)
    return any(alias in normalized for alias in get_platform_aliases())
