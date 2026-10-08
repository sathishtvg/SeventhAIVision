"""The daily briefing: drafted from a day's counts, reviewed by a person, published, and kept.

  A — The day, the words and the draft, with nothing running
  B — Drafting, reviewing, publishing and correcting
  C — Who may draft and read which briefing
  D — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced.

The claims, each with tests: a line is a fixed sentence with a count in it and
says when it is true of; nobody is named and nothing is forecast; the platform
drafts and a person publishes; the counted lines are not edited — a section is
left out whole and the reviewer's words are kept apart; a published briefing is
not changed and a correction is a new revision; publishing tells nobody.
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
from app.routers import daily_briefings as api
from app.services import daily_briefing as briefing
from app.services import intel_insight, ops_board
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/daily-briefings"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0152_daily_briefings.py").read_text(encoding="utf-8")
SGT = "Asia/Singapore"
EVERYTHING = frozenset({*ops_board.NEEDS.values(), *ops_board.PART_NEEDS.values()})
#: Words that would make a count sound like a forecast.
FORETELLING = ("will ", "likely", "predict", "expect", "forecast", "probab", "chance", "risk of")

A_DAY = {
    "INCIDENTS": {"opened": 4, "by_severity": {"critical": 1, "high": 2, "medium": 1, "low": 0}, "resolved": 2,
                  "opened_still_open": 3, "open_now": 4},
    "RESPONSE": {"opened": 4, "acknowledged": 3, "acknowledge_seconds": 240.0, "resolved": 1, "resolve_seconds": 600.0,
                 "sent": 2, "arrived": 1, "declined": 1, "arrive_seconds": 360.0,
                 "missed": {"acknowledge": 1, "arrival": 0, "resolve": 1}},
    "PATROLS": {"tours": {"scheduled": 3, "done": 1, "partial": 0, "missed": 1, "failed": 0, "open": 1, "cancelled": 0},
                "virtual": {"scheduled": 4, "done": 1, "partial": 1, "missed": 1, "failed": 0, "open": 1, "cancelled": 1},
                "drone": {"scheduled": 4, "done": 1, "partial": 0, "missed": 0, "failed": 2, "open": 1, "cancelled": 0}},
    "GUARDS": {"on_shift_now": 1, "due_not_started_now": 1, "shifts": 4, "worked": 2, "late": 1, "not_started": 1},
    "DEVICES": {"devices": 3, "by_state": {"OK": 1, "DEGRADED": 0, "DOWN": 1, "NOT_KNOWN": 1, "OFF": 0}},
    "VISITORS": {"on_site_now": 2, "arrived": 2, "departed": 1, "refused": 1, "waiting_now": 1},
    "MAINTENANCE": {"suggested_now": 1, "open_now": 2, "in_progress_now": 1, "overdue_now": 1, "raised": 2, "done": 1},
}
ADVICE = [{"statement": f"Statement {n}.", "confidence": {"level": "MEDIUM", "why": f"Rests on {n}0 records over 4 weeks."}}
          for n in range(1, 6)]


def _lines(content: dict, key: str) -> list[tuple[str, str]]:
    return [(line["text"], line["as_at"]) for s in content["sections"] if s["key"] == key for line in s["lines"]]


# ─── A. The day, the words and the draft ─────────────────────────────────────

def test_a_briefing_is_for_a_calendar_day_where_the_site_is_and_a_day_not_over_is_counted_so_far():
    after = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)
    start, end, whole = briefing.day(date(2026, 10, 7), SGT, after)
    assert (start, end, whole) == (datetime(2026, 10, 6, 16, 0, tzinfo=timezone.utc),
                                   datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc), True)
    during = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    assert briefing.day(date(2026, 10, 7), SGT, during) == (start, during, False)
    # The same date is another day somewhere else.
    assert briefing.day(date(2026, 10, 7), "UTC", after)[0] == datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    # A day when the clocks change is the day it is, not twenty-four hours.
    spring = briefing.day(date(2026, 3, 29), "Europe/London", after)
    assert spring[1] - spring[0] == timedelta(hours=23) and spring[2] is True


def test_a_count_and_a_length_of_time_are_said_in_words():
    assert [briefing.count(n, "incident was", "incidents were") for n in (0, 1, 2)] == [
        "No incidents were", "1 incident was", "2 incidents were"]
    assert [briefing.lasting(s) for s in (None, 0, 59, 60, 240, 3540, 3599, 4800, 86400, 97200)] == [
        "an unmeasured time", "under a minute", "under a minute", "1 minute", "4 minutes", "59 minutes", "1 hour",
        "1 hour 20 minutes", "1 day", "1 day 3 hours"]


def test_a_draft_is_fixed_sentences_with_the_days_counts_in_them():
    content = briefing.draft(A_DAY, True, ADVICE, [])
    assert [s["key"] for s in content["sections"]] == list(briefing.SECTIONS)
    assert [s["title"] for s in content["sections"]][-1] == "What stands out"
    assert _lines(content, "INCIDENTS") == [
        ("4 incidents were opened: 1 critical, 2 high and 1 medium.", "PERIOD"),
        ("3 of those opened are still open.", "DRAFTING"),
        ("2 incidents were resolved.", "PERIOD"),
        ("4 incidents are open in all.", "DRAFTING")]
    assert _lines(content, "RESPONSE") == [
        ("Somebody acted on 3 of the 4 opened; on half of them within 4 minutes.", "PERIOD"),
        ("1 of the 4 was resolved; half within 10 minutes.", "PERIOD"),
        ("2 guards were sent; 1 arrived, half within 6 minutes; 1 declined.", "PERIOD"),
        ("2 response clocks were missed: 1 to acknowledge and 1 to resolve.", "PERIOD")]
    assert _lines(content, "PATROLS") == [
        ("Guard tours: 1 done of 2 that are over; 1 missed.", "PERIOD"),
        ("Guard tours: 1 still to be done.", "DRAFTING"),
        ("Virtual patrols: 1 done of 3 that are over; 1 done in part and 1 missed.", "PERIOD"),
        ("Virtual patrols: 1 still to be done.", "DRAFTING"),
        ("Drone patrols: 1 done of 3 that are over; 2 failed.", "PERIOD"),
        ("Drone patrols: 1 still to be done.", "DRAFTING")]
    assert _lines(content, "GUARDS") == [
        ("4 shifts were due to begin: 2 worked, 1 of them started late, 1 not started.", "PERIOD"),
        ("1 guard is on shift; 1 shift that is due has not been started.", "DRAFTING")]
    assert _lines(content, "DEVICES") == [
        ("Of 3 devices, 1 read as working; 1 read as down and 1 with no reading.", "DRAFTING")]
    assert _lines(content, "VISITORS") == [
        ("2 arrivals and 1 departure were logged at the gate; 1 visitor was refused.", "PERIOD"),
        ("2 visitors are on site; 1 visit is waiting for a decision.", "DRAFTING")]
    assert _lines(content, "MAINTENANCE") == [
        ("2 work orders were raised, and 1 was completed.", "PERIOD"),
        ("3 orders are in hand, 1 of them overdue; 1 suggestion is waiting for a person.", "DRAFTING")]
    # What stands out is the advice word for word with what it rests on, three pieces of it, and says what it is.
    assert _lines(content, "ADVICE") == [(f"Statement {n}. Rests on {n}0 records over 4 weeks.", "WEEKS") for n in (1, 2, 3)]
    advice = content["sections"][-1]
    assert advice["note"] == briefing.ADVICE_NOTE and "not a forecast" in advice["note"]
    assert advice["figures"] == {"standing": 5, "shown": 3}
    # Every section carries the figures its lines were made of.
    for section in content["sections"][:-1]:
        assert section["figures"] == A_DAY[section["key"]], section["key"]
    assert {when for s in content["sections"] for _, when in _lines(content, s["key"])} == set(briefing.WHEN)
    assert briefing.draft(A_DAY, True, ADVICE, []) == content, "the same figures give the same lines"


def test_a_quiet_day_and_a_day_with_parts_unread_are_said_as_they_are():
    quiet = {key: ops_board.blank(key, EVERYTHING) for key in ops_board.SECTIONS}
    content = briefing.draft(quiet, False, [], [])
    assert _lines(content, "INCIDENTS") == [("No incidents were opened.", "PERIOD"), ("No incidents are open in all.", "DRAFTING")]
    # With the clocks off nothing was measured against them — which is not the same as none being missed.
    assert _lines(content, "RESPONSE") == [
        ("No incidents were opened, so no response was timed.", "PERIOD"),
        ("The response clocks are switched off, so nothing was measured against them.", "PERIOD")]
    assert _lines(briefing.draft(quiet, True, [], []), "RESPONSE")[-1] == ("No response clock was missed.", "PERIOD")
    assert _lines(content, "PATROLS") == [(f"{label}: none fell due.", "PERIOD") for label in ops_board.PATROL_LABEL.values()]
    assert _lines(content, "GUARDS") == [("No shifts were due to begin.", "PERIOD"), ("No guards are on shift.", "DRAFTING")]
    assert _lines(content, "DEVICES") == [("No devices are known.", "DRAFTING")]
    assert _lines(content, "VISITORS") == [("No arrivals and no departures were logged at the gate.", "PERIOD"),
                                           ("No visitors are on site.", "DRAFTING")]
    assert _lines(content, "MAINTENANCE") == [("No work orders were raised, and none was completed.", "PERIOD"),
                                              ("No orders are in hand.", "DRAFTING")]
    assert _lines(content, "ADVICE") == [("Nothing stands out in the last 4 weeks by the rules that are applied.", "WEEKS")]

    # Opened and not yet touched; patrols that are not over; one of each.
    one = {**quiet, "INCIDENTS": {**quiet["INCIDENTS"], "opened": 1, "by_severity": {**quiet["INCIDENTS"]["by_severity"], "low": 1},
                                  "opened_still_open": 1, "open_now": 1},
           "RESPONSE": {**quiet["RESPONSE"], "opened": 1},
           "PATROLS": {**quiet["PATROLS"], "tours": {**quiet["PATROLS"]["tours"], "scheduled": 2, "open": 2},
                       "virtual": {**quiet["PATROLS"]["virtual"], "scheduled": 1, "done": 1}}}
    said = briefing.draft(one, True, None, [])
    assert _lines(said, "INCIDENTS")[:2] == [("1 incident was opened: 1 low.", "PERIOD"), ("1 of those opened is still open.", "DRAFTING")]
    assert _lines(said, "RESPONSE")[0] == ("Nothing has yet been done with any of the 1 opened.", "DRAFTING")
    assert _lines(said, "PATROLS")[:2] == [("Guard tours: 2 still to be done; none is over yet.", "DRAFTING"),
                                           ("Virtual patrols: 1 done of 1 that is over.", "PERIOD")]
    # Advice that was not read is not a section; nothing standing out is one that says so.
    assert "ADVICE" not in [s["key"] for s in said["sections"]]

    # A part the drafter may not read is not in the draft, and is named with what it wants.
    without = EVERYTHING - {"incident:read", "vpatrol:read"}
    partial = {key: ops_board.blank(key, without) for key in ops_board.SECTIONS if ops_board.NEEDS[key] in without}
    content = briefing.draft(partial, True, None, ops_board.not_read(without))
    assert "INCIDENTS" not in [s["key"] for s in content["sections"]]
    assert [text for text, _ in _lines(content, "PATROLS")] == ["Guard tours: none fell due.", "Drone patrols: none fell due."]
    assert content["not_read"] == [{"key": "INCIDENTS", "title": "Incidents", "needs": "incident:read"},
                                   {"key": "virtual", "title": "Virtual patrols", "needs": "vpatrol:read"}]


def test_no_line_names_anybody_foretells_anything_or_comes_from_a_model():
    busy = {**A_DAY, "INCIDENTS": {**A_DAY["INCIDENTS"], "opened": 40}, "GUARDS": {**A_DAY["GUARDS"], "late": 0, "not_started": 0}}
    quiet = {key: ops_board.blank(key, EVERYTHING) for key in ops_board.SECTIONS}
    for total in (A_DAY, busy, quiet):
        for clocks in (True, False):
            for section in briefing.draft(total, clocks, [], [])["sections"]:
                for line in section["lines"]:
                    assert not [w for w in FORETELLING if w in line["text"].lower()], line["text"]
                    assert line["as_at"] in briefing.WHEN and line["text"].endswith(".")
    code = Path(briefing.__file__).read_text(encoding="utf-8")
    for name in ("sklearn", "numpy", "torch", "anthropic", "openai", "transformers", "sqlalchemy"):
        assert not re.search(rf"^\s*(import|from)\s+{name}\b", code, re.M), name
    body = code.split('"""', 2)[2]
    assert "full_name" not in body and "user" not in body.lower(), "a line counts; it does not say whose"
    # A reader is given what was not left out; whoever manages a draft is given all of it, marked.
    content = briefing.draft(A_DAY, True, ADVICE, [])
    whole = briefing.shown(content, ["VISITORS", "ADVICE"], whole=True)
    assert [s["key"] for s in whole if s["left_out"]] == ["VISITORS", "ADVICE"] and len(whole) == len(briefing.SECTIONS)
    read = briefing.shown(content, ["VISITORS", "ADVICE"], whole=False)
    assert [s["key"] for s in read] == ["INCIDENTS", "RESPONSE", "PATROLS", "GUARDS", "DEVICES", "MAINTENANCE"]
    assert not [s for s in read if s["left_out"]]


