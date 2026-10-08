"""Where recorded events gather, the advice made of that, and what a person answered.

  A — Counting and the rules, with nothing running
  B — The five kinds of record, counted from the database
  C — Advice: what it says, how much history it rests on, and who sees what
  D — A person's answer
  E — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: nothing is a forecast; confidence is how much
history a statement rests on and a burst in one week is one week; nothing
before is not called a rise; nobody is named; an answer is a person's, is kept
as the advice stood when it was given, and changes nothing else.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import security_advice as api
from app.services import risk_patterns as patterns
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_incident_responses import _guard
from tests.test_investigation_search import _audit

BASE = "/api/v1/security-advice"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0151_risk_advice_answers.py").read_text(encoding="utf-8")
CLIENT = 7
SGT = "Asia/Singapore"
#: Words that would make a count sound like a forecast.
FORETELLING = ("will ", "likely", "predict", "expect", "forecast", "probab", "chance", "risk of")
NOW = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)   # a Thursday, noon in Singapore


def _rows(since: datetime, when: list[tuple[float, float]], key: str = "cam1", name: str = "Loading bay") -> list[dict]:
    """Records at so many days and hours after the period began."""
    return [{"at": since + timedelta(days=d, hours=h), "site_id": "s", "place_key": key, "place": name} for d, h in when]


# ─── A. Counting and the rules ───────────────────────────────────────────────

def test_records_are_counted_by_the_hour_and_day_where_the_organisation_is():
    since = patterns.period(NOW, 2)
    assert NOW - since == timedelta(weeks=2)
    # 14:30 UTC on the first Saturday is 22:30 on Saturday in Singapore; 17:00 UTC is 01:00 on Sunday.
    rows = _rows(since, [(2, 10.5), (2, 13), (9, 10.5)]) + _rows(since, [(3, 2)], "cam2", "Gate 1")
    s = patterns.summarise(rows, SGT, since, 2)
    assert s["total"] == 4 and s["by_week"] == [3, 1]
    assert s["grid"][5][22] == 2 and s["grid"][6][1] == 1, "by the local hour of the local day"
    assert len(s["grid"]) == 7 and all(len(day) == 24 for day in s["grid"])
    assert sum(sum(day) for day in s["grid"]) == s["total"]
    assert s["places"] == [{"key": "cam1", "name": "Loading bay", "count": 3, "share": 75},
                           {"key": "cam2", "name": "Gate 1", "count": 1, "share": 25}]
    # The same records in another zone are other hours: the zone is not decoration.
    assert patterns.summarise(rows, "UTC", since, 2)["grid"][5][22] == 0
    empty = patterns.summarise([], SGT, since, 2)
    assert empty["total"] == 0 and empty["places"] == [] and empty["by_week"] == [0, 0]
    assert "_in_week" not in patterns.shown(s) and patterns.shown(s)["total"] == 4


def test_the_busiest_band_of_hours_wraps_past_midnight_and_takes_the_earliest_on_a_tie():
    by_hour = [0] * 24
    by_hour[23], by_hour[0], by_hour[1] = 3, 4, 2
    assert patterns.busiest_band(by_hour) == (22, 9), "22:00 to 02:00 holds all nine"
    assert patterns.busiest_band([1] * 24) == (0, 4)
    assert patterns.BAND_HOURS == 4 and patterns.busiest_band([0] * 24) == (0, 0)


def test_confidence_is_how_much_history_a_statement_rests_on():
    high = patterns.confidence(40, 4, 3)
    assert high == {"level": "HIGH", "why": "Rests on 40 records over 4 weeks; the same held within 3 of those weeks.",
                    "records": 40, "weeks": 4, "held_in_weeks": 3}
    # Thirty records, four weeks, three weeks of every four: each is needed.
    assert patterns.confidence(29, 4, 4)["level"] == "MEDIUM"
    assert patterns.confidence(40, 3, 3)["level"] == "MEDIUM"
    assert patterns.confidence(40, 4, 2)["level"] == "MEDIUM"
    # Many records in one week is one week.
    assert patterns.confidence(500, 4, 1)["level"] == "LOW"
    assert patterns.confidence(9, 4, 4)["level"] == "LOW" and patterns.confidence(40, 1, 1)["level"] == "LOW"
    # A comparison of two halves has no weeks to hold in, and is never HIGH.
    compared = patterns.confidence(400, 12)
    assert compared["level"] == "MEDIUM" and compared["held_in_weeks"] is None
    assert compared["why"] == "Rests on 400 records over 12 weeks."
    assert patterns.confidence(400, 12, at_most="LOW")["level"] == "LOW"
    assert patterns.confidence(1, 1)["why"] == "Rests on 1 record over 1 week."
    assert "not a probability" in patterns.CONFIDENCE_NOTE and "not a forecast" in patterns.NOTE


def test_a_pattern_is_stated_with_its_count_and_the_weeks_it_held_in():
    since = patterns.period(NOW, 4)
    saturday_nights = [(7 * w + 2, 10 + 0.4 * k) for w in range(4) for k in range(5)]   # 22:00 to 23:36, Singapore
    elsewhere = [(7 * w + 4, 3) for w in range(4)] + [(7 * w + 5, 6) for w in range(4)]
    s = patterns.summarise(_rows(since, saturday_nights) + _rows(since, elsewhere, "cam2", "Gate 1"), SGT, since, 4)
    found = {f["code"]: f for f in patterns.advise("INCIDENT", s, 4, "site-1")}
    assert set(found) == {"RECURRING_HOURS", "RECURRING_DAY", "RECURRING_PLACE"}
    hours = found["RECURRING_HOURS"]
    # Three bands of four hours hold the same twenty; the earliest is the one named.
    assert hours["statement"] == "71% of the incidents of the last 4 weeks fell between 20:00 and 00:00 (20 of 28)."
    assert hours["rests_on"] == {"weeks": 4, "from_hour": 20, "to_hour": 0, "in_band": 20, "of": 28}
    assert hours["confidence"]["why"] == "Rests on 28 records over 4 weeks; the same held within 4 of those weeks."
    assert hours["key"] == "RECURRING_HOURS:INCIDENT:site-1:20" and hours["source_label"] == "Incidents"
    assert found["RECURRING_DAY"]["statement"] == "71% of the incidents of the last 4 weeks fell on a Saturday (20 of 28)."
    assert found["RECURRING_DAY"]["key"] == "RECURRING_DAY:INCIDENT:site-1:SATURDAY"
    assert found["RECURRING_PLACE"]["statement"] == (
        "Loading bay accounts for 71% of the incidents of the last 4 weeks (20 of 28).")
    for f in found.values():
        assert f["is_advisory"] is True and f["is_forecast"] is False and f["consider"]
        assert f["confidence"]["level"] == "MEDIUM", "twenty-eight records are not thirty"
    # Two more a week, in the band, and it rests on enough: thirty-six records, four weeks, all four holding.
    more = patterns.summarise(_rows(since, saturday_nights + [(7 * w + 2, 8.2 + k / 10) for w in range(4) for k in range(2)])
                              + _rows(since, elsewhere, "cam2", "Gate 1"), SGT, since, 4)
    assert {f["code"]: f["confidence"]["level"] for f in patterns.advise("INCIDENT", more, 4, "s")}["RECURRING_HOURS"] == "HIGH"


def test_one_busy_afternoon_is_one_week_and_too_few_records_say_nothing():
    since = patterns.period(NOW, 4)
    # Forty in one afternoon of the last week; one a week before that, at other hours.
    burst = [(23, 5 + k / 20) for k in range(40)] + [(1, 20), (8, 20), (15, 20)]
    found = {f["code"]: f for f in patterns.advise("DEVICE", patterns.summarise(_rows(since, burst), SGT, since, 4), 4, "s")}
    hours = found["RECURRING_HOURS"]
    assert hours["rests_on"]["in_band"] == 40 and hours["confidence"]["held_in_weeks"] == 1
    assert hours["confidence"]["level"] == "LOW", "forty-three records, and one week in which it held"
    # Below the floor a rule does not speak, whatever the share.
    few = patterns.summarise(_rows(since, [(1, 3), (8, 3), (15, 3), (22, 3)]), SGT, since, 4)
    assert patterns.FLOOR == 5 and patterns.advise("INCIDENT", few, 4, "s") == []
    # One place is every record's place: that says nothing about the place.
    one_place = patterns.summarise(_rows(since, [(d, 3 + d) for d in range(1, 12)]), SGT, since, 4)
    assert "RECURRING_PLACE" not in {f["code"] for f in patterns.advise("INCIDENT", one_place, 4, "s")}
    # A single week has no other week to hold in, so a weekday is not called a pattern.
    week = patterns.summarise(_rows(patterns.period(NOW, 1), [(2, k) for k in range(6)]), SGT, patterns.period(NOW, 1), 1)
    assert "RECURRING_DAY" not in {f["code"] for f in patterns.advise("INCIDENT", week, 1, "s")}


def test_a_rise_is_twice_as_many_and_nothing_before_is_not_called_a_rise():
    since = patterns.period(NOW, 4)

    def of(days: list[int], source: str = "ACCESS") -> dict:
        s = patterns.summarise(_rows(since, [(d, 1 + (d % 20)) for d in days], "d1", "Gate"), SGT, since, 4)
        return {f["code"]: f for f in patterns.advise(source, s, 4, "s") if f["code"] in ("RISING", "FIRST_RECORDED")}

    rising = of([1, 9, 15, 16, 17, 18, 22, 23, 24, 25, 26])["RISING"]
    assert rising["statement"] == "9 door events in the last 2 weeks, against 2 in the 2 weeks before."
    assert rising["rests_on"] == {"weeks": 4, "recent": 9, "before": 2, "each_weeks": 2}
    assert rising["confidence"]["level"] == "MEDIUM" and rising["confidence"]["held_in_weeks"] is None
    assert of([1, 3, 5, 15, 16, 17, 18, 19]) == {}, "five against three is not twice as many"
    assert of([1, 15, 16, 17, 18]) == {}, "four is below the floor"
    first = of([15, 16, 17, 18, 19, 20])["FIRST_RECORDED"]
    assert first["statement"] == ("6 door events in the last 2 weeks; none were recorded in the 2 weeks before. Whether "
                                  "that is a change, or only when recording began, cannot be told from the counts.")
    assert first["confidence"]["level"] == "LOW", "however many there are"
    many = [15 + k % 13 for k in range(60)]
    assert of(many)["FIRST_RECORDED"]["confidence"]["level"] == "LOW"
    # A device that keeps going down is named; a door that keeps being refused is not "a repeated device".
    downs = patterns.summarise(_rows(since, [(1, 1), (8, 1), (15, 1), (22, 1)], "CAMERA:1", "Car park"), SGT, since, 4)
    (repeated,) = [f for f in patterns.advise("DEVICE", downs, 4, "s") if f["code"] == "REPEATED_DEVICE"]
    assert repeated["statement"] == "Car park went down 4 times in the last 4 weeks."
    assert repeated["confidence"]["why"] == "Rests on 4 records over 4 weeks; it went down in 4 of those weeks."
    assert "asset register" in repeated["consider"]
    assert not [f for f in patterns.advise("ACCESS", downs, 4, "s") if f["code"] == "REPEATED_DEVICE"]
    assert patterns.REPEATED_AT == 3


def test_what_rests_on_the_most_history_comes_first_and_nothing_foretells():
    low = {"key": "b", "confidence": {"level": "LOW", "records": 900}}
    high = {"key": "c", "confidence": {"level": "HIGH", "records": 31}}
    mid_a = {"key": "a", "confidence": {"level": "MEDIUM", "records": 20}}
    mid_b = {"key": "z", "confidence": {"level": "MEDIUM", "records": 50}}
    assert [f["key"] for f in patterns.ranked([low, mid_a, high, mid_b])] == ["c", "z", "a", "b"]
    code = Path(patterns.__file__).read_text(encoding="utf-8")
    # Every sentence every rule can write, for every kind: none of them foretells.
    since = patterns.period(NOW, 4)
    busy = _rows(since, [(7 * w + 2, 10 + 0.4 * k) for w in range(4) for k in range(5)], "CAMERA:1", "Car park")
    busy += _rows(since, [(7 * w + 4, 3) for w in range(4)], "CAMERA:2", "Gate 1")
    late = _rows(since, [(15 + k % 13, 2) for k in range(12)], "d", "Door")
    grown = _rows(since, [(1, 1), (9, 1)] + [(15 + k, 1) for k in range(9)], "d", "Door")
    said = []
    for source in patterns.SOURCES:
        for rows in (busy, late, grown):
            for f in patterns.advise(source, patterns.summarise(rows, SGT, since, 4), 4, "s"):
                said.append((f["code"], f"{f['statement']} {f['consider']}".lower()))
    assert {code_ for code_, _ in said} == {"RECURRING_HOURS", "RECURRING_DAY", "RECURRING_PLACE", "RISING",
                                           "FIRST_RECORDED", "REPEATED_DEVICE"}
    for _, sentence in said:
        assert not [word for word in FORETELLING if word in sentence], sentence
    # No model, nothing learned, nothing stored: only fixed rules over counts.
    for name in ("sklearn", "numpy", "torch", "anthropic", "openai", "statsmodels"):
        assert not re.search(rf"^\s*(import|from)\s+{name}\b", code, re.M), name
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", code), "the module only reads"
    # Nobody is counted: a place is a camera, a door, a patrol or a device.
    assert "users" not in code.split('"""', 2)[2] and "full_name" not in code
    assert set(patterns.SOURCES) == set(patterns.SOURCE_LABEL) == set(patterns.NOUN) == set(patterns.COUNTED_FROM)


