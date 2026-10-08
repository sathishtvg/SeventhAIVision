"""Security cases: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.main import app
from app.routers import cases as api
from app.services import case_files
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SECURITY_CASE_MANAGEMENT.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0155_case_files.py"
BASE = "/api/v1/cases"
CODES = ("case:read", "case:work", "case:manage")
TABLES = ["case_files", "case_investigators", "case_tasks", "case_entries", "case_links", "case_parties"]
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts"]


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _code(path) -> str:
    """A module without its opening description."""
    return Path(path).read_text(encoding="utf-8").split('"""', 2)[2]


def _routes(base: str):
    """Every route under a prefix, with the permissions it asks for."""
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not (path == base or path.startswith(base + "/")) or not getattr(route, "endpoint", None):
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
                shown = re.sub(r"\{([a-z]+)_id:uuid\}", lambda m: "{id}" if m.group(1) == "case" else "{" + m.group(1) + "}",
                               path.removeprefix(base)) or "/"
                yield method, shown, frozenset(needs)


def test_the_document_gives_the_states_of_a_case_and_who_may_do_what():
    section = _section("## 2. A case", "## 3.")
    assert re.findall(r"^\| `([A-Z_]+)` \| [A-Z]", section, re.M)[:3] == list(case_files.STATUSES)
    flat = _flat(section)
    assert "(`CASE-0001`, numbered for each organisation)" in flat and 'f"CASE-{n:04d}"' in _code(case_files.__file__)
    assert "hashtext('case:' || current_setting('app.current_tenant'))" in _code(case_files.__file__)
    kinds = re.findall(r"`([A-Z]+)`", flat.split("Its kind is one of ", 1)[1].split(".", 1)[0])
    assert kinds == list(case_files.CATEGORIES)
    assert "There is none for a member of staff's conduct: that is not a security case." in flat
    # What follows what is what may() allows.
    me, other = uuid.uuid4(), uuid.uuid4()
    manager = frozenset(CODES)
    case = {"status": "OPEN", "lead_user_id": me, "close_requested_by_user_id": None}
    assert case_files.may(case, me, manager, True)["request_close"]
    waiting = {**case, "status": "AWAITING_APPROVAL", "close_requested_by_user_id": me}
    assert not case_files.may(waiting, me, manager, True)["approve_close"] and case_files.may(waiting, other, manager, False)["approve_close"]
    assert not case_files.may(waiting, me, manager, True)["work"], "nothing is added while it waits"
    assert case_files.may({**case, "status": "CLOSED"}, me, manager, True) == {
        "work": False, "assign": False, "request_close": False, "approve_close": False, "decline_close": False, "reopen": True}
    assert "`approve-close` is for somebody who manages cases and did not ask." in flat
    # A person works on a case they are on.
    worker = frozenset({"case:read", "case:work"})
    assert case_files.may(case, me, worker, True)["work"] and not case_files.may(case, other, worker, False)["work"]
    assert "from the people who may work on cases (`case:work`)" in flat
    router = _code(api.__file__)
    assert "p.code = 'case:work'" in router and "That person cannot be put on a case" in router
    # Opening.
    assert "Whoever opens a case leads it, unless somebody who manages cases names another lead." in flat
    assert "Naming somebody else to lead a case is for whoever manages cases." in router
    assert "opens a case at one of them, not one that spans every site" in flat and "Choose the site this case is about" in router
    # Tasks, and closing.
    assert "A task ends `DONE`, with what was done, or `DROPPED`, with why." in flat and case_files.TASK_STATES == ("OPEN", "DONE", "DROPPED")
    assert "A case is not asked to be closed while a task is still `OPEN`." in flat
    assert "still open. Finish or drop" in router and "state = 'OPEN'" in router
    assert "what was written as found stays in the history" in flat
    assert '_entry(db, case_id, "CLOSE_REQUESTED", outcome, token.user_id)' in router
    # The report.
    assert "(`/report`)" in flat and "(`/report.pdf`)" in flat and "a support session takes none" in flat
    assert "A case's report is taken by the organisation's own staff, not from a support session." in router
    assert "A record this reader may not read" in _code(case_files.__file__)


