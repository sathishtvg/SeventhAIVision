"""The operations reports: records the platform already keeps, taken out as a file.

  A — A value, a heading and a file, with nothing running
  B — The reports there are, and who may have which
  C — Each report, from the database
  D — Whose records a file holds
  E — What taking a report out does, and does not

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a report is the records as they are, read under
their own permission as well as the one to take a report out; text a
spreadsheet would run is made plain text; a time says its time zone; a file
that is cut says so; somebody held to particular sites is given those sites'
records; taking a report out is audited and changes nothing.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import operations_reports as api
from app.services import ops_reports as reports
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit
from tests.test_operations_board import _seed

BASE = "/api/v1/operations-reports"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0153_operations_reports.py").read_text(encoding="utf-8")
SGT = ZoneInfo("Asia/Singapore")
#: A name somebody might type into a visitor form to have a spreadsheet run it.
FORMULA = "=cmd|' /C calc'!A0"


def _file(response) -> list[dict]:
    """A report as it would be opened: its rows, by heading."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith(b"\xef\xbb\xbf"), "a byte-order mark, so names in any script open as written"
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def _col(rows: list[dict], heading: str) -> list[str]:
    """One column, by the start of its heading — a heading of times ends with its time zone."""
    (name,) = [k for k in rows[0] if k == heading or k.startswith(heading + " (")]
    return [r[name] for r in rows]


# ─── A. A value, a heading and a file ────────────────────────────────────────

def test_a_value_is_written_as_it_is_and_what_a_spreadsheet_would_run_is_made_text():
    assert [reports.cell(v, SGT) for v in (None, True, False, 0, 42, -5, 240.4, Decimal("12.50"), date(2026, 10, 7))] == [
        "", "Yes", "No", "0", "42", "-5", "240", "12.50", "2026-10-07"]
    # A time is written where the organisation is.
    assert reports.cell(datetime(2026, 10, 7, 16, 30, tzinfo=timezone.utc), SGT) == "2026-10-08 00:30:00"
    assert reports.cell(datetime(2026, 10, 7, 16, 30, tzinfo=timezone.utc), ZoneInfo("UTC")) == "2026-10-07 16:30:00"
    # Text somebody typed that begins as a formula does is read as text.
    for typed in (FORMULA, "+65 6123 4567", "-Gate 2", "@here", "\tTabbed", "\rReturned"):
        assert reports.cell(typed, SGT) == "'" + typed, typed
    for plain in ("Mei Lin", "Gate 2 - north", "a=b", "65 6123 4567", ""):
        assert reports.cell(plain, SGT) == plain
    assert reports.RUNS == ("=", "+", "-", "@", "\t", "\r")


def test_a_file_has_its_headings_a_line_for_each_row_and_says_when_it_was_cut():
    report = reports.BY_KEY["visitors"]
    heads = reports.headings(report, "Asia/Singapore")
    assert heads[0] == "Asked for at (Asia/Singapore)" and heads[1] == "Site"
    assert [h for h in heads if h.endswith("(Asia/Singapore)")] == [
        "Asked for at (Asia/Singapore)", "Valid from at (Asia/Singapore)", "Valid until at (Asia/Singapore)",
        "Decided at (Asia/Singapore)"]
    row = {"requested_at": datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc), "site": "Factory A", "subject": FORMULA,
           "purpose": 'Says "hello", then\nleaves', "escort_required": True, "id_seen": False, "state": "REQUESTED"}
    text = reports.as_csv(report, [row], "Asia/Singapore")
    lines = list(csv.reader(io.StringIO(text)))
    assert lines[0] == heads and len(lines) == 2 and text.endswith("\r\n")
    got = dict(zip(heads, lines[1]))
    assert got["Asked for at (Asia/Singapore)"] == "2026-10-07 09:00:00"
    assert got["Visitor or permit"] == "'" + FORMULA and got["Purpose"] == 'Says "hello", then\nleaves'
    assert (got["Escort required"], got["Identity document seen"], got["Host"]) == ("Yes", "No", "")
    cut = list(csv.reader(io.StringIO(reports.as_csv(report, [row], "Asia/Singapore", cut=True))))
    assert cut[-1] == ["Cut at 10,000 records. Narrow the period or choose one site to have the rest."]
    assert reports.MAX_ROWS == 10000 and reports.PERIOD_DAYS == (1, 7, 30, 90)


