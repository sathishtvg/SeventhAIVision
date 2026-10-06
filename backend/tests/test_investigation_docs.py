"""Smart investigation: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import investigations as api
from app.services import investigation_phrase as phrases
from app.services import investigation_sources as sources
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SMART_INVESTIGATION_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0143_investigations.py"
BASE = "/api/v1/investigations"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _served() -> dict[tuple[str, str], bool]:
    """(METHOD, path as the document writes it) -> whether it needs investigation:manage."""
    out = {}
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
            written = path.removeprefix(BASE).replace("{investigation_id:uuid}", "{id}").replace(
                "{item_id:uuid}", "{item_id}")
            for method in route.methods - {"HEAD"}:
                out[(method, written)] = "investigation:manage" in needs
    return out


def test_the_document_lists_every_source_with_the_permission_that_reads_it():
    rows = dict(re.findall(r"^\| `([A-Z_]+)` \|[^|]*\| `([a-z:]+)` \|", _doc(), re.M))
    assert rows == {s.kind: s.permission for s in sources.SOURCES}
    doc = _flat(_doc())
    assert f"The {_count(len(sources.SOURCES))} sources" in doc
    assert "only when asked for by name" in doc and [s.kind for s in sources.SOURCES if s.asked_for] == ["DETECTION"]
    face = sources.BY_KIND["FACE_MATCH"]
    assert face.label_permission == "watchlist:manage" and "only for someone holding `watchlist:manage`" in doc


def _count(n: int) -> str:
    return {12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen"}[n]


def test_the_document_says_what_each_source_can_be_asked_about():
    doc = _doc()
    for source in sources.SOURCES:
        row = re.search(rf"^\| `{source.kind}` \|.*$", doc, re.M).group(0)
        asked = row.rsplit("|", 2)[1].lower()
        # A plate read is always of the kind "lpr": a source with one event type
        # is not one that can usefully be asked about event types.
        varied = source.event_type and not source.event_type.startswith("'")
        for word, answers in (("camera", source.camera), ("plate", source.plate), ("risk level", source.risk),
                              ("staff", source.staff), ("severity", source.severity),
                              ("event type", varied), ("name", source.person)):
            assert (word in asked) == bool(answers), f"{source.kind}: the document and the code disagree on {word}"


def test_the_document_lists_every_route_and_which_need_more():
    table = re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \| (.*) \|$", _doc(), re.M)
    written = {(method, path): "**manage**" in what for method, path, what in table}
    assert written == _served()
    assert not {m for m, _ in _served()} & {"DELETE", "PUT", "PATCH"}
    assert "There is no `DELETE`, `PUT` or `PATCH`." in _doc()


def test_the_limits_the_document_states_are_the_codes():
    doc = _flat(_doc())
    assert (sources.MAX_DAYS, sources.DEFAULT_HOURS, sources.MAX_LIMIT, sources.TIMEOUT_MS, api.MAX_ITEMS,
            api.DEFAULT_TRAIL_DAYS) == (92, 24, 200, 8000, 500, 30)
    assert str(api.SEARCH_LIMIT) == "60 per 1 minute"
    for said in ("the last 24 hours. At most 92 days", "200 rows a page", "not answered in 8 seconds",
                 "60 searches a minute per person", "at most 500 entries", "(30 days unless given, at most 92)"):
        assert said in doc, said


def test_the_audit_actions_the_document_names_are_the_ones_written():
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(investigation\.[a-z_.]+)"', code))
    named = set(re.findall(r"`(investigation\.[a-z_.]+)`", _doc()))
    assert written == named and len(written) == 8


def test_what_can_be_typed_is_what_the_document_says_can_be():
    doc = _doc()
    section = doc.split("## 4. A typed phrase", 1)[1].split("## 5.", 1)[0]
    for example in re.findall(r"`([^`]+)`", section.split("- **what**", 1)[0].split("- **when**", 1)[1]):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 10, 7, 23, 0, tzinfo=ZoneInfo("Asia/Singapore"))
        parsed = phrases.parse(f"alerts {example}", now=now, zone="Asia/Singapore")
        assert not parsed.not_understood and not parsed.assumed, f"the document offers '{example}' and it is not read"
    assert "no language model" in _flat(section) and "refused with the words that were not" in _flat(section)
    for word in ("show", "the", "at"):
        assert word in phrases.NOISE


def test_the_document_says_who_holds_what_and_the_migration_grants_exactly_that():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(investigation:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"investigation:read": {2, 3, 4, 6, 8}, "investigation:manage": {2, 3, 4, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    assert roles == [2, 8, 3, 4, 6, 5, 7, 1]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code


def test_the_web_gives_each_role_exactly_what_the_migration_grants():
    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(investigation:[a-z]+)'"
    everything = set(re.findall(pattern, src.split("const ROLE_PERMISSIONS", 1)[0].split("const PLATFORM_PERMISSIONS")[0]))
    assert everything == {"investigation:read", "investigation:manage"}
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: everything, 4: everything, 5: set(), 6: {"investigation:read"}, 7: set()}
    platform = src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    assert "investigation" not in platform, "the platform owner is not a customer's investigator"
    for role in (2, 8):
        excluded = re.search(rf"\n  {role}: ALL_PERMISSIONS\.filter\((.*?)\)\),", table, re.S).group(1)
        assert "investigation" not in excluded


def test_the_screens_are_in_the_menu_and_the_route_table_under_the_permission():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    entries = re.findall(r"path: '(/investigat[a-z]+)',.*permission: '([a-z:]+)'", sidebar)
    assert entries == [("/investigate", "investigation:read"), ("/investigations", "investigation:read")]
    block = sidebar.split("title: 'Investigate'", 1)[1].split("title:", 1)[0]
    assert "/investigate'" in block and "/investigations'" in block, "under Investigate, where looking back lives"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="investigate"', 'path="investigations"', 'path="investigations/:id"'):
        assert path in routes
    doc = _doc()
    for said in ("(`/investigate`)", "(`/investigations`)", "(`/investigations/{id}`)"):
        assert said in doc


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _doc().split("## 9. Files", 1)[1].split("## 10.", 1)[0], re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `(backend/tests/[a-z_.]+|frontend/src/[A-Za-z_/.]+)` \|",
                           _doc().split("## 10. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend)/[A-Za-z_/.]+)`", _doc().split("Existing files changed", 1)[1].split("---", 1)[0])
    assert changed == ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
                       "frontend/src/hooks/usePermission.ts"]
    gaps = GAPS.read_text(encoding="utf-8")
    assert "## 9. As built" in gaps and "SMART_INVESTIGATION_ARCHITECTURE.md" in gaps.split("## 9. As built", 1)[1]
    assert "| 1 | Smart investigation | **Built 2026-10-06**" in gaps.split("## 9. As built", 1)[1]
