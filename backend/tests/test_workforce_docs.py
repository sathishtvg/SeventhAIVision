"""Workforce readings and recommendations: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

from app.main import app
from app.routers import workforce as api
from app.services import workforce_advice as advice
from app.services import workforce_readings as readings
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SECURITY_ANALYTICS_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0154_workforce_advice_answers.py"
BASE = "/api/v1/workforce"
CODES = ("workforce:read", "workforce:answer", "workforce:own")
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts"]
MEI = {"id": uuid.UUID(int=1), "full_name": "Mei Lin"}
SITE = {"id": uuid.UUID(int=2), "name": "Factory A"}


def _part() -> str:
    """The second part of the analytics document: this phase's."""
    return DOC.read_text(encoding="utf-8").split("# Part two — Workforce readings and recommendations", 1)[1]


def _flat(text: str) -> str:
    return " ".join(text.replace("\n> ", "\n").split())


def _section(start: str, end: str) -> str:
    return _part().split(start, 1)[1].split(end, 1)[0]


def _code(path) -> str:
    """A module without its opening description."""
    return Path(path).read_text(encoding="utf-8").split('"""', 2)[2]


def _routes(base: str):
    """Every route under a prefix, with the permissions it asks for."""
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if not path.startswith(base + "/") or not getattr(route, "endpoint", None):
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
                yield method, re.sub(r"\{[a-z_]+:uuid\}", "{id}", path.removeprefix(base)), frozenset(needs)


def test_the_document_gives_each_section_of_a_reading_as_the_code_counts_it():
    section = _section("## 11. A reading", "## 12.")
    rows = re.findall(r"^\| `([A-Z]+)` \| `([a-z:]+)` \| ([^|]*) \|$", section, re.M)
    assert [key for key, _, _ in rows] == list(readings.SECTIONS)
    assert {key: needs for key, needs, _ in rows} == readings.NEEDS
    for key, _, figures in rows:
        assert re.findall(r"`([a-z_]+)`", figures) == list(readings.blank(key)), key
    flat = _flat(section)
    assert "(`services/workforce_readings.py`)" in flat
    assert "A period is the last 7, 28 or 90 days." in flat and readings.PERIOD_DAYS == (7, 28, 90)
    assert readings.DEFAULT_DAYS == 28 and readings.SOON_DAYS == 30 and "lapse within 30 days" in flat
    assert "Training is a person's and not a site's: it is in a guard's reading and in no site's." in flat
    assert readings.PERSONAL == ("TRAINING",)
    sql = {key: " ".join(" ".join(part).split()) for key, part in readings._PARTS.items()}
    # Each count has what it is a count of beside it in the same statement.
    assert "count(*) AS shifts" in sql["SHIFTS"] and "count(x.actual_start) AS worked" in sql["SHIFTS"]
    assert "AS tours_done" in sql["TOURS"] and "AS tours_missed" in sql["TOURS"]
    assert "count(*) AS sent" in sql["RESPONSES"] and "percentile_cont(0.5)" in sql["RESPONSES"]
    assert "the middle time from being sent to arriving" in flat
    # What a reviewer waived is counted as waived, and is not in the counts by kind.
    assert "count(*) FILTER (WHERE x.status = 'waived') AS waived" in sql["VIOLATIONS"]
    assert sql["VIOLATIONS"].count("x.status <> 'waived' AND x.violation_type") == len(readings.VIOLATION_TYPES)
    assert "and by kind those not waived" in flat
    assert "c.is_active" in readings._PERSONAL["courses"] and "courses still run" in flat
    assert "shift_certification_findings" in readings._PERSONAL["at_risk"] and "as the existing certification sweep found them" in flat
    # Who is on the list, and one's own.
    assert "one with nothing recorded is still a guard" in flat
    assert "u.role_id = 5 AND u.is_active" in _code(readings.__file__) and "ORDER BY u.full_name, u.id" in _code(readings.__file__)
    assert "`GET /me` gives the caller their own reading: every section, at every site" in flat
    router = _code(api.__file__)
    assert "everything = frozenset(workforce_readings.NEEDS.values())" in router
    assert "_in_scope(db, uuid.UUID(str(token.user_id)), None, start)" in router


