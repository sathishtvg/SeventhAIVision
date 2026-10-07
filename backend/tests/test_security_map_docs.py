"""The security map: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import site_map as api
from app.services import guard_positions as positions
from app.services import security_map as maps
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "GIS_SECURITY_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0145_site_places.py"
BASE = "/api/v1/site-map"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_document_lists_every_layer_with_the_permission_that_shows_it():
    rows = re.findall(r"^\| `([A-Z_]+)` \|[^|]*\|[^|]*\| `([a-z:]+)` \|", _doc().split("## 2. The layers", 1)[1], re.M)
    assert dict(rows) == {layer.key: layer.permission for layer in maps.LAYERS}
    assert [key for key, _ in rows] == [layer.key for layer in maps.LAYERS], "in the order the map lists them"
    doc = _flat(_doc())
    assert (maps.DEFAULT_HOURS, maps.MAX_HOURS, maps.MAX_PER_LAYER) == (24, 168, 500)
    assert "look back 24 hours unless told, at most 168" in doc and "at most 500 things" in doc
    assert (maps.DOOR_ALERTS, maps.DOOR_ALERT_MINUTES) == (("forced", "held_open", "tamper"), 15)
    assert "forced, held open or tampered with in the last 15 minutes" in doc and "holding `access:read`" in doc


def test_the_document_says_where_a_guards_position_comes_from_and_that_it_is_not_live():
    section = _doc().split("## 3. A guard's position", 1)[1].split("## 4.", 1)[0]
    tables = re.findall(r"\| `([a-z_]+)` \|$", section, re.M)
    assert tables == ["checkpoint_scans", "incident_status_history", "occurrence_book_entries", "man_down_events",
                      "shifts"]
    code = Path(positions.__file__).read_text(encoding="utf-8")
    for table in tables:
        assert f"FROM {table}" in code, f"the document names {table} and the code does not read it"
    for field in ("position_source", "position_at", "position_age_s", "stale"):
        assert f"`{field}`" in section and f'"{field}"' in code
    assert positions.STALE_AFTER_S == 3600 and "over an hour old is marked stale" in _flat(_doc())
    assert "The phone is not asked where it is." in _flat(section)
    web = (WEB / "pages" / "securityMap" / "SecurityMap.tsx").read_text(encoding="utf-8")
    assert "position_age_s" in web and "stale" in web, "the screen shows the age, not only the place"


def test_the_document_says_what_is_near_and_that_nobody_is_sent():
    doc = _flat(_doc())
    assert (maps.DEFAULT_RADIUS_M, maps.MAX_RADIUS_M) == (300, 5000)
    assert "(300 m unless given, at most 5000)" in doc
    assert "free before already sent somewhere, nearer before farther, those with no recorded position last" in doc
    assert "`located: false`" in doc and "Nobody is sent anywhere by it." in doc
    for path in (Path(api.__file__), Path(maps.__file__), Path(positions.__file__)):
        code = path.read_text(encoding="utf-8")
        for verb in ("INSERT INTO incidents", "UPDATE incidents", "dispatch_guard(", "UPDATE shifts", "UPDATE alerts"):
            assert verb not in code, f"{path.name} would act: {verb}"


def test_the_document_lists_the_kinds_of_place_and_the_rules_the_database_holds():
    section = _doc().split("## 5. The places of a site", 1)[1].split("## 6.", 1)[0]
    named = set(re.findall(r"`([A-Z_]+)`", section))
    assert named == set(maps.PLACE_KINDS)
    migration = MIGRATION.read_text(encoding="utf-8")
    for said, held in (("part of a building or of nothing", "A place can be a part of a building"),
                       ("A door is at one place", "uq_site_places_door"),
                       ("not called the same thing", "uq_site_places_name"),
                       ("three to 200 points", "MAX_OUTLINE = 200")):
        assert said in _flat(section), said
        assert held in migration + Path(api.__file__).read_text(encoding="utf-8"), held
    assert "GRANT SELECT, INSERT, UPDATE ON site_places TO svc_app" in migration, "retired, not removed"


def test_the_document_lists_every_route_and_what_each_needs():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(BASE) or not getattr(route, "endpoint", None):
                continue
            needs: set[str] = set()

            def walk(dep):
                if "require_permission" in getattr(dep.call, "__qualname__", ""):
                    needs.update(c.cell_contents for c in (dep.call.__closure__ or ())
                                 if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            assert "sitemap:read" in needs, path
            written = path.removeprefix(BASE).replace("{place_id:uuid}", "{id}")
            for method in route.methods - {"HEAD"}:
                served[(method, written)] = next(iter(needs - {"sitemap:read"}), "")
    table = re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \| ?(?:`([a-z:]+)`)? ?\|$", _doc(), re.M)
    assert {(method, path): needs for method, path, needs in table} == served
    assert "There is no `DELETE`." in _doc() and not [m for m, _ in served if m == "DELETE"]
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(sitemap\.[a-z_.]+)"', code))
    named = set(re.findall(r"`(sitemap\.[a-z_.]+)`", _doc()))
    assert written == named and len(written) == 4


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(sitemap:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"sitemap:read": {2, 3, 4, 6, 8}, "sitemap:manage": {2, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(sitemap:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(granted)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: {"sitemap:read"}, 4: {"sitemap:read"}, 5: set(), 6: {"sitemap:read"}, 7: set()}
    assert "sitemap" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]


def test_the_screens_are_in_the_menu_and_the_existing_map_is_as_it_was():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/security-map|/site-places)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/security-map", "sitemap:read"), ("/site-places", "sitemap:manage")]
    assert "{ label: 'Site Map',       path: '/map'," in sidebar, "the existing map keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="security-map"', 'path="site-places"', 'path="map"'):
        assert path in routes
    for said in ("(`/security-map`)", "(`/site-places`)", "The existing Site Map (`/map`) is unchanged."):
        assert said in _doc()
    shared = (WEB / "components" / "securityMap" / "mapFormat.ts").read_text(encoding="utf-8")
    assert "VITE_MAP_TILE_URL" in shared and "`VITE_MAP_TILE_URL`" in _doc()
    web_layers = set(re.findall(r"'([A-Z_]+)'", (WEB / "api" / "siteMap.ts").read_text(encoding="utf-8")
                                .split("export type LayerKey =", 1)[1].split("export type PlaceKind", 1)[0]))
    assert web_layers == {layer.key for layer in maps.LAYERS}


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _doc().split("## 8. Files", 1)[1].split("## 9.", 1)[0], re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `(backend/tests/[a-z_.]+|frontend/src/[A-Za-z_/.]+)` \|",
                           _doc().split("## 9. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("---", 1)[0])
    assert changed == ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
                       "frontend/src/hooks/usePermission.ts"]
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 3 | GIS | **Built 2026-10-07**" in built and "GIS_SECURITY_ARCHITECTURE.md" in built
