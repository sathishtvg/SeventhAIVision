"""Evidence packages: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import evidence_packages as api
from app.services import evidence_packages as packages
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "EVIDENCE_CHAIN_OF_CUSTODY.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
APP = REPO_ROOT / "backend" / "app"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0144_evidence_packages.py"
PREFIXES = ("/api/v1/evidence-packages", "/api/v1/evidence-holds")
OWN = {"evidence:package:read", "evidence:package:manage", "evidence:package:export", "evidence:hold:manage"}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _served() -> dict[tuple[str, str], str]:
    """(METHOD, path as the document writes it) -> the further permission it needs, or ''."""
    out = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(PREFIXES) or not getattr(route, "endpoint", None):
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
            assert "evidence:package:read" in needs, path
            more = needs - {"evidence:package:read"}
            assert len(more) <= 1, (path, more)
            written = (path.removeprefix("/api/v1").replace("{package_id:uuid}", "{id}")
                       .replace("{hold_id:uuid}", "{id}").replace("{item_id:uuid}", "{item_id}"))
            for method in route.methods - {"HEAD"}:
                out[(method, written)] = next(iter(more), "")
    return out


def test_the_document_lists_every_kind_with_its_permission_and_the_purge_that_deletes_it():
    rows = re.findall(r"^\| `([A-Z_]+)` \| `([a-z_]+)`[^|]*\| `([a-z:]+)` \| `([a-z_.]+)` \|$", _doc(), re.M)
    assert {kind: needs for kind, _, needs, _ in rows} == packages.NEEDS
    tables = {"SNAPSHOT": "evidence", "CLIP": "evidence", "RECORDING": "recordings",
              "DRONE_MEDIA": "drone_event_media"}
    assert {kind: table for kind, table, _, _ in rows} == tables
    for kind, _, _, purge in rows:
        module, function = purge.rsplit(".", 1)
        path = APP / ("scheduler_main.py" if module == "scheduler_main" else f"services/{module}.py")
        code = path.read_text(encoding="utf-8")
        assert f"async def {function}(" in code, purge
        body = code.split(f"async def {function}(", 1)[1].split("\nasync def ", 1)[0]
        assert "not_held(" in body, f"{purge} deletes a {kind} without asking whether it is held"
    assert "AND NOT EXISTS (SELECT 1 FROM evidence_holds held" in _doc()


def test_the_document_lists_every_route_and_what_each_needs():
    table = re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \| ?(?:`([a-z:]+)`)? ?\|$", _doc(), re.M)
    written = {(method, path): needs for method, path, needs in table}
    assert written == _served()
    assert [m for m, _ in _served() if m in ("PUT", "PATCH")] == []
    assert [key for key in _served() if key[0] == "DELETE"] == [("DELETE", "/evidence-packages/{id}/items/{item_id}")]
    assert "The one `DELETE` takes an item out of a draft." in _doc()


def test_the_limits_the_document_states_are_the_codes():
    doc = _flat(_doc())
    assert (packages.MAX_ITEMS, packages.MAX_EXPORT_BYTES, packages.MANIFEST_FORMAT) == (
        200, 1024 * 1024 * 1024, "seventh-evidence-manifest/1")
    code = Path(api.__file__).read_text(encoding="utf-8")
    assert 'paced("6/minute", "evidence-export"' in code and api.VIEW_EVERY.total_seconds() == 3600
    for said in ("at most 200 items", "past 1024 MB (`EVIDENCE_EXPORT_MAX_MB`)", "Six exports a minute per person",
                 "once per person per hour", "(`seventh-evidence-manifest/1`)"):
        assert said in doc, said


def test_the_steps_of_custody_the_document_names_are_the_ones_there_are():
    migration = MIGRATION.read_text(encoding="utf-8")
    recorded = set(re.findall(r"'([A-Z_]+)'", re.search(r'^STEPS = "(.*)"$', migration, re.M).group(1)))
    section = _doc().split("## 5. The chain of custody", 1)[1].split("## 6.", 1)[0]
    rows = re.findall(r"^\| (`[A-Z`, ]+`) \| (.*) \|$", section, re.M)
    named = {step: where for steps, where in rows for step in re.findall(r"`([A-Z]+)`", steps)}
    assert set(named) == recorded | {"CAPTURED", "ACCESSED"}
    assert "evidence_access_log" in named["ACCESSED"] and "evidence_custody_events" in named["SEALED"]
    assert "The item itself" in named["CAPTURED"]
    web = (WEB / "api" / "evidencePackages.ts").read_text(encoding="utf-8")
    typed = set(re.findall(r"'([A-Z]+)'", web.split("export type CustodyStep =", 1)[1].split("\n\n", 1)[0]))
    assert typed == set(named), "the web names a step the server does not, or the reverse"


def test_the_audit_actions_the_document_names_are_the_ones_written():
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(evidence\.[a-z_.]+)"', code))
    named = set(re.findall(r"`(evidence\.[a-z_.]+)`", _doc().split("**Audit.**", 1)[1].split("---", 1)[0]))
    assert written == named and len(written) == 10


def test_the_document_says_who_holds_what_and_the_migration_grants_exactly_that():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(evidence:[a-z:]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"evidence:package:read": {2, 3, 4, 6, 8}, "evidence:package:manage": {2, 3, 4, 8},
                       "evidence:package:export": {2, 3, 8}, "evidence:hold:manage": {2, 8}}
    assert set(granted) == OWN
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    assert roles == [2, 8, 3, 4, 6, 5, 7, 1]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code


def test_the_web_gives_each_role_exactly_what_the_migration_grants():
    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(evidence:package:[a-z]+|evidence:hold:manage)'"
    everything = set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0]))
    assert everything == OWN
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: OWN - {"evidence:hold:manage"}, 4: {"evidence:package:read", "evidence:package:manage"},
                   5: set(), 6: {"evidence:package:read"}, 7: set()}
    platform = src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    assert "evidence:package" not in platform and "evidence:hold" not in platform
    for role in (2, 8):
        excluded = re.search(rf"\n  {role}: ALL_PERMISSIONS\.filter\((.*?)\)\),", table, re.S).group(1)
        assert "evidence:" not in excluded


def test_the_screens_are_in_the_menu_and_the_route_table():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/evidence-[a-z]+)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/evidence-packages", "evidence:package:read")]
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="evidence-packages"', 'path="evidence-packages/:id"', 'path="evidence-holds"'):
        assert path in routes
    nav = (WEB / "pages" / "investigations" / "InvestigationNav.tsx").read_text(encoding="utf-8")
    assert "'/evidence-packages'" in nav and "'/evidence-holds'" in nav and "evidence:package:read" in nav
    for said in ("(`/evidence-packages`)", "(`/evidence-packages/{id}`)", "(`/evidence-holds`)"):
        assert said in _doc()


def test_the_web_is_never_told_where_a_file_is():
    for path in ((WEB / "api" / "evidencePackages.ts"), *(WEB / "pages" / "evidencePackages").glob("*.tsx"),
                 *(WEB / "components" / "evidence").glob("*.ts*")):
        code = path.read_text(encoding="utf-8")
        assert "storage_path" not in code and "file_path" not in code, path.name
    service = (APP / "services" / "evidence_packages.py").read_text(encoding="utf-8")
    assert service.count("def public(") == 1 and '"_path": path' in service
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "packages.public(" in router and "storage_path" not in router


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _doc().split("## 9. Files", 1)[1].split("## 10.", 1)[0], re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `(backend/tests/[a-z_.]+|frontend/src/[A-Za-z_/.]+)` \|",
                           _doc().split("## 10. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("---", 1)[0])
    assert changed == ["backend/app/main.py", "backend/app/scheduler_main.py",
                       "backend/app/services/continuous_recording.py", "backend/app/services/drone_retention.py",
                       "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
                       "frontend/src/hooks/usePermission.ts"]
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 2 | Evidence and custody | **Built 2026-10-07**" in built and "EVIDENCE_CHAIN_OF_CUSTODY.md" in built
