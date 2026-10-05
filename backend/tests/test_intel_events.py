"""AI security intelligence, phase 2: every source in one shape.

  A — Each source's mapping, with nothing running
  B — Reading the sources: once each, from where it should, and never the wrong ones
  C — Only for tenants that asked, and never another tenant's rows
  D — The runner records; it cannot act
  E — The API: who may read, which sites, and the switch
  F — The schema

Sections B and C run as the application's own database role, on two
organisations, because that is how the runner runs: this project's other tests
connect as a superuser, which no tenant policy applies to.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.services import intel_config, intel_events as events, intel_runner as runner
from tests.test_drone_api import (ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run,
                                  _sql, _world as _roles_world)

NOW = datetime.now(timezone.utc)
CLIENT_ROLE, SUPER_ADMIN = 7, 1


def _ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


# ─── Fixtures ────────────────────────────────────────────────────────────────

async def _world(*, enabled: bool = True) -> dict:
    """A tenant with two sites, a user per role, a camera on each site — and
    the feature on, unless a test is about it being off."""
    w = await _roles_world(licensed=False)
    w["cam_a"] = await _camera(w, "site_a", "Gate 1", 1.3001, 103.8001)
    w["cam_b"] = await _camera(w, "site_b", "Dock 4", 1.3501, 103.8501)
    if enabled:
        await _switch(w, True)
    return w


async def _switch(w: dict, on: bool) -> None:
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
               "VALUES (:t,'intel.enabled',CAST(:v AS jsonb),:u) "
               "ON CONFLICT (tenant_id, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
               {"t": w["tenant"], "v": json.dumps(on), "u": w["users"][ADMIN]})


async def _camera(w: dict, site: str, name: str, lat: float | None = None, lon: float | None = None) -> uuid.UUID:
    cid = uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, name, location, site_id, latitude, longitude) "
               "VALUES (:i,:t,:n,'North fence',:s,:la,:lo)",
               {"i": cid, "t": w["tenant"], "n": name, "s": w[site], "la": lat, "lo": lon})
    return cid


async def _detection(w: dict, camera, module: str, confidence: float = 0.91, at: datetime | None = None):
    did, at = uuid.uuid4(), at or _ago(seconds=30)
    await _sql("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
               "VALUES (:i,:t,:c,:m,:f,:at)",
               {"i": did, "t": w["tenant"], "c": camera, "m": module, "f": confidence, "at": at})
    return did, at


async def _alert(w: dict, module: str, *, camera="cam_a", site="site_a", severity: str = "high",
                 title: str = "Something seen", code: str | None = None, at: datetime | None = None,
                 detection=None, params: dict | None = None) -> uuid.UUID:
    aid = uuid.uuid4()
    await _sql("INSERT INTO alerts (id, tenant_id, detection_id, camera_id, site_id, module_type, severity, "
               "    alert_code, message_params, title, created_at) "
               "VALUES (:i,:t,:d,:c,:s,:m,:sev,:code,CAST(:p AS jsonb),:title,:at)",
               {"i": aid, "t": w["tenant"], "d": detection, "c": w[camera] if camera else None,
                "s": w[site] if site else None, "m": module, "sev": severity, "code": code,
                "p": json.dumps(params) if params is not None else None, "title": title,
                "at": at or _ago(seconds=20)})
    return aid


async def _events(w: dict, **where) -> list[dict]:
    clause = "".join(f" AND {k} = :{k}" for k in where)
    rows = await _sql(f"SELECT * FROM security_events WHERE tenant_id = :t{clause} ORDER BY occurred_at, id",
                      {"t": w["tenant"], **where})
    return [dict(r) for r in rows]


async def _read(w: dict, now: datetime = NOW, batch: int = events.BATCH) -> dict:
    return await events.ingest_tenant(AsyncSessionLocal, w["tenant"], now=now, batch=batch)


def _attrs(event: dict) -> dict:
    a = event["attributes"]
    return json.loads(a) if isinstance(a, str) else a


# ─── A. Each source's mapping ────────────────────────────────────────────────

ALERT = {"id": uuid.uuid4(), "module_type": "intrusion", "severity": "high", "alert_code": "intrusion.zone_breach",
         "title": "Restricted zone breach", "created_at": NOW, "site_id": uuid.uuid4(), "camera_id": uuid.uuid4(),
         "detection_id": uuid.uuid4(), "camera_name": "Gate 1", "camera_location": "North fence",
         "camera_latitude": 1.3, "camera_longitude": 103.8, "detection_confidence": 0.8731}


def test_a_cctv_alert_becomes_a_cctv_event_with_the_models_confidence_untouched():
    zone = uuid.uuid4()
    n = events.from_alert({**ALERT, "zone_id": zone, "zone_name": "Fuel store", "dwell_time_seconds": 14})
    assert (n.source_type, n.source_table, n.source_id) == ("CCTV_AI", "alerts", ALERT["id"])
    assert n.event_type == "intrusion.zone_breach" and n.severity == "high"
    assert n.subject_kind == "PERSON" and n.subject_ref is None and n.subject_verdict is None
    assert n.confidence == 0.8731, "confidence is the detection's, never adjusted"
    assert (n.latitude, n.longitude) == (1.3, 103.8) and n.location_label == "Gate 1 — North fence"
    assert n.attributes == {"module_type": "intrusion", "zone_id": str(zone), "zone_name": "Fuel store",
                            "dwell_time_seconds": 14.0}
    assert n.alert_id == ALERT["id"] and n.detection_id == ALERT["detection_id"]


def test_a_plate_read_names_the_vehicle_and_what_the_watchlist_said():
    n = events.from_alert({**ALERT, "module_type": "lpr", "alert_code": "lpr.blocklist_match",
                           "plate_number": "SGX1234A", "plate_confidence": 0.93, "plate_match": "block",
                           "direction": "in", "vehicle_type": "van"})
    assert n.source_type == "LPR" and n.subject_kind == "VEHICLE"
    assert n.subject_ref == "SGX1234A" and n.subject_verdict == "BLOCK"
    assert n.attributes["plate_confidence"] == 0.93 and n.attributes["vehicle_type"] == "van"


def test_a_face_that_matched_nobody_is_unknown_not_unauthorised():
    known = uuid.uuid4()
    matched = events.from_alert({**ALERT, "module_type": "face", "matched_watchlist_id": known,
                                 "match_confidence": 0.71, "face_match": "allow"})
    nobody = events.from_alert({**ALERT, "module_type": "face", "matched_watchlist_id": None, "face_match": None})
    assert matched.source_type == "FACE_RECOGNITION" and matched.subject_kind == "PERSON"
    assert matched.subject_ref == str(known) and matched.subject_verdict == "ALLOW"
    assert nobody.subject_ref is None, "no identity is invented for an unmatched face"
    assert nobody.subject_verdict == "UNKNOWN"


@pytest.mark.parametrize("module, source", [
    ("ppe", "CCTV_AI"), ("crowd", "CCTV_AI"), ("fire_smoke", "CCTV_AI"), ("weapon", "CCTV_AI"),
    ("behavior", "CCTV_AI"), ("tampering", "CCTV_AI"), ("abandoned", "CCTV_AI"), ("fall", "CCTV_AI"),
    ("access", "ACCESS_CONTROL"), ("alarm", "ALARM"), ("iot", "SENSOR"), ("gps", "SENSOR"),
    ("drone_patrol", "DRONE_PATROL"), ("visitor_overstay", "OTHER"), ("something_new", "OTHER"),
])
def test_every_alert_module_has_a_source_and_an_unknown_one_is_kept_as_other(module, source):
    n = events.from_alert({**ALERT, "module_type": module, "alert_code": None})
    assert n.source_type == source
    assert n.event_type == f"{module}.alert", "no alert code: the module names the event"


def test_an_object_or_scene_detection_has_no_subject():
    for module in ("fire_smoke", "abandoned", "tampering", "crowd", "weapon", "alarm", "iot"):
        assert events.from_alert({**ALERT, "module_type": module}).subject_kind == "NONE", module


def test_an_alarm_panel_alert_has_no_confidence_and_none_is_made_up():
    n = events.from_alert({**ALERT, "module_type": "alarm", "detection_id": None, "detection_confidence": None,
                           "camera_id": None, "camera_name": None, "camera_location": None,
                           "camera_latitude": None, "camera_longitude": None})
    assert n.confidence is None and n.camera_id is None and n.location_label is None
    assert n.latitude is None and n.longitude is None


def test_an_alarms_place_is_its_panel_and_zone_not_the_camera_its_alert_was_hung_on():
    arbitrary, zone_cam, panel_site = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    hung = {**ALERT, "module_type": "alarm", "alert_code": "alarm.zone_alarm", "camera_id": arbitrary,
            "camera_name": "Some other camera", "detection_id": None, "detection_confidence": None,
            "alarm_site_id": panel_site, "alarm_panel_name": "Panel 1", "alarm_zone_number": 3}
    no_camera = events.from_alert({**hung, "alarm_zone_name": "Rear PIR", "alarm_camera_id": None})
    assert no_camera.site_id == panel_site and no_camera.camera_id is None, "the arbitrary camera is not taken"
    assert no_camera.latitude is None and no_camera.location_label == "Rear PIR"
    assert no_camera.attributes == {"module_type": "alarm", "panel": "Panel 1", "zone_number": 3}

    linked = events.from_alert({**hung, "alarm_zone_name": "Rear PIR", "alarm_camera_id": zone_cam,
                                "alarm_camera_name": "Rear gate", "alarm_camera_latitude": 1.35,
                                "alarm_camera_longitude": 103.85})
    assert linked.camera_id == zone_cam and (linked.latitude, linked.longitude) == (1.35, 103.85)
    assert linked.location_label == "Rear PIR — Rear gate"
    panel_only = events.from_alert({**hung, "alarm_zone_name": None, "alarm_camera_id": None})
    assert panel_only.location_label == "Panel 1"


def test_a_sensors_place_is_the_sensor_and_a_trackers_is_where_it_reported():
    site = uuid.uuid4()
    sensor = events.from_alert({**ALERT, "module_type": "iot", "alert_code": "iot.threshold_breach",
                                "camera_id": None, "site_id": None, "camera_site_id": None, "camera_name": None,
                                "camera_location": None, "camera_latitude": None, "camera_longitude": None,
                                "sensor_site_id": site, "sensor_name": "Cold room 2", "sensor_location": "Level 1",
                                "sensor_type": "temperature"})
    assert sensor.site_id == site and sensor.camera_id is None
    assert sensor.location_label == "Cold room 2 — Level 1" and sensor.attributes["sensor_type"] == "temperature"

    tracker = events.from_alert({**ALERT, "module_type": "gps", "alert_code": "gps.geofence_exit",
                                 "gps_params": json.dumps({"vehicle_id": "v-1", "geofence": "Depot",
                                                           "lat": 1.29, "lon": 103.77, "speed": 12})})
    assert tracker.site_id is None and tracker.camera_id is None, "a vehicle on the road is at no site"
    assert (tracker.latitude, tracker.longitude) == (1.29, 103.77) and tracker.location_label == "Depot"
    assert tracker.attributes == {"module_type": "gps", "vehicle_id": "v-1", "geofence": "Depot"}


def test_a_strange_severity_does_not_stop_an_event_being_read():
    assert events.from_alert({**ALERT, "severity": "URGENT"}).severity == "medium"
    assert events.from_alert({**ALERT, "severity": "CRITICAL"}).severity == "critical"


def test_a_drone_sighting_carries_the_drones_own_assessment_as_evidence():
    e = {"id": uuid.uuid4(), "module_type": "intrusion", "detected_at": NOW, "risk_level": "HIGH",
         "risk_score": 82, "ai_confidence": 0.94, "verification_state": "VERIFIED", "site_id": uuid.uuid4(),
         "drone_id": uuid.uuid4(), "session_id": uuid.uuid4(), "mission_id": None, "alert_id": uuid.uuid4(),
         "incident_id": None, "detection_id": None, "label": "Person at the fuel store",
         "drone_latitude": 1.30, "drone_longitude": 103.80, "estimated_latitude": 1.3004,
         "estimated_longitude": 103.8004, "zone_name": "Fuel store", "zone_type": "RESTRICTED",
         "detection_count": 5, "observed_seconds": 9.5, "location_method": "PROJECTED", "attributes": "{}"}
    n = events.from_drone_event(e)
    assert (n.source_type, n.source_table, n.event_type) == ("DRONE_PATROL", "drone_events", "drone.intrusion")
    assert n.severity == "high" and n.confidence == 0.94
    assert (n.latitude, n.longitude) == (1.3004, 103.8004), "where the subject was, when the drone worked it out"
    assert n.attributes["drone_risk_score"] == 82.0 and n.attributes["drone_risk_level"] == "HIGH"
    assert n.attributes["verification_state"] == "VERIFIED" and n.attributes["detection_count"] == 5
    assert "risk_score" not in n.attributes, "the platform's own risk is decided later and kept elsewhere"

    over_the_drone = events.from_drone_event({**e, "estimated_latitude": None, "estimated_longitude": None})
    assert (over_the_drone.latitude, over_the_drone.longitude) == (1.30, 103.80)


def test_a_guard_sos_takes_its_place_from_the_guard_not_from_the_incidents_camera():
    guard, site = uuid.uuid4(), uuid.uuid4()
    n = events.from_guard_sos({
        "id": uuid.uuid4(), "severity": "critical", "created_at": NOW, "shift_site_id": site,
        "shift_site_name": "Factory A",
        "message_params": json.dumps({"guard_user_id": str(guard), "guard_name": "Tan Wei Ming",
                                      "latitude": 1.3002, "longitude": 103.8003})})
    assert n.source_type == "GUARD" and n.event_type == "guard.sos" and n.severity == "critical"
    assert n.site_id == site and n.camera_id is None
    assert (n.latitude, n.longitude) == (1.3002, 103.8003)
    assert n.attributes == {"guard_user_id": str(guard)}
    assert "Tan" not in n.title and "Tan" not in json.dumps(n.attributes), "an identifier, never a name"


def test_a_virtual_patrol_exception_is_an_event_on_its_camera():
    cam, site, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = {"id": uuid.uuid4(), "answered_at": NOW, "answered_by_user_id": uuid.uuid4(), "incident_id": None,
           "exception_reason": "Gate left open", "question_text": "Is the rear gate closed?",
           "failure_action": "CREATE_INCIDENT", "camera_id": cam, "camera_name": "Rear gate",
           "session_id": session, "site_id": site, "patrol_number": "VP-0042",
           "camera_latitude": 1.31, "camera_longitude": 103.81}
    n = events.from_patrol_exception(row)
    assert (n.source_type, n.event_type) == ("VIRTUAL_PATROL", "vpatrol.exception")
    assert n.camera_id == cam and n.site_id == site and n.severity == "high"
    assert n.attributes["patrol_number"] == "VP-0042" and n.attributes["exception_reason"] == "Gate left open"
    assert events.from_patrol_exception({**row, "failure_action": "NONE"}).severity == "medium"


def test_a_camera_going_dark_is_a_system_event():
    n = events.from_camera_health({"id": uuid.uuid4(), "camera_id": uuid.uuid4(), "event_type": "stream_disconnected",
                                   "detail": "no frames for 30 s", "occurred_at": NOW, "camera_name": "Gate 1",
                                   "camera_location": None, "camera_site_id": uuid.uuid4(),
                                   "camera_latitude": None, "camera_longitude": None})
    assert (n.source_type, n.event_type, n.severity) == ("SYSTEM", "camera.stream_disconnected", "low")
    assert n.title == "Camera stopped sending: Gate 1"


def test_every_source_type_the_mappings_can_produce_is_one_the_table_accepts():
    produced = set(events.ALERT_SOURCE.values()) | {"OTHER", "DRONE_PATROL", "GUARD", "VIRTUAL_PATROL", "SYSTEM"}
    assert produced <= set(events.SOURCE_TYPES)
    assert produced == set(events.SOURCE_TYPES), "a source type nothing produces is a promise nothing keeps"


# ─── B. Reading the sources ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_reader_is_not_a_superuser():
    """Everything below proves nothing if this connection ignores tenant policies."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).first()
    assert row.rolsuper is False and row.rolbypassrls is False


