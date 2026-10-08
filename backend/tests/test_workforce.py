"""Workforce readings and recommendations: what is recorded of each guard's work, and what a manager might consider.

  A — The rules, with nothing running
  B — A reading, counted from the database
  C — Whose reading somebody may read
  D — Recommendations and a manager's answer
  E — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a reading is counts beside how much there was to
do, with nothing scored and nobody ranked; a waived violation is not counted
against anybody; each section is read under its own permission and a person's
own reading is theirs; a recommendation is never an employment decision or a
change to a roster, and an answer to one changes nothing else.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import workforce as api
from app.services import workforce_advice as advice
from app.services import workforce_readings as readings
from tests.test_daily_briefings import _zone
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/workforce"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0154_workforce_advice_answers.py").read_text(encoding="utf-8")
EVERYTHING = frozenset(readings.NEEDS.values())
#: Words that would turn a count into a judgement of a person.
JUDGING = ("poor", "bad", "worst", "best", "underperform", "disciplin", "warning", "dismiss", "terminat", "score",
           "rank", "rating", "unreliable", "lazy")
MEI = {"id": uuid.UUID(int=1), "full_name": "Mei Lin"}
SITE = {"id": uuid.UUID(int=2), "name": "Factory A"}


# ─── A. The rules ────────────────────────────────────────────────────────────

def test_training_is_recommended_from_what_a_guard_was_not_given():
    today = date(2026, 10, 8)
    c = advice.certification(MEI, "PLRD licence", "MISSING", 2, date(2026, 10, 10))
    assert c["statement"] == "Mei Lin is rostered for 2 shifts from 10 Oct 2026 that need PLRD licence, and holds none on file."
    assert c["consider"] == ("Arranging the certificate before then, or rostering somebody who holds it. "
                             "The roster is changed in the roster, by a person.")
    assert (c["kind"], c["code"], c["key"]) == ("TRAINING", "CERTIFICATION", f"CERTIFICATION:{MEI['id']}:PLRD licence:MISSING")
    assert c["rests_on"] == {"certification": "PLRD licence", "status": "MISSING", "shifts": 2, "first_shift": "2026-10-10"}
    one = advice.certification(MEI, "First aid", "EXPIRED", 1, date(2026, 10, 9))["statement"]
    assert one == "Mei Lin is rostered for 1 shift from 9 Oct 2026 that needs First aid, and the one on file will have lapsed by then."
    assert set(advice.STATUS_WORDS) == {"MISSING", "EXPIRED", "REVOKED", "EXPIRING"}

    lapsed = advice.course(MEI, "c1", "First aid", date(2026, 9, 28), today)
    assert (lapsed["code"], lapsed["statement"]) == ("COURSE_LAPSED", "Mei Lin's pass in First aid lapsed on 28 Sep 2026.")
    assert lapsed["consider"] == "Assigning the course again. It is assigned in Training."
    lapsing = advice.course(MEI, "c1", "First aid", date(2026, 10, 20), today)
    assert (lapsing["code"], lapsing["statement"]) == ("COURSE_LAPSING", "Mei Lin's pass in First aid lapses on 20 Oct 2026.")
    # The same course is one recommendation whichever it is, so that its answer follows it.
    assert lapsed["key"] == lapsing["key"] == f"COURSE:{MEI['id']}:c1"
    assert advice.course(MEI, "c1", "First aid", today + timedelta(days=30), today) is None
    assert advice.course(MEI, "c1", "First aid", today + timedelta(days=29), today)["code"] == "COURSE_LAPSING"

    assert advice.missed_tours(MEI, 2, 40, ["Patrol basics"]) is None, "two missed is not yet worth a look"
    tours = advice.missed_tours(MEI, 3, 2, ["Patrol basics", "Site security"])
    assert tours["statement"] == "Mei Lin missed 3 of the 5 tours assigned to them that fell due in the last 4 weeks."
    assert tours["consider"] == ("Whether the route can be walked in the time given, and whether they have been shown it. "
                                 "Courses filed under security: Patrol basics, Site security.")
    # No course is invented: an empty library is said to be empty.
    assert advice.missed_tours(MEI, 3, 2, [])["consider"].endswith("No course is filed under security in the library.")
    assert (advice.MISSED_AT, advice.UNSTARTED_AT, advice.WEEKS) == (3, 3, 4)


def test_coverage_compares_a_site_with_itself():
    assert advice.unstarted(SITE, 2, 40) is None
    u = advice.unstarted(SITE, 3, 6)
    assert u["statement"] == "3 of the 6 shifts due at Factory A in the last 4 weeks were not started."
    assert (u["kind"], u["code"], u["subject"], u["site"]) == ("COVERAGE", "UNSTARTED_SHIFTS", None, SITE)

    # Guard-hours in each hour of the day, where the site is: a shift cut to the period and shared over its hours.
    since, now = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc), datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
    shift = lambda day, start, hours: {"scheduled_start": datetime(2026, 10, day, start, 30, tzinfo=timezone.utc),   # noqa: E731
                                       "scheduled_end": datetime(2026, 10, day, start, 30, tzinfo=timezone.utc) + timedelta(hours=hours)}
    cover = advice.cover_by_hour([shift(2, 0, 8), shift(3, 0, 8)], "Asia/Singapore", since, now)
    assert len(cover) == 24 and sum(cover) == pytest.approx(16)
    assert cover[8] == pytest.approx(1.0) and cover[9] == pytest.approx(2.0) and cover[16] == pytest.approx(1.0) and cover[17] == 0
    # What falls outside the period is not counted.
    assert sum(advice.cover_by_hour([shift(7, 20, 8)], "UTC", since, now)) == pytest.approx(3.5)

    day = [0.0] * 8 + [6.0] * 8 + [0.0] * 8                     # forty-eight guard-hours, all between 08:00 and 16:00
    night = [0, 0, 6, 6] + [0] * 10 + [3] + [0] * 9             # twelve of fifteen incidents between 02:00 and 04:00
    h = advice.hours_cover(SITE, night, day)
    assert h["statement"] == ("80% of the incidents at Factory A in the last 4 weeks fell between 00:00 and 04:00 (12 of 15). "
                              "0 guard-hours were rostered in those hours, against 8 for an average four hours of its day.")
    assert h["consider"] == "Whether those hours have the people the rest of the day has. The roster is changed in the roster, by a person."
    assert h["key"] == f"HOURS_COVER:{SITE['id']}:00" and h["rests_on"]["guard_hours_usual"] == 8.0
    # Not said when the busy hours are the well-rostered ones, when there are too few incidents, or no roster to compare.
    assert advice.hours_cover(SITE, [0] * 9 + [6, 6] + [0] * 3 + [3] + [0] * 9, day) is None
    assert advice.hours_cover(SITE, [0, 0, 4, 4] + [0] * 20, day) is None and advice.BUSY_FLOOR == 10
    assert advice.hours_cover(SITE, night, [0.0] * 24) is None
    assert advice.hours_cover(SITE, [1] * 24, day) is None, "incidents spread over the day gather nowhere"

    assert [advice.is_night(h) for h in (19, 20, 23, 0, 5, 6, 12)] == [False, True, True, True, True, False, False]
    slow = advice.slow_at_night(SITE, [900.0] * 5, [300.0] * 6)
    assert slow["statement"] == ("At Factory A, half of the guards sent between 20:00 and 06:00 in the last 4 weeks arrived "
                                 "within 15 minutes; in the rest of the day, within 5 minutes (5 and 6 sendings).")
    assert advice.slow_at_night(SITE, [900.0] * 4, [300.0] * 6) is None, "too few at night to say"
    assert advice.slow_at_night(SITE, [400.0] * 5, [300.0] * 5) is None, "not half as slow again"


def test_no_recommendation_judges_anybody_or_decides_anything():
    today = date(2026, 10, 8)
    said = [advice.certification(MEI, "PLRD licence", s, 2, today) for s in advice.STATUS_WORDS]
    said += [advice.course(MEI, "c", "First aid", today - timedelta(days=3), today),
             advice.course(MEI, "c", "First aid", today + timedelta(days=3), today),
             advice.missed_tours(MEI, 5, 5, []), advice.unstarted(SITE, 4, 9),
             advice.hours_cover(SITE, [0, 0, 6, 6] + [0] * 20, [0.0] * 8 + [6.0] * 8 + [0.0] * 8),
             advice.slow_at_night(SITE, [900.0] * 5, [300.0] * 5)]
    assert {r["code"] for r in said} == {c for codes in advice.CODES.values() for c in codes}
    for r in said:
        sentence = f"{r['statement']} {r['consider']}".lower()
        assert not [w for w in JUDGING if w in sentence], sentence
        assert r["is_advisory"] is True and r["is_decision"] is False
        assert (r["subject"] is None) != (r["site"] is None), "about a guard or about a site, not both"
    # Lateness and violations have their own review, by a person: they are counted and not turned into advice.
    assert not [c for codes in advice.CODES.values() for c in codes if re.search(r"LATE|VIOLATION|NO_SHOW", c)]
    for module in (advice, readings):
        code = Path(module.__file__).read_text(encoding="utf-8")
        for name in ("sklearn", "numpy", "torch", "anthropic", "openai"):
            assert not re.search(rf"^\s*(import|from)\s+{name}\b", code, re.M), name
        body = code.split('"""', 2)[2]
        assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", body), f"{module.__name__} only reads"
        assert not re.search(r"ORDER BY [a-z_.]*(late|missed|recorded|violat)[a-z_]* DESC|def \w*(score|rank|grade)", body), "nobody is ranked"
    assert "it ranks nobody" in readings.NOTE and "employment" in readings.NOTE and "employment" in advice.NOTE
    assert set(readings.SECTIONS) == set(readings.TITLE) == set(readings.NEEDS) == set(readings.COUNTED_FROM)
    for section in readings.SECTIONS:
        assert not re.search(r"score|rank|rating|grade|index", str(readings.blank(section)))
    assert [n["key"] for n in readings.not_read(EVERYTHING - {"violation:read", "training:read"})] == ["VIOLATIONS", "TRAINING"]


