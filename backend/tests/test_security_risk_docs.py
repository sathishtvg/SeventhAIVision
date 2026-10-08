"""Risk patterns and advice: the document says what the code does, and the web says what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.main import app
from app.routers import security_advice as api
from app.services import risk_patterns as patterns
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "SECURITY_RISK_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0151_risk_advice_answers.py"
BASE = "/api/v1/security-advice"
CODES = ("advice:read", "advice:answer")
CHANGED = ["backend/app/main.py", "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx",
           "frontend/src/hooks/usePermission.ts"]
SINCE = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)   # a Monday


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.replace("\n> ", "\n").split())


def _section(start: str, end: str) -> str:
    return _doc().split(start, 1)[1].split(end, 1)[0]


def _code(path: Path) -> str:
    """A module without its opening description."""
    return path.read_text(encoding="utf-8").split('"""', 2)[2]


def _rows(when: list[tuple[int, int]], key: str = "p1", name: str = "Morning Security Patrol") -> list[dict]:
    """Records at so many days and hours after the period began."""
    return [{"at": SINCE + timedelta(days=d, hours=h), "site_id": "s", "place_key": key, "place": name} for d, h in when]


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
                yield method, path.removeprefix(base) or "/", frozenset(needs)


def test_nothing_is_a_forecast_in_the_document_the_code_or_on_the_screen():
    doc = _flat(_doc())
    for said in ("A pattern that recurred is not a forecast.", "Nothing says what will happen.",
                 "Confidence is how much history a statement rests on. It is not a probability.",
                 "Both readings (`/patterns`, `/advice`) carry `is_forecast: false` and a note that says so"):
        assert said in doc, said
    assert "A pattern that recurred is not a forecast: nothing here says what will happen." in patterns.NOTE
    assert patterns.CONFIDENCE_NOTE.endswith("It is not a probability.")
    router = _code(Path(api.__file__))
    assert router.count('"is_forecast": False') == 2 and router.count('"note": risk_patterns.NOTE') == 2
    assert '"confidence_note": risk_patterns.CONFIDENCE_NOTE' in router
    service = _code(Path(patterns.__file__))
    assert '"is_advisory": True, "is_forecast": False' in service and '"is_forecast": True' not in service
    # The screen shows the server's notes, and has its own words for a confidence.
    page = (WEB / "pages" / "risk" / "RiskAdvice.tsx").read_text(encoding="utf-8")
    assert "{advice.data.note}" in page and "{advice.data.confidence_note}" in page
    assert "Counts over a period — not a forecast" in page
    words = (WEB / "components" / "risk" / "riskFormat.ts").read_text(encoding="utf-8")
    assert "HIGH: 'Rests on much history', MEDIUM: 'Rests on some history', LOW: 'Rests on little history'" in words
    assert 'The screen\'s words for it are "rests on much / some / little history".' in doc
    for file in (page, words):
        shown = re.sub(r"/\*.*?\*/|//[^\n]*", "", file, flags=re.S)
        assert not re.search(r"likely|probab|predict|chance of", shown, re.I)
    not_done = _flat(_doc().split("## 9. What this does not do", 1)[1])
    assert "It forecasts nothing." in not_done and "is not a statement about tonight" in not_done


