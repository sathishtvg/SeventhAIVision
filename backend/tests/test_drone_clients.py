"""Drone patrol, phase 10: the clients call an API that exists.

Three clients reach the drone module — the web app, the phone and the Windows
desktop app (which is the web build in an Electron shell). None of them is
compiled against the server, so a path renamed on one side is a button that
does nothing on the other, found by an officer rather than by a build.

  A — Every drone call in the web and mobile clients is an operation the API
      serves, with that method
  B — The event list accepts the filters the clients send
  C — The desktop shell's content policy allows what the drone screens load
  D — The phone registers the drone screens behind the permission they need
  E — The API document lists exactly the operations the application serves,
      each with the permission the code requires

Read from the working tree, so it runs with the repository-inspection suites.
"""
from __future__ import annotations

import re

import pytest

# Module level on purpose: app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

WEB_CLIENT = REPO_ROOT / "frontend" / "src" / "api" / "drones.ts"
MOBILE_CLIENT = REPO_ROOT / "mobile" / "src" / "api" / "drones.ts"
DESKTOP_CSP = REPO_ROOT / "desktop" / "electron" / "csp.js"
MOBILE_NAV = REPO_ROOT / "mobile" / "src" / "navigation" / "index.tsx"
MOBILE_MENU = REPO_ROOT / "mobile" / "src" / "screens" / "MoreMenuScreen.tsx"
API_DOC = REPO_ROOT / "DRONE_PATROL_API.md"

_CALL = re.compile(r"apiClient\s*\.\s*(get|post|put|patch|delete)\b")
_CONST = re.compile(r"^const\s+([A-Z_]+)\s*=\s*'([^']+)'", re.M)


def _first_argument(src: str, i: int) -> str:
    """The first argument of the call whose method name ends at `i`: skip an
    optional generic (`<...>`, which nests and spans lines), then read the
    string literal or the constant's name."""
    depth = 0
    while i < len(src):
        ch = src[i]
        if ch == "<":
            depth += 1
        elif ch == ">" and src[i - 1] != "=":
            depth -= 1
        elif ch == "(" and depth == 0:
            break
        i += 1
    rest = src[i + 1:].lstrip()
    if rest[0] in "'\"`":
        return rest[1:rest.index(rest[0], 1)]
    return re.match(r"[A-Za-z_]+", rest).group(0)


def client_calls(path) -> list[tuple[str, str]]:
    """(METHOD, path) for every API call in a client file, with each `${...}`
    in a path reduced to `{}`."""
    src = path.read_text(encoding="utf-8")
    consts = dict(_CONST.findall(src))
    out = []
    for m in _CALL.finditer(src):
        arg = _first_argument(src, m.end())
        arg = consts.get(arg, arg)
        for name, value in consts.items():
            arg = arg.replace("${" + name + "}", value)
        out.append((m.group(1).upper(), re.sub(r"\$\{[^}]*\}", "{}", arg)))
    return out


def _served(method: str, path: str, spec: dict) -> bool:
    want = path.strip("/").split("/")
    for served, operations in spec["paths"].items():
        if method.lower() not in operations:
            continue
        have = served.strip("/").split("/")
        if len(have) == len(want) and all(
            w == h or w == "{}" or (h.startswith("{") and h.endswith("}")) for w, h in zip(want, have)
        ):
            return True
    return False


@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


# ─── A. Every call is an operation the API serves ────────────────────────────

@pytest.mark.parametrize("client,at_least", [(WEB_CLIENT, 40), (MOBILE_CLIENT, 9)], ids=["web", "mobile"])
def test_every_drone_call_is_served(client, at_least, spec):
    calls = client_calls(client)
    # Fewer than this means the scan stopped finding calls, not that they went.
    assert len(calls) >= at_least, f"only {len(calls)} calls found in {client.name}"
    assert all(p.startswith("/api/v1/") for _, p in calls), [p for _, p in calls if not p.startswith("/api/v1/")]
    missing = sorted({f"{m} {p}" for m, p in calls if not _served(m, p, spec)})
    assert not missing, f"{client.name} calls operations the API does not serve: {missing}"