@pytest.mark.asyncio
async def test_alerts_from_three_modules_are_read_with_what_their_detections_add():
    w = await _world()
    d_lpr, at = await _detection(w, w["cam_a"], "lpr", 0.88)
    await _sql("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, "
               "    plate_confidence, watchlist_match, direction) VALUES (:d,:at,:t,:c,'SGX1234A',0.93,'block','in')",
               {"d": d_lpr, "at": at, "t": w["tenant"], "c": w["cam_a"]})
    d_face, at = await _detection(w, w["cam_a"], "face", 0.97)
    await _sql("INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id) VALUES (:d,:at,:t,:c)",
               {"d": d_face, "at": at, "t": w["tenant"], "c": w["cam_a"]})
    zone = uuid.uuid4()
    await _sql("INSERT INTO restricted_zones (id, tenant_id, camera_id, name, polygon) "
               "VALUES (:i,:t,:c,'Fuel store','[]'::jsonb)", {"i": zone, "t": w["tenant"], "c": w["cam_a"]})
    d_int, at = await _detection(w, w["cam_a"], "intrusion", 0.81)
    await _sql("INSERT INTO intrusion_events (detection_id, detected_at, tenant_id, camera_id, zone_id, person_bbox, "
               "    dwell_time_seconds) VALUES (:d,:at,:t,:c,:z,'{}'::jsonb,14)",
               {"d": d_int, "at": at, "t": w["tenant"], "c": w["cam_a"], "z": zone})
    a_lpr = await _alert(w, "lpr", detection=d_lpr, code="lpr.blocklist_match", at=_ago(seconds=50))
    a_face = await _alert(w, "face", detection=d_face, at=_ago(seconds=40))
    a_int = await _alert(w, "intrusion", detection=d_int, code="intrusion.zone_breach", at=_ago(seconds=30))

    counts = await _read(w)
    assert counts["alerts"] == 3 and all(n >= 0 for n in counts.values()), counts

    by = {e["source_id"]: e for e in await _events(w)}
    lpr, face, intr = by[a_lpr], by[a_face], by[a_int]
    assert (lpr["source_type"], lpr["subject_kind"], lpr["subject_ref"], lpr["subject_verdict"]) == \
           ("LPR", "VEHICLE", "SGX1234A", "BLOCK")
    assert float(lpr["confidence"]) == 0.88 and _attrs(lpr)["plate_confidence"] == 0.93
    assert (face["source_type"], face["subject_ref"], face["subject_verdict"]) == ("FACE_RECOGNITION", None, "UNKNOWN")
    assert (intr["source_type"], intr["event_type"]) == ("CCTV_AI", "intrusion.zone_breach")
    assert _attrs(intr)["zone_name"] == "Fuel store" and _attrs(intr)["zone_id"] == str(zone)
    for e in (lpr, face, intr):
        assert e["site_id"] == w["site_a"] and e["camera_id"] == w["cam_a"] and e["alert_id"] == e["source_id"]
        assert e["location_label"] == "Gate 1 — North fence" and float(e["latitude"]) == 1.3001
        assert e["status"] == "NEW"