# ─── B. The five kinds of record, counted from the database ──────────────────

async def _camera(w: dict, name: str, site: str = "site_a") -> uuid.UUID:
    cid = uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
               {"i": cid, "t": w["tenant"], "s": w[site], "n": name})
    return cid


async def _incidents(w: dict, camera, days_ago: list[float]) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in days_ago]
    await _run([("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
                 "VALUES (:i,:t,:c,'Forced gate','high','open', now() - make_interval(secs => :s))",
                 {"i": i, "t": w["tenant"], "c": camera, "s": d * 86400}) for i, d in zip(ids, days_ago)])
    return ids


async def _seed(w: dict) -> dict:
    """One of everything that is counted, at site A, and something of each that is not."""
    t = w["tenant"]
    s: dict = {"cam": await _camera(w, "Loading bay"), "far": await _camera(w, "B gate", "site_b")}
    s["incidents"] = await _incidents(w, s["cam"], [1, 2, 3])
    await _incidents(w, s["far"], [1])
    await _incidents(w, None, [1])                           # raised by hand: no camera, so no site
    await _incidents(w, s["cam"], [40])                      # before the period
    door, card = uuid.uuid4(), uuid.uuid4()
    route, tour, sensor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Server room')", {"i": door, "t": t, "s": w["site_a"]}),
        ("INSERT INTO access_credentials (id, tenant_id, credential_ref) VALUES (:i,:t,'C-1')", {"i": card, "t": t}),
        *[("INSERT INTO access_events (tenant_id, door_id, credential_id, event_type, occurred_at) "
           "VALUES (:t,:d,:c,:k, now() - make_interval(hours => :h))", {"t": t, "d": door, "c": card, "k": kind, "h": hours})
          for kind, hours in (("denied", 5), ("forced", 30), ("tamper", 50), ("granted", 6), ("door_opened", 7), ("held_open", 8))],
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, status) "
         "VALUES (:t,:s,'VP-1','Night round', now() - interval '2 days', 'MISSED'), "
         "       (:t,:s,'VP-2','Night round', now() - interval '3 days', 'FAILED'), "
         "       (:t,:s,'VP-3','Night round', now() - interval '4 days', 'COMPLETED')", {"t": t, "s": w["site_a"]}),
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')", {"i": route, "t": t, "s": w["site_a"]}),
        ("INSERT INTO tour_schedules (id, tenant_id, route_id, name, scheduled_time) VALUES (:i,:t,:r,'Perimeter tour','22:00')",
         {"i": tour, "t": t, "r": route}),
        ("INSERT INTO tour_occurrences (tenant_id, schedule_id, scheduled_at, window_end, status) "
         "VALUES (:t,:s, now() - interval '5 days', now() - interval '5 days' + interval '1 hour', 'missed'), "
         "       (:t,:s, now() - interval '6 days', now() - interval '6 days' + interval '1 hour', 'completed')", {"t": t, "s": tour}),
        *[("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
           "VALUES (:t,:c,:k, now() - make_interval(hours => :h))", {"t": t, "c": s["cam"], "k": kind, "h": hours})
          for kind, hours in (("stream_disconnected", 3), ("stream_reconnected", 2), ("stream_disconnected", 26), ("stream_degraded", 27))],
        ("INSERT INTO iot_sensors (id, tenant_id, site_id, name, sensor_type) VALUES (:i,:t,:s,'Flood sensor','water')",
         {"i": sensor, "t": t, "s": w["site_a"]}),
        *[("INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, observed_at) "
           "VALUES (:t,:k,:d,:s,:st, now() - make_interval(hours => :h))",
           {"t": t, "k": kind, "d": device, "s": w["site_a"], "st": state, "h": hours})
          for kind, device, state, hours in (("SENSOR", sensor, "DOWN", 10), ("SENSOR", sensor, "OK", 9),
                                             # A camera's outage is its own record of disconnecting, counted once.
                                             ("CAMERA", s["cam"], "DOWN", 3))],
        *[("INSERT INTO incident_escalations (tenant_id, incident_id, site_id, kind, clock, policy_name, due_at, created_at) "
           "VALUES (:t,:i,:s,:k,:c,:p, now(), now() - make_interval(hours => :h))",
           {"t": t, "i": s["incidents"][0], "s": w["site_a"], "k": kind, "c": clock, "p": policy, "h": hours})
          for kind, clock, policy, hours in (("SLA_BREACH", "ARRIVAL", None, 4), ("SLA_BREACH", "RESOLVE", None, 5),
                                             ("POLICY_STEP", "ARRIVAL", "Tell the manager", 4))],
    ])
    return s


