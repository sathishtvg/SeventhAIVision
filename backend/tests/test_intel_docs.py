"""The intelligence layer's documents say what the code does.

A document that drifts from the code is worse than none in a system whose claim
is that a person, not the software, takes the decision: these check the claims
that can be checked against the repository.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re

import yaml

from app.main import app
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

ARCHITECTURE = REPO_ROOT / "AI_SECURITY_INTELLIGENCE_ARCHITECTURE.md"
PREFIX = "/security-intelligence"


def _served() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path) -> the permissions its dependencies require, read from the
    application's own route table."""
    def permissions(route) -> set[str]:
        found: set[str] = set()

        def walk(dep):
            if "require_permission" in getattr(dep.call, "__qualname__", ""):
                found.update(c.cell_contents for c in (dep.call.__closure__ or ())
                             if isinstance(c.cell_contents, str))
            for sub in dep.dependencies:
                walk(sub)

        for d in route.dependant.dependencies:
            walk(d)
        return found

    out = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        inner = [r] if contexts is None else (contexts() if callable(contexts) else contexts)
        for route in inner:
            if PREFIX in getattr(route, "path", "") and hasattr(route, "dependant"):
                for method in route.methods - {"HEAD"}:
                    out[(method, route.path.removeprefix("/api/v1"))] = permissions(route)
    return out


def _documented() -> dict[tuple[str, str], set[str]]:
    """The rows of the document's operation table: | METHOD | `path` | permission |."""
    rows = {}
    for line in ARCHITECTURE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`\s*\|([^|]*)\|", line)
        if m:
            rows[(m.group(1), m.group(2).strip().split("?")[0])] = set(
                re.findall(r"`([a-z_0-9]+(?::[a-z_0-9]+)+)`", m.group(3)))
    return rows


def test_the_document_lists_exactly_the_operations_that_are_served():
    served, documented = _served(), _documented()
    assert served, "the route walk found nothing to compare"
    assert sorted(set(served) - set(documented)) == [], "served, but not in the architecture document"
    assert sorted(set(documented) - set(served)) == [], "in the architecture document, but not served"


def test_the_document_states_the_permission_the_code_requires():
    served, documented = _served(), _documented()
    wrong = [f"{method} {path}: code {sorted(served[(method, path)])}, document {sorted(written)}"
             for (method, path), written in sorted(documented.items())
             if (method, path) in served and written != served[(method, path)]]
    assert wrong == []


def test_every_operation_needs_a_permission():
    assert [key for key, needed in _served().items() if not needed] == []


def test_the_runner_service_is_extra_and_can_reach_nothing_it_does_not_need():
    """It publishes no port and mounts nothing: it reads the database and
    writes its own tables, and the document says so."""
    services = yaml.safe_load((REPO_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    runner = services["intelligence-runner"]
    assert runner["command"] == ["python", "-m", "app.intelligence_main"]
    assert "ports" not in runner and "volumes" not in runner
    assert not runner.get("privileged") and runner.get("network_mode") != "host"
    assert set(runner["depends_on"]) == {"postgres", "redis"}
    for name, other in services.items():
        if name != "intelligence-runner":
            assert "intelligence-runner" not in (other.get("depends_on") or {}), \
                f"{name} must not wait for the runner: everything else works without it"