@pytest.mark.asyncio
async def test_reading_twice_adds_nothing_and_changes_no_alert():
    w = await _world()
    aid = await _alert(w, "intrusion")
    before = await _sql("SELECT status, severity, title, acknowledged_at, correlation_id FROM alerts WHERE id = :i",
                        {"i": aid})
    assert (await _read(w))["alerts"] == 1
    assert (await _read(w))["alerts"] == 0
    assert (await _read(w, now=NOW + timedelta(seconds=30)))["alerts"] == 0
    assert len(await _events(w)) == 1
    after = await _sql("SELECT status, severity, title, acknowledged_at, correlation_id FROM alerts WHERE id = :i",
                       {"i": aid})
    assert [dict(r) for r in after] == [dict(r) for r in before], "the source is read, never written"


@pytest.mark.asyncio
async def test_workforce_alerts_are_left_to_their_own_screens():
    w = await _world()
    await _alert(w, "payroll", camera=None, site=None, title="Overtime cap: a guard")
    await _alert(w, "roster", camera=None, site=None, title="Rest day breach")
    kept = await _alert(w, "visitor_overstay", camera=None, title="Visitor overstayed")
    assert (await _read(w))["alerts"] == 1
    assert [e["source_id"] for e in await _events(w)] == [kept]
    assert (await _events(w))[0]["source_type"] == "OTHER"


