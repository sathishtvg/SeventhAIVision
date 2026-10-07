"""Guard response: what a guard does on a dispatch, and who is suggested for one.

  A — The rules, with nothing running: which step from which state, and how a guard is scored
  B — A response, through the API: sent, accepted, on the way, there, reported
  C — Not coming, called off, sent again
  D — Who may answer, and who may not
  E — Who to send: a suggestion that sends nobody
  F — The desk, and one incident's response
  G — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. The dispatch in every test is the EXISTING endpoint, unchanged.

The claims, each with tests: nothing here sends a guard; only the guard who was
sent answers, as a signed-in person; what they do is written through to the
incident's own status, history and arrival time; a guard who is not coming gives
the incident back and nobody is sent in their place; and a step, once written,
cannot be rewritten.
"""
from __future__ import annotations

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
from app.realtime import redis_listener
from app.routers import incident_responses as api
from app.services import guard_positions as positions
from app.services import incident_response as responses
from app.services import response_notify as notify
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/incident-responses"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0146_incident_responses.py").read_text(encoding="utf-8")
#: Site A of the test world is at 1.3000, 103.8000. A thousandth of a degree of latitude is about 111 m.
LAT, LNG = 1.3000, 103.8000
TABLES = ("incident_responses", "incident_response_steps", "escalation_policies", "incident_escalations")


class FakeRedis:
    """The two things telling people asks of Redis."""

    def __init__(self, tokens: dict | None = None):
        self.published: list[tuple[str, dict]] = []
        self.tokens = tokens or {}

    async def publish(self, channel: str, data: str) -> int:
        import json
        self.published.append((channel, json.loads(data)))
        return 1

    async def smembers(self, key: str) -> set:
        return set(self.tokens.get(key, ()))

    def events(self, kind: str) -> list[dict]:
        return [d["payload"] for _, d in self.published if d["event_type"] == kind]


@pytest.fixture
def told(monkeypatch):
    """The organisation's channel and the phones, both watched. Nothing leaves the machine."""
    redis, pushes = FakeRedis(), []

    async def push(tokens, title, body, data):
        pushes.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})

    monkeypatch.setattr(redis_listener, "_send_expo_push", push)
    had = getattr(app.state, "redis", None)
    app.state.redis = redis
    redis.pushes = pushes
    yield redis
    app.state.redis = had


async def _scene(w: dict, *, site: str = "site_a", tag: str = "A", severity: str = "high", minutes_ago: int = 5,
                 lat: float = LAT, lng: float = LNG, on_shift: bool = True) -> dict:
    """A camera with a position, an open incident at it, and the world's guard clocked in beside it."""
    t, now = w["tenant"], datetime.now(timezone.utc)
    ids = {k: uuid.uuid4() for k in ("camera", "incident")}
    stmts = [
        ("INSERT INTO cameras (id, tenant_id, site_id, name, location, latitude, longitude) "
         "VALUES (:i,:t,:s,:n,'Gate',:a,:o)", {"i": ids["camera"], "t": t, "s": w[site], "n": f"Gate {tag}",
                                              "a": lat, "o": lng}),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, created_at) "
         "VALUES (:i,:t,:c,:n,:sev,'open',:at)", {"i": ids["incident"], "t": t, "c": ids["camera"],
                                                 "n": f"Forced gate {tag}", "sev": severity,
                                                 "at": now - timedelta(minutes=minutes_ago)}),
    ]
    if on_shift:
        stmts.append(_shift(w, w["users"][GUARD], site=site, lat=lat + 0.0009, lng=lng))
    await _run(stmts)
    return {**ids, "now": now, "guard": w["users"][GUARD]}


def _shift(w: dict, guard, *, site: str = "site_a", lat: float = LAT, lng: float = LNG, hours_ago: float = 2) -> tuple:
    now = datetime.now(timezone.utc)
    return ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, "
            "    check_in_lat, check_in_lon) VALUES (:t,:s,:g,:a,:b,:a,:la,:lo)",
            {"t": w["tenant"], "s": w[site], "g": guard, "a": now - timedelta(hours=hours_ago),
             "b": now + timedelta(hours=6), "la": lat, "lo": lng})


async def _guard(w: dict, name: str, *, role: int = GUARD) -> tuple[uuid.UUID, dict]:
    uid = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
               {"i": uid, "t": w["tenant"], "r": role, "e": f"{uid.hex[:10]}@resp.test", "n": name})
    return uid, _auth(uid, w["tenant"], role)


async def _dispatch(c, w: dict, incident, guard, *, who: int = OPERATOR, notes: str | None = None) -> dict:
    """The existing dispatch endpoint, as it has always been."""
    r = await c.post(f"/api/v1/dispatch/incidents/{incident}", headers=w["h"][who],
                     json={"guard_user_id": str(guard), "dispatch_notes": notes})
    assert r.status_code == 200, r.text
    return r.json()


async def _incident(incident) -> dict:
    return dict((await _sql("SELECT status, dispatched_guard_id, dispatched_at, guard_arrived_at, sla_deadline_at, "
                            "severity, assigned_to_user_id FROM incidents WHERE id = :i", {"i": incident}))[0])


async def _steps(incident) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT step, actor_user_id, actor_role, note, latitude, longitude, occurred_at FROM incident_response_steps "
        "WHERE incident_id = :i ORDER BY occurred_at, id", {"i": incident})]


# ─── A. The rules, with nothing running ──────────────────────────────────────

def test_which_step_may_be_taken_from_which_state():
    allowed = {(step, state) for step in responses.MOVES for state in responses.STATES if responses.may(step, state)}
    assert allowed == {
        ("ACCEPTED", "SENT"),
        ("DECLINED", "SENT"), ("DECLINED", "ACCEPTED"),
        ("EN_ROUTE", "SENT"), ("EN_ROUTE", "ACCEPTED"),
        ("ARRIVED", "SENT"), ("ARRIVED", "ACCEPTED"), ("ARRIVED", "EN_ROUTE"),
    }
    assert not responses.may("REPORTED", "SENT") and not responses.may("STOOD_DOWN", "SENT"), "not a guard's move"
    for over in responses.FINAL:
        assert not [step for step in responses.MOVES if responses.may(step, over)], "nothing follows a response that is over"