# ─── B. Drafting, reviewing, publishing and correcting ───────────────────────

async def _zone(w: dict) -> str:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        zone = await intel_insight.zone_for(db, None)
        await db.rollback()
    return zone


async def _days(w: dict) -> dict:
    """Yesterday where the organisation is, with two incidents and an arrival
    at site A and one incident at site B, all at known times of that day."""
    zone = await _zone(w)
    today = datetime.now(ZoneInfo(zone)).date()
    yesterday = today - timedelta(days=1)
    noon = datetime.combine(yesterday, time(12), tzinfo=ZoneInfo(zone))
    d = {"zone": zone, "today": today, "yesterday": yesterday, "noon": noon,
         "cam": uuid.uuid4(), "far": uuid.uuid4(), "visitor": uuid.uuid4()}
    t = w["tenant"]
    await _run([
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate A')", {"i": d["cam"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Gate B')", {"i": d["far"], "t": t, "s": w["site_b"]}),
        *[("INSERT INTO incidents (tenant_id, camera_id, title, severity, status, created_at, resolved_at) "
           "VALUES (:t,:c,'Forced gate',:sev,:st,:at,:done)",
           {"t": t, "c": d[cam], "sev": severity, "st": status, "at": noon + timedelta(hours=hours),
            "done": noon + timedelta(hours=hours, minutes=30) if status == "resolved" else None})
          for cam, severity, status, hours in (("cam", "critical", "open", 0), ("cam", "high", "resolved", 1), ("far", "low", "open", 0))],
        ("INSERT INTO visitors (id, tenant_id, site_id, full_name, qr_token, status) VALUES (:i,:t,:s,'Mei Lin',:q,'departed')",
         {"i": d["visitor"], "t": t, "s": w["site_a"], "q": uuid.uuid4().hex}),
        ("INSERT INTO visitor_logs (tenant_id, visitor_id, site_id, event_type, occurred_at) VALUES (:t,:v,:s,'arrival',:at)",
         {"t": t, "v": d["visitor"], "s": w["site_a"], "at": noon}),
    ])
    return d