@pytest.mark.asyncio
async def test_a_drone_event_is_read_once_verified_and_its_own_alert_is_not_read_as_a_second_event():
    w = await _world()
    seen, verified = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, risk_score, "
         "    ai_confidence, verification_state, created_at) "
         "VALUES (:i,:t,:s,'intrusion',:at,'LOW',20,0.6,'OBSERVING',:at)",
         {"i": seen, "t": w["tenant"], "s": w["site_a"], "at": _ago(seconds=40)}),
        ("INSERT INTO drone_events (id, tenant_id, site_id, module_type, detected_at, risk_level, risk_score, "
         "    ai_confidence, verification_state, verified_at, created_at) "
         "VALUES (:i,:t,:s,'intrusion',:at,'HIGH',82,0.94,'VERIFIED',:v,:at)",
         {"i": verified, "t": w["tenant"], "s": w["site_a"], "at": _ago(seconds=40), "v": _ago(seconds=10)}),
    ])
    await _alert(w, "drone_patrol", camera=None, code="drone.event", params={"event_id": str(verified)})
    flight = await _alert(w, "drone_patrol", camera=None, code="drone.comms_lost", params={"session_id": "x"})

    counts = await _read(w)
    assert counts["drone_events"] == 1 and counts["alerts"] == 1, counts
    got = {(e["source_table"], e["source_id"]) for e in await _events(w)}
    assert got == {("drone_events", verified), ("alerts", flight)}

    # The sighting that was only being observed is verified later, and read then.
    await _sql("UPDATE drone_events SET verification_state = 'VERIFIED', verified_at = :v WHERE id = :i",
               {"i": seen, "v": NOW + timedelta(seconds=20)})
    assert (await _read(w, now=NOW + timedelta(seconds=30)))["drone_events"] == 1


