"""After a drone event: the incident, the guard, the second look, the resolution.

  AI event ─► risk ─► alert ─► INCIDENT ─► officer ─► GUARD ─► resolution
                                   ▲                    │
                                   └── verify with drone ┘

THE PLATFORM'S OWN INCIDENT SYSTEM. A drone incident is a row in `incidents`,
with its notes in `incident_notes` and its status history in
`incident_status_history`, dispatched through the existing dispatch endpoint's
own function. It appears in every existing incident list, timeline, SLA and
mobile screen unchanged. There is no parallel incident system.

  - linked to the drone's camera, so the existing lists show its site and apply
    site access to it (a drone with no camera gives an incident with no site);
  - its alert is the drone event's alert;
  - message_params carries the drone facts (event, session, mission, zone, risk,
    confidence, position, related cameras) and a display reference, since
    `incidents` has no number column;
  - snapshots, clips and CCTV playback stay on the drone event (outside the
    evidence purge, decision D5) and the incident points at the event.

A WORKER'S INCIDENT IS NOT DUPLICATED. If the worker that detected the thing
already opened an incident (weapons, fire, PPE and falls do), the drone event is
linked to it.

NEAREST AVAILABLE GUARD. Guards on shift at the site, not already dispatched to
an open incident, ranked by distance from their last known position: their
latest checkpoint scan or status update this shift, else where they checked in.
The position's source and age are returned with it — it is where they were, not
where they are.

VERIFY WITH DRONE holds an active flight where it is, through the ordinary
command queue, and resumes it afterwards. The flight must be active, still near
the spot, able to pause and resume, and have battery to spare; nothing here can
steer the aircraft. While it holds, the AI pipeline keeps working: new
detections join the event, re-score it and update its incident.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import drone_ai as ai
from app.services import drone_flight_plan as fp
from app.services import drone_sessions as ds
from app.services.drone_providers import Capability, capabilities_of
from app.services.geofence import haversine_meters

VERIFY_MAX_DISTANCE_M = 75.0
VERIFY_BATTERY_MARGIN_PCT = 10
WHAT = {"intrusion": "Possible unauthorised person", "crowd": "Crowd", "face": "Person",
        "lpr": "Vehicle", "weapon": "Weapon", "fire_smoke": "Fire or smoke", "ppe": "PPE violation",
        "fall": "Person down", "behavior": "Behaviour", "abandoned": "Abandoned object", "tampering": "Tampering"}


def incident_ref(incident_id, created_at: datetime) -> str:
    return f"INC-{created_at:%Y%m%d}-{str(incident_id).replace('-', '')[:8].upper()}"


def _s(v) -> str | None:
    return str(v) if v is not None else None


# ── The facts an operator sees ───────────────────────────────────────────────

async def event_card(db: AsyncSession, event_id) -> dict | None:
    """The command-centre alert card: what, where, which drone and mission,
    when (site time), AI confidence, risk — and the related cameras."""
    e = (await db.execute(text("""
        SELECT e.*, s.name AS site_name, d.code AS drone_code, d.name AS drone_name, d.camera_id AS drone_camera_id,
               ps.mission_name, ps.session_number, ps.status AS session_status,
               t.timezone AS tenant_timezone
          FROM drone_events e
          LEFT JOIN sites s ON s.id = e.site_id
          LEFT JOIN drones d ON d.id = e.drone_id
          LEFT JOIN drone_patrol_sessions ps ON ps.id = e.session_id
          LEFT JOIN tenants t ON t.id = e.tenant_id
         WHERE e.id = :id
    """), {"id": event_id})).mappings().first()
    if e is None:
        return None
    e = dict(e)
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(e["tenant_timezone"] or "Asia/Singapore")
    except Exception:
        tz = ZoneInfo("Asia/Singapore")
    cams = [dict(r) for r in (await db.execute(text("""
        SELECT camera_id, camera_name, correlation_method, corroborates, distance_m
          FROM drone_event_cameras WHERE event_id = :id ORDER BY rank NULLS LAST, distance_m NULLS LAST
    """), {"id": event_id})).mappings().all()]
    what = WHAT.get(e["module_type"], e["module_type"])
    if e.get("label"):
        what += f" ({e['label']})"
    return {
        "event_id": e["id"], "headline": what, "module_type": e["module_type"], "label": e.get("label"),
        "site_id": e["site_id"], "site_name": e["site_name"],
        "area": e["zone_name"], "zone_type": e["zone_type"],
        "drone_id": e["drone_id"], "drone_code": e["drone_code"], "drone_name": e["drone_name"],
        "drone_camera_id": e["drone_camera_id"],
        "session_id": e["session_id"], "session_number": e["session_number"], "mission_name": e["mission_name"],
        "session_status": e["session_status"], "waypoint_sequence": e["waypoint_sequence"],
        "detected_at": e["detected_at"], "detected_at_site_time": e["detected_at"].astimezone(tz).isoformat(),
        "ai_confidence": float(e["ai_confidence"]) if e["ai_confidence"] is not None else None,
        "risk_level": e["risk_level"], "risk_score": e["risk_score"], "risk_factors": e["risk_factors"],
        "verification_state": e["verification_state"], "status": e["status"],
        "latitude": e["drone_latitude"], "longitude": e["drone_longitude"], "location_method": e["location_method"],
        "alert_id": e["alert_id"], "incident_id": e["incident_id"],
        "related_cameras": cams,
    }


def _description(card: dict, ref: str) -> str:
    lines = [f"Drone patrol event — {card['headline']}  [{ref}]",
             f"Site: {card['site_name'] or '—'}" + (f" · Area: {card['area']} ({(card['zone_type'] or '').lower().replace('_', ' ')})"
                                                    if card["area"] else ""),
             f"Drone: {card['drone_code'] or '—'} {card['drone_name'] or ''}".rstrip()
             + (f" · Mission: {card['mission_name']}" if card["mission_name"] else "")
             + (f" · Session {card['session_number']}" if card["session_number"] else "")]
    if card["waypoint_sequence"] is not None:
        lines.append(f"Waypoint: {card['waypoint_sequence']}")
    lines.append(f"Time: {card['detected_at_site_time']} (site time)")
    conf = f"{card['ai_confidence'] * 100:.0f}%" if card["ai_confidence"] is not None else "—"
    lines.append(f"AI confidence: {conf} · Risk: {card['risk_level']} ({card['risk_score']}/100) — two different things")
    why = [f["detail"] for f in (card["risk_factors"] or []) if f.get("points", 0) > 0]
    if why:
        lines.append("Why: " + "; ".join(why))
    if card["latitude"] is not None:
        lines.append(f"Location: {card['latitude']:.5f}, {card['longitude']:.5f} (the drone's position, "
                     "not the object's)")
    if card["related_cameras"]:
        lines.append("Related cameras: " + ", ".join(
            f"{c['camera_name']} ({'covers' if c['correlation_method'] == 'COVERAGE' else 'nearby'}"
            f"{', saw it too' if c['corroborates'] else ''})" for c in card["related_cameras"]))
    lines.append(f"Evidence: drone event {card['event_id']} — its snapshots, clips and CCTV playback are "
                 "there.")
    return "\n".join(lines)


# ── Incidents ────────────────────────────────────────────────────────────────

async def open_incident(db: AsyncSession, event_id, now: datetime, out: ds.Announcements, *,
                        by_user_id: str | None = None, reason: str | None = None) -> tuple[str, bool]:
    """The event's incident — opened now if it has none. Returns (id, created)."""
    e = (await db.execute(text("SELECT * FROM drone_events WHERE id = :id FOR UPDATE"),
                          {"id": event_id})).mappings().first()
    if e is None:
        raise LookupError("event not found")
    e = dict(e)
    if e["incident_id"]:
        return str(e["incident_id"]), False

    worker = (await db.execute(text("""
        SELECT i.id FROM drone_observations o
          JOIN incidents i ON i.alert_id = CAST(o.attributes->>'worker_alert_id' AS uuid)
         WHERE o.event_id = :e
         ORDER BY CASE i.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC
         LIMIT 1
    """), {"e": event_id})).scalar()
    card = await event_card(db, event_id)
    if worker is not None:
        await _link(db, e, worker)
        await _note(db, worker, by_user_id, f"Linked to drone event {event_id} ({card['headline']}, risk "
                                            f"{card['risk_level']}) — the drone saw it too.")
        return str(worker), False

    severity = ai.INCIDENT_SEVERITY.get(e["risk_level"] or "MEDIUM", "medium")
    created_at = now
    incident_id = (await db.execute(text("SELECT gen_random_uuid()"))).scalar()
    ref = incident_ref(incident_id, created_at)
    params = {"incident_ref": ref, "drone_event_id": str(event_id), "session_id": _s(e["session_id"]),
              "drone_id": _s(e["drone_id"]), "mission_id": _s(e["mission_id"]), "site_id": _s(e["site_id"]),
              "zone_id": _s(e["security_zone_id"]), "zone_name": e["zone_name"],
              "module_type": e["module_type"], "label": e.get("label"), "waypoint_sequence": e["waypoint_sequence"],
              "ai_confidence": card["ai_confidence"], "risk_level": e["risk_level"], "risk_score": e["risk_score"],
              "latitude": e["drone_latitude"], "longitude": e["drone_longitude"],
              "related_cameras": [c["camera_name"] for c in card["related_cameras"]]}
    title = f"Drone: {card['headline']}" + (f" — {card['area']}" if card["area"] else "") + f" ({e['risk_level']})"
    await db.execute(text("""
        INSERT INTO incidents (id, tenant_id, alert_id, camera_id, title, description, alert_code, message_params,
                               severity, status, is_auto_created, created_at, updated_at)
        VALUES (:id, current_setting('app.current_tenant')::uuid, :alert, :cam, :title, :descr, :code,
                CAST(:params AS jsonb), :sev, 'open', :auto, :now, :now)
    """), {"id": incident_id, "alert": e["alert_id"], "cam": card["drone_camera_id"], "title": title[:255],
           "descr": _description(card, ref) + (f"\nOpened by an officer: {reason}" if reason else ""),
           "code": f"drone.{e['module_type']}", "params": json.dumps(fp.jsonable(params)), "sev": severity,
           "auto": by_user_id is None, "now": created_at})
    await db.execute(text("""
        INSERT INTO incident_status_history (tenant_id, incident_id, changed_by_user_id, from_status, to_status,
                                             latitude, longitude, notes, changed_at)
        VALUES (current_setting('app.current_tenant')::uuid, :i, CAST(:u AS uuid), NULL, 'open', :lat, :lng, :n, :now)
    """), {"i": incident_id, "u": by_user_id, "lat": e["drone_latitude"], "lng": e["drone_longitude"],
           "n": f"Opened from drone event ({'automatically, ' + e['risk_level'] + ' risk' if by_user_id is None else 'by an officer'}).",
           "now": created_at})
    await _link(db, e, incident_id)
    if e["session_id"]:
        await db.execute(text("UPDATE drone_patrol_sessions SET incident_count = incident_count + 1 WHERE id = :s"),
                         {"s": e["session_id"]})
    out.add("incident_created", {"incident_id": str(incident_id), "title": title[:255], "severity": severity,
                                 "camera_id": _s(card["drone_camera_id"]), "incident_ref": ref,
                                 "drone_event_id": str(event_id), "site_id": _s(e["site_id"]),
                                 "is_auto_created": by_user_id is None})
    return str(incident_id), True