# ─── B. A reading, counted from the database ─────────────────────────────────

MEI_AT_A = {
    "SHIFTS": {"shifts": 4, "worked": 3, "late": 1, "late_minutes": 12, "not_started": 1},
    "PATROLS": {"tours_done": 2, "tours_missed": 3, "walked": 2, "checkpoints_scanned": 16, "checkpoints_total": 20},
    "RESPONSES": {"sent": 3, "accepted": 2, "declined": 1, "arrived": 2, "arrive_seconds": 480.0},
    "VIOLATIONS": {"recorded": 3, "waived": 1, "disputed": 1,
                   "by_type": {"no_show": 0, "late_checkin": 1, "geofence_failure": 1, "early_departure": 0, "manual": 0}},
    "HANDOVERS": {"given": 2, "accepted": 1, "disputed": 1},
}
MEI_TRAINING = {"completed": 1, "courses_lapsed_now": 1, "courses_lapsing_now": 1, "certificates_lapsed_now": 1,
                "certificates_lapsing_now": 1, "shifts_at_risk_now": 2}


async def _seed(w: dict) -> dict:
    """Four weeks of one guard's work at site A, a little at site B, a second
    guard with nothing recorded, and enough at site A for its cover to be said."""
    t, a, b = w["tenant"], w["site_a"], w["site_b"]
    zone = await _zone(w)
    tz = ZoneInfo(zone)
    today = datetime.now(tz).date()

    def at(days_ago: int, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(today - timedelta(days=days_ago), time(hour, minute), tzinfo=tz)

    s: dict = {k: uuid.uuid4() for k in ("cam", "route", "tour", "asha", "fire", "aid", "cctv", "patrol", "old")}
    s.update(zone=zone, today=today, mei=w["users"][GUARD])
    shifts = {k: uuid.uuid4() for k in ("d2", "d3", "d4", "d5", "b6", "o7", "o8", "f2", "f3")}
    s["shifts"] = shifts
    mei, op = w["users"][GUARD], w["users"][OPERATOR]
    shift = ("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status, "
             "is_late, late_minutes) VALUES (:i,:t,:site,:g,:from,:to,:began,:st,:late,:mins)")
    incidents = [uuid.uuid4() for _ in range(15)]
    await _run([
        ("UPDATE users SET full_name = 'Mei Lin' WHERE id = :u", {"u": mei}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) VALUES (:i,:t,5,:e,'x','Asha Rao')",
         {"i": s["asha"], "t": t, "e": f"asha-{s['asha'].hex[:8]}@drone.test"}),
        ("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)", {"u": s["asha"], "s": a, "t": t}),
        *[(shift, {"i": shifts[k], "t": t, "site": site, "g": who, "from": at(d, 8), "to": at(d, 16),
                   "began": at(d, 8) if worked else None, "st": status, "late": late, "mins": 12 if late else 0})
          for k, site, who, d, worked, status, late in (
              ("d2", a, mei, 2, True, "completed", False), ("d3", a, mei, 3, True, "completed", False),
              ("d4", a, mei, 4, True, "completed", True), ("d5", a, mei, 5, False, "scheduled", False),
              ("b6", b, mei, 6, True, "completed", False),
              ("o7", a, op, 7, False, "scheduled", False), ("o8", a, op, 8, False, "scheduled", False),
              ("f2", a, mei, -2, False, "scheduled", False), ("f3", a, mei, -3, False, "scheduled", False))],
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate A')", {"i": s["cam"], "t": t, "s": a}),
        # Twelve incidents in the small hours and three in the afternoon: the site's busy hours are its unrostered ones.
        *[("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
           "VALUES (:i,:t,:c,'Forced gate','high','open',:at)",
           {"i": incidents[n], "t": t, "c": s["cam"], "at": at(2 + n, 2, 30) if n < 12 else at(n - 10, 14)})
          for n in range(15)],
        *[("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state, accepted_at, "
           "arrived_at, declined_at, decline_reason) VALUES (:t,:i,:site,:g,:at,:st,:acc,:arr,:dec,:why)",
           {"t": t, "i": incidents[12 + n], "site": a, "g": mei, "at": at(2 + n, 14, 5), "st": state,
            "acc": at(2 + n, 14, 6) if state != "DECLINED" else None,
            "arr": at(2 + n, 14, 5) + timedelta(minutes=minutes) if minutes else None,
            "dec": at(2 + n, 14, 6) if state == "DECLINED" else None, "why": "On another call" if state == "DECLINED" else None})
          for n, (state, minutes) in enumerate((("ARRIVED", 6), ("DECLINED", 0), ("ARRIVED", 10)))],
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')", {"i": s["route"], "t": t, "s": a}),
        ("INSERT INTO tour_schedules (id, tenant_id, route_id, name, scheduled_time, assigned_guard_user_id) "
         "VALUES (:i,:t,:r,'Perimeter tour','22:00',:g)", {"i": s["tour"], "t": t, "r": s["route"], "g": mei}),
        *[("INSERT INTO tour_occurrences (tenant_id, schedule_id, scheduled_at, window_end, status) VALUES (:t,:sch,:at,:end,:st)",
           {"t": t, "sch": s["tour"], "at": at(d, 22), "end": at(d, 23), "st": status})
          for d, status in ((2, "completed"), (3, "completed"), (4, "missed"), (5, "missed"), (6, "missed"), (1, "pending"))],
        *[("INSERT INTO patrol_sessions (tenant_id, route_id, guard_user_id, status, started_at, total_checkpoints, scanned_checkpoints) "
           "VALUES (:t,:r,:g,'completed',:at,10,:n)", {"t": t, "r": s["route"], "g": mei, "at": at(d, 22), "n": n})
          for d, n in ((2, 10), (3, 6))],
        *[("INSERT INTO violations (tenant_id, guard_user_id, site_id, violation_type, status, occurred_at) VALUES (:t,:g,:s,:k,:st,:at)",
           {"t": t, "g": mei, "s": a, "k": kind, "st": status, "at": at(d, 9)})
          for d, kind, status in ((4, "late_checkin", "open"), (5, "no_show", "waived"), (6, "geofence_failure", "disputed"))],
        ("INSERT INTO shift_handovers (tenant_id, shift_id, outgoing_guard_id, status, created_at) VALUES (:t,:sh,:g,'accepted',:at)",
         {"t": t, "sh": shifts["d2"], "g": mei, "at": at(2, 16)}),
        ("INSERT INTO shift_handovers (tenant_id, shift_id, outgoing_guard_id, status, dispute_reason, created_at) "
         "VALUES (:t,:sh,:g,'disputed','Keys not counted',:at)", {"t": t, "sh": shifts["d3"], "g": mei, "at": at(3, 16)}),
        *[("INSERT INTO training_courses (id, tenant_id, name, category, is_active) VALUES (:i,:t,:n,:c,:on)",
           {"i": s[k], "t": t, "n": name, "c": category, "on": on})
          for k, name, category, on in (("fire", "Fire safety", "fire_safety", True), ("aid", "First aid", "first_aid", True),
                                        ("cctv", "CCTV basics", "cctv", True), ("patrol", "Security patrol basics", "security", True),
                                        ("old", "Retired course", "security", False))],
        *[("INSERT INTO training_records (tenant_id, user_id, course_id, completed_at, passed, expires_at) VALUES (:t,:u,:c,:done,:ok,:exp)",
           {"t": t, "u": mei, "c": s[k], "done": today - timedelta(days=done), "ok": passed,
            "exp": today + timedelta(days=expires) if expires is not None else None})
          for k, done, passed, expires in (("fire", 20, True, 700), ("aid", 400, True, -10), ("aid", 800, True, -400),
                                           ("cctv", 350, True, 12), ("fire", 5, False, None), ("old", 400, True, -30))],
        *[("INSERT INTO guard_certifications (tenant_id, user_id, certification_type, expires_at, is_valid) VALUES (:t,:u,:k,:exp,:ok)",
           {"t": t, "u": mei, "k": kind, "exp": today + timedelta(days=days), "ok": valid})
          for kind, days, valid in (("WSQ Security", 5, True), ("First Aid Certificate", -3, True), ("Old licence", -900, False),
                                    ("PLRD ID", 400, True))],
        *[("INSERT INTO shift_certification_findings (tenant_id, shift_id, guard_user_id, site_id, certification_type, status, shift_date) "
           "VALUES (:t,:sh,:g,:s,:k,:st,:day)",
           {"t": t, "sh": shifts[k], "g": mei, "s": a, "k": kind, "st": status, "day": today + timedelta(days=days)})
          for k, kind, status, days in (("f2", "PLRD licence", "MISSING", 2), ("f3", "PLRD licence", "MISSING", 3),
                                        ("f2", "WSQ Security", "EXPIRING", 2))],
    ])
    return s