@pytest.mark.asyncio
async def test_a_guard_sos_a_patrol_exception_and_a_dark_camera_are_read():
    w = await _world()
    guard = w["users"][GUARD]
    sos = uuid.uuid4()
    await _run([
        ("INSERT INTO shifts (tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, actual_start, status) "
         "VALUES (:t,:s,:g,:a,:b,:a,'active')",
         {"t": w["tenant"], "s": w["site_b"], "g": guard, "a": _ago(hours=2), "b": NOW + timedelta(hours=6)}),
        # Hung on site A's camera, as the SOS code does; the guard is at site B.
        ("INSERT INTO incidents (id, tenant_id, camera_id, title, severity, alert_code, message_params, "
         "    is_auto_created, created_at) "
         "VALUES (:i,:t,:c,'GUARD SOS — Tan Wei Ming','critical','guard.sos',CAST(:p AS jsonb),TRUE,:at)",
         {"i": sos, "t": w["tenant"], "c": w["cam_a"], "at": _ago(seconds=25),
          "p": json.dumps({"guard_user_id": str(guard), "guard_name": "Tan Wei Ming",
                           "latitude": 1.3502, "longitude": 103.8502})}),
    ])
    session, scam, question, answer, health = (uuid.uuid4() for _ in range(5))
    await _run([
        ("INSERT INTO virtual_patrol_sessions (id, tenant_id, site_id, patrol_number, schedule_name, scheduled_for) "
         "VALUES (:i,:t,:s,'VP-0042','Night round',:at)",
         {"i": session, "t": w["tenant"], "s": w["site_a"], "at": _ago(minutes=5)}),
        ("INSERT INTO virtual_patrol_session_cameras (id, tenant_id, session_id, camera_id, sequence_no, camera_name) "
         "VALUES (:i,:t,:s,:c,1,'Gate 1')", {"i": scam, "t": w["tenant"], "s": session, "c": w["cam_a"]}),
        ("INSERT INTO virtual_patrol_session_questions (id, tenant_id, session_camera_id, question_text, "
         "    question_type, is_required, sequence_no, failure_action) "
         "VALUES (:i,:t,:c,'Is the gate closed?','YES_NO',TRUE,1,'CREATE_INCIDENT')",
         {"i": question, "t": w["tenant"], "c": scam}),
        ("INSERT INTO virtual_patrol_session_answers (id, tenant_id, session_question_id, answered_by_user_id, "
         "    answer_text, answered_at, is_exception, exception_reason) "
         "VALUES (:i,:t,:q,:u,'NO',:at,TRUE,'Gate left open')",
         {"i": answer, "t": w["tenant"], "q": question, "u": w["users"][OPERATOR], "at": _ago(seconds=15)}),
        ("INSERT INTO camera_health_events (id, tenant_id, camera_id, event_type, detail, occurred_at) "
         "VALUES (:i,:t,:c,'stream_disconnected','no frames',:at)",
         {"i": health, "t": w["tenant"], "c": w["cam_a"], "at": _ago(seconds=10)}),
        ("INSERT INTO camera_health_events (tenant_id, camera_id, event_type, occurred_at) "
         "VALUES (:t,:c,'stream_reconnected',:at)", {"t": w["tenant"], "c": w["cam_a"], "at": _ago(seconds=5)}),
    ])

    counts = await _read(w)
    assert (counts["guard_sos"], counts["virtual_patrol"], counts["camera_health"]) == (1, 1, 1), counts
    by = {e["source_type"]: e for e in await _events(w)}
    g, v, s = by["GUARD"], by["VIRTUAL_PATROL"], by["SYSTEM"]
    assert g["source_id"] == sos and g["incident_id"] == sos
    assert g["site_id"] == w["site_b"] and g["camera_id"] is None, "the guard's site, not the incident's camera"
    assert g["title"] == "Guard SOS" and "Tan" not in json.dumps(_attrs(g))
    assert (v["source_id"], v["camera_id"], v["site_id"], v["severity"]) == (answer, w["cam_a"], w["site_a"], "high")
    assert s["source_id"] == health and s["event_type"] == "camera.stream_disconnected"


@pytest.mark.asyncio
async def test_an_alarm_and_a_sensor_are_placed_at_their_own_site_whatever_camera_the_alert_carries():
    w = await _world()
    panel, zone, bare_zone, sensor = (uuid.uuid4() for _ in range(4))
    # The panel is at site B. Both alerts are hung on site A's camera, as the
    # alarm code does when a zone has no camera of its own.
    with_camera = await _alert(w, "alarm", camera="cam_a", site=None, code="alarm.zone_alarm", title="Zone alarm")
    without = await _alert(w, "alarm", camera="cam_a", site=None, code="alarm.zone_alarm", title="Zone alarm 2")
    breach = await _alert(w, "iot", camera=None, site=None, code="iot.threshold_breach", title="Too warm",
                          params={"sensor_id": str(sensor), "value": 9.5})
    await _run([
        ("INSERT INTO alarm_panels (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'Panel B')",
         {"i": panel, "t": w["tenant"], "s": w["site_b"]}),
        ("INSERT INTO alarm_zones (id, tenant_id, panel_id, zone_number, name, linked_camera_id) "
         "VALUES (:i,:t,:p,1,'Dock PIR',:c)", {"i": zone, "t": w["tenant"], "p": panel, "c": w["cam_b"]}),
        ("INSERT INTO alarm_zones (id, tenant_id, panel_id, zone_number, name) VALUES (:i,:t,:p,2,'Roof hatch')",
         {"i": bare_zone, "t": w["tenant"], "p": panel}),
        ("INSERT INTO alarm_events (tenant_id, panel_id, zone_id, zone_number, event_type, alert_id) "
         "VALUES (:t,:p,:z,1,'zone_alarm',:a)", {"t": w["tenant"], "p": panel, "z": zone, "a": with_camera}),
        ("INSERT INTO alarm_events (tenant_id, panel_id, zone_id, zone_number, event_type, alert_id) "
         "VALUES (:t,:p,:z,2,'zone_alarm',:a)", {"t": w["tenant"], "p": panel, "z": bare_zone, "a": without}),
        ("INSERT INTO iot_sensors (id, tenant_id, site_id, name, sensor_type, location) "
         "VALUES (:i,:t,:s,'Cold room 2','temperature','Level 1')", {"i": sensor, "t": w["tenant"], "s": w["site_b"]}),
    ])
    assert (await _read(w))["alerts"] == 3
    by = {e["source_id"]: e for e in await _events(w)}
    assert (by[with_camera]["site_id"], by[with_camera]["camera_id"]) == (w["site_b"], w["cam_b"])
    assert by[with_camera]["location_label"] == "Dock PIR — Dock 4"
    assert (by[without]["site_id"], by[without]["camera_id"]) == (w["site_b"], None), \
        "site A's camera was only where the alert was hung"
    assert by[without]["location_label"] == "Roof hatch" and by[without]["source_type"] == "ALARM"
    assert (by[breach]["site_id"], by[breach]["camera_id"]) == (w["site_b"], None)
    assert by[breach]["location_label"] == "Cold room 2 — Level 1" and by[breach]["source_type"] == "SENSOR"


@pytest.mark.asyncio
async def test_switching_on_does_not_bring_in_the_whole_history():
    w = await _world()
    await _alert(w, "intrusion", at=_ago(days=30), title="Last month")
    await _alert(w, "intrusion", at=_ago(hours=3), title="This morning")
    recent = await _alert(w, "intrusion", at=_ago(minutes=20), title="Just now")
    assert (await _read(w))["alerts"] == 1
    assert [e["source_id"] for e in await _events(w)] == [recent]
    # And it stays that way: the old ones are behind where reading starts.
    assert (await _read(w, now=NOW + timedelta(minutes=5)))["alerts"] == 0