def test_the_reports_are_lists_of_records_each_under_its_own_permission():
    keys = [r.key for r in reports.REPORTS]
    assert keys == ["board-sites", "response", "device-health", "maintenance", "visitors", "access", "risk",
                    "evidence", "investigations"] and len(set(keys)) == 9
    assert {r.key: r.needs for r in reports.REPORTS} == {
        "board-sites": ("board:read",), "response": ("response:read", "incident:read"),
        "device-health": ("asset:read",), "maintenance": ("maintenance:read",), "visitors": ("visitorauth:read",),
        "access": ("access:read",), "risk": ("advice:read",), "evidence": ("evidence:package:read",),
        "investigations": ("investigation:read",)}
    assert [r.key for r in reports.REPORTS if not r.periodic] == ["device-health", "risk"]
    for r in reports.REPORTS:
        assert len({k for k, _ in r.columns}) == len(r.columns) == len({h for _, h in r.columns}), r.key
        assert not re.search(r"\b(score|rating|rank|grade)\b", " ".join(h for _, h in r.columns), re.I), r.key
        # The export permission alone opens none of them.
        assert not reports.may_have(r, {"opsreport:export"}) and reports.may_have(r, set(r.needs))
    assert "opsreport:export" not in api.PERMISSIONS
    assert api._why_not(reports.BY_KEY["response"], frozenset({"response:read"})) == (
        "Its records are read under incident:read, which you do not hold.")
    assert api._why_not(reports.BY_KEY["response"], frozenset({"response:read", "incident:read"})) is None
    # A row of the board is blank, not zero, where a section was not read.
    row = reports._board_row({"name": "Factory A", "client_name": None}, {"GUARDS": {
        "on_shift_now": 2, "due_not_started_now": 0, "shifts": 3, "worked": 3, "late": 0, "not_started": 0}})
    assert row["on_shift_now"] == 2 and "incidents_opened" not in row and "devices_down" not in row
    assert set(row) <= {k for k, _ in reports.BY_KEY["board-sites"].columns}
    code = Path(reports.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", code), "the reports only read"


# ─── B. The reports there are ────────────────────────────────────────────────

async def test_the_list_says_what_each_report_holds_and_only_some_may_take_one_out():
    w = await _world()
    async with _client() as c:
        for role in (ADMIN, MANAGER, SUPERVISOR):
            r = await c.get(BASE, headers=w["h"][role])
            assert r.status_code == 200, r.text
            listed = r.json()
            assert [x["key"] for x in listed["reports"]] == [x.key for x in reports.REPORTS]
            assert all(x["may"] and x["why_not"] is None for x in listed["reports"])
        by_key = {x["key"]: x for x in listed["reports"]}
        assert by_key["response"]["needs"] == ["response:read", "incident:read"]
        assert by_key["response"]["columns"][0].startswith("Opened at (") and by_key["response"]["periodic"] is True
        assert by_key["device-health"]["periodic"] is False and "has no period" in by_key["device-health"]["holds"]
        assert "not a forecast" in by_key["risk"]["holds"]
        assert (listed["periods"], listed["max_rows"], listed["note"]) == ([1, 7, 30, 90], 10000, api.NOTE)
        # Reading records on a screen is not taking them away: an operator and a viewer may not.
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.get(BASE, headers=w["h"][role])).status_code == 403
            assert (await c.get(f"{BASE}/response", headers=w["h"][role])).status_code == 403
        assert (await c.get(BASE)).status_code in (401, 403)
    assert await _audit(w, "report.export") == [], "looking at the list takes nothing out"


# ─── C. Each report, from the database ───────────────────────────────────────