async def test_a_reading_counts_what_is_recorded_beside_how_much_there_was_to_do():
    w, other = await _world(), await _world()
    s = await _seed(w)
    site = str(w["site_a"])
    async with _client() as c:
        r = await c.get(f"{BASE}/readings", headers=w["h"][MANAGER], params={"site_id": site})
        assert r.status_code == 200, r.text
        view = r.json()
        assert [x["key"] for x in view["sections"]] == list(readings.SECTIONS) and view["not_read"] == []
        assert {x["key"]: x["personal"] for x in view["sections"]}["TRAINING"] is True
        assert (view["period"]["days"], view["site"]["name"], view["note"]) == (28, "Factory A", readings.NOTE)
        # In order of name, and nothing else: a guard with nothing recorded is still on the list.
        assert [g["name"] for g in view["guards"]] == ["Asha Rao", "Mei Lin", "Role 4 User"]
        asha, mei, operator = view["guards"]
        for key, figures in MEI_AT_A.items():
            assert mei["figures"][key] == figures, key
        assert mei["figures"]["TRAINING"] == MEI_TRAINING
        assert asha["figures"]["SHIFTS"] == readings.blank("SHIFTS") and asha["figures"]["TRAINING"] == readings.blank("TRAINING")
        assert operator["figures"]["SHIFTS"] == {"shifts": 2, "worked": 0, "late": 0, "late_minutes": 0, "not_started": 2}
        # No figure is a total of a person, and no key is a score.
        assert set(mei["figures"]) == set(readings.SECTIONS) and "score" not in r.text.lower().replace("not an appraisal", "")
        # The site's figures are its guards' added up; what is a person's is in no site's.
        (factory,) = view["sites"]
        assert factory["figures"]["SHIFTS"] == {"shifts": 6, "worked": 3, "late": 1, "late_minutes": 12, "not_started": 3}
        assert "TRAINING" not in factory["figures"] and "TRAINING" not in view["total"]
        assert view["total"] == factory["figures"] and view["no_site"] is None

        # Every site: the shift at site B is hers too.
        every = (await c.get(f"{BASE}/readings", headers=w["h"][ADMIN])).json()
        hers = next(g for g in every["guards"] if g["name"] == "Mei Lin")["figures"]
        assert hers["SHIFTS"] == {**MEI_AT_A["SHIFTS"], "shifts": 5, "worked": 4} and hers["TRAINING"] == MEI_TRAINING
        assert [x["name"] for x in every["sites"]] == ["Factory A", "Factory B"]
        assert every["sites"][1]["figures"]["SHIFTS"]["worked"] == 1
        # A week is a week: what is older is not in it.
        week = (await c.get(f"{BASE}/readings", headers=w["h"][ADMIN], params={"site_id": site, "days": 7})).json()
        in_week = next(g for g in week["guards"] if g["name"] == "Mei Lin")["figures"]
        assert in_week["SHIFTS"]["shifts"] == 4 and in_week["TRAINING"]["completed"] == 0 and in_week["TRAINING"]["courses_lapsed_now"] == 1
        for days in (1, 30, 365):
            r = await c.get(f"{BASE}/readings", headers=w["h"][ADMIN], params={"days": days})
            assert r.status_code == 422 and "7, 28 or 90 days" in r.json()["detail"]

        # One guard: the same, and site by site.
        one = await c.get(f"{BASE}/readings/{s['mei']}", headers=w["h"][MANAGER])
        assert one.status_code == 200, one.text
        hers = one.json()
        assert hers["guard"]["name"] == "Mei Lin" and hers["figures"]["TRAINING"] == MEI_TRAINING
        assert hers["figures"]["SHIFTS"]["shifts"] == 5 and hers["figures"]["RESPONSES"] == MEI_AT_A["RESPONSES"]
        assert [(x["name"], x["figures"]["SHIFTS"]["shifts"]) for x in hers["by_site"]] == [("Factory A", 4), ("Factory B", 1)]
        assert hers["note"] == readings.NOTE and "not an appraisal" in hers["note"]
        assert (await c.get(f"{BASE}/readings/{uuid.uuid4()}", headers=w["h"][MANAGER])).status_code == 404
        # Another organisation reads none of it.
        theirs = (await c.get(f"{BASE}/readings", headers=other["h"][ADMIN], params={"days": 90})).json()
        assert "Mei Lin" not in str(theirs) and theirs["total"]["SHIFTS"] == readings.blank("SHIFTS")
        assert (await c.get(f"{BASE}/readings/{s['mei']}", headers=other["h"][ADMIN])).status_code == 404

    # That somebody's reading was read is written down.
    listed = await _audit(w, "workforce.readings.read")
    assert len(listed) == 3 and listed[0]["detail"]["guards"] == 3
    looked = await _audit(w, "workforce.reading.read")
    assert [(str(e["resource_id"]), e["user_id"]) for e in looked] == [(str(s["mei"]), w["users"][MANAGER])]


