"""Visitor and contractor authorisation: asked for, answered by the host, read at the gate — and where a badge was used.

  A — How one stands and what the gate reads, with nothing running
  B — Asking
  C — The answer: the host's, or that of somebody who manages visits
  D — Cancelling and extending
  E — The places, the escort and the ID
  F — The gate, the lists, and who reads what
  G — Where a visitor's badge was used
  H — What the application role and the database refuse

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. Visitors are checked in and out through the EXISTING endpoints,
unchanged.

The claims, each with tests: an authorisation admits nobody and refuses nobody;
the answer is a named person's; a visitor's ID number is never taken or given;
a door event outside what a visit is authorised for is listed for a person to
look at and raises nothing; what is not known is said to be not known.
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
from app.routers import visitor_authorizations as api
from app.services import visitor_authorization as authorisation
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_incident_responses import FakeRedis, _guard, told  # noqa: F401
from tests.test_investigation_search import _audit

BASE = "/api/v1/visitor-authorizations"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = (VERSIONS / "0149_visitor_authorizations.py").read_text(encoding="utf-8")
TABLES = ("visitor_authorizations", "visitor_authorization_places", "visitor_movement_reviews")
CLIENT = 7
ID_NUMBER = "S7654321Z"
#: Words for a person that only a person may choose.
ACCUSING = ("unauthor", "intruder", "trespass", "suspect", "violat", "breach", "offender")


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _at(text_: str) -> datetime:
    return datetime.fromisoformat(text_.replace("Z", "+00:00"))


async def _visit(w: dict, name: str = "Lim Mei Ling", *, site: str | None = "site_a", host=None, company: str = "Acme Lifts",
                 expected: bool = True, status: str = "pending") -> uuid.UUID:
    """A visit, as the existing registration leaves it — ID number and all."""
    vid, now = uuid.uuid4(), datetime.now(timezone.utc)
    await _sql("INSERT INTO visitors (id, tenant_id, site_id, full_name, company, id_number, host_user_id, purpose, "
               "expected_from, expected_until, qr_token, status) VALUES (:i,:t,:s,:n,:c,:idn,:h,'Lift servicing',:a,:b,:q,:st)",
               {"i": vid, "t": w["tenant"], "s": w[site] if site else None, "n": name, "c": company, "idn": ID_NUMBER,
                "h": host, "a": now - timedelta(minutes=30) if expected else None,
                "b": now + timedelta(hours=4) if expected else None, "q": uuid.uuid4().hex, "st": status})
    return vid


async def _permit(w: dict, *, site: str = "site_a", status: str = "approved", company: str = "Coolair Services") -> uuid.UUID:
    cid, pid, now = uuid.uuid4(), uuid.uuid4(), datetime.now(timezone.utc)
    await _run([
        ("INSERT INTO contractors (id, tenant_id, company_name) VALUES (:i,:t,:n)", {"i": cid, "t": w["tenant"], "n": company}),
        ("INSERT INTO work_permits (id, tenant_id, contractor_id, site_id, permit_number, work_description, start_at, "
         "end_at, status, workers_count) VALUES (:i,:t,:c,:s,'WP-0042','Chiller overhaul',:a,:b,:st,3)",
         {"i": pid, "t": w["tenant"], "c": cid, "s": w[site], "a": now - timedelta(hours=1),
          "b": now + timedelta(days=2), "st": status}),
    ])
    return pid


async def _place(w: dict, name: str, kind: str = "BUILDING", *, site: str = "site_a", parent=None, door=None,
                 active: bool = True) -> uuid.UUID:
    pid = uuid.uuid4()
    await _sql("INSERT INTO site_places (id, tenant_id, site_id, kind, name, parent_id, latitude, longitude, door_id, "
               "is_active) VALUES (:i,:t,:s,:k,:n,:p,1.3,103.8,:d,:on)",
               {"i": pid, "t": w["tenant"], "s": w[site], "k": kind, "n": name, "p": parent, "d": door, "on": active})
    return pid


async def _door(w: dict, name: str, *, site: str = "site_a") -> uuid.UUID:
    did = uuid.uuid4()
    await _sql("INSERT INTO access_doors (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
               {"i": did, "t": w["tenant"], "s": w[site], "n": name})
    return did


async def _card(w: dict, ref: str) -> uuid.UUID:
    cid = uuid.uuid4()
    await _sql("INSERT INTO access_credentials (id, tenant_id, credential_ref, holder_name) VALUES (:i,:t,:r,'Visitor badge')",
               {"i": cid, "t": w["tenant"], "r": ref})
    return cid


async def _swipe(w: dict, door, card, minutes_ago: float, *, kind: str = "granted") -> uuid.UUID:
    eid = uuid.uuid4()
    await _sql("INSERT INTO access_events (id, tenant_id, door_id, credential_id, event_type, occurred_at) "
               "VALUES (:i,:t,:d,:c,:k, now() - make_interval(secs => :s))",
               {"i": eid, "t": w["tenant"], "d": door, "c": card, "k": kind, "s": minutes_ago * 60})
    return eid


async def _ask(c, w: dict, visit=None, *, who: int = GUARD, headers: dict | None = None, **body):
    subject = {"visitor_id": str(visit)} if visit is not None else {}
    return await c.post(BASE, headers=headers or w["h"][who], json={**subject, **body})


async def _asked(c, w: dict, visit=None, **kw) -> dict:
    r = await _ask(c, w, visit, **kw)
    assert r.status_code == 201, r.text
    return r.json()


async def _approved(c, w: dict, visit=None, *, by: int = MANAGER, **kw) -> dict:
    made = await _asked(c, w, visit, **kw)
    r = await c.post(f"{BASE}/{made['id']}/approve", headers=w["h"][by])
    assert r.status_code == 200, r.text
    return r.json()


async def _period(authorization_id, from_minutes_ago: float, until_minutes_ago: float) -> None:
    """Move the period an authorisation is for. A negative number is ahead of now."""
    await _sql("UPDATE visitor_authorizations SET valid_from = now() - make_interval(secs => :a), "
               "valid_until = now() - make_interval(secs => :b) WHERE id = :i",
               {"a": from_minutes_ago * 60, "b": until_minutes_ago * 60, "i": authorization_id})


async def _check_in(c, w: dict, visit, badge: str | None, *, minutes_ago: float = 0) -> None:
    """Through the endpoint that has always checked visitors in."""
    r = await c.post(f"/api/v1/visitors/{visit}/checkin", headers=w["h"][GUARD], json={"badge_number": badge})
    assert r.status_code == 200, r.text
    if minutes_ago:
        await _sql("UPDATE visitor_logs SET occurred_at = now() - make_interval(secs => :s) WHERE id = :i",
                   {"s": minutes_ago * 60, "i": r.json()["id"]})


async def _raised(w: dict) -> int:
    (row,) = await _sql("SELECT (SELECT count(*) FROM alerts WHERE tenant_id = :t) + "
                        "(SELECT count(*) FROM incidents WHERE tenant_id = :t) AS n", {"t": w["tenant"]})
    return row["n"]


# ─── A. How one stands, and what the gate reads ──────────────────────────────

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)  # noon in Singapore


def _a(**over) -> dict:
    base = {"state": "REQUESTED", "valid_from": NOW - timedelta(hours=1), "valid_until": NOW + timedelta(hours=5),
            "requested_at": NOW - timedelta(minutes=30), "host_name": "Tan Wei Ming", "decided_by_name": None,
            "decision_note": None, "cancelled_by_name": None, "cancel_reason": None, "escort_required": False,
            "escort_name": None, "escort_note": None, "id_document_kind": None, "id_checked_by_name": None}
    return {**base, **over}


def test_where_an_authorisation_stands_is_its_answer_and_the_hour():
    hour = timedelta(hours=1)
    assert authorisation.standing(None, NOW) == "NOT_ASKED"
    assert authorisation.standing(_a(), NOW) == "AWAITING_HOST"
    assert authorisation.standing(_a(valid_until=NOW - hour, valid_from=NOW - 3 * hour), NOW) == "LAPSED"
    yes = _a(state="APPROVED", decided_by_name="Siti Rahman")
    assert authorisation.standing(yes, NOW) == "VALID"
    assert authorisation.standing({**yes, "valid_from": NOW + hour}, NOW) == "NOT_YET_VALID"
    assert authorisation.standing({**yes, "valid_from": NOW - 3 * hour, "valid_until": NOW - hour}, NOW) == "EXPIRED"
    # The first and the last moment of the period are inside it.
    assert authorisation.standing({**yes, "valid_from": NOW}, NOW) == "VALID"
    assert authorisation.standing({**yes, "valid_until": NOW}, NOW) == "VALID"
    # A no, and a withdrawal, stay what they are whatever the hour.
    for state in ("DECLINED", "CANCELLED"):
        for until in (NOW + hour, NOW - hour):
            assert authorisation.standing(_a(state=state, valid_until=until, valid_from=NOW - 3 * hour), NOW) == state
    assert set(authorisation.STANDINGS) == {"NOT_ASKED", "AWAITING_HOST", "LAPSED", "DECLINED", "CANCELLED",
                                           "NOT_YET_VALID", "VALID", "EXPIRED"}


def test_the_gate_reads_what_is_on_the_record_in_plain_sentences():
    sg = {"timezone": "Asia/Singapore"}
    assert authorisation.says(None, NOW) == ["No authorisation has been asked for."]
    assert authorisation.says(_a(), NOW, **sg) == [
        "Asked of Tan Wei Ming at 11:30. Not yet answered.", "No escort is asked for.",
        "Nobody has recorded seeing an ID.", "No particular places are named: the site in general."]
    assert authorisation.says(_a(host_name=None), NOW, **sg)[0] == "Asked of whoever manages visits at 11:30. Not yet answered."
    assert authorisation.says(_a(), NOW, places=["Block A"], **sg)[-1] == "Asked for: Block A."
    lapsed = _a(valid_from=NOW - timedelta(hours=3), valid_until=NOW - timedelta(hours=1))
    assert authorisation.says(lapsed, NOW, **sg)[0] == ("Asked of Tan Wei Ming and never answered. The time it was "
                                                        "asked for has passed.")
    yes = _a(state="APPROVED", decided_by_name="Siti Rahman", escort_required=True, escort_name="Kumar Raj",
             escort_note="Stay with the engineer", id_document_kind="Work pass", id_checked_by_name="Ong Bee Lian")
    assert authorisation.says(yes, NOW, places=["Block A", "Plant room"], **sg) == [
        "Approved by Siti Rahman. Valid until 17:00.", "To be escorted by Kumar Raj (Stay with the engineer).",
        "ID seen: Work pass, by Ong Bee Lian.", "Authorised for: Block A, Plant room."]
    # Another day is named; the same day is only an hour.
    later = {**yes, "valid_until": NOW + timedelta(days=2, hours=5)}
    assert authorisation.says(later, NOW, **sg)[0] == "Approved by Siti Rahman. Valid until 9 Oct 17:00."
    assert authorisation.says({**yes, "valid_from": NOW + timedelta(hours=2)}, NOW, **sg)[0] == (
        "Approved by Siti Rahman. Valid from 14:00.")
    assert authorisation.says({**yes, "valid_from": NOW - timedelta(hours=5), "valid_until": NOW - timedelta(hours=1)},
                              NOW, **sg)[0] == "Approved by Siti Rahman. It ran out at 11:00."
    assert authorisation.says({**yes, "escort_name": None, "escort_note": None}, NOW, **sg)[1] == (
        "To be escorted: nobody is named yet.")
    # A no and a withdrawal say who and why, and nothing about an escort that will not be needed.
    assert authorisation.says(_a(state="DECLINED", decided_by_name="Siti Rahman", decision_note="Not expected today."),
                              NOW, **sg) == ["Declined by Siti Rahman: Not expected today."]
    assert authorisation.says(_a(state="CANCELLED", cancelled_by_name="Siti Rahman", cancel_reason="Visit moved."),
                              NOW, **sg) == ["Cancelled by Siti Rahman: Visit moved."]
    # Somebody who has left is not named, and is not left out either.
    assert authorisation.says({**yes, "decided_by_name": None}, NOW, **sg)[0].startswith(
        "Approved by somebody no longer on the system.")


def test_nothing_the_module_says_uses_a_word_for_the_visitor_that_a_person_has_not_used():
    source = Path(authorisation.__file__).read_text(encoding="utf-8").split('"""', 2)[2].lower()
    router = Path(api.__file__).read_text(encoding="utf-8").split('"""', 2)[2].lower()
    for word in ACCUSING:
        assert word not in source and word not in router, word
    assert "not a finding" in authorisation.MOVEMENT_NOTE and "does not refuse anybody" in authorisation.GATE_NOTE