async def _more(w: dict) -> dict:
    """What the board's day does not have: door events, an evidence package and an investigation."""
    t, a = w["tenant"], w["site_a"]
    m = {k: uuid.uuid4() for k in ("door", "card", "package", "investigation")}
    await _run([
        ("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Server room')", {"i": m["door"], "t": t, "s": a}),
        ("INSERT INTO access_credentials (id, tenant_id, credential_ref, holder_name) VALUES (:i,:t,'C-1',:n)",
         {"i": m["card"], "t": t, "n": FORMULA}),
        *[("INSERT INTO access_events (tenant_id, door_id, credential_id, event_type, denial_reason, occurred_at) "
           "VALUES (:t,:d,:c,:k,:why, now() - make_interval(secs => :s))",
           {"t": t, "d": m["door"], "c": card, "k": kind, "why": why, "s": hours * 3600})
          for card, kind, why, hours in ((m["card"], "denied", "Outside its hours", 5), (m["card"], "granted", None, 4),
                                         (None, "forced", None, 3), (m["card"], "granted", None, 60))],
        ("INSERT INTO evidence_packages (id, tenant_id, site_id, package_number, title, purpose, created_by_user_id) "
         "VALUES (:i,:t,:s,'EP-0001','Forced gate, 7 October','For the client''s insurer',:u)",
         {"i": m["package"], "t": t, "s": a, "u": w["users"][MANAGER]}),
        ("INSERT INTO evidence_package_items (tenant_id, package_id, kind, ref_id, captured_at) "
         "VALUES (:t,:p,'SNAPSHOT',gen_random_uuid(), now()), (:t,:p,'RECORDING',gen_random_uuid(), now())",
         {"t": t, "p": m["package"]}),
        ("INSERT INTO investigations (id, tenant_id, site_id, investigation_number, title, reason, opened_by_user_id) "
         "VALUES (:i,:t,:s,'INV-0001','Who forced the gate','Reported by the client',:u)",
         {"i": m["investigation"], "t": t, "s": a, "u": w["users"][SUPERVISOR]}),
        ("UPDATE visitors SET full_name = :n WHERE tenant_id = :t AND full_name = 'Arun K'", {"n": FORMULA, "t": t}),
    ])
    return m


