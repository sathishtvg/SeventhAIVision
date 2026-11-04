"""Phase 5 of the known limits (4 November 2026): a drone's picture withheld over privacy areas
(services/drone_privacy.py, services/privacy_mask.py, routers/drone_privacy.py, migration 0179).

A. Where an area is: inside, within its margin, and an area whose shape cannot be read.
B. A drone's camera: withheld while the drone is at an area, or its position is unknown or stale; given as it is
   elsewhere; a fixed camera's zones as before; no copy-through stream for a watched drone camera.
C. The site gateway's pictures: a snapshot taken at an area is stored blank, with the blank's checksum; a clip
   recorded at one is not kept, and the event says so.
D. Planning: a route crossing an area is told which, and for how long, and saved.
E. Drawing: privacy:manage only, a person's, audited; another organisation sees none of it.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services import drone_privacy, privacy_mask
from tests.test_drone_edge_sync import JPEG, _claimed, _client, _event_with_snapshot, _now, _sql, _sync, _world
from tests.test_investigation_search import _audit

AREAS = "/api/v1/drone-privacy-areas"
#: A square of about 110 m by 110 m north-east of the depot (1.3, 103.8).
SQUARE = [{"lat": 1.3020, "lng": 103.8020}, {"lat": 1.3020, "lng": 103.8030},
          {"lat": 1.3030, "lng": 103.8030}, {"lat": 1.3030, "lng": 103.8020}]


def _area(**kw) -> drone_privacy.Area:
    base = dict(id="a", name="Neighbour's yard", shape="POLYGON",
                points=tuple((p["lat"], p["lng"]) for p in SQUARE), center=None, radius_m=None, margin_m=25.0)
    return drone_privacy.Area(**{**base, **kw})


# ─── A. Where an area is ─────────────────────────────────────────────────────

def test_a_point_is_at_an_area_inside_it_or_within_its_margin():
    yard = _area()
    assert drone_privacy.contains(yard, 1.3025, 103.8025), "inside"
    assert drone_privacy.contains(yard, 1.3018, 103.8025), "about 22 m south of its edge: within 25 m"
    assert not drone_privacy.contains(yard, 1.3015, 103.8025), "about 55 m away"
    assert not drone_privacy.contains(_area(margin_m=0), 1.3018, 103.8025), "no margin: only inside"
    circle = _area(shape="CIRCLE", points=None, center=(1.3, 103.8), radius_m=50.0, margin_m=10.0)
    assert drone_privacy.contains(circle, 1.3005, 103.8) and not drone_privacy.contains(circle, 1.3010, 103.8)
    assert drone_privacy.contains(_area(points=None), 1.0, 100.0), "a shape not known contains everything"
    assert drone_privacy.at(None, None, [yard]) == [yard], "a position not known is at every area"


def test_a_route_is_told_which_areas_it_crosses_and_for_about_how_long():
    yard = _area()
    path = [(1.3025, 103.8010), (1.3025, 103.8040)]  # west to east through the middle of the square
    (crossed,) = drone_privacy.crossings(path, [5.0], [yard])
    assert crossed["name"] == "Neighbour's yard" and 140 <= crossed["metres"] <= 175, crossed
    assert crossed["seconds"] == round(crossed["metres"] / 5.0)
    assert drone_privacy.crossings([(1.3, 103.8), (1.3005, 103.8)], [5.0], [yard]) == []
    assert drone_privacy.crossing_words([crossed]).startswith("This route crosses 1 privacy area: the picture will be "
                                                              "blank for about")


# ─── B. A drone's camera ─────────────────────────────────────────────────────

async def _camera(w: dict, *, lat=1.3025, lng=103.8025, seen_ago: timedelta = timedelta(seconds=1)) -> uuid.UUID:
    cam = uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:c,:t,:s,'Drone One camera')",
               {"c": cam, "t": w["tenant"], "s": w["site"]})
    await _sql("UPDATE drones SET camera_id = :c, current_latitude = :la, current_longitude = :lo, "
               "last_heartbeat_at = now() - make_interval(secs => :ago) WHERE id = :d",
               {"c": cam, "la": lat, "lo": lng, "ago": seen_ago.total_seconds(), "d": w["drone"]})
    return cam


async def _draw(c, w, **body):
    return await c.post(AREAS, headers=w["h_admin"], json={"site_id": str(w["site"]), "name": "Neighbour's yard",
                                                           "shape": "POLYGON", "polygon": SQUARE, **body})


async def test_a_drone_camera_is_withheld_at_an_area_and_when_its_position_is_not_known():
    w = await _world()
    cam = await _camera(w)
    # No areas: the drone's camera is given as it is.
    assert await privacy_mask.read_for(w["tenant"], cam) == ()
    async with _client() as c:
        assert (await _draw(c, w)).status_code == 201
    zones = await privacy_mask.read_for(w["tenant"], cam)
    assert privacy_mask.WITHHELD in zones, "over the yard"
    import numpy as np

    frame = np.full((90, 160, 3), 200, dtype=np.uint8)
    painted = privacy_mask.paint(frame.copy(), zones)
    assert (painted != 200).all(axis=2).mean() > 0.95, "the whole picture painted"
    # Away from it: as it is. Not heard from for a while: withheld again.
    await _sql("UPDATE drones SET current_latitude = 1.3, current_longitude = 103.8 WHERE id = :d", {"d": w["drone"]})
    assert await privacy_mask.read_for(w["tenant"], cam) == ()
    await _sql("UPDATE drones SET last_heartbeat_at = now() - interval '1 minute' WHERE id = :d", {"d": w["drone"]})
    assert privacy_mask.WITHHELD in await privacy_mask.read_for(w["tenant"], cam), "a stale position is not believed"
    # Watched: no copy-through stream, and asked again every couple of seconds.
    privacy_mask.forget(cam)
    assert await privacy_mask.is_masked(w["tenant"], cam) is True
    keeper = privacy_mask.Keeper(w["tenant"], cam, clock=lambda: 100.0)
    await keeper.refresh()
    keeper._clock = lambda: 100.0 + drone_privacy.REFRESH_SECONDS
    assert keeper.due, "a drone's camera is asked again within seconds, not ten"


async def test_a_fixed_camera_is_masked_as_before_whatever_the_drone_areas():
    w = await _world()
    fixed = uuid.uuid4()
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) VALUES (:c,:t,:s,'Gate')",
               {"c": fixed, "t": w["tenant"], "s": w["site"]})
    async with _client() as c:
        await _draw(c, w)
    assert await privacy_mask.read_for(w["tenant"], fixed) == ()
    privacy_mask.forget(fixed)
    assert await privacy_mask.is_masked(w["tenant"], fixed) is False


# ─── C. The gateway's pictures ───────────────────────────────────────────────

async def test_a_snapshot_taken_at_an_area_is_stored_blank_and_a_clip_there_is_not_kept():
    w = await _world()
    stored = None
    try:
        async with _client() as c:
            assert (await _draw(c, w)).status_code == 201
            s = await _claimed(c, w)
            # The drone is over the yard: its telemetry says so.
            await _sql("UPDATE drones SET current_latitude = 1.3025, current_longitude = 103.8025, last_heartbeat_at = now() "
                       "WHERE id = :d", {"d": w["drone"]})
            ev = str(uuid.uuid4())
            snap, clip = str(uuid.uuid4()), str(uuid.uuid4())
            at = {"latitude": 1.3025, "longitude": 103.8025}
            body = (await _sync(
                c, w["key"],
                events=[{"client_ref": ev, "drone_id": str(w["drone"]), "session_id": s["id"], "module_type": "intrusion",
                         "detected_at": _now().isoformat(), "drone_latitude": 1.3025, "drone_longitude": 103.8025}],
                media=[{"client_ref": snap, "event_client_ref": ev, "media_kind": "SNAPSHOT", "captured_at": _now().isoformat(),
                        "checksum_sha256": hashlib.sha256(JPEG).hexdigest(), "size_bytes": len(JPEG),
                        "telemetry_snapshot": at},
                       {"client_ref": clip, "event_client_ref": ev, "media_kind": "EVENT_CLIP",
                        "captured_at": _now().isoformat(), "checksum_sha256": "c" * 64, "size_bytes": 5000,
                        "duration_seconds": 10, "telemetry_snapshot": at}])).json()
            assert body["media"]["accepted"] == 2, body
            assert clip not in body.get("media_upload_requested", []), "a clip not kept is not asked for"
            up = await c.put(f"/api/v1/drone-edge/media/{snap}", content=JPEG,
                             headers={"X-Gateway-Key": w["key"], "X-Checksum-Sha256": hashlib.sha256(JPEG).hexdigest()})
            assert up.status_code == 200, up.text
            (row,) = await _sql("SELECT id, event_id, storage_path, checksum_sha256, size_bytes FROM drone_event_media "
                                "WHERE client_ref = :r", {"r": uuid.UUID(snap)})
            stored = Path(settings.EVIDENCE_ROOT) / row["storage_path"]
            content = stored.read_bytes()
            event = (await c.get(f"/api/v1/drone-events/{row['event_id']}", headers=w["h_op"])).json()
        assert content != JPEG and content[:2] == b"\xff\xd8", "stored as a blank picture, not what was sent"
        assert row["checksum_sha256"] == hashlib.sha256(content).hexdigest() and row["size_bytes"] == len(content)
        assert await _sql("SELECT 1 FROM drone_event_media WHERE client_ref = :r", {"r": uuid.UUID(clip)}) == []
        (shot,) = [m for m in event["media"] if m["media_kind"] == "SNAPSHOT"]
        assert shot["withheld_reason"].startswith("Stored blank: it was taken over a privacy area (Neighbour's yard)")
        (kept_not,) = event["withheld"]
        assert kept_not["media_kind"] == "EVENT_CLIP" and kept_not["action"] == "NOT_KEPT"
        assert kept_not["reason"].startswith("Not kept: it was recorded over a privacy area")
    finally:
        if stored is not None and stored.exists():
            stored.unlink()


async def test_a_picture_away_from_every_area_is_kept_as_it_was_sent():
    w = await _world()
    async with _client() as c:
        await _draw(c, w)
        s = await _claimed(c, w)
        _, ref = await _event_with_snapshot(c, w, s)  # the drone is at the depot, 300 m from the yard
    # The snapshot was described with no position of its own: the drone's track around it is read; none was sent,
    # so its position is not known and it is withheld - what is not known is not shown.
    (row,) = await _sql("SELECT action, reason FROM drone_media_withheld WHERE client_ref = :r", {"r": uuid.UUID(ref)})
    assert row["action"] == "BLANKED" and "its position is not known" in row["reason"]
    # With a position away from the yard it is kept as sent.
    async with _client() as c:
        ev, media = str(uuid.uuid4()), str(uuid.uuid4())
        await _sync(c, w["key"],
                    events=[{"client_ref": ev, "drone_id": str(w["drone"]), "session_id": s["id"],
                             "module_type": "intrusion", "detected_at": _now().isoformat()}],
                    media=[{"client_ref": media, "event_client_ref": ev, "media_kind": "SNAPSHOT",
                            "captured_at": _now().isoformat(), "checksum_sha256": hashlib.sha256(JPEG).hexdigest(),
                            "size_bytes": len(JPEG), "telemetry_snapshot": {"latitude": 1.3, "longitude": 103.8}}])
    assert await _sql("SELECT 1 FROM drone_media_withheld WHERE client_ref = :r", {"r": uuid.UUID(media)}) == []


# ─── D. Planning ─────────────────────────────────────────────────────────────

async def test_a_route_that_crosses_an_area_is_told_and_saved():
    w = await _world()
    async with _client() as c:
        await _draw(c, w)
        route = await c.put(f"/api/v1/drone-routes/{w['route']}/waypoints", headers=w["h_admin"], json={"waypoints": [
            {"latitude": 1.3025, "longitude": 103.8010}, {"latitude": 1.3025, "longitude": 103.8040}]})
        assert route.status_code == 200, route.text
        summary = route.json()["summary"]
        mission = (await c.get(f"/api/v1/drone-missions/{w['mission']}", headers=w["h_admin"])).json()
    (crossed,) = summary["privacy_crossings"]
    assert crossed["name"] == "Neighbour's yard" and crossed["seconds"] > 0
    assert summary["privacy_warning"].startswith("This route crosses 1 privacy area")
    assert mission["privacy_warning"] == summary["privacy_warning"]


# ─── E. Drawing ──────────────────────────────────────────────────────────────

async def test_privacy_areas_are_drawn_by_privacy_managers_audited_and_kept_when_taken_away():
    w, other = await _world(), await _world()
    async with _client() as c:
        refused = await c.post(AREAS, headers=w["h_op"], json={"site_id": str(w["site"]), "name": "X", "shape": "POLYGON",
                                                               "polygon": SQUARE})
        assert refused.status_code == 403
        for bad in ({"polygon": SQUARE[:2]}, {"margin_m": 500}, {"name": " "}, {"shape": "CIRCLE"}):
            assert (await _draw(c, w, **bad)).status_code == 422, bad
        made = await _draw(c, w, margin_m=40)
        assert made.status_code == 201 and float(made.json()["margin_m"]) == 40
        listed = await c.get(AREAS, headers=w["h_op"])
        assert listed.status_code == 200 and [a["name"] for a in listed.json()["areas"]] == ["Neighbour's yard"]
        assert (await c.get(AREAS, headers=other["h_op"])).json()["areas"] == []
        assert (await c.post(f"{AREAS}/{made.json()['id']}/remove", headers=other["h_admin"])).status_code == 404
        gone = await c.post(f"{AREAS}/{made.json()['id']}/remove", headers=w["h_admin"])
        assert gone.status_code == 200 and (await c.get(AREAS, headers=w["h_op"])).json()["areas"] == []
    kept = await _sql("SELECT removed_at, removed_by_user_id FROM drone_privacy_areas WHERE id = :a",
                      {"a": uuid.UUID(made.json()["id"])})
    assert kept[0]["removed_at"] is not None and kept[0]["removed_by_user_id"] == w["admin"]
    actions = [a["action"] for a in await _audit({"tenant": w["tenant"]}, "drone.privacy_area.draw")]
    assert actions == ["drone.privacy_area.draw"]
    # As the application's role in the other organisation's scope, the area is not there.
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(other["tenant"])})
        assert (await db.execute(text("SELECT count(*) FROM drone_privacy_areas"))).scalar() == 0
        await db.rollback()