def _g(**over) -> dict:
    return {"user_id": uuid.uuid4(), "full_name": "A Guard", "available": True, "emergency_id": None,
            "distance_m": 80, "position_age_s": 300, "stale": False, **over}


def test_a_score_is_the_sum_of_parts_it_states():
    best = responses.score(_g(), sent_this_shift=0, holds_what_the_site_requires=True)
    assert {p["factor"]: p["points"] for p in best["parts"]} == {
        "AVAILABILITY": 40, "DISTANCE": 30, "POSITION_AGE": 10, "WORKLOAD": 0, "CERTIFICATION": 10}
    assert best["score"] == 90 == sum(p["points"] for p in best["parts"])
    assert all(p["detail"] for p in best["parts"]), "every part says why"

    def points(guard, factor, **kw):
        kw = {"sent_this_shift": 0, "holds_what_the_site_requires": None, **kw}
        return {p["factor"]: p["points"] for p in responses.score(guard, **kw)["parts"]}.get(factor)

    assert points(_g(available=False), "AVAILABILITY") == 0
    assert points(_g(available=False, emergency_id=uuid.uuid4()), "AVAILABILITY") == -100
    assert [points(_g(distance_m=d), "DISTANCE") for d in (100, 101, 300, 301, 1000, 1001)] == [30, 20, 20, 10, 10, 0]
    assert points(_g(distance_m=None, position_age_s=None), "DISTANCE") == 0
    assert points(_g(distance_m=None, position_age_s=None), "POSITION_AGE") is None, "no position, no age to weigh"
    assert points(_g(position_age_s=1800), "POSITION_AGE") == 0
    assert points(_g(position_age_s=4000, stale=True), "POSITION_AGE") == -10
    assert [points(_g(), "WORKLOAD", sent_this_shift=n) for n in (1, 2, 3, 9)] == [-5, -10, -15, -15]
    assert points(_g(), "CERTIFICATION") is None, "a site that requires nothing scores nobody on it"
    assert points(_g(), "CERTIFICATION", holds_what_the_site_requires=False) == 0


def test_the_ranking_puts_the_higher_score_first_and_the_nearer_first_among_equals():
    near, far, busy, lost = (_g(full_name="Near", distance_m=50), _g(full_name="Far", distance_m=250),
                             _g(full_name="Busy", distance_m=10, available=False),
                             _g(full_name="Lost", distance_m=None, position_age_s=None))
    ranked = responses.rank([lost, busy, far, near], sent={}, certified=None)
    assert [g["full_name"] for g in ranked] == ["Near", "Far", "Lost", "Busy"]
    assert [g["score"] for g in ranked] == sorted((g["score"] for g in ranked), reverse=True)
    # Somebody already sent on three things this shift falls behind somebody a little farther off.
    worked = responses.rank([near, far], sent={str(near["user_id"]): 3}, certified=None)
    assert [g["full_name"] for g in worked] == ["Far", "Near"]
    held = responses.rank([near, far], sent={}, certified={str(far["user_id"]): True})
    assert {g["full_name"]: g["parts"][-1]["points"] for g in held} == {"Near": 0, "Far": 10}
    assert responses.rank([], sent={}, certified=None) == []


# ─── B. A response, through the API ──────────────────────────────────────────

async def test_a_dispatch_becomes_a_response_the_guard_is_told_of_once(told):
    w = await _world()
    s = await _scene(w)
    told.tokens[f"push_tokens:{w['tenant']}:{s['guard']}"] = {"ExponentPushToken[guard]"}
    async with _client() as c:
        sent = await _dispatch(c, w, s["incident"], s["guard"], notes="North gate, forced")
        assert sent["status"] == "in_progress", "the existing dispatch is as it was"
        assert await _sql("SELECT 1 FROM incident_responses WHERE incident_id = :i", {"i": s["incident"]}) == [], \
            "the dispatch itself writes nothing here"
        r = await c.get(f"{BASE}/mine", headers=w["h"][GUARD])
        assert r.status_code == 200, r.text
        (mine,) = r.json()["items"]
        again = (await c.get(f"{BASE}/mine", headers=w["h"][GUARD])).json()["items"]
    assert mine["id"] == str(s["incident"]) and mine["title"] == "Forced gate A" and mine["site_name"] == "Factory A"
    assert mine["dispatch_notes"] == "North gate, forced" and mine["camera_location"] == "Gate"
    assert mine["response"]["state"] == "SENT" and mine["response"]["guard_name"] == f"Role {GUARD} User"
    assert mine["may"] == {"accept": True, "decline": True, "en_route": True, "arrived": True, "report": True,
                           "stand_down": False}
    assert [i["response"]["id"] for i in again] == [mine["response"]["id"]], "looking again makes no second record"
    (step,) = await _steps(s["incident"])
    assert step["step"] == "SENT" and step["actor_user_id"] is None and step["note"] == "North gate, forced"
    assert step["occurred_at"] == (await _incident(s["incident"]))["dispatched_at"], "stamped when it was sent"
    # The guard's own phone looked first, so there is nobody to wake: they are reading it.
    assert told.pushes == [] and told.events(notify.SENT_EVENT) == []


async def test_the_desk_looking_first_wakes_the_guards_phone_once(told):
    w = await _world()
    s = await _scene(w)
    told.tokens[f"push_tokens:{w['tenant']}:{s['guard']}"] = {"ExponentPushToken[guard]"}
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        for _ in range(2):
            assert (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR])).status_code == 200
    (push,) = told.pushes
    assert push["tokens"] == ["ExponentPushToken[guard]"] and push["body"] == "You have been sent to: Forced gate A at Factory A"
    assert push["data"] == {"type": notify.SENT_EVENT, "incident_id": str(s["incident"])}
    (event,) = told.events(notify.SENT_EVENT)
    assert event["guard_user_id"] == str(s["guard"]) and event["title"] == "Forced gate A"