def test_the_document_gives_the_five_kinds_as_the_code_counts_them():
    section = _section("## 2. What is counted", "## 3.")
    assert re.findall(r"^\| `([A-Z]+)` \|", section, re.M) == list(patterns.SOURCES)
    assert set(patterns.SOURCE_LABEL) == set(patterns.NOUN) == set(patterns.COUNTED_FROM) == set(patterns.SOURCES)
    flat = _flat(section)
    sql = patterns._ROWS
    assert "`denied`, `forced` or `tamper`" in flat and "e.event_type IN ('denied', 'forced', 'tamper')" in sql["ACCESS"]
    assert "Virtual patrols `MISSED` or `FAILED`; drone patrols `MISSED`, `FAILED` or `BLOCKED`; guard tours `missed`" in flat
    assert "v.status IN ('MISSED', 'FAILED')" in sql["PATROL"] and "p.status IN ('MISSED', 'FAILED', 'BLOCKED')" in sql["PATROL"]
    assert "o.status = 'missed'" in sql["PATROL"]
    assert "h.event_type = 'stream_disconnected'" in sql["DEVICE"] and "x.state = 'DOWN' AND x.device_kind <> 'CAMERA'" in sql["DEVICE"]
    assert "e.kind = 'SLA_BREACH'" in sql["SLA"] and "(`response.sla_enabled`)" in flat
    assert "FROM incidents i LEFT JOIN cameras c" in sql["INCIDENT"] and "an incident with no camera" in flat
    assert "(`services/risk_patterns.py`)" in flat and "(`services/intel_insight.py`)" in flat
    # Situations are the layer's, and nobody is counted.
    every = " ".join(sql.values())
    assert "security_" not in every and "situations" not in every
    assert not re.search(r"user_id|full_name|guard_id|visitor|employee", every)
    assert "this is a count of what went wrong, not of activity" in flat
    # The period, the three ways, and the cap.
    assert "A period is whole weeks ending now: 1 to 12, 4 unless asked." in flat
    assert (patterns.DEFAULT_WEEKS, patterns.MAX_WEEKS) == (4, 12)
    assert "ge=1, le=risk_patterns.MAX_WEEKS" in _code(Path(api.__file__))
    assert "seven rows of twenty-four, Monday first" in flat and patterns.WEEKDAYS[0] == "Monday" and len(patterns.WEEKDAYS) == 7
    assert "the time zone of the site's security profile when it has one and the organisation's otherwise" in flat
    assert "intel_insight.zone_for(db, site[\"id\"] if site else None)" in _code(Path(api.__file__))
    assert "The answer gives the first eight." in flat and '"places": s["places"][:8]' in _code(Path(patterns.__file__))
    assert "at most 20,000 records of a kind" in flat and patterns.MAX_ROWS == 20000 and "(`cut_at`)" in flat
    # The tables the document says are read are the ones the statements read, and the service writes to none.
    read = set(re.findall(r"(?:FROM|JOIN) ([a-z_]+)", every))
    named = set(re.findall(r"`([a-z_]+)`", _flat(_doc()).split("The service reads ", 1)[1].split("and writes to none", 1)[0]))
    assert named == read and len(read) == 17
    assert not re.search(r"INSERT INTO|UPDATE |DELETE FROM", _code(Path(patterns.__file__)))


def test_the_document_gives_each_rule_and_what_it_takes_to_speak():
    section = _section("## 3. Advice", "**Confidence**")
    flat = _flat(section)
    table = dict(re.findall(r"^\| `([A-Z_]+)` \| ([^|]*) \|", section, re.M))
    service = _code(Path(patterns.__file__))
    assert set(table) == set(re.findall(r'add\("([A-Z_]+)"', service)) and len(table) == 6
    assert "Every rule but the last speaks only when it has at least 5 records" in flat and patterns.FLOOR == 5
    assert list(table)[-1] == "REPEATED_DEVICE" and service.index("if total >= FLOOR:") < service.index('if source == "DEVICE":')
    assert patterns.BAND_HOURS == 4 and "One band of 4 hours" in table["RECURRING_HOURS"]
    assert (patterns.HOURS_SHARE, patterns.DAY_SHARE, patterns.PLACE_SHARE) == (50, 40, 40)
    assert "holds 50% or more" in table["RECURRING_HOURS"] and "40% or more" in table["RECURRING_DAY"]
    assert "more than one place and one holds 40% or more" in table["RECURRING_PLACE"]
    assert "2 weeks or more" in table["RECURRING_DAY"] and "if weeks >= 2 and" in service
    assert patterns.REPEATED_AT == 3 and "went down 3 times or more" in table["REPEATED_DEVICE"]
    assert "the three most only" in table["REPEATED_DEVICE"] and '>= REPEATED_AT][:3]' in service
    assert "at least twice as many" in table["RISING"] and "after >= 2 * before" in service
    assert "the earlier half has none" in table["FIRST_RECORDED"] and 'at_most="LOW"' in service
    assert "wrapping past midnight, the earliest on a tie" in table["RECURRING_HOURS"]
    assert patterns.busiest_band([0] * 22 + [3, 3]) == (20, 6) and patterns.busiest_band([3] + [0] * 22 + [3]) == (21, 6)
    assert "A period of one week has no halves to compare." in flat
    assert not [f for f in patterns.advise("INCIDENT", patterns.summarise(_rows([(0, 9)] * 9), "UTC", SINCE, 1), 1, "s")
                if f["code"] in ("RISING", "FIRST_RECORDED")]
    assert "is halved without its oldest week" in flat and "s[\"by_week\"][weeks - 2 * half:weeks - half]" in service

    # The two statements the document quotes are what the code says of such records.
    said = [(0, 8), (0, 11), (1, 8), (2, 11),                 # week 1: four, all in the band
            (7, 8), (8, 11), (9, 8), (9, 15),                 # week 2: three of four
            (14, 8), (15, 11),                                # week 3: both
            (21, 8), (22, 11), (23, 8), (24, 11), (25, 20)]   # week 4: four of five
    hours = next(f for f in patterns.advise("PATROL", patterns.summarise(_rows(said), "UTC", SINCE, 4), 4, "s")
                 if f["code"] == "RECURRING_HOURS")
    assert hours["statement"] == "87% of the missed patrols of the last 4 weeks fell between 08:00 and 12:00 (13 of 15)."
    assert hours["confidence"]["why"] == "Rests on 15 records over 4 weeks; the same held within 4 of those weeks."
    assert hours["statement"] in flat and hours["confidence"]["why"] in flat.replace("*", "")
    assert hours["consider"][1:] in flat and hours["confidence"]["level"] == "MEDIUM"
    late = patterns.summarise(_rows([(49 + n % 30, n % 24) for n in range(125)], "CAMERA:1", "Car park"), "UTC", SINCE, 12)
    first = next(f for f in patterns.advise("DEVICE", late, 12, "s") if f["code"] == "FIRST_RECORDED")
    assert first["statement"] in flat and first["statement"].startswith("125 device outages in the last 6 weeks; none")
    assert first["confidence"]["level"] == "LOW" and "rule 4 at work" in flat
    assert not [f for f in patterns.advise("DEVICE", late, 12, "s") if f["code"] == "RISING"]