# ─── C. Whose reading somebody may read ──────────────────────────────────────

async def test_who_may_read_whose_reading():
    w = await _world()
    s = await _seed(w)
    async with _client() as c:
        # Somebody held to site A reads what is recorded there: her shift at site B is not theirs to see.
        held = (await c.get(f"{BASE}/readings", headers=w["h"][SUPERVISOR])).json()
        hers = next(g for g in held["guards"] if g["name"] == "Mei Lin")["figures"]
        assert hers["SHIFTS"] == MEI_AT_A["SHIFTS"] and [x["name"] for x in held["sites"]] == ["Factory A"]
        one = (await c.get(f"{BASE}/readings/{s['mei']}", headers=w["h"][SUPERVISOR])).json()
        assert one["figures"]["SHIFTS"]["shifts"] == 4 and [x["name"] for x in one["by_site"]] == ["Factory A"]
        assert (await c.get(f"{BASE}/readings", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        # A guard who has only ever worked elsewhere is not theirs to look up.
        away = uuid.uuid4()
        await _run([
            ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) VALUES (:i,:t,5,:e,'x','Ben Tan')",
             {"i": away, "t": w["tenant"], "e": f"ben-{away.hex[:8]}@drone.test"}),
            ("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)", {"u": away, "s": w["site_b"], "t": w["tenant"]}),
        ])
        assert (await c.get(f"{BASE}/readings/{away}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/readings/{away}", headers=w["h"][MANAGER])).status_code == 200
        assert "Ben Tan" not in str((await c.get(f"{BASE}/readings", headers=w["h"][SUPERVISOR])).json())

        # Another person's reading is for the people a guard answers to — not an operator, a viewer or another guard.
        for role in (OPERATOR, VIEWER, GUARD):
            for path in ("/readings", f"/readings/{s['mei']}", "/recommendations", "/recommendations/answers"):
                assert (await c.get(BASE + path, headers=w["h"][role])).status_code == 403, (role, path)
        assert (await c.get(f"{BASE}/readings")).status_code in (401, 403)

        # A person's own reading is theirs, and is whole: every section, at every site, whatever else they may read.
        mine = await c.get(f"{BASE}/me", headers=w["h"][GUARD])
        assert mine.status_code == 200, mine.text
        own = mine.json()
        assert own["guard"]["name"] == "Mei Lin" and [x["key"] for x in own["sections"]] == list(readings.SECTIONS)
        assert own["figures"]["SHIFTS"]["shifts"] == 5 and own["figures"]["RESPONSES"] == MEI_AT_A["RESPONSES"]
        assert own["figures"]["TRAINING"] == MEI_TRAINING and own["not_read"] == []
        assert {r["code"] for r in own["recommendations"]} == {"CERTIFICATION", "COURSE_LAPSED", "COURSE_LAPSING", "MISSED_TOURS"}
        assert not [r for r in own["recommendations"] if r["may_answer"] or r["answer"]]
        # It is theirs alone: the operator's own is the operator's, and a viewer has none.
        theirs = (await c.get(f"{BASE}/me", headers=w["h"][OPERATOR])).json()
        assert theirs["guard"]["name"] == "Role 4 User" and theirs["figures"]["SHIFTS"]["not_started"] == 2
        assert (await c.get(f"{BASE}/me", headers=w["h"][VIEWER])).status_code == 403
        assert (await c.get(f"{BASE}/me", headers=w["h"][MANAGER])).status_code == 403
        assert (await c.get(f"{BASE}/me", headers=w["h"][GUARD], params={"days": 3})).status_code == 422
    assert len(await _audit(w, "workforce.reading.read")) == 2, "one's own reading is not a look at somebody else"

    # A section is read under its own permission.
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        some = await readings.read(db, frozenset({"shift:read", "training:read"}), [w["site_a"]], now - timedelta(days=28), now, now)
        await db.rollback()
    assert set(some["total"]) == {"SHIFTS"} and set(some["guards"][str(s["mei"])]) == {"SHIFTS"}
    assert [n["key"] for n in some["not_read"]] == ["PATROLS", "RESPONSES", "VIOLATIONS", "HANDOVERS"]

    # A support session reads nobody's reading; a key reads but does not answer.
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            assert (await c.get(f"{BASE}/readings")).status_code == 200
            r = await c.post(f"{BASE}/recommendations/answer", json={"key": f"MISSED_TOURS:{s['mei']}", "answer": "ACCEPTED"})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_token_payload, None)