def test_a_place_is_within_what_is_authorised_when_it_or_anything_it_is_part_of_is():
    parents = {"door": "floor", "floor": "block_a", "block_a": None, "lobby": "block_b", "block_b": None,
               "x": "y", "y": "x"}
    assert authorisation.within("door", {"block_a"}, parents) is True, "a door on a floor of a building that is authorised"
    assert authorisation.within("door", {"floor"}, parents) is True
    assert authorisation.within("door", {"door"}, parents) is True
    assert authorisation.within("lobby", {"block_a"}, parents) is False
    assert authorisation.within("block_a", {"door"}, parents) is False, "a building is not within one of its doors"
    # What cannot be said is not guessed.
    assert authorisation.within("door", set(), parents) is None, "the visit names no places"
    assert authorisation.within(None, {"block_a"}, parents) is None, "the door is not a place on the map"
    assert authorisation.within("x", {"block_a"}, parents) is False, "a loop in the map ends"
    assert authorisation.within("unknown", {"block_a"}, parents) is False


async def test_the_list_judges_a_standing_as_the_module_does():
    w = await _world()
    t, s, now = w["tenant"], w["site_a"], datetime.now(timezone.utc)
    hour = timedelta(hours=1)
    cases = []
    for state, a, b, more in (
        ("REQUESTED", -hour, 2 * hour, {}), ("REQUESTED", -3 * hour, -hour, {}),
        ("APPROVED", -hour, 2 * hour, {"d": now}), ("APPROVED", hour, 2 * hour, {"d": now}),
        ("APPROVED", -3 * hour, -hour, {"d": now}),
        ("DECLINED", -hour, 2 * hour, {"d": now, "n": "No."}), ("CANCELLED", -3 * hour, -hour, {"c": now, "r": "Moved."}),
    ):
        visit = await _visit(w, f"Visitor {len(cases)}")
        (row,) = await _sql(
            "INSERT INTO visitor_authorizations (tenant_id, site_id, visitor_id, state, valid_from, valid_until, "
            "decided_at, decision_note, cancelled_at, cancel_reason) VALUES (:t,:s,:v,:st,:a,:b,:d,:n,:c,:r) "
            "RETURNING id, state, valid_from, valid_until",
            {"t": t, "s": s, "v": visit, "st": state, "a": now + a, "b": now + b, "d": more.get("d"),
             "n": more.get("n"), "c": more.get("c"), "r": more.get("r")})
        cases.append(row)
    rows = await _sql(f"SELECT a.id, {authorisation.STANDING_SQL} AS standing FROM visitor_authorizations a "
                      "WHERE a.tenant_id = :t", {"t": t, "now": now})
    by_sql = {r["id"]: r["standing"] for r in rows}
    assert {c["id"]: authorisation.standing(c, now) for c in cases} == by_sql
    assert sorted(by_sql.values()) == sorted(["AWAITING_HOST", "LAPSED", "VALID", "NOT_YET_VALID", "EXPIRED",
                                              "DECLINED", "CANCELLED"])


# ─── B. Asking ───────────────────────────────────────────────────────────────

async def test_asking_takes_the_host_and_the_period_from_the_visit_and_the_host_is_told(told):
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    told.tokens[f"push_tokens:{w['tenant']}:{host}"] = {"ExponentPushToken[host]"}
    told.tokens[f"push_tokens:{w['tenant']}:{w['users'][GUARD]}"] = {"ExponentPushToken[guard]"}
    visit = await _visit(w, host=host)
    (on_visit,) = await _sql("SELECT expected_from, expected_until FROM visitors WHERE id = :v", {"v": visit})
    async with _client() as c:
        r = await _ask(c, w, visit)
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["state"] == "REQUESTED" and made["standing"] == "AWAITING_HOST"
        assert made["host_user_id"] == str(host) and made["host_name"] == "Tan Wei Ming"
        assert _at(made["valid_from"]) == on_visit["expected_from"] and _at(made["valid_until"]) == on_visit["expected_until"]
        assert made["subject"] == {"kind": "visit", "id": str(visit), "name": "Lim Mei Ling", "company": "Acme Lifts",
                                   "detail": "walk_in", "status": "pending"}
        assert made["purpose"] == "Lift servicing" and made["site_name"] == "Factory A"
        assert made["says"][0].startswith("Asked of Tan Wei Ming at ") and made["says"][0].endswith("Not yet answered.")
        assert made["asked_by_me"] is True and made["asked_of_me"] is False
        assert made["may"] == {"approve": False, "decline": False, "cancel": True, "extend": False, "places": True,
                               "escort": True, "id_seen": True, "review_movements": False}
        assert made["requested_by_name"] == "Role 5 User"
        # The number on the visitor's ID is on the visit. It is not given here.
        assert ID_NUMBER not in r.text
        seen_by_host = (await c.get(f"{BASE}/{made['id']}", headers=host_h)).json()
        assert seen_by_host["asked_of_me"] is True and seen_by_host["may"]["approve"] is True
        assert seen_by_host["movements"] is None, "where a badge was used is for whoever manages visits"
    (entry,) = await _audit(w, "visitorauth.request")
    assert entry["user_id"] == w["users"][GUARD] and str(entry["resource_id"]) == made["id"]
    assert entry["detail"]["subject_id"] == str(visit) and entry["detail"]["host_user_id"] == str(host)
    # The host's phone, and nobody else's — not the phone of whoever asked.
    (push,) = told.pushes
    assert push["tokens"] == ["ExponentPushToken[host]"] and push["body"] == (
        "Lim Mei Ling at Factory A: your answer is asked for.")
    assert push["data"] == {"type": api.REQUESTED_EVENT, "authorization_id": made["id"]}
    (event,) = told.events(api.REQUESTED_EVENT)
    assert event["authorization_id"] == made["id"] and event["state"] == "REQUESTED"
    assert await _raised(w) == 0