def test_the_document_gives_confidence_as_the_code_reckons_it():
    section = _section("**Confidence**", "## 4.")
    assert re.findall(r"^\| `([A-Z]+)` \|", section, re.M) == list(patterns.LEVELS)
    flat = _flat(section)
    assert "30 records or more, over 4 weeks or more, and it held in three weeks of every four" in flat
    assert "10 records or more, over 2 weeks or more, and it held in half of them" in flat
    level = lambda *a, **k: patterns.confidence(*a, **k)["level"]   # noqa: E731
    assert [level(30, 4, 3), level(29, 4, 4), level(30, 3, 3), level(30, 4, 2)] == ["HIGH", "MEDIUM", "MEDIUM", "MEDIUM"]
    assert [level(10, 2, 1), level(9, 2, 2), level(10, 1, 1), level(10, 4, 1)] == ["MEDIUM", "LOW", "LOW", "LOW"]
    # A statement with no weeks to hold in is never HIGH; one held down stays down.
    assert level(500, 12) == "MEDIUM" and "or it is a statement with no weeks to hold in" in flat
    assert level(500, 12, 12, at_most="LOW") == "LOW"
    assert "(`why`)" in flat and "(`records`, `weeks`, `held_in_weeks`)" in flat
    assert set(patterns.confidence(12, 4, 2)) == {"level", "why", "records", "weeks", "held_in_weeks"}
    assert "What rests on the most history is given first; then what rests on the most records." in flat
    order = patterns.ranked([{"key": "a", "confidence": {"level": "LOW", "records": 90}},
                             {"key": "b", "confidence": {"level": "MEDIUM", "records": 10}},
                             {"key": "c", "confidence": {"level": "MEDIUM", "records": 40}}])
    assert [f["key"] for f in order] == ["c", "b", "a"]
    # One busy afternoon is one week.
    burst = patterns.summarise(_rows([(22, 14)] * 40 + [(1, 3), (8, 6), (15, 9)], "c1", "Lobby"), "UTC", SINCE, 4)
    one = next(f for f in patterns.advise("INCIDENT", burst, 4, "s") if f["code"] == "RECURRING_HOURS")
    assert one["confidence"]["held_in_weeks"] == 1 and one["confidence"]["level"] == "LOW"
    assert "that is one week in which it held, not four" in _flat(_doc())