async def test_a_briefing_is_drafted_reviewed_published_and_corrected():
    w = await _world()
    d = await _days(w)
    site, day = str(w["site_a"]), d["yesterday"].isoformat()
    async with _client() as c:
        r = await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site, "briefing_date": day})
        assert r.status_code == 201, r.text
        draft = r.json()
        bid = draft["id"]
        assert (draft["state"], draft["revision"], draft["site"]["name"], draft["briefing_date"]) == ("DRAFT", 1, "Factory A", day)
        assert draft["period"]["timezone"] == d["zone"] and draft["period"]["whole_day"] is True
        assert draft["drafted_by_name"] == "Role 8 User" and draft["published_at"] is None and draft["note"] is None
        assert draft["may"] == {"edit": True, "recount": True, "publish": True, "discard": True, "correct": False}
        assert [s["key"] for s in draft["sections"]] == list(briefing.SECTIONS) and draft["not_read"] == []
        assert not [s for s in draft["sections"] if s["left_out"]] and draft["left_out"] == []
        said = {s["key"]: [line["text"] for line in s["lines"]] for s in draft["sections"]}
        assert said["INCIDENTS"][0] == "2 incidents were opened: 1 critical and 1 high."
        assert "1 incident was resolved." in said["INCIDENTS"]
        # The one that was resolved half an hour after it was opened is the one somebody acted on.
        assert said["RESPONSE"][0] == "Somebody acted on 1 of the 2 opened; on half of them within 30 minutes."
        assert said["VISITORS"][0] == "1 arrival and no departures were logged at the gate."
        assert draft["drafting_note"] == briefing.DRAFTING_NOTE
        # Nobody is named in it.
        assert "Mei Lin" not in r.text and "Role 5 User" not in r.text

        # A draft is read only by whoever manages briefings.
        for role in (OPERATOR, VIEWER):
            assert (await c.get(f"{BASE}/{bid}", headers=w["h"][role])).status_code == 404
            listed = (await c.get(BASE, headers=w["h"][role])).json()
            assert listed["items"] == [] and listed["can_manage"] is False
        mine = (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()
        assert [(b["id"], b["state"]) for b in mine["items"]] == [(bid, "DRAFT")] and mine["can_manage"] is True
        assert "sections" not in mine["items"][0], "the list does not carry the lines"
        # A day has one draft at a time.
        again = await c.post(BASE, headers=w["h"][ADMIN], json={"site_id": site, "briefing_date": day})
        assert again.status_code == 409 and "already has a draft" in again.json()["detail"]

        # The reviewer leaves sections out and writes a note. The counted lines are not theirs to edit.
        url = f"{BASE}/{bid}"
        for body, status, words in (
            ({"left_out": ["WEATHER"]}, 422, "has no section WEATHER"),
            ({"sections": []}, 422, None), ({"content": {"sections": []}}, 422, None), ({"state": "PUBLISHED"}, 422, None),
            ({"note": "x" * 4001}, 422, None),
        ):
            r = await c.patch(url, headers=w["h"][MANAGER], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in r.json()["detail"]
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.patch(url, headers=w["h"][role], json={"note": "Mine"})).status_code == 403
        r = await c.patch(url, headers=w["h"][MANAGER], json={"left_out": ["VISITORS", "GUARDS", "VISITORS"],
                                                              "note": "  Gate 2 is closed for works until Friday.  "})
        assert r.status_code == 200, r.text
        reviewed = r.json()
        assert reviewed["note"] == "Gate 2 is closed for works until Friday."
        assert [s["key"] for s in reviewed["sections"] if s["left_out"]] == ["GUARDS", "VISITORS"]
        assert reviewed["left_out"] == [{"key": "GUARDS", "title": "Guards on shift"}, {"key": "VISITORS", "title": "Visitors"}]
        assert len(reviewed["sections"]) == len(briefing.SECTIONS), "whoever reviews it still sees what they left out"
        # One thing changed leaves the other as it was; an empty note is no note.
        assert (await c.patch(url, headers=w["h"][MANAGER], json={"note": "   "})).json()["note"] is None
        kept = (await c.patch(url, headers=w["h"][MANAGER], json={"note": "Gate 2 is closed for works until Friday."})).json()
        assert [s["key"] for s in kept["sections"] if s["left_out"]] == ["GUARDS", "VISITORS"]

        # Counted again by somebody else: the lines are theirs to count, the review stays.
        recounted = await c.post(f"{url}/recount", headers=w["h"][SUPERVISOR])
        assert recounted.status_code == 200, recounted.text
        assert recounted.json()["drafted_by_name"] == "Role 3 User" and recounted.json()["note"] == kept["note"]
        assert recounted.json()["left_out"] == reviewed["left_out"] and recounted.json()["drafted_at"] > draft["drafted_at"]

        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(f"{url}/publish", headers=w["h"][role])).status_code == 403
        r = await c.post(f"{url}/publish", headers=w["h"][MANAGER])
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["state"], out["published_by_name"], out["replaced_by"]) == ("PUBLISHED", "Role 8 User", None)
        assert out["may"] == {"edit": False, "recount": False, "publish": False, "discard": False, "correct": True}
        # What was published is what everybody reads — the manager too: less what was left out, and saying that it was.
        for role in (MANAGER, OPERATOR, VIEWER):
            seen = (await c.get(url, headers=w["h"][role])).json()
            assert [s["key"] for s in seen["sections"]] == ["INCIDENTS", "RESPONSE", "PATROLS", "DEVICES", "MAINTENANCE", "ADVICE"]
            assert seen["left_out"] == reviewed["left_out"] and seen["note"] == kept["note"]
            assert seen["may"]["correct"] is (role == MANAGER) and "logged at the gate" not in str(seen["sections"])
        listed = (await c.get(BASE, headers=w["h"][VIEWER], params={"site_id": site})).json()["items"]
        assert [(b["id"], b["state"], b["revision"]) for b in listed] == [(bid, "PUBLISHED", 1)]

        # A published briefing is not changed.
        for method, path, body in (("patch", url, {"note": "Later thought"}), ("post", f"{url}/recount", None),
                                   ("post", f"{url}/publish", None), ("post", f"{url}/discard", None)):
            r = await getattr(c, method)(path, headers=w["h"][MANAGER], **({"json": body} if body else {}))
            assert r.status_code == 409 and "not changed" in r.json()["detail"], (path, r.text)
        assert (await c.get(url, headers=w["h"][MANAGER])).json()["note"] == kept["note"]

        # A correction is a new revision for the same day. Until it is published the first one stands.
        r = await c.post(BASE, headers=w["h"][SUPERVISOR], json={"site_id": site, "briefing_date": day})
        assert r.status_code == 201 and (r.json()["revision"], r.json()["state"], r.json()["note"]) == (2, "DRAFT", None)
        second = r.json()["id"]
        assert (await c.get(url, headers=w["h"][OPERATOR])).json()["replaced_by"] is None
        assert (await c.post(f"{BASE}/{second}/publish", headers=w["h"][SUPERVISOR])).status_code == 200
        first = (await c.get(url, headers=w["h"][MANAGER])).json()
        assert first["replaced_by"] == second and first["state"] == "PUBLISHED" and first["may"]["correct"] is False
        assert first["note"] == kept["note"], "the one that was replaced is kept as it was"
        listed = (await c.get(BASE, headers=w["h"][OPERATOR])).json()["items"]
        assert [(b["revision"], b["replaced_by"]) for b in listed] == [(2, None), (1, second)]

        # A draft set aside is kept, is not listed unless asked for, and the day can be drafted again.
        older = (d["yesterday"] - timedelta(days=1)).isoformat()
        aside = (await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site, "briefing_date": older})).json()["id"]
        r = await c.post(f"{BASE}/{aside}/discard", headers=w["h"][MANAGER])
        assert r.status_code == 200 and r.json()["state"] == "DISCARDED" and not any(r.json()["may"].values())
        assert aside not in [b["id"] for b in (await c.get(BASE, headers=w["h"][MANAGER])).json()["items"]]
        asked = (await c.get(BASE, headers=w["h"][MANAGER], params={"state": "DISCARDED"})).json()["items"]
        assert [b["id"] for b in asked] == [aside]
        assert (await c.get(BASE, headers=w["h"][OPERATOR], params={"state": "DISCARDED"})).json()["items"] == []
        assert (await c.get(BASE, headers=w["h"][MANAGER], params={"state": "LOST"})).status_code == 422
        redo = await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site, "briefing_date": older})
        assert redo.status_code == 201 and redo.json()["revision"] == 2, "a draft set aside keeps its number"
        # With every section left out and nothing written there is nothing to publish.
        empty = f"{BASE}/{redo.json()['id']}"
        await c.patch(empty, headers=w["h"][MANAGER], json={"left_out": list(briefing.SECTIONS)})
        r = await c.post(f"{empty}/publish", headers=w["h"][MANAGER])
        assert r.status_code == 422 and "nothing in it to publish" in r.json()["detail"]
        await c.patch(empty, headers=w["h"][MANAGER], json={"note": "Nothing to report from the counts; see the handover."})
        assert (await c.post(f"{empty}/publish", headers=w["h"][MANAGER])).json()["sections"] == []

    entries = {action: await _audit(w, action) for action in ("briefing.draft", "briefing.review", "briefing.recount",
                                                               "briefing.publish", "briefing.discard")}
    assert [len(entries[a]) for a in entries] == [4, 5, 1, 3, 1]
    assert {e["user_id"] for e in entries["briefing.recount"]} == {w["users"][SUPERVISOR]}
    first_published = min(entries["briefing.publish"], key=lambda e: e["detail"]["revision"] * 10 + len(e["detail"]["left_out"]) % 7)
    assert sorted(first_published["detail"]["left_out"]) == ["GUARDS", "VISITORS"] and first_published["detail"]["revision"] == 1
    assert {str(e["resource_id"]) for e in entries["briefing.discard"]} == {aside}
    # Publishing told nobody and did nothing else.
    (rows,) = await _sql("SELECT (SELECT count(*) FROM alerts WHERE tenant_id = :t) AS alerts, "
                         "(SELECT count(*) FROM incidents WHERE tenant_id = :t) AS incidents, "
                         "(SELECT count(*) FROM maintenance_work_orders WHERE tenant_id = :t) AS orders", {"t": w["tenant"]})
    assert (rows["alerts"], rows["incidents"], rows["orders"]) == (0, 3, 0)
    for module in (api, briefing):
        code = Path(module.__file__).read_text(encoding="utf-8").split('"""', 2)[2].lower()
        for word in ("redis", "response_notify", "send_expo_push", "smtp", "webhook"):
            assert word not in code, (module.__name__, word)