def test_nothing_is_scored_nobody_is_ranked_and_the_document_says_so():
    doc = _flat(_part())
    for said in ("A reading is not an appraisal.", "Nothing is scored and nobody is ranked.",
                 "A list of guards is in order of name, and the table has nothing to sort it by.",
                 "What a reviewer set aside is not counted against anybody.",
                 "A recommendation is never an employment decision and never a change to a roster.",
                 "Coverage compares a site with itself.", "No course is invented.",
                 "A support session reads none."):
        assert said in doc, said
    assert "It is not an appraisal, it ranks nobody" in readings.NOTE
    for module in (readings, advice, api):
        code = _code(module.__file__)
        assert not re.search(r"ORDER BY [a-z_.]*(late|missed|recorded|violat|not_started)[a-z_]* (DESC|ASC)", code), module.__name__
        assert not re.search(r"def \w*(score|rank|grade|rating)", code), module.__name__
    for section in readings.SECTIONS:
        assert not re.search(r"score|rank|rating|grade|index|total", str(readings.blank(section)).replace("checkpoints_total", ""))
    migration = MIGRATION.read_text(encoding="utf-8")
    table = migration.split("CREATE TABLE workforce_advice_answers (", 1)[1].split("CONSTRAINT", 1)[0]
    assert not re.search(r"score|point|rank|grade|flag|rating", table), "nothing is kept about a guard but a manager's answer"
    # The screen: rows in the order given, no heading that sorts, and the note above every reading.
    page = (WEB / "pages" / "workforce" / "WorkforceReadings.tsx").read_text(encoding="utf-8")
    assert "TableSortLabel" not in page and ".sort(" not in page and "sortBy" not in page
    assert page.count("{data.note}") == 3 and "{data.recommendations_note}" in page
    assert "not an appraisal, and nobody is ranked" in page
    words = (WEB / "components" / "workforce" / "workforceFormat.ts").read_text(encoding="utf-8")
    assert "label: 'Violations not waived'" in words and "waived by a reviewer" in words and "disputed by the guard" in words
    assert "export const of = (part: number, whole: number) => `${part} of ${whole}`" in words
    for label in re.findall(r"label: '([^']+)'", words):
        assert not re.search(r"score|rank|rating|grade|performance", label, re.I), label
    not_done = _flat(_part().split("## 17. What the workforce part does not do", 1)[1])
    for said in ("It appraises nobody.", "It decides nothing about anybody's employment",
                 "It assigns no course and changes no roster.", "It does not say why.", "leave is not read",
                 "It makes no recommendation out of lateness or violations.", "It is not taken out as a file.",
                 "The phone is not part of it."):
        assert said in not_done, said


def test_the_document_gives_each_recommendation_and_when_it_speaks():
    section = _section("## 12. Recommendations", "## 13.")
    rows = re.findall(r"^\| `([A-Z_]+)` \| (A guard|A site) \| ([^|]*) \|$", section, re.M)
    assert [code for code, _, _ in rows] == [c for kind in advice.KINDS for c in advice.CODES[kind]]
    assert {code: who for code, who, _ in rows} == {**dict.fromkeys(advice.CODES["TRAINING"], "A guard"),
                                                    **dict.fromkeys(advice.CODES["COVERAGE"], "A site")}
    when = {code: text for code, _, text in rows}
    flat = _flat(section)
    assert "Made of the last 4 weeks' records, whatever period a reading is for" in flat and advice.WEEKS == 4
    assert "(`services/workforce_advice.py`)" in flat
    assert advice.MISSED_AT == 3 and "3 or more of the tours assigned to them were missed" in when["MISSED_TOURS"]
    assert advice.UNSTARTED_AT == 3 and "3 or more of its shifts were not started" in when["UNSTARTED_SHIFTS"]
    assert advice.BUSY_FLOOR == 10 and "10 or more incidents" in when["HOURS_COVER"] and "one band of 4 hours" in when["HOURS_COVER"]
    assert (advice.NIGHT_FROM, advice.NIGHT_TO, advice.SLOW_FLOOR, advice.SLOW_TIMES) == (20, 6, 5, 1.5)
    assert "between 20:00 and 06:00" in when["SLOW_AT_NIGHT"] and "at least one and a half times" in when["SLOW_AT_NIGHT"]
    assert "lapses within 30 days" in when["COURSE_LAPSING"] and "a course that is still run" in when["COURSE_LAPSED"]
    assert "as the existing sweep recorded it" in when["CERTIFICATION"]
    service = _code(advice.__file__)
    assert "FROM shift_certification_findings f" in service and "t.passed AND c.is_active" in service
    assert set(advice.STATUS_WORDS) == {"MISSING", "EXPIRED", "REVOKED", "EXPIRING"}
    # The two statements the document quotes are what the code says of such records.
    quoted = [line[2:] for line in section.split("\n") if line.startswith("> ")]
    assert quoted == [advice.missed_tours(MEI, 3, 2, [])["statement"], advice.unstarted(SITE, 3, 6)["statement"]]
    assert "with the courses the library files under `security`, or that it files none" in flat and advice.TOUR_CATEGORY == "security"
    assert "No course is filed under security in the library." in advice.missed_tours(MEI, 3, 2, [])["consider"]
    assert advice.missed_tours(MEI, 2, 9, []) is None and advice.unstarted(SITE, 2, 9) is None
    assert advice.course(MEI, "c", "First aid", date(2026, 10, 1), date(2026, 10, 8))["code"] == "COURSE_LAPSED"
    # Lateness and violations are not turned into advice.
    assert not [c for codes in advice.CODES.values() for c in codes if re.search(r"LATE|VIOLATION|NO_SHOW", c)]
    assert "is_late" not in service and "violations" not in service.lower().replace("lateness and violations", "")
    # A manager's answer.
    router = _code(api.__file__)
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "the answer is refused (409)" in flat and "raise HTTPException(409, NO_LONGER)" in router
    assert api.NO_LONGER == "That no longer stands. Look again before answering."
    assert "The application's role may read and add rows, and nothing else." in flat
    assert "REVOKE ALL ON workforce_advice_answers FROM svc_app" in migration
    assert re.findall(r"GRANT ([A-Z, ]+) ON workforce_advice_answers TO svc_app", migration) == ["SELECT, INSERT"]
    assert "(`said_then`)" in flat and '"said_then": answer["statement"] if answer["statement"] != rec["statement"] else None' in router
    body = router.split("class AnswerBody", 1)[1].split("@router.post", 1)[0]
    assert set(re.findall(r"^    ([a-z_]+): ", body, re.M)) == {"key", "answer", "reason"} and 'extra="forbid"' in body
    assert "CONSTRAINT ck_wfans_reason" in migration and "CONSTRAINT ck_wfans_subject" in migration