def test_an_answer_is_a_persons_is_kept_as_the_advice_stood_and_changes_nothing_else():
    section = _flat(_section("## 4. A person's answer", "## 5."))
    router = _code(Path(api.__file__))
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "(`code:kind:site:subject`)" in section
    assert 'f"{code}:{source}:{scope}:{subject}"' in _code(Path(patterns.__file__))
    assert "(`answer_note`)" in section and '"answer_note": None if site else ANSWER_ONE_SITE' in router
    assert api.ANSWER_ONE_SITE == "Advice is answered for one site. Choose the site it is about."
    assert "can = site is not None and \"advice:answer\" in held" in router
    assert "refuses (422)" in section and 'raise HTTPException(422, "Say why it is not accepted.")' in router
    assert "(`ck_advans_reason`)" in section and "CONSTRAINT ck_advans_reason" in migration
    assert "the answer is refused (409) and nothing is kept" in section
    assert router.index("raise HTTPException(409") < router.index("INSERT INTO risk_advice_answers")
    # What is kept is the server's own statement, not one the caller sent.
    body = router.split("class AnswerBody", 1)[1].split("@router.post", 1)[0]
    assert "statement" not in body and "confidence" not in body and 'extra="forbid"' in body
    assert '"statement": finding["statement"]' in router and '"confidence": finding["confidence"]["level"]' in router
    for kept in ("statement", "rests_on", "confidence", "period_weeks", "period_end", "answer", "reason",
                 "answered_by_user_id", "answered_at"):
        assert re.search(rf"^\s+{kept}\s", migration, re.M), kept
    assert "The application's role may read and add rows, and nothing else." in section
    assert "REVOKE ALL ON risk_advice_answers FROM svc_app" in migration
    assert re.findall(r"GRANT ([A-Z, ()a-z_]+) ON risk_advice_answers TO svc_app", migration) == ["SELECT, INSERT"]
    assert set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", router)) == {"risk_advice_answers"}
    assert "(`said_then`)" in section and '"said_then": answer["statement"] if answer["statement"] != finding["statement"] else None' in router
    assert "DISTINCT ON (a.advice_key)" in router and "the latest answer to it is shown with it" in section
    # An answer starts nothing and tells nobody; it is a person's.
    assert "An answer sends no notification and starts nothing." in section
    for path in (Path(api.__file__), Path(patterns.__file__)):
        for word in ("redis", "response_notify", "send_expo_push", "maintenance_work_orders", "guard_shifts", "dispatch"):
            assert word not in _code(path).lower(), (path.name, word)
    assert "_a_person(token)" in router.split("async def answer_advice", 1)[1]
    doc = _flat(_doc())
    for said in ("The platform advises; a person answers; the answer changes nothing else.",
                 "Accepting advice raises no work, moves no guard and alters no roster.",
                 "An answer is added and never rewritten", "not an API key, not a support session"):
        assert said in doc, said
    page = (WEB / "pages" / "risk" / "RiskAdvice.tsx").read_text(encoding="utf-8")
    assert "f.may_answer" in page and "advice.data.answer_note" in page and "f.answer.said_then" in page
    assert "usePermission" not in page, "whether somebody may answer is the server's to say"


def test_the_document_lists_every_route_what_each_needs_and_what_is_audited():
    section = _section("## 5. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$", section, re.M)}
    served = {(method, path): needs - {"advice:read"} for method, path, needs in _routes(BASE)}
    assert all("advice:read" in needs for _, _, needs in _routes(BASE)), "every route needs advice:read"
    assert table == served and len(served) == 4
    assert not [m for m, _ in served if m in ("PUT", "PATCH", "DELETE")]
    assert "There is no `PUT`, `PATCH` or `DELETE`." in section
    router = Path(api.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r'"(advice\.[a-z_.]+)"', router)) == set(re.findall(r"`(advice\.[a-z_.]+)`", section)) == {"advice.answer"}
    assert "Reading is not audited" in _flat(section) and router.count("intel_audit.record(") == 1


