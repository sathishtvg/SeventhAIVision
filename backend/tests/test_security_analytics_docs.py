"""The operations board and the daily briefing: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.routers import daily_briefings as briefings_api
from app.routers import operations_board as board_api
from app.routers import operations_reports as reports_api
from app.services import daily_briefing as briefing
from app.services import device_health, ops_board, ops_reports, response_sla, risk_patterns
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SECURITY_ANALYTICS_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0152_daily_briefings.py"
REPORT_MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0153_operations_reports.py"
BOARD, BRIEFINGS, REPORTS = "/api/v1/operations-board", "/api/v1/daily-briefings", "/api/v1/operations-reports"
CODES = ("board:read", "briefing:read", "briefing:manage", "opsreport:export")
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts"]
EVERYTHING = frozenset(board_api.SOURCE_PERMISSIONS)
A_DAY = {
    "INCIDENTS": {"opened": 4, "by_severity": {"critical": 1, "high": 2, "medium": 1, "low": 0}, "resolved": 2,
                  "opened_still_open": 3, "open_now": 4},
    "RESPONSE": {"opened": 4, "acknowledged": 3, "acknowledge_seconds": 240.0, "resolved": 1, "resolve_seconds": 600.0,
                 "sent": 2, "arrived": 1, "declined": 1, "arrive_seconds": 360.0,
                 "missed": {"acknowledge": 1, "arrival": 0, "resolve": 1}},
    "PATROLS": {"tours": {"scheduled": 3, "done": 1, "partial": 0, "missed": 1, "failed": 0, "open": 1, "cancelled": 0},
                "virtual": None, "drone": None},
    "DEVICES": {"devices": 3, "by_state": {"OK": 1, "DEGRADED": 0, "DOWN": 1, "NOT_KNOWN": 1, "OFF": 0}},
}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.replace("\n> ", "\n").split())


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
                shown = re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(base)) or "/"
                yield method, shown, frozenset(needs)


def test_the_document_gives_each_section_what_it_is_read_under_and_its_figures():
    section = _section("## 2. The board", "## 3.")
    rows = re.findall(r"^\| `([A-Z]+)` \| `([a-z:]+)` \| ([^|]*) \|$", section, re.M)
    assert [key for key, _, _ in rows] == list(ops_board.SECTIONS)
    assert {key: needs for key, needs, _ in rows} == ops_board.NEEDS
    for key, _, figures in rows:
        named = re.findall(r"`([a-z_]+)`", figures)
        blank = ops_board.blank(key, EVERYTHING)
        if key == "PATROLS":
            assert named[:3] == list(ops_board.PATROL_KINDS) and named[3:] == list(blank["tours"])
        else:
            assert named == list(blank), key
    flat = _flat(section)
    assert "(`services/ops_board.py`)" in flat
    # The three parts with a permission of their own.
    assert "virtual patrols (`vpatrol:read`), drone patrols (`drone:read`) and visits waiting for a decision (`visitorauth:read`)" in flat
    assert ops_board.PART_NEEDS == {"virtual": "vpatrol:read", "drone": "drone:read", "waiting_now": "visitorauth:read"}
    assert "A part that is not read is given as nothing, not as zero." in flat
    without = EVERYTHING - set(ops_board.PART_NEEDS.values())
    assert ops_board.blank("PATROLS", without)["virtual"] is None and ops_board.blank("VISITORS", without)["waiting_now"] is None
    # How each is counted is how the statements count it.
    sql = {key: " ".join(" ".join(part).split()) for key, part in ops_board._PARTS.items()}
    assert "an incident is open until it is `resolved` or `closed`" in flat and "i.status NOT IN ('resolved', 'closed')" in sql["INCIDENTS"]
    assert "(`services/response_sla.py`)" in flat
    assert " ".join(ops_board.ACKNOWLEDGED_AT.split()) in " ".join(response_sla.INCIDENTS.split())
    assert "the first of a dispatch, a change of status, a guard being sent, or its resolution" in flat
    for source in ("i.dispatched_at", "incident_status_history", "incident_responses", "i.resolved_at"):
        assert source in ops_board.ACKNOWLEDGED_AT
    assert sql["RESPONSE"].count("percentile_cont(0.5)") == 2 and sql["SENT"].count("percentile_cont(0.5)") == 1
    assert "(`clocks_on_since`)" in flat and "e.kind = 'SLA_BREACH'" in sql["MISSED"]
    assert "A drone patrol that was blocked or aborted is counted as failed." in flat
    assert "x.status IN ('FAILED', 'BLOCKED', 'ABORTED')) AS failed" in sql["drone"]
    assert "A share is taken only of those that are over." in flat
    assert ops_board.over({"scheduled": 9, "done": 3, "partial": 1, "missed": 2, "failed": 1, "open": 2, "cancelled": 0}) == 7
    assert "(`services/device_health.py`)" in flat and "device_health.readings(" in _code(ops_board.__file__)
    assert "A suggestion is not counted as raised until a person accepts it." in flat
    assert "x.state NOT IN ('SUGGESTED', 'DISMISSED')" in sql["MAINTENANCE"]
    assert "A period is the last 1, 7 or 30 days, ending now." in flat and ops_board.PERIOD_DAYS == (1, 7, 30)
    # Sites and customers.
    assert "A site's customer is `sites.client_id`; each customer's sites are summed (`add()`), without their times." in flat
    summed = ops_board.add(ops_board.blank("RESPONSE", EVERYTHING), ops_board.blank("RESPONSE", EVERYTHING))
    assert summed["opened"] == 0 and summed["acknowledge_seconds"] is None
    assert "ops_board.add(into[\"figures\"], row[\"figures\"])" in _code(board_api.__file__)
    assert "(`client_id`)" in flat and "(`advice:read`)" in flat and "over the last 4 weeks" in flat
    assert briefing.ADVICE_WEEKS == 4 and "weeks = daily_briefing.ADVICE_WEEKS" in _code(board_api.__file__)


def test_the_board_only_reads_and_the_document_names_what_it_reads():
    for path in (ops_board.__file__, board_api.__file__, briefing.__file__):
        code = _code(path)
        assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", code), Path(path).name
        assert "intel_audit" not in code and "redis" not in code.lower(), Path(path).name
    read = set(re.findall(r"(?:FROM|JOIN) ([a-z_]+)(?![a-z_.])", " ".join(" ".join(p) for p in ops_board._PARTS.values())
                          + ops_board.ACKNOWLEDGED_AT))
    named = set(re.findall(r"`([a-z_]+)`", _flat(_doc()).split("The board reads ", 1)[1].split("and writes to none", 1)[0]))
    assert named == read and len(read) == 15
    doc = _flat(_doc())
    for said in ("The board stores nothing and writes nothing.", "Nothing is scored, and the board and a briefing name nobody.",
                 "A figure is a count of what is recorded, and says what it counts.",
                 "When there were none it is nothing, not zero.",
                 "Each section is read under its own existing permission.",
                 "Reading the board is not audited: it reads counts."):
        assert said in doc, said
    assert "a site's customer is read from `billing_clients`" in doc and "billing_clients" in _code(board_api.__file__)
    assert "Nothing here is a score or a forecast." in ops_board.NOTE
    for key in ops_board.SECTIONS:
        figures = ops_board.blank(key, EVERYTHING)
        flat = str(figures)
        assert not re.search(r"score|rating|grade|rank|index", flat), key
    # Nobody is named: no statement reads a person's name or counts by a person.
    assert not re.search(r"full_name|guard_user_id|GROUP BY [a-z.]*user", " ".join(" ".join(p) for p in ops_board._PARTS.values()))
    # The screen marks what is as things stand, and does not call clocks that are off "none missed".
    page = (WEB / "pages" / "board" / "OperationsBoard.tsx").read_text(encoding="utf-8")
    assert "{label}{now ? ' · now' : ''}" in page and "That is not the same as none being missed." in page
    assert "{data.note}" in page and "<NotShown items={data.not_read} />" in page
    words = (WEB / "components" / "board" / "boardFormat.ts").read_text(encoding="utf-8")
    assert "if (seconds === null || seconds === undefined) return '—'" in words
    assert "NOT_KNOWN: 'No reading'" in words


def test_the_document_gives_the_briefing_as_the_code_drafts_and_keeps_it():
    section = _section("## 3. The daily briefing", "## 4.")
    states = re.findall(r"^\| `([A-Z]+)` \|", section, re.M)
    assert states == list(briefing.STATES)
    flat = _flat(section)
    assert "It may be drafted for a day in the last 31 days." in flat and briefing.MAX_DAYS_BACK == 31
    assert "A day that is not over is counted so far." in flat
    assert "(`services/daily_briefing.py`)" in flat
    # The lines the document quotes are what the code writes of such a day.
    content = briefing.draft(A_DAY, True, None, [])
    written = [line["text"] for s in content["sections"] for line in s["lines"]]
    quoted = [line.strip() for line in section.split("\n") if line.startswith("> ")]
    assert quoted == ["> 4 incidents were opened: 1 critical, 2 high and 1 medium.",
                      "> Somebody acted on 3 of the 4 opened; on half of them within 4 minutes.",
                      "> Guard tours: 1 done of 2 that are over; 1 missed.",
                      "> Of 3 devices, 1 read as working; 1 read as down and 1 with no reading."]
    for line in quoted:
        assert line[2:] in written, line
    assert "carries up to 3 pieces of phase 9's advice word for word" in flat and briefing.ADVICE_LINES == 3
    advice = [{"statement": f"S{n}.", "confidence": {"why": "Rests on it."}} for n in range(5)]
    last = briefing.draft(A_DAY, True, advice, [])["sections"][-1]
    assert (last["title"], len(last["lines"]), last["note"]) == ("What stands out", 3, briefing.ADVICE_NOTE)
    assert "not a forecast" in briefing.ADVICE_NOTE and risk_patterns.NOTE.split(": ", 1)[1] in briefing.ADVICE_NOTE
    assert "the 4 weeks before and not a forecast" in flat
    # Reviewing: sections left out and a note; never a line.
    router = _code(briefings_api.__file__)
    assert "(`left_out`)" in flat and "(`note`)" in flat
    body = router.split("class ReviewBody", 1)[1].split("@router.patch", 1)[0]
    assert set(re.findall(r"^    ([a-z_]+): ", body, re.M)) == {"left_out", "note"} and 'extra="forbid"' in body
    assert "there is no field for one, and the request refuses any" in flat
    assert "They may count the draft again; the note stays." in flat
    recount = router.split("async def recount_briefing", 1)[1].split("@router.post", 1)[0]
    assert "note" not in recount.split("UPDATE daily_briefings", 1)[1].split('"""', 1)[0]
    # Publishing.
    assert "A briefing with every section left out and no note is not published." in flat
    assert 'raise HTTPException(422, "There is nothing in it to publish' in router
    assert "publishing it is the decision to share its counts" in flat
    assert "What was left out is not shown to anybody, the reviewer included; that it was left out is." in flat
    assert "daily_briefing.shown(content, row[\"left_out\"] or [], whole=manages and draft)" in router
    assert '"left_out": [{"key": s["key"], "title": s["title"]}' in router
    # Correcting.
    assert "(`replaced_by`)" in flat and "n.state = 'PUBLISHED' AND n.revision > b.revision" in router
    assert "A day has one draft at a time. A draft set aside keeps its number." in flat
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX uq_brief_one_draft" in migration and "WHERE state = 'DRAFT'" in migration
    assert "COALESCE(max(revision), 0) + 1" in router
    # Who reads which.
    assert "drafted and read only by somebody who is not held to particular sites" in flat
    assert briefings_api.EVERY_SITE.endswith("drafted by somebody who is not held to particular sites.")
    assert "return allowed is None if row[\"site_id\"] is None else is_site_allowed(allowed, row[\"site_id\"])" in router