async def test_each_report_holds_the_records_as_they_are():
    w = await _world()
    await _seed(w)
    await _more(w)
    site = {"site_id": str(w["site_a"])}
    async with _client() as c:
        h = w["h"][MANAGER]

        board = _file(await c.get(f"{BASE}/board-sites", headers=h, params={"days": 1}))
        assert [r["Site"] for r in board] == ["Factory A", "Factory B", "At no site"]
        a = board[0]
        assert (a["Incidents opened"], a["Incidents resolved"], a["Incidents open now"]) == ("4", "2", "4")
        assert (a["Incidents acted on"], a["Guards sent"], a["Guards arrived"]) == ("3", "2", "1")
        assert abs(int(a["Middle seconds until acted on"]) - 240) <= 2 and abs(int(a["Middle seconds from sent to arrived"]) - 360) <= 2
        assert (a["Clocks missed: acknowledge"], a["Clocks missed: arrival"], a["Clocks missed: resolve"]) == ("1", "0", "1")
        assert (a["Guard tours done"], a["Guard tours over"], a["Guard tours missed"]) == ("1", "2", "1")
        assert (a["Virtual patrols done"], a["Virtual patrols over"], a["Virtual patrols missed or failed"]) == ("1", "3", "1")
        assert (a["Drone patrols done"], a["Drone patrols over"], a["Drone patrols missed or failed"]) == ("1", "3", "2")
        assert (a["Guards on shift now"], a["Shifts due to begin"], a["Shifts worked"], a["Shifts not started"]) == ("1", "4", "2", "1")
        assert (a["Devices"], a["Devices down"], a["Devices with no reading"]) == ("3", "1", "1")
        assert (a["Visitors on site now"], a["Arrivals logged"], a["Orders overdue now"], a["Orders completed"]) == ("2", "2", "1", "1")
        # No time was measured at site B: that is blank, not zero.
        assert board[1]["Incidents opened"] == "1" and board[1]["Middle seconds until acted on"] == ""
        assert board[2]["Incidents opened"] == "1" and board[2]["Customer"] == ""

        response = _file(await c.get(f"{BASE}/response", headers=h, params={**site, "days": 1}))
        assert _col(response, "Severity") == ["critical", "high", "high", "medium"], "oldest first"
        assert [abs(int(s) - want) <= 2 for s, want in zip(_col(response, "Seconds until acted on")[:3], (120, 240, 600))] == [True] * 3
        # Nothing was done with the fourth: no time, not a nought.
        assert _col(response, "Seconds until acted on")[3] == "" and _col(response, "First acted on at")[3] == ""
        assert _col(response, "Guards sent") == ["0", "2", "0", "0"] and _col(response, "First arrival at")[1] != ""
        assert _col(response, "Clocks missed") == ["", "", "", "acknowledge, resolve"]
        assert abs(int(_col(response, "Seconds until resolved")[2]) - 600) <= 2
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", _col(response, "Opened at")[0])
        assert set(_col(response, "Site")) == {"Factory A"} and set(_col(response, "Camera")) == {"Gate A"}

        health = _file(await c.get(f"{BASE}/device-health", headers=h, params=site))
        assert sorted(_col(health, "Device")) == ["Dock", "Gate A", "Unwired"] and set(_col(health, "Kind")) == {"Camera"}
        states = dict(zip(_col(health, "Device"), _col(health, "Read as")))
        assert (states["Dock"], states["Unwired"]) == ("Down", "Not known"), "what is not known is not called working"
        assert all(why for why, state in zip(_col(health, "Why"), _col(health, "Read as")) if state != "Working")
        # It has no period: any is taken, and it is the same file.
        assert len(_file(await c.get(f"{BASE}/device-health", headers=h, params={**site, "days": 5}))) == 3

        orders = _file(await c.get(f"{BASE}/maintenance", headers=h, params={**site, "days": 1}))
        assert sorted(_col(orders, "Order")) == [f"WO-000{n}" for n in (1, 2, 3, 4, 5)]
        by_number = {r["Order"]: r for r in orders}
        assert by_number["WO-0003"]["What was done"] == "Reseated the cable" and by_number["WO-0003"]["State"] == "DONE"
        assert (by_number["WO-0004"]["State"], by_number["WO-0004"]["Put forward by"]) == ("SUGGESTED", "HEALTH")
        assert by_number["WO-0004"]["Why it was put forward"] == "Down for 5 hours." and by_number["WO-0001"]["Why it was put forward"] == ""
        assert _col(orders, "Completed at").count("") == 4 and _col(orders, "Accepted at").count("") == 4

        visitors = _file(await c.get(f"{BASE}/visitors", headers=h, params={**site, "days": 1}))
        assert sorted(_col(visitors, "State")) == ["APPROVED", "REQUESTED"]
        asked = next(r for r in visitors if r["State"] == "REQUESTED")
        # What somebody typed as their name is in the file as text, not as something to run.
        assert asked["Visitor or permit"] == "'" + FORMULA
        assert (asked["Escort required"], asked["Identity document seen"], asked["Decided by"]) == ("No", "No", "")
        assert next(r for r in visitors if r["State"] == "APPROVED")["Visitor or permit"] == "Mei Lin"

        doors = _file(await c.get(f"{BASE}/access", headers=h, params={**site, "days": 1}))
        assert _col(doors, "Event") == ["denied", "granted", "forced"] and set(_col(doors, "Door")) == {"Server room"}
        assert _col(doors, "Why refused") == ["Outside its hours", "", ""]
        assert _col(doors, "Held by") == ["'" + FORMULA, "'" + FORMULA, ""] and _col(doors, "Credential") == ["C-1", "C-1", ""]
        assert len(_file(await c.get(f"{BASE}/access", headers=h, params={**site, "days": 7}))) == 4

        packages = _file(await c.get(f"{BASE}/evidence", headers=h, params={**site, "days": 1}))
        (package,) = packages
        assert (package["Package"], package["Status"], package["Made by"], package["Items"]) == ("EP-0001", "DRAFT", "Role 8 User", "2")
        assert (_col(packages, "Sealed at"), package["Holds in force"], package["Purpose"]) == ([""], "0", "For the client's insurer")
        (inquiry,) = _file(await c.get(f"{BASE}/investigations", headers=h, params={**site, "days": 1}))
        assert (inquiry["Investigation"], inquiry["Status"], inquiry["Opened by"], inquiry["Items held"]) == (
            "INV-0001", "OPEN", "Role 3 User", "0")

        # What stands out is the advice of the same site, statement for statement.
        advice = (await c.get("/api/v1/security-advice/advice", headers=h, params=site)).json()["findings"]
        risk = await c.get(f"{BASE}/risk", headers=h, params=site)
        assert risk.status_code == 200
        lines = list(csv.reader(io.StringIO(risk.content.decode("utf-8-sig"))))
        assert lines[0][:3] == ["Kind", "What stands out", "Rests on"] and len(lines) == 1 + len(advice)
        assert [line[1] for line in lines[1:]] == [f["statement"] for f in advice]
        assert {line[2] for line in lines[1:]} <= set(reports.LEVEL_WORDS.values())