@pytest.mark.asyncio
async def test_an_alert_that_commits_late_with_an_earlier_time_is_still_read():
    w = await _world()
    await _alert(w, "intrusion", at=_ago(seconds=5))
    assert (await _read(w))["alerts"] == 1
    # A slow writer: stamped a minute before the reader passed, visible only now.
    late = await _alert(w, "weapon", at=_ago(seconds=60))
    assert (await _read(w, now=NOW + timedelta(seconds=10)))["alerts"] == 1
    assert late in {e["source_id"] for e in await _events(w)}


@pytest.mark.asyncio
async def test_a_backlog_larger_than_a_batch_drains_without_losing_rows_stamped_together():
    w = await _world()
    same_instant = _ago(seconds=30)
    ids = {await _alert(w, "intrusion", at=same_instant, title=f"burst {i}") for i in range(7)}
    assert (await _read(w, batch=3))["alerts"] == 3
    assert (await _read(w, batch=3))["alerts"] == 3
    assert (await _read(w, batch=3))["alerts"] == 1
    assert (await _read(w, batch=3))["alerts"] == 0
    assert {e["source_id"] for e in await _events(w)} == ids


@pytest.mark.asyncio
async def test_one_source_failing_does_not_stop_the_others_and_is_recorded(monkeypatch):
    w = await _world()
    await _alert(w, "intrusion")
    broken = events.Source(name="drone_events", time_column="written_at", normalise=events.from_drone_event,
                           select="SELECT * FROM a_table_that_is_not_there WHERE x > :floor LIMIT :batch")
    monkeypatch.setattr(events, "SOURCES", tuple(broken if s.name == "drone_events" else s for s in events.SOURCES))

    counts = await _read(w)
    assert counts["alerts"] == 1 and counts["drone_events"] == -1, counts
    cursor = {r["source"]: dict(r) for r in await _sql(
        "SELECT source, last_error, last_count FROM security_ingest_cursors WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert cursor["drone_events"]["last_error"], "the failure is on the record"
    assert "a_table_that_is_not_there" not in cursor["drone_events"]["last_error"], "the kind of error, not its text"
    assert cursor["alerts"]["last_error"] is None and cursor["alerts"]["last_count"] == 1


# ─── C. Only for tenants that asked ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_runner_works_only_for_tenants_that_switched_it_on():
    on, off = await _world(enabled=True), await _world(enabled=False)
    explicit_off = await _world(enabled=False)
    await _switch(explicit_off, False)
    for w in (on, off, explicit_off):
        await _alert(w, "intrusion")

    listed = set(await runner.tenants(AsyncSessionLocal))
    assert str(on["tenant"]) in listed
    assert str(off["tenant"]) not in listed and str(explicit_off["tenant"]) not in listed

    result = await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    assert result["failed"] == 0 and result["by_source"].get("alerts", 0) >= 1
    assert len(await _events(on)) == 1
    assert await _events(off) == [] and await _events(explicit_off) == []
    untouched = await _sql("SELECT count(*) AS n FROM security_ingest_cursors WHERE tenant_id IN (:a,:b)",
                           {"a": off["tenant"], "b": explicit_off["tenant"]})
    assert untouched[0]["n"] == 0, "a tenant that has not asked is not read at all"


@pytest.mark.asyncio
async def test_a_deactivated_tenant_is_not_worked_for():
    w = await _world()
    await _sql("UPDATE tenants SET is_active = FALSE WHERE id = :t", {"t": w["tenant"]})
    assert str(w["tenant"]) not in await runner.tenants(AsyncSessionLocal)


@pytest.mark.asyncio
async def test_two_tenants_are_read_in_one_pass_and_neither_sees_the_other():
    a, b = await _world(), await _world()
    a1, b1 = await _alert(a, "intrusion", title="A's"), await _alert(b, "weapon", title="B's")
    await runner.run_ingest_tick(AsyncSessionLocal, now=NOW)
    assert [e["source_id"] for e in await _events(a)] == [a1]
    assert [e["source_id"] for e in await _events(b)] == [b1]

    # With no tenant set the application role gets nothing: either no rows, or —
    # on a pooled connection, where the setting reads back empty — a refusal.
    async with AsyncSessionLocal() as db:
        try:
            nobody = (await db.execute(text("SELECT count(*) FROM security_events"))).scalar_one()
        except Exception:
            nobody = 0
        await db.rollback()
    assert nobody == 0

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(a["tenant"])})
        as_a = (await db.execute(text("SELECT title FROM security_events"))).scalars().all()
        cursors_a = (await db.execute(text("SELECT count(*) FROM security_ingest_cursors"))).scalar_one()
        await db.rollback()
    assert as_a == ["A's"] and cursors_a == len(events.SOURCES)

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(a["tenant"])})
        with pytest.raises(Exception, match="row-level security"):
            await db.execute(text(
                "INSERT INTO security_events (tenant_id, source_type, source_table, source_id, event_type, "
                "    occurred_at, severity, title) VALUES (:b,'OTHER','alerts',:i,'x',now(),'low','forged')"),
                {"b": b["tenant"], "i": uuid.uuid4()})
        await db.rollback()


# ─── D. The runner records; it cannot act ────────────────────────────────────

APP = Path(events.__file__).resolve().parents[1]
RUNNER_FILES = [APP / "intelligence_main.py", APP / "services" / "intel_runner.py",
                APP / "services" / "intel_events.py", APP / "services" / "intel_config.py",
                APP / "services" / "intel_correlation.py", APP / "services" / "intel_risk.py",
                APP / "services" / "intel_context.py", APP / "services" / "intel_recommend.py"]
MAY_IMPORT = {"app.core.config", "app.db.session", "app.services", "app.services.intel_events",
              "app.services.intel_runner", "app.services.intel_config", "app.services.intel_correlation",
              "app.services.intel_risk", "app.services.intel_context", "app.services.intel_recommend"}


def _app_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
            if node.module == "app.services":
                found |= {f"app.services.{a.name}" for a in node.names}
            else:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("app")}
    return found


def test_the_runner_imports_nothing_that_could_take_a_security_action():
    """Dispatch, incidents, alerts, drones and doors are other modules. The
    runner cannot call what it does not import."""
    assert "app.services.intel_runner" in _app_imports(RUNNER_FILES[0]), \
        "found no imports in the runner — the check no longer reads the file"
    for path in RUNNER_FILES:
        imports = _app_imports(path)
        assert imports <= MAY_IMPORT, f"{path.name} imports {sorted(imports - MAY_IMPORT)}"