def test_an_answer_changes_nothing_else_and_the_existing_modules_are_not_called():
    doc = _flat(_part())
    for said in ("An answer is a manager's, and changes nothing else.",
                 "Accepting a recommendation assigns no course, moves no shift and records nothing against anybody.",
                 "The course is assigned in Training and the roster is changed in the roster, by a person, as before.",
                 "The roster, the auto-scheduler, the training module, the violations review and the certification sweep "
                 "are not called, changed or written to."):
        assert said in doc, said
    router = _code(api.__file__)
    assert set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", router)) == {"workforce_advice_answers"}
    for module in (api, advice, readings):
        code = _code(module.__file__)
        for name in ("roster_autoschedule", "from app.services import roster", "services.training", "services.violations",
                     "certification_compliance", "redis", "response_notify", "send_expo_push", "smtp"):
            assert name not in code, (module.__name__, name)
    for module in (advice, readings):
        assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", _code(module.__file__)), module.__name__
    services = REPO_ROOT / "backend" / "app" / "services"
    for name in ("roster.py", "roster_autoschedule.py", "training.py", "violations.py", "certification_compliance.py"):
        source = (services / name).read_text(encoding="utf-8")
        assert "workforce_readings" not in source and "workforce_advice" not in source, name
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert "workforce_" not in scheduler, "nothing is counted or recommended by a scheduler"
    # The screen says it beside the buttons, and offers answering only where the server does.
    page = (WEB / "pages" / "workforce" / "WorkforceReadings.tsx").read_text(encoding="utf-8")
    assert "r.may_answer" in page and "usePermission('workforce:answer')" not in page
    assert advice.NOTE.count("accepting one assigns no course and changes no roster") == 1
    assert "{data.note}" in page and "The roster is changed in the roster, by a person." == advice.ROSTER
    # Somebody else's reading is read by the organisation's own people, and that is written down.
    assert router.count("_own_staff(token)") >= 5 and "if token.support_session_id:" in router
    assert '"workforce.reading.read"' in router and '"workforce.readings.read"' in router
    read_mine = router.split("async def read_mine", 1)[1].split("async def _answers", 1)[0]
    assert "intel_audit" not in read_mine, "one's own reading is not a look at somebody else"


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    section = _section("## 13. The workforce API", "**Workforce permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$", section, re.M)}
    served = {(method, path): needs for method, path, needs in _routes(BASE)}
    assert table == served and len(served) == 6
    assert not [m for m, _ in served if m in ("PUT", "PATCH", "DELETE")]
    assert "Nothing is changed or removed by any of them but an answer being added." in _flat(section)
    router = Path(api.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'"(workforce\.[a-z_.]+)"', router))
    assert written == set(re.findall(r"`(workforce\.[a-z_.]+)`", section)) and len(written) == 3
    assert "Reading one's own is not audited" in _flat(section)


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(workforce:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"workforce:read": {2, 3, 8}, "workforce:answer": {2, 3, 8}, "workforce:own": {3, 4, 5}}
    part = _part()
    header = re.search(r"^\| \| Admin 2 \|.*$", part, re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", part, re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert ("Super Admin, a viewer and the client role hold none of the new permissions; an operator and a guard hold "
            "only the one to read their own.") in _flat(part)
    assert api.PERMISSIONS == CODES

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(workforce:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screens_are_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/workforce-readings|/my-reading)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/workforce-readings", "workforce:read"), ("/my-reading", "workforce:own")]
    assert sidebar.index("title: 'Guard Operations'") < sidebar.index("'/workforce-readings'") < sidebar.index("title: 'Security Intelligence'")
    for kept in ("path: '/attendance',", "path: '/shifts',", "path: '/roster',", "path: '/violations',", "path: '/training',",
                 "path: '/certification-compliance',", "path: '/compliance',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="workforce-readings"', 'path="my-reading"', 'path="roster"', 'path="violations"', 'path="training"'):
        assert path in routes
    part = _part()
    doc = _flat(part)
    assert "(`/workforce-readings`), under Guard Operations, in three parts" in doc and "**My Reading** (`/my-reading`)" in doc
    assert "The phone is not changed in this phase." in doc
    assert ("The existing attendance, shifts, roster, tour compliance, violations, training and certification screens "
            "are unchanged.") in doc
    page = (WEB / "pages" / "workforce" / "WorkforceReadings.tsx").read_text(encoding="utf-8")
    for label in ("Guards", "Sites", "Recommendations"):
        assert f'label="{label}"' in page and f"**{label}**" in part, label
    assert "usePermission('workforce:read')" in page and "usePermission('workforce:own')" in page
    assert "export function MyReading()" in page and "getMyReading(days)" in page
    client = (WEB / "api" / "workforce.ts").read_text(encoding="utf-8")
    assert set(re.findall(r"'([A-Z]+)'", client.split("export type SectionKey =", 1)[1].split("\n", 1)[0])) == set(readings.SECTIONS)
    assert set(re.findall(r"'([a-z_]+)'", client.split("export type ViolationType =", 1)[1].split("\n", 1)[0])) == set(readings.VIOLATION_TYPES)
    assert set(re.findall(r"'([A-Z]+)'", client.split("export type Kind =", 1)[1].split("\n", 1)[0])) == set(advice.KINDS)
    for path in re.findall(r"`\$\{BASE\}(/[a-z/]+)`", client):
        assert path in {p for _, p, _ in _routes(BASE)}, path
    words = (WEB / "components" / "workforce" / "workforceFormat.ts").read_text(encoding="utf-8")
    assert "PERIODS = [7, 28, 90]" in words
    mobile = REPO_ROOT / "mobile" / "src"
    assert not list(mobile.rglob("*orkforce*")) and not list(mobile.rglob("*yReading*"))


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 15. The workforce files", "## 16."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src)/[A-Za-z_/.]+)` \|", _part().split("## 16. The workforce tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _part().split("Existing files changed in this part", 1)[1].split("No existing table is altered", 1)[0])
    assert changed == CHANGED
    migration = MIGRATION.read_text(encoding="utf-8")
    upgrade = migration.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["workforce_advice_answers"]
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"workforce_advice_answers"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade)
    assert "The new table refers to `users` and `sites`." in _flat(_part())
    assert set(re.findall(r"REFERENCES (\w+)\(", upgrade)) == {"tenants", "users", "sites"}
    assert "**Phase 11 of the enterprise expansion.** Built 2026-10-08, migration `0154`." in _part()
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 11 | Workforce intelligence | **Built 2026-10-08** — migration `0154`" in built
    phase = built.split("### Phase 11", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 11, by additions only:**", 1)[1]
                      .split("The attendance, shift, roster", 1)[0]) == CHANGED
    for said in ("nobody is appraised, scored or ranked", "accepting a recommendation assigns no course and changes no roster",
                 "a league table waiting to be sorted", "leave is not read"):
        assert said in _flat(phase), said