# ─── D. Whose records a file holds ───────────────────────────────────────────

async def test_a_file_holds_the_records_of_the_sites_its_reader_may_see():
    w, other = await _world(), await _world()
    await _seed(w)
    await _more(w)
    customer = uuid.uuid4()
    await _run([
        ("INSERT INTO billing_clients (id, tenant_id, name) VALUES (:i,:t,'Acme Properties')", {"i": customer, "t": w["tenant"]}),
        ("UPDATE sites SET client_id = :c WHERE id = :b", {"c": customer, "b": w["site_b"]}),
    ])
    async with _client() as c:
        # Every site, for somebody who is not held: site B's incident and the one at no site are in it.
        everything = _file(await c.get(f"{BASE}/response", headers=w["h"][ADMIN], params={"days": 1}))
        assert sorted(_col(everything, "Site")) == ["", "Factory A", "Factory A", "Factory A", "Factory A", "Factory B"]
        # Somebody held to site A is given site A's, whatever they ask for.
        mine = _file(await c.get(f"{BASE}/response", headers=w["h"][SUPERVISOR], params={"days": 1}))
        assert _col(mine, "Site") == ["Factory A"] * 4
        assert [r["Site"] for r in _file(await c.get(f"{BASE}/board-sites", headers=w["h"][SUPERVISOR], params={"days": 1}))] == ["Factory A"]
        for key in ("response", "access", "visitors", "board-sites"):
            r = await c.get(f"{BASE}/{key}", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"]), "days": 1})
            assert r.status_code == 404 and r.json()["detail"] == "Site not found", key
        # By customer: that customer's sites.
        theirs = _file(await c.get(f"{BASE}/response", headers=w["h"][ADMIN], params={"client_id": str(customer), "days": 1}))
        assert _col(theirs, "Site") == ["Factory B"]
        sites = _file(await c.get(f"{BASE}/board-sites", headers=w["h"][ADMIN], params={"client_id": str(customer), "days": 1}))
        assert [(r["Site"], r["Customer"]) for r in sites] == [("Factory B", "Acme Properties")]
        assert (await c.get(f"{BASE}/response", headers=w["h"][ADMIN], params={"client_id": str(uuid.uuid4())})).status_code == 404
        # Another organisation's file has its headings and nothing under them.
        for key in ("response", "maintenance", "visitors", "access", "evidence", "investigations", "device-health", "risk"):
            r = await c.get(f"{BASE}/{key}", headers=other["h"][ADMIN], params={"days": 90})
            assert r.status_code == 200 and len(list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))) == 1, key
        assert (await c.get(f"{BASE}/response", headers=other["h"][ADMIN], params={"site_id": str(w["site_a"])})).status_code == 404


# ─── E. What taking a report out does, and does not ──────────────────────────

