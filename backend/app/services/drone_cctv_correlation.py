"""CCTV correlation against the database: for a drone event, the site's fixed
cameras that could have seen it, what they detected and recorded in its window,
and whether any of them corroborates the drone.

  drone event ─► where (the drone's position) ─► nearby / covering cameras
              ─► the event's window (the site's clip bounds either side)
              ─► per camera: detections, the most serious alert, the recording
                 covering the moment and the offset into it, online or not
              ─► corroboration ─► the event is re-assessed (a second sensor
                 agreeing verifies it and raises its risk)

Run by the drone runner's AI job for every event not yet settled, at most every
REFRESH_EVERY while it grows, and settled once late detections can no longer
arrive. An operator can ask for it again (POST /drone-events/{id}/correlate).

Reads cameras, streams, recordings, detections, lpr_events and alerts; writes
only drone_event_cameras and two columns on drone_events. Runs inside a
transaction the caller owns, with the tenant set; announces through `out`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import drone_cctv as cctv
from app.services import drone_sessions as ds
from app.services import recording_policy

DEFAULT_CLIP_PRE_S, DEFAULT_CLIP_POST_S = 20, 60
SEVERITY_ORDER = "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"


async def _window(db: AsyncSession, event: dict) -> tuple[datetime, datetime, int, int]:
    pre, post = await recording_policy.get_effective_clip_bounds(
        db, str(event["site_id"]), DEFAULT_CLIP_PRE_S, DEFAULT_CLIP_POST_S)
    last = event.get("last_detected_at") or event["detected_at"]
    return event["detected_at"] - timedelta(seconds=pre), last + timedelta(seconds=post), pre, post


async def _site_cameras(db: AsyncSession, site_id) -> list[dict]:
    """The site's fixed cameras — never a drone's own camera — with their
    surveyed coverage, if any."""
    rows = (await db.execute(text("""
        SELECT c.id, c.name, c.latitude, c.longitude, c.is_active,
               cov.heading_deg, cov.fov_deg, cov.range_m, cov.coverage_polygon, cov.id AS coverage_id
          FROM cameras c
          LEFT JOIN drone_camera_coverage cov ON cov.camera_id = c.id
         WHERE c.site_id = :site AND c.latitude IS NOT NULL AND c.longitude IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM drones d WHERE d.camera_id = c.id)
    """), {"site": site_id})).mappings().all()
    out = []
    for r in rows:
        r = dict(r)
        r["coverage"] = ({"heading_deg": r["heading_deg"], "fov_deg": r["fov_deg"], "range_m": r["range_m"],
                          "coverage_polygon": r["coverage_polygon"]} if r["coverage_id"] else None)
        out.append(r)
    return out


async def correlate(db: AsyncSession, event_id, now: datetime, out: ds.Announcements) -> dict | None:
    """Correlate one event now. Returns {cameras, corroborating, changed} or
    None if the event is gone."""
    event = (await db.execute(text("SELECT * FROM drone_events WHERE id = :id FOR UPDATE"),
                              {"id": event_id})).mappings().first()
    if event is None:
        return None
    event = dict(event)
    start, end, _, _ = await _window(db, event)
    final = now >= end + cctv.LATE_DETECTIONS
    before = {str(r["camera_id"]): r["corroborates"] for r in (await db.execute(text(
        "SELECT camera_id, corroborates FROM drone_event_cameras WHERE event_id = :e"),
        {"e": event_id})).mappings().all()}

    found: list[cctv.Candidate] = []
    if event["drone_latitude"] is not None and event["drone_longitude"] is not None:
        found = cctv.candidates(await _site_cameras(db, event["site_id"]),
                                float(event["drone_latitude"]), float(event["drone_longitude"]))
    at = event["detected_at"]
    for cand in found:
        dets = (await db.execute(text("""
            SELECT d.id, d.module_type, d.confidence, d.detected_at, l.plate_number
              FROM detections d
              LEFT JOIN lpr_events l ON l.detection_id = d.id AND l.detected_at = d.detected_at
             WHERE d.camera_id = :c AND d.detected_at BETWEEN :a AND :b
             ORDER BY d.detected_at
        """), {"c": cand.camera_id, "a": start, "b": end})).mappings().all()
        agreeing = [d for d in dets if cctv.corroborates(event["module_type"], event.get("label"),
                                                         d["module_type"], d["plate_number"])]
        pick = (min(agreeing, key=lambda d: abs((d["detected_at"] - at).total_seconds())) if agreeing
                else max(dets, key=lambda d: float(d["confidence"] or 0), default=None))
        alert = (await db.execute(text(f"""
            SELECT id FROM alerts WHERE camera_id = :c AND created_at BETWEEN :a AND :b
             ORDER BY {SEVERITY_ORDER} DESC, created_at LIMIT 1
        """), {"c": cand.camera_id, "a": start, "b": end})).scalar()
        rec = (await db.execute(text("""
            SELECT id, started_at FROM recordings
             WHERE camera_id = :c AND started_at <= :t AND (ended_at IS NULL OR ended_at >= :t)
               AND status IN ('recording','completed')
             ORDER BY started_at DESC LIMIT 1
        """), {"c": cand.camera_id, "t": at})).mappings().first()
        online = (await db.execute(text(
            "SELECT bool_or(status = 'online') FROM streams WHERE camera_id = :c"), {"c": cand.camera_id})).scalar()
        await db.execute(text("""
            INSERT INTO drone_event_cameras
                (tenant_id, event_id, camera_id, camera_name, distance_m, correlation_method, bearing_deg,
                 in_coverage, corroborates, related_detection_count, related_detection_id, related_module_type,
                 related_detected_at, related_alert_id, recording_id, recording_offset_s, camera_online, rank,
                 window_start, window_end, updated_at)
            VALUES (current_setting('app.current_tenant')::uuid, :e, :c, :name, :dist, :method, :bearing,
                    :cov, :agree, :n, :det, :mod, :det_at, :alert, :rec, :off, :online, :rank, :a, :b, now())
            ON CONFLICT (event_id, camera_id) DO UPDATE SET
                camera_name = EXCLUDED.camera_name, distance_m = EXCLUDED.distance_m,
                correlation_method = EXCLUDED.correlation_method, bearing_deg = EXCLUDED.bearing_deg,
                in_coverage = EXCLUDED.in_coverage, corroborates = EXCLUDED.corroborates,
                related_detection_count = EXCLUDED.related_detection_count,
                related_detection_id = EXCLUDED.related_detection_id,
                related_module_type = EXCLUDED.related_module_type,
                related_detected_at = EXCLUDED.related_detected_at, related_alert_id = EXCLUDED.related_alert_id,
                recording_id = EXCLUDED.recording_id, recording_offset_s = EXCLUDED.recording_offset_s,
                camera_online = EXCLUDED.camera_online, rank = EXCLUDED.rank,
                window_start = EXCLUDED.window_start, window_end = EXCLUDED.window_end, updated_at = now()
        """), {"e": event_id, "c": cand.camera_id, "name": cand.name, "dist": cand.distance_m,
               "method": cand.method, "bearing": cand.bearing_deg, "cov": cand.in_coverage,
               "agree": bool(agreeing), "n": len(dets), "det": pick["id"] if pick else None,
               "mod": pick["module_type"] if pick else None, "det_at": pick["detected_at"] if pick else None,
               "alert": alert, "rec": rec["id"] if rec else None,
               "off": round((at - rec["started_at"]).total_seconds(), 2) if rec else None,
               "online": online, "rank": cand.rank, "a": start, "b": end})
    after = {c.camera_id for c in found}
    gone = [cid for cid in before if cid not in after]
    if gone:
        # Coverage was surveyed and the camera turns out to face away.
        await db.execute(text("DELETE FROM drone_event_cameras WHERE event_id = :e "
                              "   AND camera_id = ANY(CAST(:ids AS uuid[]))"), {"e": event_id, "ids": gone})
    await db.execute(text("UPDATE drone_events SET cctv_correlated_at = :now, cctv_final = :final WHERE id = :e"),
                     {"now": now, "final": final, "e": event_id})
    agreeing_now = {str(r["camera_id"]) for r in (await db.execute(text(
        "SELECT camera_id FROM drone_event_cameras WHERE event_id = :e AND corroborates"),
        {"e": event_id})).mappings().all()}
    agreeing_before = {cid for cid, agree in before.items() if agree}
    changed = set(before) != after or agreeing_now != agreeing_before
    if changed:
        out.add("drone_event_cctv_updated", {"event_id": str(event_id), "cameras": len(after),
                                             "corroborating": len(agreeing_now)})
    return {"cameras": len(after), "corroborating": len(agreeing_now), "changed": changed,
            "corroboration_changed": agreeing_now != agreeing_before}


async def correlate_due(db: AsyncSession, now: datetime, limit: int = 100) -> list:
    """Events whose correlation is not settled and not refreshed recently."""
    return list((await db.execute(text("""
        SELECT id FROM drone_events
         WHERE NOT cctv_final AND detected_at > CAST(:now AS timestamptz) - interval '1 day'
           AND (cctv_correlated_at IS NULL OR cctv_correlated_at <= CAST(:cut AS timestamptz))
         ORDER BY detected_at LIMIT :n
    """), {"now": now, "cut": now - cctv.REFRESH_EVERY, "n": limit})).scalars().all())


async def view(db: AsyncSession, event: dict) -> dict:
    """What an operator opens next to the drone's view: each related camera,
    where to watch it live, where to play back the moment, and what it saw."""
    start, end, pre, post = await _window(db, event)
    rows = (await db.execute(text("""
        SELECT ec.*, a.severity AS related_alert_severity, a.title AS related_alert_title,
               r.started_at AS recording_started_at, r.status AS recording_status
          FROM drone_event_cameras ec
          LEFT JOIN alerts a ON a.id = ec.related_alert_id
          LEFT JOIN recordings r ON r.id = ec.recording_id
         WHERE ec.event_id = :e
         ORDER BY ec.rank NULLS LAST, ec.distance_m NULLS LAST
    """), {"e": event["id"]})).mappings().all()
    cameras = []
    for r in rows:
        r = dict(r)
        streams = (await db.execute(text(
            "SELECT id, status FROM streams WHERE camera_id = :c ORDER BY created_at"), {"c": r["camera_id"]}
        )).mappings().all() if r["camera_id"] else []
        cid = r["camera_id"]
        r["streams"] = [{"stream_id": s["id"], "status": s["status"],
                         "live_path": f"/api/v1/cameras/{cid}/streams/{s['id']}/live",
                         "hls_path": f"/api/v1/cameras/{cid}/streams/{s['id']}/hls/index.m3u8"} for s in streams]
        r["playback"] = ({"recording_id": r["recording_id"], "offset_s": r["recording_offset_s"],
                          "started_at": r["recording_started_at"], "status": r["recording_status"],
                          "download_path": f"/api/v1/cameras/recordings/{r['recording_id']}/download"}
                         if r["recording_id"] else None)
        cameras.append(r)
    return {
        "event_id": event["id"], "module_type": event["module_type"], "label": event.get("label"),
        "location": {"latitude": event["drone_latitude"], "longitude": event["drone_longitude"],
                     "method": event["location_method"],
                     "note": "The drone's position when it saw this, not the object's own position."},
        "detected_at": event["detected_at"], "last_detected_at": event.get("last_detected_at"),
        "window": {"start": start, "end": end, "pre_seconds": pre, "post_seconds": post},
        "correlated_at": event.get("cctv_correlated_at"), "settled": event.get("cctv_final"),
        "nearby_radius_m": cctv.NEARBY_RADIUS_M,
        "cameras": cameras,
        "corroborating": [c["camera_name"] for c in cameras if c["corroborates"]],
    }