async def test_accepting_says_so_and_moves_nothing_else():
    w = await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        r = await c.post(f"{BASE}/{s['incident']}/accept", headers=w["h"][GUARD], json={"note": "On it"})
        assert r.status_code == 200, r.text
        body = r.json()
        twice = await c.post(f"{BASE}/{s['incident']}/accept", headers=w["h"][GUARD])
    assert body["response"]["state"] == "ACCEPTED" and body["response"]["accepted_at"]
    assert body["may"] == {"accept": False, "decline": True, "en_route": True, "arrived": True, "report": True,
                           "stand_down": False}
    assert [st["step"] for st in body["steps"]] == ["SENT", "ACCEPTED"]
    assert body["steps"][1]["actor_name"] == f"Role {GUARD} User" and body["steps"][1]["note"] == "On it"
    assert body["escalations"] == [], "a guard reads their own response and nothing of who else was told"
    assert (await _incident(s["incident"]))["status"] == "in_progress", "accepting is not setting off"
    assert twice.status_code == 409 and twice.json()["detail"] == {
        "message": "This dispatch has already been answered.", "state": "ACCEPTED"}
    (entry,) = await _audit(w, "response.accept")
    assert entry["detail"]["incident_id"] == str(s["incident"]) and entry["detail"]["from_state"] == "SENT"
    assert entry["detail"]["site_id"] == str(w["site_a"]) and entry["detail"]["actor_role"] == GUARD


async def test_setting_off_and_arriving_are_written_through_to_the_incident():
    w = await _world()
    s = await _scene(w)
    await _sql("INSERT INTO sla_configs (tenant_id, severity, ack_within_seconds, dispatch_within_seconds, "
               "resolve_within_seconds) VALUES (:t,'high',120,300,1800)", {"t": w["tenant"]})
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        r = await c.post(f"{BASE}/{s['incident']}/en-route", headers=w["h"][GUARD],
                         json={"latitude": LAT + 0.0005, "longitude": LNG})
        assert r.status_code == 200 and r.json()["response"]["state"] == "EN_ROUTE", r.text
        assert r.json()["response"]["accepted_at"], "setting off without having said yes is still a yes"
        on_the_way = await _incident(s["incident"])
        r = await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD],
                         json={"note": "At the gate", "latitude": LAT, "longitude": LNG})
        assert r.status_code == 200 and r.json()["response"]["state"] == "ARRIVED", r.text
        arrival = r.json()["clocks"]["ARRIVAL"]
        assert arrival["met_at"] and not arrival["running"] and not arrival["breached"], \
            "arriving stops the arrival clock"
        again = await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD])
        back = await c.post(f"{BASE}/{s['incident']}/decline", headers=w["h"][GUARD], json={"reason": "Changed my mind"})
        # The incident's own timeline, read through the endpoint that has always given it.
        timeline = await c.get(f"/api/v1/incidents/{s['incident']}/timeline", headers=w["h"][OPERATOR])
    there = await _incident(s["incident"])
    assert on_the_way["status"] == "en_route" and on_the_way["guard_arrived_at"] is None
    assert there["status"] == "on_scene" and there["guard_arrived_at"] is not None
    assert there["dispatched_guard_id"] == s["guard"], "arriving does not take the guard off the incident"
    history = await _sql("SELECT from_status, to_status, changed_by_user_id, latitude, notes FROM incident_status_history "
                         "WHERE incident_id = :i ORDER BY changed_at", {"i": s["incident"]})
    assert [(h["from_status"], h["to_status"]) for h in history] == [("in_progress", "en_route"), ("en_route", "on_scene")]
    assert all(h["changed_by_user_id"] == s["guard"] for h in history) and history[1]["notes"] == "At the gate"
    assert history[0]["latitude"] == pytest.approx(LAT + 0.0005)
    assert again.status_code == 409 and again.json()["detail"]["message"] == "Your arrival is already recorded."
    assert back.status_code == 409 and "stood down" in back.json()["detail"]["message"]
    assert timeline.status_code == 200 and "on_scene" in timeline.text
    # The map's reading of where the guard last was is now where they said they arrived.
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        (seen,) = await positions.on_shift(db, datetime.now(timezone.utc), None, site_id=w["site_a"])
    assert seen["position_source"] == "incident status update" and seen["latitude"] == pytest.approx(LAT)
    assert not seen["available"] and seen["busy_incident_id"] == s["incident"]


async def test_arriving_straight_away_is_one_step_and_a_report_changes_nothing():
    w = await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        r = await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD])
        assert r.status_code == 200, r.text
        assert r.json()["response"]["accepted_at"] and r.json()["response"]["en_route_at"] is None
        before = await _incident(s["incident"])
        said = await c.post(f"{BASE}/{s['incident']}/report", headers=w["h"][GUARD],
                            json={"note": "  Padlock cut. Nobody on site.  ", "latitude": LAT, "longitude": LNG})
        assert said.status_code == 200, said.text
        for nothing in ({"note": "   "}, {}, {"note": "x", "latitude": 1.3}):
            assert (await c.post(f"{BASE}/{s['incident']}/report", headers=w["h"][GUARD],
                                 json=nothing)).status_code == 422, nothing
    assert before["status"] == "on_scene" and await _incident(s["incident"]) == before
    assert said.json()["response"]["state"] == "ARRIVED" and said.json()["may"]["report"]
    steps = await _steps(s["incident"])
    assert [st["step"] for st in steps] == ["SENT", "ARRIVED", "REPORTED"]
    assert steps[2]["note"] == "Padlock cut. Nobody on site." and steps[2]["latitude"] == pytest.approx(LAT)
    assert steps[2]["actor_role"] == GUARD
    assert len(await _audit(w, "response.report")) == 1 and len(await _audit(w, "response.arrive")) == 1


# ─── C. Not coming, called off, sent again ───────────────────────────────────

