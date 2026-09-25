"""Drone patrol, phase 6: detection → context → risk → verification → event,
against the real database.

No drone video exists, so these tests write detections exactly as the AI workers
do — a `detections` row on the drone's camera, the module's own row
(`lpr_events`, `face_events`) and, where the worker alerts, its `alerts` row —
and let the drone runner's AI job take it from there. The drone's position comes
from telemetry recorded over a restricted zone, at a chosen hour of the night or
day in site time.

  A — Verification: one frame, a sustained sighting, a sighting never confirmed
  B — What is looked at: the profile, thresholds, unreliable modules
  C — Alerts: never a second one for what a worker already alerted
  D — Authorisation: allowed and blocklisted vehicles
  E — Reading detections: idempotent, and after landing
  F — Pre-flight, the edge, the API
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db.session import AsyncSessionLocal
from app.services import drone_ai_pipeline as pipeline
from app.services import drone_runner as runner
from tests.test_drone_edge_sync import (
    ADMIN, OPERATOR, _claimed, _client, _now, _run, _session, _sql, _sync, _world as _edge_world,
)
from app.core.security import create_access_token

SGT = ZoneInfo("Asia/Singapore")
ZONE_SQUARE = [[1.3005, 103.7995], [1.3005, 103.8005], [1.3015, 103.8005], [1.3015, 103.7995]]
OVER_ZONE = (1.3010, 103.8000)


def _last(hour: int, minute: int = 0) -> datetime:
    """The most recent hour:minute in Singapore that is safely in the past."""
    now = datetime.now(SGT)
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if t > now - timedelta(minutes=5):
        t -= timedelta(days=1)
    return t.astimezone(timezone.utc)


async def _world(*, rules: list[tuple] | None = None, zone_type: str = "RESTRICTED", zone_extra: dict | None = None,
                 camera_modules=("intrusion", "lpr", "face", "weapon", "fire_smoke", "tampering_off"),
                 camera: bool = True, image_zone: bool = True) -> dict:
    """A licensed Singapore tenant: a site with a restricted zone, a simulator
    drone whose camera runs the given AI modules, a route over the zone and a
    mission — with a security profile when `rules` is given."""
    w = {k: uuid.uuid4() for k in ("tenant", "site", "camera", "provider", "drone", "route", "mission", "zone",
                                   "profile", "admin", "operator")}
    modules = [m for m in camera_modules if not m.endswith("_off")]
    zx = zone_extra or {}
    stmts = [
        ("INSERT INTO tenants (id, name, slug, timezone) VALUES (:t,'AI Co',:s,'Asia/Singapore')",
         {"t": w["tenant"], "s": f"dai-{w['tenant'].hex[:10]}"}),
        ("INSERT INTO sites (id, tenant_id, name, latitude, longitude, geofence_radius_meters) "
         "VALUES (:i,:t,'Depot',1.3,103.8,800)", {"i": w["site"], "t": w["tenant"]}),
        ("INSERT INTO drone_module_licenses (tenant_id, is_enabled, licensed_at) VALUES (:t,TRUE,now())",
         {"t": w["tenant"]}),
        ("INSERT INTO drone_provider_configs (id, tenant_id, name, provider_key, config) "
         "VALUES (:i,:t,'Sim','simulator','{\"speed_factor\": 50}')", {"i": w["provider"], "t": w["tenant"]}),
        ("INSERT INTO drone_security_zones (id, tenant_id, site_id, name, zone_type, shape, polygon, severity, "
         "   allowed_vehicle_plates, allowed_role_ids, alert_policy) "
         "VALUES (:i,:t,:s,'Loading Bay',:zt,'POLYGON',CAST(:p AS jsonb),'MEDIUM',:plates,"
         "        CAST(:roles AS smallint[]), :policy)",
         {"i": w["zone"], "t": w["tenant"], "s": w["site"], "zt": zone_type, "p": json.dumps(ZONE_SQUARE),
          "plates": list(zx.get("allowed_vehicle_plates", [])), "roles": list(zx.get("allowed_role_ids", [])),
          "policy": zx.get("alert_policy", "ALERT")}),
    ]
    if camera:
        stmts.append(("INSERT INTO cameras (id, tenant_id, site_id, name, ai_modules_enabled) "
                      "VALUES (:i,:t,:s,'Drone One camera',CAST(:m AS jsonb))",
                      {"i": w["camera"], "t": w["tenant"], "s": w["site"], "m": json.dumps(modules)}))
        if image_zone:
            stmts.append(("INSERT INTO restricted_zones (tenant_id, camera_id, name, polygon, severity) "
                          "VALUES (:t,:c,'Full frame',CAST(:p AS jsonb),'low')",
                          {"t": w["tenant"], "c": w["camera"],
                           "p": json.dumps([{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}])}))
    stmts += [
        ("INSERT INTO drones (id, tenant_id, site_id, provider_config_id, camera_id, name, code, status, battery_level, "
         "   gps_status, communication_status, camera_status, storage_status, last_heartbeat_at, "
         "   current_latitude, current_longitude) "
         "VALUES (:i,:t,:s,:p,:c,'Drone One','D-01','READY',100,'OK','OK','OK','OK',now(),1.3,103.8)",
         {"i": w["drone"], "t": w["tenant"], "s": w["site"], "p": w["provider"],
          "c": w["camera"] if camera else None}),
        ("INSERT INTO drone_routes (id, tenant_id, site_id, name, base_latitude, base_longitude, default_speed_mps) "
         "VALUES (:i,:t,:s,'Perimeter',1.3,103.8,8)", {"i": w["route"], "t": w["tenant"], "s": w["site"]}),
        ("INSERT INTO drone_waypoints (tenant_id, route_id, sequence, latitude, longitude) "
         "VALUES (:t,:r,1,1.3010,103.8000)", {"t": w["tenant"], "r": w["route"]}),
    ]
    if rules is not None:
        stmts.append(("INSERT INTO drone_security_profiles (id, tenant_id, name, min_confidence, verify_min_seconds) "
                      "VALUES (:i,:t,'Night',0.5,3)", {"i": w["profile"], "t": w["tenant"]}))
        for module, sev, conf, *incident in rules:
            # No incident level given: leave the column to its default, as the API does.
            cols, vals = ("incident_risk_level, ", ":inc, ") if incident else ("", "")
            stmts.append((f"INSERT INTO drone_profile_rules (tenant_id, profile_id, module_type, base_severity, "
                          f"{cols}min_confidence) VALUES (:t,:p,:m,:s,{vals}:c)",
                          {"t": w["tenant"], "p": w["profile"], "m": module, "s": sev.upper(), "c": conf,
                           **({"inc": incident[0]} if incident else {})}))
    stmts.append(("INSERT INTO drone_missions (id, tenant_id, site_id, drone_id, route_id, security_profile_id, name) "
                  "VALUES (:i,:t,:s,:d,:r,:p,'Night Watch')",
                  {"i": w["mission"], "t": w["tenant"], "s": w["site"], "d": w["drone"], "r": w["route"],
                   "p": w["profile"] if rules is not None else None}))
    for key, role in (("admin", ADMIN), ("operator", OPERATOR)):
        stmts.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                      "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
                      {"i": w[key], "t": w["tenant"], "r": role, "e": f"{key}-{w[key].hex[:8]}@ai.test",
                       "n": f"{key.title()} User"}))
    await _run(stmts)
    w["h_admin"] = {"Authorization": f"Bearer {create_access_token(str(w['admin']), str(w['tenant']), ADMIN)}"}
    w["h_op"] = {"Authorization": f"Bearer {create_access_token(str(w['operator']), str(w['tenant']), OPERATOR)}"}
    return w


async def _flying(w: dict, at: datetime, *, ended: bool = False) -> str:
    """A flight over the zone around `at`: a real session, launched two minutes
    before, with telemetry placing the drone over the zone."""
    # The drone has just reported: a slow machine must not fail pre-flight's
    # heartbeat check between building the world and launching.
    await _sql("UPDATE drones SET last_heartbeat_at = now() WHERE id = :d", {"d": w["drone"]})
    async with _client() as c:
        r = await c.post(f"/api/v1/drone-missions/{w['mission']}/run", headers=w["h_admin"])
    assert r.status_code == 201 and r.json()["session"]["status"] == "READY", r.json()
    sid = r.json()["session"]["id"]
    stmts = [("UPDATE drone_patrol_sessions SET status = :st, started_at = :l, launched_at = :l, ended_at = :e WHERE id = :s",
              {"st": "COMPLETED" if ended else "ACTIVE", "l": at - timedelta(minutes=2), "e": at if ended else None,
               "s": uuid.UUID(sid)})]
    for i in range(-6, 30):
        stmts.append(("INSERT INTO drone_telemetry (tenant_id, drone_id, session_id, recorded_at, latitude, longitude, "
                      "altitude_m, mission_state) VALUES (:t,:d,:s,:at,:la,:lo,40,'ACTIVE')",
                      {"t": w["tenant"], "d": w["drone"], "s": uuid.UUID(sid), "at": at + timedelta(seconds=5 * i),
                       "la": OVER_ZONE[0], "lo": OVER_ZONE[1]}))
    await _run(stmts)
    return sid


async def _detect(w: dict, at: datetime, module: str, conf: float = 0.85, *, plate: str | None = None,
                  lpr_match: str | None = None, face_match: str | None = None, weapon: str | None = None,
                  worker_alert: str | None = None) -> uuid.UUID:
    """A detection as a worker writes it."""
    det = uuid.uuid4()
    meta = {"model_version": "test"} | ({"weapon_type": weapon} if weapon else {})
    stmts = [("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, bounding_box, raw_metadata, "
              "detected_at) VALUES (:i,:t,:c,:m,:conf,'{}',CAST(:meta AS jsonb),:at)",
              {"i": det, "t": w["tenant"], "c": w["camera"], "m": module, "conf": conf, "meta": json.dumps(meta),
               "at": at})]
    if module == "lpr":
        stmts.append(("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number, "
                      "watchlist_match) VALUES (:i,:at,:t,:c,:p,:wl)",
                      {"i": det, "at": at, "t": w["tenant"], "c": w["camera"], "p": plate, "wl": lpr_match}))
    if module == "face":
        stmts.append(("INSERT INTO face_events (detection_id, detected_at, tenant_id, camera_id, watchlist_match) "
                      "VALUES (:i,:at,:t,:c,:wl)", {"i": det, "at": at, "t": w["tenant"], "c": w["camera"],
                                                     "wl": face_match}))
    if worker_alert:
        stmts.append(("INSERT INTO alerts (tenant_id, detection_id, camera_id, module_type, severity, alert_code, "
                      "title, status) VALUES (:t,:i,:c,:m,:sev,:code,'worker alert','open')",
                      {"t": w["tenant"], "i": det, "c": w["camera"], "m": module, "sev": worker_alert,
                       "code": f"{module}.test"}))
    await _run(stmts)
    return det


async def _ai(now: datetime) -> dict:
    return await runner.run_ai_tick(AsyncSessionLocal, runner.ListPublisher(), now=now)


async def _events(sid: str) -> list[dict]:
    return [dict(r) for r in await _sql("SELECT * FROM drone_events WHERE session_id = :s ORDER BY detected_at",
                                        {"s": uuid.UUID(sid)})]


async def _drone_alerts(w: dict) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT id, alert_code, severity, title FROM alerts WHERE tenant_id = :t AND module_type = 'drone_patrol' "
        "  AND alert_code NOT LIKE 'drone.mission%' AND alert_code NOT LIKE 'drone.preflight%' ORDER BY created_at",
        {"t": w["tenant"]})]


# ─── A. Verification ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_sustained_intrusion_at_night_in_a_restricted_zone_is_verified_high_and_alerted_once():
    w = await _world()
    at = _last(2, 17)
    sid = await _flying(w, at)

    await _detect(w, at, "intrusion")
    first = await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    assert first["accepted"] == 1
    assert e["verification_state"] == "OBSERVING" and e["zone_name"] == "Loading Bay"
    assert await _drone_alerts(w) == [], "one frame raised an alert"

    for s in (2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion", 0.91)
    await _ai(at + timedelta(seconds=10))
    await _ai(at + timedelta(seconds=12))                 # again: nothing new to read
    [e] = await _events(sid)
    assert e["verification_state"] == "VERIFIED" and e["detection_count"] == 3
    assert e["risk_level"] == "HIGH" and float(e["ai_confidence"]) == pytest.approx(0.91)
    assert e["attributes"]["authorisation"] == "UNAUTHORISED"
    factors = {f["factor"] for f in e["risk_factors"]}
    assert {"ZONE", "AUTHORISATION", "TIME", "VERIFICATION"} <= factors
    alerts = await _drone_alerts(w)
    assert [(a["alert_code"], a["severity"]) for a in alerts] == [("drone.intrusion", "high")]
    assert e["alert_id"] == alerts[0]["id"]
    assert (await _session(sid))["event_count"] == 1


@pytest.mark.asyncio
async def test_a_sighting_never_confirmed_closes_unverified_and_asks_for_a_review_if_it_looked_serious():
    w = await _world(zone_type="CRITICAL")
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _detect(w, at, "intrusion")
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    assert e["verification_state"] == "OBSERVING" and e["risk_level"] == "HIGH"
    await _ai(at + timedelta(minutes=2))
    [e] = await _events(sid)
    alerts = await _drone_alerts(w)
    assert e["verification_state"] == "UNVERIFIED"
    assert [a["severity"] for a in alerts] == ["low"] and alerts[0]["title"].startswith("Unconfirmed")


# ─── B. What is looked at ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_profile_and_its_thresholds_decide_what_is_looked_at():
    w = await _world(rules=[("lpr", "low", 0.6)])
    at = _last(14)
    sid = await _flying(w, at)
    await _detect(w, at, "intrusion", 0.95)                          # not in the profile
    await _detect(w, at, "lpr", 0.55, plate="SBA1111A")             # below the rule's 0.6
    await _detect(w, at, "tampering", 0.99)                          # unreliable on a moving camera
    await _detect(w, at + timedelta(seconds=1), "lpr", 0.9, plate="SBA2222B")
    got = await _ai(at + timedelta(seconds=5))
    events = await _events(sid)
    assert got["ignored"] == 3 and got["accepted"] == 1
    assert [(e["module_type"], e["label"]) for e in events] == [("lpr", "SBA2222B")]


# ─── C. Alerts ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_worker_alert_already_as_serious_is_linked_not_repeated():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at)
    det = await _detect(w, at, "weapon", 0.92, weapon="handgun", worker_alert="critical")
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    worker = (await _sql("SELECT id FROM alerts WHERE detection_id = :d", {"d": det}))[0]["id"]
    assert e["verification_state"] == "VERIFIED", "a clear weapon is verified on first sight"
    assert e["risk_level"] == "CRITICAL" and e["label"] == "handgun"
    assert e["alert_id"] == worker
    assert await _drone_alerts(w) == [], "the drone repeated the worker's alert"


@pytest.mark.asyncio
async def test_context_escalates_an_event_beyond_what_the_worker_alerted():
    """An unrecognised face is an 'info' alert to the face worker. Seen again and
    again at night in a restricted zone with no one allowed there, it is not."""
    w = await _world()
    at = _last(2, 17)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "face", 0.8, face_match=None, worker_alert="info")
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    alerts = await _drone_alerts(w)
    assert e["risk_level"] == "HIGH" and [(a["alert_code"], a["severity"]) for a in alerts] == [("drone.face", "high")]
    assert e["alert_id"] == alerts[0]["id"]


# ─── D. Authorisation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_allowed_vehicle_is_low_risk_and_a_blocklisted_one_is_not():
    w = await _world(zone_type="VEHICLE_RESTRICTED", zone_extra={"allowed_vehicle_plates": ["SBA1234A"]})
    at = _last(14)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "lpr", 0.9, plate="SBA 1234 A")
        await _detect(w, at + timedelta(seconds=s), "lpr", 0.9, plate="SGX9999Z", lpr_match="block")
    await _ai(at + timedelta(seconds=10))
    by_plate = {e["label"]: e for e in await _events(sid)}
    assert by_plate["SBA 1234 A"]["attributes"]["authorisation"] == "AUTHORISED"
    assert by_plate["SBA 1234 A"]["risk_level"] in ("INFO", "LOW")
    assert by_plate["SGX9999Z"]["attributes"]["authorisation"] == "BLOCKLISTED"
    assert by_plate["SGX9999Z"]["risk_level"] in ("HIGH", "CRITICAL")
    assert [a["alert_code"] for a in await _drone_alerts(w)] == ["drone.lpr"]


# ─── E. Reading detections ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reading_detections_again_adds_nothing():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion")
    await _ai(at + timedelta(seconds=10))
    await _sql("UPDATE drone_patrol_sessions SET ai_watermark_at = NULL WHERE id = :s", {"s": uuid.UUID(sid)})
    again = await _ai(at + timedelta(seconds=12))
    assert again["accepted"] == 0 and again["duplicate"] >= 3
    obs = await _sql("SELECT count(*) AS n FROM drone_observations WHERE session_id = :s", {"s": uuid.UUID(sid)})
    assert obs[0]["n"] == 3 and len(await _events(sid)) == 1


@pytest.mark.asyncio
async def test_detections_just_after_landing_still_count_and_then_reading_stops():
    w = await _world()
    at = _last(14)
    sid = await _flying(w, at, ended=True)
    await _detect(w, at + timedelta(seconds=10), "fire_smoke", 0.93)
    got = await _ai(at + timedelta(minutes=1))
    assert got["accepted"] == 1
    s = await _session(sid)
    assert s["ai_watermark_at"] >= at + pipeline.AFTER_LANDING
    async with AsyncSessionLocal() as db:
        await db.execute(__import__("sqlalchemy").text("SELECT set_config('app.current_tenant', :t, true)"),
                         {"t": str(w["tenant"])})
        assert sid not in {str(x["id"]) for x in await pipeline.sessions_to_read(db, at + timedelta(minutes=2))}


# ─── F. Pre-flight, the edge, the API ────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_says_what_the_ai_can_and_cannot_see():
    tampering = await _world(camera_modules=("intrusion", "tampering"))
    no_camera = await _world(camera=False)
    no_zone = await _world(rules=[("intrusion", "low", None), ("fall", "high", None)], image_zone=False)
    async with _client() as c:
        a = (await c.post(f"/api/v1/drone-missions/{tampering['mission']}/run", headers=tampering["h_admin"])).json()
        b = (await c.get(f"/api/v1/drone-missions/{no_camera['mission']}/preflight", headers=no_camera["h_admin"])).json()
        d = (await c.get(f"/api/v1/drone-missions/{no_zone['mission']}/preflight", headers=no_zone["h_admin"])).json()
    assert a["session"]["status"] == "BLOCKED" and "AI_TAMPERING" in a["preflight"]["blocking"]
    assert b["passed"] and "AI_CAMERA" in b["warnings"]
    assert d["passed"] and {"AI_IMAGE_ZONES", "AI_MODULES"} <= set(d["warnings"])


@pytest.mark.asyncio
async def test_a_sighting_from_a_site_gateway_is_judged_like_a_central_detection():
    w = await _edge_world()
    async with _client() as c:
        s = await _claimed(c, w)
        t = _now()
        sightings = [{"client_ref": str(uuid.uuid4()), "drone_id": str(w["drone"]), "session_id": s["id"],
                      "module_type": "intrusion", "detected_at": (t + timedelta(seconds=i)).isoformat(),
                      "ai_confidence": 0.9, "drone_latitude": 1.3005, "drone_longitude": 103.8005}
                     for i in (0, 2, 4)]
        body = (await _sync(c, w["key"], events=sightings)).json()
        again = (await _sync(c, w["key"], events=sightings)).json()
    assert body["events"]["accepted"] == 3 and again["events"]["duplicates"] == 3
    [e] = await _events(s["id"])
    assert e["source"] == "EDGE" and e["detection_count"] == 3 and e["verification_state"] == "VERIFIED"
    assert e["risk_level"] and e["risk_factors"]


@pytest.mark.asyncio
async def test_the_event_shows_what_it_rests_on_and_the_modules_say_what_they_can_do():
    w = await _world()
    at = _last(2, 17)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion")
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    async with _client() as c:
        detail = (await c.get(f"/api/v1/drone-events/{e['id']}", headers=w["h_op"])).json()
        modules = (await c.get("/api/v1/drone-ai/modules", headers=w["h_op"])).json()
    assert len(detail["observations"]) == 3 and detail["observations"][0]["source"] == "CENTRAL"
    assert detail["risk_level"] == "HIGH" and detail["ai_confidence"] is not None
    kinds = {m["module_type"]: m["suitability"] for m in modules["modules"]}
    assert kinds["weapon"] == "SUPPORTED" and kinds["intrusion"] == "NEEDS_IMAGE_ZONE"
    assert kinds["tampering"] == "UNRELIABLE" and len(kinds) == 11