async def test_five_kinds_of_record_are_counted_and_what_is_not_one_is_left_out():
    w, other = await _world(), await _world()
    await _seed(w)
    async with _client() as c:
        r = await c.get(f"{BASE}/patterns", headers=w["h"][VIEWER], params={"site_id": str(w["site_a"]), "weeks": 1})
        assert r.status_code == 200, r.text
        got = r.json()
        by = {s["source"]: s for s in got["sources"]}
        assert list(by) == list(patterns.SOURCES)
        # Three incidents at the site in the week; one elsewhere, one with no site, one before it.
        # Denied, forced and tamper; not a door that opened for a card that was let in.
        # Two virtual patrols and one guard tour missed; not the ones that were done.
        # Two disconnections and a sensor read as down; not a reconnection, and not the camera a second time.
        # Two clocks missed; not a step of an escalation policy.
        assert {k: v["total"] for k, v in by.items()} == {"INCIDENT": 3, "ACCESS": 3, "PATROL": 3, "DEVICE": 3, "SLA": 2}
        assert [p["name"] for p in by["INCIDENT"]["places"]] == ["Loading bay"]
        assert {p["name"]: p["count"] for p in by["PATROL"]["places"]} == {"Night round": 2, "Perimeter tour": 1}
        assert {p["name"]: p["count"] for p in by["DEVICE"]["places"]} == {"Loading bay": 2, "Flood sensor": 1}
        assert {p["name"] for p in by["SLA"]["places"]} == {"Arrival clock", "Resolve clock"}
        for s in got["sources"]:
            assert len(s["grid"]) == 7 and sum(map(sum, s["grid"])) == s["total"] == sum(s["by_week"])
            assert s["label"] == patterns.SOURCE_LABEL[s["source"]] and s["counted_from"] and s["cut_at"] is None
            assert "_in_week" not in s
        assert got["period"]["weeks"] == 1 and got["period"]["timezone"] == SGT and got["site"]["name"] == "Factory A"
        assert got["is_forecast"] is False and got["note"] == patterns.NOTE and got["weekdays"][5] == "Saturday"

        every = (await c.get(f"{BASE}/patterns", headers=w["h"][ADMIN], params={"weeks": 1})).json()
        assert every["site"] is None
        assert {s["source"]: s["total"] for s in every["sources"]}["INCIDENT"] == 5, "with site B's, and the one at no site"
        longer = (await c.get(f"{BASE}/patterns", headers=w["h"][ADMIN], params={"site_id": str(w["site_a"]), "weeks": 8})).json()
        assert {s["source"]: s["total"] for s in longer["sources"]}["INCIDENT"] == 4 and len(longer["sources"][0]["by_week"]) == 8

        # Held to site A: every site is their site, and what has no site is not theirs.
        mine = (await c.get(f"{BASE}/patterns", headers=w["h"][SUPERVISOR], params={"weeks": 1})).json()
        assert {s["source"]: s["total"] for s in mine["sources"]}["INCIDENT"] == 3
        assert (await c.get(f"{BASE}/patterns", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        for bad in ({"weeks": 0}, {"weeks": 13}, {"site_id": "x"}):
            assert (await c.get(f"{BASE}/patterns", headers=w["h"][ADMIN], params=bad)).status_code == 422
        assert (await c.get(f"{BASE}/patterns", headers=w["h"][ADMIN], params={"site_id": str(uuid.uuid4())})).status_code == 404
        # Another organisation counts none of it.
        theirs = (await c.get(f"{BASE}/patterns", headers=other["h"][ADMIN], params={"weeks": 1})).json()
        assert sum(s["total"] for s in theirs["sources"]) == 0
        assert (await c.get(f"{BASE}/patterns", headers=other["h"][ADMIN], params={"site_id": str(w["site_a"])})).status_code == 404
        assert (await c.get(f"{BASE}/patterns", headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(f"{BASE}/patterns")).status_code == 401
    _, client_h = await _guard(w, "Building Owner", role=CLIENT)
    async with _client() as c:
        for path in ("/patterns", "/advice", "/advice/answers"):
            assert (await c.get(BASE + path, headers=client_h)).status_code == 403, path
    # Counting kept nothing.
    assert await _sql("SELECT 1 FROM risk_advice_answers WHERE tenant_id = :t", {"t": w["tenant"]}) == []


# ─── C. Advice ───────────────────────────────────────────────────────────────

async def _pattern(w: dict) -> dict:
    """Six incidents a week for four weeks at the loading bay, and one a week at the gate."""
    bay, gate = await _camera(w, "Loading bay"), await _camera(w, "Gate 1")
    await _incidents(w, bay, [7 * week + 1 + k / 50 for week in range(4) for k in range(6)])
    await _incidents(w, gate, [7 * week + 3 for week in range(4)])
    return {"bay": bay, "gate": gate}


async def test_advice_says_what_stands_out_what_it_rests_on_and_how_much_history_that_is():
    w = await _world()
    p = await _pattern(w)
    async with _client() as c:
        r = await c.get(f"{BASE}/advice", headers=w["h"][OPERATOR], params={"site_id": str(w["site_a"])})
        assert r.status_code == 200, r.text
        got = r.json()
        found = {f["code"]: f for f in got["findings"]}
        place = found["RECURRING_PLACE"]
        assert place["statement"] == "Loading bay accounts for 86% of the incidents of the last 4 weeks (24 of 28)."
        assert place["key"] == f"RECURRING_PLACE:INCIDENT:{w['site_a']}:{p['bay']}"
        assert place["rests_on"] == {"weeks": 4, "place": "Loading bay", "at_place": 24, "of": 28}
        assert place["confidence"]["why"] == "Rests on 28 records over 4 weeks; the same held within 4 of those weeks."
        assert place["answer"] is None and place["may_answer"] is False, "an operator reads advice; they do not answer it"
        assert "RECURRING_HOURS" in found and found["RECURRING_HOURS"]["rests_on"]["in_band"] >= 24
        levels = [f["confidence"]["level"] for f in got["findings"]]
        assert levels == sorted(levels, key=patterns.LEVELS.index), "what rests on the most history first"
        for f in got["findings"]:
            assert f["is_forecast"] is False and f["is_advisory"] is True
            assert not [word for word in FORETELLING if word in f["statement"].lower()], f["statement"]
        assert got["is_forecast"] is False and got["note"] == patterns.NOTE
        assert got["confidence_note"] == patterns.CONFIDENCE_NOTE and got["can_answer"] is False and got["answer_note"] is None
        assert {s["source"]: s["total"] for s in got["counted"]} == {"INCIDENT": 28, "ACCESS": 0, "PATROL": 0, "DEVICE": 0, "SLA": 0}
        assert (await c.get(f"{BASE}/advice", headers=w["h"][MANAGER], params={"site_id": str(w["site_a"])})).json()[
            "findings"][0]["may_answer"] is True

        # Every site together is read, and is not answered: whose "every site" would the answer be for?
        every = (await c.get(f"{BASE}/advice", headers=w["h"][MANAGER])).json()
        assert every["site"] is None and every["answer_note"] == api.ANSWER_ONE_SITE and every["can_answer"] is True
        assert every["findings"] and all(f["may_answer"] is False and ":ALL:" in f["key"] for f in every["findings"])
        # A week of it is too little to speak of a weekday, and the period asked for is the period counted.
        week = (await c.get(f"{BASE}/advice", headers=w["h"][VIEWER], params={"site_id": str(w["site_a"]), "weeks": 1})).json()
        assert week["period"]["weeks"] == 1 and "RECURRING_DAY" not in {f["code"] for f in week["findings"]}
        assert (await c.get(f"{BASE}/advice", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/advice", headers=w["h"][GUARD])).status_code == 403
        quiet = (await c.get(f"{BASE}/advice", headers=w["h"][ADMIN], params={"site_id": str(w["site_b"])})).json()
        assert quiet["findings"] == [] and sum(s["total"] for s in quiet["counted"]) == 0


# ─── D. A person's answer ────────────────────────────────────────────────────

async def test_advice_is_answered_by_a_person_and_the_answer_is_kept_as_the_advice_stood():
    w = await _world()
    p = await _pattern(w)
    site = str(w["site_a"])
    key = f"RECURRING_PLACE:INCIDENT:{site}:{p['bay']}"
    async with _client() as c:
        url = f"{BASE}/advice/answer"
        for body, status, words in (
            ({"site_id": site, "key": key, "answer": "NOT_ACCEPTED"}, 422, "Say why"),
            ({"site_id": site, "key": key, "answer": "NOT_ACCEPTED", "reason": "  "}, 422, "Say why"),
            ({"site_id": site, "key": key, "answer": "MAYBE"}, 422, None),
            ({"site_id": site, "key": key}, 422, None), ({"key": key, "answer": "ACCEPTED"}, 422, None),
            # The statement kept is the server's own: there is no sending one.
            ({"site_id": site, "key": key, "answer": "ACCEPTED", "statement": "Everything is fine."}, 422, None),
            ({"site_id": site, "key": "RECURRING_PLACE:INCIDENT:nowhere", "answer": "ACCEPTED"}, 409, "no longer stands"),
            # Advice that does not stand for this site and period cannot be answered: nothing rose here.
            ({"site_id": site, "key": f"RISING:INCIDENT:{site}:RECENT", "answer": "ACCEPTED"}, 409, "no longer stands"),
            ({"site_id": str(uuid.uuid4()), "key": key, "answer": "ACCEPTED"}, 404, "Site not found"),
        ):
            r = await c.post(url, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(url, headers=w["h"][role], json={"site_id": site, "key": key, "answer": "ACCEPTED"})).status_code == 403

        no = await c.post(url, headers=w["h"][SUPERVISOR], json={
            "site_id": site, "key": key, "answer": "NOT_ACCEPTED", "reason": " The bay is where every lorry is checked. "})
        assert no.status_code == 201, no.text
        said = no.json()
        assert said["statement"] == "Loading bay accounts for 86% of the incidents of the last 4 weeks (24 of 28)."
        assert said["answer"]["answer"] == "NOT_ACCEPTED" and said["answer"]["answered_by_name"] == "Role 3 User"
        assert said["answer"]["reason"] == "The bay is where every lorry is checked." and said["answer"]["said_then"] is None
        shown = {f["key"]: f for f in (await c.get(f"{BASE}/advice", headers=w["h"][VIEWER], params={"site_id": site})).json()["findings"]}
        assert shown[key]["answer"]["answer"] == "NOT_ACCEPTED", "everybody who reads the advice reads its answer"
        assert [f for f in shown.values() if f["key"] != key and f["answer"] is not None] == []

        # More happens, and the advice says something else: the answer is shown against what it was an answer to.
        await _incidents(w, p["bay"], [0.2, 0.3])
        later = {f["key"]: f for f in (await c.get(f"{BASE}/advice", headers=w["h"][VIEWER], params={"site_id": site})).json()["findings"]}
        assert later[key]["statement"] == "Loading bay accounts for 87% of the incidents of the last 4 weeks (26 of 30)."
        assert later[key]["answer"]["said_then"] == said["statement"]
        # Somebody changes their mind: a further answer, and the first one stays.
        yes = await c.post(url, headers=w["h"][MANAGER], json={"site_id": site, "key": key, "answer": "ACCEPTED"})
        assert yes.status_code == 201 and yes.json()["answer"]["answer"] == "ACCEPTED" and yes.json()["answer"]["reason"] is None
        history = (await c.get(f"{BASE}/advice/answers", headers=w["h"][VIEWER], params={"site_id": site})).json()["items"]
        assert [(a["answer"], a["answered_by_name"], a["statement"][:19]) for a in history] == [
            ("ACCEPTED", "Role 8 User", "Loading bay account"), ("NOT_ACCEPTED", "Role 3 User", "Loading bay account")]
        assert history[1]["rests_on"]["at_place"] == 24 and history[0]["rests_on"]["at_place"] == 26
        assert history[0]["source_label"] == "Incidents" and history[0]["confidence"] in patterns.LEVELS
        assert history[0]["site_name"] == "Factory A" and history[0]["period_weeks"] == 4
        assert (await c.get(f"{BASE}/advice/answers", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
    key_token = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key_token
    try:
        async with _client() as c:
            r = await c.post(f"{BASE}/advice/answer", json={"site_id": site, "key": key, "answer": "ACCEPTED"})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(f"{BASE}/advice", params={"site_id": site})).status_code == 200
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    entries = await _audit(w, "advice.answer")
    assert [(e["detail"]["answer"], e["detail"]["code"], e["user_id"]) for e in entries] == [
        ("NOT_ACCEPTED", "RECURRING_PLACE", w["users"][SUPERVISOR]), ("ACCEPTED", "RECURRING_PLACE", w["users"][MANAGER])]
    # Accepting advice did nothing else: no work, no alert, no incident beyond the ones that were there.
    (rows,) = await _sql("SELECT (SELECT count(*) FROM maintenance_work_orders WHERE tenant_id = :t) AS orders, "
                         "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
                         "(SELECT count(*) FROM incidents WHERE tenant_id = :t) AS incidents", {"t": w["tenant"]})
    assert (rows["orders"], rows["alerts"], rows["incidents"]) == (0, 0, 30)


async def test_answers_are_kept_to_the_sites_and_the_organisation_they_were_given_in():
    w, other = await _world(), await _world()
    bay = await _camera(w, "B loading bay", "site_b")
    await _incidents(w, bay, [7 * week + 1 + k / 50 for week in range(4) for k in range(6)])
    await _incidents(w, await _camera(w, "B gate", "site_b"), [7 * week + 3 for week in range(4)])
    site, key = str(w["site_b"]), f"RECURRING_PLACE:INCIDENT:{w['site_b']}:{bay}"
    async with _client() as c:
        # Held to site A: site B's advice is not theirs to answer, or its answers theirs to read.
        r = await c.post(f"{BASE}/advice/answer", headers=w["h"][SUPERVISOR], json={"site_id": site, "key": key, "answer": "ACCEPTED"})
        assert r.status_code == 404
        assert (await c.post(f"{BASE}/advice/answer", headers=w["h"][MANAGER], json={"site_id": site, "key": key, "answer": "ACCEPTED"})).status_code == 201
        assert (await c.get(f"{BASE}/advice/answers", headers=w["h"][SUPERVISOR])).json()["items"] == []
        assert len((await c.get(f"{BASE}/advice/answers", headers=w["h"][VIEWER])).json()["items"]) == 1
        assert (await c.get(f"{BASE}/advice/answers", headers=other["h"][ADMIN])).json()["items"] == []
        r = await c.post(f"{BASE}/advice/answer", headers=other["h"][ADMIN], json={"site_id": site, "key": key, "answer": "ACCEPTED"})
        assert r.status_code == 404
        assert (await c.get(f"{BASE}/advice/answers", headers=w["h"][VIEWER], params={"limit": 0})).status_code == 422


# ─── E. What the application role and the database refuse ────────────────────

async def test_an_answer_is_added_and_never_rewritten_by_the_application():
    w, other = await _world(), await _world()
    p = await _pattern(w)
    async with _client() as c:
        r = await c.post(f"{BASE}/advice/answer", headers=w["h"][MANAGER], json={
            "site_id": str(w["site_a"]), "key": f"RECURRING_PLACE:INCIDENT:{w['site_a']}:{p['bay']}", "answer": "ACCEPTED"})
        assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        scoped = "tenant_id = current_setting('app.current_tenant')::uuid"
        for statement in (f"UPDATE risk_advice_answers SET answer = 'NOT_ACCEPTED', reason = 'Rewritten' WHERE {scoped}",
                          f"UPDATE risk_advice_answers SET statement = 'Something else' WHERE {scoped}",
                          f"UPDATE risk_advice_answers SET answered_by_user_id = NULL WHERE {scoped}",
                          f"DELETE FROM risk_advice_answers WHERE {scoped}"):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement))
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        assert (await db.execute(text("SELECT count(*) FROM risk_advice_answers"))).scalar() == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text(
                "INSERT INTO risk_advice_answers (tenant_id, site_id, advice_key, code, source, statement, confidence, "
                "period_weeks, period_end, answer) VALUES (:t, :s, 'k', 'RISING', 'INCIDENT', 'Planted', 'LOW', 4, now(), 'ACCEPTED')"),
                {"t": w["tenant"], "s": w["site_a"]})
        await db.rollback()
    (table,) = await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'risk_advice_answers'")
    assert table["relrowsecurity"] and table["relforcerowsecurity"]
    (can,) = await _sql("SELECT has_table_privilege('svc_app', 'risk_advice_answers', 'DELETE') AS d, "
                        "has_table_privilege('svc_app', 'risk_advice_answers', 'UPDATE') AS u, "
                        "has_any_column_privilege('svc_app', 'risk_advice_answers', 'UPDATE') AS any_column")
    assert not can["d"] and not can["u"] and not can["any_column"], "not one column of an answer can be changed"
    assert "GRANT ALL" not in MIGRATION and "GRANT SELECT, INSERT ON risk_advice_answers TO svc_app" in MIGRATION
    # Somebody who leaves does not take their answer with them.
    await _run([("DELETE FROM audit_logs WHERE user_id = :u", {"u": w["users"][MANAGER]}),
                ("DELETE FROM users WHERE id = :u", {"u": w["users"][MANAGER]})])
    (kept,) = await _sql("SELECT answer, answered_by_user_id FROM risk_advice_answers WHERE tenant_id = :t", {"t": w["tenant"]})
    assert kept["answer"] == "ACCEPTED" and kept["answered_by_user_id"] is None


async def test_what_the_database_refuses_of_an_answer():
    w = await _world()
    make = ("INSERT INTO risk_advice_answers (tenant_id, site_id, advice_key, code, source, statement, confidence, "
            "period_weeks, period_end, answer, reason) VALUES (:t, :s, 'k', 'RISING', 'INCIDENT', :statement, :confidence, "
            ":weeks, now(), :answer, :reason)")
    base = {"t": w["tenant"], "s": w["site_a"], "statement": "Nine against two.", "confidence": "LOW", "weeks": 4,
            "answer": "ACCEPTED", "reason": None}
    for over, constraint in (({"answer": "MAYBE"}, "ck_advans_answer"), ({"confidence": "SURE"}, "ck_advans_confidence"),
                             ({"weeks": 0}, "ck_advans_weeks"), ({"weeks": 13}, "ck_advans_weeks"),
                             ({"statement": "  "}, "ck_advans_statement"),
                             ({"answer": "NOT_ACCEPTED"}, "ck_advans_reason"),
                             ({"answer": "NOT_ACCEPTED", "reason": " "}, "ck_advans_reason")):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(make, {**base, **over})
    closing = uuid.uuid4()
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i, :t, 'Closing depot')", {"i": closing, "t": w["tenant"]})
    await _sql(make, {**base, "s": closing})
    await _sql(make, {**base, "s": closing, "answer": "NOT_ACCEPTED", "reason": "Because."})
    # The site goes, and what was answered about it goes with it.
    await _sql("DELETE FROM sites WHERE id = :s", {"s": closing})
    assert await _sql("SELECT 1 FROM risk_advice_answers WHERE tenant_id = :t", {"t": w["tenant"]}) == []


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


def test_every_route_asks_for_what_it_should():
    served = {}
    for r in app.routes:
        contexts = getattr(r, "effective_route_contexts", None)
        for route in ([r] if contexts is None else (contexts() if callable(contexts) else contexts)):
            path = getattr(route, "path", "")
            if path.startswith(BASE + "/") and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.removeprefix(BASE))] = _needs(route)
    read = {"advice:read"}
    assert served == {("GET", "/patterns"): read, ("GET", "/advice"): read, ("GET", "/advice/answers"): read,
                      ("POST", "/advice/answer"): read | {"advice:answer"}}
    assert not [m for m, _ in served if m in ("DELETE", "PUT", "PATCH")], "an answer is added; nothing is changed or removed"


async def test_who_holds_the_two_permissions_and_what_this_phase_left_alone():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'advice:%' "
                      "GROUP BY p.code, p.category")
    assert {r["code"]: list(r["roles"]) for r in rows} == {"advice:read": [2, 3, 4, 6, 8], "advice:answer": [2, 3, 8]}
    assert {r["category"] for r in rows} == {"risk"}
    # The intelligence layer's own permissions and tables are as they were.
    (layer,) = await _sql("SELECT count(*) AS n FROM permissions WHERE category = 'security_intelligence'")
    assert layer["n"] == 7
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["risk_advice_answers"]
    assert not re.search(r"CREATE TABLE security_", upgrade) and "ALTER TABLE risk_advice_answers" in upgrade
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"risk_advice_answers"}, "no existing table is altered"
    assert "TRUNC" + "ATE" not in upgrade
    # The router writes its own table and the audit log, and nothing else; the insight service is not touched.
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", router)) == {"risk_advice_answers"}
    insight = (Path(patterns.__file__).parent / "intel_insight.py").read_text(encoding="utf-8")
    assert "risk_patterns" not in insight and "risk_advice" not in insight
