"""Pure helpers for the dashboard (no FastAPI import, easy to test)."""
from __future__ import annotations

import importlib
import re
from functools import lru_cache
from urllib.parse import urlparse

TASK_MODULES = ("saucedemo_checkout", "booking_search", "wikipedia_extract")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?$")


def valid_domain(domain: str) -> bool:
    """Accept only plain hostnames (optionally with a port)."""
    return bool(domain) and len(domain) <= 253 and bool(_DOMAIN.match(domain.lower()))


def normalize_domain(domain: str) -> str:
    domain = domain.lower()
    return domain[4:] if domain.startswith("www.") else domain


@lru_cache(maxsize=1)
def task_variables() -> dict:
    """{(domain, workflow_name): variables} for the predefined demo tasks."""
    out = {}
    for name in TASK_MODULES:
        try:
            task = importlib.import_module(f"tasks.{name}").TASK
        except Exception:
            continue
        domain = normalize_domain(urlparse(task["url"]).netloc)
        out[(domain, task["name"])] = dict(task.get("variables", {}))
    return out


def placeholders_in(workflow) -> set:
    """Names used as ``{name}`` in the steps' value templates."""
    names = set()
    for step in workflow.steps:
        if step.value_template:
            names.update(_PLACEHOLDER.findall(step.value_template))
    return names


def missing_variables(workflow, variables: dict) -> list:
    """Placeholders the workflow needs that we cannot fill (sorted)."""
    return sorted(placeholders_in(workflow) - set(variables))