async def test_a_guard_who_cannot_attend_gives_the_incident_back_and_the_desk_is_told(told):
    w = await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        for no_reason in ({}, {"reason": ""}, {"reason": "   "}):
            assert (await c.post(f"{BASE}/{s['incident']}/decline", headers=w["h"][GUARD],
                                 json=no_reason)).status_code == 422
        r = await c.post(f"{BASE}/{s['incident']}/decline", headers=w["h"][GUARD],
                         json={"reason": "Holding a detained person at Gate 2"})
        assert r.status_code == 200, r.text
        after = await c.post(f"{BASE}/{s['incident']}/accept", headers=w["h"][GUARD])
        desk = (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR])).json()
        mine = (await c.get(f"{BASE}/mine", headers=w["h"][GUARD])).json()["items"]
    body = r.json()
    assert body["response"] is None, "there is no sending standing any more"
    assert body["responses"][0]["state"] == "DECLINED"
    assert body["responses"][0]["decline_reason"] == "Holding a detained person at Gate 2"
    assert not any(body["may"].values())
    incident = await _incident(s["incident"])
    assert incident["status"] == "open" and incident["dispatched_guard_id"] is None
    assert incident["dispatched_at"] is None and incident["sla_deadline_at"] is None
    (line,) = await _sql("SELECT from_status, to_status, notes FROM incident_status_history WHERE incident_id = :i",
                         {"i": s["incident"]})
    assert dict(line) == {"from_status": "in_progress", "to_status": "open",
                          "notes": "Guard cannot attend: Holding a detained person at Gate 2"}
    assert after.status_code == 403, "it is not theirs any more"
    assert mine == []
    (row,) = desk["items"]
    assert row["needs"] == "DISPATCH" and row["response"] is None
    assert row["last_response"]["state"] == "DECLINED" and row["last_response"]["guard_name"] == f"Role {GUARD} User"
    (event,) = told.events(notify.DECLINED_EVENT)
    assert event == {"incident_id": str(s["incident"]), "title": "Forced gate A", "severity": "high",
                     "site_id": str(w["site_a"]), "site_name": "Factory A", "guard_name": f"Role {GUARD} User",
                     "reason": "Holding a detained person at Gate 2"}
    # Nobody was sent in their place.
    assert len(await _sql("SELECT 1 FROM incident_responses WHERE incident_id = :i", {"i": s["incident"]})) == 1


async def test_standing_a_guard_down_says_why_frees_the_incident_and_tells_the_guard(told):
    w = await _world()
    s = await _scene(w)
    told.tokens[f"push_tokens:{w['tenant']}:{s['guard']}"] = {"ExponentPushToken[guard]"}
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        await c.post(f"{BASE}/{s['incident']}/en-route", headers=w["h"][GUARD])
        url = f"{BASE}/{s['incident']}/stand-down"
        for no_reason in ({}, {"reason": "  "}):
            assert (await c.post(url, headers=w["h"][OPERATOR], json=no_reason)).status_code == 422
        assert (await c.post(url, headers=w["h"][VIEWER], json={"reason": "x"})).status_code == 403
        assert (await c.post(url, headers=w["h"][GUARD], json={"reason": "x"})).status_code == 403
        told.pushes.clear()
        r = await c.post(url, headers=w["h"][OPERATOR], json={"reason": "False alarm: contractor with a permit"})
        assert r.status_code == 200, r.text
        nobody = await c.post(url, headers=w["h"][OPERATOR], json={"reason": "Again"})
        late = await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD])
    body = r.json()
    (response,) = body["responses"]
    assert body["response"] is None and response["state"] == "STOOD_DOWN"
    assert response["stand_down_reason"] == "False alarm: contractor with a permit"
    assert response["stood_down_by_name"] == f"Role {OPERATOR} User"
    assert [st["step"] for st in body["steps"]] == ["SENT", "EN_ROUTE", "STOOD_DOWN"]
    incident = await _incident(s["incident"])
    assert incident["status"] == "open" and incident["dispatched_guard_id"] is None
    assert nobody.status_code == 409 and nobody.json()["detail"] == "Nobody is sent on this incident."
    assert late.status_code == 403
    (push,) = told.pushes
    assert push["body"] == "Stood down from: Forced gate A (False alarm: contractor with a permit)"
    (entry,) = await _audit(w, "response.stand_down")
    assert entry["detail"]["from_state"] == "EN_ROUTE" and entry["detail"]["guard_user_id"] == str(s["guard"])


async def test_a_guard_who_has_arrived_and_is_stood_down_leaves_the_incident_where_it_was():
    w = await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD])
        r = await c.post(f"{BASE}/{s['incident']}/stand-down", headers=w["h"][ADMIN], json={"reason": "Relieved"})
    assert r.status_code == 200, r.text
    incident = await _incident(s["incident"])
    assert incident["status"] == "on_scene" and incident["dispatched_guard_id"] is None
    assert incident["guard_arrived_at"] is not None, "that somebody got there stays on the record"


async def test_sending_somebody_else_closes_the_first_response_and_opens_another():
    w = await _world()
    s = await _scene(w)
    second, second_h = await _guard(w, "Second Guard")
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        await c.post(f"{BASE}/{s['incident']}/accept", headers=w["h"][GUARD])
        await _dispatch(c, w, s["incident"], second, who=SUPERVISOR)
        detail = (await c.get(f"{BASE}/{s['incident']}", headers=w["h"][OPERATOR])).json()
        first_now = await c.post(f"{BASE}/{s['incident']}/en-route", headers=w["h"][GUARD])
        theirs = (await c.get(f"{BASE}/{s['incident']}", headers=w["h"][GUARD])).json()
        mine = (await c.get(f"{BASE}/mine", headers=second_h)).json()["items"]
    assert [r["state"] for r in detail["responses"]] == ["SENT", "STOOD_DOWN"], "newest first"
    assert detail["response"]["guard_user_id"] == str(second)
    assert detail["responses"][1]["stand_down_reason"] == responses.SUPERSEDED
    assert detail["responses"][1]["stood_down_by_name"] is None, "the platform noticed; nobody stood them down"
    superseded = [st for st in detail["steps"] if st["step"] == "STOOD_DOWN"]
    assert len(superseded) == 1 and superseded[0]["actor_user_id"] is None and superseded[0]["note"] == responses.SUPERSEDED
    assert first_now.status_code == 403
    assert [r["state"] for r in theirs["responses"]] == ["STOOD_DOWN"], "the first guard reads their own and no more"
    assert theirs["response"] is None and not any(theirs["may"].values())
    assert len(mine) == 1 and mine[0]["response"]["state"] == "SENT"


# ─── D. Who may answer, and who may not ──────────────────────────────────────