def test_the_platform_drafts_a_person_publishes_and_what_is_published_is_held_still():
    doc = _flat(_doc())
    migration = MIGRATION.read_text(encoding="utf-8")
    router = _code(briefings_api.__file__)
    for said in ("The platform drafts; a person publishes.", "Nothing publishes one by itself, and publishing tells nobody",
                 "No model writes a briefing", "The counted lines are not edited.", "A line says when it is true of.",
                 "A published briefing is not changed.", "A trigger refuses it.",
                 "not an API key, not a support session"):
        assert said in doc, said
    assert "CREATE TRIGGER daily_briefing_settled BEFORE UPDATE ON daily_briefings" in migration
    assert "OLD.state IN ('PUBLISHED', 'DISCARDED') AND pg_trigger_depth() = 1" in migration
    assert "CONSTRAINT ck_brief_published" in migration
    assert "REVOKE ALL ON daily_briefings FROM svc_app" in migration and "GRANT SELECT, INSERT ON daily_briefings TO svc_app" in migration
    changes = migration.split("BRIEFING_CHANGES = (", 1)[1].split(")", 1)[0]
    for held in ("briefing_date", "revision", "site_id", "tenant_id", "period_start", "timezone"):
        assert not re.search(rf'"{held}"', changes), f"the application may not change a briefing's {held}"
    assert re.findall(r"`([A-Z]+)` is", _flat(_section("10. **A line says when it is true of.**", "11."))) == list(briefing.WHEN)
    # Nothing publishes but the one route, and that one is a person's.
    assert router.count("SET state = 'PUBLISHED'") == 1
    for name in ("draft_briefing", "review_briefing", "recount_briefing", "publish_briefing", "discard_briefing"):
        assert "_a_person(token)" in router.split(f"async def {name}", 1)[1].split("@router.", 1)[0], name
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert "daily_briefing" not in scheduler and "ops_board" not in scheduler
    not_done = _flat(_doc().split("## 9. What this does not do", 1)[1])
    assert "It drafts nothing by itself." in not_done and "No scheduler writes one each morning." in not_done
    for path in (briefings_api.__file__, briefing.__file__):
        code = _code(path).lower()
        for word in ("redis", "response_notify", "send_expo_push", "smtp", "webhook"):
            assert word not in code, (Path(path).name, word)
    assert "It sends nothing." in not_done
    # No model: the service imports nothing that could be one, and reads nothing.
    source = Path(briefing.__file__).read_text(encoding="utf-8")
    for name in ("sklearn", "numpy", "torch", "anthropic", "openai", "transformers", "sqlalchemy"):
        assert not re.search(rf"^\s*(import|from)\s+{name}\b", source, re.M), name
    # The screen: the lines are shown, not fields; publishing is said to be final first.
    dialogs = (WEB / "components" / "board" / "BriefingDialogs.tsx").read_text(encoding="utf-8")
    assert "Once published it is not changed" in dialogs and "onClick={() => setPublishing(true)}" in dialogs
    for key in ("edit", "recount", "publish", "discard", "correct"):
        assert f"data?.may.{key}" in dialogs or f"data.may.{key}" in dialogs, f"the {key} button is the server's to offer"
    assert "Left out by the reviewer:" in dialogs and "whoever drafted it may not read them" in dialogs
    assert dialogs.count("<TextField") == 3, "a site, a day and the note: there is no field for a line"
    words = (WEB / "components" / "board" / "boardFormat.ts").read_text(encoding="utf-8")
    assert "PERIOD: null, DRAFTING: 'when drafted', WEEKS: 'the 4 weeks before'" in words


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    section = _section("## 5. API", "**Permissions**")
    first, second = section.split("Under `/api/v1/daily-briefings`", 1)
    second, third = second.split("Under `/api/v1/operations-reports`", 1)

    def table(text: str) -> dict:
        return {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
                for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                      text, re.M)}

    for base, text, always, count in ((BOARD, first, "board:read", 2), (BRIEFINGS, second, "briefing:read", 7),
                                      (REPORTS, third, "opsreport:export", 2)):
        served = {(method, path): needs - {always} for method, path, needs in _routes(base)}
        assert all(always in needs for _, _, needs in _routes(base)), f"every route under {base} needs {always}"
        assert table(text) == served and len(served) == count, base
        assert not [m for m, _ in served if m in ("PUT", "DELETE")]
    assert {m for m, _, _ in _routes(BOARD)} == {"GET"} == {m for m, _, _ in _routes(REPORTS)}
    assert "There is no `PUT` or `DELETE` under any, and nothing but `GET` under the board and the reports." in _flat(section)
    assert "`GET /{key}` also needs what that report's records are read under" in _flat(section)
    assert "`report.export`" in section
    router = Path(briefings_api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(briefing\.[a-z_.]+)"', router))
    assert written == set(re.findall(r"`(briefing\.[a-z_.]+)`", section)) and len(written) == 5
    assert router.count("intel_audit.record(") == 5


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8") + REPORT_MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '((?:board|briefing|opsreport):[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"board:read": {2, 3, 4, 6, 8}, "briefing:read": {2, 3, 4, 6, 8}, "briefing:manage": {2, 3, 8},
                       "opsreport:export": {2, 3, 8}}
    assert "(migrations `0152`, `0153`)" in _doc()
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert "Super Admin, a guard and the client role hold none of the new permissions." in _flat(_doc())
    assert briefings_api.PERMISSIONS == CODES[1:3]

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'((?:board|briefing|opsreport):[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screen_is_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/operations-board)',.*permission: '([a-z:]+)'", sidebar) == [("/operations-board", "board:read")]
    assert sidebar.index("title: 'Monitoring'") < sidebar.index("'/operations-board'") < sidebar.index("title: 'Investigate'")
    for kept in ("path: '/command-centre',", "path: '/',", "path: '/analytics',", "path: '/reports',", "path: '/action-center',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="operations-board"', 'path="command-centre"', 'path="analytics"', 'path="reports"'):
        assert path in routes
    doc = _flat(_doc())
    assert "(`/operations-board`), under Monitoring, in four parts" in doc
    assert "The phone is not changed in this phase." in doc
    assert ("The existing dashboard (`/`), command centre (`/command-centre`), analytics (`/analytics`) and reports "
            "(`/reports`) are unchanged.") in doc
    page = (WEB / "pages" / "board" / "OperationsBoard.tsx").read_text(encoding="utf-8")
    for label, part in (("Board", "Board"), ("Sites and customers", "Sites and customers"), ("Daily briefing", "Daily briefing"),
                        ("Reports", "Reports")):
        assert f'label="{label}"' in page and f"**{part}**" in _doc(), part
    assert "usePermission('board:read')" in page and "usePermission('briefing:read')" in page
    assert "usePermission('opsreport:export')" in page
    assert "usePermission('briefing:manage')" not in page, "whether somebody may draft is the server's to say"
    assert "data?.can_manage" in page
    client = (WEB / "api" / "operationsBoard.ts").read_text(encoding="utf-8")
    sections = set(re.findall(r"'([A-Z]+)'", client.split("export type SectionKey =", 1)[1].split("\n", 1)[0]))
    assert sections == set(ops_board.SECTIONS)
    assert set(re.findall(r"'([A-Z_]+)'", client.split("export type DeviceState =", 1)[1].split("\n", 1)[0])) == set(device_health.STATES)
    assert set(re.findall(r"'([A-Z]+)'", client.split("export type BriefingState =", 1)[1].split("\n", 1)[0])) == set(briefing.STATES)
    assert set(re.findall(r"'([A-Z]+)'", client.split("export type AsAt =", 1)[1].split("\n", 1)[0])) == set(briefing.WHEN)
    assert set(re.findall(r"'([a-z]+)'", client.split("export type PatrolKind =", 1)[1].split("\n", 1)[0])) == set(ops_board.PATROL_KINDS)
    # The words for a length of time are the same on the screen as in a briefing.
    for seconds, said in ((30, "under a minute"), (240, "4 minutes"), (4800, "1 hour 20 minutes"), (97200, "1 day 3 hours")):
        assert briefing.lasting(seconds) == said
    words = (WEB / "components" / "board" / "boardFormat.ts").read_text(encoding="utf-8")
    assert "return 'under a minute'" in words and "PERIODS = [1, 7, 30]" in words
    # No existing figure was touched, and the phone has no part of it.
    routers = REPO_ROOT / "backend" / "app" / "routers"
    for name in ("analytics.py", "command_centre.py", "reports.py", "scheduled_reports.py", "exports.py"):
        source = (routers / name).read_text(encoding="utf-8")
        assert "ops_board" not in source and "daily_briefing" not in source, name
    mobile = REPO_ROOT / "mobile" / "src"
    assert not list(mobile.rglob("*perationsBoard*")) and not list(mobile.rglob("*riefing*"))


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 7. Files", "## 8."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src)/[A-Za-z_/.]+)` \|", _doc().split("## 8. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("No existing table is altered", 1)[0])
    assert changed == CHANGED
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["daily_briefings"]
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"daily_briefings"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade)
    assert "The table is `daily_briefings` and not `security_briefings`, as the plan had it" in _flat(_doc())
    assert "The new table refers to `sites` and `users`." in _flat(_doc())
    assert set(re.findall(r"REFERENCES (\w+)\(", upgrade)) == {"tenants", "sites", "users"}
    not_done = _flat(_doc().split("## 9. What this does not do", 1)[1])
    for said in ("It scores nothing.", "The board and a briefing name nobody.", "It keeps no history of the board.",
                 "It does not let a line be rewritten.", "It delivers no report on a schedule.",
                 "The phone is not part of it."):
        assert said in not_done, said
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 10 | Analytics | **Built 2026-10-08** — migrations `0152`, `0153`" in built and "SECURITY_ANALYTICS_ARCHITECTURE.md" in built
    phase = built.split("### Phase 10", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 10, by additions only:**", 1)[1]
                      .split("The dashboard, the command centre", 1)[0]) == CHANGED
    assert "no report is delivered on a schedule" in _flat(phase) and "nothing is scored, graded or ranked" in _flat(phase)
    for path in ("backend/alembic/versions/0153_operations_reports.py", "backend/app/services/ops_reports.py",
                 "backend/app/routers/operations_reports.py", "frontend/src/api/operationsReports.ts",
                 "backend/tests/test_operations_reports.py", "frontend/src/pages/board/operationsReports.test.tsx"):
        assert f"`{path}`" in _doc() and (REPO_ROOT / path).exists(), path


