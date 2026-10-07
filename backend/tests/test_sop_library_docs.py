"""The SOP library: the document says what the code does, and the web and the phone say what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import sop as api
from app.services import sop_library as library
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SECURITY_SOP_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0148_sop_library.py"
BASE = "/api/v1/sop"
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts", "frontend/src/pages/intel/Situation.tsx",
           "mobile/src/screens/IncidentDetailScreen.tsx"]


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _routes():
    """Every route this router serves, with the permissions it asks for."""
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(BASE + "/") or not getattr(route, "endpoint", None):
                continue
            needs: set[str] = set()

            def walk(dep, found=needs):
                if "require_permission" in getattr(dep.call, "__qualname__", ""):
                    found.update(c.cell_contents for c in (dep.call.__closure__ or ())
                                 if isinstance(c.cell_contents, str))
                for sub in dep.dependencies:
                    walk(sub)

            for d in route.dependant.dependencies:
                walk(d)
            for method in route.methods - {"HEAD"}:
                yield method, re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(BASE)), frozenset(needs)


def test_nothing_composes_an_answer_and_the_document_says_so():
    for path in (Path(library.__file__), Path(api.__file__)):
        source = path.read_text(encoding="utf-8")
        for name in ("anthropic", "openai", "httpx", "requests", "llm", "langchain"):
            assert not re.search(rf"^\s*(import|from)\s+{name}\b", source, re.M), f"{path.name} imports {name}"
    reads = Path(library.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO" not in reads and "UPDATE " not in reads and "DELETE FROM" not in reads
    assert '"is_an_answer": False' in reads and '"text": r["body"]' in reads
    doc = _flat(_doc())
    for said in ("Nothing composes an answer", "No language model is involved.", "offers nothing nearer",
                 "The answer carries `is_an_answer: false`"):
        assert said in doc, said
    assert library.MAX_PASSAGES == 8 and "returns up to 8 passages" in doc
    assert "Nothing nearer was looked for." in library.NOTHING
    assert "to_tsvector('english'" in reads + MIGRATION.read_text(encoding="utf-8") and "It searches in English." in doc


def test_the_document_gives_the_states_of_a_version_and_which_one_is_in_force():
    section = _section("## 2. A procedure and its versions", "## 3.")
    assert re.findall(r"^\| `([A-Z]+)` \|", section, re.M) == ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED"]
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "'DRAFT','SUBMITTED','APPROVED','REJECTED'" in migration
    doc = _flat(section)
    assert "(`uq_sop_version_open`)" in doc and "uq_sop_version_open" in migration
    assert "Of a procedure's approved versions whose date has come, the latest version." in doc
    assert "ORDER BY v.document_id, v.version_no DESC" in library.IN_FORCE and "v.effective_from <= :now" in library.IN_FORCE
    assert "does not fall back to the version it replaced" in doc
    assert "f.effective_until IS NULL OR f.effective_until > :now" in library.STANDING and "NOT d.is_retired" in library.STANDING
    states = set(re.findall(r"`([A-Z_]+)`", doc.split("Where a procedure stands is one of", 1)[1].split(".", 1)[0]))
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert states == set(re.findall(r'return "([A-Z_]+)"', router.split("def _state(", 1)[1].split("\ndef ", 1)[0])
                         ) | {"EXPIRED", "IN_FORCE"}
    assert "SOP-{number:04d}" in router and "`SOP-0001`" in doc
    assert "with its SHA-256" in doc and "hashlib.sha256" in router
    assert "The platform does not read it" in doc


def test_an_approved_version_is_held_still_by_the_database_and_not_only_by_the_code():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TRIGGER sop_version_decided BEFORE UPDATE ON sop_versions" in migration
    assert "OLD.state IN ('APPROVED', 'REJECTED') AND pg_trigger_depth() = 1" in migration
    assert "GRANT SELECT, INSERT ON {table} TO svc_app" in migration and "GRANT ALL" not in migration
    # Passages take no UPDATE and no DELETE; a procedure and a version, only the named columns.
    assert "ON sop_passages TO" not in migration.replace("GRANT SELECT, INSERT ON {table} TO svc_app", "")
    assert 'GRANT UPDATE ({DOCUMENT_CHANGES}) ON sop_documents' in migration
    assert 'GRANT UPDATE ({VERSION_CHANGES}) ON sop_versions' in migration
    changes = migration.split("VERSION_CHANGES = (", 1)[1].split(")", 1)[0]
    for held in ("version_no", "document_id", "drafted_by_user_id", "drafted_at"):
        assert held not in changes, f"the application may not change a version's {held}"
    doc = _flat(_doc())
    assert "A trigger refuses every change to a version that has been approved or rejected" in doc
    assert "Whoever drafted a version does not approve it." in doc
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "A procedure is approved by somebody other than who wrote it." in router
    assert "UPDATE sop_passages" not in router and "DELETE FROM sop_passages" not in router
    assert "DELETE FROM sop_documents" not in router and "DELETE FROM sop_versions" not in router


def test_the_document_says_how_a_text_is_cut_into_passages():
    section = _flat(_section("## 3. Passages", "## 4."))
    assert "a line that starts with `#`, a line written in capitals, or a line on its own that ends in a colon" in section
    cut = library.passages("# Title\n\nSTEP ONE\nGo to the panel.\n\nThen:\n\n1. Read it.\n2. Radio it.\n\nA plain paragraph.")
    assert cut == [{"heading": "STEP ONE", "body": "Go to the panel."},
                   {"heading": "Then", "body": "1. Read it.\n2. Radio it."},
                   {"heading": "Then", "body": "A plain paragraph."}]
    assert "The steps of a list stay together." in section
    assert "A version with headings and no text cannot be approved." in section
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "it has headings and no text" in router and "INSERT INTO sop_passages" in router
    assert router.count("INSERT INTO sop_passages") == 1, "passages are written in one place: at approval"


def test_the_document_says_what_is_put_beside_an_incident():
    section = _flat(_section("## 5. The procedure beside an incident", "## 6."))
    assert "`fire_smoke.detected` is a `fire_smoke`" in section
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "split_part(i.alert_code, '.', 1)" in router and "split_part(e.event_type, '.', 1)" in router
    assert "its site's own first, then the ones for every site" in section
    assert "ORDER BY (d.site_id IS NULL), d.code" in Path(library.__file__).read_text(encoding="utf-8")
    assert "An incident raised by hand has no kind" in section and "raised by hand and has no kind" in router
    assert "`sop_incident_types`" in section


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    served = {(method, path): needs - {"sop:read"} for method, path, needs in _routes()}
    assert all("sop:read" in needs for _, _, needs in _routes()), "every route needs sop:read"
    section = _section("## 6. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                   section, re.M)}
    assert table == served and len(served) == 20
    assert "There is no `DELETE`." in section and not [m for m, _ in served if m == "DELETE"]
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(sop\.[a-z_.]+)"', code))
    named = set(re.findall(r"`(sop\.[a-z_.]+)`", section))
    assert named == written and len(named) == 11


def test_the_document_says_who_holds_what_and_the_migration_the_web_and_the_phone_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(sop:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"sop:read": {2, 3, 4, 5, 6, 8}, "sop:write": {2, 3, 8}, "sop:approve": {2, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(sop:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(granted)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: {"sop:read", "sop:write"}, 4: {"sop:read"}, 5: {"sop:read"}, 6: {"sop:read"}, 7: set()}
    assert "sop:" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    card = (PHONE / "components" / "ProcedureCard.tsx").read_text(encoding="utf-8")
    assert "canSee({ permission: 'sop:read' }" in card


def test_the_screens_are_in_the_menu_and_post_orders_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/sop-library)',.*permission: '([a-z:]+)'", sidebar) == [("/sop-library", "sop:read")]
    assert "{ label: 'SOP',              path: '/post-orders'," in sidebar, "the existing screen keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="sop-library"', 'path="post-orders"'):
        assert path in routes
    for said in ("(`/sop-library`)", "The existing SOP screen (`/post-orders`) is unchanged."):
        assert said in _doc()
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "post_orders" not in migration.split("def upgrade", 1)[1], "nothing of post orders is altered"
    existing = (REPO_ROOT / "backend" / "app" / "routers" / "post_orders.py").read_text(encoding="utf-8")
    assert "sop_" not in existing
    page = (WEB / "pages" / "sop" / "SopLibrary.tsx").read_text(encoding="utf-8")
    assert "{ask.data.note}" in page and "{ask.data.nothing}" in page, "the screen says it is not an answer"
    client = (WEB / "api" / "sop.ts").read_text(encoding="utf-8")
    states = set(re.findall(r"'([A-Z_]+)'", client.split("export type VersionState =", 1)[1].split("\n", 1)[0]))
    assert states == {"DRAFT", "SUBMITTED", "APPROVED", "REJECTED"}
    desk = (WEB / "components" / "response" / "ResponseDialogs.tsx").read_text(encoding="utf-8")
    assert "<ProcedureForIncident incidentId={incident.id} />" in desk
    situation = (WEB / "pages" / "intel" / "Situation.tsx").read_text(encoding="utf-8")
    assert "<ProcedureForSituation situationId={situation.id} />" in situation
    shown = (WEB / "components" / "sop" / "SopDialogs.tsx").read_text(encoding="utf-8")
    assert "It is the organisation's procedure, not a recommendation." in shown


def test_the_phone_calls_routes_that_exist_and_presents_no_answer_of_its_own():
    calls = (PHONE / "api" / "sop.ts").read_text(encoding="utf-8")
    assert "const BASE = '/api/v1/sop'" in calls
    served = {(method, path) for method, path, _ in _routes()}
    assert ("GET", "/for-incident/{id}") in served and "`${BASE}/for-incident/${incidentId}`" in calls
    assert ("POST", "/ask") in served and "`${BASE}/ask`" in calls
    assert "/versions" not in calls and "/documents" not in calls, "a phone reads procedures; it does not write them"
    rules = (PHONE / "lib" / "procedures.ts").read_text(encoding="utf-8")
    assert "Nothing was written in answer." in rules
    card = (PHONE / "components" / "ProcedureCard.tsx").read_text(encoding="utf-8")
    assert "ask.data.nothing ?? NOT_AN_ANSWER" in card and "data.why_none" in card
    screen = (PHONE / "screens" / "IncidentDetailScreen.tsx").read_text(encoding="utf-8")
    assert "<ProcedureCard incidentId={params.incidentId} />" in screen


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 8. Files", "## 9."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src|mobile/src)/[A-Za-z_/.]+)` \|",
                           _doc().split("## 9. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("The document as issued is stored", 1)[0])
    assert changed == CHANGED
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "settings.EMPLOYEE_DOCS_ROOT" in router and "(`EMPLOYEE_DOCS_ROOT`)" in _doc()
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 6 | SOP | **Built 2026-10-07**" in built and "SECURITY_SOP_ARCHITECTURE.md" in built
    phase = built.split("### Phase 6", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 6, by additions only:**", 1)[1]
                      .split("Post orders", 1)[0]) == CHANGED