async def test_what_an_authorisation_has_to_be_and_who_may_ask_for_one():
    w, other = await _world(), await _world()
    visit, elsewhere = await _visit(w), await _visit(w, "Goh Kim Huat", site="site_b")
    undated = await _visit(w, "Ravi Pillai", expected=False)
    gone = await _visit(w, "Chua Li Na", status="departed")
    client, _ = await _guard(w, "Building Owner", role=CLIENT)
    stranger = other["users"][OPERATOR]
    far = await _place(w, "Block B", site="site_b")
    now = datetime.now(timezone.utc)
    soon = _iso(now + timedelta(hours=2))
    async with _client() as c:
        assert (await _ask(c, w, visit, who=VIEWER)).status_code == 403, "a viewer reads; a viewer does not ask"
        for body, status, words in (
            ({}, 422, "one of the two"),
            ({"visitor_id": str(visit), "work_permit_id": str(uuid.uuid4())}, 422, "one of the two"),
            ({"visitor_id": str(uuid.uuid4())}, 404, "Visit not found"),
            ({"visitor_id": str(undated)}, 422, "until when"),
            ({"visitor_id": str(visit), "valid_until": "2026-12-01T10:00:00"}, 422, "time zone"),
            ({"visitor_id": str(visit), "valid_until": _iso(now - timedelta(hours=1))}, 422, "after it starts"),
            ({"visitor_id": str(undated), "valid_from": _iso(now - timedelta(hours=3)),
              "valid_until": _iso(now - timedelta(hours=1))}, 422, "already passed"),
            ({"visitor_id": str(visit), "host_user_id": str(client)}, 422, "cannot read visitor authorisations"),
            ({"visitor_id": str(visit), "host_user_id": str(stranger)}, 422, "not one of the organisation's people"),
            ({"visitor_id": str(visit), "escort_user_id": str(w["users"][GUARD])}, 422, "none is asked for"),
            ({"visitor_id": str(visit), "escort_required": "yes"}, 422, None),
            ({"visitor_id": str(visit), "place_ids": [str(far)]}, 422, "not an active place of this site"),
            ({"visitor_id": str(visit), "site_id": str(w["site_b"])}, 422, "at another site"),
            ({"visitor_id": str(gone)}, 409, "is over"),
            ({"visitor_id": str(visit), "valid_until": soon, "id_number": ID_NUMBER}, 422, None),
        ):
            r = await c.post(BASE, headers=w["h"][GUARD], json=body)
            assert r.status_code == status, (body, r.text)
            if words:
                assert words in str(r.json()["detail"]), (body, r.text)
        # Somebody held to site A does not ask about a visit at site B, or learn that there is one.
        r = await _ask(c, w, elsewhere, who=SUPERVISOR)
        assert r.status_code == 404 and r.json()["detail"] == "Visit not found"
        assert (await _ask(c, w, elsewhere, who=OPERATOR)).status_code == 201
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.post(BASE, json={"visitor_id": str(visit)})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            assert (await c.get(BASE)).status_code == 200, "an integration may read what it may read"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    (count,) = await _sql("SELECT count(*) AS n FROM visitor_authorizations WHERE tenant_id = :t", {"t": w["tenant"]})
    assert count["n"] == 1, "only the one that was asked for properly"


async def test_one_request_waits_at_a_time_and_one_that_lapsed_is_asked_for_again():
    w = await _world()
    first, second, third = await _visit(w), await _visit(w, "Goh Kim Huat"), await _visit(w, "Ravi Pillai")
    async with _client() as c:
        waiting = await _asked(c, w, first)
        again = await _ask(c, w, first, who=OPERATOR)
        assert again.status_code == 409 and "already waiting for an answer" in again.json()["detail"]
        assert (await c.post(f"{BASE}/{waiting['id']}/approve", headers=w["h"][MANAGER])).status_code == 200
        again = await _ask(c, w, first)
        assert again.status_code == 409 and "already authorised" in again.json()["detail"]

        # Nobody answered before the time asked for had passed.
        lapsed = await _asked(c, w, second)
        await _period(lapsed["id"], 180, 60)
        seen = (await c.get(f"{BASE}/{lapsed['id']}", headers=w["h"][MANAGER])).json()
        assert seen["standing"] == "LAPSED" and seen["may"]["approve"] is False and seen["may"]["cancel"] is True
        late = await c.post(f"{BASE}/{lapsed['id']}/approve", headers=w["h"][MANAGER])
        assert late.status_code == 409 and "has to be asked for again" in late.json()["detail"]
        fresh = await _asked(c, w, second, who=OPERATOR)
        old = (await c.get(f"{BASE}/{lapsed['id']}", headers=w["h"][MANAGER])).json()
        assert old["state"] == "CANCELLED" and old["cancel_reason"] == api.WITHDRAWN_BY_ASKING_AGAIN
        assert old["cancelled_by_name"] == "Role 4 User" and old["is_latest"] is False
        assert fresh["is_latest"] is True and fresh["standing"] == "AWAITING_HOST"

        # One that was approved and ran its course stays as it was; another may be asked for.
        ran = await _approved(c, w, third)
        await _period(ran["id"], 180, 60)
        assert (await c.get(f"{BASE}/{ran['id']}", headers=w["h"][MANAGER])).json()["standing"] == "EXPIRED"
        newer = await _asked(c, w, third)
        kept = (await c.get(f"{BASE}/{ran['id']}", headers=w["h"][MANAGER])).json()
        assert kept["state"] == "APPROVED" and kept["is_latest"] is False and kept["may"]["extend"] is False
        r = await c.post(f"{BASE}/{ran['id']}/extend", headers=w["h"][MANAGER], json={
            "valid_until": _iso(datetime.now(timezone.utc) + timedelta(hours=3)), "reason": "Still on site."})
        assert r.status_code == 409 and "newer authorisation" in r.json()["detail"]

        latest = (await c.get(BASE, headers=w["h"][ADMIN])).json()["items"]
        assert {i["id"] for i in latest} == {waiting["id"], fresh["id"], newer["id"]}, "the latest of each visit"
        everything = (await c.get(BASE, headers=w["h"][ADMIN], params={"history": "true"})).json()["items"]
        assert {i["id"] for i in everything} == {waiting["id"], lapsed["id"], fresh["id"], ran["id"], newer["id"]}
    (entry,) = [e for e in await _audit(w, "visitorauth.request") if str(e["resource_id"]) == fresh["id"]]
    assert entry["detail"]["asked_again"] is True


async def test_a_contractors_work_permit_is_authorised_in_the_same_way():
    w = await _world()
    permit, refused = await _permit(w), await _permit(w, status="rejected", company="Speedy Scaffold")
    (on_permit,) = await _sql("SELECT start_at, end_at FROM work_permits WHERE id = :p", {"p": permit})
    async with _client() as c:
        r = await c.post(BASE, headers=w["h"][OPERATOR], json={"work_permit_id": str(permit)})
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["subject"] == {"kind": "work_permit", "id": str(permit), "name": "Coolair Services",
                                   "company": "Coolair Services", "detail": "Permit WP-0042: Chiller overhaul",
                                   "status": "approved", "workers_count": 3}
        assert _at(made["valid_from"]) == on_permit["start_at"] and _at(made["valid_until"]) == on_permit["end_at"]
        assert made["host_user_id"] is None and made["purpose"] == "Chiller overhaul"
        assert made["says"][0].startswith("Asked of whoever manages visits at ")
        # No host is named, so it waits for whoever manages visits at that site.
        for role, waits in ((MANAGER, True), (SUPERVISOR, True), (GUARD, False), (OPERATOR, False)):
            mine = (await c.get(f"{BASE}/mine", headers=w["h"][role])).json()["items"]
            assert [i["id"] for i in mine] == ([made["id"]] if waits else []), role
        assert (await c.get(f"{BASE}/mine", headers=w["h"][MANAGER])).json()["items"][0]["asked_of_me"] is False
        assert (await c.post(f"{BASE}/{made['id']}/approve", headers=w["h"][OPERATOR])).status_code == 403
        done = await c.post(f"{BASE}/{made['id']}/approve", headers=w["h"][SUPERVISOR], json={"note": "Briefed."})
        assert done.status_code == 200 and done.json()["standing"] == "VALID"
        assert done.json()["decision_note"] == "Briefed." and done.json()["decided_by_name"] == "Role 3 User"
        seen = (await c.get(f"{BASE}/{made['id']}", headers=w["h"][MANAGER])).json()
        assert seen["movements"] == {"available": False, "why": authorisation.NO_PERMIT_BADGE, "badges": [],
                                     "items": [], "note": authorisation.MOVEMENT_NOTE}
        over = await c.post(BASE, headers=w["h"][OPERATOR], json={"work_permit_id": str(refused)})
        assert over.status_code == 409 and "work permit is over" in over.json()["detail"]
        assert (await c.post(BASE, headers=w["h"][OPERATOR], json={"work_permit_id": str(uuid.uuid4())})).status_code == 404
    # The permit itself is as it was: its own approval is the existing endpoint's.
    (same,) = await _sql("SELECT status, approved_by_user_id FROM work_permits WHERE id = :p", {"p": permit})
    assert same["status"] == "approved" and same["approved_by_user_id"] is None


