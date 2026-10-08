"""The operations board: what each part of the operation counts, by site and by customer.

  A — Adding and sharing, with nothing running
  B — Each section, counted from the database
  C — Who is shown what
  D — Sites and customers
  E — What the board does not do

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a figure is a count of what is recorded for the
period and the sites asked for; a time is a middle time and is never summed; a
section is read under its own permission and what is left out is named;
somebody held to particular sites sees those sites; nothing is written, scored
or named.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.routers import operations_board as api
from app.services import device_health, ops_board
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world

BASE = "/api/v1/operations-board"
EVERYTHING = frozenset(api.SOURCE_PERMISSIONS)


def _ago(**k) -> dict:
    """How long ago, as seconds, for `now() - make_interval(secs => :s)`."""
    return {"s": timedelta(**k).total_seconds()}


def _by_key(sections: list[dict]) -> dict:
    return {s["key"]: s["figures"] for s in sections}


# ─── A. Adding and sharing ───────────────────────────────────────────────────

def test_figures_are_added_but_a_middle_time_is_not():
    a, b = ops_board.blank("RESPONSE", EVERYTHING), ops_board.blank("RESPONSE", EVERYTHING)
    a.update(opened=4, acknowledged=3, acknowledge_seconds=240.0, missed={"acknowledge": 1, "arrival": 0, "resolve": 1})
    b.update(opened=1, acknowledged=1, acknowledge_seconds=30.0, missed={"acknowledge": 0, "arrival": 2, "resolve": 0})
    both = ops_board.add(a, b)
    assert (both["opened"], both["acknowledged"]) == (5, 4)
    assert both["missed"] == {"acknowledge": 1, "arrival": 2, "resolve": 1}
    # A middle time of two sites is not the sum, the mean or either of them: it is not given.
    assert both["acknowledge_seconds"] is None and both["resolve_seconds"] is None and both["arrive_seconds"] is None
    assert ops_board.add(a, b) == ops_board.add(b, a)


def test_a_part_nobody_may_read_stays_unread_when_added_and_a_share_of_nothing_is_nothing():
    without = EVERYTHING - {"vpatrol:read", "visitorauth:read"}
    patrols = ops_board.blank("PATROLS", without)
    assert patrols["virtual"] is None and patrols["tours"]["scheduled"] == 0 and patrols["drone"]["scheduled"] == 0
    assert ops_board.add(patrols, ops_board.blank("PATROLS", without))["virtual"] is None
    assert ops_board.blank("VISITORS", without)["waiting_now"] is None
    assert ops_board.blank("VISITORS", EVERYTHING)["waiting_now"] == 0
    assert [ops_board.share(18, 20), ops_board.share(1, 3), ops_board.share(0, 5)] == [90, 33, 0]
    # None of none is not 0% and not 100%.
    assert ops_board.share(0, 0) is None and ops_board.share(None, 4) is None
    assert ops_board.over({"scheduled": 9, "done": 3, "partial": 2, "missed": 1, "failed": 1, "open": 2, "cancelled": 4}) == 7


def test_what_is_not_read_is_named_with_the_permission_it_wants():
    assert ops_board.not_read(EVERYTHING) == []
    left = ops_board.not_read(EVERYTHING - {"incident:read", "vpatrol:read", "visitorauth:read"})
    assert [(n["key"], n["needs"]) for n in left] == [("INCIDENTS", "incident:read"), ("virtual", "vpatrol:read"),
                                                      ("waiting_now", "visitorauth:read")]
    assert all("which you do not hold" in n["reason"] for n in left)
    # A part of a section that is itself not read is not named twice.
    assert [n["key"] for n in ops_board.not_read(EVERYTHING - {"patrol:read", "vpatrol:read"})] == ["PATROLS"]
    assert set(ops_board.SECTIONS) == set(ops_board.TITLE) == set(ops_board.NEEDS) == set(ops_board.COUNTED_FROM)
    assert set(api.SOURCE_PERMISSIONS) == {*ops_board.NEEDS.values(), *ops_board.PART_NEEDS.values(), "advice:read"}


# ─── B. Each section, counted from the database ──────────────────────────────

async def _seed(w: dict) -> dict:
    """A day at site A with something of everything, a little at site B, and
    one incident at no site. Each row is a known time ago."""
    t, a, b = w["tenant"], w["site_a"], w["site_b"]
    s: dict = {k: uuid.uuid4() for k in ("cam", "dock", "dark", "far", "i1", "i2", "i3", "i4", "old", "late", "b1",
                                         "none", "route", "tour", "v1", "v2", "v3", "v4")}
    guard, other = w["users"][GUARD], w["users"][OPERATOR]
    incident = ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at, resolved_at) "
                "VALUES (:i,:t,:c,'Forced gate',:sev,:st, now() - make_interval(secs => :s), "
                "        CASE WHEN CAST(:r AS double precision) IS NULL THEN NULL "
                "             ELSE now() - make_interval(secs => CAST(:r AS double precision)) END)")

    def opened(key: str, camera, severity: str, status: str, hours: float, resolved_hours_ago: float | None = None):
        return (incident, {"i": s[key], "t": t, "c": camera, "sev": severity, "st": status, "s": hours * 3600,
                           "r": None if resolved_hours_ago is None else resolved_hours_ago * 3600})

    await _run([
        *[("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)", {"i": s[k], "t": t, "s": site, "n": n})
          for k, site, n in (("cam", a, "Gate A"), ("dock", a, "Dock"), ("dark", a, "Unwired"), ("far", b, "Gate B"))],
        *[("INSERT INTO streams (tenant_id, camera_id, url, status) VALUES (:t,:c,'rtsp://x',:st)", {"t": t, "c": s[k], "st": st})
          for k, st in (("cam", "online"), ("dock", "offline"))],
        opened("i1", s["cam"], "critical", "investigating", 6),
        opened("i2", s["cam"], "high", "dispatched", 5),
        opened("i3", s["cam"], "high", "resolved", 4, 4 - 10 / 60),          # resolved ten minutes after it was opened
        opened("i4", s["cam"], "medium", "open", 3),                         # nothing done with it
        opened("old", s["cam"], "low", "open", 40 * 24),                     # before any period, and still open
        opened("late", s["cam"], "low", "resolved", 3 * 24, 2),              # opened three days ago, resolved today
        opened("b1", s["far"], "low", "open", 2),
        opened("none", None, "critical", "open", 1),                         # raised by hand: no camera, so no site
        ("INSERT INTO incident_status_history (tenant_id, incident_id, to_status, changed_at) "
         "VALUES (:t,:i,'investigating', now() - make_interval(secs => :s))", {"t": t, "i": s["i1"], **_ago(hours=6, minutes=-2)}),
        ("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state, declined_at, decline_reason) "
         "VALUES (:t,:i,:site,:g, now() - make_interval(secs => :s), 'DECLINED', now(), 'On another call')",
         {"t": t, "i": s["i2"], "site": a, "g": other, **_ago(hours=5, minutes=-5)}),
        ("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state, accepted_at, arrived_at) "
         "VALUES (:t,:i,:site,:g, now() - make_interval(secs => :s), 'ARRIVED', now() - make_interval(secs => :s), "
         "        now() - make_interval(secs => :s) + interval '6 minutes')",
         {"t": t, "i": s["i2"], "site": a, "g": guard, **_ago(hours=5, minutes=-4)}),
        *[("INSERT INTO incident_escalations (tenant_id, incident_id, site_id, kind, clock, policy_name, due_at, created_at) "
           "VALUES (:t,:i,:site,:k,:c,:p, now(), now() - make_interval(secs => :s))",
           {"t": t, "i": s[of], "site": a, "k": kind, "c": clock, "p": policy, "s": hours * 3600})
          # A clock is recorded as missed once for an incident.
          for of, kind, clock, policy, hours in (("i4", "SLA_BREACH", "ACKNOWLEDGE", None, 3), ("i4", "SLA_BREACH", "RESOLVE", None, 2),
                                                 ("i4", "POLICY_STEP", "ARRIVAL", "Tell the manager", 2),
                                                 ("late", "SLA_BREACH", "ACKNOWLEDGE", None, 71))],
        ("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
         "VALUES (:t,'response.sla_enabled','true'::jsonb,:u)", {"t": t, "u": w["users"][ADMIN]}),
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')", {"i": s["route"], "t": t, "s": a}),
        ("INSERT INTO tour_schedules (id, tenant_id, route_id, name, scheduled_time) VALUES (:i,:t,:r,'Perimeter tour','22:00')",
         {"i": s["tour"], "t": t, "r": s["route"]}),
        *[("INSERT INTO tour_occurrences (tenant_id, schedule_id, scheduled_at, window_end, status) "
           "VALUES (:t,:sch, now() - make_interval(secs => :s), now() - make_interval(secs => :s) + interval '1 hour', :st)",
           {"t": t, "sch": s["tour"], "st": status, "s": hours * 3600})
          for status, hours in (("completed", 5), ("missed", 4), ("pending", 0.5), ("completed", 72))],
        *[("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, status) "
           "VALUES (:t,:site,:n,'Night round', now() - make_interval(secs => :s), :st)",
           {"t": t, "site": a, "n": f"VP-{n}", "st": status, "s": hours * 3600})
          for n, (status, hours) in enumerate((("COMPLETED", 6), ("PARTIALLY_COMPLETED", 5), ("MISSED", 4), ("CANCELLED", 3),
                                               ("SCHEDULED", 1), ("FAILED", 48)))],
        *[("INSERT INTO drone_patrol_sessions (tenant_id, site_id, session_number, status, scheduled_for) "
           "VALUES (:t,:site,:n,:st, CASE WHEN CAST(:s AS double precision) IS NULL THEN NULL "
           "                              ELSE now() - make_interval(secs => CAST(:s AS double precision)) END)",
           {"t": t, "site": a, "n": f"DP-{n}", "st": status, "s": None if hours is None else hours * 3600})
          for n, (status, hours) in enumerate((("COMPLETED", 6), ("BLOCKED", 5), ("ABORTED", 4), ("ACTIVE", None)))],
        *[("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status, is_late) "
           "VALUES (:t,:site,:g, now() - make_interval(secs => :from), now() - make_interval(secs => :to), "
           "        CASE WHEN :worked THEN now() - make_interval(secs => :from) END, :st, :late)",
           {"t": t, "site": site, "g": w["users"][who], "from": start * 3600, "to": end * 3600, "worked": worked,
            "st": status, "late": late})
          for site, who, start, end, worked, status, late in (
              (a, GUARD, 5, -3, True, "active", True),            # being worked now, started late
              (a, OPERATOR, 10, 2, True, "completed", False),
              (a, VIEWER, 9, 1, False, "scheduled", False),       # over, and never started
              (a, MANAGER, 1, -7, False, "scheduled", False),     # due now, not started
              (a, ADMIN, 72, 64, True, "completed", False),       # three days ago
              (b, SUPERVISOR, 30, -2, True, "active", False))],   # at site B, began before the day
        *[("INSERT INTO visitors (id, tenant_id, site_id, full_name, qr_token, status, arrived_at, departed_at) "
           "VALUES (:i,:t,:site,:n,:q,:st, CASE WHEN :in THEN now() - interval '3 hours' END, "
           "        CASE WHEN :out THEN now() - interval '1 hour' END)",
           {"i": s[k], "t": t, "site": a, "n": name, "q": uuid.uuid4().hex, "st": status, "in": came, "out": left})
          for k, name, status, came, left in (("v1", "Mei Lin", "arrived", False, False), ("v2", "Arun K", "pending", False, False),
                                               ("v3", "Sara B", "departed", True, True), ("v4", "Van 12", "pending", True, False))],
        *[("INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, event_type, occurred_at) "
           "VALUES (:t,:v,:site,:k, now() - make_interval(secs => :s))", {"t": t, "v": s[v], "site": a, "k": kind, "s": hours * 3600})
          for v, kind, hours in (("v1", "arrival", 3), ("v3", "arrival", 5), ("v3", "departure", 1), ("v2", "denied", 2),
                                 ("v3", "arrival", 72))],
        ("INSERT INTO visitor_authorizations (tenant_id, site_id, visitor_id, state, valid_from, valid_until) "
         "VALUES (:t,:site,:v,'REQUESTED', now(), now() + interval '4 hours')", {"t": t, "site": a, "v": s["v2"]}),
        ("INSERT INTO visitor_authorizations (tenant_id, site_id, visitor_id, state, valid_from, valid_until, decided_at) "
         "VALUES (:t,:site,:v,'APPROVED', now(), now() + interval '4 hours', now())", {"t": t, "site": a, "v": s["v1"]}),
        *[("INSERT INTO maintenance_work_orders (tenant_id, site_id, number, title, kind, state, origin, origin_key, "
           "    suggestion_reason, raised_by_user_id, raised_at, accepted_at, due_at, started_at, completed_at, completion_note) "
           "VALUES (:t,:site,:n,'Look at the gate camera','CORRECTIVE',CAST(:st AS text),:o,:ok,:why,:u, now() - make_interval(secs => :raised), "
           "        CASE WHEN CAST(:acc AS double precision) IS NULL THEN NULL ELSE now() - make_interval(secs => CAST(:acc AS double precision)) END, "
           "        CASE WHEN CAST(:due AS double precision) IS NULL THEN NULL ELSE now() - make_interval(secs => CAST(:due AS double precision)) END, "
           "        CASE WHEN CAST(:st AS text) IN ('IN_PROGRESS','DONE') THEN now() - interval '5 hours' END, "
           "        CASE WHEN CAST(:st AS text) = 'DONE' THEN now() - interval '3 hours' END, "
           "        CASE WHEN CAST(:st AS text) = 'DONE' THEN 'Reseated the cable' END)",
           {"t": t, "site": a, "n": f"WO-{n:04d}", "st": state, "o": origin, "ok": f"k{n}" if origin != "PERSON" else None,
            "why": "Down for 5 hours." if origin != "PERSON" else None, "u": w["users"][MANAGER] if origin == "PERSON" else None,
            "raised": raised * 3600, "acc": None if accepted is None else accepted * 3600,
            "due": None if due is None else due * 3600})
          for n, (state, origin, raised, accepted, due) in enumerate((
              ("OPEN", "PERSON", 2, None, 1),                 # raised today, and overdue
              ("IN_PROGRESS", "PERSON", 72, None, -24),
              ("DONE", "PERSON", 48, None, None),             # completed today
              ("SUGGESTED", "HEALTH", 1, None, None),         # waiting for a person: not yet work
              ("OPEN", "SCHEDULE", 48, 4, None)), start=1)],  # put forward two days ago, accepted today
    ])
    return s


A_DAY = {
    "INCIDENTS": {"opened": 4, "by_severity": {"critical": 1, "high": 2, "medium": 1, "low": 0}, "resolved": 2,
                  "opened_still_open": 3, "open_now": 4},
    "PATROLS": {"tours": {"scheduled": 3, "done": 1, "partial": 0, "missed": 1, "failed": 0, "open": 1, "cancelled": 0},
                "virtual": {"scheduled": 4, "done": 1, "partial": 1, "missed": 1, "failed": 0, "open": 1, "cancelled": 1},
                "drone": {"scheduled": 4, "done": 1, "partial": 0, "missed": 0, "failed": 2, "open": 1, "cancelled": 0}},
    "GUARDS": {"on_shift_now": 1, "due_not_started_now": 1, "shifts": 4, "worked": 2, "late": 1, "not_started": 1},
    "VISITORS": {"on_site_now": 2, "arrived": 2, "departed": 1, "refused": 1, "waiting_now": 1},
    "MAINTENANCE": {"suggested_now": 1, "open_now": 2, "in_progress_now": 1, "overdue_now": 1, "raised": 2, "done": 1},
}


async def test_each_section_counts_what_is_recorded_for_the_period_and_the_sites():
    w, other = await _world(), await _world()
    await _seed(w)
    site = str(w["site_a"])
    async with _client() as c:
        r = await c.get(BASE, headers=w["h"][ADMIN], params={"site_id": site})
        assert r.status_code == 200, r.text
        day = r.json()
        got = _by_key(day["sections"])
        assert [s["key"] for s in day["sections"]] == list(ops_board.SECTIONS) and day["not_read"] == []
        for key, figures in A_DAY.items():
            assert got[key] == figures, key
        # The response: three of the four opened had something done with them — after two, four and ten minutes.
        response = got["RESPONSE"]
        assert (response["opened"], response["acknowledged"], response["resolved"]) == (4, 3, 1)
        assert response["acknowledge_seconds"] == pytest.approx(240, abs=2) and response["resolve_seconds"] == pytest.approx(600, abs=2)
        assert (response["sent"], response["arrived"], response["declined"]) == (2, 1, 1)
        assert response["arrive_seconds"] == pytest.approx(360, abs=2)
        # A step of an escalation policy is not a missed clock.
        assert response["missed"] == {"acknowledge": 1, "arrival": 0, "resolve": 1}
        assert day["clocks_on_since"] is not None
        assert day["period"]["days"] == 1 and day["scope"]["site"]["name"] == "Factory A" and day["scope"]["sites"] == 1
        assert day["note"] == ops_board.NOTE and "score" in day["note"]
        assert {s["key"]: s["counted_from"] for s in day["sections"]} == ops_board.COUNTED_FROM
        # Devices are the health reading's own count for the same site.
        health = (await c.get("/api/v1/security-assets/health", headers=w["h"][ADMIN], params={"site_id": site})).json()
        assert got["DEVICES"]["devices"] == 3 == health["summary"]["devices"]
        assert got["DEVICES"]["by_state"] == health["summary"]["by_state"]
        assert got["DEVICES"]["by_state"]["DOWN"] == 1 and got["DEVICES"]["by_state"]["NOT_KNOWN"] == 1

        # A week takes in what is three days old; what is forty days old is only ever "open now".
        week = _by_key((await c.get(BASE, headers=w["h"][ADMIN], params={"site_id": site, "days": 7})).json()["sections"])
        assert week["INCIDENTS"] == {**A_DAY["INCIDENTS"], "opened": 5,
                                     "by_severity": {"critical": 1, "high": 2, "medium": 1, "low": 1}}
        assert (week["RESPONSE"]["acknowledged"], week["RESPONSE"]["resolved"]) == (4, 2)
        assert week["RESPONSE"]["acknowledge_seconds"] == pytest.approx(420, abs=2), "the middle of four is between the two in the middle"
        assert week["RESPONSE"]["missed"]["acknowledge"] == 2
        assert week["PATROLS"]["tours"] == {**A_DAY["PATROLS"]["tours"], "scheduled": 4, "done": 2}
        assert week["PATROLS"]["virtual"]["failed"] == 1 and week["PATROLS"]["virtual"]["scheduled"] == 5
        assert (week["GUARDS"]["shifts"], week["GUARDS"]["worked"]) == (5, 3)
        assert (week["VISITORS"]["arrived"], week["MAINTENANCE"]["raised"], week["MAINTENANCE"]["done"]) == (3, 4, 1)
        # What stands as it is now does not change with the period.
        for key, figure in (("INCIDENTS", "open_now"), ("GUARDS", "on_shift_now"), ("VISITORS", "on_site_now"),
                            ("MAINTENANCE", "suggested_now"), ("MAINTENANCE", "overdue_now")):
            assert week[key][figure] == A_DAY[key][figure], (key, figure)
        for days in (0, 2, 31, 365):
            r = await c.get(BASE, headers=w["h"][ADMIN], params={"days": days})
            assert r.status_code == 422 and "1, 7 or 30 days" in r.json()["detail"]

        # Every site together takes in site B, and the incident that is at no site.
        every = (await c.get(BASE, headers=w["h"][ADMIN])).json()
        both = _by_key(every["sections"])
        assert every["scope"] == {"site": None, "client": None, "sites": 2, "every_site": True}
        assert both["INCIDENTS"]["opened"] == 6 and both["INCIDENTS"]["by_severity"] == {"critical": 2, "high": 2, "medium": 1, "low": 1}
        assert both["INCIDENTS"]["open_now"] == 6 and both["GUARDS"]["on_shift_now"] == 2 and both["DEVICES"]["devices"] == 4
        site_b = _by_key((await c.get(BASE, headers=w["h"][ADMIN], params={"site_id": str(w["site_b"])})).json()["sections"])
        assert (site_b["INCIDENTS"]["opened"], site_b["GUARDS"]["on_shift_now"], site_b["GUARDS"]["shifts"]) == (1, 1, 0)
        assert site_b["PATROLS"]["tours"]["scheduled"] == 0 and site_b["RESPONSE"]["acknowledge_seconds"] is None
        assert site_b["VISITORS"] == ops_board.blank("VISITORS", EVERYTHING)

        # Another organisation counts nothing of this one.
        theirs = (await c.get(BASE, headers=other["h"][ADMIN], params={"days": 30})).json()
        for key, figures in _by_key(theirs["sections"]).items():
            assert figures == ops_board.blank(key, EVERYTHING), key
        assert theirs["clocks_on_since"] is None
        assert (await c.get(BASE, headers=other["h"][ADMIN], params={"site_id": site})).status_code == 404


# ─── C. Who is shown what ────────────────────────────────────────────────────

async def test_somebody_held_to_sites_sees_those_sites_and_a_part_is_read_under_its_own_permission():
    w = await _world()
    await _seed(w)
    async with _client() as c:
        # The supervisor is held to site A: "every site" is that one, and what has no site is not theirs.
        mine = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        assert mine["scope"] == {"site": None, "client": None, "sites": 1, "every_site": False}
        got = _by_key(mine["sections"])
        for key, figures in A_DAY.items():
            assert got[key] == figures, key
        assert got["DEVICES"]["devices"] == 3
        assert (await c.get(BASE, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(BASE, headers=w["h"][SUPERVISOR], params={"site_id": str(uuid.uuid4())})).status_code == 404

        # A viewer may not read virtual patrols: that part is not given, and is named.
        seen = (await c.get(BASE, headers=w["h"][VIEWER], params={"site_id": str(w["site_a"])})).json()
        patrols = _by_key(seen["sections"])["PATROLS"]
        assert patrols["virtual"] is None and patrols["tours"] == A_DAY["PATROLS"]["tours"]
        assert [(n["key"], n["needs"]) for n in seen["not_read"]] == [("virtual", "vpatrol:read")]
        for role in (ADMIN, MANAGER, OPERATOR):
            assert (await c.get(BASE, headers=w["h"][role])).json()["not_read"] == []
        # A guard, and somebody not signed in, are not shown the board.
        for path in ("", "/sites"):
            assert (await c.get(BASE + path, headers=w["h"][GUARD])).status_code == 403
            assert (await c.get(BASE + path)).status_code in (401, 403)

        # How much advice stands for the same sites, for whoever may read advice.
        advice = (await c.get(BASE, headers=w["h"][OPERATOR], params={"site_id": str(w["site_a"])})).json()["advice"]
        assert advice["weeks"] == 4 and advice["standing"] == sum(advice["by_level"].values())
        assert set(advice["by_level"]) == {"HIGH", "MEDIUM", "LOW"} and "not a forecast" in advice["note"]


async def test_a_section_the_caller_may_not_read_is_not_counted_at_all():
    w = await _world()
    await _seed(w)
    now = datetime.now(timezone.utc)
    held = frozenset({"shift:read", "visitor:read"})
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        board = await ops_board.read(db, held, [w["site_a"]], now - timedelta(days=1), now, now)
        nothing = await ops_board.read(db, frozenset(), None, now - timedelta(days=1), now, now)
        await db.rollback()
    assert set(board["total"]) == {"GUARDS", "VISITORS"} and board["clocks_on_since"] is None
    assert board["total"]["GUARDS"] == A_DAY["GUARDS"]
    assert board["total"]["VISITORS"] == {**A_DAY["VISITORS"], "waiting_now": None}
    assert set(board["sites"]) == {str(w["site_a"])} and set(board["sites"][str(w["site_a"])]) == {"GUARDS", "VISITORS"}
    assert [n["key"] for n in board["not_read"]] == ["INCIDENTS", "RESPONSE", "PATROLS", "DEVICES", "MAINTENANCE", "waiting_now"]
    assert nothing["total"] == {} and nothing["sites"] == {} and len(nothing["not_read"]) == len(ops_board.SECTIONS)


# ─── D. Sites and customers ──────────────────────────────────────────────────

async def test_each_site_has_its_figures_and_a_customers_sites_are_summed_without_their_times():
    w = await _world()
    await _seed(w)
    customer, lone, closed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO billing_clients (id, tenant_id, name) VALUES (:i,:t,'Acme Properties')", {"i": customer, "t": w["tenant"]}),
        ("UPDATE sites SET client_id = :c WHERE id IN (:a, :b)", {"c": customer, "a": w["site_a"], "b": w["site_b"]}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Annex')", {"i": lone, "t": w["tenant"]}),
        ("INSERT INTO sites (id, tenant_id, name, is_active) VALUES (:i,:t,'Closed yard', FALSE)", {"i": closed, "t": w["tenant"]}),
    ])
    async with _client() as c:
        r = await c.get(f"{BASE}/sites", headers=w["h"][ADMIN])
        assert r.status_code == 200, r.text
        view = r.json()
        # Every site in use, by name — one with nothing counted included, one not in use and with nothing left out.
        assert [s["name"] for s in view["sites"]] == ["Annex", "Factory A", "Factory B"]
        annex, a, b = view["sites"]
        assert annex["client"] is None and annex["figures"]["INCIDENTS"] == ops_board.blank("INCIDENTS", EVERYTHING)
        assert a["client"] == {"id": str(customer), "name": "Acme Properties"}
        for key, figures in A_DAY.items():
            assert a["figures"][key] == figures, key
        assert (b["figures"]["INCIDENTS"]["opened"], b["figures"]["GUARDS"]["on_shift_now"]) == (1, 1)
        assert [s["key"] for s in view["sections"]] == list(ops_board.SECTIONS)
        # What is at no site is given apart, and is in the total.
        assert view["no_site"]["INCIDENTS"]["opened"] == 1 and view["no_site"]["INCIDENTS"]["by_severity"]["critical"] == 1
        assert view["total"]["INCIDENTS"]["opened"] == 6

        (acme,) = view["clients"]
        assert (acme["name"], acme["sites"]) == ("Acme Properties", 2)
        assert acme["figures"]["INCIDENTS"]["opened"] == 5 and acme["figures"]["INCIDENTS"]["open_now"] == 5
        assert acme["figures"]["GUARDS"]["on_shift_now"] == 2 and acme["figures"]["DEVICES"]["devices"] == 4
        assert acme["figures"]["PATROLS"]["virtual"] == A_DAY["PATROLS"]["virtual"]
        # A customer's middle time is not the sum of its sites' — it is not given there.
        assert a["figures"]["RESPONSE"]["acknowledge_seconds"] == pytest.approx(240, abs=2)
        for figure in ("acknowledge_seconds", "resolve_seconds", "arrive_seconds"):
            assert acme["figures"]["RESPONSE"][figure] is None
        assert acme["figures"]["RESPONSE"]["acknowledged"] == 3
        # Asked for by customer, it is counted for those sites — and then the time is a real one.
        one = (await c.get(BASE, headers=w["h"][ADMIN], params={"client_id": str(customer)})).json()
        assert one["scope"]["client"] == {"id": str(customer), "name": "Acme Properties"} and one["scope"]["sites"] == 2
        assert one["scope"]["every_site"] is False
        counted = _by_key(one["sections"])
        assert counted["INCIDENTS"]["opened"] == 5 and counted["RESPONSE"]["acknowledge_seconds"] == pytest.approx(240, abs=2)
        theirs = (await c.get(f"{BASE}/sites", headers=w["h"][ADMIN], params={"client_id": str(customer)})).json()
        assert [s["name"] for s in theirs["sites"]] == ["Factory A", "Factory B"] and theirs["no_site"] is None
        assert theirs["total"]["INCIDENTS"]["opened"] == 5 and theirs["client"]["name"] == "Acme Properties"
        for path in ("", "/sites"):
            r = await c.get(BASE + path, headers=w["h"][ADMIN], params={"client_id": str(uuid.uuid4())})
            assert r.status_code == 404 and r.json()["detail"] == "Customer not found"

        # A site not in use is still shown when something is counted at it.
        await _sql("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, status) "
                   "VALUES (:t,:s,:g, now() - interval '2 hours', now() + interval '6 hours', 'scheduled')",
                   {"t": w["tenant"], "s": closed, "g": w["users"][ADMIN]})
        names = [s["name"] for s in (await c.get(f"{BASE}/sites", headers=w["h"][ADMIN])).json()["sites"]]
        assert names == ["Annex", "Closed yard", "Factory A", "Factory B"]

        # Somebody held to site A is given site A, the customer as far as they see it, and nothing of no site.
        held = (await c.get(f"{BASE}/sites", headers=w["h"][SUPERVISOR])).json()
        assert [s["name"] for s in held["sites"]] == ["Factory A"] and held["no_site"] is None
        assert [(k["name"], k["sites"]) for k in held["clients"]] == [("Acme Properties", 1)]
        assert held["total"]["INCIDENTS"]["opened"] == 4


# ─── E. What the board does not do ───────────────────────────────────────────

async def test_the_board_reads_and_does_nothing_else():
    w = await _world()
    await _seed(w)
    before = await _sql("SELECT (SELECT count(*) FROM audit_logs WHERE tenant_id = :t) AS audits, "
                        "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
                        "(SELECT count(*) FROM daily_briefings WHERE tenant_id = :t) AS briefings", {"t": w["tenant"]})
    async with _client() as c:
        for path, params in (("", {}), ("", {"days": 30}), ("/sites", {}), ("/sites", {"days": 7})):
            assert (await c.get(BASE + path, headers=w["h"][MANAGER], params=params)).status_code == 200
        for method in ("post", "put", "patch", "delete"):
            assert (await getattr(c, method)(BASE, headers=w["h"][ADMIN])).status_code == 405
    after = await _sql("SELECT (SELECT count(*) FROM audit_logs WHERE tenant_id = :t) AS audits, "
                       "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
                       "(SELECT count(*) FROM daily_briefings WHERE tenant_id = :t) AS briefings", {"t": w["tenant"]})
    assert dict(before[0]) == dict(after[0]), "reading the board keeps nothing and tells nobody"
    for module in (ops_board, api):
        code = Path(module.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
        assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", code), f"{module.__name__} only reads"
        for word in ("redis", "response_notify", "send_expo_push", "intel_audit"):
            assert word not in code, (module.__name__, word)
        # Nothing is scored or ranked, and nobody is named.
        assert not re.search(r"def \w*(score|rating|grade|rank)|ORDER BY \w+ DESC LIMIT", code, re.I), module.__name__
        assert "full_name" not in code and "guard_user_id" not in code

    def keys(figures) -> list[str]:
        return [k for key, v in figures.items() for k in ([key] + (keys(v) if isinstance(v, dict) else []))]

    for section in ops_board.SECTIONS:
        assert not [k for k in keys(ops_board.blank(section, EVERYTHING)) if re.search(r"score|rating|grade|rank|index", k)]
    # No model: fixed counts only.
    for name in ("sklearn", "numpy", "torch", "anthropic", "openai"):
        assert not re.search(rf"^\s*(import|from)\s+{name}\b", Path(ops_board.__file__).read_text(encoding="utf-8"), re.M)
    # The acknowledgement the board times is the one the response clocks reckon.
    from app.services import response_sla
    flat = lambda s: " ".join(s.split())   # noqa: E731
    assert flat(ops_board.ACKNOWLEDGED_AT) in flat(response_sla.INCIDENTS)
    assert set(device_health.STATES) == set(ops_board.blank("DEVICES", EVERYTHING)["by_state"])


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


def test_every_route_asks_for_the_board_and_only_reads():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if (path == BASE or path.startswith(BASE + "/")) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.removeprefix(BASE) or "/")] = _needs(route)
    assert served == {("GET", "/"): {"board:read"}, ("GET", "/sites"): {"board:read"}}


async def test_who_holds_the_board_and_what_it_is_read_under():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code = 'board:read' GROUP BY p.code, p.category")
    assert [(r["code"], r["category"], list(r["roles"])) for r in rows] == [("board:read", "operations", [2, 3, 4, 6, 8])]
    # Each section is read under a permission that exists, and that its own screen already asks for.
    known = {r["code"] for r in await _sql("SELECT code FROM permissions WHERE code = ANY(:c)", {"c": list(api.SOURCE_PERMISSIONS)})}
    assert known == set(api.SOURCE_PERMISSIONS)
    # The intelligence layer's own permissions are as they were.
    (layer,) = await _sql("SELECT count(*) AS n FROM permissions WHERE category = 'security_intelligence'")
    assert layer["n"] == 7
