"""Drone patrol, phase 7: CCTV correlation against the real database.

The site has fixed cameras placed around the restricted zone the drone flies
over: one merely near, one whose surveyed view faces the spot, one close but
facing away, one too far, and one that covers the spot from beyond the nearby
radius. The drone's own camera is at the spot too, and must never be offered as
"related CCTV".
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from app.db.session import AsyncSessionLocal
from app.services import drone_cctv_correlation as corr
from tests.test_drone_ai_pipeline import OVER_ZONE, _ai, _detect, _events, _flying, _last, _world
from tests.test_drone_edge_sync import _client, _run, _sql

M = 1 / 111_320


async def _cameras(w: dict) -> dict:
    """Fixed cameras around the spot, by name → id."""
    spec = {
        "CAM-27": (30, 0, None),
        "CAM-28": (0, 80, {"heading_deg": 270, "fov_deg": 60, "range_m": 120}),
        "CAM-29": (-20, 0, {"heading_deg": 180, "fov_deg": 60, "range_m": 100}),
        "CAM-99": (500, 0, None),
        "CAM-31": (0, -200, {"heading_deg": 90, "fov_deg": 30, "range_m": 300}),
    }
    ids, stmts = {}, []
    for name, (dn, de, cov) in spec.items():
        ids[name] = uuid.uuid4()
        stmts.append(("INSERT INTO cameras (id, tenant_id, site_id, name, latitude, longitude) "
                      "VALUES (:i,:t,:s,:n,:la,:lo)",
                      {"i": ids[name], "t": w["tenant"], "s": w["site"], "n": name,
                       "la": OVER_ZONE[0] + dn * M, "lo": OVER_ZONE[1] + de * M}))
        if cov:
            stmts.append(("INSERT INTO drone_camera_coverage (tenant_id, camera_id, heading_deg, fov_deg, range_m) "
                          "VALUES (:t,:c,:h,:f,:r)", {"t": w["tenant"], "c": ids[name], "h": cov["heading_deg"],
                                                      "f": cov["fov_deg"], "r": cov["range_m"]}))
    # The drone's own camera sits right on the spot: it must never count.
    stmts.append(("UPDATE cameras SET latitude = :la, longitude = :lo WHERE id = :c",
                  {"la": OVER_ZONE[0], "lo": OVER_ZONE[1], "c": w["camera"]}))
    await _run(stmts)
    return ids


async def _cam_detect(w: dict, camera, at, module: str, *, plate: str | None = None, conf: float = 0.8):
    det = uuid.uuid4()
    stmts = [("INSERT INTO detections (id, tenant_id, camera_id, module_type, confidence, detected_at) "
              "VALUES (:i,:t,:c,:m,:conf,:at)", {"i": det, "t": w["tenant"], "c": camera, "m": module,
                                                  "conf": conf, "at": at})]
    if module == "lpr":
        stmts.append(("INSERT INTO lpr_events (detection_id, detected_at, tenant_id, camera_id, plate_number) "
                      "VALUES (:i,:at,:t,:c,:p)", {"i": det, "at": at, "t": w["tenant"], "c": camera, "p": plate}))
    await _run(stmts)
    return det


async def _related(event_id) -> dict:
    rows = await _sql("SELECT c.name, ec.* FROM drone_event_cameras ec JOIN cameras c ON c.id = ec.camera_id "
                      " WHERE ec.event_id = :e ORDER BY ec.rank", {"e": event_id})
    return {r["name"]: dict(r) for r in rows}


# ─── Which cameras ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_event_lists_the_cameras_that_could_have_seen_it_best_first():
    w = await _world()
    await _cameras(w)
    at = _last(2, 17)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion")
    got = await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    related = await _related(e["id"])
    assert got["correlated"] >= 1
    assert list(related) == ["CAM-28", "CAM-31", "CAM-27"], related.keys()
    assert related["CAM-28"]["correlation_method"] == "COVERAGE" and related["CAM-28"]["in_coverage"] is True
    assert related["CAM-27"]["correlation_method"] == "DISTANCE" and related["CAM-27"]["in_coverage"] is None
    assert float(related["CAM-27"]["distance_m"]) == pytest.approx(30, abs=1)
    assert related["CAM-27"]["window_start"] == e["detected_at"] - timedelta(seconds=20)
    assert related["CAM-27"]["window_end"] == e["last_detected_at"] + timedelta(seconds=60)


# ─── Corroboration ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_fixed_camera_that_saw_a_person_too_verifies_the_event_and_raises_its_risk():
    w = await _world()
    cams = await _cameras(w)
    at = _last(2, 17)
    sid = await _flying(w, at)
    await _detect(w, at, "intrusion")                                  # the drone: once
    await _ai(at + timedelta(seconds=3))
    [alone] = await _events(sid)
    assert alone["verification_state"] == "OBSERVING"

    det = await _cam_detect(w, cams["CAM-27"], at + timedelta(seconds=6), "face")
    await _sql("UPDATE drone_events SET cctv_correlated_at = NULL WHERE id = :e", {"e": alone["id"]})
    await _ai(at + timedelta(seconds=8))
    [e] = await _events(sid)
    related = await _related(e["id"])
    assert related["CAM-27"]["corroborates"] is True and related["CAM-27"]["related_detection_id"] == det
    assert related["CAM-28"]["corroborates"] is False
    assert e["verification_state"] == "VERIFIED", "a second, independent sensor agreeing verifies the event"
    assert any(f["factor"] == "CCTV" and "CAM-27" in f["detail"] for f in e["risk_factors"])
    assert e["risk_score"] > alone["risk_score"]


@pytest.mark.asyncio
async def test_a_plate_is_corroborated_only_by_the_same_plate():
    w = await _world()
    cams = await _cameras(w)
    at = _last(14)
    sid = await _flying(w, at)
    await _detect(w, at, "lpr", 0.9, plate="SGX1234A")
    await _cam_detect(w, cams["CAM-27"], at + timedelta(seconds=2), "lpr", plate="SBA9999Z")
    await _cam_detect(w, cams["CAM-28"], at + timedelta(seconds=3), "lpr", plate="SGX 1234 A")
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    related = await _related(e["id"])
    assert related["CAM-27"]["corroborates"] is False and related["CAM-27"]["related_detection_count"] == 1
    assert related["CAM-28"]["corroborates"] is True and related["CAM-28"]["related_module_type"] == "lpr"


# ─── Footage ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_operator_gets_live_view_and_playback_at_the_moment_but_never_a_stream_address():
    w = await _world()
    cams = await _cameras(w)
    at = _last(14)
    sid = await _flying(w, at)
    stream, rec = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO streams (id, tenant_id, camera_id, url, protocol, status, auth_config) "
         "VALUES (:i,:t,:c,'rtsp://10.0.0.27:554/live','rtsp','online',CAST(:a AS jsonb))",
         {"i": stream, "t": w["tenant"], "c": cams["CAM-27"],
          "a": json.dumps({"username": "admin", "password": "cam-secret-27"})}),
        ("INSERT INTO recordings (id, tenant_id, camera_id, stream_id, site_id, started_at, ended_at, status, "
         "   file_path) VALUES (:i,:t,:c,:s,:site,:a,:b,'completed','x.mp4')",
         {"i": rec, "t": w["tenant"], "c": cams["CAM-27"], "s": stream, "site": w["site"],
          "a": at - timedelta(minutes=10), "b": at + timedelta(minutes=10)}),
    ])
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion")
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    async with _client() as c:
        r = await c.get(f"/api/v1/drone-events/{e['id']}/cctv", headers=w["h_op"])
    assert r.status_code == 200, r.text
    view = r.json()
    cam27 = next(x for x in view["cameras"] if x["camera_name"] == "CAM-27")
    assert cam27["camera_online"] is True
    assert cam27["streams"][0]["live_path"] == f"/api/v1/cameras/{cams['CAM-27']}/streams/{stream}/live"
    assert cam27["playback"]["recording_id"] == str(rec)
    assert float(cam27["playback"]["offset_s"]) == pytest.approx(600, abs=0.01)
    assert cam27["playback"]["download_path"].endswith(f"/recordings/{rec}/download")
    assert "rtsp://" not in r.text and "cam-secret-27" not in r.text and "10.0.0.27" not in r.text
    assert view["location"]["method"] == "DRONE_POSITION" and "not the object" in view["location"]["note"]


# ─── Settling and re-correlating ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_correlation_is_refreshed_while_the_event_grows_and_settles_after_its_window():
    w = await _world()
    await _cameras(w)
    at = _last(14)
    sid = await _flying(w, at)
    await _detect(w, at, "fire_smoke", 0.93)
    await _ai(at + timedelta(seconds=5))
    [e] = await _events(sid)
    assert e["cctv_correlated_at"] is not None and e["cctv_final"] is False
    await _ai(at + timedelta(minutes=5))
    [e] = await _events(sid)
    assert e["cctv_final"] is True
    async with AsyncSessionLocal() as db:
        await db.execute(__import__("sqlalchemy").text("SELECT set_config('app.current_tenant', :t, true)"),
                         {"t": str(w["tenant"])})
        assert e["id"] not in await corr.correlate_due(db, at + timedelta(minutes=6))


@pytest.mark.asyncio
async def test_surveying_a_camera_and_correlating_again_drops_one_that_faces_away():
    w = await _world()
    cams = await _cameras(w)
    at = _last(14)
    sid = await _flying(w, at)
    for s in (0, 2, 4):
        await _detect(w, at + timedelta(seconds=s), "intrusion")
    await _ai(at + timedelta(seconds=10))
    [e] = await _events(sid)
    assert "CAM-27" in await _related(e["id"])
    async with _client() as c:
        put = await c.put(f"/api/v1/drones/camera-coverage/{cams['CAM-27']}", headers=w["h_admin"],
                          json={"heading_deg": 0, "fov_deg": 40, "range_m": 60, "notes": "faces the gate"})
        again = await c.post(f"/api/v1/drone-events/{e['id']}/correlate", headers=w["h_op"])
        listed = (await c.get("/api/v1/drones/camera-coverage", headers=w["h_op"])).json()
    assert put.status_code == 200, put.text
    assert again.status_code == 200 and "CAM-27" not in [x["camera_name"] for x in again.json()["cameras"]]
    assert "CAM-27" not in await _related(e["id"])
    assert {x["camera_name"] for x in listed} >= {"CAM-27", "CAM-28", "CAM-29", "CAM-31"}


@pytest.mark.asyncio
async def test_coverage_is_validated():
    w = await _world()
    cams = await _cameras(w)
    no_pos = uuid.uuid4()
    await _run([("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:i,:t,:s,'No position')",
                 {"i": no_pos, "t": w["tenant"], "s": w["site"]})])
    sector = {"heading_deg": 90, "fov_deg": 60, "range_m": 50}
    box = [[1.3008, 103.7998], [1.3008, 103.8002], [1.3012, 103.8002]]
    async with _client() as c:
        put = lambda cam, body, h=w["h_admin"]: c.put(f"/api/v1/drones/camera-coverage/{cam}", json=body, headers=h)  # noqa: E731
        drone_cam = await put(w["camera"], sector)
        unplaced = await put(no_pos, sector)
        half = await put(cams["CAM-27"], {"heading_deg": 90})
        bad_poly = await put(cams["CAM-27"], {"coverage_polygon": [[1.3, 103.8], [1.3, 103.8]]})
        poly = await put(cams["CAM-27"], {"coverage_polygon": box})
        by_operator = await put(cams["CAM-27"], sector, w["h_op"])
        gone = await c.delete(f"/api/v1/drones/camera-coverage/{cams['CAM-27']}", headers=w["h_admin"])
        again = await c.delete(f"/api/v1/drones/camera-coverage/{cams['CAM-27']}", headers=w["h_admin"])
    assert drone_cam.status_code == 409 and unplaced.status_code == 422
    assert half.status_code == 422 and bad_poly.status_code == 422
    assert poly.status_code == 200 and len(poly.json()["coverage_polygon"]) == 3
    assert by_operator.status_code == 403
    assert gone.status_code == 200 and again.status_code == 404