# ─── C. The answer ───────────────────────────────────────────────────────────

async def test_the_host_answers_and_whoever_asked_is_told(told):
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    told.tokens[f"push_tokens:{w['tenant']}:{w['users'][GUARD]}"] = {"ExponentPushToken[guard]"}
    told.tokens[f"push_tokens:{w['tenant']}:{host}"] = {"ExponentPushToken[host]"}
    visit = await _visit(w, host=host)
    async with _client() as c:
        made = await _asked(c, w, visit)
        told.pushes.clear()
        r = await c.post(f"{BASE}/{made['id']}/approve", headers=host_h)
        assert r.status_code == 200, r.text
        yes = r.json()
        assert yes["state"] == "APPROVED" and yes["standing"] == "VALID" and yes["decided_by_name"] == "Tan Wei Ming"
        assert yes["says"][0].startswith("Approved by Tan Wei Ming. Valid until ")
        assert yes["may"]["approve"] is False and yes["may"]["extend"] is True and yes["may"]["cancel"] is True
        again = await c.post(f"{BASE}/{made['id']}/approve", headers=host_h)
        assert again.status_code == 409 and again.json()["detail"] == "This has already been answered."
        assert (await c.post(f"{BASE}/{made['id']}/decline", headers=host_h, json={"reason": "Changed my mind."})).status_code == 409
    (entry,) = await _audit(w, "visitorauth.approve")
    assert entry["user_id"] == host and entry["detail"]["as_host"] is True
    (push,) = told.pushes
    assert push["tokens"] == ["ExponentPushToken[guard]"], "whoever asked, and not the host who has just answered"
    assert push["body"] == "Lim Mei Ling at Factory A: approved by Tan Wei Ming."
    (event,) = told.events(api.DECIDED_EVENT)
    assert event["state"] == "APPROVED" and event["subject_name"] == "Lim Mei Ling"
    # Saying yes checked nobody in.
    (row,) = await _sql("SELECT status, arrived_at FROM visitors WHERE id = :v", {"v": visit})
    assert row["status"] == "pending" and row["arrived_at"] is None
    assert await _sql("SELECT 1 FROM visitor_logs WHERE visitor_id = :v", {"v": visit}) == []