def test_the_document_says_who_holds_what_and_the_migration_and_the_web_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(advice:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"advice:read": {2, 3, 4, 6, 8}, "advice:answer": {2, 3, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code
    assert "Super Admin, a guard and the client role hold neither of the new permissions." in _flat(_doc())
    assert api.PERMISSIONS == CODES

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(advice:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(CODES)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {role: {code for code, holders in granted.items() if role in holders} for role in (3, 4, 5, 6, 7)}
    assert not re.search(pattern, src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0])


def test_the_screen_is_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/risk-advice)',.*permission: '([a-z:]+)'", sidebar) == [("/risk-advice", "advice:read")]
    assert sidebar.index("title: 'Security Intelligence'") < sidebar.index("'/risk-advice'") < sidebar.index("title: 'Drone Patrol'")
    for kept in ("path: '/heatmap',", "path: '/security-insight',", "path: '/situations',", "path: '/analytics',"):
        assert kept in sidebar, f"the existing screen at {kept} keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="risk-advice"', 'path="heatmap"', 'path="security-insight"'):
        assert path in routes
    doc = _flat(_doc())
    assert "(`/risk-advice`), under Security Intelligence, for a site or every site and a period, in three parts" in doc
    assert "The phone is not changed in this phase." in doc
    assert "The existing heatmap (`/heatmap`), the intelligence layer's insight (`/security-insight`) and the drone analytics are unchanged." in doc
    page = (WEB / "pages" / "risk" / "RiskAdvice.tsx").read_text(encoding="utf-8")
    for part in ("What stands out", "Where it gathers", "Answers given"):
        assert f">{part}</Typography>" in page and f"**{part}**" in _doc(), part
    # A week of hours in four steps, with a key in counts, readable without its shade, and the numbers on request.
    words = (WEB / "components" / "risk" / "riskFormat.ts").read_text(encoding="utf-8")
    assert "export const STEPS = 4" in words and "shaded in four steps" in doc
    assert 'data-testid="grid-key"' in page and "title={cellTitle(weekdays[d], hour, count)}" in page
    assert 'label="Show the numbers"' in page and "the numbers themselves on request" in doc
    client = (WEB / "api" / "securityAdvice.ts").read_text(encoding="utf-8")
    kinds = set(re.findall(r"'([A-Z]+)'", client.split("export type Source =", 1)[1].split("\n", 1)[0]))
    assert kinds == set(patterns.SOURCES)
    assert set(re.findall(r"'([A-Z]+)'", client.split("export type Level =", 1)[1].split("\n", 1)[0])) == set(patterns.LEVELS)
    assert set(re.findall(r"'([A-Z_]+)'", client.split("export type Answer =", 1)[1].split("\n", 1)[0])) == {"ACCEPTED", "NOT_ACCEPTED"}
    for path in re.findall(r"`\$\{BASE\}(/[a-z/]+)`", client):
        assert path in {p for _, p, _ in _routes(BASE)}, path
    # No existing reading was touched, and the phone has no part of it.
    services = REPO_ROOT / "backend" / "app" / "services"
    for name in ("intel_insight.py", "drone_analytics.py", "device_health.py", "response_sla.py"):
        source = (services / name).read_text(encoding="utf-8")
        assert "risk_patterns" not in source and "risk_advice" not in source, name
    mobile = REPO_ROOT / "mobile" / "src"
    assert not list(mobile.rglob("*isk*dvice*")) and not list(mobile.rglob("*ecurityAdvice*"))


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
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["risk_advice_answers"]
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"risk_advice_answers"}, "no existing table is altered"
    assert "The table is `risk_advice_answers` and not `security_advice`, as the plan had it" in _flat(_doc())
    assert "The new table refers to `sites` and `users`." in _flat(_doc())
    assert set(re.findall(r"REFERENCES (\w+)\(", upgrade)) == {"tenants", "sites", "users"}
    not_done = _flat(_doc().split("## 9. What this does not do", 1)[1])
    for said in ("It forecasts nothing.", "It makes no risk score.", "It does not allow for how much is watched.",
                 "It does not tell a real record from a test.", "Its places are not zones.",
                 "It does not act on an answer.", "The phone is not part of it."):
        assert said in not_done, said
    for path in (Path(patterns.__file__), Path(api.__file__)):
        assert "score" not in _code(path).lower(), f"{path.name} makes a score"
    built = GAPS.read_text(encoding="utf-8").split("## 9. As built", 1)[1]
    assert "| 9 | Risk and advisor | **Built 2026-10-08**" in built and "SECURITY_RISK_ARCHITECTURE.md" in built
    phase = built.split("### Phase 9", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 9, by additions only:**", 1)[1]
                      .split("The intelligence layer's insight", 1)[0]) == CHANGED
    assert "nothing is forecast" in _flat(phase) and "no risk score is made" in _flat(phase)