async def test_only_the_guard_who_was_sent_answers_and_only_as_a_person():
    w, other = await _world(), await _world()
    s = await _scene(w)
    bystander, bystander_h = await _guard(w, "Another Guard")
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        for step, body in (("accept", None), ("en-route", None), ("arrived", None), ("report", {"note": "x"}),
                           ("decline", {"reason": "x"})):
            url = f"{BASE}/{s['incident']}/{step}"
            assert (await c.post(url, headers=bystander_h, json=body)).status_code == 403, step
            assert (await c.post(url, headers=w["h"][ADMIN], json=body)).status_code == 403, f"an admin, {step}"
            assert (await c.post(url, headers=w["h"][VIEWER], json=body)).status_code == 403, f"a viewer, {step}"
            assert (await c.post(url, headers=other["h"][GUARD], json=body)).status_code == 404, f"another tenant, {step}"
            assert (await c.post(url, json=body)).status_code == 401
        assert (await c.post(f"{BASE}/{uuid.uuid4()}/accept", headers=w["h"][GUARD])).status_code == 404
        assert (await c.get(f"{BASE}/{s['incident']}", headers=bystander_h)).status_code == 404, \
            "a guard who was never sent on it reads nothing of it here"
        assert (await c.get(f"{BASE}/mine", headers=w["h"][VIEWER])).status_code == 403
        assert (await c.get(f"{BASE}/mine", headers=bystander_h)).json()["items"] == []
    assert await _steps(s["incident"]) == [], "none of them so much as made the record"

    from fastapi import HTTPException
    for not_a_person, why in (
        (TokenPayload(user_id=str(s["guard"]), tenant_id=str(w["tenant"]), role_id=GUARD, via_api_key=True),
         "not by an API key"),
        (TokenPayload(user_id=str(s["guard"]), tenant_id=str(w["tenant"]), role_id=GUARD,
                      support_session_id=str(uuid.uuid4())), "not from a support session"),
    ):
        with pytest.raises(HTTPException) as refused:
            api._a_person(not_a_person)
        assert refused.value.status_code == 403 and why in refused.value.detail
    key = TokenPayload(user_id=str(s["guard"]), tenant_id=str(w["tenant"]), role_id=GUARD, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(f"{BASE}/{s['incident']}/accept")
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert await _steps(s["incident"]) == []


async def test_nothing_is_answered_on_an_incident_that_is_over():
    w = await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        assert (await c.post(f"/api/v1/incidents/{s['incident']}/resolve", headers=w["h"][ADMIN])).status_code == 200
        r = await c.post(f"{BASE}/{s['incident']}/arrived", headers=w["h"][GUARD])
        down = await c.post(f"{BASE}/{s['incident']}/stand-down", headers=w["h"][ADMIN], json={"reason": "Done"})
        mine = (await c.get(f"{BASE}/mine", headers=w["h"][GUARD])).json()["items"]
        desk = (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR])).json()["items"]
    assert r.status_code == 409 and r.json()["detail"] == "This incident is already resolved."
    assert down.status_code == 409
    assert mine == [] and desk == [], "a resolved incident is on nobody's list"


# ─── E. Who to send: a suggestion that sends nobody ──────────────────────────

async def test_the_suggestion_ranks_whoever_is_on_shift_gives_its_reasons_and_sends_nobody():
    w = await _world()
    s = await _scene(w)                      # the world's guard: clocked in 100 m from the gate, two hours ago
    near, _ = await _guard(w, "Near Guard")
    far, _ = await _guard(w, "Far Guard")
    busy, _ = await _guard(w, "Busy Guard")
    elsewhere, _ = await _guard(w, "Guard At B")
    other = uuid.uuid4()
    await _run([
        _shift(w, near, lat=LAT + 0.0002, hours_ago=0.1),           # ~22 m, six minutes ago
        _shift(w, far, lat=LAT + 0.008, hours_ago=0.1),             # ~890 m
        _shift(w, busy, lat=LAT, hours_ago=0.1),                    # on the spot, but already sent somewhere
        _shift(w, elsewhere, site="site_b", lat=LAT, hours_ago=0.1),
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, dispatched_guard_id, dispatched_at) "
         "VALUES (:i,:t,:c,'Another','low','in_progress',:g, now())",
         {"i": other, "t": w["tenant"], "c": s["camera"], "g": busy}),
    ])
    before = await _incident(s["incident"])
    async with _client() as c:
        r = await c.get(f"{BASE}/recommend", headers=w["h"][OPERATOR], params={"incident_id": str(s["incident"])})
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_decision"] is False and "sends nobody" in body["note"] and "not where" in body["note"]
    assert body["incident"]["title"] == "Forced gate A" and body["located"] is True
    names = [g["full_name"] for g in body["guards"]]
    assert names == ["Near Guard", f"Role {GUARD} User", "Far Guard", "Busy Guard"], "nobody from another site"
    top = body["guards"][0]
    assert top["distance_m"] < 30 and top["position_source"] == "shift check-in" and top["available"]
    assert {p["factor"] for p in top["parts"]} == {"AVAILABILITY", "DISTANCE", "POSITION_AGE", "WORKLOAD"}
    assert top["score"] == sum(p["points"] for p in top["parts"]) == 80
    world_guard = body["guards"][1]
    assert {p["factor"]: p["points"] for p in world_guard["parts"]}["POSITION_AGE"] == -10 and world_guard["stale"]
    last = body["guards"][-1]
    assert not last["available"] and last["busy_incident_id"] == str(other)
    assert "site_requires" not in body
    # It read; it sent nobody and wrote nothing.
    assert await _incident(s["incident"]) == before
    assert await _sql("SELECT 1 FROM incident_responses WHERE incident_id = :i", {"i": s["incident"]}) == []


