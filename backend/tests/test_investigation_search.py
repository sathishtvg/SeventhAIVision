"""Smart investigation: one search across every source, for the right people.

  A — The rules, with nothing running: which source can answer which question
  B — Through the API: one list, one shape, every kind
  C — Who may search what: permissions, sites, organisations
  D — The filters, and a typed phrase
  E — Where was this seen
  F — What is on the record, and what happens when a search cannot answer

Every request goes through the real app over ASGI, as svc_app with RLS
enforced. Fixtures are written with the admin connection.

The claims, each with tests: a search gives nobody a record they could not
already open; a source that cannot answer a question is not asked it, and the
answer says so; the caller's sites and organisation bound every source; every
search is audited with what was asked and not what was found; and only a plate
or a watchlist entry can be followed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.routers import investigations as api
from app.services import investigation_sources as sources
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world

BASE = "/api/v1/investigations"
EVERY_KIND = set(sources.KINDS)
ALL = frozenset(sources.PERMISSIONS)
VECTOR = "[" + ",".join(["0.1"] * 512) + "]"


def _iso(at: datetime) -> str:
    return at.isoformat()


async def _seed(w: dict, *, site: str = "site_a", minutes_ago: int = 60, plate: str = "SGA1234B",
                tag: str = "A") -> dict:
    """One record of every kind at `site`, a minute apart, oldest first in the
    order of sources.KINDS. Returns their ids by kind, and the camera."""
    t, s = w["tenant"], w[site]
    ids = {k: uuid.uuid4() for k in sources.KINDS}
    extra = {k: uuid.uuid4() for k in ("camera", "watch", "door", "credential", "visitor", "panel", "route",
                                       "checkpoint", "session", "sensor", "detection")}
    guard, operator = w["users"][GUARD], w["users"][OPERATOR]
    base = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    at = {k: base + timedelta(minutes=i) for i, k in enumerate(sources.KINDS)}
    stmts = [
        ("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) VALUES (:i,:t,:s,:n,1.3001,103.8001)",
         {"i": extra["camera"], "t": t, "s": s, "n": f"Gate Camera {tag}"}),
        ("INSERT INTO alerts (id, tenant_id, camera_id, site_id, module_type, severity, title, message, status, "
         "    created_at) VALUES (:i,:t,:c,:s,'intrusion','critical',:n,'Somebody crossed the line','open',:at)",
         {"i": ids["ALERT"], "t": t, "c": extra["camera"], "s": s, "n": f"Zone breach {tag}", "at": at["ALERT"]}),
        ("INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, description, severity, status, "
         "    assigned_to_user_id, created_at) VALUES (:i,:t,:a,:c,:n,'Raised from the alert','high','open',:u,:at)",
         {"i": ids["INCIDENT"], "t": t, "a": ids["ALERT"], "c": extra["camera"], "n": f"Incident {tag}",
          "u": operator, "at": at["INCIDENT"]}),
        ("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
         "VALUES (:i,:t,:c,'lpr',0.91,:at)",
         {"i": ids["PLATE_READ"], "t": t, "c": extra["camera"], "at": at["PLATE_READ"]}),
        ("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, plate_confidence, "
         "    direction, vehicle_type, vehicle_color, watchlist_match) "
         "VALUES (:i,:at,:t,:c,:p,0.91,'entry','lorry','blue','block')",
         {"i": ids["PLATE_READ"], "at": at["PLATE_READ"], "t": t, "c": extra["camera"], "p": plate}),
        ("INSERT INTO face_watchlist_entries (id, tenant_id, person_name, list_type, embedding_v) "
         "VALUES (:i,:t,:n,'block',CAST(:v AS vector))",
         {"i": extra["watch"], "t": t, "n": f"Lim Ah Kow {tag}", "v": VECTOR}),
        ("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
         "VALUES (:i,:t,:c,'face',0.88,:at)",
         {"i": ids["FACE_MATCH"], "t": t, "c": extra["camera"], "at": at["FACE_MATCH"]}),
        ("INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id, matched_watchlist_id, "
         "    match_confidence, watchlist_match) VALUES (:i,:at,:t,:c,:w,0.88,'block')",
         {"i": ids["FACE_MATCH"], "at": at["FACE_MATCH"], "t": t, "c": extra["camera"], "w": extra["watch"]}),
        # A face the recogniser matched to nobody: a raw detection and nothing more.
        ("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
         "VALUES (:i,:t,:c,'face',0.7,:at)",
         {"i": extra["detection"], "t": t, "c": extra["camera"], "at": at["DETECTION"]}),
        ("INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id) VALUES (:i,:at,:t,:c)",
         {"i": extra["detection"], "at": at["DETECTION"], "t": t, "c": extra["camera"]}),
        ("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
         "VALUES (:i,:t,:c,'intrusion',0.8,:at)",
         {"i": ids["DETECTION"], "t": t, "c": extra["camera"], "at": at["DETECTION"]}),
        ("INSERT INTO access_doors (id, tenant_id, site_id, camera_id, name) VALUES (:i,:t,:s,:c,:n)",
         {"i": extra["door"], "t": t, "s": s, "c": extra["camera"], "n": f"Server Room {tag}"}),
        ("INSERT INTO access_credentials (id, tenant_id, user_id, holder_name, credential_ref) "
         "VALUES (:i,:t,:u,:n,:r)",
         {"i": extra["credential"], "t": t, "u": guard, "n": f"Raj Kumar {tag}", "r": f"card-{uuid.uuid4().hex[:8]}"}),
        ("INSERT INTO access_events (id, tenant_id, door_id, credential_id, event_type, denial_reason, occurred_at) "
         "VALUES (:i,:t,:d,:c,'denied','Outside permitted hours',:at)",
         {"i": ids["ACCESS"], "t": t, "d": extra["door"], "c": extra["credential"], "at": at["ACCESS"]}),
        ("INSERT INTO visitors (id, tenant_id, site_id, full_name, company, purpose, vehicle_plate, qr_token) "
         "VALUES (:i,:t,:s,:n,'Acme Logistics','Delivery',:p,:q)",
         {"i": extra["visitor"], "t": t, "s": s, "n": f"Tan Wei Ming {tag}", "p": plate, "q": uuid.uuid4().hex}),
        ("INSERT INTO visitor_logs (id, tenant_id, visitor_id, site_id, guard_user_id, event_type, occurred_at) "
         "VALUES (:i,:t,:v,:s,:g,'check_in',:at)",
         {"i": ids["VISITOR"], "t": t, "v": extra["visitor"], "s": s, "g": guard, "at": at["VISITOR"]}),
        ("INSERT INTO occurrence_book_entries (id, tenant_id, site_id, author_user_id, entry_type, body, severity, "
         "    occurred_at) VALUES (:i,:t,:s,:g,'incident','A blue lorry was left by the fence.','medium',:at)",
         {"i": ids["OCCURRENCE"], "t": t, "s": s, "g": guard, "at": at["OCCURRENCE"]}),
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, ai_confidence, "
         "    risk_score, label) VALUES (:i,:t,:s,'intrusion',:at,'HIGH',0.94,82,'Person near the tank farm')",
         {"i": ids["DRONE"], "t": t, "s": s, "at": at["DRONE"]}),
        ("INSERT INTO alarm_panels (id, tenant_id, site_id, name) VALUES (:i,:t,:s,:n)",
         {"i": extra["panel"], "t": t, "s": s, "n": f"Main Panel {tag}"}),
        ("INSERT INTO alarm_events (id, tenant_id, panel_id, event_type, severity, description, occurred_at) "
         "VALUES (:i,:t,:p,'zone_alarm','high','Zone 4 in alarm',:at)",
         {"i": ids["ALARM"], "t": t, "p": extra["panel"], "at": at["ALARM"]}),
        ("INSERT INTO patrol_routes (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Perimeter')",
         {"i": extra["route"], "t": t, "s": s}),
        ("INSERT INTO patrol_checkpoints (id, tenant_id, route_id, sequence, name, latitude, longitude) "
         "VALUES (:i,:t,:r,1,:n,1.3004,103.8004)",
         {"i": extra["checkpoint"], "t": t, "r": extra["route"], "n": f"Back Fence {tag}"}),
        ("INSERT INTO patrol_sessions (id, tenant_id, route_id, guard_user_id) VALUES (:i,:t,:r,:g)",
         {"i": extra["session"], "t": t, "r": extra["route"], "g": guard}),
        ("INSERT INTO checkpoint_scans (id, tenant_id, session_id, checkpoint_id, scanned_at, scan_method, "
         "    guard_user_id, verified) VALUES (:i,:t,:ss,:c,:at,'qr',:g,TRUE)",
         {"i": ids["PATROL_SCAN"], "t": t, "ss": extra["session"], "c": extra["checkpoint"], "g": guard,
          "at": at["PATROL_SCAN"]}),
        # Dealt with already: a guard has one live emergency at a time.
        ("INSERT INTO man_down_events (id, tenant_id, guard_user_id, site_id, trigger, detected_at, escalate_at, "
         "    status, resolved_at, outcome) VALUES (:i,:t,:g,:s,'manual',:at,:at,'resolved',:at,'guard_ok')",
         {"i": ids["MAN_DOWN"], "t": t, "g": guard, "s": s, "at": at["MAN_DOWN"]}),
        ("INSERT INTO security_situations (id, tenant_id, site_id, situation_number, title, severity, started_at, "
         "    last_event_at, risk_level, risk_score) VALUES (:i,:t,:s,:n,:ti,'high',:at,:at,'HIGH',72)",
         {"i": ids["SITUATION"], "t": t, "s": s, "n": f"SIT-T-{uuid.uuid4().hex[:8]}",
          "ti": f"Activity at the gate {tag}", "at": at["SITUATION"]}),
        ("INSERT INTO iot_sensors (id, tenant_id, site_id, name, sensor_type) VALUES (:i,:t,:s,:n,'temperature')",
         {"i": extra["sensor"], "t": t, "s": s, "n": f"Cold Room {tag}"}),
        ("INSERT INTO iot_alerts (id, tenant_id, sensor_id, alert_type, severity, value, message, created_at) "
         "VALUES (:i,:t,:se,'threshold','high',9.5,'Above the warning level',:at)",
         {"i": ids["SENSOR"], "t": t, "se": extra["sensor"], "at": at["SENSOR"]}),
    ]
    await _run(stmts)
    return {"ids": ids, "at": at, **extra, "since": base - timedelta(minutes=5),
            "until": base + timedelta(minutes=len(sources.KINDS) + 5)}


def _window(seed: dict, **more) -> dict:
    return {"from": _iso(seed["since"]), "to": _iso(seed["until"]), **more}


async def _search(c, w: dict, who: int, body: dict, expect: int = 200) -> dict:
    r = await c.post(f"{BASE}/search", headers=w["h"][who], json=body)
    assert r.status_code == expect, r.text
    return r.json()


def _kinds(found: dict) -> set[str]:
    return {i["kind"] for i in found["items"]}


def _left_out(found: dict) -> dict[str, str]:
    return {n["kind"]: n["reason"] for n in found["not_searched"]}


# ─── A. The rules ────────────────────────────────────────────────────────────

def _query(**what) -> sources.Query:
    now = datetime.now(timezone.utc)
    return sources.Query(since=now - timedelta(hours=1), until=now, **what)


def test_every_source_asks_for_the_permission_its_own_screen_asks_for():
    wanted = {"ALERT": "alert:read", "INCIDENT": "incident:read", "PLATE_READ": "detection:read",
              "FACE_MATCH": "detection:read", "DETECTION": "detection:read", "ACCESS": "access:read",
              "VISITOR": "visitor:read", "OCCURRENCE": "dob:read", "DRONE": "drone:event:read",
              "ALARM": "alarm:read", "PATROL_SCAN": "patrol:read", "MAN_DOWN": "mandown:read",
              "SITUATION": "intel:read", "SENSOR": "iot:read"}
    assert {s.kind: s.permission for s in sources.SOURCES} == wanted
    served = {}
    for path, methods in app.openapi()["paths"].items():
        served[path] = methods
    for path in ("/api/v1/alerts", "/api/v1/incidents", "/api/v1/detections/lpr-events", "/api/v1/access/events",
                 "/api/v1/visitors/logs", "/api/v1/dob", "/api/v1/drone-events", "/api/v1/alarms/events",
                 "/api/v1/man-down", "/api/v1/security-intelligence/situations", "/api/v1/iot/alerts"):
        assert path in served, f"{path} is the screen a source's permission is taken from"


def test_a_source_is_left_out_for_someone_who_may_not_read_it_and_the_answer_says_why():
    searched, left = sources.plan(_query(), ALL)
    assert [s.kind for s in searched] == [k for k in sources.KINDS if k != "DETECTION"]
    assert [n["kind"] for n in left] == ["DETECTION"] and "only when asked for" in left[0]["reason"]

    searched, left = sources.plan(_query(), ALL - {"visitor:read", "alert:read"})
    assert {"VISITOR", "ALERT"}.isdisjoint(s.kind for s in searched)
    why = {n["kind"]: n["reason"] for n in left}
    assert why["VISITOR"] == "You do not hold the permission visitor:read."
    assert why["ALERT"] == "You do not hold the permission alert:read."

    searched, left = sources.plan(_query(kinds=("VISITOR",)), frozenset())
    assert searched == [] and len(left) == 1, "asked for by name does not get round the permission"
    assert sources.plan(_query(kinds=("DETECTION",)), ALL)[0][0].kind == "DETECTION"


def test_a_source_that_cannot_answer_a_question_is_not_asked_it():
    def asked(**what) -> tuple[set[str], dict[str, str]]:
        searched, left = sources.plan(_query(**what), ALL)
        return {s.kind for s in searched}, {n["kind"]: n["reason"] for n in left}

    with_camera, why = asked(camera_ids=(str(uuid.uuid4()),))
    assert with_camera == {"ALERT", "INCIDENT", "PLATE_READ", "FACE_MATCH", "ACCESS", "SITUATION"}
    assert why["OCCURRENCE"] == "These records are not tied to a camera."
    with_plate, why = asked(plate="SGA1234B")
    assert with_plate == {"PLATE_READ", "VISITOR"} and why["ALERT"] == "These records carry no number plate."
    with_name, why = asked(person="tan")
    assert with_name == {"FACE_MATCH", "ACCESS", "VISITOR"} and why["DRONE"] == "These records name no person."
    with_staff, _ = asked(staff_user_id=str(uuid.uuid4()))
    assert with_staff == {"ALERT", "INCIDENT", "ACCESS", "VISITOR", "OCCURRENCE", "PATROL_SCAN", "MAN_DOWN"}
    with_risk, why = asked(risk_levels=("HIGH",))
    assert with_risk == {"DRONE", "SITUATION"} and why["ALERT"] == "These records carry no risk level."
    with_severity, _ = asked(severities=("high",))
    assert "VISITOR" not in with_severity and "ALERT" in with_severity

    # A name on the face watchlist is its keeper's to search by.
    searched, left = sources.plan(_query(person="lim"), ALL - {"watchlist:manage"})
    assert "FACE_MATCH" not in {s.kind for s in searched}
    assert {n["kind"]: n["reason"] for n in left}["FACE_MATCH"] == \
        "Searching these by name needs the permission watchlist:manage."


def test_what_a_search_may_ask_is_checked_before_anything_is_read():
    now = datetime.now(timezone.utc)
    for bad, why in (
        (sources.Query(since=now, until=now - timedelta(hours=1)), "ends before it starts"),
        (sources.Query(since=now - timedelta(days=93), until=now), "at most 92 days"),
        (_query(kinds=("EVERYTHING",)), "Unknown kind of record"),
        (_query(severities=("severe",)), "Unknown severity"),
        (_query(risk_levels=("high",)), "Unknown risk level"),
        (_query(plate="S*"), "at least three"),
        (_query(person="x"), "at least two"),
        (_query(text=" "), "at least two"),
    ):
        with pytest.raises(ValueError, match=why):
            sources.validate(bad)
    assert sources.normalise_plate(" sga-1234 b ") == "SGA1234B" and sources.normalise_plate("sg*4b") == "SG*4B"


def test_every_source_is_bounded_by_time_and_site_and_none_writes():
    params: dict = {}
    for source in sources.SOURCES:
        sql = sources._branch(source, _query(), ALL, [str(uuid.uuid4())], params)
        assert f"{source.time} >= :since" in sql and f"{source.time} < :until" in sql, source.kind
        assert f"{source.site} = ANY(:allowed_site_ids)" in sql, f"{source.kind} is not held to the caller's sites"
        assert source.site, f"{source.kind} has no site, so nobody restricted to sites could be shown it safely"
        upper = sql.upper()
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP ", "ALTER "):
            assert verb not in upper, f"{source.kind}: {verb.strip()}"
    assert set(sources.COLUMNS) >= {"kind", "id", "occurred_at", "site_id", "title", "subject_ref", "subject_label"}


def test_a_name_is_selected_only_for_someone_who_may_see_it():
    face = sources.BY_KIND["FACE_MATCH"]
    assert (face.subject_label, face.label_permission, face.subject_ref) == (
        "fw.person_name", "watchlist:manage", "f.matched_watchlist_id")
    with_it = sources._branch(face, _query(), ALL, None, {})
    without = sources._branch(face, _query(), ALL - {"watchlist:manage"}, None, {})
    assert "CAST(fw.person_name AS text) AS subject_label" in with_it
    assert "CAST(NULL AS text) AS subject_label" in without and "person_name AS" not in without
    assert "f.matched_watchlist_id IS NOT NULL" in without, "a face matched to nobody is not a person to search for"


# ─── B. One list, one shape ──────────────────────────────────────────────────

async def test_one_search_returns_every_kind_in_one_list_in_one_shape():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        found = await _search(c, w, ADMIN, _window(seed, kinds=sorted(EVERY_KIND)))
    assert found["total"] == len(EVERY_KIND) + 3 and not found["not_searched"], found["found"]
    assert found["found"] == {**{k: 1 for k in EVERY_KIND}, "DETECTION": 4}, "a plate, two faces, an intrusion: raw"
    assert set(found["searched"]) == EVERY_KIND
    times = [i["occurred_at"] for i in found["items"]]
    assert times == sorted(times, reverse=True), "newest first"
    for item in found["items"]:
        assert set(item) == set(sources.COLUMNS) | {"site_name", "camera_name"}, item["kind"]
        assert item["site_id"] == str(w["site_a"]) and item["site_name"] == "Factory A", item["kind"]
    by_kind = {i["kind"]: i for i in found["items"] if i["id"] == str(seed["ids"][i["kind"]])}
    assert set(by_kind) == EVERY_KIND

    plate = by_kind["PLATE_READ"]
    assert (plate["title"], plate["subject_kind"], plate["subject_ref"]) == ("SGA1234B", "VEHICLE", "SGA1234B")
    assert plate["summary"] == "entry · blue · lorry" and plate["camera_name"] == "Gate Camera A"
    assert (plate["latitude"], plate["longitude"]) == (1.3001, 103.8001), "where the camera is"
    assert by_kind["INCIDENT"]["event_type"] == "intrusion", "an incident is of the kind its alert was"
    assert by_kind["ALERT"]["detection_id"] is None and plate["detection_id"] == plate["id"]
    assert by_kind["DRONE"]["risk_level"] == "HIGH" and by_kind["DRONE"]["severity"] == "high"
    assert by_kind["SITUATION"]["risk_level"] == "HIGH"
    assert by_kind["VISITOR"]["subject_label"] == "Tan Wei Ming A"
    assert by_kind["ACCESS"]["subject_label"] == "Raj Kumar A" and by_kind["ACCESS"]["summary"] == \
        "Outside permitted hours"
    assert by_kind["MAN_DOWN"]["staff_user_id"] == str(w["users"][GUARD])
    assert by_kind["PATROL_SCAN"]["status"] == "verified" and by_kind["PATROL_SCAN"]["title"] == "Back Fence A"
    assert by_kind["OCCURRENCE"]["title"] == "Occurrence book: incident"
    assert "storage_path" not in str(found) and "embedding" not in str(found)


async def test_raw_detections_are_searched_only_when_asked_for_and_the_answer_says_so():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        found = await _search(c, w, ADMIN, _window(seed))
        assert "DETECTION" not in _kinds(found) and found["total"] == len(EVERY_KIND) - 1
        assert "only when asked for" in _left_out(found)["DETECTION"]
        assert found["note"] == sources.NOTE and "says nothing" in found["note"]
        only = await _search(c, w, ADMIN, _window(seed, kinds=["DETECTION"]))
        assert only["total"] == 4 and {i["event_type"] for i in only["items"]} == {"lpr", "face", "intrusion"}
        faces = await _search(c, w, ADMIN, _window(seed, kinds=["FACE_MATCH"]))
        assert faces["total"] == 1, "the face matched to nobody is a detection, not a watchlist match"


async def test_pages_run_through_the_whole_list_once():
    w = await _world()
    seed = await _seed(w)
    seen: list[tuple] = []
    async with _client() as c:
        for offset in (0, 5, 10):
            page = await _search(c, w, ADMIN, _window(seed, limit=5, offset=offset))
            seen += [(i["kind"], i["id"]) for i in page["items"]]
            assert page["total"] == 13 and page["has_more"] == (offset + 5 < 13)
        oldest = await _search(c, w, ADMIN, _window(seed, limit=3, oldest_first=True))
    assert len(seen) == len(set(seen)) == 13
    assert [i["kind"] for i in oldest["items"]] == ["ALERT", "INCIDENT", "PLATE_READ"]


# ─── C. Who may search what ──────────────────────────────────────────────────

async def test_who_may_search_at_all():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        for role in (ADMIN, MANAGER, SUPERVISOR, OPERATOR, VIEWER):
            assert (await _search(c, w, role, _window(seed)))["total"] == 13, role
        for role in (GUARD,):
            r = await c.post(f"{BASE}/search", headers=w["h"][role], json=_window(seed))
            assert r.status_code == 403 and r.json()["detail"] == "Missing permission: investigation:read"
        for role in (1, 7):
            headers = _auth(uuid.uuid4(), w["tenant"], role)
            for method, path in (("POST", "/search"), ("GET", "/sources"), ("GET", "/trail?plate=SGA1234B"),
                                 ("GET", "")):
                r = await c.request(method, f"{BASE}{path}", headers=headers, json={} if method == "POST" else None)
                assert r.status_code == 403, (role, path, r.text)
        assert (await c.post(f"{BASE}/search", json=_window(seed))).status_code == 401


async def test_the_platform_owner_and_a_customers_client_hold_none_of_it():
    rows = await _sql("SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = "
                      "rp.permission_id WHERE p.code LIKE 'investigation:%'")
    held: dict[str, set[int]] = {}
    for r in rows:
        held.setdefault(r["code"], set()).add(r["role_id"])
    assert held == {"investigation:read": {2, 3, 4, 6, 8}, "investigation:manage": {2, 3, 4, 8}}


async def test_an_api_key_and_a_support_session_are_not_investigators():
    w = await _world()
    seed = await _seed(w)
    for not_a_person, why in (
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True),
         "not by an API key"),
        (TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN,
                      support_session_id=str(uuid.uuid4())), "not from a support session"),
    ):
        app.dependency_overrides[get_token_payload] = lambda who=not_a_person: who
        try:
            async with _client() as c:
                for method, path in (("POST", "/search"), ("GET", "/sources"), ("GET", "")):
                    r = await c.request(method, f"{BASE}{path}", json=_window(seed) if method == "POST" else None)
                    if not_a_person.support_session_id and r.status_code in (401, 403) and why not in r.text:
                        continue  # refused earlier still: the session it names does not exist
                    assert r.status_code == 403 and why in r.json()["detail"], (path, r.text)
        finally:
            app.dependency_overrides.pop(get_token_payload, None)
        with pytest.raises(HTTPException) as refused:
            api._a_person(not_a_person)
        assert refused.value.status_code == 403 and why in refused.value.detail
    api._a_person(TokenPayload(user_id="u", tenant_id="t", role_id=ADMIN))


async def test_someone_restricted_to_sites_is_shown_those_sites_and_nothing_without_one():
    w = await _world()
    seed_a = await _seed(w, site="site_a", tag="A")
    seed_b = await _seed(w, site="site_b", tag="B", plate="SBF4491T")
    nowhere = uuid.uuid4()
    await _sql("INSERT INTO incidents (id, tenant_id, title, severity, status, created_at) "
               "VALUES (:i,:t,'An incident with no camera','low','open',:at)",
               {"i": nowhere, "t": w["tenant"], "at": seed_a["at"]["INCIDENT"]})
    window = {"from": _iso(seed_a["since"]), "to": _iso(seed_b["until"]), "kinds": sorted(EVERY_KIND), "limit": 200}
    async with _client() as c:
        everything = await _search(c, w, ADMIN, window)
        mine = await _search(c, w, SUPERVISOR, window)
        other = await _search(c, w, SUPERVISOR, {**window, "site_ids": [str(w["site_b"])]})
        named = await _search(c, w, SUPERVISOR, {**window, "plate": "SBF4491T"})
    assert everything["total"] == 2 * (len(EVERY_KIND) + 3) + 1
    assert str(nowhere) in {i["id"] for i in everything["items"]}
    assert mine["total"] == len(EVERY_KIND) + 3
    assert {i["site_id"] for i in mine["items"]} == {str(w["site_a"])}
    assert str(nowhere) not in {i["id"] for i in mine["items"]}, "no site, so not shown to someone held to sites"
    assert other["total"] == 0 and named["total"] == 0, "asking for another site by name finds nothing in it"


async def test_another_organisations_records_are_never_found_as_the_application_role():
    mine, theirs = await _world(), await _world()
    await _seed(mine, tag="A")
    seed = await _seed(theirs, tag="B", plate="SBF4491T")
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar(), \
            "this test means nothing unless the application's role is bound by row-level security"
    async with _client() as c:
        found = await _search(c, mine, ADMIN, _window(seed, kinds=sorted(EVERY_KIND), limit=200))
        plate = await _search(c, mine, ADMIN, _window(seed, plate="SBF4491T"))
        trail = (await c.get(f"{BASE}/trail", headers=mine["h"][ADMIN], params={
            "plate": "SBF4491T", "from": _iso(seed["since"]), "to": _iso(seed["until"])})).json()
    theirs_ids = {str(i) for i in seed["ids"].values()}
    assert found["total"] and theirs_ids.isdisjoint(i["id"] for i in found["items"])
    assert plate["total"] == 0 and trail["summary"]["sightings"] == 0
    assert {i["site_name"] for i in found["items"]} == {"Factory A"}


async def test_a_watchlist_name_is_shown_only_to_someone_who_keeps_the_watchlist():
    w = await _world()
    seed = await _seed(w)
    body = _window(seed, kinds=["FACE_MATCH"])
    async with _client() as c:
        for role, name in ((ADMIN, "Lim Ah Kow A"), (SUPERVISOR, "Lim Ah Kow A"), (OPERATOR, None), (VIEWER, None)):
            item = (await _search(c, w, role, body))["items"][0]
            assert item["subject_label"] == name, role
            assert item["subject_ref"] == str(seed["watch"]) and item["title"] == "A face matched a watchlist entry"
            assert "Lim" not in item["title"]
        by_name = await _search(c, w, OPERATOR, _window(seed, person="lim ah"))
        assert "FACE_MATCH" not in by_name["searched"]
        assert _left_out(by_name)["FACE_MATCH"] == "Searching these by name needs the permission watchlist:manage."
        assert (await _search(c, w, ADMIN, _window(seed, person="lim ah")))["found"] == {"FACE_MATCH": 1}
        listed = (await c.get(f"{BASE}/sources", headers=w["h"][OPERATOR])).json()["sources"]
        face = next(s for s in listed if s["kind"] == "FACE_MATCH")
        assert face["may_search"] is True and face["answers"]["person"] is False


# ─── D. The filters, and a typed phrase ──────────────────────────────────────

async def test_each_filter_narrows_and_says_which_sources_it_left_out():
    w = await _world()
    seed = await _seed(w)
    guard = str(w["users"][GUARD])
    async with _client() as c:
        async def ask(**what) -> dict:
            return await _search(c, w, ADMIN, _window(seed, **what))

        camera = await ask(camera_ids=[str(seed["camera"])])
        assert camera["found"] == {"ALERT": 1, "INCIDENT": 1, "PLATE_READ": 1, "FACE_MATCH": 1, "ACCESS": 1}
        assert _left_out(camera)["VISITOR"] == "These records are not tied to a camera."
        assert (await ask(camera_ids=[str(uuid.uuid4())]))["total"] == 0

        assert (await ask(plate="sga 1234b"))["found"] == {"PLATE_READ": 1, "VISITOR": 1}
        assert (await ask(plate="SGA*"))["found"] == {"PLATE_READ": 1, "VISITOR": 1}
        assert (await ask(plate="SGA1234"))["total"] == 0, "a plate is matched whole unless a * is written"
        assert (await ask(person="wei ming"))["found"] == {"VISITOR": 1}
        assert (await ask(person="RAJ"))["found"] == {"ACCESS": 1}
        assert (await ask(staff_user_id=guard))["found"] == {
            "ACCESS": 1, "VISITOR": 1, "OCCURRENCE": 1, "PATROL_SCAN": 1, "MAN_DOWN": 1}
        assert (await ask(staff_user_id=str(w["users"][OPERATOR])))["found"] == {"INCIDENT": 1}
        assert (await ask(severities=["critical"]))["found"] == {"ALERT": 1, "MAN_DOWN": 1}
        assert (await ask(risk_levels=["HIGH"]))["found"] == {"DRONE": 1, "SITUATION": 1}
        assert (await ask(event_types=["intrusion"]))["found"] == {"ALERT": 1, "INCIDENT": 1, "DRONE": 1}
        assert (await ask(event_types=["Denied"]))["found"] == {"ACCESS": 1}
        assert (await ask(text="Blue Lorry"))["found"] == {"OCCURRENCE": 1}
        assert (await ask(text="100%_"))["total"] == 0, "what is typed is looked for as written, not as a pattern"
        assert (await ask(site_ids=[str(w["site_b"])]))["total"] == 0
        assert (await ask(kinds=["ALARM", "SENSOR"]))["found"] == {"ALARM": 1, "SENSOR": 1}
        both = await ask(kinds=["PLATE_READ", "VISITOR", "ALERT"], plate="SGA1234B")
        assert both["found"] == {"PLATE_READ": 1, "VISITOR": 1} and set(_left_out(both)) == {"ALERT"}


async def test_a_search_that_cannot_be_is_refused_with_the_reason():
    w = await _world()
    now = datetime.now(timezone.utc)
    async with _client() as c:
        for body, why in (
            ({"from": _iso(now - timedelta(days=100)), "to": _iso(now)}, "at most 92 days"),
            ({"from": _iso(now), "to": _iso(now - timedelta(hours=1))}, "ends before it starts"),
            ({"from": "2026-10-05T01:00:00"}, "needs a time zone"),
            ({"kinds": ["EVERYTHING"]}, "Unknown kind of record"),
            ({"severities": ["severe"]}, "Unknown severity"),
            ({"plate": "S"}, "at least three"),
        ):
            r = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json=body)
            assert r.status_code == 422 and why in r.text, (body, r.text)
        for body in ({"limit": 500}, {"offset": 999999}, {"nonsense": True}, {"phrase": "x" * 400}):
            assert (await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json=body)).status_code == 422, body
        empty = await _search(c, w, ADMIN, {})
        assert empty["total"] == 0 and empty["query"]["kinds"] == []
        span = datetime.fromisoformat(empty["query"]["to"]) - datetime.fromisoformat(empty["query"]["from"])
        assert span == timedelta(hours=24), "with no period, the last day"


async def test_a_typed_phrase_becomes_the_search_and_says_what_it_made_of_the_words():
    w = await _world()
    seed = await _seed(w)
    async with _client() as c:
        found = await _search(c, w, ADMIN, {"phrase": "Show me vehicles at gate camera a in the last 3 hours"})
        assert found["found"] == {"PLATE_READ": 1}
        assert found["query"]["kinds"] == ["PLATE_READ"] and found["query"]["camera_ids"] == [str(seed["camera"])]
        said = found["phrase"]
        assert said["text"] == "Show me vehicles at gate camera a in the last 3 hours"
        assert {u["field"] for u in said["understood"]} == {"kind", "camera", "period"}
        assert said["not_understood"] == [] and said["assumed"] == []

        plate = await _search(c, w, ADMIN, {"phrase": "where was SGA1234B at Factory A today, suspicious"})
        assert plate["query"]["plate"] == "SGA1234B" and plate["query"]["site_ids"] == [str(w["site_a"])]
        assert plate["phrase"]["not_understood"] == ["suspicious"], "said, not used, and reported"

        # A filter given beside the phrase replaces what the phrase said about the same thing.
        corrected = await _search(c, w, ADMIN, {"phrase": "vehicles in the last 3 hours", "kinds": ["VISITOR"]})
        assert corrected["query"]["kinds"] == ["VISITOR"] and corrected["found"] == {"VISITOR": 1}

        assumed = await _search(c, w, ADMIN, {"phrase": "critical alerts"})
        assert assumed["phrase"]["assumed"] == ["No period was given, so the last 24 hours were searched."]
        assert assumed["found"] == {"ALERT": 1}

        refused = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"phrase": "anything odd going on"})
        assert refused.status_code == 422
        detail = refused.json()["detail"]
        assert detail["not_understood"] == ["odd", "going"] and "Nothing in that was understood" in detail["message"]
        future = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"phrase": "alerts last 300 days"})
        assert future.status_code == 422 and "at most 92 days" in future.json()["detail"]


async def test_a_phrase_can_only_name_a_place_the_caller_may_see():
    w = await _world()
    await _seed(w, site="site_a", tag="A")
    await _seed(w, site="site_b", tag="B")
    async with _client() as c:
        mine = await _search(c, w, SUPERVISOR, {"phrase": "alerts at gate camera a today"})
        assert len(mine["query"]["camera_ids"]) == 1 and not mine["phrase"]["not_understood"]
        other = await _search(c, w, SUPERVISOR, {"phrase": "alerts at gate camera b today"})
        assert other["query"]["camera_ids"] == [] and other["phrase"]["not_understood"] == ["gate", "b"], \
            "the name of a camera at a site they are not assigned to is just words"
        assert (await _search(c, w, ADMIN, {"phrase": "alerts at gate camera b today"}))["query"]["camera_ids"]


async def test_the_screen_is_told_what_it_may_search_and_how_to_ask():
    w = await _world()
    async with _client() as c:
        r = await c.get(f"{BASE}/sources", headers=w["h"][VIEWER])
    assert r.status_code == 200
    answer = r.json()
    assert [s["kind"] for s in answer["sources"]] == list(sources.KINDS)
    assert all(s["may_search"] for s in answer["sources"]), "a viewer reads every one of these already"
    detection = next(s for s in answer["sources"] if s["kind"] == "DETECTION")
    assert detection["asked_for"] is True
    assert next(s for s in answer["sources"] if s["kind"] == "OCCURRENCE")["answers"]["camera"] is False
    assert (answer["max_days"], answer["default_hours"]) == (92, 24)
    assert set(answer["phrase"]["kinds"]) == EVERY_KIND and "not understood" in answer["phrase"]["limits"]


# ─── E. Where was this seen ──────────────────────────────────────────────────

async def _sightings(w: dict, plate: str = "SGA1234B") -> dict:
    """The same plate at three cameras, and the same watchlist entry at two."""
    t = w["tenant"]
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    cams = {k: uuid.uuid4() for k in ("gate", "yard", "far")}
    watch = uuid.uuid4()
    stmts = [
        ("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) VALUES (:i,:t,:s,'Trail Gate',1.3000,103.8000)",
         {"i": cams["gate"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) VALUES (:i,:t,:s,'Trail Yard',1.3009,103.8000)",
         {"i": cams["yard"], "t": t, "s": w["site_a"]}),
        ("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Trail Far')",
         {"i": cams["far"], "t": t, "s": w["site_b"]}),
        ("INSERT INTO face_watchlist_entries (id, tenant_id, person_name, list_type, embedding_v) "
         "VALUES (:i,:t,'Chan Mei Ling','block',CAST(:v AS vector))", {"i": watch, "t": t, "v": VECTOR}),
    ]
    reads = []
    for minutes, cam, read in ((0, "gate", plate), (4, "yard", plate), (5, "yard", "SJK9000Z"), (9, "yard", plate),
                               (30, "far", plate.lower())):
        rid = uuid.uuid4()
        at = base + timedelta(minutes=minutes)
        reads.append((rid, cam, read, at))
        stmts.append(("INSERT INTO detections (id, tenant_id, camera_id, module_type, detected_at) "
                      "VALUES (:i,:t,:c,'lpr',:at)", {"i": rid, "t": t, "c": cams[cam], "at": at}))
        stmts.append(("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, "
                      "    plate_confidence) VALUES (:i,:at,:t,:c,:p,0.9)",
                      {"i": rid, "at": at, "t": t, "c": cams[cam], "p": read}))
    faces = []
    for minutes, cam in ((1, "gate"), (6, "yard")):
        fid = uuid.uuid4()
        at = base + timedelta(minutes=minutes)
        faces.append((fid, cam, at))
        stmts.append(("INSERT INTO detections (id, tenant_id, camera_id, module_type, detected_at) "
                      "VALUES (:i,:t,:c,'face',:at)", {"i": fid, "t": t, "c": cams[cam], "at": at}))
        stmts.append(("INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id, matched_watchlist_id, "
                      "    match_confidence, watchlist_match) VALUES (:i,:at,:t,:c,:w,0.83,'block')",
                      {"i": fid, "at": at, "t": t, "c": cams[cam], "w": watch}))
    await _run(stmts)
    return {"cams": cams, "watch": watch, "reads": reads, "faces": faces, "base": base}


async def test_a_plate_is_followed_from_camera_to_camera_with_what_lies_between():
    w = await _world()
    s = await _sightings(w)
    async with _client() as c:
        r = await c.get(f"{BASE}/trail", headers=w["h"][ADMIN], params={"plate": "sga 1234-b"})
        assert r.status_code == 200, r.text
        trail = r.json()
        restricted = (await c.get(f"{BASE}/trail", headers=w["h"][SUPERVISOR], params={"plate": "SGA1234B"})).json()
    assert trail["subject"] == {"kind": "VEHICLE", "plate": "SGA1234B"}
    assert [x["camera_name"] for x in trail["sightings"]] == ["Trail Gate", "Trail Yard", "Trail Yard", "Trail Far"]
    assert [x["id"] for x in trail["sightings"]] == [str(r[0]) for r in s["reads"] if r[2].upper() == "SGA1234B"]
    assert trail["summary"] == {"sightings": 4, "first_at": trail["sightings"][0]["occurred_at"],
                                "last_at": trail["sightings"][-1]["occurred_at"], "cameras": 3, "sites": 2}
    first, second, third = trail["legs"]
    assert (first["seconds"], first["same_camera"], first["same_site"]) == (240, False, True)
    assert 95 <= first["metres"] <= 105, "about a hundred metres between the two cameras"
    assert (second["seconds"], second["metres"], second["same_camera"]) == (300, 0, True)
    assert (third["metres"], third["same_site"]) == (None, False), "no distance where a camera has no position"
    assert trail["complete"] is True and trail["basis"] == sources.PLATE_BASIS
    assert "not who was driving" in trail["basis"] and trail["not_followed"] == sources.NOT_FOLLOWED
    assert restricted["summary"]["sightings"] == 3 and restricted["summary"]["sites"] == 1, \
        "the sighting at a site they are not assigned to is not theirs to see"


async def test_a_watchlist_entry_is_followed_and_nobody_else_can_be():
    w = await _world()
    s = await _sightings(w)
    params = {"watchlist_entry_id": str(s["watch"])}
    async with _client() as c:
        trail = (await c.get(f"{BASE}/trail", headers=w["h"][ADMIN], params=params)).json()
        unnamed = (await c.get(f"{BASE}/trail", headers=w["h"][OPERATOR], params=params)).json()
        nobody = (await c.get(f"{BASE}/trail", headers=w["h"][ADMIN],
                              params={"watchlist_entry_id": str(uuid.uuid4())})).json()
        for bad, why in (({}, "one of them"), ({"plate": "SGA1234B", **params}, "one of them"),
                         ({"plate": "SGA*"}, "Give it in full"),
                         ({"plate": "SGA1234B", "from": "2026-01-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
                          "at most 92 days")):
            r = await c.get(f"{BASE}/trail", headers=w["h"][ADMIN], params=bad)
            assert r.status_code == 422 and why in r.text, (bad, r.text)
    assert trail["subject"] == {"kind": "PERSON", "watchlist_entry_id": str(s["watch"]), "name": "Chan Mei Ling"}
    assert [x["camera_name"] for x in trail["sightings"]] == ["Trail Gate", "Trail Yard"]
    assert trail["legs"][0]["seconds"] == 300 and trail["sightings"][0]["confidence"] == 0.83
    assert trail["basis"] == sources.FACE_BASIS and "not an identification" in trail["basis"]
    assert "cannot be followed" in trail["not_followed"]
    assert unnamed["subject"]["name"] is None and unnamed["summary"]["sightings"] == 2, \
        "an operator sees where the entry was matched, and not whose it is"
    assert "Chan" not in str(unnamed)
    assert nobody["summary"] == {"sightings": 0, "first_at": None, "last_at": None, "cameras": 0, "sites": 0}


def test_what_lies_between_sightings_is_arithmetic():
    at = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)
    seen = [
        {"id": "1", "occurred_at": at, "camera_id": "a", "site_id": "s", "latitude": 1.3, "longitude": 103.8},
        {"id": "2", "occurred_at": at + timedelta(seconds=90), "camera_id": "b", "site_id": "s", "latitude": 1.3,
         "longitude": 103.801},
        {"id": "3", "occurred_at": at + timedelta(hours=2), "camera_id": None, "site_id": None, "latitude": None,
         "longitude": None},
    ]
    one, two = sources.legs(seen)
    assert (one["from_id"], one["to_id"], one["seconds"], one["same_camera"], one["same_site"]) == \
        ("1", "2", 90, False, True)
    assert 105 <= one["metres"] <= 115
    assert (two["seconds"], two["metres"], two["same_camera"], two["same_site"]) == (7110, None, False, False)
    assert sources.legs(seen[:1]) == [] and sources.legs([]) == []
    assert sources.summarise(seen) == {"sightings": 3, "first_at": at, "last_at": at + timedelta(hours=2),
                                      "cameras": 2, "sites": 1}


# ─── F. On the record, and when a search cannot answer ───────────────────────

async def _audit(w: dict, action: str) -> list[dict]:
    rows = await _sql("SELECT user_id, action, resource_type, resource_id, detail, row_hash FROM audit_logs "
                      " WHERE tenant_id = :t AND action = :a ORDER BY created_at, id",
                      {"t": w["tenant"], "a": action})
    return [{**r, "detail": r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"])}
            for r in rows]


async def test_every_search_is_on_the_record_with_what_was_asked_and_not_what_was_found():
    w = await _world()
    seed = await _seed(w)
    s = await _sightings(w)
    async with _client() as c:
        await _search(c, w, OPERATOR, _window(seed, plate="SGA1234B", person=None))
        await _search(c, w, OPERATOR, {"phrase": "visitors named Tan Wei Ming today"})
        await c.get(f"{BASE}/trail", headers=w["h"][ADMIN], params={"watchlist_entry_id": str(s["watch"])})
        await c.post(f"{BASE}/search", headers=w["h"][OPERATOR], json={"kinds": ["EVERYTHING"]})
    searches = await _audit(w, "investigation.search")
    assert len(searches) == 2, "the refused search read nothing and is not there"
    first, second = (r["detail"] for r in searches)
    assert searches[0]["user_id"] == w["users"][OPERATOR] and searches[0]["resource_type"] == "investigation_search"
    assert first["actor_role"] == OPERATOR and first["source"] == "user" and first["result"] == "ok"
    assert first["query"]["plate"] == "SGA1234B" and "person" not in first["query"], "only what was asked"
    assert first["found"] == 2 and first["searched"] == ["PLATE_READ", "VISITOR"] and first["offset"] == 0
    assert second["query"]["person"] == "Tan Wei Ming" and second["phrase"] == "visitors named Tan Wei Ming today"
    for detail in (first, second):
        assert "items" not in detail and str(seed["ids"]["VISITOR"]) not in str(detail)
    (trail,) = await _audit(w, "investigation.trail")
    assert trail["user_id"] == w["users"][ADMIN]
    assert trail["detail"]["subject"] == {"kind": "PERSON", "watchlist_entry_id": str(s["watch"]), "name": None}
    assert trail["detail"]["found"] == 2 and "Chan" not in str(trail["detail"])


async def test_a_search_that_does_not_answer_in_time_is_stopped_and_the_session_is_left_as_it_was(monkeypatch):
    w = await _world()
    monkeypatch.setattr(sources, "TIMEOUT_MS", 60)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
        before = (await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar()

        async def slow():
            return (await db.execute(text("SELECT pg_sleep(2)"))).scalar()

        with pytest.raises(sources.TooWide):
            await sources._bounded(db, slow)
        assert (await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar() == before
        assert (await db.execute(text("SELECT current_setting('app.current_tenant')"))).scalar() == str(w["tenant"])
        assert (await db.execute(text("SELECT count(*) FROM sites"))).scalar() == 2, "still this organisation's"

        async def quick():
            return (await db.execute(text("SELECT 7"))).scalar()

        assert await sources._bounded(db, quick) == 7
        assert (await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar() == before


async def test_a_search_that_ran_out_of_time_tells_the_person_to_narrow_it(monkeypatch):
    w = await _world()

    async def too_wide(*args, **kwargs):
        raise sources.TooWide()

    monkeypatch.setattr(sources, "_bounded", too_wide)
    async with _client() as c:
        r = await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={"kinds": ["ALERT"]})
        t = await c.get(f"{BASE}/trail", headers=w["h"][ADMIN], params={"plate": "SGA1234B"})
    assert r.status_code == 422 and "Narrow the period" in r.json()["detail"]
    assert t.status_code == 422 and "Narrow the period" in t.json()["detail"]
    assert await _audit(w, "investigation.search") == [], "nothing was read, so nothing is recorded as searched"


async def test_one_person_cannot_search_without_pause(monkeypatch):
    from limits import parse

    w = await _world()
    monkeypatch.setattr(api, "SEARCH_LIMIT", parse("3/minute"))
    async with _client() as c:
        codes = [(await c.post(f"{BASE}/search", headers=w["h"][VIEWER], json={})).status_code for _ in range(5)]
        other = (await c.post(f"{BASE}/search", headers=w["h"][ADMIN], json={})).status_code
    assert codes == [200, 200, 200, 429, 429] and other == 200, "counted per person, not per address"