async def test_somebody_who_manages_visits_answers_for_a_host_and_nobody_else_does():
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    here, there = await _visit(w, host=host), await _visit(w, "Goh Kim Huat", site="site_b", host=host)
    declined = await _visit(w, "Ravi Pillai", host=host)
    async with _client() as c:
        a, b, d = await _asked(c, w, here), await _asked(c, w, there, who=OPERATOR), await _asked(c, w, declined)
        for role in (OPERATOR, GUARD, VIEWER):
            r = await c.post(f"{BASE}/{a['id']}/approve", headers=w["h"][role])
            assert r.status_code == 403 and "the host's" in r.json()["detail"], role
            assert (await c.post(f"{BASE}/{a['id']}/decline", headers=w["h"][role], json={"reason": "No."})).status_code == 403
        # Held to site A: the visit at site B is not theirs to see, let alone answer.
        assert (await c.post(f"{BASE}/{b['id']}/approve", headers=w["h"][SUPERVISOR])).status_code == 404
        r = await c.post(f"{BASE}/{a['id']}/approve", headers=w["h"][SUPERVISOR])
        assert r.status_code == 200 and r.json()["decided_by_name"] == "Role 3 User"
        assert r.json()["host_name"] == "Tan Wei Ming", "the host is still the host"
        # A no says why.
        for body in ({}, {"reason": ""}, {"reason": "   "}):
            assert (await c.post(f"{BASE}/{d['id']}/decline", headers=host_h, json=body)).status_code == 422
        no = await c.post(f"{BASE}/{d['id']}/decline", headers=host_h, json={"reason": "Not expected today."})
        assert no.status_code == 200 and no.json()["standing"] == "DECLINED"
        assert no.json()["says"] == ["Declined by Tan Wei Ming: Not expected today."]
        assert not any(v for k, v in no.json()["may"].items()), "nothing more is done to a no"
        assert (await c.post(f"{BASE}/{uuid.uuid4()}/approve", headers=w["h"][ADMIN])).status_code == 404
    (entry,) = await _audit(w, "visitorauth.approve")
    assert entry["user_id"] == w["users"][SUPERVISOR] and entry["detail"]["as_host"] is False
    assert len(await _audit(w, "visitorauth.decline")) == 1
    # The host's own API key is not the host.
    key = TokenPayload(user_id=str(host), tenant_id=str(w["tenant"]), role_id=OPERATOR, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            for step, body in (("approve", None), ("decline", {"reason": "No."}), ("cancel", {"reason": "No."})):
                r = await c.post(f"{BASE}/{b['id']}/{step}", json=body)
                assert r.status_code == 403 and "not by an API key" in r.json()["detail"], step
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    (still,) = await _sql("SELECT state FROM visitor_authorizations WHERE id = :i", {"i": b["id"]})
    assert still["state"] == "REQUESTED"


# ─── D. Cancelling and extending ─────────────────────────────────────────────

async def test_cancelling_says_why_and_is_the_hosts_the_managers_or_the_askers_while_unanswered(told):
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    one, two = await _visit(w, host=host), await _visit(w, "Goh Kim Huat", host=host)
    async with _client() as c:
        a = await _asked(c, w, one)
        url = f"{BASE}/{a['id']}/cancel"
        assert (await c.post(url, headers=w["h"][GUARD], json={"reason": " "})).status_code == 422
        assert (await c.post(url, headers=w["h"][VIEWER], json={"reason": "No."})).status_code == 403
        gone = await c.post(url, headers=w["h"][GUARD], json={"reason": "Visitor called off."})
        assert gone.status_code == 200 and gone.json()["standing"] == "CANCELLED"
        assert gone.json()["says"] == ["Cancelled by Role 5 User: Visitor called off."]
        again = await c.post(url, headers=w["h"][MANAGER], json={"reason": "Twice."})
        assert again.status_code == 409 and again.json()["detail"] == "There is nothing standing to cancel."

        # Once it is approved it is no longer the asker's to withdraw.
        b = await _asked(c, w, two)
        url = f"{BASE}/{b['id']}/cancel"
        assert (await c.post(f"{BASE}/{b['id']}/approve", headers=host_h)).status_code == 200
        r = await c.post(url, headers=w["h"][GUARD], json={"reason": "Mine to take back?"})
        assert r.status_code == 403 and "while it is unanswered" in r.json()["detail"]
        done = await c.post(url, headers=host_h, json={"reason": "Meeting moved to Friday."})
        assert done.status_code == 200 and done.json()["cancelled_by_name"] == "Tan Wei Ming"
        assert done.json()["state"] == "CANCELLED" and done.json()["decided_by_name"] == "Tan Wei Ming", "the yes is kept"
    entries = await _audit(w, "visitorauth.cancel")
    assert [(e["detail"]["was"], e["detail"]["stood"]) for e in entries] == [("REQUESTED", "AWAITING_HOST"),
                                                                             ("APPROVED", "VALID")]
    assert [e["state"] for e in told.events(api.DECIDED_EVENT)] == ["CANCELLED", "APPROVED", "CANCELLED"]


async def test_extending_says_why_and_runs_later_than_it_did():
    w = await _world()
    visit = await _visit(w)
    now = datetime.now(timezone.utc)
    later = _iso(now + timedelta(hours=9))
    async with _client() as c:
        made = await _asked(c, w, visit)
        url = f"{BASE}/{made['id']}/extend"
        r = await c.post(url, headers=w["h"][MANAGER], json={"valid_until": later, "reason": "Overrunning."})
        assert r.status_code == 409 and "approved" in r.json()["detail"], "an unanswered request is not extended"
        assert (await c.post(f"{BASE}/{made['id']}/approve", headers=w["h"][MANAGER])).status_code == 200
        for who, body, status in (
            (GUARD, {"valid_until": later, "reason": "Overrunning."}, 403),
            (MANAGER, {"valid_until": later}, 422),
            (MANAGER, {"valid_until": later, "reason": "  "}, 422),
            (MANAGER, {"valid_until": "2027-01-01T10:00:00", "reason": "Overrunning."}, 422),
            (MANAGER, {"valid_until": _iso(now + timedelta(hours=1)), "reason": "Earlier."}, 422),
        ):
            assert (await c.post(url, headers=w["h"][who], json=body)).status_code == status, body
        done = await c.post(url, headers=w["h"][MANAGER], json={"valid_until": later, "reason": "Lift part delayed."})
        assert done.status_code == 200, done.text
        assert _at(done.json()["valid_until"]) == _at(later) and done.json()["extend_reason"] == "Lift part delayed."
        assert done.json()["extended_by_name"] == "Role 8 User" and done.json()["decided_by_name"] == "Role 8 User"
        # One that has run out is brought back by a person, with a reason — not by itself.
        await _period(made["id"], 300, 60)
        assert (await c.get(f"{BASE}/{made['id']}", headers=w["h"][GUARD])).json()["standing"] == "EXPIRED"
        back = await c.post(url, headers=w["h"][SUPERVISOR], json={
            "valid_until": _iso(now + timedelta(hours=2)), "reason": "Returned to finish."})
        assert back.status_code == 200 and back.json()["standing"] == "VALID"
    first, second = await _audit(w, "visitorauth.extend")
    assert _at(first["detail"]["now"]) == _at(later) and first["detail"]["was"] != first["detail"]["now"]
    assert second["user_id"] == w["users"][SUPERVISOR]


# ─── E. The places, the escort and the ID ────────────────────────────────────

async def test_the_places_of_a_visit_are_the_askers_until_it_is_approved_and_the_hosts_after():
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    block, plant = await _place(w, "Block A"), await _place(w, "Plant room", "ZONE")
    far, shut = await _place(w, "Block B", site="site_b"), await _place(w, "Old store", "ZONE", active=False)
    visit = await _visit(w, host=host)
    async with _client() as c:
        made = await _asked(c, w, visit, place_ids=[str(block)])
        assert [p["name"] for p in made["places"]] == ["Block A"] and made["says"][-1] == "Asked for: Block A."
        url = f"{BASE}/{made['id']}/places"
        both = await c.put(url, headers=w["h"][GUARD], json={"place_ids": [str(plant), str(block), str(block)]})
        assert both.status_code == 200 and [p["name"] for p in both.json()["places"]] == ["Block A", "Plant room"]
        for wrong in (far, shut, uuid.uuid4()):
            r = await c.put(url, headers=w["h"][GUARD], json={"place_ids": [str(wrong)]})
            assert r.status_code == 422 and "not an active place of this site" in r.json()["detail"]
        assert (await c.put(url, headers=w["h"][OPERATOR], json={"place_ids": []})).status_code == 403, "not theirs"
        assert (await c.post(f"{BASE}/{made['id']}/approve", headers=host_h)).status_code == 200
        # What was approved is not changed by whoever asked.
        r = await c.put(url, headers=w["h"][GUARD], json={"place_ids": [str(block)]})
        assert r.status_code == 403 and "Once approved" in r.json()["detail"]
        after = (await c.get(f"{BASE}/{made['id']}", headers=w["h"][GUARD])).json()
        assert after["says"][-1] == "Authorised for: Block A, Plant room." and after["may"]["places"] is False
        general = await c.put(url, headers=host_h, json={"place_ids": []})
        assert general.status_code == 200 and general.json()["places"] == []
        assert general.json()["says"][-1] == "No particular places are named: the site in general."
        await c.post(f"{BASE}/{made['id']}/cancel", headers=host_h, json={"reason": "Done."})
        over = await c.put(url, headers=host_h, json={"place_ids": [str(block)]})
        assert over.status_code == 409 and "stands as it was" in over.json()["detail"]
    first, second = await _audit(w, "visitorauth.places")
    assert (first["detail"]["was"], first["detail"]["now"], first["detail"]["stood"]) == (1, 2, "AWAITING_HOST")
    assert (second["detail"]["was"], second["detail"]["now"], second["detail"]["stood"]) == (2, 0, "VALID")


async def test_an_escort_is_asked_for_and_named_and_an_id_is_seen_without_its_number():
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    walker, _ = await _guard(w, "Kumar Raj")
    client, _ = await _guard(w, "Building Owner", role=CLIENT)
    visit = await _visit(w, host=host)
    async with _client() as c:
        made = await _asked(c, w, visit, escort_required=True)
        assert made["says"][1] == "To be escorted: nobody is named yet."
        url = f"{BASE}/{made['id']}/escort"
        named = await c.put(url, headers=w["h"][GUARD], json={
            "escort_required": True, "escort_user_id": str(walker), "escort_note": "Stay with the engineer"})
        assert named.status_code == 200, named.text
        assert named.json()["says"][1] == "To be escorted by Kumar Raj (Stay with the engineer)."
        for body, words in (({"escort_required": False, "escort_user_id": str(walker)}, "none is asked for"),
                            ({"escort_required": True, "escort_user_id": str(client)}, "cannot read"),
                            ({"escort_required": "yes"}, None), ({}, None)):
            r = await c.put(url, headers=w["h"][GUARD], json=body)
            assert r.status_code == 422, body
            assert words is None or words in str(r.json()["detail"])
        assert (await c.put(url, headers=w["h"][VIEWER], json={"escort_required": False})).status_code == 403
        assert (await c.post(f"{BASE}/{made['id']}/approve", headers=host_h)).status_code == 200
        # Whether there is an escort at all was approved. Who it is, is the gate's to say.
        r = await c.put(url, headers=w["h"][GUARD], json={"escort_required": False})
        assert r.status_code == 403 and "Once approved" in r.json()["detail"]
        swapped = await c.put(url, headers=w["h"][GUARD], json={"escort_required": True,
                                                                "escort_user_id": str(w["users"][GUARD])})
        assert swapped.status_code == 200 and swapped.json()["escort_name"] == "Role 5 User"
        assert (await c.put(url, headers=host_h, json={"escort_required": False})).json()["says"][1] == "No escort is asked for."

        url = f"{BASE}/{made['id']}/id-seen"
        assert named.json()["says"][2] == "Nobody has recorded seeing an ID."
        for kind, words in ((ID_NUMBER, "not its number"), ("Passport K1234567", "not its number"), ("   ", "what kind"),
                            ("", None), ("x" * 31, None)):
            r = await c.post(url, headers=w["h"][GUARD], json={"kind": kind})
            assert r.status_code == 422, kind
            assert words is None or words in str(r.json()["detail"])
        assert (await c.post(url, headers=w["h"][VIEWER], json={"kind": "Passport"})).status_code == 403
        assert (await c.post(url, headers=w["h"][GUARD], json={"kind": "Passport", "number": "K1"})).status_code == 422
        seen = await c.post(url, headers=w["h"][GUARD], json={"kind": "Work pass"})
        assert seen.status_code == 200 and seen.json()["id_document_kind"] == "Work pass"
        assert seen.json()["id_checked_by_name"] == "Role 5 User" and seen.json()["id_checked_at"]
        assert seen.json()["says"][2] == "ID seen: Work pass, by Role 5 User."
    assert "Work pass" in authorisation.ID_KINDS
    (entry,) = await _audit(w, "visitorauth.id_seen")
    assert (entry["detail"]["kind"], entry["detail"]["seen_before"]) == ("Work pass", None)
    assert ID_NUMBER not in str(entry["detail"])
    columns = [r["column_name"] for r in await _sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ANY(:t)", {"t": list(TABLES)})]
    assert not [col for col in columns if "number" in col or col in ("nric", "passport")], "no ID number is kept"
    assert len(await _audit(w, "visitorauth.escort")) == 3


# ─── F. The gate, the lists, and who reads what ──────────────────────────────

async def test_the_gate_reads_what_stands_and_checks_the_visitor_in_as_it_always_has():
    w = await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    visit, permit = await _visit(w, host=host), await _permit(w)
    async with _client() as c:
        url = f"{BASE}/standing"
        none = (await c.get(url, headers=w["h"][GUARD], params={"visitor_id": str(visit)})).json()
        assert none == {"standing": "NOT_ASKED", "says": ["No authorisation has been asked for."],
                        "authorization": None, "note": authorisation.GATE_NOTE}
        assert (await c.get(url, headers=w["h"][GUARD], params={"work_permit_id": str(permit)})).json()["standing"] == "NOT_ASKED"
        assert (await c.get(url, headers=w["h"][GUARD])).status_code == 422
        both = {"visitor_id": str(visit), "work_permit_id": str(permit)}
        assert (await c.get(url, headers=w["h"][GUARD], params=both)).status_code == 422
        assert (await c.get(url, headers=w["h"][GUARD], params={"visitor_id": str(uuid.uuid4())})).status_code == 404

        made = await _asked(c, w, visit)
        waits = (await c.get(url, headers=w["h"][GUARD], params={"visitor_id": str(visit)})).json()
        assert waits["standing"] == "AWAITING_HOST" and waits["authorization"]["id"] == made["id"]
        assert (await c.post(f"{BASE}/{made['id']}/decline", headers=host_h, json={"reason": "Not expected today."})).status_code == 200
        no = (await c.get(url, headers=w["h"][GUARD], params={"visitor_id": str(visit)})).json()
        assert no["standing"] == "DECLINED" and no["says"] == ["Declined by Tan Wei Ming: Not expected today."]
        assert no["note"] == authorisation.GATE_NOTE and ID_NUMBER not in str(no)

        # It informs. The guard decides, and the endpoint that has always checked visitors in still does.
        await _check_in(c, w, visit, "V-03")
    (row,) = await _sql("SELECT status FROM visitors WHERE id = :v", {"v": visit})
    assert row["status"] == "arrived", "an authorisation refuses nobody"
    assert await _raised(w) == 0, "and a visitor checked in against a no raises nothing"


async def test_the_lists_are_narrowed_and_kept_to_the_sites_and_people_they_concern():
    w, other = await _world(), await _world()
    host, host_h = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    lim, goh = await _visit(w, host=host), await _visit(w, "Goh Kim Huat", site="site_b", company="Percent % Co")
    ravi = await _visit(w, "Ravi Pillai", site="site_b", host=w["users"][SUPERVISOR])
    permit = await _permit(w)
    async with _client() as c:
        a = await _approved(c, w, lim)
        b = await _asked(c, w, goh, who=OPERATOR)
        r_ = await _asked(c, w, ravi, who=OPERATOR)
        p = (await c.post(BASE, headers=w["h"][OPERATOR], json={"work_permit_id": str(permit)})).json()

        async def ids(who=ADMIN, headers=None, **params) -> set:
            r = await c.get(BASE, headers=headers or w["h"][who], params=params)
            assert r.status_code == 200, r.text
            return {i["id"] for i in r.json()["items"]}

        assert await ids() == {a["id"], b["id"], r_["id"], p["id"]}
        assert await ids(standing="VALID") == {a["id"]}
        assert await ids(standing=["AWAITING_HOST", "VALID"]) == {a["id"], b["id"], r_["id"], p["id"]}
        assert await ids(standing="EXPIRED") == set()
        assert await ids(subject="work_permit") == {p["id"]} and await ids(subject="visit") == {a["id"], b["id"], r_["id"]}
        assert await ids(q="lim mei") == {a["id"]} and await ids(q="coolair") == {p["id"]} and await ids(q="WP-0042") == {p["id"]}
        # The words as typed: a percent sign is looked for, not treated as "anything".
        assert await ids(q="%") == {b["id"]} and await ids(q="_") == set()
        assert await ids(site_id=str(w["site_b"])) == {b["id"], r_["id"]}
        assert await ids(who=GUARD, whose="asked_by_me") == {a["id"]}
        assert await ids(headers=host_h, whose="asked_of_me") == {a["id"]}
        assert (await c.get(BASE, headers=w["h"][ADMIN], params={"standing": "PENDING"})).status_code == 422
        page = (await c.get(BASE, headers=w["h"][ADMIN], params={"limit": 1})).json()
        assert len(page["items"]) == 1 and page["has_more"] is True and page["can_ask"] and page["can_manage"]
        assert (await c.get(BASE, headers=w["h"][VIEWER])).json()["can_ask"] is False

        # Held to site A — and still reads the one at site B they are the host of.
        assert await ids(who=SUPERVISOR) == {a["id"], p["id"], r_["id"]}
        assert (await c.get(BASE, headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/{b['id']}", headers=w["h"][SUPERVISOR])).status_code == 404
        assert (await c.get(f"{BASE}/{r_['id']}", headers=w["h"][SUPERVISOR])).json()["asked_of_me"] is True
        mine = (await c.get(f"{BASE}/mine", headers=w["h"][SUPERVISOR])).json()["items"]
        assert [i["id"] for i in mine] == [r_["id"], p["id"]], "theirs to answer, whoever has waited longest first"
        assert (await c.get(f"{BASE}/mine", headers=host_h)).json()["items"] == [], "already answered"

        # Another organisation sees none of it.
        assert await ids(headers=other["h"][ADMIN]) == set()
        assert (await c.get(f"{BASE}/{a['id']}", headers=other["h"][ADMIN])).status_code == 404
        assert (await c.get(f"{BASE}/standing", headers=other["h"][GUARD], params={"visitor_id": str(lim)})).status_code == 404
        everything = (await c.get(BASE, headers=w["h"][ADMIN], params={"history": "true"})).text
        assert ID_NUMBER not in everything
    client, client_h = await _guard(w, "Building Owner", role=CLIENT)
    async with _client() as c:
        for path in ("", "/mine", f"/{a['id']}", f"/standing?visitor_id={lim}"):
            assert (await c.get(BASE + path, headers=client_h)).status_code == 403, path
            assert (await c.get(BASE + path)).status_code == 401, path


async def test_what_there_is_to_ask_about_at_a_site():
    w = await _world()
    host, _ = await _guard(w, "Tan Wei Ming", role=OPERATOR)
    await _guard(w, "Building Owner", role=CLIENT)
    left, _ = await _guard(w, "Former Guard")
    await _sql("UPDATE users SET is_active = FALSE WHERE id = :u", {"u": left})
    lim, roving = await _visit(w, host=host), await _visit(w, "Ravi Pillai", site=None)
    await _visit(w, "Chua Li Na", status="departed")
    await _visit(w, "Goh Kim Huat", site="site_b")
    permit = await _permit(w)
    await _permit(w, status="completed", company="Speedy Scaffold")
    block = await _place(w, "Block A")
    await _place(w, "Level 2", "FLOOR", parent=block)
    await _place(w, "Old store", "ZONE", active=False)
    async with _client() as c:
        r = await c.get(f"{BASE}/options", headers=w["h"][GUARD], params={"site_id": str(w["site_a"])})
        assert r.status_code == 200, r.text
        got = r.json()
        assert {v["id"]: v["host_name"] for v in got["visits"]} == {str(lim): "Tan Wei Ming", str(roving): None}
        assert [p["id"] for p in got["permits"]] == [str(permit)] and got["permits"][0]["name"] == "Coolair Services"
        assert [(p["name"], p["kind"], p["part_of"]) for p in got["places"]] == [("Block A", "BUILDING", None),
                                                                                ("Level 2", "FLOOR", "Block A")]
        names = [p["name"] for p in got["people"]]
        assert "Tan Wei Ming" in names and "Building Owner" not in names and "Former Guard" not in names
        assert got["id_kinds"] == list(authorisation.ID_KINDS) and ID_NUMBER not in r.text
        assert (await c.get(f"{BASE}/options", headers=w["h"][VIEWER], params={"site_id": str(w["site_a"])})).status_code == 403
        assert (await c.get(f"{BASE}/options", headers=w["h"][SUPERVISOR], params={"site_id": str(w["site_b"])})).status_code == 404
        assert (await c.get(f"{BASE}/options", headers=w["h"][GUARD])).status_code == 422
        # A visit that names no site is asked about with the site said.
        assert (await _ask(c, w, roving)).status_code == 422
        made = await _ask(c, w, roving, site_id=str(w["site_a"]), valid_until=_iso(datetime.now(timezone.utc) + timedelta(hours=2)))
        assert made.status_code == 201 and made.json()["site_name"] == "Factory A"
    (still,) = await _sql("SELECT site_id FROM visitors WHERE id = :v", {"v": roving})
    assert still["site_id"] is None, "the visit itself is not altered"


# ─── G. Where a visitor's badge was used ─────────────────────────────────────

async def _site_with_doors(w: dict) -> dict:
    """Block A with a door on its second floor, Block B with a lobby door, and a
    loading bay door that nobody has put on the map."""
    s: dict = {"block_a": await _place(w, "Block A"), "block_b": await _place(w, "Block B")}
    s["floor"] = await _place(w, "Level 2", "FLOOR", parent=s["block_a"])
    s["d_a2"], s["d_b"], s["d_bay"] = await _door(w, "A2 east"), await _door(w, "B lobby"), await _door(w, "Loading bay")
    s["d_far"] = await _door(w, "B-site gate", site="site_b")
    await _place(w, "A2 east door", "ACCESS_POINT", parent=s["floor"], door=s["d_a2"])
    await _place(w, "B lobby door", "ACCESS_POINT", parent=s["block_b"], door=s["d_b"])
    return s


async def test_where_a_badge_was_used_is_set_against_what_the_visit_is_authorised_for():
    w = await _world()
    s = await _site_with_doors(w)
    visit = await _visit(w)
    card, another = await _card(w, "V-17"), await _card(w, "V-18")
    async with _client() as c:
        yes = await _approved(c, w, visit, place_ids=[str(s["block_a"])])
        await _period(yes["id"], 180, 30)
        await _check_in(c, w, visit, "V-17", minutes_ago=150)
        early = await _swipe(w, s["d_b"], card, 165)        # before the badge was theirs
        inside = await _swipe(w, s["d_a2"], card, 120)      # a door of Block A, in the period
        outside = await _swipe(w, s["d_b"], card, 90)       # Block B
        unmapped = await _swipe(w, s["d_bay"], card, 80)    # a door that is not on the map
        late = await _swipe(w, s["d_a2"], card, 10, kind="denied")   # after it ran out
        await _swipe(w, s["d_b"], another, 85)              # somebody else's badge
        await _swipe(w, s["d_far"], card, 70)               # the same number at another site

        seen = (await c.get(f"{BASE}/{yes['id']}", headers=w["h"][SUPERVISOR])).json()["movements"]
        assert seen["available"] is True and seen["badges"] == ["V-17"] and seen["note"] == authorisation.MOVEMENT_NOTE
        got = {i["access_event_id"]: i for i in seen["items"]}
        assert list(got) == [str(inside), str(outside), str(unmapped), str(late)], "oldest first, and only theirs"
        assert str(early) not in got
        assert (got[str(inside)]["within"], got[str(inside)]["in_period"], got[str(inside)]["to_look_at"]) == (True, True, False)
        assert got[str(inside)]["place_name"] == "A2 east door" and got[str(inside)]["part_of"] == "Level 2"
        assert (got[str(outside)]["within"], got[str(outside)]["in_period"], got[str(outside)]["to_look_at"]) == (False, True, True)
        assert got[str(outside)]["door_name"] == "B lobby" and got[str(outside)]["why_not_known"] is None
        assert (got[str(unmapped)]["within"], got[str(unmapped)]["to_look_at"]) == (None, False)
        assert got[str(unmapped)]["why_not_known"] == authorisation.NOT_ON_MAP and got[str(unmapped)]["place_id"] is None
        assert (got[str(late)]["within"], got[str(late)]["in_period"], got[str(late)]["to_look_at"]) == (True, False, True)
        assert got[str(late)]["event_type"] == "denied"
        assert all(i["review"] is None for i in got.values())

        # Where a visitor went is for whoever manages visits. Not for everybody who reads the authorisation.
        for role in (GUARD, OPERATOR, VIEWER):
            assert (await c.get(f"{BASE}/{yes['id']}", headers=w["h"][role])).json()["movements"] is None
            assert (await c.get(f"{BASE}/to-review", headers=w["h"][role])).status_code == 403

        todo = (await c.get(f"{BASE}/to-review", headers=w["h"][SUPERVISOR])).json()
        assert [i["access_event_id"] for i in todo["items"]] == [str(late), str(outside)], "newest first"
        assert todo["items"][0]["subject_name"] == "Lim Mei Ling" and todo["note"] == authorisation.MOVEMENT_NOTE
        assert (await c.get(f"{BASE}/to-review", headers=w["h"][MANAGER], params={"site_id": str(w["site_b"])})).json()["items"] == []

        url = f"{BASE}/{yes['id']}/movements"
        assert (await c.post(f"{url}/{outside}/review", headers=w["h"][GUARD], json={"outcome": "IN_ORDER"})).status_code == 403
        for body in ({"outcome": "FOLLOWED_UP"}, {"outcome": "FOLLOWED_UP", "note": "  "}, {"outcome": "SUSPICIOUS"}, {}):
            assert (await c.post(f"{url}/{outside}/review", headers=w["h"][SUPERVISOR], json=body)).status_code == 422
        for not_theirs in (early, uuid.uuid4()):
            r = await c.post(f"{url}/{not_theirs}/review", headers=w["h"][SUPERVISOR], json={"outcome": "IN_ORDER"})
            assert r.status_code == 404 and "not a door event of this visit's badge" in r.json()["detail"]
        done = await c.post(f"{url}/{outside}/review", headers=w["h"][SUPERVISOR], json={
            "outcome": "IN_ORDER", "note": "Escorted to the canteen in Block B."})
        assert done.status_code == 201, done.text
        assert done.json()["review"]["outcome"] == "IN_ORDER" and done.json()["review"]["reviewed_by_name"] == "Role 3 User"
        twice = await c.post(f"{url}/{outside}/review", headers=w["h"][MANAGER], json={"outcome": "IN_ORDER"})
        assert twice.status_code == 409
        followed = await c.post(f"{url}/{late}/review", headers=w["h"][MANAGER], json={
            "outcome": "FOLLOWED_UP", "note": "Badge collected; host reminded of the hours."})
        assert followed.status_code == 201
        assert (await c.get(f"{BASE}/to-review", headers=w["h"][SUPERVISOR])).json()["items"] == []
    entries = await _audit(w, "visitorauth.movement_review")
    assert [(e["detail"]["outcome"], e["detail"]["within"], e["detail"]["in_period"]) for e in entries] == [
        ("IN_ORDER", False, True), ("FOLLOWED_UP", True, False)]
    # Listing them, and a person looking at them, raised nothing.
    assert await _raised(w) == 0
    assert await _sql("SELECT 1 FROM security_events WHERE tenant_id = :t", {"t": w["tenant"]}) == []


async def test_what_cannot_be_said_of_a_movement_is_said_to_be_not_known():
    w = await _world()
    s = await _site_with_doors(w)
    card = await _card(w, "V-21")
    unbadged, general, unanswered = await _visit(w), await _visit(w, "Goh Kim Huat"), await _visit(w, "Ravi Pillai")
    async with _client() as c:
        # No badge number was written when they were checked in.
        a = await _approved(c, w, unbadged, place_ids=[str(s["block_a"])])
        await _check_in(c, w, unbadged, None, minutes_ago=60)
        seen = (await c.get(f"{BASE}/{a['id']}", headers=w["h"][MANAGER])).json()["movements"]
        assert (seen["available"], seen["why"], seen["items"]) == (False, authorisation.NO_BADGE, [])

        # Approved for the site in general: there are no places to be outside of.
        b = await _approved(c, w, general)
        await _period(b["id"], 180, -120)
        await _check_in(c, w, general, "V-21", minutes_ago=60)
        await _swipe(w, s["d_b"], card, 30)
        (item,) = (await c.get(f"{BASE}/{b['id']}", headers=w["h"][MANAGER])).json()["movements"]["items"]
        assert (item["within"], item["in_period"], item["to_look_at"]) == (None, True, False)
        assert item["why_not_known"] == authorisation.NO_PLACES

        # The badge goes back at the gate, and the next visitor is handed the same number.
        r = await c.post(f"/api/v1/visitors/{general}/checkout", headers=w["h"][GUARD], json={})
        assert r.status_code == 200, r.text
        await _sql("UPDATE visitors SET departed_at = now() - interval '20 minutes' WHERE id = :v", {"v": general})
        d = await _asked(c, w, unanswered, place_ids=[str(s["block_a"])])
        await _check_in(c, w, unanswered, "V-21", minutes_ago=15)
        next_swipe = await _swipe(w, s["d_b"], card, 5)
        first = (await c.get(f"{BASE}/{b['id']}", headers=w["h"][MANAGER])).json()["movements"]["items"]
        assert len(first) == 1 and first[0]["access_event_id"] != str(next_swipe), "not theirs once they had left"

        # Never approved: there is nothing authorised to compare with, and it is not called anything.
        (item,) = (await c.get(f"{BASE}/{d['id']}", headers=w["h"][MANAGER])).json()["movements"]["items"]
        assert item["access_event_id"] == str(next_swipe)
        assert (item["within"], item["in_period"], item["to_look_at"]) == (None, None, False)
        assert item["why_not_known"] == authorisation.NOT_APPROVED
        assert (await c.get(f"{BASE}/to-review", headers=w["h"][MANAGER])).json()["items"] == []
    assert await _raised(w) == 0


async def test_a_badge_handed_to_somebody_else_is_no_longer_the_first_visitors():
    w = await _world()
    s = await _site_with_doors(w)
    card = await _card(w, "V-30")
    first, second = await _visit(w), await _visit(w, "Goh Kim Huat")
    async with _client() as c:
        a = await _approved(c, w, first, place_ids=[str(s["block_a"])])
        b = await _approved(c, w, second, place_ids=[str(s["block_b"])])
        await _period(a["id"], 180, -120)
        await _period(b["id"], 180, -120)
        # The first visitor was never checked out; the same number was written against the second.
        await _check_in(c, w, first, "V-30", minutes_ago=120)
        mine = await _swipe(w, s["d_a2"], card, 100)
        await _check_in(c, w, second, "V-30", minutes_ago=60)
        theirs = await _swipe(w, s["d_b"], card, 30)
        one = (await c.get(f"{BASE}/{a['id']}", headers=w["h"][MANAGER])).json()["movements"]["items"]
        two = (await c.get(f"{BASE}/{b['id']}", headers=w["h"][MANAGER])).json()["movements"]["items"]
    assert [i["access_event_id"] for i in one] == [str(mine)] and one[0]["within"] is True
    assert [i["access_event_id"] for i in two] == [str(theirs)] and two[0]["within"] is True
    # Had the second swipe been put to the first visitor it would have been "outside". It is not put to them.
    assert not [i for i in one + two if i["to_look_at"]]


# ─── H. What the application role and the database refuse ────────────────────

async def test_what_the_application_role_cannot_do_to_an_authorisation():
    w, other = await _world(), await _world()
    s = await _site_with_doors(w)
    card, visit = await _card(w, "V-40"), await _visit(w)
    async with _client() as c:
        yes = await _approved(c, w, visit, place_ids=[str(s["block_a"])])
        await _check_in(c, w, visit, "V-40", minutes_ago=60)
        swipe = await _swipe(w, s["d_b"], card, 30)
        r = await c.post(f"{BASE}/{yes['id']}/movements/{swipe}/review", headers=w["h"][MANAGER], json={"outcome": "IN_ORDER"})
        assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
        scoped = "tenant_id = current_setting('app.current_tenant')::uuid"
        for statement in (
            f"DELETE FROM visitor_authorizations WHERE {scoped}",
            # Whose visit it is, at which site, who asked and from when: none of it is rewritten.
            f"UPDATE visitor_authorizations SET visitor_id = NULL, work_permit_id = gen_random_uuid() WHERE {scoped}",
            f"UPDATE visitor_authorizations SET site_id = gen_random_uuid() WHERE {scoped}",
            f"UPDATE visitor_authorizations SET requested_by_user_id = NULL WHERE {scoped}",
            f"UPDATE visitor_authorizations SET requested_at = now() WHERE {scoped}",
            f"UPDATE visitor_authorizations SET valid_from = now() - interval '1 year' WHERE {scoped}",
            f"UPDATE visitor_authorizations SET host_user_id = NULL WHERE {scoped}",
            f"UPDATE visitor_authorization_places SET place_id = gen_random_uuid() WHERE {scoped}",
            f"UPDATE visitor_movement_reviews SET note = 'rewritten' WHERE {scoped}",
            f"UPDATE visitor_movement_reviews SET outcome = 'FOLLOWED_UP' WHERE {scoped}",
            f"DELETE FROM visitor_movement_reviews WHERE {scoped}",
        ):
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(statement))
            await db.rollback()
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0, table
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(text(
                "INSERT INTO visitor_authorizations (tenant_id, site_id, visitor_id, valid_from, valid_until) "
                "VALUES (:t, :s, :v, now(), now() + interval '1 hour')"),
                {"t": w["tenant"], "s": w["site_a"], "v": visit})
        await db.rollback()
    held = {}
    for table in TABLES:
        row = (await _sql("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :n", {"n": table}))[0]
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        (can,) = await _sql("SELECT has_table_privilege('svc_app', :n, 'DELETE') AS d, "
                            "has_table_privilege('svc_app', :n, 'UPDATE') AS u", {"n": table})
        held[table] = (can["d"], can["u"])
    # The places of an authorisation are a list that is set afresh; nothing else is removed, and no whole row is updated.
    assert held == {"visitor_authorizations": (False, False), "visitor_authorization_places": (True, False),
                    "visitor_movement_reviews": (False, False)}
    assert "GRANT ALL" not in MIGRATION and MIGRATION.count("REVOKE ALL ON {table} FROM svc_app") == 1


async def test_what_the_database_refuses_of_an_authorisation():
    w = await _world()
    t, s = w["tenant"], w["site_a"]
    visit, permit = await _visit(w), await _permit(w)
    door, card = await _door(w, "Gate"), await _card(w, "V-50")
    swipe = await _swipe(w, door, card, 5)
    make = ("INSERT INTO visitor_authorizations (tenant_id, site_id, visitor_id, work_permit_id, state, valid_from, "
            "valid_until, decided_at, decision_note, cancelled_at, cancel_reason, id_document_kind) "
            "VALUES (:t, :s, :v, :p, :st, now(), now() + make_interval(hours => :h), :d, :n, :c, :r, :k) RETURNING id")
    now = datetime.now(timezone.utc)
    base = {"t": t, "s": s, "v": visit, "p": None, "st": "REQUESTED", "h": 2, "d": None, "n": None, "c": None,
            "r": None, "k": None}
    for over, constraint in (
        ({"p": permit}, "ck_visauth_subject"), ({"v": None}, "ck_visauth_subject"),
        ({"st": "MAYBE"}, "ck_visauth_state"), ({"h": 0}, "ck_visauth_period"),
        ({"st": "APPROVED"}, "ck_visauth_decided"),
        ({"st": "DECLINED", "d": now}, "ck_visauth_declined"), ({"st": "DECLINED", "d": now, "n": "  "}, "ck_visauth_declined"),
        ({"st": "CANCELLED", "c": now}, "ck_visauth_cancelled"), ({"st": "CANCELLED", "r": "Why"}, "ck_visauth_cancelled"),
        ({"k": "Passport"}, "ck_visauth_id_seen"),
    ):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(make, {**base, **over})
    (first,) = await _sql(make, base)
    with pytest.raises(DBAPIError, match="uq_visauth_visitor"):
        await _sql(make, base)
    (of_permit,) = await _sql(make, {**base, "v": None, "p": permit})
    with pytest.raises(DBAPIError, match="uq_visauth_permit"):
        await _sql(make, {**base, "v": None, "p": permit})
    # One that has been answered does not stand in the way of the next.
    await _sql("UPDATE visitor_authorizations SET state = 'APPROVED', decided_at = now() WHERE id = :i", {"i": first["id"]})
    await _sql(make, base)
    review = ("INSERT INTO visitor_movement_reviews (tenant_id, authorization_id, access_event_id, outcome, note) "
              "VALUES (:t, :a, :e, :o, :n)")
    for outcome, note, constraint in (("SUSPICIOUS", "x", "ck_vismove_outcome"), ("FOLLOWED_UP", None, "ck_vismove_note"),
                                      ("FOLLOWED_UP", " ", "ck_vismove_note")):
        with pytest.raises(DBAPIError, match=constraint):
            await _sql(review, {"t": t, "a": first["id"], "e": swipe, "o": outcome, "n": note})
    await _sql(review, {"t": t, "a": first["id"], "e": swipe, "o": "IN_ORDER", "n": None})
    with pytest.raises(DBAPIError, match="uq_vismove_once"):
        await _sql(review, {"t": t, "a": first["id"], "e": swipe, "o": "IN_ORDER", "n": None})
    # The visit goes, and what was authorised of it goes with it; so does the permit's.
    await _run([("DELETE FROM visitors WHERE id = :v", {"v": visit}), ("DELETE FROM work_permits WHERE id = :p", {"p": permit})])
    assert await _sql("SELECT 1 FROM visitor_authorizations WHERE id = ANY(:i)", {"i": [first["id"], of_permit["id"]]}) == []
    assert await _sql("SELECT 1 FROM visitor_movement_reviews WHERE tenant_id = :t", {"t": t}) == []


async def test_a_person_who_leaves_does_not_take_their_answer_with_them():
    w = await _world()
    host, host_h = await _guard(w, "Leaving Host", role=OPERATOR)
    visit = await _visit(w, host=host)
    async with _client() as c:
        made = await _asked(c, w, visit)
        assert (await c.post(f"{BASE}/{made['id']}/approve", headers=host_h)).status_code == 200
    await _run([("UPDATE visitors SET host_user_id = NULL WHERE id = :v", {"v": visit}),
                ("DELETE FROM audit_logs WHERE user_id = :u", {"u": host}), ("DELETE FROM users WHERE id = :u", {"u": host})])
    async with _client() as c:
        kept = (await c.get(f"{BASE}/{made['id']}", headers=w["h"][GUARD])).json()
    assert kept["state"] == "APPROVED" and kept["standing"] == "VALID"
    assert kept["host_user_id"] is None and kept["decided_by_name"] is None
    assert kept["says"][0].startswith("Approved by somebody no longer on the system. Valid until ")


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
                    served[(method, path.replace(":uuid", "").removeprefix(BASE))] = _needs(route)
    read = {"visitorauth:read"}
    write, manage = read | {"visitorauth:write"}, read | {"visitorauth:manage"}
    one = "/{authorization_id}"
    assert served == {
        ("GET", ""): read, ("POST", ""): write, ("GET", "/options"): write, ("GET", "/mine"): read,
        ("GET", "/standing"): read, ("GET", "/to-review"): manage, ("GET", one): read,
        # The host answers, and a host is anybody who reads: who it is, is judged in the handler.
        ("POST", f"{one}/approve"): read, ("POST", f"{one}/decline"): read, ("POST", f"{one}/cancel"): read,
        ("POST", f"{one}/extend"): read, ("PUT", f"{one}/places"): read,
        ("PUT", f"{one}/escort"): write, ("POST", f"{one}/id-seen"): write,
        ("POST", f"{one}/movements/{{access_event_id}}/review"): manage,
    }
    assert not [m for m, _ in served if m == "DELETE"], "an authorisation is cancelled, never removed"


async def test_who_holds_the_three_permissions_and_the_existing_endpoints_are_as_they_were():
    rows = await _sql("SELECT p.code, p.category, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                      "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code LIKE 'visitorauth:%' "
                      "GROUP BY p.code, p.category")
    assert {r["code"]: list(r["roles"]) for r in rows} == {
        "visitorauth:read": [2, 3, 4, 5, 6, 8], "visitorauth:write": [2, 3, 4, 5, 8], "visitorauth:manage": [2, 3, 8]}
    assert {r["category"] for r in rows} == {"visitor"}
    # The permissions that were there are held by whoever held them.
    was = await _sql("SELECT p.code, array_agg(rp.role_id ORDER BY rp.role_id) AS roles FROM permissions p "
                     "JOIN role_permissions rp ON rp.permission_id = p.id WHERE p.code = ANY(:c) GROUP BY p.code",
                     {"c": ["visitor:read", "visitor:checkin", "visitor:manage", "contractor:approve"]})
    assert {r["code"]: list(r["roles"]) for r in was} == {
        "visitor:read": [2, 3, 4, 5, 6, 8], "visitor:checkin": [2, 3, 4, 5, 8], "visitor:manage": [2, 3, 8],
        "contractor:approve": [2, 3, 8]}
    upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    for table in ("visitors", "visitor_logs", "work_permits", "contractors", "access_events", "site_places"):
        assert not re.search(rf"(ALTER TABLE|UPDATE|DELETE FROM|INSERT INTO|DROP TABLE)\s+{table}\b", upgrade), table
    assert "TRUNC" + "ATE" not in upgrade
    # Nothing here writes a visit, a check-in, a permit, a door event, an alert or an incident.
    for source in (Path(api.__file__), Path(authorisation.__file__)):
        code = source.read_text(encoding="utf-8").split('"""', 2)[2]
        for table in ("visitors", "visitor_logs", "work_permits", "contractors", "access_events", "access_credentials",
                      "alerts", "incidents", "security_events"):
            assert not re.search(rf"(INSERT INTO|UPDATE|DELETE FROM)\s+{table}\b", code), (source.name, table)
    service = Path(authorisation.__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"\b(INSERT INTO|UPDATE |DELETE FROM)", service), "the module only reads"
