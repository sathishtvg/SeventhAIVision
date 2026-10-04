"""Drone patrol, phase 12: analytics.

  A — The arithmetic: the analytical score, its levels, night
  B — Recommendations: fixed rules over the aggregates, each with its evidence
  C — The overview: patrol statistics, mission success, rates, trends, and hours
      and days in the organisation's own zone
  D — The risk map: areas ranked, false positives left out, hot spots and
      repeated intrusion locations
  E — Who sees what: permission, site scoping, tenant isolation, an empty period

Events and flights are written directly, with chosen times and places, and read
back through the API as the application's database user.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.services import drone_analytics as analytics
from tests.test_drone_api import ADMIN, GUARD, SUPERVISOR, VIEWER, _client, _run, _world

SGT = ZoneInfo("Asia/Singapore")
SPOT = (1.30010, 103.80010)        # one place, inside one hot-spot cell
ELSEWHERE = (1.30200, 103.80300)


def _day(back: int):
    return datetime.now(SGT).date() - timedelta(days=back)


def _at(back: int, hour: int, minute: int = 0) -> datetime:
    """A local time `back` days ago."""
    return datetime.combine(_day(back), time(hour, minute), tzinfo=SGT)


class Fixture:
    """Collects rows and writes them in one go."""

    def __init__(self, w: dict):
        self.w, self.stmts = w, []

    def flight(self, *, status: str = "COMPLETED", site: str = "site_a", mission: str = "Night Perimeter",
               drone: str = "Hawk One", at: datetime | None = None, minutes: int = 10, reason: str | None = None,
               metres: float = 500.0) -> uuid.UUID:
        sid, at = uuid.uuid4(), at or _at(2, 22)
        flew = status in ("COMPLETED", "FAILED", "ABORTED")
        self.stmts.append((
            "INSERT INTO drone_patrol_sessions (id, tenant_id, session_number, site_id, mission_name, drone_name, "
            "    status, started_at, launched_at, ended_at, distance_m, blocked_reason, created_at) "
            "VALUES (:i,:t,:n,:s,:m,:d,:st,:a,:l,:e,:dist,:r,:a)",
            {"i": sid, "t": self.w["tenant"], "n": f"DP-{sid.hex[:10]}", "s": self.w[site], "m": mission, "d": drone,
             "st": status, "a": at, "l": at if flew else None, "e": at + timedelta(minutes=minutes),
             "dist": metres if flew else None, "r": reason}))
        return sid

    def event(self, *, at: datetime, site: str = "site_a", session: uuid.UUID | None = None,
              module: str = "intrusion", risk: str = "HIGH", status: str = "NEW", zone: str | None = "Rear Perimeter",
              where: tuple[float, float] | None = SPOT, incident: bool = False) -> uuid.UUID:
        eid = uuid.uuid4()
        inc = uuid.uuid4() if incident else None
        if inc:
            self.stmts.append(("INSERT INTO incidents (id, tenant_id, title) VALUES (:i,:t,'Drone incident')",
                               {"i": inc, "t": self.w["tenant"]}))
        self.stmts.append((
            "INSERT INTO drone_events (id, tenant_id, site_id, session_id, module_type, detected_at, risk_level, "
            "    risk_score, ai_confidence, status, zone_name, zone_type, drone_latitude, drone_longitude, "
            "    incident_id, verification_state) "
            "VALUES (:i,:t,:s,:ps,:m,:at,:r,60,0.9,:st,:z,:zt,:la,:lo,:inc,'VERIFIED')",
            {"i": eid, "t": self.w["tenant"], "s": self.w[site], "ps": session, "m": module, "at": at, "r": risk,
             "st": status, "z": zone, "zt": "RESTRICTED" if zone else None,
             "la": where[0] if where else None, "lo": where[1] if where else None, "inc": inc}))
        return eid

    async def write(self) -> None:
        await _run(self.stmts)
        self.stmts = []


def _q(back_from: int = 6, back_to: int = 0, **extra) -> dict:
    return {"from": str(_day(back_from)), "to": str(_day(back_to)), **{k: str(v) for k, v in extra.items()}}


async def _get(w: dict, path: str, role: int = ADMIN, **params):
    async with _client() as c:
        return await c.get(f"/api/v1/drone-analytics/{path}", headers=w["h"][role], params=params)


# ─── A. The arithmetic ───────────────────────────────────────────────────────

def test_the_score_is_weighted_events_per_week_capped_and_levelled():
    # One HIGH event (weight 6) in a week: 6 per week, times 5.
    assert analytics.area_score(6, 7) == 30 and analytics.area_level(30) == "MEDIUM"
    # The same event over four weeks is a quarter of the rate.
    assert analytics.area_score(6, 28) == 8 and analytics.area_level(8) == "LOW"
    assert analytics.area_score(0, 7) == 0 and analytics.area_level(0) == "NONE"
    assert analytics.area_score(12, 7) == 60 and analytics.area_level(60) == "HIGH"
    assert analytics.area_score(10_000, 7) == 100, "capped"
    # A false positive weighs nothing, and INFO weighs nothing.
    assert analytics.WEIGHT["INFO"] == 0 and "CASE WHEN e.status = 'FALSE_POSITIVE' THEN 0" in analytics._WEIGHT_SQL


def test_night_runs_from_evening_to_morning():
    assert [h for h in range(24) if analytics.is_night(h)] == [0, 1, 2, 3, 4, 5, 6, 19, 20, 21, 22, 23]


# ─── B. Recommendations ──────────────────────────────────────────────────────

def _aggregates(**over) -> tuple[dict, dict]:
    overview = {"detection_types": [], "missions": [], "events": {"unreviewed_over_a_day": 0}}
    risk = {"areas": [], "repeated_intrusion_locations": []}
    overview.update({k: v for k, v in over.items() if k in overview})
    risk.update({k: v for k, v in over.items() if k in risk})
    return overview, risk


def _area(**kw) -> dict:
    return {"area": "Rear Perimeter", "site_name": "Factory A", "night_suspicious": 0, "suspicious": 0,
            "suspicious_without_cctv": 0, "score": 40, **kw}


def test_nothing_is_recommended_below_the_thresholds():
    over, risk = _aggregates(
        areas=[_area(night_suspicious=2, suspicious_without_cctv=2)],
        repeated_intrusion_locations=[{"events": 5, "days": 1, "zone_name": "Gate", "site_name": "A", "at_night": 0,
                                       "latitude": 1.3, "longitude": 103.8}],
        detection_types=[{"name": "Licence plate", "module_type": "lpr", "events": 4, "false_positives": 4,
                          "false_positive_rate": 1.0}],
        missions=[{"name": "Round", "due": 4, "completed": 0, "success_rate": 0.0, "common_reason": None}])
    assert analytics.recommend(over, risk) == []


def test_each_recommendation_states_what_it_saw_and_is_marked_system_generated():
    over, risk = _aggregates(
        areas=[_area(night_suspicious=4, suspicious=5, suspicious_without_cctv=3)],
        repeated_intrusion_locations=[{"events": 3, "days": 2, "zone_name": "Rear Perimeter", "site_name": "Factory A",
                                       "at_night": 3, "latitude": 1.3, "longitude": 103.8}],
        detection_types=[{"name": "Licence plate", "module_type": "lpr", "events": 10, "false_positives": 6,
                          "false_positive_rate": 0.6}],
        missions=[{"name": "Night Perimeter", "due": 10, "completed": 6, "success_rate": 0.6,
                   "common_reason": "Battery too low"}],
        events={"unreviewed_over_a_day": 2})
    recs = analytics.recommend(over, risk)
    assert [r["code"] for r in recs] == [
        "NIGHT_ACTIVITY_IN_AREA", "MISSION_OFTEN_NOT_COMPLETED", "REPEATED_INTRUSION_AT_SPOT", "NO_CCTV_FOR_AREA",
        "HIGH_FALSE_POSITIVE_RATE", "EVENTS_NOT_REVIEWED"]
    assert all(r["system_generated"] is True and r["observation"] and r["suggestion"] and r["basis"] for r in recs)
    by = {r["code"]: r for r in recs}
    assert by["NIGHT_ACTIVITY_IN_AREA"]["observation"] == \
        "Rear Perimeter at Factory A generated 4 suspicious events during night patrols."
    assert "increase the approved patrol frequency" in by["NIGHT_ACTIVITY_IN_AREA"]["suggestion"]
    assert by["MISSION_OFTEN_NOT_COMPLETED"]["observation"] == \
        "Night Perimeter completed 6 of 10 flights. The most common reason: Battery too low"
    assert by["HIGH_FALSE_POSITIVE_RATE"]["observation"] == "6 of 10 licence plate events were marked false positives."
    assert by["REPEATED_INTRUSION_AT_SPOT"]["basis"]["days"] == 2
    assert by["EVENTS_NOT_REVIEWED"]["observation"].startswith("2 events have had no officer action")
    # A suggestion, never a verdict.
    assert not any(word in r["suggestion"].lower() for r in recs for word in ("must", "will happen", "guarantee"))


# ─── C. The overview ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patrol_statistics_mission_success_and_the_two_rates():
    w = await _world()
    f = Fixture(w)
    flown = [f.flight() for _ in range(6)]
    f.flight(status="BLOCKED", reason="Battery too low")
    f.flight(status="BLOCKED", reason="Battery too low")
    f.flight(status="MISSED", mission="Dawn Sweep", drone="Hawk Two")
    f.flight(status="CANCELLED")          # a person's decision: not a failure
    f.flight(status="ACTIVE")             # still flying: not yet a result
    f.event(at=_at(2, 22, 5), session=flown[0], incident=True)
    f.event(at=_at(2, 22, 6), session=flown[0], risk="MEDIUM")
    f.event(at=_at(2, 22, 7), session=flown[1], module="lpr", risk="LOW")
    f.event(at=_at(2, 22, 8), session=flown[1], module="lpr", risk="HIGH", status="FALSE_POSITIVE")
    await f.write()

    r = await _get(w, "overview", **_q())
    assert r.status_code == 200, r.text
    d = r.json()
    fl, ev = d["flights"], d["events"]
    assert d["scope"] == "all sites" and d["timezone"] == "Asia/Singapore" and d["days"] == 7
    assert fl["total"] == 11 and fl["completed"] == 6
    # 6 of the 9 that should have flown to the end; cancelled and still-flying are neither.
    assert fl["due"] == 9 and fl["success_rate"] == round(6 / 9, 4)
    assert fl["by_status"] == {"COMPLETED": 6, "BLOCKED": 2, "MISSED": 1, "CANCELLED": 1, "ACTIVE": 1}
    assert fl["did_not_complete"][0] == {"status": "BLOCKED", "reason": "Battery too low", "flights": 2}
    assert fl["flight_seconds"] == 6 * 600 and fl["distance_m"] == 3000

    assert ev["total"] == 4 and ev["by_risk"] == {"CRITICAL": 0, "HIGH": 2, "MEDIUM": 1, "LOW": 1, "INFO": 0}
    # Suspicious: MEDIUM or above, and not dismissed as a false positive.
    assert ev["suspicious"] == 2 and ev["false_positives"] == 1
    assert ev["false_positive_rate"] == 0.25 and ev["incident_conversion_rate"] == 0.25 and ev["incidents"] == 1

    types = {t["module_type"]: t for t in d["detection_types"]}
    assert types["lpr"]["name"] == "Licence plate" and types["lpr"]["false_positive_rate"] == 0.5
    assert types["intrusion"]["incident_conversion_rate"] == 0.5 and types["intrusion"]["suspicious"] == 2
    missions = {m["name"]: m for m in d["missions"]}
    assert missions["Night Perimeter"]["flights"] == 10 and missions["Night Perimeter"]["events"] == 4
    assert missions["Night Perimeter"]["success_rate"] == 0.75        # 6 of the 8 due
    assert missions["Night Perimeter"]["common_reason"] == "Battery too low"
    assert missions["Dawn Sweep"]["success_rate"] == 0.0 and missions["Dawn Sweep"]["events"] == 0
    drones = {x["name"]: x for x in d["drones"]}
    assert drones["Hawk One"]["events"] == 4 and drones["Hawk Two"]["flights"] == 1


@pytest.mark.asyncio
async def test_hours_days_and_the_trend_are_the_organisations_own():
    w = await _world()
    f = Fixture(w)
    f.flight(at=_at(3, 23, 20))
    # 23:30 local is 15:30 UTC — the afternoon, in the wrong clock.
    f.event(at=_at(3, 23, 30))
    f.event(at=_at(3, 23, 45))
    f.event(at=_at(2, 0, 30))          # after midnight: the next local day
    f.event(at=_at(2, 14, 0), risk="LOW")   # daytime, and not suspicious
    await f.write()

    d = (await _get(w, "overview", **_q())).json()
    hours = {h["hour"]: h for h in d["suspicious_by_hour"]}
    assert len(hours) == 24 and hours[23]["events"] == 2 and hours[0]["events"] == 1 and hours[15]["events"] == 0
    assert hours[23]["night"] is True and hours[14]["night"] is False and hours[14]["events"] == 0
    weekday = {x["weekday"]: x["events"] for x in d["suspicious_by_weekday"]}
    assert weekday[_day(3).isoweekday()] == 2 and weekday[_day(2).isoweekday()] == 1 and sum(weekday.values()) == 3

    daily = {x["day"]: x for x in d["daily"]}
    assert len(d["daily"]) == 7, "every day of the period, including the quiet ones"
    assert daily[str(_day(3))] == {"day": str(_day(3)), "flights": 1, "completed": 1, "events": 2, "suspicious": 2,
                                  "false_positives": 0, "incidents": 0}
    assert daily[str(_day(2))]["events"] == 2 and daily[str(_day(2))]["suspicious"] == 1
    assert daily[str(_day(5))]["events"] == 0 and daily[str(_day(5))]["flights"] == 0


# ─── D. The risk map ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_areas_are_ranked_by_an_analytical_score_that_says_what_it_is():
    w = await _world()
    f = Fixture(w)
    for back in (1, 2, 3):                                   # three nights at the same spot
        f.event(at=_at(back, 23, 10), zone="Rear Perimeter")
    f.event(at=_at(4, 22, 0), zone="Rear Perimeter", risk="CRITICAL", where=ELSEWHERE)
    f.event(at=_at(2, 11, 0), zone="Loading Bay", risk="MEDIUM", module="lpr", where=ELSEWHERE)
    f.event(at=_at(2, 11, 5), zone="Loading Bay", risk="HIGH", status="FALSE_POSITIVE", where=ELSEWHERE)
    f.event(at=_at(1, 10, 0), zone=None, risk="LOW", module="lpr", where=None)
    await f.write()

    d = (await _get(w, "risk-map", **_q())).json()
    assert "not a prediction" in d["disclaimer"] and d["method"]["weights"]["HIGH"] == 6
    assert d["method"]["levels"] == {"HIGH": 60, "MEDIUM": 25, "LOW": 1}
    areas = {a["area"]: a for a in d["areas"]}
    assert [a["area"] for a in d["areas"]] == ["Rear Perimeter", "Loading Bay", "Outside any zone"]

    rear = areas["Rear Perimeter"]
    # Three HIGH and one CRITICAL in seven days: 28 weighted, 28 per week.
    assert rear["weighted_events"] == 28 and rear["score"] == 100 and rear["level"] == "HIGH"
    assert rear["suspicious"] == 4 and rear["night_suspicious"] == 4 and rear["suspicious_without_cctv"] == 4
    assert rear["by_risk"] == {"CRITICAL": 1, "HIGH": 3, "MEDIUM": 0, "LOW": 0}
    # A place is not risky because the AI was wrong there: the false positive
    # is counted, and weighs nothing.
    bay = areas["Loading Bay"]
    assert bay["events"] == 2 and bay["false_positives"] == 1 and bay["weighted_events"] == 3
    assert bay["score"] == 15 and bay["level"] == "LOW"
    assert areas["Outside any zone"]["score"] == 5 and areas["Outside any zone"]["zone_type"] is None

    # Hot spots: where events cluster, false positives left out, unlocated ones too.
    assert sum(c["events"] for c in d["hot_spots"]) == 5
    top = d["hot_spots"][0]
    assert top["events"] == 3 and abs(top["latitude"] - SPOT[0]) < 1e-6
    [again] = d["repeated_intrusion_locations"]
    assert again["events"] == 3 and again["days"] == 3 and again["at_night"] == 3
    assert again["zone_name"] == "Rear Perimeter" and abs(again["longitude"] - SPOT[1]) < 1e-6


@pytest.mark.asyncio
async def test_recommendations_come_from_the_same_numbers_with_their_rules():
    w = await _world()
    f = Fixture(w)
    for back in (1, 2, 3):
        f.event(at=_at(back, 23, 10), zone="Rear Perimeter")
    for i in range(5):
        f.event(at=_at(2, 9, i), module="lpr", risk="LOW", zone="Loading Bay", where=ELSEWHERE,
                status="FALSE_POSITIVE" if i < 3 else "RESOLVED")
    for _ in range(3):
        f.flight()
    for _ in range(3):
        f.flight(status="BLOCKED", reason="Battery too low")
    await f.write()

    r = await _get(w, "recommendations", **_q())
    assert r.status_code == 200, r.text
    d = r.json()
    assert "not a conclusion" in d["disclaimer"] and d["rules"]["false_positive_rate"] == 0.4
    codes = [x["code"] for x in d["recommendations"]]
    assert codes == ["NIGHT_ACTIVITY_IN_AREA", "MISSION_OFTEN_NOT_COMPLETED", "REPEATED_INTRUSION_AT_SPOT",
                     "NO_CCTV_FOR_AREA", "HIGH_FALSE_POSITIVE_RATE", "EVENTS_NOT_REVIEWED"], codes
    by = {x["code"]: x for x in d["recommendations"]}
    assert by["MISSION_OFTEN_NOT_COMPLETED"]["basis"] == {
        "mission": "Night Perimeter", "completed": 3, "flights": 6, "success_rate": 0.5,
        "common_reason": "Battery too low", "threshold": 0.8}
    assert by["HIGH_FALSE_POSITIVE_RATE"]["basis"]["false_positive_rate"] == 0.6
    assert all(x["system_generated"] is True for x in d["recommendations"])


# ─── E. Who sees what ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_respect_permission_site_scope_and_the_tenant():
    w, stranger = await _world(), await _world()
    f = Fixture(w)
    f.flight()
    f.flight(site="site_b")
    f.event(at=_at(1, 22, 0))
    f.event(at=_at(1, 22, 5), site="site_b", zone="B Yard")
    f.event(at=_at(1, 22, 6), site="site_b", zone="B Yard")
    await f.write()
    g = Fixture(stranger)
    for _ in range(4):
        g.event(at=_at(1, 22, 0), zone="Somebody Else's Fence")
    await g.write()

    admin = (await _get(w, "overview", **_q())).json()
    assert admin["events"]["total"] == 3 and admin["flights"]["total"] == 2, "another tenant's rows were counted"
    # The supervisor is restricted to site A.
    sup = await _get(w, "overview", SUPERVISOR, **_q())
    assert sup.json()["scope"] == "your sites" and sup.json()["events"]["total"] == 1
    assert sup.json()["flights"]["total"] == 1
    sup_map = (await _get(w, "risk-map", SUPERVISOR, **_q())).json()
    assert [a["area"] for a in sup_map["areas"]] == ["Rear Perimeter"]
    assert (await _get(w, "risk-map", SUPERVISOR, **_q(site_id=w["site_b"]))).status_code == 404
    one_site = (await _get(w, "risk-map", **_q(site_id=w["site_b"]))).json()
    assert one_site["scope"] == "Factory B" and [a["area"] for a in one_site["areas"]] == ["B Yard"]
    assert "Somebody Else's Fence" not in str((await _get(w, "risk-map", **_q())).json())

    assert (await _get(w, "overview", VIEWER, **_q())).status_code == 200
    for path in ("overview", "risk-map", "recommendations"):
        assert (await _get(w, path, GUARD, **_q())).status_code == 403, path
    assert (await _get(w, "overview", **_q(back_from=400))).status_code == 422
    assert (await _get(w, "overview", **{"from": str(_day(0)), "to": str(_day(3))})).status_code == 422
    assert (await _get(w, "overview", **_q(mission_id=uuid.uuid4()))).status_code == 404


@pytest.mark.asyncio
async def test_a_period_with_nothing_in_it_is_zeros_and_no_rate_not_an_error():
    w = await _world()
    over = (await _get(w, "overview", **_q())).json()
    assert over["flights"]["total"] == 0 and over["events"]["total"] == 0
    # No flights is not a 0% success rate; no events is not a 0% false-positive rate.
    assert over["flights"]["success_rate"] is None and over["events"]["false_positive_rate"] is None
    assert over["events"]["incident_conversion_rate"] is None and over["events"]["per_flight"] is None
    assert over["detection_types"] == [] and over["missions"] == [] and len(over["daily"]) == 7
    risk = (await _get(w, "risk-map", **_q())).json()
    assert risk["areas"] == [] and risk["hot_spots"] == [] and risk["repeated_intrusion_locations"] == []
    assert (await _get(w, "recommendations", **_q())).json()["recommendations"] == []
