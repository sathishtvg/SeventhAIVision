"""Visitor authorisation: the document says what the code does, and the web and the phone say what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.main import app
from app.routers import visitor_authorizations as api
from app.services import visitor_authorization as authorisation
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "VISITOR_CONTRACTOR_SECURITY.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0149_visitor_authorizations.py"
BASE = "/api/v1/visitor-authorizations"
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts", "mobile/src/screens/DashboardScreen.tsx",
           "mobile/src/screens/VisitorsScreen.tsx"]


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
            if not (path == BASE or path.startswith(BASE + "/")) or not getattr(route, "endpoint", None):
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
                yield method, re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(BASE)) or "/", frozenset(needs)


def test_an_authorisation_informs_and_the_document_and_the_code_both_say_so():
    doc = _flat(_doc())
    for said in ("An authorisation informs; it admits nobody and refuses nobody.", "Nothing accuses anybody.",
                 "What is not known is said to be not known.", "A visitor's ID number is not taken.",
                 "It raises no alert and no incident, it is handed to the intelligence layer only when the "
                 "organisation asks for that"):
        assert said in doc, said
    assert "does not check anybody in" in authorisation.GATE_NOTE and "not a finding" in authorisation.MOVEMENT_NOTE
    for path in (Path(api.__file__), Path(authorisation.__file__)):
        code = path.read_text(encoding="utf-8").split('"""', 2)[2]
        # Nothing here writes a visit, a check-in, a permit, a door event, an alert, an incident or an event of the layer.
        for table in ("visitors", "visitor_logs", "work_permits", "access_events", "alerts", "incidents", "security_events"):
            assert not re.search(rf"(INSERT INTO|UPDATE|DELETE FROM)\s+{table}\b", code), (path.name, table)
        for word in ("unauthor", "intruder", "trespass", "suspect", "violat"):
            assert word not in code.lower(), (path.name, word)
        assert "id_number" not in code, "the number on a visitor's ID is not read"
    reads = Path(authorisation.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", reads), "the module only reads"
    existing = (REPO_ROOT / "backend" / "app" / "routers" / "visitors.py").read_text(encoding="utf-8")
    assert "visitor_authorization" not in existing, "check-in does not look at an authorisation"
    assert "The existing visitor screen (`/visitor-prereg`) and contractor screen (`/contractors`) are unchanged." in doc


def test_the_document_gives_the_states_and_the_standings_the_code_has():
    section = _section("## 2. An authorisation and where it stands", "## 3.")
    states, standings = section.split("Where it **stands**", 1)
    assert re.findall(r"^\| `([A-Z_]+)` \|", states, re.M) == list(authorisation.STATES)
    assert set(re.findall(r"^\| `([A-Z_]+)` \|", standings, re.M)) == set(authorisation.STANDINGS)
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "'REQUESTED','APPROVED','DECLINED','CANCELLED'" in migration
    doc = _flat(section)
    assert "worked out when it is read and stored nowhere" in doc and "standing" not in migration.split("def upgrade", 1)[1]
    assert "there is no job" in doc
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert "visitor_authoriz" not in scheduler, "nothing runs to make one lapse"
    assert "(`uq_visauth_visitor`, `uq_visauth_permit`)" in doc
    for index in ("uq_visauth_visitor", "uq_visauth_permit"):
        assert re.search(rf"CREATE UNIQUE INDEX {index} .*\n.*state = 'REQUESTED'", migration), index
    # What the document says of each standing is what the code works out.
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    hour = timedelta(hours=1)
    period = {"valid_from": now - hour, "valid_until": now + hour}
    assert authorisation.standing(None, now) == "NOT_ASKED"
    assert authorisation.standing({"state": "REQUESTED", **period}, now) == "AWAITING_HOST"
    assert authorisation.standing({"state": "REQUESTED", **period}, now + 2 * hour) == "LAPSED"
    assert authorisation.standing({"state": "APPROVED", **period}, now - 2 * hour) == "NOT_YET_VALID"
    assert authorisation.standing({"state": "APPROVED", **period}, now) == "VALID"
    assert authorisation.standing({"state": "APPROVED", **period}, now + 2 * hour) == "EXPIRED"
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "one that lapsed is withdrawn by asking again" in doc and "WITHDRAWN_BY_ASKING_AGAIN" in router
    assert "The newest authorisation of a visit is the one the gate reads." in doc
    assert "ORDER BY a.requested_at DESC, a.id DESC LIMIT 1" in router


def test_the_application_changes_only_what_a_person_decides_and_removes_nothing():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT SELECT, INSERT ON {table} TO svc_app" in migration and "GRANT ALL" not in migration
    assert 'GRANT UPDATE ({CHANGES}) ON visitor_authorizations TO svc_app' in migration
    changes = migration.split("CHANGES = (", 1)[1].split(")", 1)[0]
    for held in ("visitor_id", "work_permit_id", "site_id", "requested_by_user_id", "requested_at", "valid_from",
                 "host_user_id", "tenant_id"):
        assert not re.search(rf"\b{held}\b", changes), f"the application may not change an authorisation's {held}"
    assert "GRANT DELETE ON visitor_authorization_places TO svc_app" in migration
    assert migration.count("GRANT DELETE") == 1 and "visitor_movement_reviews TO" not in migration
    doc = _flat(_section("## 2. An authorisation and where it stands", "## 3."))
    assert "not whose visit it is, which site, who asked or when, or when it starts. It deletes none." in doc
    assert "(`visitor_authorization_places`) are a list that is set afresh" in doc
    assert "(`visitor_movement_reviews`) takes no update and no delete" in doc
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM visitor_authorizations" not in router and "UPDATE visitor_movement_reviews" not in router
    assert "DELETE FROM visitor_movement_reviews" not in router
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["visitor_authorizations", "visitor_authorization_places",
                                                          "visitor_movement_reviews"]
    assert "ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in upgrade
    assert set(re.findall(r"ALTER TABLE (\S+)", upgrade)) == {"{table}"}, "no existing table is altered"
    assert "No existing table is altered." in _doc()
    for table in ("visitors", "work_permits", "site_places", "access_events", "sites", "users"):
        assert f"REFERENCES {table}(id)" in upgrade and f"`{table}`" in _doc(), table


def test_the_document_says_who_answers_and_what_asking_takes_from_the_visit():
    section = _flat(_section("## 3. Asking, and the answer", "## 4."))
    router = Path(api.__file__).read_text(encoding="utf-8")
    for column in ("host_user_id", "expected_from", "expected_until", "start_at", "end_at"):
        assert f"`{column}`" in section and column in router.split("async def ask(", 1)[1].split("\n@router", 1)[0], column
    assert "(`visitor_authorization_requested`)" in section and "(`visitor_authorization_decided`)" in section
    assert api.REQUESTED_EVENT == "visitor_authorization_requested" and api.DECIDED_EVENT == "visitor_authorization_decided"
    assert "The host, or somebody who manages visits. Not once the period asked for has passed" in section
    assert "The time it was asked for has passed. It has to be asked for again." in router
    assert "while it is unanswered — whoever asked" in section and 'asked and row["state"] == "REQUESTED"' in router
    assert "Only an approved one, only the newest, and only to later than it runs out now" in section
    assert 'not row["is_latest"]' in router and 'body.valid_until <= row["valid_until"]' in router
    assert "Each of decline, cancel and extend says why. Approving checks nobody in." in section
    assert router.count("class ReasonBody") == 1 and "reason: str = Field(..., min_length=1" in router
    # Every act is a person's.
    handlers = {chunk.split("(", 1)[0]: chunk for chunk in router.split("\nasync def ")[1:]}
    for act in ("ask", "_decide", "cancel", "extend", "_open", "review_movement"):
        assert "_a_person(token)" in handlers[act], act
    for through in ("approve", "decline"):
        assert "await _decide(" in handlers[through], through
    for through in ("set_places", "set_escort", "id_seen"):
        assert "await _open(" in handlers[through], through
    assert "not an API key, not a support session" in _flat(_doc())


def test_the_document_shows_what_the_gate_reads_as_the_code_writes_it():
    section = _section("## 4. What the gate reads", "## 5.")
    shown = section.split("```", 2)[1].strip().splitlines()
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    record = {"state": "APPROVED", "valid_from": now - timedelta(hours=1), "valid_until": now + timedelta(hours=5),
              "requested_at": now, "host_name": "Tan Wei Ming", "decided_by_name": "Tan Wei Ming", "escort_required": True,
              "escort_name": "Kumar Raj", "escort_note": None, "id_document_kind": "Work pass",
              "id_checked_by_name": "Ong Bee Lian"}
    assert authorisation.says(record, now, places=["Block A"], timezone="Asia/Singapore") == shown
    flat = _flat(section)
    assert "in a fixed order — the answer, the escort, the ID, the places" in flat
    assert "Times are in the organisation's time zone; another day is named." in flat
    assert '"somebody no longer on the system"' in flat
    assert "somebody no longer on the system" in Path(authorisation.__file__).read_text(encoding="utf-8")
    assert "does not check anybody in and does not refuse anybody" in flat


def test_the_document_says_how_a_badge_is_set_against_an_authorisation():
    section = _flat(_section("## 6. Where a badge was used", "## 7."))
    service = Path(authorisation.__file__).read_text(encoding="utf-8")
    assert "until they leave, or until the same number is written against somebody else" in section
    assert "v.departed_at" in service and "n.visitor_id IS DISTINCT FROM l.visitor_id" in service
    assert "(`access_events`, through `access_credentials.credential_ref`)" in section
    assert "cr.credential_ref = h.badge" in service and "d.site_id = a.site_id" in service
    assert "a door on a floor of an authorised building is within it" in section
    parents = {"door": "floor", "floor": "block", "block": None, "other": None}
    assert authorisation.within("door", {"block"}, parents) is True
    assert authorisation.within("other", {"block"}, parents) is False
    assert authorisation.within(None, {"block"}, parents) is None and authorisation.within("door", set(), parents) is None
    assert "An event is **to look at** when `within` is false or `in_period` is false." in section
    assert '"to_look_at": inside is False or in_period is False' in service
    assert "`IN_ORDER`, or `FOLLOWED_UP` with what was done — once, and it is kept" in section
    assert authorisation.REVIEW_OUTCOMES == ("IN_ORDER", "FOLLOWED_UP")
    assert "A work permit has no badge of its own" in section and "no badge of its own" in authorisation.NO_PERMIT_BADGE
    rules = _flat(_section("## 5. Places, escort and ID", "## 6."))
    assert "A text with four or more digits together is refused" in rules
    assert r're.search(r"\d{4,}", kind)' in Path(api.__file__).read_text(encoding="utf-8")
    assert "(`site_places`, phase 3)" in rules
    # Where a visitor went is for whoever manages visits.
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert 'if "visitorauth:manage" in held else None' in router
    assert "Where a visitor went is for whoever manages visits" in _flat(_doc())


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    served = {(method, path): needs - {"visitorauth:read"} for method, path, needs in _routes()}
    assert all("visitorauth:read" in needs for _, _, needs in _routes()), "every route needs visitorauth:read"
    section = _section("## 7. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                   section, re.M)}
    assert table == served and len(served) == 15
    assert "There is no `DELETE`." in section and not [m for m, _ in served if m == "DELETE"]
    code = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(visitorauth\.[a-z_.]+)"', code))
    named = set(re.findall(r"`(visitorauth\.[a-z_.]+)`", section))
    assert named == written and len(named) == 9


def test_the_document_says_who_holds_what_and_the_migration_the_web_and_the_phone_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(visitorauth:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"visitorauth:read": {2, 3, 4, 5, 6, 8}, "visitorauth:write": {2, 3, 4, 5, 8},
                       "visitorauth:manage": {2, 3, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(visitorauth:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(granted)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert "visitorauth:" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    cards = (PHONE / "components" / "VisitorAuthCards.tsx").read_text(encoding="utf-8")
    assert "useHolds('visitorauth:read')" in cards and "useHolds('visitorauth:write')" in cards


def test_the_screens_are_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/visitor-authorisations)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/visitor-authorisations", "visitorauth:read")]
    assert "{ label: 'Visitor Pre-Reg', path: '/visitor-prereg'," in sidebar, "the existing screen keeps its entry"
    assert "{ label: 'Contractors',     path: '/contractors'," in sidebar
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="visitor-authorisations"', 'path="visitor-prereg"', 'path="contractors"'):
        assert path in routes
    assert "(`/visitor-authorisations`), under People & Vehicles" in _flat(_doc())
    assert sidebar.index("title: 'People & Vehicles'") < sidebar.index("'/visitor-authorisations'")
    page = (WEB / "pages" / "visitorAuth" / "VisitorAuthorisations.tsx").read_text(encoding="utf-8")
    assert "{review.note}" in page and "{a.says[0]}" in page, "the screen shows the server's own words"
    assert "enabled: !!data?.can_manage" in page, "door events are not asked for by somebody who does not manage visits"
    dialogs = (WEB / "components" / "visitorAuth" / "AuthDialogs.tsx").read_text(encoding="utf-8")
    for key in ("approve", "decline", "extend", "places", "escort", "id_seen", "cancel", "review_movements"):
        assert f"a.may.{key}" in dialogs, f"the {key} button is the server's to offer"
    assert "Its number is not taken." in dialogs and "{a.movements.note}" in dialogs
    client = (WEB / "api" / "visitorAuth.ts").read_text(encoding="utf-8")
    standings = set(re.findall(r"'([A-Z_]+)'", client.split("export type Standing =", 1)[1].split("export type", 1)[0]))
    assert standings == set(authorisation.STANDINGS)
    words = (WEB / "components" / "visitorAuth" / "authFormat.ts").read_text(encoding="utf-8")
    assert not re.search(r"unauthori|intruder|trespass|suspect|violat", words, re.I)


def test_the_phone_calls_routes_that_exist_and_leaves_check_in_as_it_was():
    calls = (PHONE / "api" / "visitorAuth.ts").read_text(encoding="utf-8")
    assert "const BASE = '/api/v1/visitor-authorizations'" in calls
    served = {(method, path) for method, path, _ in _routes()}
    for method, path, call in (("GET", "/mine", "`${BASE}/mine`"), ("GET", "/standing", "`${BASE}/standing`"),
                               ("POST", "/", "apiClient.post<Authorisation>(BASE, { visitor_id: visitorId })"),
                               ("POST", "/{id}/approve", "`${BASE}/${id}/approve`"),
                               ("POST", "/{id}/decline", "`${BASE}/${id}/decline`"),
                               ("POST", "/{id}/id-seen", "`${BASE}/${id}/id-seen`")):
        assert (method, path) in served and call in calls, path
    assert "/to-review" not in calls and "/movements" not in calls, "where a visitor went is not on the phone"
    rules = (PHONE / "lib" / "visitorAuth.ts").read_text(encoding="utf-8")
    standings = set(re.findall(r"\b([A-Z_]{5,}): '", rules.split("STANDING_LABEL", 1)[1].split("}", 1)[0]))
    assert standings == set(authorisation.STANDINGS)
    assert set(re.findall(r"'([A-Za-z ]+)'", rules.split("export const ID_KINDS = [", 1)[1].split("]", 1)[0])) == set(
        authorisation.ID_KINDS)
    screen = (PHONE / "screens" / "VisitorsScreen.tsx").read_text(encoding="utf-8")
    assert "{confirmTarget?.action === 'in' && <VisitStanding visitorId={confirmTarget.visitor.id} />}" in screen
    # The button that checks a visitor in calls what it always called, whatever stands.
    assert "if (confirmTarget.action === 'in') checkinMut.mutate(confirmTarget.visitor)" in screen
    assert "mutationFn: (v: Visitor) => checkinVisitor(v.id)," in screen
    home = (PHONE / "screens" / "DashboardScreen.tsx").read_text(encoding="utf-8")
    assert "<WaitingVisitorsCard />" in home
    cards = (PHONE / "components" / "VisitorAuthCards.tsx").read_text(encoding="utf-8")
    assert "Saying yes checks nobody in" in cards and "{data.note}" in cards
    assert "The phone part has not been run on a device" in _flat(_doc())


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 9. Files", "## 10."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src|mobile/src)/[A-Za-z_/.]+)` \|",
                           _doc().split("## 10. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("No existing table is altered", 1)[0])
    assert changed == CHANGED
    not_done = _flat(_doc().split("## 11. What this does not do", 1)[1])
    for said in ("It does not feed the intelligence layer unless the organisation asks.", "It does not use `restricted_zones`.",
                 "It does not verify an ID.", "It does not open or lock anything."):
        assert said in not_done, said
    layer = [REPO_ROOT / "backend" / "app" / "intelligence_main.py",
             *sorted((REPO_ROOT / "backend" / "app" / "services").glob("intel_*.py"))]
    assert len(layer) > 3
    for path in layer:
        assert "visitor_authoriz" not in path.read_text(encoding="utf-8"), f"{path.name}: the layer reads no authorisation"
    # What the layer is handed, it is handed by the visitor module's own reader, in one place, when asked.
    handed = [path.name for path in layer if "visitor_movement_events" in path.read_text(encoding="utf-8")]
    assert handed == ["intel_runner.py"]
    reader = (REPO_ROOT / "backend" / "app" / "services" / "visitor_movement_events.py").read_text(encoding="utf-8")
    assert 'SETTING = "visitor.movements_to_intelligence"' in reader and "if not await enabled(db):" in reader
    assert 'subject_kind' not in reader.split("def normalise", 1)[1].split("async def enabled", 1)[0], "no subject is named"
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 7 | Visitors and contractors | **Built 2026-10-07**" in built and "VISITOR_CONTRACTOR_SECURITY.md" in built
    phase = built.split("### Phase 7", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 7, by additions only:**", 1)[1]
                      .split("Registering a visitor", 1)[0]) == CHANGED
    assert "not written into the intelligence layer" in _flat(phase)