async def test_taking_a_report_out_is_audited_refused_where_it_should_be_and_changes_nothing():
    w = await _world()
    await _seed(w)
    site = str(w["site_a"])
    counts = ("SELECT (SELECT count(*) FROM incidents WHERE tenant_id = :t) AS incidents, "
              "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
              "(SELECT count(*) FROM maintenance_work_orders WHERE tenant_id = :t) AS orders, "
              "(SELECT count(*) FROM daily_briefings WHERE tenant_id = :t) AS briefings")
    (before,) = await _sql(counts, {"t": w["tenant"]})
    async with _client() as c:
        h = w["h"][MANAGER]
        r = await c.get(f"{BASE}/response", headers=h, params={"site_id": site, "days": 1})
        assert re.fullmatch(r'attachment; filename="operations-response-\d{8}\.csv"', r.headers["content-disposition"])
        assert (r.headers["x-report-rows"], r.headers["x-report-cut"]) == ("4", "false")
        assert (await c.get(f"{BASE}/device-health", headers=w["h"][SUPERVISOR])).status_code == 200
        for path, params, status, words in (
            ("/payroll", {}, 404, "There is no such report"),
            ("/response", {"days": 2}, 422, "1, 7, 30 or 90 days"), ("/response", {"days": 365}, 422, "1, 7, 30 or 90 days"),
            ("/response", {"site_id": str(uuid.uuid4())}, 404, "Site not found"),
            ("/response", {"site_id": "not-a-site"}, 422, None),
        ):
            r = await c.get(BASE + path, headers=h, params=params)
            assert r.status_code == status, (path, params, r.text)
            if words:
                assert words in str(r.json()["detail"])
        for method in ("post", "put", "patch", "delete"):
            assert (await getattr(c, method)(f"{BASE}/response", headers=h)).status_code == 405

        # A file that is cut says so, in its last line and in its headers.
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(reports, "MAX_ROWS", 2)
            cut = await c.get(f"{BASE}/response", headers=h, params={"site_id": site, "days": 1})
        lines = list(csv.reader(io.StringIO(cut.content.decode("utf-8-sig"))))
        assert len(lines) == 1 + 2 + 1 and lines[-1] == ["Cut at 2 records. Narrow the period or choose one site to have the rest."]
        assert (cut.headers["x-report-rows"], cut.headers["x-report-cut"]) == ("2", "true")

    # A key may take a report out; a support session may not.
    for token, status in (
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True), 200),
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN,
                      support_session_id=str(uuid.uuid4())), 403),
    ):
        app.dependency_overrides[get_token_payload] = lambda token=token: token
        try:
            async with _client() as c:
                assert (await c.get(f"{BASE}/maintenance", params={"days": 7})).status_code == status
        finally:
            app.dependency_overrides.pop(get_token_payload, None)

    entries = await _audit(w, "report.export")
    taken = [(e["detail"]["report"], e["detail"]["rows"], e["detail"]["days"], e["detail"]["cut"]) for e in entries]
    assert taken == [("response", 4, 1, False), ("device-health", 3, None, False), ("response", 2, 1, True),
                     ("maintenance", 5, 7, False)]
    assert [e["user_id"] for e in entries[:2]] == [w["users"][MANAGER], w["users"][SUPERVISOR]]
    assert entries[0]["detail"]["every_site"] is False and entries[1]["detail"]["every_site"] is False
    (after,) = await _sql(counts, {"t": w["tenant"]})
    assert dict(before) == dict(after), "a report takes records out; it changes none"
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", router)
    for word in ("redis", "smtp", "webhook", "send_expo_push"):
        assert word not in router.lower(), "a report is handed to whoever asked, and sent nowhere"


def _needs(route) -> set[str]:
    found: set[str] = set()

    def walk(dep):
        if "require_permission" in getattr(dep.call, "__qualname__", ""):
            found.update(c.cell_contents for c in (dep.call.__closure__ or ()) if isinstance(c.cell_contents, str))
        for sub in dep.dependencies:
            walk(sub)

    for d in route.dependant.dependencies:
        walk(d)
    return found


def test_every_route_asks_for_the_permission_to_take_a_report_out():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if (path == BASE or path.startswith(BASE + "/")) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.removeprefix(BASE) or "/")] = _needs(route)
    assert served == {("GET", "/"): {"opsreport:export"}, ("GET", "/{key}"): {"opsreport:export"}}


async def test_who_may_take_a_report_out_and_that_nothing_is_stored_of_one():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code = 'opsreport:export' GROUP BY p.code, p.category")
    assert [(r["code"], r["category"], list(r["roles"])) for r in rows] == [("opsreport:export", "operations", [2, 3, 8])]
    # Every permission a report's records are read under is one that exists.
    wanted = sorted({code for r in reports.REPORTS for code in r.needs})
    known = await _sql("SELECT code FROM permissions WHERE code = ANY(:c)", {"c": wanted})
    assert sorted(k["code"] for k in known) == wanted
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert "CREATE TABLE" not in upgrade and "ALTER TABLE" not in upgrade and "TRUNC" + "ATE" not in upgrade
    (layer,) = await _sql("SELECT count(*) AS n FROM permissions WHERE category = 'security_intelligence'")
    assert layer["n"] == 7
