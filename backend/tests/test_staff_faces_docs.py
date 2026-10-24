"""Staff faces: the design note, the web and the phone say what the migration grants and the server serves.

Reads files outside backend/, so the module runs with the repository-inspection suites.
"""
from __future__ import annotations

import importlib.util
import re

from app.main import app
from app.services import staff_faces as faces
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "docs" / "plans" / "2026-10-23-staff-face-registration-design.md"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0167_staff_faces.py"


def _granted() -> dict[str, set[int]]:
    spec = importlib.util.spec_from_file_location("m0167", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out: dict[str, set[int]] = {}
    for role, code in module.ROLES:
        out.setdefault(code, set()).add(role)
    return out


def test_the_grants_follow_the_rules_and_leave_out_the_platform_owner():
    granted = _granted()
    assert granted == {"face:register_self": set(faces.FACE_ROLES), "face:manage": {2, 3, 8}}
    assert set().union(*faces.CHANGERS.values()) == granted["face:manage"]
    assert set(faces.CHANGERS) == set(faces.FACE_ROLES)
    doc = DOC.read_text(encoding="utf-8")
    assert "`face:register_self` (roles 2, 3, 4, 5, 8)" in doc and "`face:manage` (roles 2, 3, 8)" in doc


def test_the_web_gives_each_role_what_the_migration_grants():
    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(r"'(face:[a-z_]+)'", re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    granted = _granted()
    assert web == {role: {c for c, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert "face:" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    assert set(re.findall(r"'(face:[a-z_]+)'", src.split("const PLATFORM_PERMISSIONS")[0])) == set(granted)


def _served() -> set[tuple[str, str]]:
    """Every route under /api/v1/faces, with its methods. app.routes holds the included routers themselves, so each
    is walked to its routes."""
    out = set()
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/faces") and getattr(route, "endpoint", None):
                out.update((m, path) for m in getattr(route, "methods", ()))
    return out


def test_every_route_is_one_the_design_names():
    served = _served()
    assert served, "the routes were found"
    doc = DOC.read_text(encoding="utf-8")
    named = {(m, "/api/v1/faces" + p.split("?")[0]) for m, p in re.findall(r"`(GET|POST|PUT|DELETE) /api/v1/faces([^`]*)`", doc)}
    assert served == named


def test_the_clients_ask_for_what_the_server_serves():
    phone = (PHONE / "api" / "faces.ts").read_text(encoding="utf-8")
    for path in ("'/api/v1/faces/me'", "'/api/v1/faces/me/checks'", "/api/v1/faces/${userId}/photo?token="):
        assert path in phone, path
    web = (WEB / "api" / "faces.ts").read_text(encoding="utf-8")
    for path in ("'/api/v1/faces'", "`/api/v1/faces/${userId}`", "'/api/v1/faces/checks'",
                 "/api/v1/faces/${userId}/photo?token="):
        assert path in web, path
    panel = (WEB / "components" / "faces" / "FaceChecksPanel.tsx").read_text(encoding="utf-8")
    assert "useTenantTimeZone()" in panel and "timeZone })" in panel, "the review panel says times in the organisation's zone"
    menu = (PHONE / "screens" / "MoreMenuScreen.tsx").read_text(encoding="utf-8")
    assert "screen: 'MyFace'" in menu and "permission: 'face:register_self'" in menu


def test_the_existing_files_changed_are_named_and_there():
    part = DOC.read_text(encoding="utf-8").split("## 8. Existing files this changes", 1)[1].split("## 9.", 1)[0]
    named = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`", part)
    assert len(named) == len(set(named)) == 11
    for path in named:
        assert (REPO_ROOT / path).exists(), path