# ─── D. Recommendations and a manager's answer ───────────────────────────────

async def test_recommendations_say_what_is_recorded_and_an_answer_changes_nothing_else():
    w = await _world()
    s = await _seed(w)
    mei, site = str(s["mei"]), str(w["site_a"])
    counts = ("SELECT (SELECT count(*) FROM shifts WHERE tenant_id = :t) AS shifts, "
              "(SELECT count(*) FROM training_records WHERE tenant_id = :t) AS records, "
              "(SELECT count(*) FROM training_attempts WHERE tenant_id = :t) AS attempts, "
              "(SELECT count(*) FROM violations WHERE tenant_id = :t) AS violations, "
              "(SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts")
    (before,) = await _sql(counts, {"t": w["tenant"]})
    async with _client() as c:
        r = await c.get(f"{BASE}/recommendations", headers=w["h"][MANAGER])
        assert r.status_code == 200, r.text
        view = r.json()
        assert (view["weeks"], view["can_answer"], view["note"]) == (4, True, advice.NOTE)
        assert view["is_advisory"] is True and view["is_decision"] is False and view["not_read"] == []
        training = {x["key"]: x for x in view["training"]}
        assert set(training) == {f"CERTIFICATION:{mei}:PLRD licence:MISSING", f"CERTIFICATION:{mei}:WSQ Security:EXPIRING",
                                 f"COURSE:{mei}:{s['aid']}", f"COURSE:{mei}:{s['cctv']}", f"MISSED_TOURS:{mei}"}
        missing = training[f"CERTIFICATION:{mei}:PLRD licence:MISSING"]
        assert missing["statement"].startswith("Mei Lin is rostered for 2 shifts from ") and missing["statement"].endswith(
            "that need PLRD licence, and holds none on file.")
        assert missing["subject"] == {"user_id": mei, "name": "Mei Lin"} and missing["site"] is None
        # The latest pass is the one that counts; a course that is no longer run is not recommended again.
        assert training[f"COURSE:{mei}:{s['aid']}"]["code"] == "COURSE_LAPSED"
        assert training[f"COURSE:{mei}:{s['cctv']}"]["code"] == "COURSE_LAPSING"
        assert not [k for k in training if str(s["old"]) in k or str(s["fire"]) in k]
        tours = training[f"MISSED_TOURS:{mei}"]
        assert tours["statement"] == "Mei Lin missed 3 of the 5 tours assigned to them that fell due in the last 4 weeks."
        assert tours["consider"].endswith("Courses filed under security: Security patrol basics.")
        coverage = {x["code"]: x for x in view["coverage"]}
        assert set(coverage) == {"UNSTARTED_SHIFTS", "HOURS_COVER"}
        assert coverage["UNSTARTED_SHIFTS"]["statement"] == "3 of the 6 shifts due at Factory A in the last 4 weeks were not started."
        assert coverage["HOURS_COVER"]["statement"] == (
            "80% of the incidents at Factory A in the last 4 weeks fell between 00:00 and 04:00 (12 of 15). "
            "0 guard-hours were rostered in those hours, against 8 for an average four hours of its day.")
        assert coverage["HOURS_COVER"]["site"] == {"id": site, "name": "Factory A"} and coverage["HOURS_COVER"]["subject"] is None
        for rec in (*view["training"], *view["coverage"]):
            assert rec["answer"] is None and rec["may_answer"] is True and rec["is_decision"] is False
            assert not [word for word in JUDGING if word in f"{rec['statement']} {rec['consider']}".lower()]
        # Nothing is said of a guard who was late, or of one with violations: those are counted, and reviewed by a person.
        assert not re.search(r"late|violation", " ".join(x["statement"] for x in view["training"]).lower())
        # For one site: the same; for a site with nothing to say, nothing.
        assert (await c.get(f"{BASE}/recommendations", headers=w["h"][MANAGER], params={"site_id": str(w["site_b"])})).json()["coverage"] == []
        hers = (await c.get(f"{BASE}/readings/{mei}", headers=w["h"][MANAGER])).json()
        assert {x["key"] for x in hers["recommendations"]} == set(training) and hers["recommendations_note"] == advice.NOTE

        url = f"{BASE}/recommendations/answer"
        key = f"MISSED_TOURS:{mei}"
        for body, status, words in (
            ({"key": key, "answer": "NOT_ACCEPTED"}, 422, "Say why"), ({"key": key, "answer": "NOT_ACCEPTED", "reason": " "}, 422, "Say why"),
            ({"key": key, "answer": "ASSIGNED"}, 422, None), ({"key": key}, 422, None),
            # The statement kept is the server's own: there is no sending one, and no naming a course to assign.
            ({"key": key, "answer": "ACCEPTED", "statement": "Fine."}, 422, None),
            ({"key": key, "answer": "ACCEPTED", "course_id": str(s["patrol"])}, 422, None),
            ({"key": "MISSED_TOURS", "answer": "ACCEPTED"}, 409, "no longer stands"),
            ({"key": f"MISSED_TOURS:{s['asha']}", "answer": "ACCEPTED"}, 409, "no longer stands"),
            ({"key": f"MISSED_TOURS:{uuid.uuid4()}", "answer": "ACCEPTED"}, 409, "no longer stands"),
            ({"key": f"SLOW_AT_NIGHT:{site}", "answer": "ACCEPTED"}, 409, "no longer stands"),
            ({"key": f"UNSTARTED_SHIFTS:{w['site_b']}", "answer": "ACCEPTED"}, 409, "no longer stands"),
        ):
            r = await c.post(url, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(url, headers=w["h"][role], json={"key": key, "answer": "ACCEPTED"})).status_code == 403

        no = await c.post(url, headers=w["h"][SUPERVISOR], json={
            "key": key, "answer": "NOT_ACCEPTED", "reason": "  The route was closed for works that week.  "})
        assert no.status_code == 201, no.text
        assert no.json()["answer"] == {"answer": "NOT_ACCEPTED", "reason": "The route was closed for works that week.",
                                       "answered_at": no.json()["answer"]["answered_at"], "answered_by_name": "Role 3 User",
                                       "said_then": None}
        yes = await c.post(url, headers=w["h"][MANAGER], json={"key": f"HOURS_COVER:{site}:00", "answer": "ACCEPTED"})
        assert yes.status_code == 201 and yes.json()["answer"]["answered_by_name"] == "Role 8 User"
        # Shown again, each carries its latest answer; a changed mind is a second answer, and both stay.
        again = (await c.get(f"{BASE}/recommendations", headers=w["h"][MANAGER])).json()
        assert next(x for x in again["training"] if x["key"] == key)["answer"]["answer"] == "NOT_ACCEPTED"
        assert next(x for x in again["coverage"] if x["code"] == "HOURS_COVER")["answer"]["answer"] == "ACCEPTED"
        assert (await c.post(url, headers=w["h"][MANAGER], json={"key": key, "answer": "ACCEPTED"})).status_code == 201
        history = (await c.get(f"{BASE}/recommendations/answers", headers=w["h"][MANAGER])).json()["items"]
        assert [(x["kind"], x["answer"], x["answered_by_name"]) for x in history] == [
            ("TRAINING", "ACCEPTED", "Role 8 User"), ("COVERAGE", "ACCEPTED", "Role 8 User"), ("TRAINING", "NOT_ACCEPTED", "Role 3 User")]
        assert history[0]["guard_name"] == "Mei Lin" and history[1]["site_name"] == "Factory A" and history[1]["guard_name"] is None
        assert history[2]["statement"].startswith("Mei Lin missed 3 of the 5 tours") and history[2]["rests_on"]["missed"] == 3
        # Somebody held to site A is given the answers about site A and about the guards posted there.
        held = (await c.get(f"{BASE}/recommendations/answers", headers=w["h"][SUPERVISOR])).json()["items"]
        assert [x["kind"] for x in held] == ["TRAINING", "COVERAGE", "TRAINING"]
        # An answer about a guard who has only worked elsewhere, or about another site, is not theirs.
        await _sql("INSERT INTO workforce_advice_answers (tenant_id, kind, site_id, advice_key, code, statement, answer) "
                   "VALUES (:t,'COVERAGE',:s,'UNSTARTED_SHIFTS:b','UNSTARTED_SHIFTS','At site B.','ACCEPTED')",
                   {"t": w["tenant"], "s": w["site_b"]})
        assert len((await c.get(f"{BASE}/recommendations/answers", headers=w["h"][SUPERVISOR])).json()["items"]) == 3
        assert len((await c.get(f"{BASE}/recommendations/answers", headers=w["h"][MANAGER])).json()["items"]) == 4

    entries = await _audit(w, "workforce.answer")
    assert [(e["detail"]["kind"], e["detail"]["code"], e["detail"]["answer"], e["user_id"]) for e in entries] == [
        ("TRAINING", "MISSED_TOURS", "NOT_ACCEPTED", w["users"][SUPERVISOR]),
        ("COVERAGE", "HOURS_COVER", "ACCEPTED", w["users"][MANAGER]), ("TRAINING", "MISSED_TOURS", "ACCEPTED", w["users"][MANAGER])]
    # Accepting assigned no course, moved no shift, recorded nothing against anybody and told nobody.
    (after,) = await _sql(counts, {"t": w["tenant"]})
    assert dict(before) == dict(after)
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", router)) == {"workforce_advice_answers"}
    for word in ("redis", "response_notify", "send_expo_push", "smtp", "webhook", "roster_autoschedule"):
        assert word not in router.lower(), word


