"""Guard response: the document says what the code does, and the web and the phone say what the database grants.

Reads files outside backend/, so the module runs with the repository-inspection
suites.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import app
from app.core.config_keys import SETTING_VALIDATORS
from app.routers import incident_responses as api
from app.services import guard_positions as positions
from app.services import incident_response as responses
from app.services import response_notify as notify
from app.services import response_sla as sla
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "GUARD_RESPONSE_ARCHITECTURE.md"
GAPS = REPO_ROOT / "LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md"
WEB = REPO_ROOT / "frontend" / "src"
PHONE = REPO_ROOT / "mobile" / "src"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0146_incident_responses.py"
BASE = "/api/v1/incident-responses"
CHANGED = ["backend/app/main.py", "backend/app/core/config_keys.py", "backend/app/scheduler_main.py",
           "frontend/src/App.tsx", "frontend/src/components/layout/Sidebar.tsx", "frontend/src/hooks/usePermission.ts",
           "mobile/src/screens/DashboardScreen.tsx", "mobile/src/screens/IncidentDetailScreen.tsx"]


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
            if not path.startswith(BASE) or not getattr(route, "endpoint", None):
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
                yield method, path.removeprefix(BASE), frozenset(needs)


def test_the_document_lists_every_state_and_every_step_a_guard_may_take():
    section = _section("## 2. A response and its steps", "## 3.")
    states = re.findall(r"^\| `([A-Z_]+)` \| [A-Z]", section, re.M)
    assert sorted(states) == sorted(responses.STATES)
    moves = {step: (set(re.findall(r"`([A-Z_]+)`", origin)), to.strip("` "))
             for step, origin, to in re.findall(r"^\| `([A-Z_]+)` \| ([^|]+) \| ([^|]+) \|[^|]*\|$", section, re.M)
             if step in responses.MOVES}
    assert moves == {step: (set(origin), to) for step, (to, origin) in responses.MOVES.items()}
    assert "| `REPORTED` | any state that is not over | no change | — |" in section
    doc = _flat(section)
    assert responses.SUPERSEDED == "The incident was dispatched again." and f'"{responses.SUPERSEDED}"' in doc
    assert "(`uq_response_sending`)" in doc and "uq_response_sending" in MIGRATION.read_text(encoding="utf-8")
    assert responses.SENT_STATUSES == ("open", "in_progress", "dispatched")
    assert 'treats `in_progress` as "sent, not yet set off"' in doc
    assert notify.FRESH_FOR_S == 3600 and "A sending older than an hour is given its record without waking" in doc


def test_what_a_guard_does_is_written_to_the_incidents_own_columns_and_nothing_here_dispatches():
    code = Path(responses.__file__).read_text(encoding="utf-8")
    for written in ("INSERT INTO incident_status_history", "guard_arrived_at = COALESCE(guard_arrived_at, now())",
                    '"en_route"', '"on_scene"'):
        assert written in code, written
    doc = _flat(_doc())
    for said in ("status `en_route`", "status `on_scene`, `guard_arrived_at` stamped", "nobody sent; status `open`"):
        assert said in doc, said
    # Everything the phase may change of an incident. It clears who is sent; it never names anybody.
    may_set = {"status", "updated_at", "guard_arrived_at", "dispatched_guard_id", "dispatched_at",
               "sla_deadline_at", "sla_breached", "escalated_at", "escalated_to_user_id"}
    updates = 0
    for path in (Path(api.__file__), Path(responses.__file__), Path(sla.__file__), Path(notify.__file__)):
        source = path.read_text(encoding="utf-8")
        for assignments in re.findall(r"UPDATE incidents\s+SET (.*?)\bWHERE\b", source, re.S):
            updates += 1
            assert set(re.findall(r"\b([a-z_]+) = ", assignments)) <= may_set, f"{path.name}: {assignments}"
            assert not re.search(r"dispatched_guard_id = (?!NULL)", assignments), f"{path.name} would send somebody"
        for verb in ("INSERT INTO incidents", "dispatch_guard(", "UPDATE shifts", "UPDATE alerts", "DELETE FROM"):
            assert verb not in source, f"{path.name} would act: {verb}"
    assert updates >= 5
    dispatch = (REPO_ROOT / "backend" / "app" / "routers" / "dispatch.py").read_text(encoding="utf-8")
    assert "incident_response" not in dispatch, "the existing dispatch is as it was"
    assert "`POST /api/v1/dispatch/incidents/{id}` is as it was; the new router has no route that dispatches." in doc


def test_the_document_gives_the_parts_of_a_score_as_the_code_scores_them():
    section = _section("## 3. Who to send", "## 4.")
    parts = re.findall(r"^\| `([A-Z_]+)` \| (.+) \|$", section, re.M)
    assert [name for name, _ in parts] == ["AVAILABILITY", "DISTANCE", "POSITION_AGE", "WORKLOAD", "CERTIFICATION"]
    said = dict(parts)

    def points(factor: str, **guard) -> int | None:
        g = {"available": True, "emergency_id": None, "distance_m": 50, "position_age_s": 60, "stale": False, **guard}
        kw = {"sent_this_shift": g.pop("sent", 0), "holds_what_the_site_requires": g.pop("holds", None)}
        return {p["factor"]: p["points"] for p in responses.score(g, **kw)["parts"]}.get(factor)

    assert (points("AVAILABILITY"), points("AVAILABILITY", available=False),
            points("AVAILABILITY", available=False, emergency_id="x")) == (40, 0, -100)
    assert "+40 free" in said["AVAILABILITY"] and "−100" in said["AVAILABILITY"]
    assert [points("DISTANCE", distance_m=d) for d in (100, 300, 1000, 1001)] == [30, 20, 10, 0]
    assert "+30 within 100 m; +20 within 300 m; +10 within 1000 m" in said["DISTANCE"]
    assert [points("POSITION_AGE", position_age_s=s, stale=s > positions.STALE_AFTER_S) for s in (900, 901, 3601)] \
        == [10, 0, -10]
    assert "last 15 minutes" in said["POSITION_AGE"] and "stale (over an hour)" in said["POSITION_AGE"]
    assert [points("WORKLOAD", sent=n) for n in (1, 3, 4)] == [-5, -15, -15]
    assert "−5 for each" in said["WORKLOAD"] and "at most −15" in said["WORKLOAD"]
    assert (points("CERTIFICATION", holds=True), points("CERTIFICATION", holds=False), points("CERTIFICATION")) \
        == (10, 0, None)
    assert "Scored only when the site requires one" in said["CERTIFICATION"]
    doc = _flat(section)
    assert "somebody free before somebody already sent, then the nearer" in doc
    assert "It needs `incident:dispatch`" in doc and "services/guard_positions.py" in doc
    assert "is_decision: false" in _doc() and '"is_decision": False' in Path(responses.__file__).read_text(encoding="utf-8")


def test_the_document_gives_the_clocks_the_switch_and_the_pass():
    section = _section("## 4. The clocks", "## 5.")
    assert re.findall(r"^\| `([A-Z]+)` \|", section, re.M) == list(sla.CLOCKS)
    doc = _flat(section)
    for named in ("`ack_within_seconds`", "`sla_deadline_at`", "`resolve_within_seconds`", "`sla_configs`",
                  "`PUT /api/v1/sla/configs/{severity}`"):
        assert named in doc, named
    assert sla.ENABLED_KEY == "response.sla_enabled" and "`response.sla_enabled`, off by default" in doc
    assert sla.ENABLED_KEY in SETTING_VALIDATORS
    assert sla.MAX_PER_PASS == 2000 and "resolved in the last 24 hours, at most 2000 a pass" in doc
    assert "timedelta(hours=24)" in Path(sla.__file__).read_text(encoding="utf-8")
    assert "an incident sent twice can be late twice" in doc
    scheduler = (REPO_ROOT / "backend" / "app" / "scheduler_main.py").read_text(encoding="utf-8")
    assert "response_sla.run(AsyncSessionLocal, redis)" in scheduler and "runs `response_sla.run` every minute" in doc


def test_the_document_says_who_is_told_and_how():
    section = _section("## 5. Who is told", "## 6.")
    doc = _flat(section)
    for kind in (*sla.BREACH_TYPE.values(), sla.STEP_TYPE):
        assert f"`{kind}`" in section, kind
    assert set(re.findall(r"`(NOT_[A-Z]+)`", section)) == set(sla.TRIGGERS)
    assert "after 30 seconds to 7 days" in doc
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "after_seconds BETWEEN 30 AND 604800" in migration and 604800 == 7 * 24 * 3600
    events = set(re.findall(r"`(incident_[a-z_]+)`", section.split("**Telling**", 1)[1]))
    assert events == {sla.BREACH_EVENT, sla.ESCALATION_EVENT, notify.SENT_EVENT, notify.DECLINED_EVENT,
                      notify.STOOD_DOWN_EVENT}
    for written in ("`incidents.sla_breached` is set", "`incident_escalations`", "`escalation_events`"):
        assert written in doc, written
    code = Path(sla.__file__).read_text(encoding="utf-8")
    assert "SET sla_breached = TRUE" in code and "INSERT INTO escalation_events" in code
    assert "ON CONFLICT DO NOTHING" in code, "recorded once"
    assert "A step is not a breach" in doc and "it does nothing until the switch is on" in doc
    assert sla.NOTIFY_ROLES == (2, 3, 4, 5, 6, 8)
    assert "notify_role_id IN (2, 3, 4, 5, 6, 8)" in migration
    assert "A person held to other sites is not told" in doc and "user_sites" in code
    assert "`notification_sent` is true when the event went to the screens or reached a phone" in doc


def test_the_document_lists_every_route_what_each_needs_and_every_audited_act():
    served = {(method, path.replace("{policy_id:uuid}", "{id}").replace("{incident_id:uuid}", "{incident}")): needs
              for method, path, needs in _routes()}
    section = _section("## 6. API", "**Permissions**")
    table = {(method, path): frozenset(re.findall(r"`([a-z:]+)`", needs))
             for method, path, needs in re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]*)` \|([^|]*)\|$",
                                                   section, re.M)}
    assert table == served and len(served) == 18
    assert "There is no `DELETE`, and no route here dispatches." in _flat(section)
    assert not [m for m, _ in served if m == "DELETE"] and not [p for _, p in served if "dispatch" in p]
    code = Path(api.__file__).read_text(encoding="utf-8")
    # Seven named outright, and one per step a guard takes.
    written = set(re.findall(r'"(response\.[a-z_.]+)"', code)) | {f"response.{word}" for word in api._STEP_WORDS.values()}
    named = set(re.findall(r"`(response\.[a-z_.]+)`", section))
    assert named == written and len(named) == 12


def test_the_document_says_who_holds_what_and_the_migration_the_web_and_the_phone_agree():
    migration = MIGRATION.read_text(encoding="utf-8")
    granted: dict[str, set[int]] = {}
    for role, code in re.findall(r"\((\d), '(response:[a-z]+)'\)", migration):
        granted.setdefault(code, set()).add(int(role))
    assert granted == {"response:read": {2, 3, 4, 6, 8}, "response:act": {2, 3, 4, 5, 8}}
    header = re.search(r"^\| \| Admin 2 \|.*$", _doc(), re.M).group(0)
    roles = [int(n) for n in re.findall(r" (\d) \|", header)]
    for code, holders in granted.items():
        cells = re.search(rf"^\| `{code}` \|(.*)\|$", _doc(), re.M).group(1).split("|")
        assert {role for role, cell in zip(roles, cells) if "✓" in cell} == holders, code

    src = (WEB / "hooks" / "usePermission.ts").read_text(encoding="utf-8")
    pattern = r"'(response:[a-z]+)'"
    assert set(re.findall(pattern, src.split("const PLATFORM_PERMISSIONS")[0])) == set(granted)
    table = src.split("const ROLE_PERMISSIONS", 1)[1].split("export function usePermission", 1)[0]
    web = {role: set(re.findall(pattern, re.search(rf"\n  {role}: \[(.*?)\n  \],", table, re.S).group(1)))
           for role in (3, 4, 5, 6, 7)}
    assert web == {3: {"response:read", "response:act"}, 4: {"response:read", "response:act"}, 5: {"response:act"},
                   6: {"response:read"}, 7: set()}
    assert "response" not in src.split("const PLATFORM_PERMISSIONS", 1)[1].split("]", 1)[0]
    # The phone asks the server what the person holds, and shows the card to whoever may act.
    card = (PHONE / "components" / "ResponseCard.tsx").read_text(encoding="utf-8")
    assert "canSee({ permission: 'response:act' }" in card


def test_the_screens_are_in_the_menu_and_the_existing_ones_are_as_they_were():
    sidebar = (WEB / "components" / "layout" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert re.findall(r"path: '(/response-desk|/response-settings)',.*permission: '([a-z:]+)'", sidebar) == [
        ("/response-desk", "response:read"), ("/response-settings", "sla:manage")]
    assert "{ label: 'Incidents',      path: '/incidents'," in sidebar, "the existing screen keeps its entry"
    routes = (WEB / "App.tsx").read_text(encoding="utf-8")
    for path in ('path="response-desk"', 'path="response-settings"', 'path="incidents"'):
        assert path in routes
    for said in ("(`/response-desk`)", "(`/response-settings`)",
                 "The existing Incidents screen and its dispatch dialog are unchanged."):
        assert said in _doc()
    # The desk sends a guard through the dispatch the web has always had.
    dialogs = (WEB / "components" / "response" / "ResponseDialogs.tsx").read_text(encoding="utf-8")
    assert "import { dispatchGuard } from '@/api/guards'" in dialogs
    client = (WEB / "api" / "incidentResponses.ts").read_text(encoding="utf-8")
    assert "dispatch/incidents" not in client and "/api/v1/sla/configs/" in client
    states = set(re.findall(r"'([A-Z_]+)'", client.split("export type ResponseState =", 1)[1].split("\n", 1)[0]))
    assert states == set(responses.STATES)


def test_the_phone_calls_routes_that_exist_and_offers_the_steps_in_the_documents_words():
    calls = (PHONE / "api" / "responses.ts").read_text(encoding="utf-8")
    assert "const BASE = '/api/v1/incident-responses'" in calls
    used = set(re.findall(r"`\$\{BASE\}/\$\{incidentId\}/([a-z-]+)`", calls)) | set(re.findall(r"`\$\{BASE\}/([a-z]+)`", calls))
    assert used == {"accept", "decline", "en-route", "arrived", "report", "mine"}
    served = {(method, path): needs for method, path, needs in _routes()}
    for name in used - {"mine"}:
        assert served[("POST", f"/{{incident_id:uuid}}/{name}")] == {"response:act"}, name
    assert served[("GET", "/mine")] == {"response:act"}
    assert "stand-down" not in calls and "/dispatch/" not in calls, "a guard answers; the desk sends"
    rules = (PHONE / "lib" / "responses.ts").read_text(encoding="utf-8")
    labels = re.findall(r"label: '([^']+)'", rules)
    assert labels == ["Accept", "On my way", "I am there", "Report what I found", "I cannot attend"]
    assert "Accept, On my way, I am there, Report what I found, I cannot attend." in _flat(_doc())
    for screen, card in (("DashboardScreen.tsx", "<SentCard />"),
                         ("IncidentDetailScreen.tsx", "<ResponseCard incidentId={params.incidentId} />")):
        assert card in (PHONE / "screens" / screen).read_text(encoding="utf-8"), screen
    # The phone's own status button is as it was: the workflow it walks is unchanged.
    detail = (PHONE / "screens" / "IncidentDetailScreen.tsx").read_text(encoding="utf-8")
    assert "in_progress" not in detail and "open: 'dispatched'," in detail


def test_the_files_the_document_names_exist_and_the_gap_analysis_records_the_phase():
    for path in re.findall(r"^\| `([a-z_/.0-9A-Za-z]+)` \|", _section("## 8. Files", "## 9."), re.M):
        assert (REPO_ROOT / path).exists(), path
    for path in re.findall(r"^\| `((?:backend/tests|frontend/src|mobile/src)/[A-Za-z_/.]+)` \|",
                           _doc().split("## 9. Tests", 1)[1], re.M):
        assert (REPO_ROOT / path).exists(), path
    changed = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                         _doc().split("Existing files changed", 1)[1].split("**Existing columns", 1)[0])
    assert changed == CHANGED
    first = _section("**Existing columns and an existing table now written for the first time:**", "---")
    assert re.findall(r"`([a-z_.]+)`", first)[:4] == ["incidents.sla_breached", "incidents.escalated_at",
                                                      "incidents.escalated_to_user_id", "escalation_events"]
    gaps = GAPS.read_text(encoding="utf-8")
    built = gaps.split("## 9. As built", 1)[1]
    assert "| 4 | Dispatch, SLA, escalation | **Built 2026-10-07**" in built and "GUARD_RESPONSE_ARCHITECTURE.md" in built
    phase = built.split("### Phase 4", 1)[1]
    assert re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`",
                      phase.split("**Existing files changed in phase 4, by additions only:**", 1)[1]
                      .split("The existing dispatch", 1)[0]) == CHANGED
    assert "a guard could never use it" in _flat(phase) and "`in_progress`" in phase
