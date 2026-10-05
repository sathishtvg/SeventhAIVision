"""AI security intelligence: the clients call an API that exists.

The web app (and the Windows desktop app, which is the web build in a shell) is
not compiled against the server. A path renamed on one side is a button that
does nothing on the other, and a field the server does not know is a decision
refused — found by an officer at two in the morning rather than by a build.

  A — Every call the web client makes is an operation the API serves, with
      that method
  B — The filters it sends are ones the API takes, and the body of a decision
      has only fields the API accepts
  C — The web permission table gives each role what the migration grants: no
      more, so no button onto a 403; no less, so no screen hidden from a role
      that may use it
  D — The screens are registered, and the sidebar guards them

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import re

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from tests._repo import REPO_ROOT, requires_repo_tree
from tests.test_drone_clients import _served, client_calls

pytestmark = requires_repo_tree

WEB_CLIENT = REPO_ROOT / "frontend" / "src" / "api" / "securityIntelligence.ts"
WEB_PERMISSIONS = REPO_ROOT / "frontend" / "src" / "hooks" / "usePermission.ts"
WEB_ROUTES = REPO_ROOT / "frontend" / "src" / "App.tsx"
WEB_SIDEBAR = REPO_ROOT / "frontend" / "src" / "components" / "layout" / "Sidebar.tsx"
COMMAND_CENTRE = REPO_ROOT / "frontend" / "src" / "pages" / "CommandCentre.tsx"
GRANTS = REPO_ROOT / "backend" / "alembic" / "versions" / "0132_security_intelligence_events.py"
BASE = "/api/v1/security-intelligence"


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


# ─── A. Every call is an operation the API serves ────────────────────────────

def test_every_intelligence_call_in_the_web_client_is_served(spec):
    calls = client_calls(WEB_CLIENT)
    # Fewer than this means the scan stopped finding calls, not that they went.
    assert len(calls) >= 18, f"only {len(calls)} calls found in {WEB_CLIENT.name}"
    assert all(p.startswith(BASE + "/") for _, p in calls), [p for _, p in calls if not p.startswith(BASE + "/")]
    missing = sorted({f"{m} {p}" for m, p in calls if not _served(m, p, spec)})
    assert not missing, f"{WEB_CLIENT.name} calls operations the API does not serve: {missing}"
    # The scan itself, on the shapes this client uses.
    assert ("POST", f"{BASE}/situations/{{}}/decisions") in calls
    assert ("DELETE", f"{BASE}/decision-policy/sites/{{}}") in calls
    assert not _served("POST", f"{BASE}/situations/{{}}/execute", spec), "there is no way to act without deciding"


# ─── B. Filters and bodies ───────────────────────────────────────────────────

def _interface(name: str) -> set[str]:
    """The field names of an exported TypeScript interface in the web client."""
    src = WEB_CLIENT.read_text(encoding="utf-8")
    body = re.search(rf"export interface {name} \{{(.*?)\n\}}", src, re.S).group(1)
    return set(re.findall(r"^\s*(\w+)\??:", body, re.M))


def test_the_filters_the_web_client_sends_are_ones_the_api_takes(spec):
    def taken(path: str) -> set[str]:
        return {p["name"] for p in spec["paths"][path]["get"]["parameters"]}

    sent = _interface("SituationFilters")
    assert sent >= {"open", "sort", "decision_status", "risk_level"}, "the interface was not read"
    assert sent <= taken(f"{BASE}/situations"), sent - taken(f"{BASE}/situations")
    src = WEB_CLIENT.read_text(encoding="utf-8")
    listed = re.search(r"export const listDecisions = \(params: \{(.*?)\}", src, re.S).group(1)
    assert set(re.findall(r"(\w+)\?:", listed)) <= taken(f"{BASE}/decisions")


def test_a_decision_from_the_web_has_only_fields_the_api_accepts(spec):
    """The API refuses a body with a field it does not know, so an invented
    field here is every decision refused."""
    accepted = set(spec["components"]["schemas"]["DecisionIn"]["properties"])
    sent = _interface("DecisionInput") | {"via"}                 # `via` is added where the call is made
    assert {"action", "client_ref", "seen_assessment_id"} <= sent, "the interface was not read"
    assert sent <= accepted, sent - accepted
    assert spec["components"]["schemas"]["DecisionIn"].get("additionalProperties") is False


def test_the_web_client_reads_fields_the_api_returns():
    """Spot checks on the three records the screens keep apart, against the
    code that builds them."""
    from app.services import intel_decisions

    row = {k: None for k in ("id", "situation_id", "situation_number", "decided_at", "action", "basis", "reason_code",
                             "note", "actor_user_id", "actor_name", "actor_role", "via", "suggested_action",
                             "recommendation_id", "assessment_id", "seen_assessment_id", "risk_level", "risk_score",
                             "authority", "policy", "params", "approval_verdict", "approval_note", "approval_at",
                             "approver_user_id", "approver_name", "approver_role")}
    served = set(intel_decisions.view({**row, "authority": "ALONE"}, []))
    read = _interface("Decision") - {"replayed"}                 # present only on a retry
    assert read <= served, f"the web reads fields a decision does not have: {sorted(read - served)}"
    assert _interface("ActionRow") == {"sequence", "action", "through", "target_type", "target_id", "result",
                                       "detail", "executed_at", "executed_by_user_id"}


# ─── C. The permission table ─────────────────────────────────────────────────

def _granted() -> dict[int, set[str]]:
    """Role -> the intel permissions migration 0132 grants it."""
    out: dict[int, set[str]] = {}
    for role, code in re.findall(r"\((\d), '(intel:[a-z:]+)'\)", GRANTS.read_text(encoding="utf-8")):
        out.setdefault(int(role), set()).add(code)
    return out


def _web_matrix() -> dict[int, set[str]]:
    """Role -> the intel permissions the web's own table gives it."""
    src = WEB_PERMISSIONS.read_text(encoding="utf-8")
    everything = set(re.findall(r"'(intel:[a-z:]+)'", src.split("const ROLE_PERMISSIONS", 1)[0]))
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    out: dict[int, set[str]] = {}
    for role in (3, 4, 5, 6, 7):
        block = re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)
        out[role] = set(re.findall(r"'(intel:[a-z:]+)'", block))
    for role in (2, 8):                                           # everything, less a named few
        excluded = set(re.findall(r"'([a-z:_0-9]+)'", re.search(rf"\n  {role}: ALL_PERMISSIONS\.filter\((.*?)\)\),", table, re.S).group(1)))
        out[role] = everything - excluded
    out[1] = set(re.findall(r"'(intel:[a-z:]+)'", src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]))
    return out