def test_the_document_gives_each_report_what_it_is_read_under_and_how_a_file_is_written():
    section = _section("## 4. Reports", "## 5.")
    rows = re.findall(r"^\| `([a-z-]+)` \| ([^|]*) \| ([^|]*) \|$", section, re.M)
    assert [key for key, _, _ in rows] == [r.key for r in ops_reports.REPORTS] and len(rows) == 9
    for key, needs, _ in rows:
        assert tuple(re.findall(r"`([a-z:]+)`", needs)) == ops_reports.BY_KEY[key].needs, key
    flat = _flat(section)
    assert "(`services/ops_reports.py`)" in flat and "Nine of them, each a list and none a judgement" in flat
    assert "A period** is the last 1, 7, 30 or 90 days." in flat and ops_reports.PERIOD_DAYS == (1, 7, 30, 90)
    assert [r.key for r in ops_reports.REPORTS if not r.periodic] == ["device-health", "risk"]
    assert "`device-health` is of how things are now and `risk` is of the last 4 weeks whatever is asked: neither takes a period." in flat
    assert ops_reports.ADVICE_WEEKS == 4
    # What a spreadsheet would run is made text; a time says its zone; a cut file says so.
    assert "begins with `=`, `+`, `-`, `@`, a tab or a return is written with an apostrophe before it" in flat
    from zoneinfo import ZoneInfo
    for typed in ("=1+1", "+1", "-1", "@a", "\tx", "\rx"):
        assert ops_reports.cell(typed, ZoneInfo("UTC")) == "'" + typed
    assert ops_reports.cell("Mei Lin", ZoneInfo("UTC")) == "Mei Lin"
    assert "the heading of its column names the time zone" in flat
    assert ops_reports.headings(ops_reports.BY_KEY["access"], "UTC")[0] == "Happened at (UTC)"
    router = _code(reports_api.__file__)
    assert "byte-order mark" in flat and 'content.encode("utf-8-sig")' in router
    assert "A file holds at most 10,000 records.** Past that its last line says it was cut, in words, and so do the response's headers." in flat
    assert ops_reports.MAX_ROWS == 10000 and ops_reports.CUT.startswith("Cut at {n} records.") and '"X-Report-Cut"' in router
    # Two permissions; nothing stored but the audit line; a support session takes none out.
    assert "`opsreport:export` to take any report out, and the permission the report's own records are read under" in flat
    assert "opsreport:export" not in reports_api.PERMISSIONS and "ops_reports.may_have(r, held)" in router
    assert "(`report.export`)" in flat and router.count("intel_audit.record(") == 1 and '"report.export"' in router
    assert "A support session takes none out.** " in flat and "An API key may." in flat
    assert "if token.support_session_id:" in router and "via_api_key" not in router
    service = _code(ops_reports.__file__)
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", service + router)
    doc = _flat(_doc())
    for said in ("A report is the records as they are.", "Taking records out is its own permission, held on top of the one they are read under.",
                 "`opsreport:export` alone opens no report, and a support session takes none out.",
                 "A file is written to be opened safely and read rightly.", "Every report taken out is audited"):
        assert said in doc, said
    upgrade = REPORT_MIGRATION.read_text(encoding="utf-8").split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "CREATE TABLE" not in upgrade and "ALTER TABLE" not in upgrade
    # What it does not do, and that the existing reports and exports are as they were.
    not_done = _flat(_doc().split("## 9. What this does not do", 1)[1])
    for said in ("It delivers no report on a schedule.", "A report is a CSV file.", "It has no report per guard.",
                 "The existing scheduled reports are unchanged and do not carry these."):
        assert said in not_done, said
    routers = REPO_ROOT / "backend" / "app" / "routers"
    assert 'VALID_REPORT_TYPES = {"site_summary", "dob", "incident_summary"}' in (routers / "scheduled_reports.py").read_text(encoding="utf-8")
    for name in ("scheduled_reports.py", "exports.py", "reports.py"):
        assert "ops_reports" not in (routers / name).read_text(encoding="utf-8"), name
    assert "ops_reports" not in (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    phase = GAPS.read_text(encoding="utf-8").split("### Phase 10", 1)[1]
    assert "**Found in phase 10 and left alone (for phase 13):** the existing CSV exports" in phase
    assert "`=`" not in (routers / "exports.py").read_text(encoding="utf-8"), "the existing exports were not changed"
    # The screen: a report the reader may not have is not offered, and says why; the list is the server's.
    tab = (WEB / "components" / "board" / "ReportsTab.tsx").read_text(encoding="utf-8")
    assert "disabled={!r.may || take.isPending}" in tab and "{r.why_not}" in tab and "read under {r.needs.join(' and ')}" in tab
    assert "{data.note}" in tab and "usePermission" not in tab
    client = (WEB / "api" / "operationsReports.ts").read_text(encoding="utf-8")
    assert "responseType: 'blob'" in client and "`${BASE}/${key}`" in client