async def test_the_suggestion_counts_what_a_guard_was_already_sent_on_and_what_the_site_requires():
    w = await _world()
    s = await _scene(w)
    fresh, _ = await _guard(w, "Fresh Guard")
    earlier = uuid.uuid4()
    await _run([
        _shift(w, fresh, lat=LAT + 0.0009, hours_ago=2),
        ("INSERT INTO certification_requirements (tenant_id, site_id, certification_type) VALUES (:t,:s,'First aid')",
         {"t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO certification_requirements (tenant_id, site_id, certification_type, is_active) "
         "VALUES (:t,:s,'Crane', FALSE)", {"t": w["tenant"], "s": w["site_a"]}),
        ("INSERT INTO guard_certifications (tenant_id, user_id, certification_type, expires_at) "
         "VALUES (:t,:u,'First aid', CURRENT_DATE + 30)", {"t": w["tenant"], "u": fresh}),
        ("INSERT INTO guard_certifications (tenant_id, user_id, certification_type, expires_at) "
         "VALUES (:t,:u,'First aid', CURRENT_DATE - 1)", {"t": w["tenant"], "u": s["guard"]}),
        # The world's guard was sent on something an hour ago, since resolved.
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, status, resolved_at) "
         "VALUES (:i,:t,:c,'Earlier','low','resolved', now())", {"i": earlier, "t": w["tenant"], "c": s["camera"]}),
        ("INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at, state, arrived_at) "
         "VALUES (:t,:i,:s,:g, now() - interval '1 hour', 'ARRIVED', now())",
         {"t": w["tenant"], "i": earlier, "s": w["site_a"], "g": s["guard"]}),
    ])
    async with _client() as c:
        body = (await c.get(f"{BASE}/recommend", headers=w["h"][MANAGER],
                            params={"incident_id": str(s["incident"])})).json()
    assert body["site_requires"] == ["First aid"], "a requirement that is switched off is not one"
    by_name = {g["full_name"]: {p["factor"]: p for p in g["parts"]} for g in body["guards"]}
    assert by_name["Fresh Guard"]["CERTIFICATION"]["points"] == 10 and by_name["Fresh Guard"]["WORKLOAD"]["points"] == 0
    held = by_name[f"Role {GUARD} User"]
    assert held["CERTIFICATION"]["points"] == 0, "a certificate that ran out yesterday is not held"
    assert held["WORKLOAD"]["points"] == -5 and "1 incident this shift" in held["WORKLOAD"]["detail"]
    assert [g["full_name"] for g in body["guards"]] == ["Fresh Guard", f"Role {GUARD} User"]


async def test_the_suggestion_says_why_there_is_nobody_and_is_for_whoever_may_dispatch():
    w, other = await _world(), await _world()
    empty = await _scene(w, on_shift=False)
    at_b = await _scene(w, site="site_b", tag="B", on_shift=False)
    siteless = uuid.uuid4()
    await _sql("INSERT INTO incidents (id, tenant_id, title, severity, status) VALUES (:i,:t,'Phoned in','low','open')",
               {"i": siteless, "t": w["tenant"]})
    async with _client() as c:
        ask = lambda who, incident: c.get(f"{BASE}/recommend", headers=who, params={"incident_id": str(incident)})  # noqa: E731
        nobody = (await ask(w["h"][OPERATOR], empty["incident"])).json()
        nowhere = (await ask(w["h"][ADMIN], siteless)).json()
        assert (await ask(w["h"][VIEWER], empty["incident"])).status_code == 403, "reading responses is not dispatching"
        assert (await ask(w["h"][GUARD], empty["incident"])).status_code == 403
        assert (await ask(w["h"][SUPERVISOR], empty["incident"])).status_code == 200
        assert (await ask(w["h"][SUPERVISOR], at_b["incident"])).status_code == 404, "held to site A"
        assert (await ask(w["h"][SUPERVISOR], siteless)).status_code == 404
        assert (await ask(other["h"][ADMIN], empty["incident"])).status_code == 404
        assert (await c.get(f"{BASE}/recommend", headers=w["h"][ADMIN])).status_code == 422
    assert nobody["guards"] == [] and nobody["why_nobody"] == "Nobody is clocked in at this site."
    assert nowhere["guards"] == [] and "no site" in nowhere["why_nobody"] and nowhere["located"] is False


# ─── F. The desk, and one incident's response ────────────────────────────────