def test_the_runner_writes_only_its_own_tables():
    # Not "ON CONFLICT ... DO UPDATE SET", and not the row lock "FOR UPDATE SKIP LOCKED".
    writes = re.compile(r"\b(INSERT\s+INTO|(?<!DO )(?<!FOR )UPDATE|DELETE\s+FROM)\s+([a-z_]+)", re.I)
    seen = set()
    for path in RUNNER_FILES:
        for _, table in writes.findall(path.read_text(encoding="utf-8")):
            seen.add(table.lower())
    assert seen, "found no writes at all — the pattern no longer matches the code"
    assert all(t.startswith("security_") for t in seen), f"writes outside its own tables: {sorted(seen)}"


def test_the_heartbeat_says_how_the_runner_is_and_nothing_about_a_tenant():
    value = json.loads(runner.heartbeat_value(True, {"tenants": 2, "events": 5, "failed": 0,
                                                     "by_source": {"alerts": 5}}, NOW))
    assert value == {"at": NOW.isoformat(), "ok": True, "tenants": 2, "events": 5, "failed": 0}


def test_a_runner_that_could_not_be_asked_is_never_reported_as_running():
    assert runner.runner_state(False, None) == "unknown"
    assert runner.runner_state(False, {"ok": True}) == "unknown"
    assert runner.runner_state(True, None) == "stopped"
    assert runner.runner_state(True, {"ok": True}) == "running"
    assert runner.runner_state(True, {"ok": False}) == "degraded"


# ─── E. The API ──────────────────────────────────────────────────────────────

BASE = "/api/v1/security-intelligence"


@pytest.mark.asyncio
async def test_who_may_read_events():
    w = await _world()
    await _alert(w, "intrusion")
    await _read(w)
    client_user = uuid.uuid4()
    await _sql("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
               "VALUES (:i,:t,7,:e,'x','A Client')",
               {"i": client_user, "t": w["tenant"], "e": f"client-{client_user.hex[:8]}@intel.test"})
    async with _client() as c:
        for role in (ADMIN, MANAGER, SUPERVISOR, OPERATOR, GUARD, VIEWER):
            for path in ("/events", "/status"):
                r = await c.get(BASE + path, headers=w["h"][role])
                assert r.status_code == 200, (role, path, r.text)
        refused = await c.get(BASE + "/events", headers=_auth(client_user, w["tenant"], CLIENT_ROLE))
        anonymous = await c.get(BASE + "/events")
    assert refused.status_code == 403, "a site's customer does not read its security assessments"
    assert anonymous.status_code in (401, 403)


@pytest.mark.asyncio
async def test_a_supervisor_restricted_to_one_site_sees_that_sites_events_only():
    w = await _world()
    at_a = await _alert(w, "intrusion", camera="cam_a", site="site_a", title="At A")
    at_b = await _alert(w, "weapon", camera="cam_b", site="site_b", title="At B")
    nowhere = await _alert(w, "alarm", camera=None, site=None, title="No site")
    await _read(w)
    ids = {e["source_id"]: e["id"] for e in await _events(w)}
    async with _client() as c:
        everything = await c.get(BASE + "/events", headers=w["h"][ADMIN])
        mine = await c.get(BASE + "/events", headers=w["h"][SUPERVISOR])
        own = await c.get(f"{BASE}/events/{ids[at_a]}", headers=w["h"][SUPERVISOR])
        other = await c.get(f"{BASE}/events/{ids[at_b]}", headers=w["h"][SUPERVISOR])
        siteless = await c.get(f"{BASE}/events/{ids[nowhere]}", headers=w["h"][SUPERVISOR])
        missing = await c.get(f"{BASE}/events/{uuid.uuid4()}", headers=w["h"][ADMIN])
        status = await c.get(BASE + "/status", headers=w["h"][SUPERVISOR])
    assert everything.json()["total"] == 3
    assert [e["title"] for e in mine.json()["items"]] == ["At A"]
    assert own.status_code == 200 and own.json()["site_name"] == "Factory A" and own.json()["camera_name"] == "Gate 1"
    assert other.status_code == 404 and siteless.status_code == 404 and missing.status_code == 404
    assert status.json()["last_24_hours"] == {"CCTV_AI": 1}, "the count is of the sites the caller may see"


@pytest.mark.asyncio
async def test_another_tenants_event_is_not_found():
    a, b = await _world(), await _world()
    await _alert(b, "intrusion", title="B's")
    await _read(b)
    theirs = (await _events(b))[0]["id"]
    async with _client() as c:
        got = await c.get(f"{BASE}/events/{theirs}", headers=a["h"][ADMIN])
        listed = await c.get(BASE + "/events", headers=a["h"][ADMIN])
    assert got.status_code == 404 and listed.json()["total"] == 0


@pytest.mark.asyncio
async def test_events_are_listed_newest_first_and_filtered():
    w = await _world()
    await _alert(w, "intrusion", severity="high", title="First", at=_ago(seconds=50))
    await _alert(w, "alarm", severity="critical", title="Second", at=_ago(seconds=40), camera=None)
    await _alert(w, "weapon", severity="critical", title="Third", at=_ago(seconds=30), camera="cam_b", site="site_b")
    await _read(w)
    h = w["h"][OPERATOR]
    async with _client() as c:
        everything = (await c.get(BASE + "/events", headers=h)).json()
        alarms = (await c.get(BASE + "/events", params={"source_type": "ALARM"}, headers=h)).json()
        critical = (await c.get(BASE + "/events", params={"severity": "critical"}, headers=h)).json()
        site_b = (await c.get(BASE + "/events", params={"site_id": str(w["site_b"])}, headers=h)).json()
        camera = (await c.get(BASE + "/events", params={"camera_id": str(w["cam_a"])}, headers=h)).json()
        window = (await c.get(BASE + "/events", headers=h, params={
            "from": _ago(seconds=45).isoformat(), "to": _ago(seconds=35).isoformat()})).json()
        page = (await c.get(BASE + "/events", params={"limit": 2}, headers=h)).json()
        bad_source = await c.get(BASE + "/events", params={"source_type": "RADAR"}, headers=h)
        bad_severity = await c.get(BASE + "/events", params={"severity": "urgent"}, headers=h)
        backwards = await c.get(BASE + "/events", headers=h, params={
            "from": NOW.isoformat(), "to": _ago(hours=1).isoformat()})
    assert [e["title"] for e in everything["items"]] == ["Third", "Second", "First"]
    assert [e["title"] for e in alarms["items"]] == ["Second"]
    assert {e["title"] for e in critical["items"]} == {"Second", "Third"}
    assert [e["title"] for e in site_b["items"]] == ["Third"] and [e["title"] for e in camera["items"]] == ["First"]
    assert [e["title"] for e in window["items"]] == ["Second"]
    assert len(page["items"]) == 2 and page["total"] == 3 and page["has_more"] is True
    assert bad_source.status_code == 422 and "RADAR" in bad_source.json()["detail"]
    assert bad_severity.status_code == 422 and backwards.status_code == 422