# ─── E. What the application role and the database refuse ────────────────────

async def test_an_answer_is_added_and_never_rewritten_and_what_the_database_refuses():
    w, other = await _world(), await _world()
    s = await _seed(w)
    async with _client() as c:
        r = await c.post(f"{BASE}/recommendations/answer", headers=w["h"][MANAGER],
                         json={"key": f"MISSED_TOURS:{s['mei']}", "answer": "ACCEPTED"})
        assert r.status_code == 201

    async def as_app(statement: str, params: dict, tenant=None) -> list:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant or w["tenant"])})
            assert not (await db.execute(text(
                "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
            try:
                result = await db.execute(text(statement), params)
                return [dict(x) for x in result.mappings()] if result.returns_rows else []
            finally:
                await db.rollback()

    for statement in ("UPDATE workforce_advice_answers SET answer = 'NOT_ACCEPTED', reason = 'x'",
                      "UPDATE workforce_advice_answers SET statement = 'Rewritten'", "DELETE FROM workforce_advice_answers"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await as_app(statement, {})
    assert len(await as_app("SELECT id FROM workforce_advice_answers", {})) == 1
    assert await as_app("SELECT id FROM workforce_advice_answers", {}, other["tenant"]) == []
    grants = await _sql("SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee = 'svc_app' AND table_name = 'workforce_advice_answers'")
    assert {g["privilege_type"] for g in grants} == {"SELECT", "INSERT"}
    (rls,) = await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'workforce_advice_answers'")
    assert rls["relrowsecurity"] and rls["relforcerowsecurity"]

    row = ("INSERT INTO workforce_advice_answers (tenant_id, kind, subject_user_id, site_id, advice_key, code, statement, answer, reason) "
           "VALUES (:t,:kind,:guard,:site,'K','MISSED_TOURS',:statement,:answer,:reason)")
    base = {"t": w["tenant"], "kind": "TRAINING", "guard": s["mei"], "site": None, "statement": "S", "answer": "ACCEPTED", "reason": None}
    for change, constraint in (
        ({"answer": "MAYBE"}, "ck_wfans_answer"), ({"kind": "PAY"}, "ck_wfans_kind"),
        ({"answer": "NOT_ACCEPTED"}, "ck_wfans_reason"), ({"answer": "NOT_ACCEPTED", "reason": "  "}, "ck_wfans_reason"),
        ({"statement": "  "}, "ck_wfans_statement"),
        # A training recommendation is about a person; a coverage one is about a site and about nobody.
        ({"guard": None}, "ck_wfans_subject"), ({"kind": "COVERAGE"}, "ck_wfans_subject"),
        ({"kind": "COVERAGE", "site": w["site_a"]}, "ck_wfans_subject"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            await _sql(row, {**base, **change})
    await _sql(row, {**base, "kind": "COVERAGE", "guard": None, "site": w["site_a"]})
    # A guard who is removed takes the answers about them away; whoever answered being removed leaves the answer.
    keys = await _sql("SELECT a.attname AS col, c.confdeltype::text AS on_delete FROM pg_constraint c "
                      "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                      "WHERE c.conrelid = 'workforce_advice_answers'::regclass AND c.contype = 'f'")
    assert {k["col"]: k["on_delete"] for k in keys} == {"tenant_id": "c", "subject_user_id": "c", "site_id": "c",
                                                       "answered_by_user_id": "n"}


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
                    served[(method, path.removeprefix(BASE).replace("{user_id:uuid}", "{id}"))] = _needs(route)
    read = {"workforce:read"}
    assert served == {("GET", "/readings"): read, ("GET", "/readings/{id}"): read, ("GET", "/me"): {"workforce:own"},
                      ("GET", "/recommendations"): read, ("GET", "/recommendations/answers"): read,
                      ("POST", "/recommendations/answer"): read | {"workforce:answer"}}
    assert not [m for m, _ in served if m in ("DELETE", "PUT", "PATCH")]


async def test_who_holds_the_three_permissions_and_what_this_phase_left_alone():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'workforce:%' "
                      "GROUP BY p.code, p.category")
    assert {r["code"]: list(r["roles"]) for r in rows} == {
        "workforce:read": [2, 3, 8], "workforce:answer": [2, 3, 8], "workforce:own": [3, 4, 5]}
    assert {r["category"] for r in rows} == {"workforce"}
    known = {r["code"] for r in await _sql("SELECT code FROM permissions WHERE code = ANY(:c)", {"c": list(api.SOURCE_PERMISSIONS)})}
    assert known == set(api.SOURCE_PERMISSIONS)
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["workforce_advice_answers"]
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"workforce_advice_answers"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade) and "TRUNC" + "ATE" not in upgrade
    # Nothing is kept about a guard but a manager's answer: no score, no points, no flag.
    columns = await _sql("SELECT column_name FROM information_schema.columns WHERE table_name = 'workforce_advice_answers'")
    assert not [c["column_name"] for c in columns if re.search(r"score|point|rank|grade|flag|rating", c["column_name"])]
    (layer,) = await _sql("SELECT count(*) AS n FROM permissions WHERE category = 'security_intelligence'")
    assert layer["n"] == 7
    # The roster, the training module and the violations review are not touched by any of it.
    services = Path(readings.__file__).parent
    for name in ("roster.py", "roster_autoschedule.py", "training.py", "violations.py", "certification_compliance.py"):
        source = (services / name).read_text(encoding="utf-8")
        assert "workforce_readings" not in source and "workforce_advice" not in source, name