async def test_the_desk_shows_what_each_open_incident_is_waiting_for():
    w, other = await _world(), await _world()
    waiting = await _scene(w, tag="Waiting", severity="low")
    sent = await _scene(w, tag="Sent", severity="critical", on_shift=False)
    moving = await _scene(w, tag="Moving", severity="medium", on_shift=False)
    there = await _scene(w, tag="There", severity="high", on_shift=False)
    old = await _scene(w, tag="Old", severity="info", minutes_ago=60 * 30, on_shift=False)
    at_b = await _scene(w, site="site_b", tag="B", on_shift=False)
    g2, g2_h = await _guard(w, "Guard Two")
    g3, g3_h = await _guard(w, "Guard Three")
    async with _client() as c:
        await _dispatch(c, w, sent["incident"], waiting["guard"])
        await _dispatch(c, w, moving["incident"], g2)
        await c.post(f"{BASE}/{moving['incident']}/en-route", headers=g2_h)
        await _dispatch(c, w, there["incident"], g3)
        await c.post(f"{BASE}/{there['incident']}/arrived", headers=g3_h)
        r = await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR])
        assert r.status_code == 200, r.text
        desk = r.json()
        views = {v: [i["title"] for i in (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR],
                                                      params={"view": v})).json()["items"]]
                 for v in ("waiting", "sent", "late")}
        week = (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR], params={"hours": 168})).json()
        supervisor = (await c.get(f"{BASE}/desk", headers=w["h"][SUPERVISOR])).json()
        only_b = (await c.get(f"{BASE}/desk", headers=w["h"][ADMIN], params={"site_id": str(w["site_b"])})).json()
        assert (await c.get(f"{BASE}/desk", headers=w["h"][SUPERVISOR],
                            params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/desk", headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(f"{BASE}/desk", headers=w["h"][VIEWER])).status_code == 200
        assert (await c.get(f"{BASE}/desk", headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"{BASE}/desk", headers=w["h"][ADMIN], params={"view": "everything"})).status_code == 422
    needs = {i["title"]: i["needs"] for i in desk["items"]}
    assert needs == {"Forced gate Sent": "ANSWER", "Forced gate There": "RESOLVE", "Forced gate B": "DISPATCH",
                     "Forced gate Moving": "ARRIVAL", "Forced gate Waiting": "DISPATCH"}
    assert [i["severity"] for i in desk["items"]] == ["critical", "high", "high", "medium", "low"], "the gravest first"
    assert desk["counts"] == {"waiting": 2, "unanswered": 1, "on_the_way": 1, "late": 0}
    assert desk["sla_enabled"] is False and desk["can_dispatch"] is True and desk["can_manage"] is False
    assert all(i["judged"] is False for i in desk["items"]), "the clocks are off: nothing is judged"
    by_title = {i["title"]: i for i in desk["items"]}
    assert by_title["Forced gate Sent"]["response"]["state"] == "SENT"
    assert by_title["Forced gate Sent"]["may"]["stand_down"] and not by_title["Forced gate Sent"]["may"]["accept"]
    assert by_title["Forced gate Waiting"]["response"] is None and not by_title["Forced gate Waiting"]["may"]["stand_down"]
    assert views == {"waiting": ["Forced gate B", "Forced gate Waiting"],
                     "sent": ["Forced gate Sent", "Forced gate There", "Forced gate Moving"], "late": []}
    assert "Forced gate Old" in [i["title"] for i in week["items"]] and "Forced gate Old" not in needs
    assert "Forced gate B" not in [i["title"] for i in supervisor["items"]] and len(supervisor["items"]) == 4
    assert [i["title"] for i in only_b["items"]] == ["Forced gate B"]
    assert old and at_b


async def test_one_incidents_response_is_read_by_whoever_may_and_a_guard_reads_their_own():
    w, other = await _world(), await _world()
    s = await _scene(w)
    at_b = await _scene(w, site="site_b", tag="B", on_shift=False)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"], notes="Take the east path")
        await _dispatch(c, w, at_b["incident"], s["guard"])
        read = lambda who, incident: c.get(f"{BASE}/{incident}", headers=who)  # noqa: E731
        viewer = await read(w["h"][VIEWER], s["incident"])
        assert viewer.status_code == 200, viewer.text
        guard = (await read(w["h"][GUARD], s["incident"])).json()
        assert (await read(w["h"][SUPERVISOR], at_b["incident"])).status_code == 404, "held to site A"
        assert (await read(w["h"][SUPERVISOR], s["incident"])).status_code == 200
        assert (await read(other["h"][ADMIN], s["incident"])).status_code == 404
        assert (await read(w["h"][ADMIN], uuid.uuid4())).status_code == 404
        assert (await c.get(f"{BASE}/not-a-uuid", headers=w["h"][ADMIN])).status_code in (404, 422)
    body = viewer.json()
    assert body["incident"]["dispatch_notes"] == "Take the east path" and body["incident"]["site_name"] == "Factory A"
    assert body["response"]["state"] == "SENT" and [st["step"] for st in body["steps"]] == ["SENT"]
    assert set(body["clocks"]) == {"ACKNOWLEDGE", "ARRIVAL", "RESOLVE"} and body["sla_enabled"] is False
    assert not any(body["may"].values()), "a viewer reads and does nothing"
    assert guard["may"]["accept"] and guard["may"]["decline"] and not guard["may"]["stand_down"]


# ─── G. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_cannot_do_to_a_response():
    w, other = await _world(), await _world()
    s = await _scene(w)
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        await c.post(f"{BASE}/{s['incident']}/accept", headers=w["h"][GUARD])
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        for statement in (
            "UPDATE incident_response_steps SET note = 'rewritten' WHERE incident_id = :i",
            "DELETE FROM incident_response_steps WHERE incident_id = :i",
            "DELETE FROM incident_responses WHERE incident_id = :i",
            "UPDATE incident_responses SET guard_user_id = gen_random_uuid() WHERE incident_id = :i",
            "UPDATE incident_responses SET dispatched_at = now() WHERE incident_id = :i",
            "UPDATE incident_responses SET incident_id = gen_random_uuid() WHERE incident_id = :i",
            "UPDATE incident_escalations SET recipients = 9 WHERE incident_id = :i",
            "DELETE FROM incident_escalations WHERE incident_id = :i",
            "DELETE FROM escalation_policies WHERE tenant_id = current_setting('app.current_tenant')::uuid",
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement), {"i": s["incident"]} if ":i" in statement else {})
            await db.rollback()
        # Another organisation sees none of it and can plant nothing in it.
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at) "
                                  "VALUES (:t, :i, now())"), {"t": w["tenant"], "i": s["incident"]})
        await db.rollback()
    for table in TABLES:
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n", {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        can = (await _sql("SELECT has_table_privilege('svc_app', :n, 'SELECT') AS s, "
                          "has_table_privilege('svc_app', :n, 'INSERT') AS i, "
                          "has_table_privilege('svc_app', :n, 'DELETE') AS d, "
                          "has_table_privilege('svc_app', :n, 'UPDATE') AS u", {"n": table}))[0]
        assert can["s"] and can["i"] and not can["d"], table
        assert can["u"] == (table == "escalation_policies"), table
    assert "GRANT ALL" not in MIGRATION and MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1


