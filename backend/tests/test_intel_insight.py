"""AI security intelligence, phase 13: a site's security score, and what a period looked like.

  A — The rules, with nothing running: the score and every point of it, the
      findings and what each rests on
  B — Counted from real records, through the API: a site's counts, its score,
      the sites side by side
  C — Who sees which sites, the weights, and that it only reads

The claims this phase makes, each with tests: the score is arithmetic whose
lines add up; nothing recorded is not reported as nothing wrong; every finding
says what it rests on and is advisory; nobody is named; and a person sees the
scores of their own sites only.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import intel_insight as insight
from tests.test_drone_api import ADMIN, GUARD, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql
from tests.test_intel_decisions import BASE, _ago, _decide, _pass
from tests.test_intel_events import _alert, _camera, _world
from tests.test_intel_timeline import without_docstrings

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_the_score_starts_at_a_hundred_and_every_point_taken_off_is_a_stated_line():
    s = insight.score({"cameras": 12, "cameras_offline": 2, "incidents": 4, "incidents_open": 3, "situations": 9,
                       "high_risk_open": 1, "unattended": 0, "sla_breached": 1, "repeated_locations": 1,
                       "patrols_missed": 2, "drone_patrols": 5, "drone_patrols_completed": 5})
    assert [(d["factor"], d["count"], d["points"]) for d in s["deductions"]] == [
        ("OPEN_INCIDENTS", 3, -15), ("HIGH_RISK_OPEN", 1, -5), ("CAMERAS_OFFLINE", 2, -10), ("SLA_BREACHED", 1, -5),
        ("REPEATED_LOCATION", 1, -5), ("PATROLS_MISSED", 2, -6)]
    assert s["score"] == 100 + sum(d["points"] for d in s["deductions"]) == 54, "the lines add up to the score"
    assert (s["out_of"], s["band"]) == (100, "NEEDS_ATTENTION")
    by = {d["factor"]: d["detail"] for d in s["deductions"]}
    assert by["OPEN_INCIDENTS"] == "3 unresolved incident(s)." and by["CAMERAS_OFFLINE"] == "2 camera(s) not sending."
    assert by["PATROLS_MISSED"] == "2 patrol(s) missed or failed in the period."
    assert "5 drone patrol(s) completed." in s["went_well"], "what went right is said, and moves no points"
    assert s["note"] is None and s["basis"] == {"cameras": 12, "situations": 9, "incidents": 4, "virtual_patrols": 0,
                                                "drone_patrols": 5}


def test_a_factor_takes_off_no_more_than_its_cap_and_the_score_never_goes_below_nothing():
    many = insight.score({"cameras": 30, "cameras_offline": 30, "incidents_open": 30, "high_risk_open": 30,
                          "unattended": 30, "sla_breached": 30, "repeated_locations": 30, "patrols_missed": 30,
                          "situations": 30})
    caps = {factor: cap for factor, _, cap in insight.SCORE_RULES}
    assert {d["factor"]: -d["points"] for d in many["deductions"]} == caps
    assert all(d["capped"] for d in many["deductions"])
    assert sum(caps.values()) == 109 and many["score"] == 0 and many["band"] == "POOR"
    one = insight.score({"cameras": 4, "cameras_offline": 1})
    assert one["deductions"][0]["capped"] is False and one["score"] == 95 and one["band"] == "GOOD"
    assert [insight.band_of(v) for v in (100, 85, 84, 65, 64, 40, 39, 0)] == [
        "GOOD", "GOOD", "FAIR", "FAIR", "NEEDS_ATTENTION", "NEEDS_ATTENTION", "POOR", "POOR"]
    assert set(insight.SENTENCES) == set(insight.FACTORS) == set(caps)


def test_nothing_recorded_is_not_reported_as_nothing_wrong():
    empty = insight.score({})
    assert empty["score"] == 100 and empty["deductions"] == [] and empty["went_well"] == []
    assert empty["band"] == insight.NOTHING_RECORDED == "NOTHING_RECORDED", "a hundred from no records is not 'good'"
    assert empty["note"].startswith("Nothing was recorded for this site in the period")
    assert "not that nothing is" in empty["note"]
    quiet = insight.score({"cameras": 6})
    assert quiet["score"] == 100 and quiet["note"].startswith("No situation was recorded in the period.")
    assert quiet["band"] == "GOOD", "six cameras all sending is something recorded"
    assert quiet["went_well"] == ["All 6 camera(s) are sending."]
    busy = insight.score({"cameras": 6, "situations": 4})
    assert busy["note"] is None
    assert "No situation is waiting for a decision or open at high risk." in busy["went_well"]


def test_a_tenant_weighs_each_factor_and_nonsense_is_refused():
    counts = {"cameras": 10, "cameras_offline": 2, "incidents_open": 2}
    shipped = insight.score(counts)
    assert {d["factor"]: d["points"] for d in shipped["deductions"]} == {"OPEN_INCIDENTS": -10, "CAMERAS_OFFLINE": -10}
    weighed = insight.score(counts, {"CAMERAS_OFFLINE": 2, "OPEN_INCIDENTS": 0})
    assert {d["factor"]: d["points"] for d in weighed["deductions"]} == {"CAMERAS_OFFLINE": -20}
    assert weighed["score"] == 80
    # Weighted, the cap is weighted too: the proportion a factor can take stays what the tenant said.
    heavy = insight.score({"cameras_offline": 9}, {"CAMERAS_OFFLINE": 2})
    assert heavy["deductions"][0]["points"] == -40
    for good in ({}, {"CAMERAS_OFFLINE": 0}, {"UNATTENDED": 3, "PATROLS_MISSED": 0.5}):
        insight.validate_weights(good)
    for bad in ("heavy", ["CAMERAS_OFFLINE"], {"CAMERAS": 1}, {"UNATTENDED": -1}, {"UNATTENDED": 3.1},
                {"UNATTENDED": "2"}, {"UNATTENDED": True}):
        with pytest.raises(ValueError):
            insight.validate_weights(bad)


def test_the_busiest_hours_are_found_across_midnight():
    assert insight.busiest_hours({}) is None
    # Between bands that hold the same number, the earliest is given, so the answer does not wander.
    assert insight.busiest_hours({1: 4, 2: 3, 3: 2, 14: 1}) == (0, 9)
    assert insight.busiest_hours({23: 5, 0: 4, 1: 3, 12: 2}) == (22, 12), "a band that runs past midnight is one band"
    assert insight.busiest_hours({9: 1}) == (6, 1)


def _period(**over) -> dict:
    base = {"situations": 10, "closed": 0, "false_positive": 0, "decisions": 0, "decided": 0, "overrides": 0,
            "median_seconds_to_decide": None, "cameras": 8, "cameras_offline": 0, "patrols_missed": 0,
            "locations": [], "by_hour": {}, "vehicles": [], "persons": [], "timezone": "Asia/Singapore"}
    return {**base, **over}


def test_each_finding_says_what_it_rests_on_and_what_a_person_might_consider():
    found = insight.findings(_period(
        locations=[{"name": "Rear perimeter", "camera_id": uuid.uuid4(), "situations": 7},
                   {"name": "Gate 1", "camera_id": uuid.uuid4(), "situations": 3}],
        by_hour={1: 3, 2: 3, 3: 2, 15: 2}, closed=8, false_positive=5, decisions=10, decided=8, overrides=5,
        median_seconds_to_decide=1500, cameras_offline=2, offline_names=["Dock 4", "Gate 2"], patrols_missed=3,
        vehicles=[{"plate": "SGX1234A", "situations": 4}, {"plate": "SBA1B", "situations": 2}]), 7)
    by = {f["code"]: f for f in found}
    assert list(by) == ["CONCENTRATED_PLACE", "CONCENTRATED_HOURS", "MOSTLY_FALSE", "SLOW_DECISIONS",
                        "CAMERAS_OFFLINE", "PATROLS_MISSED", "REPEATED_VEHICLE", "OFTEN_OVERRIDDEN"]
    assert by["CONCENTRATED_PLACE"]["finding"] == (
        "Rear perimeter is where 70% of the situations of the last 7 day(s) began (7 of 10).")
    assert by["CONCENTRATED_PLACE"]["rests_on"] == {"place": "Rear perimeter", "situations": 7, "of": 10}
    assert by["CONCENTRATED_HOURS"]["finding"] == "80% of the situations began between 00:00 and 04:00 (8 of 10)."
    assert by["CONCENTRATED_HOURS"]["rests_on"]["timezone"] == "Asia/Singapore"
    assert by["MOSTLY_FALSE"]["finding"] == (
        "5 of the 8 situations closed in the period were closed as false positives (62%).")
    assert by["SLOW_DECISIONS"]["finding"] == (
        "Half of the situations waited more than 25 minutes for their first decision.")
    assert by["CAMERAS_OFFLINE"]["finding"] == "2 of 8 camera(s) are not sending."
    assert by["CAMERAS_OFFLINE"]["rests_on"]["names"] == ["Dock 4", "Gate 2"]
    assert by["REPEATED_VEHICLE"]["finding"] == "Number plate SGX1234A was part of 4 situations."
    assert by["OFTEN_OVERRIDDEN"]["finding"] == "5 of 10 decisions went against what was suggested (50%)."
    for f in found:
        assert f["is_advisory"] is True and f["is_decision"] is False and f["consider"] and f["rests_on"]
        assert not re.search(r"\b(must|shall|will be)\b", f["consider"]), "a finding suggests; it orders nothing"
        assert not f["consider"].startswith("Consider"), "the screen already says 'To consider'"


def test_a_rule_about_a_share_does_not_speak_from_too_few():
    assert insight.findings(_period(situations=0), 7) == [], "an empty period finds nothing"
    few = insight.findings(_period(
        situations=4, locations=[{"name": "Gate 1", "camera_id": None, "situations": 4}], by_hour={2: 4},
        closed=4, false_positive=4, decisions=4, decided=4, overrides=4, median_seconds_to_decide=5000,
        vehicles=[{"plate": "SBA1B", "situations": 2}]), 7)
    assert few == [], "four of four is not a pattern, and a plate seen twice is not yet a repeat"
    spread = insight.findings(_period(
        locations=[{"name": "Gate 1", "camera_id": None, "situations": 3}], by_hour={h: 1 for h in range(10)},
        closed=10, false_positive=4, decisions=10, decided=10, overrides=3, median_seconds_to_decide=600), 7)
    assert spread == [], "thirty per cent at one place, spread over the day, decided in ten minutes: nothing stands out"


def test_nobody_is_named_and_the_module_only_reads():
    code = without_docstrings(SERVICES / "intel_insight.py")
    assert "FROM security_situations s" in code, "the statements are what is being looked at"
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code) and ".commit(" not in code
    assert "full_name" not in code and "users" not in re.findall(r"FROM (\w+)", code), "no person is read at all"
    assert "intel_actions" not in code
    for name in ("intel_runner", "intel_events", "intel_correlation", "intel_risk", "intel_recommend"):
        assert "intel_insight" not in (SERVICES / f"{name}.py").read_text(encoding="utf-8"), name
    for word in ("intruder", "unauthorised", "criminal", "suspect"):
        assert word not in code.lower()


def test_the_table_that_says_how_the_score_is_made_has_a_line_for_every_factor():
    assert set(insight.EACH) == set(insight.FACTORS)
    assert [name for name, _ in insight.BANDS] == ["GOOD", "FAIR", "NEEDS_ATTENTION", "POOR"]
    assert [floor for _, floor in insight.BANDS] == sorted((floor for _, floor in insight.BANDS), reverse=True)
    assert insight.BANDS[-1][1] == 0, "every score is in a band"


# ─── B. Counted from real records ────────────────────────────────────────────

async def _week(w: dict) -> dict:
    """Site A, in the last few days: six situations at Gate 1 and one at another
    camera, one incident still open and past its response time, a camera that
    stopped sending, a patrol missed and a patrol kept. Site B: nothing."""
    w["cam_c"] = await _camera(w, "site_a", "Dock 7", 1.3003, 103.8003)
    w["cam_d"] = await _camera(w, "site_a", "Fence 2", 1.3009, 103.8009)
    alerts = []
    for n in range(6):
        # Far enough apart that each is a matter of its own, and all within the hour the layer reads back.
        alerts.append(await _alert(w, "intrusion", camera="cam_a", site="site_a", code="intrusion.zone_breach",
                                   severity="critical" if n < 2 else "medium", title="Person at Gate 1",
                                   at=_ago(minutes=8 * (n + 1))))
    alerts.append(await _alert(w, "lpr", camera="cam_c", site="site_a", code="lpr.watchlist", severity="high",
                               title="Vehicle at Dock 7", at=_ago(minutes=4)))
    w["alerts"] = alerts
    incident = uuid.uuid4()
    await _run([
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, sla_breached, created_at) "
         "VALUES (:i,:t,:c,'Left open','high','open',TRUE,:at)",
         {"i": incident, "t": w["tenant"], "c": w["cam_a"], "at": _ago(hours=5)}),
        ("INSERT INTO incidents (tenant_id, camera_id, title, severity, status, resolved_at, created_at) "
         "VALUES (:t,:c,'Dealt with','low','resolved',:at,:at)", {"t": w["tenant"], "c": w["cam_a"], "at": _ago(hours=9)}),
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_disconnected',:at)", {"t": w["tenant"], "c": w["cam_d"], "at": _ago(hours=2)}),
        # Dock 7 went and came back: it is sending.
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_disconnected',:at)", {"t": w["tenant"], "c": w["cam_c"], "at": _ago(hours=3)}),
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_reconnected',:at)", {"t": w["tenant"], "c": w["cam_c"], "at": _ago(hours=2)}),
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, status) "
         "VALUES (:t,:s,'VP-0001','Night round',:at,'MISSED')", {"t": w["tenant"], "s": w["site_a"], "at": _ago(hours=20)}),
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, status) "
         "VALUES (:t,:s,'VP-0002','Night round',:at,'COMPLETED')",
         {"t": w["tenant"], "s": w["site_a"], "at": _ago(hours=8)}),
        # Older than the period, and so not counted in it.
        ("INSERT INTO virtual_patrol_sessions (tenant_id, site_id, patrol_number, schedule_name, scheduled_for, status) "
         "VALUES (:t,:s,'VP-0000','Night round',:at,'MISSED')", {"t": w["tenant"], "s": w["site_a"], "at": _ago(days=20)}),
    ])
    w["incident"] = incident
    await _pass(w)
    return w


async def _get(c, w: dict, role: int, path: str, **params):
    return await c.get(f"{BASE}/{path}", headers=w["h"][role], params=params)


@pytest.mark.asyncio
async def test_a_sites_score_is_counted_from_its_records_and_its_lines_add_up():
    w = await _week(await _world())
    async with _client() as c:
        r = await _get(c, w, ADMIN, "site-scores")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["sites"]) == 2
    a = next(s for s in body["sites"] if s["site_id"] == str(w["site_a"]))
    b = next(s for s in body["sites"] if s["site_id"] == str(w["site_b"]))
    assert body["sites"][0]["site_id"] == a["site_id"], "the lowest score is first: where to look"
    lines = {d["factor"]: (d["count"], d["points"]) for d in a["deductions"]}
    assert lines["OPEN_INCIDENTS"] == (1, -5) and lines["SLA_BREACHED"] == (1, -5)
    assert lines["CAMERAS_OFFLINE"] == (1, -5), "Fence 2 is not sending; Dock 7 came back and is not counted"
    assert lines["PATROLS_MISSED"] == (1, -3), "the one missed in the period, not the one from three weeks ago"
    assert lines["REPEATED_LOCATION"] == (1, -5), "Gate 1, where six of the seven began"
    assert lines["HIGH_RISK_OPEN"][0] >= 1 and lines["UNATTENDED"][0] >= 1
    assert a["score"] == 100 + sum(d["points"] for d in a["deductions"])
    assert a["basis"]["situations"] == 7 and a["basis"]["cameras"] == 3 and a["basis"]["virtual_patrols"] == 2
    assert "1 virtual patrol(s) completed." in a["went_well"]
    # Site B has a camera and nothing else: nothing found is said as that.
    assert b["score"] == 100 and b["deductions"] == [] and b["note"].startswith("No situation was recorded")
    assert [x["factor"] for x in body["rules"]] == list(insight.FACTORS)
    assert all(x["weight"] == 1.0 and x["counts"].startswith("each ") for x in body["rules"])
    assert body["days"] == 7 and [x["band"] for x in body["bands"]] == ["GOOD", "FAIR", "NEEDS_ATTENTION", "POOR"]


@pytest.mark.asyncio
async def test_what_the_period_looked_like_is_counted_for_one_site_and_for_all():
    w = await _week(await _world())
    async with _client() as c:
        # Decide one, close one as false, so that how people answered has something to count.
        rows = await _sql("SELECT s.id FROM security_situations s WHERE s.tenant_id = :t ORDER BY s.started_at",
                          {"t": w["tenant"]})
        first, second = {"id": rows[0]["id"]}, {"id": rows[1]["id"]}
        a1 = await _decide(c, w, first, OPERATOR, "FALSE_POSITIVE", reason_code="FALSE_DETECTION")
        a2 = await _decide(c, w, second, OPERATOR, "ACKNOWLEDGE")
        assert a1.status_code == 201 and a2.status_code == 201, (a1.text, a2.text)
        one = await _get(c, w, ADMIN, "insight", site_id=str(w["site_a"]))
        every = await _get(c, w, ADMIN, "insight")
        other = await _get(c, w, ADMIN, "insight", site_id=str(w["site_b"]))
    assert one.status_code == 200 and every.status_code == 200, (one.text, every.text)
    body = one.json()
    c_ = body["counts"]
    assert body["site"] == {"id": str(w["site_a"]), "name": body["site"]["name"]} and body["is_advisory"] is True
    assert body["period"]["days"] == 7 and body["period"]["timezone"] == "Asia/Singapore"
    assert c_["situations"] == 7 and sum(c_["by_risk"].values()) == 7
    assert c_["by_source"] == {"CCTV_AI": 6, "LPR": 1}
    assert (c_["closed"], c_["false_positive"], c_["still_open"]) == (1, 1, 6)
    assert (c_["decisions"], c_["decided"]) == (2, 2) and c_["median_seconds_to_decide"] > 0
    assert c_["locations"][0]["name"] == "Gate 1" and c_["locations"][0]["situations"] == 6
    assert sum(c_["by_hour"].values()) == 7 and all(0 <= int(h) <= 23 for h in c_["by_hour"])
    assert (c_["cameras"], c_["cameras_offline"], c_["offline_names"]) == (3, 1, ["Fence 2"])
    assert (c_["incidents"], c_["incidents_open"], c_["sla_breached"]) == (2, 1, 1)
    assert body["score"]["score"] == 100 + sum(d["points"] for d in body["score"]["deductions"])
    codes = [f["code"] for f in body["findings"]]
    assert "CONCENTRATED_PLACE" in codes and "CAMERAS_OFFLINE" in codes and "PATROLS_MISSED" in codes
    place = next(f for f in body["findings"] if f["code"] == "CONCENTRATED_PLACE")
    assert place["finding"] == "Gate 1 is where 86% of the situations of the last 7 day(s) began (6 of 7)."
    assert all(f["is_advisory"] and f["is_decision"] is False for f in body["findings"])
    # Every site together has no single score, and counts the same seven.
    assert every.json()["site"] is None and every.json()["score"] is None
    assert every.json()["counts"]["situations"] == 7
    assert other.json()["counts"]["situations"] == 0 and other.json()["findings"] == []
    assert other.json()["score"]["score"] == 100
    # Nobody is named anywhere in it.
    assert "User" not in one.text and "full_name" not in one.text


# ─── C. Who sees which sites, the weights, and that it only reads ────────────

@pytest.mark.asyncio
async def test_a_person_sees_the_scores_of_their_own_sites_and_nobody_elses_organisation():
    w = await _week(await _world())
    other = await _world()
    async with _client() as c:
        admin = await _get(c, w, ADMIN, "site-scores")
        supervisor = await _get(c, w, SUPERVISOR, "site-scores")          # kept to site A in this world
        theirs = await _get(c, w, SUPERVISOR, "insight", site_id=str(w["site_b"]))
        mine = await _get(c, w, SUPERVISOR, "insight")
        viewer = await _get(c, w, VIEWER, "site-scores")
        outsider = await _get(c, other, ADMIN, "site-scores")
        outsider_site = await _get(c, other, ADMIN, "insight", site_id=str(w["site_a"]))
        nobody = await c.get(f"{BASE}/site-scores")
        too_long = await _get(c, w, ADMIN, "insight", days=400)
    assert len(admin.json()["sites"]) == 2
    assert [s["site_id"] for s in supervisor.json()["sites"]] == [str(w["site_a"])]
    assert theirs.status_code == 404, "another site's picture is not theirs to ask for"
    assert mine.json()["counts"]["situations"] == 7 and mine.json()["site"] is None
    assert viewer.status_code == 200, "reading the score needs no more than reading the situations"
    assert {s["site_id"] for s in outsider.json()["sites"]}.isdisjoint({str(w["site_a"]), str(w["site_b"])})
    assert all(s["score"] == 100 for s in outsider.json()["sites"])
    assert outsider_site.status_code == 404 and nobody.status_code in (401, 403) and too_long.status_code == 422


@pytest.mark.asyncio
async def test_an_organisation_weighs_the_score_through_its_settings_and_nonsense_is_refused():
    w = await _week(await _world())
    url = "/api/v1/settings/intel.score_weights"
    async with _client() as c:
        before = next(s for s in (await _get(c, w, ADMIN, "site-scores")).json()["sites"]
                      if s["site_id"] == str(w["site_a"]))
        bad = await c.put(url, headers=w["h"][ADMIN], json={"setting_value": {"CAMERAS": 2}})
        worse = await c.put(url, headers=w["h"][ADMIN], json={"setting_value": {"CAMERAS_OFFLINE": 9}})
        good = await c.put(url, headers=w["h"][ADMIN],
                           json={"setting_value": {"CAMERAS_OFFLINE": 3, "PATROLS_MISSED": 0}})
        operator = await c.put(url, headers=w["h"][OPERATOR], json={"setting_value": {"CAMERAS_OFFLINE": 1}})
        body = (await _get(c, w, ADMIN, "site-scores")).json()
    assert bad.status_code == 422 and worse.status_code == 422 and good.status_code in (200, 201), good.text
    assert operator.status_code == 403
    after = next(s for s in body["sites"] if s["site_id"] == str(w["site_a"]))
    lines = {d["factor"]: d["points"] for d in after["deductions"]}
    assert lines["CAMERAS_OFFLINE"] == -15 and "PATROLS_MISSED" not in lines
    assert after["score"] == before["score"] - 10 + 3
    weights = {x["factor"]: x["weight"] for x in body["rules"]}
    assert weights["CAMERAS_OFFLINE"] == 3.0 and weights["PATROLS_MISSED"] == 0.0 and weights["UNATTENDED"] == 1.0


@pytest.mark.asyncio
async def test_asking_for_the_score_or_the_period_writes_nothing():
    w = await _week(await _world())

    async def marks() -> list:
        return [(await _sql(f"SELECT count(*) AS n, max(xmin::text::bigint) AS x FROM {name} WHERE tenant_id = :t",
                            {"t": w["tenant"]}))[0] for name in (
            "security_situations", "security_events", "security_assessments", "security_decisions", "incidents",
            "alerts", "cameras", "camera_health_events", "virtual_patrol_sessions", "audit_logs", "tenant_settings")]

    before = await marks()
    async with _client() as c:
        for _ in range(2):
            assert (await _get(c, w, ADMIN, "site-scores")).status_code == 200
            assert (await _get(c, w, ADMIN, "insight", site_id=str(w["site_a"]))).status_code == 200
            assert (await _get(c, w, ADMIN, "insight", days=30)).status_code == 200
    assert await marks() == before