async def _link(db: AsyncSession, e: dict, incident_id) -> None:
    await db.execute(text("""
        UPDATE drone_events SET incident_id = :i, updated_at = now(),
               status = CASE WHEN status IN ('NEW','ACKNOWLEDGED','INVESTIGATING') THEN 'ESCALATED' ELSE status END
         WHERE id = :e
    """), {"i": incident_id, "e": e["id"]})


async def _note(db: AsyncSession, incident_id, user_id, note: str) -> None:
    await db.execute(text("""
        INSERT INTO incident_notes (tenant_id, incident_id, author_user_id, note)
        VALUES (current_setting('app.current_tenant')::uuid, :i, CAST(:u AS uuid), :n)
    """), {"i": incident_id, "u": user_id, "n": note})


async def on_assessed(db: AsyncSession, event: dict, level: str, verified: bool, zone: dict | None,
                      rule: dict | None, factors: list[dict], now: datetime, out: ds.Announcements) -> None:
    """After every assessment: open the incident the rules call for, or raise
    the severity of the drone's own incident if the risk has risen."""
    if event.get("incident_id"):
        await _escalate(db, event, level, factors, out)
    elif ai.incident_due(level, verified, zone, rule):
        await open_incident(db, event["id"], now, out)


async def _escalate(db: AsyncSession, event: dict, level: str, factors: list[dict], out: ds.Announcements) -> None:
    inc = (await db.execute(text(
        "SELECT id, severity, status, message_params FROM incidents WHERE id = :i"),
        {"i": event["incident_id"]})).mappings().first()
    if inc is None or inc["status"] in ("resolved", "closed"):
        return
    if (inc["message_params"] or {}).get("drone_event_id") != str(event["id"]):
        return            # a worker's incident: the drone adds notes, not severity
    new = ai.INCIDENT_SEVERITY.get(level, "medium")
    if ai.SEVERITY_RANK.get(new, 0) <= ai.SEVERITY_RANK.get(inc["severity"], 0):
        return
    await db.execute(text("UPDATE incidents SET severity = :s, updated_at = now() WHERE id = :i"),
                     {"s": new, "i": inc["id"]})
    why = "; ".join(f["detail"] for f in factors if f.get("points", 0) > 0)
    await _note(db, inc["id"], None, f"Drone risk rose to {level}: {why}.")
    out.add("incident_updated", {"incident_id": str(inc["id"]), "severity": new,
                                 "previous_severity": inc["severity"], "drone_event_id": str(event["id"])})