@pytest.mark.asyncio
async def test_the_switch_is_off_until_an_administrator_turns_it_on():
    w = await _world(enabled=False)
    async with _client() as c:
        before = await c.get(BASE + "/status", headers=w["h"][ADMIN])
        refused = await c.put("/api/v1/settings/intel.enabled", json={"setting_value": True},
                              headers=w["h"][OPERATOR])
        nonsense = await c.put("/api/v1/settings/intel.enabled", json={"setting_value": "yes"},
                               headers=w["h"][ADMIN])
        turned_on = await c.put("/api/v1/settings/intel.enabled", json={"setting_value": True},
                                headers=w["h"][ADMIN])
        after = await c.get(BASE + "/status", headers=w["h"][ADMIN])
    assert before.status_code == 200 and before.json()["enabled"] is False
    assert before.json()["sources"] == [] and before.json()["last_24_hours"] == {}
    assert refused.status_code == 403 and nonsense.status_code == 422
    assert turned_on.status_code == 200 and after.json()["enabled"] is True
    assert str(w["tenant"]) in await runner.tenants(AsyncSessionLocal)
    assert after.json()["runner"]["state"] in ("running", "degraded", "stopped", "unknown")


@pytest.mark.asyncio
async def test_status_shows_how_far_each_source_has_been_read():
    w = await _world()
    await _alert(w, "intrusion")
    await _read(w)
    async with _client() as c:
        body = (await c.get(BASE + "/status", headers=w["h"][ADMIN])).json()
    by = {s["source"]: s for s in body["sources"]}
    assert set(by) == {s.name for s in events.SOURCES}
    assert by["alerts"]["total_count"] == 1 and by["alerts"]["last_error"] is None
    assert by["alerts"]["read_from"] and by["alerts"]["last_run_at"]


@pytest.mark.asyncio
async def test_is_enabled_reads_only_a_true_setting():
    never, off, on = await _world(enabled=False), await _world(enabled=False), await _world(enabled=True)
    await _switch(off, False)
    answers = []
    for w in (never, off, on):
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(w["tenant"])})
            answers.append(await intel_config.is_enabled(db))
            await db.rollback()
    assert answers == [False, False, True]


# ─── F. The schema ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_both_tables_are_tenant_isolated_and_forced():
    rows = await _sql(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies, "
        "       has_table_privilege('svc_app', c.oid, 'SELECT,INSERT,UPDATE,DELETE') AS app_can "
        "  FROM pg_class c WHERE c.relname IN ('security_events','security_ingest_cursors') ORDER BY 1")
    assert [r["relname"] for r in rows] == ["security_events", "security_ingest_cursors"]
    for r in rows:
        assert r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] == 1 and r["app_can"], dict(r)


@pytest.mark.asyncio
async def test_the_platform_owner_and_the_client_role_hold_no_intelligence_permission():
    rows = await _sql(
        "SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        " WHERE p.code LIKE 'intel:%'")
    held: dict[int, set[str]] = {}
    for r in rows:
        held.setdefault(r["role_id"], set()).add(r["code"])
    assert SUPER_ADMIN not in held and CLIENT_ROLE not in held
    assert held[VIEWER] == {"intel:read"}
    assert "intel:manage" in held[ADMIN] and "intel:manage" in held[MANAGER]
    assert "intel:manage" not in held[SUPERVISOR] and "intel:approve" in held[SUPERVISOR]
    assert "intel:override" in held[OPERATOR] and "intel:approve" not in held[OPERATOR]
    assert held[GUARD] == {"intel:read", "intel:recommendation:read", "intel:decide"}


@pytest.mark.asyncio
async def test_a_source_record_can_be_an_event_only_once_and_a_bad_row_is_refused():
    w = await _world()
    source = uuid.uuid4()
    insert = ("INSERT INTO security_events (tenant_id, source_type, source_table, source_id, event_type, "
              "    occurred_at, severity, title, confidence) VALUES (:t,:st,'alerts',:s,'x',now(),:sev,'t',:c)")
    ok = {"t": w["tenant"], "st": "CCTV_AI", "s": source, "sev": "high", "c": 0.5}
    await _sql(insert, ok)
    for bad in ({}, {"s": uuid.uuid4(), "st": "RADAR"}, {"s": uuid.uuid4(), "sev": "urgent"},
                {"s": uuid.uuid4(), "c": 1.5}):
        with pytest.raises(Exception):
            await _sql(insert, {**ok, **bad})
    assert len(await _events(w)) == 1


@pytest.mark.asyncio
async def test_removing_a_camera_or_an_alert_keeps_the_event():
    w = await _world()
    aid = await _alert(w, "intrusion")
    await _read(w)
    await _sql("DELETE FROM alerts WHERE id = :i", {"i": aid})
    await _sql("DELETE FROM cameras WHERE id = :i", {"i": w["cam_a"]})
    event = (await _events(w))[0]
    assert event["source_id"] == aid, "what was reported stays on the record"
    assert event["alert_id"] is None and event["camera_id"] is None
    assert event["location_label"] == "Gate 1 — North fence", "and it still says where"