def test_a_case_refers_to_what_it_is_about_and_the_document_says_what_a_reader_sees_of_a_link():
    section = _section("**Linked records**", "**People and vehicles named in it**")
    rows = dict(re.findall(r"^\| `([A-Z_]+)` \| `([a-z:]+)` \|$", section, re.M))
    assert rows == case_files.LINK_NEEDS and list(rows) == list(case_files.LINK_KINDS)
    flat = _flat(section)
    assert "(`services/case_files.py`)" in flat
    assert re.findall(r"`([A-Z_]+)`", flat.split("Each link is shown to a reader as ", 1)[1]) == ["SHOWN", "NOT_PERMITTED", "NOT_AVAILABLE"]
    service = _code(case_files.__file__)
    assert '"SHOWN" if found else ("NOT_PERMITTED" if LINK_NEEDS[x["kind"]] not in held else "NOT_AVAILABLE")' in service
    # Which record it is, is said only to somebody who may read it.
    assert '"ref_id": x["ref_id"] if found else None' in service and '"label": found["label"] if found else None' in service
    doc = _flat(_doc())
    for said in ("A case refers to what it is about; it copies none of it.",
                 "Taking a link off a case removes nothing of theirs.",
                 "A link to a record the reader may not read says that it is one, and what kind, and nothing else — no title, no detail, no id."):
        assert said in doc, said
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    # No foreign key reaches into the records a case is about.
    assert set(re.findall(r"REFERENCES (\w+)\(", upgrade)) == {"tenants", "sites", "users", "case_files"}
    assert "they refer to `sites` and `users`, and to no table of the records a case is about — a link is a kind and an id" in doc
    router = _code(api.__file__)
    for table in ("incidents", "investigations", "evidence_packages"):
        assert not re.search(rf"(INSERT INTO|UPDATE|DELETE FROM)\s+{table}\b", router + service), table
    not_done = _flat(_doc().split("## 7. What this does not do", 1)[1])
    assert "Closing a case does not resolve its incident, close its investigation or seal its evidence package." in not_done
    # The screen shows a link the reader may not read as that.
    words = (WEB / "components" / "cases" / "caseFormat.ts").read_text(encoding="utf-8")
    assert "you may not read — it is read under ${x.needs}" in words and "at a site you are not shown, or no longer there" in words
    dialogs = (WEB / "components" / "cases" / "CaseDialogs.tsx").read_text(encoding="utf-8")
    assert "Referred to, not copied: each stays where it is, under its own permission." in dialogs


def test_closing_takes_two_a_closed_case_is_held_still_and_nothing_is_removed():
    doc = _flat(_doc())
    migration = MIGRATION.read_text(encoding="utf-8")
    router = _code(api.__file__)
    for said in ("Closing takes two people.", "A closed case is not changed.", "Nothing is removed.",
                 "A case has its own history.", "the application's role may read and add, and nothing else",
                 "not an API key, not a support session", "Nobody is told."):
        assert said in doc, said
    assert case_files.TWO_PEOPLE == "Closing a case takes two people: whoever asked for it to be closed does not approve it."
    assert "CONSTRAINT ck_case_two" in migration and "closed_by_user_id <> close_requested_by_user_id" in migration
    assert "raise HTTPException(409, case_files.TWO_PEOPLE)" in router
    assert "CREATE TRIGGER case_file_closed BEFORE UPDATE ON case_files" in migration
    assert "OLD.status = 'CLOSED' AND NEW.status <> 'OPEN' AND pg_trigger_depth() = 1" in migration
    assert 'for table in ("case_investigators", "case_tasks", "case_links", "case_parties"):' in migration
    assert "CREATE TRIGGER case_entries_closed BEFORE INSERT ON case_entries" in migration and "NEW.kind = 'NOTE'" in migration
    # The history is written before the case is closed, since a closed case takes no more of it.
    approve = router.split("async def approve_close", 1)[1].split("@router.post", 1)[0]
    assert approve.index('"CLOSE_APPROVED"') < approve.index("SET status = 'CLOSED'")
    # Grants: read and add; change only what a step changes; the history, nothing.
    changes = migration.split("CHANGES = {", 1)[1].split("\n}", 1)[0]
    assert '"case_entries"' not in changes
    assert re.search(r'"case_links": \("removed_at", "removed_by_user_id", "remove_reason"\)', changes)
    for held in ("case_number", "site_id", "opened_at", "opened_by_user_id"):
        assert f'"{held}"' not in changes.split('"case_investigators"', 1)[0], held
    assert 'op.execute(f"GRANT SELECT, INSERT ON {table} TO svc_app")' in migration
    assert "DELETE FROM" not in router and not [m for m, _, _ in _routes(BASE) if m == "DELETE"]
    assert set(case_files.ENTRY_KINDS) == set(re.findall(r"'([A-Z_]+)'", migration.split("CONSTRAINT ck_caseentry_kind CHECK (kind IN (", 1)[1].split("))", 1)[0]))
    # Every step is a person's; nobody is told.
    for name in ("open_case", "change_case", "set_lead", "add_investigator", "remove_investigator", "add_note", "add_task",
                 "add_link", "add_party", "request_close", "approve_close", "decline_close", "reopen_case"):
        body = router.split(f"async def {name}", 1)[1].split("@router.", 1)[0]
        assert "_a_person(token)" in body or "_working(" in body, name
    assert "_a_person(token)" in router.split("async def _working", 1)[1].split("async def _may_work", 1)[0]
    assert "_a_person(token)" in router.split("async def _finish", 1)[1].split("@router.post", 1)[0]
    for word in ("redis", "response_notify", "send_expo_push", "smtp", "webhook"):
        assert word not in router.lower() and word not in _code(case_files.__file__).lower(), word
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert "case_files" not in scheduler, "no case is opened, closed or chased by a scheduler"
    # The screen offers an act only when the server does, and says the two rules beside what they govern.
    dialogs = (WEB / "components" / "cases" / "CaseDialogs.tsx").read_text(encoding="utf-8")
    for key in ("work", "assign", "request_close", "approve_close", "decline_close", "reopen"):
        assert f"c.may.{key}" in dialogs, f"the {key} button is the server's to offer"
    assert "{c.two_people_note}" in dialogs and "t.may_finish" in dialogs and "usePermission" not in dialogs
    assert "You asked for it to be closed, so somebody else approves." in dialogs