async def test_what_the_database_refuses_of_a_response():
    w = await _world()
    s = await _scene(w)
    t, i = w["tenant"], s["incident"]
    at = datetime.now(timezone.utc)
    (made,) = await _sql("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at) VALUES (:t,:i,:at) "
                         "RETURNING id", {"t": t, "i": i, "at": at})
    step = ("INSERT INTO incident_response_steps (tenant_id, response_id, incident_id, step{more}) "
            "VALUES (:t, :r, :i, :step{values})")
    for statement, params, constraint in (
        ("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at) VALUES (:t,:i,:at)",
         {"at": at}, "uq_response_sending"),
        ("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at, state) VALUES (:t,:i,now(),'LOST')",
         {}, "ck_response_state"),
        ("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at, state, declined_at) "
         "VALUES (:t,:i,now(),'DECLINED', now())", {}, "ck_response_declined"),
        ("INSERT INTO incident_responses (tenant_id, incident_id, dispatched_at, state, stood_down_at, stand_down_reason) "
         "VALUES (:t,:i,now(),'STOOD_DOWN', now(), '  ')", {}, "ck_response_stood_down"),
        (step.format(more="", values=""), {"step": "WANDERED"}, "ck_respstep_step"),
        (step.format(more="", values=""), {"step": "REPORTED"}, "ck_respstep_report"),
        (step.format(more=", latitude", values=", 1.3"), {"step": "ARRIVED"}, "ck_respstep_point"),
        (step.format(more=", latitude, longitude", values=", 95, 103.8"), {"step": "ARRIVED"}, "ck_respstep_point"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(statement, {"t": t, "i": i, "r": made["id"], **params})
    policy = ("INSERT INTO escalation_policies (tenant_id, name, trigger, after_seconds, notify_role_id{more}) "
              "VALUES (:t, :n, :trigger, :after, :role{values})")
    for name, trigger, after, role, more, values, constraint in (
        (" ", "NOT_ARRIVED", 60, 3, "", "", "ck_escpol_name"),
        ("P", "NOT_HAPPY", 60, 3, "", "", "ck_escpol_trigger"),
        ("P", "NOT_ARRIVED", 10, 3, "", "", "ck_escpol_after"),
        ("P", "NOT_ARRIVED", 700000, 3, "", "", "ck_escpol_after"),
        ("P", "NOT_ARRIVED", 60, 1, "", "", "ck_escpol_role"),
        ("P", "NOT_ARRIVED", 60, 7, "", "", "ck_escpol_role"),
        ("P", "NOT_ARRIVED", 60, None, "", "", "ck_escpol_whom"),
        ("P", "NOT_ARRIVED", 60, 3, ", severity", ", 'dire'", "ck_escpol_severity"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(policy.format(more=more, values=values),
                       {"t": t, "n": name, "trigger": trigger, "after": after, "role": role})
    # A retired policy may be addressed to nobody: the person it named has left.
    await _sql(policy.format(more=", is_active", values=", FALSE"),
               {"t": t, "n": "Retired", "trigger": "NOT_ARRIVED", "after": 60, "role": None})
    told = ("INSERT INTO incident_escalations (tenant_id, incident_id, kind, clock, due_at{more}) "
            "VALUES (:t, :i, :kind, :clock, now(){values})")
    for kind, clock, more, values, constraint in (
        ("WHIM", "ARRIVAL", "", "", "ck_incesc_kind"),
        ("SLA_BREACH", "LUNCH", "", "", "ck_incesc_clock"),
        ("POLICY_STEP", "ARRIVAL", "", "", "ck_incesc_policy"),
        ("SLA_BREACH", "ARRIVAL", ", recipients", ", -1", "ck_incesc_recipients"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(told.format(more=more, values=values), {"t": t, "i": i, "kind": kind, "clock": clock})
    await _sql(told.format(more="", values=""), {"t": t, "i": i, "kind": "SLA_BREACH", "clock": "RESOLVE"})
    with pytest.raises(DBAPIError, match="uq_incesc_breach"):
        await _sql(told.format(more="", values=""), {"t": t, "i": i, "kind": "SLA_BREACH", "clock": "RESOLVE"})


async def test_removing_an_incident_or_a_person_does_not_strand_a_response():
    w = await _world()
    s = await _scene(w)
    gone, gone_h = await _guard(w, "Leaving Guard")
    async with _client() as c:
        await _dispatch(c, w, s["incident"], gone)
        await c.post(f"{BASE}/{s['incident']}/accept", headers=gone_h)
    # The housekeeping that removes a person keeps what they did, without their name on it.
    await _run([("DELETE FROM audit_logs WHERE user_id = :u", {"u": gone}),
                ("UPDATE incidents SET dispatched_guard_id = NULL WHERE id = :i", {"i": s["incident"]}),
                ("DELETE FROM users WHERE id = :u", {"u": gone})])
    (response,) = await _sql("SELECT guard_user_id, state FROM incident_responses WHERE incident_id = :i",
                             {"i": s["incident"]})
    assert response["guard_user_id"] is None and response["state"] == "ACCEPTED"
    assert [st["actor_user_id"] for st in await _steps(s["incident"])] == [None, None]
    await _sql("DELETE FROM incidents WHERE id = :i", {"i": s["incident"]})
    for table in ("incident_responses", "incident_response_steps"):
        assert await _sql(f"SELECT 1 FROM {table} WHERE incident_id = :i", {"i": s["incident"]}) == [], table


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
            if path.startswith(BASE) and getattr(route, "endpoint", None):
                for method in route.methods - {"HEAD"}:
                    served[(method, path.replace(":uuid", "").removeprefix(BASE))] = _needs(route)
    read, act, send, manage = "response:read", "response:act", "incident:dispatch", "sla:manage"
    assert served == {
        ("GET", "/desk"): {read}, ("GET", "/recommend"): {read, send}, ("GET", "/mine"): {act},
        ("GET", "/settings"): {read}, ("PUT", "/settings"): {read, manage},
        ("GET", "/policies"): {read}, ("POST", "/policies"): {read, manage},
        ("PATCH", "/policies/{policy_id}"): {read, manage},
        ("POST", "/policies/{policy_id}/retire"): {read, manage},
        ("POST", "/policies/{policy_id}/restore"): {read, manage},
        ("GET", "/escalations"): {read},
        # Read by whoever may read responses, or by the guard who was sent: decided inside.
        ("GET", "/{incident_id}"): set(),
        ("POST", "/{incident_id}/accept"): {act}, ("POST", "/{incident_id}/decline"): {act},
        ("POST", "/{incident_id}/en-route"): {act}, ("POST", "/{incident_id}/arrived"): {act},
        ("POST", "/{incident_id}/report"): {act},
        ("POST", "/{incident_id}/stand-down"): {read, send},
    }
    assert not [m for m, _ in served if m == "DELETE"], "nothing here is removed"
    assert not [p for _, p in served if "dispatch" in p], "the dispatch is the existing endpoint's, not this router's"


async def test_who_holds_the_two_permissions():
    rows = await _sql("SELECT p.code, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'response:%' "
                      "GROUP BY p.code")
    assert {r["code"]: list(r["roles"]) for r in rows} == {
        "response:read": [2, 3, 4, 6, 8], "response:act": [2, 3, 4, 5, 8]}