# ─── C. Who may draft and read which briefing ────────────────────────────────

async def test_who_may_draft_and_read_which_briefing():
    w, other = await _world(), await _world()
    d = await _days(w)
    day, site_a, site_b = d["yesterday"].isoformat(), str(w["site_a"]), str(w["site_b"])
    async with _client() as c:
        # Every site together: drafted by somebody who is not held to particular sites.
        r = await c.post(BASE, headers=w["h"][SUPERVISOR], json={"briefing_date": day})
        assert r.status_code == 403 and r.json()["detail"] == api.EVERY_SITE
        r = await c.post(BASE, headers=w["h"][ADMIN], json={"briefing_date": day})
        assert r.status_code == 201 and r.json()["site"] is None
        whole = r.json()["id"]
        incidents = next(s for s in r.json()["sections"] if s["key"] == "INCIDENTS")
        assert incidents["lines"][0]["text"] == "3 incidents were opened: 1 critical, 1 high and 1 low."
        assert (await c.post(f"{BASE}/{whole}/publish", headers=w["h"][ADMIN])).status_code == 200
        # The same day at one site is another briefing.
        b = (await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site_b, "briefing_date": day})).json()
        assert b["revision"] == 1 and next(s for s in b["sections"] if s["key"] == "INCIDENTS")["lines"][0]["text"] == (
            "1 incident was opened: 1 low.")
        assert (await c.post(f"{BASE}/{b['id']}/publish", headers=w["h"][MANAGER])).status_code == 200
        a = (await c.post(BASE, headers=w["h"][SUPERVISOR], json={"site_id": site_a, "briefing_date": day})).json()

        # Somebody held to site A sees site A's — not site B's, and not the one for every site together.
        assert (await c.post(BASE, headers=w["h"][SUPERVISOR], json={"site_id": site_b, "briefing_date": day})).status_code == 404
        for hidden in (whole, b["id"]):
            assert (await c.get(f"{BASE}/{hidden}", headers=w["h"][SUPERVISOR])).status_code == 404
            assert (await c.post(f"{BASE}/{hidden}/recount", headers=w["h"][SUPERVISOR])).status_code == 404
        assert [x["id"] for x in (await c.get(BASE, headers=w["h"][SUPERVISOR])).json()["items"]] == [a["id"]]
        # Somebody who is not held sees what is published for every site and for each; the draft is not theirs to read.
        seen = (await c.get(BASE, headers=w["h"][OPERATOR])).json()["items"]
        assert [(x["site"]["name"] if x["site"] else None) for x in seen] == [None, "Factory B"]
        assert {x["id"] for x in (await c.get(BASE, headers=w["h"][MANAGER])).json()["items"]} == {whole, b["id"], a["id"]}
        only_b = (await c.get(BASE, headers=w["h"][VIEWER], params={"site_id": site_b})).json()["items"]
        assert [x["id"] for x in only_b] == [b["id"]]

        # The day: one that has begun, and not long ago.
        for when, words in ((d["today"] + timedelta(days=2), "has not begun"), (d["today"] - timedelta(days=40), "last 31 days")):
            r = await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site_a, "briefing_date": when.isoformat()})
            assert r.status_code == 422 and words in r.json()["detail"], r.text
        for body in ({"site_id": site_a}, {"site_id": site_a, "briefing_date": "yesterday"},
                     {"site_id": site_a, "briefing_date": day, "note": "Mine"}):
            assert (await c.post(BASE, headers=w["h"][MANAGER], json=body)).status_code == 422
        assert (await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": str(uuid.uuid4()), "briefing_date": day})).status_code == 404
        # Today can be drafted: it is counted so far.
        so_far = await c.post(BASE, headers=w["h"][MANAGER], json={"site_id": site_b, "briefing_date": d["today"].isoformat()})
        assert so_far.status_code == 201
        assert datetime.fromisoformat(so_far.json()["period"]["to"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)

        # Who may do which.
        for role in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(BASE, headers=w["h"][role], json={"site_id": site_a, "briefing_date": day})).status_code == 403
        assert (await c.get(BASE, headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(f"{BASE}/{whole}", headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(BASE)).status_code in (401, 403)
        # Another organisation sees none of it.
        assert (await c.get(BASE, headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"{BASE}/{whole}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.post(f"{BASE}/{a['id']}/publish", headers=other["h"][ADMIN])).status_code == 404

    # A briefing is drafted, reviewed and published by a person: not an API key, not a support session.
    for token, words in (
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True), "not by an API key"),
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN,
                      support_session_id=str(uuid.uuid4())), "support session"),
    ):
        app.dependency_overrides[get_token_payload] = lambda token=token: token
        try:
            async with _client() as c:
                older = (d["yesterday"] - timedelta(days=3)).isoformat()
                for method, path, body in (("post", BASE, {"site_id": site_a, "briefing_date": older}),
                                           ("patch", f"{BASE}/{a['id']}", {"note": "From a key"}),
                                           ("post", f"{BASE}/{a['id']}/recount", None),
                                           ("post", f"{BASE}/{a['id']}/publish", None),
                                           ("post", f"{BASE}/{a['id']}/discard", None)):
                    r = await getattr(c, method)(path, **({"json": body} if body else {}))
                    assert r.status_code == 403 and words in r.json()["detail"], (path, r.text)
                # Reading is not a person's act: a key may. (A support session that does not exist is refused before this.)
                if token.via_api_key:
                    assert (await c.get(f"{BASE}/{whole}")).status_code == 200
        finally:
            app.dependency_overrides.pop(get_token_payload, None)
    (state,) = await _sql("SELECT state, note FROM daily_briefings WHERE id = :i", {"i": a["id"]})
    assert (state["state"], state["note"]) == ("DRAFT", None)


# ─── D. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_and_the_database_refuse_of_a_briefing():
    w, other = await _world(), await _world()
    d = await _days(w)
    async with _client() as c:
        body = {"site_id": str(w["site_a"]), "briefing_date": d["yesterday"].isoformat()}
        published = (await c.post(BASE, headers=w["h"][MANAGER], json=body)).json()["id"]
        assert (await c.post(f"{BASE}/{published}/publish", headers=w["h"][MANAGER])).status_code == 200
        draft = (await c.post(BASE, headers=w["h"][MANAGER], json=body)).json()["id"]

    async def as_app(statement: str, params: dict, tenant=None) -> list:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant or w["tenant"])})
            assert not (await db.execute(text(
                "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
            try:
                result = await db.execute(text(statement), params)
                return [dict(r) for r in result.mappings()] if result.returns_rows else []
            finally:
                await db.rollback()

    # The application's role removes nothing, and cannot move a briefing to another day, site or number.
    for statement in ("DELETE FROM daily_briefings WHERE id = :i",
                      "UPDATE daily_briefings SET briefing_date = briefing_date - 1 WHERE id = :i",
                      "UPDATE daily_briefings SET revision = 9 WHERE id = :i",
                      "UPDATE daily_briefings SET site_id = NULL WHERE id = :i",
                      "UPDATE daily_briefings SET period_start = period_start - interval '1 day' WHERE id = :i",
                      "UPDATE daily_briefings SET tenant_id = gen_random_uuid() WHERE id = :i"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await as_app(statement, {"i": draft})
    # A draft's note is the application's to change; a published briefing is nobody's.
    assert await as_app("UPDATE daily_briefings SET note = 'x' WHERE id = :i RETURNING state", {"i": draft}) == [{"state": "DRAFT"}]
    for column, value in (("note", "'Rewritten'"), ("content", "'{}'::jsonb"), ("left_out", "'{INCIDENTS}'"), ("state", "'DRAFT'")):
        with pytest.raises(DBAPIError, match="is not changed"):
            await as_app(f"UPDATE daily_briefings SET {column} = {value} WHERE id = :i", {"i": published})
    # Another organisation's session reads and changes none of it.
    assert await as_app("SELECT id FROM daily_briefings", {}, other["tenant"]) == []
    assert await as_app("UPDATE daily_briefings SET note = 'x' WHERE id = :i RETURNING id", {"i": draft}, other["tenant"]) == []
    assert len(await as_app("SELECT id FROM daily_briefings", {})) == 2

    grants = await _sql("SELECT privilege_type FROM information_schema.role_table_grants "
                        "WHERE grantee = 'svc_app' AND table_name = 'daily_briefings'")
    assert {g["privilege_type"] for g in grants} == {"SELECT", "INSERT"}
    columns = await _sql("SELECT column_name FROM information_schema.column_privileges "
                         "WHERE grantee = 'svc_app' AND table_name = 'daily_briefings' AND privilege_type = 'UPDATE'")
    changes = MIGRATION.split("BRIEFING_CHANGES = (", 1)[1].split(")", 1)[0]
    assert {c["column_name"] for c in columns} == set(re.findall(r'"(\w+)"', changes))
    for held in ("briefing_date", "revision", "site_id", "tenant_id", "period_start", "timezone"):
        assert held not in {c["column_name"] for c in columns}
    (rls,) = await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'daily_briefings'")
    assert rls["relrowsecurity"] and rls["relforcerowsecurity"]

    # What the database itself refuses, whoever asks.
    row = ("INSERT INTO daily_briefings (tenant_id, site_id, briefing_date, revision, state, period_start, period_end, timezone, "
           "content, note, published_at, discarded_at) VALUES (:t,:s,:day,:rev,:state, now() - interval '1 day', {end}, 'UTC', "
           "'{{}}'::jsonb, :note, {published}, {discarded})")
    base = {"t": w["tenant"], "s": w["site_b"], "day": d["yesterday"], "rev": 1, "state": "DRAFT", "note": None}
    for change, sql, constraint in (
        ({"state": "SENT"}, {}, "ck_brief_state"),
        ({"rev": 0}, {}, "ck_brief_revision"),
        ({}, {"end": "now() - interval '2 days'"}, "ck_brief_period"),
        ({"state": "PUBLISHED"}, {}, "ck_brief_published"),
        ({"state": "DISCARDED"}, {}, "ck_brief_discarded"),
        ({"note": "   "}, {}, "ck_brief_note"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            await _sql(row.format(**{"end": "now()", "published": "NULL", "discarded": "NULL", **sql}), {**base, **change})
    ok = row.format(end="now()", published="NULL", discarded="NULL")
    await _sql(ok, base)
    with pytest.raises(IntegrityError, match="uq_brief_revision"):
        await _sql(row.format(end="now()", published="now()", discarded="NULL"), {**base, "state": "PUBLISHED"})
    with pytest.raises(IntegrityError, match="uq_brief_one_draft"):
        await _sql(ok, {**base, "rev": 2})
    # The same holds for the briefing of every site together, where the site is nothing.
    await _sql(ok, {**base, "s": None})
    with pytest.raises(IntegrityError, match="uq_brief_one_draft"):
        await _sql(ok, {**base, "s": None, "rev": 2})
    with pytest.raises(IntegrityError, match="uq_brief_revision"):
        await _sql(row.format(end="now()", published="now()", discarded="NULL"), {**base, "s": None, "state": "PUBLISHED"})


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
            if (path == BASE or path.startswith(BASE + "/")) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.removeprefix(BASE).replace("{briefing_id:uuid}", "{id}") or "/")] = _needs(route)
    read, manage = {"briefing:read"}, {"briefing:read", "briefing:manage"}
    assert served == {("GET", "/"): read, ("POST", "/"): manage, ("GET", "/{id}"): read, ("PATCH", "/{id}"): manage,
                      ("POST", "/{id}/recount"): manage, ("POST", "/{id}/publish"): manage, ("POST", "/{id}/discard"): manage}
    assert not [m for m, _ in served if m in ("DELETE", "PUT")], "a briefing is not removed or replaced"


async def test_who_holds_the_two_permissions_and_what_this_phase_left_alone():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'briefing:%' "
                      "GROUP BY p.code, p.category")
    assert {r["code"]: list(r["roles"]) for r in rows} == {"briefing:read": [2, 3, 4, 6, 8], "briefing:manage": [2, 3, 8]}
    assert {r["category"] for r in rows} == {"operations"}
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == ["daily_briefings"]
    assert set(re.findall(r"ALTER TABLE (\w+)", upgrade)) == {"daily_briefings"}, "no existing table is altered"
    assert not re.search(r"CREATE TABLE security_", upgrade) and "TRUNC" + "ATE" not in upgrade
    assert "CREATE TRIGGER daily_briefing_settled BEFORE UPDATE ON daily_briefings" in upgrade
    assert "OLD.state IN ('PUBLISHED', 'DISCARDED') AND pg_trigger_depth() = 1" in upgrade
    # The router writes its own table and the audit log, and nothing else.
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert set(re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", router)) == {"daily_briefings"}
    assert "DELETE FROM" not in router