def test_being_named_in_a_case_is_not_an_accusation():
    doc = _flat(_doc())
    assert "Being named in a case is not an accusation." in doc
    assert "There is no way to record anybody as a suspect, and nothing here names anybody by itself." in doc
    named = _flat(_section("**People and vehicles named in it**", "**Closing.**"))
    assert re.findall(r"`([A-Z_]+)`", named) == list(case_files.CONNECTIONS)
    migration = MIGRATION.read_text(encoding="utf-8")
    allowed = re.findall(r"'([A-Z_]+)'", migration.split("CONSTRAINT ck_caseparty_connection CHECK (connection IN (", 1)[1].split("))", 1)[0])
    assert allowed == list(case_files.CONNECTIONS)
    accusing = re.compile(r"suspect|offender|culprit|perpetrator|accused|guilty|intruder", re.I)
    for text in (*case_files.CONNECTION_LABEL.values(), *case_files.ENTRY_WORDS.values(), *case_files.CATEGORY_LABEL.values()):
        assert not accusing.search(text), text
    assert case_files.PARTY_NOTE.startswith("Being named in a case is not an accusation.")
    router = _code(api.__file__)
    assert '"party_note": case_files.PARTY_NOTE' in router
    # Nothing names anybody: a name comes in from a person's request and nowhere else.
    assert router.count("INSERT INTO case_parties") == 1 and "INSERT INTO case_parties" not in _code(case_files.__file__)
    for module in ("intel_pipeline.py", "intel_correlation.py", "visitor_authorization.py"):
        assert "case_parties" not in (REPO_ROOT / "backend" / "app" / "services" / module).read_text(encoding="utf-8"), module
    not_done = _flat(_doc().split("## 7. What this does not do", 1)[1])
    for said in ("It does not open a case by itself.", "It names nobody by itself", "A name is text.",
                 "It tells nobody.", "A situation is not linked."):
        assert said in not_done, said
    dialogs = (WEB / "components" / "cases" / "CaseDialogs.tsx").read_text(encoding="utf-8")
    assert "{c.party_note}" in dialogs and not accusing.search(dialogs)
    assert "(options?.connections ?? []).map" in dialogs, "how somebody is connected is chosen from the server's words"


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    section = _section("## 3. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$", section, re.M)}
    served = {(method, path): needs - {"case:read"} for method, path, needs in _routes(BASE)}
    assert all("case:read" in needs for _, _, needs in _routes(BASE)), "every route needs case:read"
    assert table == served and len(served) == 22
    assert "There is no `DELETE`." in section
    assert "Holding `case:work` is not enough to work on a case" in _flat(section)
    router = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(case\.[a-z_.]+)"', router)) | {f"case.task.{how}" for how in ("done", "drop")}
    written.discard("case.task.")
    assert written == set(re.findall(r"`(case\.[a-z_.]+)`", section)) and len(written) == 18
    assert "f\"case.task.{'done' if state == 'DONE' else 'drop'}\"" in router


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(case:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"case:read": {2, 3, 4, 6, 8}, "case:work": {2, 3, 4, 8}, "case:manage": {2, 3, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert "Super Admin, a guard and the client role hold none of the new permissions." in _flat(_doc())
    assert api.PERMISSIONS == CODES

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(case:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screen_is_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/cases)',.*permission: '([a-z:]+)'", sidebar) == [("/cases", "case:read")]
    assert sidebar.index("title: 'Investigate'") < sidebar.index("path: '/cases'") < sidebar.index("title: 'Guard Operations'")
    for kept in ("path: '/incidents',", "path: '/investigations',", "path: '/evidence-packages',", "path: '/investigate',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="cases"', 'path="incidents"', 'path="investigations"', 'path="evidence-packages"'):
        assert path in routes
    doc = _flat(_doc())
    assert "**Cases** (`/cases`), under Investigate" in doc and "A button is there only when the server offers the act." in doc
    assert "The phone is not changed in this phase." in doc
    assert ("The existing incident (`/incidents`), investigation (`/investigations`) and evidence package "
            "(`/evidence-packages`) screens are unchanged.") in doc
    page = (WEB / "pages" / "cases" / "Cases.tsx").read_text(encoding="utf-8")
    assert "data?.can_open" in page and "usePermission" not in page, "whether somebody may open a case is the server's to say"
    client = (WEB / "api" / "cases.ts").read_text(encoding="utf-8")
    for name, values in (("Status", case_files.STATUSES), ("Category", case_files.CATEGORIES), ("Priority", case_files.PRIORITIES),
                         ("LinkKind", case_files.LINK_KINDS), ("PartyKind", case_files.PARTY_KINDS),
                         ("Connection", case_files.CONNECTIONS)):
        found = set(re.findall(r"'([A-Z_]+)'", client.split(f"export type {name} =", 1)[1].split("\n", 1)[0]))
        assert found == set(values), name
    assert set(re.findall(r"'([A-Z_]+)'", client.split("export type LinkState =", 1)[1].split("\n", 1)[0])) == {
        "SHOWN", "NOT_PERMITTED", "NOT_AVAILABLE"}
    routers = REPO_ROOT / "backend" / "app" / "routers"
    for name in ("incidents.py", "investigations.py", "evidence_packages.py"):
        source = (routers / name).read_text(encoding="utf-8")
        assert "case_files" not in source and "case_links" not in source, name
    mobile = REPO_ROOT / "mobile" / "src"
    assert not list(mobile.rglob("*ases.ts*")) and not list(mobile.rglob("*aseFile*"))


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 5. Files", "## 6."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src)/[A-Za-z_/.]+)` \|", _doc().split("## 6. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("No existing table is altered", 1)[0])
    assert changed == CHANGED
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == TABLES
    named = re.findall(r"`(case_[a-z]+)`", _flat(_doc()).split("The six tables are ", 1)[1].split(";", 1)[0])
    assert named == TABLES
    assert set(re.findall(r"ALTER TABLE (\S+)", upgrade)) == {"{table}"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade)
    assert "The tables are not named `security_cases…`, as the plan had them" in _flat(_doc())
    not_done = _flat(_doc().split("## 7. What this does not do", 1)[1])
    for said in ("It has no deadlines of its own.", "A task cannot be edited or given to somebody else.",
                 "Its report is a PDF and a reading.", "The phone is not part of it.", "It has run on test data only."):
        assert said in not_done, said
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 12 | Case management | **Built 2026-10-08** — migration `0155`" in built and "SECURITY_CASE_MANAGEMENT.md" in built
    phase = built.split("### Phase 12", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 12, by additions only:**", 1)[1]
                      .split("Incidents, investigations, evidence", 1)[0]) == CHANGED
    for said in ("no case is opened by a rule", "there is no word for a suspect", "nobody is told"):
        assert said in _flat(phase), said