def test_the_scan_reads_generics_constants_and_templates():
    """The scanner itself, on the three shapes the clients use — so a pass above
    cannot come from a scanner that quietly skips what it cannot read."""
    calls = client_calls(WEB_CLIENT)
    assert ("GET", "/api/v1/drones") in calls                       # a bare constant, behind a nested generic
    assert ("GET", "/api/v1/drones/entitlement") in calls           # ${CONSTANT}/literal
    assert ("POST", "/api/v1/drone-events/{}/{}") in calls          # two interpolations
    assert ("POST", "/api/v1/drone-events/{}/incident") in calls    # a generic spanning lines
    assert not _served("GET", "/api/v1/drone-events/{}/no-such-thing", app.openapi())
    assert not _served("DELETE", "/api/v1/drone-events/{}/card", app.openapi())


# ─── B. The event list accepts the filters the clients send ──────────────────

def test_the_event_list_takes_the_clients_filters(spec):
    names = {p["name"] for p in spec["paths"]["/api/v1/drone-events"]["get"]["parameters"]}
    assert {"alert_id", "incident_id", "session_id", "site_id", "open_only", "risk_level", "status",
            "limit", "offset"} <= names, names


# ─── C. The desktop shell allows what the drone screens load ─────────────────

def test_the_desktop_policy_allows_the_drone_screens():
    """The Windows app is the web build under a strict content policy. The drone
    screens draw map tiles over https, show snapshots and clips from memory
    (blob:), play live video through hls.js (blob: media, a worker) and call the
    API — each of which that policy must name, or the screen is silently blank."""
    csp = DESKTOP_CSP.read_text(encoding="utf-8")
    directive = lambda name: next(line for line in csp.splitlines() if f"{name} " in line and "`" in line)  # noqa: E731
    assert "blob:" in directive("img-src") and "https:" in directive("img-src")
    assert "blob:" in directive("media-src") and "${api}" in directive("media-src")
    assert "${api}" in directive("connect-src") and "${wsApi}" in directive("connect-src")
    assert "blob:" in next(line for line in csp.splitlines() if "worker-src" in line)


# ─── D. The phone registers the drone screens behind their permission ────────

def test_the_phone_registers_the_drone_screens():
    nav = MOBILE_NAV.read_text(encoding="utf-8")
    for screen in ('name="DroneEvents"', 'name="DroneEventDetail"', 'name="AlertDroneEvent"'):
        assert screen in nav, screen
    row = next(line for line in MOBILE_MENU.read_text(encoding="utf-8").splitlines() if "screen: 'DroneEvents'" in line)
    # The list endpoint requires exactly this; without the gate the row is a
    # door onto a 403 for any role that lacks it.
    assert "permission: 'drone:event:read'" in row
    # Not an oversight screen: a guard holds drone:event:read and is who walks over.
    assert "audience: 'ops'" not in row


# ─── E. The API document ─────────────────────────────────────────────────────

def _served_operations() -> dict[tuple[str, str], set[str]]:
    """(METHOD, path) -> the permissions its dependencies require, for every
    drone operation, read from the application's own route table."""
    def permissions(route) -> set[str]:
        found: set[str] = set()

        def walk(dep):
            name = getattr(dep.call, "__qualname__", "")
            if "require_permission" in name:
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
            if "drone" in getattr(route, "path", "") and hasattr(route, "dependant"):
                for method in route.methods - {"HEAD"}:
                    out[(method, route.path.removeprefix("/api/v1"))] = permissions(route)
    return out


def _documented_operations() -> dict[tuple[str, str], set[str]]:
    """The rows of the document's operation tables: | METHOD | `path` | permission |."""
    rows = {}
    for line in API_DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`\s*\|([^|]*)\|", line)
        if m:
            path = m.group(2).strip().split("?")[0]          # a query string shown in the path is not part of it
            rows[(m.group(1), path)] = set(re.findall(r"`([a-z_0-9]+(?::[a-z_0-9]+)+)`", m.group(3)))
    return rows


def test_the_api_document_lists_exactly_what_is_served():
    served, documented = _served_operations(), _documented_operations()
    assert len(served) >= 100, "the route walk found too little to compare"
    assert sorted(set(served) - set(documented)) == [], "served, but not in DRONE_PATROL_API.md"
    assert sorted(set(documented) - set(served)) == [], "in DRONE_PATROL_API.md, but not served"


def test_the_api_document_states_the_permission_the_code_requires():
    served, documented = _served_operations(), _documented_operations()
    wrong = [f"{method} {path}: code {sorted(served[(method, path)])}, document {sorted(written)}"
             for (method, path), written in sorted(documented.items())
             if (method, path) in served and served[(method, path)] and written != served[(method, path)]]
    assert wrong == []