def test_the_web_gives_each_role_exactly_the_intel_permissions_the_migration_grants():
    granted, web = _granted(), _web_matrix()
    assert sum(len(v) for v in granted.values()) == 27, "the migration's grants were not read"
    for role in (1, 2, 3, 4, 5, 6, 7, 8):
        assert web[role] == granted.get(role, set()), f"role {role}: web {sorted(web[role])}, migration {sorted(granted.get(role, set()))}"
    assert web[1] == set() and web[7] == set(), "the platform owner and the client role hold none"


# ─── D. The screens are registered and guarded ───────────────────────────────

def test_the_web_registers_the_screens_and_the_sidebar_guards_them():
    routes = WEB_ROUTES.read_text(encoding="utf-8")
    for route in ('path="situations"', 'path="situations/:id"', 'path="situation-decisions"', 'path="intelligence-setup"'):
        assert route in routes, route
    sidebar = WEB_SIDEBAR.read_text(encoding="utf-8")
    for path, permission in (("/situations", "intel:read"), ("/situation-decisions", "intel:read"),
                             ("/intelligence-setup", "intel:manage")):
        row = next(line for line in sidebar.splitlines() if f"path: '{path}'" in line)
        assert f"permission: '{permission}'" in row, row


def test_the_command_centre_gained_one_panel_and_nothing_else():
    """The existing page is touched by an import and one element. The panel
    decides for itself whether to show anything."""
    page = COMMAND_CENTRE.read_text(encoding="utf-8")
    # The name appears in the import (twice: the binding and the path) and in the one element.
    assert page.count("SituationsPanel") == 3 and page.count("<SituationsPanel />") == 1
    assert "securityIntelligence" not in page, "the page itself calls nothing of the layer's"
    panel = (REPO_ROOT / "frontend" / "src" / "components" / "intel" / "SituationsPanel.tsx").read_text("utf-8")
    assert "if (!on || !data) return null" in panel
    assert "usePermission('intel:read')" in panel and "status?.enabled" in panel