async def sync_resolutions(db: AsyncSession, now: datetime, out: ds.Announcements) -> int:
    """An event whose incident has been resolved or closed is resolved too."""
    rows = (await db.execute(text("""
        UPDATE drone_events e SET status = 'RESOLVED', resolved_at = COALESCE(i.resolved_at, CAST(:now AS timestamptz)),
               updated_at = now()
          FROM incidents i
         WHERE i.id = e.incident_id AND i.status IN ('resolved','closed')
           AND e.status NOT IN ('RESOLVED','FALSE_POSITIVE')
        RETURNING e.id, i.id AS incident_id
    """), {"now": now})).mappings().all()
    for r in rows:
        out.add("drone_event_updated", {"event_id": str(r["id"]), "status": "RESOLVED",
                                        "incident_id": str(r["incident_id"]), "reason": "incident resolved"})
    return len(rows)


# ── Guards ───────────────────────────────────────────────────────────────────

async def available_guards(db: AsyncSession, event: dict, now: datetime) -> list[dict]:
    """Guards on shift at the event's site, nearest first, free before busy."""
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (s.guard_user_id)
               s.guard_user_id AS user_id, u.full_name, u.role_id, s.actual_start,
               s.check_in_lat, s.check_in_lon,
               cs.latitude AS scan_lat, cs.longitude AS scan_lng, cs.scanned_at,
               h.latitude AS upd_lat, h.longitude AS upd_lng, h.changed_at,
               (SELECT i.id FROM incidents i WHERE i.dispatched_guard_id = s.guard_user_id
                   AND i.status NOT IN ('resolved','closed') ORDER BY i.dispatched_at DESC NULLS LAST LIMIT 1)
                   AS busy_incident_id
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id AND u.is_active
          LEFT JOIN LATERAL (SELECT latitude, longitude, scanned_at FROM checkpoint_scans
                              WHERE guard_user_id = s.guard_user_id AND latitude IS NOT NULL
                                AND scanned_at >= s.actual_start ORDER BY scanned_at DESC LIMIT 1) cs ON TRUE
          LEFT JOIN LATERAL (SELECT latitude, longitude, changed_at FROM incident_status_history
                              WHERE changed_by_user_id = s.guard_user_id AND latitude IS NOT NULL
                                AND changed_at >= s.actual_start ORDER BY changed_at DESC LIMIT 1) h ON TRUE
         WHERE s.site_id = :site AND s.actual_start IS NOT NULL AND s.actual_start <= :now
           AND (s.actual_end IS NULL OR s.actual_end >= :now)
         ORDER BY s.guard_user_id, s.actual_start DESC
    """), {"site": event["site_id"], "now": now})).mappings().all()
    lat, lng = event.get("drone_latitude"), event.get("drone_longitude")
    guards = []
    for r in rows:
        known = [(r["scanned_at"], r["scan_lat"], r["scan_lng"], "checkpoint scan"),
                 (r["changed_at"], r["upd_lat"], r["upd_lng"], "incident status update"),
                 (r["actual_start"], r["check_in_lat"], r["check_in_lon"], "shift check-in")]
        known = [k for k in known if k[0] is not None and k[1] is not None and k[2] is not None]
        at, glat, glng, source = max(known, key=lambda k: k[0]) if known else (None, None, None, None)
        dist = (round(haversine_meters(glat, glng, lat, lng), 1)
                if glat is not None and lat is not None else None)
        guards.append({"user_id": r["user_id"], "full_name": r["full_name"], "role_id": r["role_id"],
                       "available": r["busy_incident_id"] is None, "busy_incident_id": r["busy_incident_id"],
                       "distance_m": dist, "position_source": source, "position_at": at,
                       "position_age_s": round((now - at).total_seconds()) if at else None})
    guards.sort(key=lambda g: (not g["available"], g["distance_m"] is None, g["distance_m"] or 0))
    return guards


# ── Verify with drone ────────────────────────────────────────────────────────

class VerificationRefused(Exception):
    def __init__(self, status: int, reason: str, existing: dict | None = None):
        super().__init__(reason)
        self.status, self.reason, self.existing = status, reason, existing


async def request_verification(db: AsyncSession, event: dict, user_id: str | None, hold_seconds: int,
                               reason: str | None, now: datetime) -> dict:
    """Check the flight, then queue the hold. Every refusal says why."""
    if not event.get("session_id"):
        raise VerificationRefused(409, "This event did not come from a flight.")
    s = (await db.execute(text("SELECT * FROM drone_patrol_sessions WHERE id = :s FOR UPDATE"),
                          {"s": event["session_id"]})).mappings().first()
    if s is None:
        raise VerificationRefused(409, "The flight no longer exists.")
    active = (await db.execute(text(
        "SELECT * FROM drone_verification_requests WHERE session_id = :s AND status IN ('REQUESTED','HOLDING')"),
        {"s": s["id"]})).mappings().first()
    if active is not None:
        raise VerificationRefused(409, "This flight is already holding for a verification.", dict(active))
    if s["status"] != "ACTIVE":
        raise VerificationRefused(409, f"The flight is {s['status'].lower()}; only an active flight can hold "
                                       "to look again.")
    drone = (await db.execute(text("SELECT * FROM drones WHERE id = :d"), {"d": s["drone_id"]})).mappings().first()
    key = ((s["config_snapshot"] or {}).get("drone") or {}).get("provider_key")
    caps = capabilities_of(key)
    if Capability.PAUSE not in caps or Capability.RESUME not in caps:
        raise VerificationRefused(409, f"The drone's provider ({key or 'none'}) cannot hold and resume.")
    if drone is None or drone["current_latitude"] is None or event.get("drone_latitude") is None:
        raise VerificationRefused(409, "Where the drone is, or where it saw this, is not known.")
    dist = haversine_meters(drone["current_latitude"], drone["current_longitude"],
                            event["drone_latitude"], event["drone_longitude"])
    if dist > VERIFY_MAX_DISTANCE_M:
        raise VerificationRefused(409, f"The drone is {dist:.0f} m from where it saw this (limit "
                                       f"{VERIFY_MAX_DISTANCE_M:.0f} m). Sending it back needs a provider that can "
                                       "be re-tasked in flight, and none installed can.")
    need = fp.RESERVE_PCT + VERIFY_BATTERY_MARGIN_PCT
    if drone["battery_level"] is not None and drone["battery_level"] < need:
        raise VerificationRefused(409, f"The drone has {drone['battery_level']}% battery; holding needs at least "
                                       f"{need}% so it can still come home.")
    stopping = (await db.execute(text(
        "SELECT command FROM drone_session_commands WHERE session_id = :s AND status = 'PENDING' "
        "   AND command IN ('ABORT','RETURN_TO_HOME','CANCEL') LIMIT 1"), {"s": s["id"]})).scalar()
    if stopping:
        raise VerificationRefused(409, f"An operator has already asked it to {stopping.lower().replace('_', ' ')}.")

    why = f"Verify with drone: event {event['id']}" + (f" — {reason}" if reason else "")
    pause = (await db.execute(text("""
        INSERT INTO drone_session_commands (tenant_id, session_id, command, reason, requested_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :s, 'PAUSE', :r, CAST(:u AS uuid))
        ON CONFLICT (session_id, command) WHERE status = 'PENDING' DO NOTHING
        RETURNING id
    """), {"s": s["id"], "r": why, "u": user_id})).scalar()
    if pause is None:
        pause = (await db.execute(text(
            "SELECT id FROM drone_session_commands WHERE session_id = :s AND command = 'PAUSE' AND status = 'PENDING'"),
            {"s": s["id"]})).scalar()
    row = (await db.execute(text("""
        INSERT INTO drone_verification_requests
            (tenant_id, event_id, session_id, requested_by_user_id, reason, hold_seconds, pause_command_id,
             detections_before, risk_before)
        VALUES (current_setting('app.current_tenant')::uuid, :e, :s, CAST(:u AS uuid), :r, :h, :p, :n, :risk)
        RETURNING *
    """), {"e": event["id"], "s": s["id"], "u": user_id, "r": reason, "h": hold_seconds, "p": pause,
           "n": event["detection_count"], "risk": event["risk_level"]})).mappings().first()
    return dict(row)


async def advance_verifications(db: AsyncSession, now: datetime, out: ds.Announcements) -> dict:
    """Move each active verification on: the pause done → holding; the hold
    over → resume and report; the flight ended → cancelled."""
    counts = {"holding": 0, "completed": 0, "failed": 0, "cancelled": 0}
    rows = (await db.execute(text("""
        SELECT v.*, c.status AS pause_status, c.processed_at AS paused_at, c.result AS pause_result,
               s.status AS session_status
          FROM drone_verification_requests v
          JOIN drone_patrol_sessions s ON s.id = v.session_id
          LEFT JOIN drone_session_commands c ON c.id = v.pause_command_id
         WHERE v.status IN ('REQUESTED','HOLDING')
         FOR UPDATE OF v SKIP LOCKED
    """))).mappings().all()
    for v in rows:
        v = dict(v)
        new, result = None, dict(v["result"] or {})
        if v["session_status"] in ds.TERMINAL:
            new, result["reason"] = "CANCELLED", f"The flight ended ({v['session_status'].lower()})."
        elif v["status"] == "REQUESTED":
            if v["pause_status"] == "DONE":
                started = v["paused_at"] or now
                await db.execute(text(
                    "UPDATE drone_verification_requests SET status = 'HOLDING', started_at = :a, ends_at = :b "
                    " WHERE id = :id"), {"a": started, "b": started + timedelta(seconds=v["hold_seconds"]),
                                         "id": v["id"]})
                out.add("drone_verification_updated", {"verification_id": str(v["id"]), "event_id": str(v["event_id"]),
                                                       "status": "HOLDING"})
                counts["holding"] += 1
                continue
            if v["pause_status"] in ("REJECTED", "FAILED") or v["pause_command_id"] is None:
                new, result["reason"] = "FAILED", v["pause_result"] or "The drone could not hold."
        elif v["status"] == "HOLDING" and v["ends_at"] is not None and now >= v["ends_at"]:
            resume = None
            if v["session_status"] == "PAUSED":
                resume = (await db.execute(text("""
                    INSERT INTO drone_session_commands (tenant_id, session_id, command, reason, requested_by_user_id)
                    VALUES (current_setting('app.current_tenant')::uuid, :s, 'RESUME', :r, CAST(:u AS uuid))
                    ON CONFLICT (session_id, command) WHERE status = 'PENDING' DO NOTHING
                    RETURNING id
                """), {"s": v["session_id"], "r": f"Verification of event {v['event_id']} done",
                       "u": _s(v["requested_by_user_id"])})).scalar()
            e = (await db.execute(text("SELECT detection_count, risk_level, verification_state, incident_id "
                                       "  FROM drone_events WHERE id = :e"), {"e": v["event_id"]})).mappings().first()
            added = (e["detection_count"] - (v["detections_before"] or 0)) if e else 0
            result.update(detections_added=added, risk_before=v["risk_before"],
                          risk_after=e["risk_level"] if e else None,
                          verification_state=e["verification_state"] if e else None)
            await db.execute(text("UPDATE drone_verification_requests SET resume_command_id = :r WHERE id = :id"),
                             {"r": resume, "id": v["id"]})
            if e and e["incident_id"]:
                await _note(db, e["incident_id"], _s(v["requested_by_user_id"]),
                            f"Verified with drone: held {v['hold_seconds']} s; {added} new detection(s); "
                            f"risk {v['risk_before']} → {e['risk_level']}.")
            new = "COMPLETED"
        if new:
            await db.execute(text(
                "UPDATE drone_verification_requests SET status = :st, completed_at = :now, "
                "       result = CAST(:res AS jsonb) WHERE id = :id"),
                {"st": new, "now": now, "res": json.dumps(fp.jsonable(result)), "id": v["id"]})
            out.add("drone_verification_updated", {"verification_id": str(v["id"]), "event_id": str(v["event_id"]),
                                                   "status": new, "result": fp.jsonable(result)})
            counts[new.lower()] += 1
    return counts
