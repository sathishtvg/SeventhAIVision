"""From detection to drone security event: the pipeline around drone_ai's rules.

  detection (worker, on the drone's camera) ─┐
                                              ├─► observe() ─► context ─► risk ─► verification ─► event ─► alert
  sighting (site edge gateway)  ─────────────┘

Two ways in, one pipeline. The drone runner reads each flight's new detections
from the AI workers (read_detections); the edge sync hands in what a gateway
saw. Both go through observe(), so a detection is judged the same way whoever
reported it.

THE WORKERS ARE NOT CHANGED, AND THEY STILL ALERT ON THEIR OWN. A drone's camera
is a camera (decision D1), and each worker raises its own alert under the
tenant's alert rules the moment it detects something — before this pipeline
sees it. This pipeline therefore never duplicates a worker's alert: an event
whose worker alert is already as severe as the drone's assessment links that
alert; a drone alert is raised only when context makes the event MORE serious
than the worker judged it. Suppressing worker alerts for drone cameras would
mean changing the workers, and is left to the owner (DRONE_PATROL_AI.md).

EVERYTHING HERE RUNS INSIDE A TRANSACTION THE CALLER OWNS, with the tenant set,
and announces nothing itself: realtime events and alerts are returned in `out`
for the caller to publish after its commit.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import drone_ai as ai
from app.services import drone_flight_plan as fp
from app.services import drone_response as response
from app.services import drone_sessions as ds

logger = logging.getLogger(__name__)

#: How far behind the watermark detections are read again: a worker writes a
#: detection a moment after the frame it saw, so late rows are not skipped.
READ_OVERLAP = timedelta(seconds=15)
#: Detections that arrive after a flight lands still belong to it.
AFTER_LANDING = timedelta(seconds=30)
BATCH = 500
TELEMETRY_TOLERANCE = timedelta(seconds=15)
CONCURRENT_WINDOW = timedelta(minutes=2)
HISTORY_WINDOW = timedelta(days=30)
CLOSED_STATUSES = ("RESOLVED", "FALSE_POSITIVE")
_AIRBORNE_SQL = ", ".join(f"'{s}'" for s in ds.AIRBORNE)


@dataclass
class ObservationIn:
    source: str                     # CENTRAL | EDGE
    module_type: str
    detected_at: datetime
    drone_id: str
    confidence: float | None = None
    label: str | None = None
    watchlist: str | None = None
    attributes: dict = field(default_factory=dict)
    session_id: str | None = None
    detection_id: str | None = None
    client_ref: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    waypoint_sequence: int | None = None
    worker_alert_id: str | None = None
    worker_alert_severity: str | None = None


async def tenant_tz(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


# ── The flight's rules ───────────────────────────────────────────────────────

async def _flight(db: AsyncSession, o: ObservationIn) -> tuple[dict | None, dict, list[dict], dict | None, list[dict]]:
    """(session, drone, zones, profile, rules). A session flies the zones and
    profile frozen when it started; the allowed people and vehicles are read
    live — who may be somewhere is today's policy, not the one at take-off."""
    drone = (await db.execute(text("SELECT id, site_id, name FROM drones WHERE id = :d"),
                              {"d": o.drone_id})).mappings().first()
    if drone is None:
        raise ValueError("unknown drone")
    session = None
    if o.session_id:
        session = (await db.execute(text(
            "SELECT id, site_id, drone_id, mission_id, config_snapshot, status, last_waypoint_sequence, "
            "       mission_name, drone_name FROM drone_patrol_sessions WHERE id = :s"),
            {"s": o.session_id})).mappings().first()
        session = dict(session) if session else None
    site_id = (session or drone)["site_id"]
    live = {str(z["id"]): dict(z) for z in (await db.execute(text(
        "SELECT * FROM drone_security_zones WHERE site_id = :s AND is_active"), {"s": site_id})).mappings().all()}
    snap = (session or {}).get("config_snapshot") or {}
    if session is not None:
        zones = []
        for z in snap.get("zones") or []:
            now_z = live.get(str(z.get("id")), {})
            zones.append({**z, **{k: now_z.get(k) for k in ("allowed_vehicle_plates", "allowed_user_ids",
                                                            "allowed_role_ids") if k not in z or z.get(k) is None}})
        profile, rules = snap.get("profile"), snap.get("rules") or []
    else:
        zones, profile, rules = list(live.values()), None, []
    return session, dict(drone), zones, profile, rules


async def _position(db: AsyncSession, o: ObservationIn) -> tuple[float | None, float | None, float | None, int | None]:
    """Where the drone was when it saw this — the telemetry sample nearest in
    time. The object itself is somewhere in view; its own position is not
    estimated (location_method DRONE_POSITION)."""
    if o.latitude is not None and o.longitude is not None:
        return o.latitude, o.longitude, o.altitude_m, o.waypoint_sequence
    row = (await db.execute(text("""
        SELECT latitude, longitude, altitude_m, waypoint_sequence FROM drone_telemetry
         WHERE drone_id = :d AND recorded_at BETWEEN :a AND :b
         ORDER BY abs(extract(epoch FROM recorded_at - CAST(:t AS timestamptz))) LIMIT 1
    """), {"d": o.drone_id, "a": o.detected_at - TELEMETRY_TOLERANCE, "b": o.detected_at + TELEMETRY_TOLERANCE,
           "t": o.detected_at})).first()
    if row is None:
        return None, None, None, o.waypoint_sequence
    return row[0], row[1], float(row[2]) if row[2] is not None else None, row[3] or o.waypoint_sequence


async def _on_duty(db: AsyncSession, site_id, at: datetime) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT s.guard_user_id AS user_id, u.role_id FROM shifts s JOIN users u ON u.id = s.guard_user_id
         WHERE s.site_id = :site AND s.actual_start IS NOT NULL AND s.actual_start <= :t
           AND (s.actual_end IS NULL OR s.actual_end >= :t)
    """), {"site": site_id, "t": at})).mappings().all()
    return [dict(r) for r in rows]


# ── Observing ────────────────────────────────────────────────────────────────

async def observe(db: AsyncSession, o: ObservationIn, now: datetime, out: ds.Announcements
                  ) -> tuple[str, str | None, str | None]:
    """Take one detection or sighting. Returns (outcome, event_id, why):
    'accepted', 'duplicate' or 'ignored'."""
    if o.detection_id and (await db.execute(text(
            "SELECT 1 FROM drone_observations WHERE detection_id = :d"), {"d": o.detection_id})).first():
        return "duplicate", None, None
    if o.client_ref and (await db.execute(text(
            "SELECT 1 FROM drone_observations WHERE client_ref = :c"), {"c": o.client_ref})).first():
        return "duplicate", None, None

    session, drone, zones, profile, rules = await _flight(db, o)
    tz = await tenant_tz(db)
    local = o.detected_at.astimezone(tz)
    lat, lng, alt, wp = await _position(db, o)
    zone = ai.zone_at(zones, lat, lng, local)
    rule = ai.rule_for(rules, profile, o.module_type)
    sighting = ai.Sighting(module_type=o.module_type, detected_at=o.detected_at, confidence=o.confidence,
                           label=o.label, latitude=lat, longitude=lng, watchlist=o.watchlist,
                           attributes=o.attributes)
    why = ai.ignore_reason(sighting, rule, profile, zone)
    if why:
        return "ignored", None, why

    site_id = (session or drone)["site_id"]
    zone_id = zone.get("id") if zone else None
    # Grouping: same flight (or drone), same module, same zone, same plate or
    # face, seen within the window.
    event = (await db.execute(text("""
        SELECT * FROM drone_events
         WHERE drone_id = :d
           AND (CAST(:s AS uuid) IS NULL AND session_id IS NULL OR session_id = CAST(:s AS uuid))
           AND module_type = :m
           AND security_zone_id IS NOT DISTINCT FROM CAST(:z AS uuid)
           AND (CAST(:lbl AS text) IS NULL OR label IS NOT DISTINCT FROM CAST(:lbl AS text))
           AND last_detected_at >= CAST(:t AS timestamptz) - make_interval(secs => :w)
           AND detected_at <= CAST(:t AS timestamptz) + make_interval(secs => :w)
         ORDER BY last_detected_at DESC LIMIT 1 FOR UPDATE
    """), {"d": o.drone_id, "s": o.session_id, "m": o.module_type, "z": str(zone_id) if zone_id else None,
           "lbl": o.label if o.module_type in ("lpr", "face") else None, "t": o.detected_at,
           "w": ai.GROUPING_WINDOW.total_seconds()})).mappings().first()

    attrs = {k: v for k, v in {**o.attributes, "watchlist": o.watchlist,
                               "worker_alert_id": o.worker_alert_id,
                               "worker_alert_severity": o.worker_alert_severity}.items() if v is not None}
    created = event is None
    if created:
        event = (await db.execute(text("""
            INSERT INTO drone_events
                (tenant_id, site_id, session_id, drone_id, mission_id, security_zone_id, detection_id,
                 module_type, label, waypoint_sequence, detected_at, last_detected_at, drone_latitude,
                 drone_longitude, drone_altitude_m, location_method, zone_type, zone_name, ai_confidence,
                 attributes, source, verification_state)
            VALUES (current_setting('app.current_tenant')::uuid, :site, CAST(:s AS uuid), :d, :mission,
                    CAST(:z AS uuid), CAST(:det AS uuid), :m, :lbl, :wp, :t, :t, :lat, :lng, :alt, 'DRONE_POSITION',
                    :zt, :zn, :conf, CAST(:attrs AS jsonb), :src, 'OBSERVING')
            RETURNING *
        """), {"site": site_id, "s": o.session_id, "d": o.drone_id, "mission": (session or {}).get("mission_id"),
               "z": str(zone_id) if zone_id else None, "det": o.detection_id, "m": o.module_type,
               "lbl": o.label, "wp": wp, "t": o.detected_at, "lat": lat, "lng": lng, "alt": alt,
               "zt": (zone or {}).get("zone_type"), "zn": (zone or {}).get("name"), "conf": o.confidence,
               "attrs": json.dumps(fp.jsonable(attrs)), "src": o.source})).mappings().first()
        if o.session_id:
            await db.execute(text("UPDATE drone_patrol_sessions SET event_count = event_count + 1, "
                                  "updated_at = now() WHERE id = CAST(:s AS uuid)"), {"s": o.session_id})
    else:
        event = (await db.execute(text("""
            UPDATE drone_events SET
                detection_count = detection_count + 1,
                detected_at = LEAST(detected_at, CAST(:t AS timestamptz)),
                last_detected_at = GREATEST(last_detected_at, CAST(:t AS timestamptz)),
                observed_seconds = EXTRACT(EPOCH FROM (GREATEST(last_detected_at, CAST(:t AS timestamptz))
                                                       - LEAST(detected_at, CAST(:t AS timestamptz)))),
                ai_confidence = GREATEST(ai_confidence, CAST(:conf AS numeric)),
                attributes = attributes || CAST(:attrs AS jsonb),
                updated_at = now()
             WHERE id = :id RETURNING *
        """), {"t": o.detected_at, "conf": o.confidence, "attrs": json.dumps(fp.jsonable(attrs)),
               "id": event["id"]})).mappings().first()
    event = dict(event)

    await db.execute(text("""
        INSERT INTO drone_observations
            (tenant_id, event_id, session_id, drone_id, source, detection_id, client_ref, module_type, label,
             ai_confidence, detected_at, drone_latitude, drone_longitude, drone_altitude_m, attributes)
        VALUES (current_setting('app.current_tenant')::uuid, :e, CAST(:s AS uuid), :d, :src, CAST(:det AS uuid),
                CAST(:ref AS uuid), :m, :lbl, :conf, :t, :lat, :lng, :alt, CAST(:attrs AS jsonb))
    """), {"e": event["id"], "s": o.session_id, "d": o.drone_id, "src": o.source, "det": o.detection_id,
           "ref": o.client_ref, "m": o.module_type, "lbl": o.label, "conf": o.confidence, "t": o.detected_at,
           "lat": lat, "lng": lng, "alt": alt, "attrs": json.dumps(fp.jsonable(attrs))})

    await evaluate(db, event, sighting, rule, profile, zone, site_id, local, now, out, created=created)
    return "accepted", str(event["id"]), None


async def evaluate(db: AsyncSession, event: dict, sighting: ai.Sighting, rule: dict, profile: dict | None,
                   zone: dict | None, site_id, local: datetime, now: datetime, out: ds.Announcements,
                   *, created: bool) -> None:
    """Score the event as it now stands, verify it, and alert if it has earned it."""
    count, observed = int(event["detection_count"]), float(event["observed_seconds"] or 0)
    max_conf = float(event["ai_confidence"]) if event["ai_confidence"] is not None else None
    cctv = [r[0] for r in (await db.execute(text(
        "SELECT camera_name FROM drone_event_cameras WHERE event_id = :e AND corroborates ORDER BY rank"),
        {"e": event["id"]})).all()]
    verified = event["verification_state"] == "VERIFIED" or ai.is_verified(
        event["module_type"], count, observed, max_conf, profile, corroborated=bool(cctv))
    concurrent = set((await db.execute(text("""
        SELECT DISTINCT module_type FROM drone_events
         WHERE drone_id = :d AND id <> :id AND last_detected_at >= :since
           AND status NOT IN ('FALSE_POSITIVE')
    """), {"d": event["drone_id"], "id": event["id"], "since": event["last_detected_at"] - CONCURRENT_WINDOW}
    )).scalars().all())
    history = (await db.execute(text("""
        SELECT count(*) FROM drone_events
         WHERE site_id = :site AND id <> :id AND detected_at >= :since AND status <> 'FALSE_POSITIVE'
           AND security_zone_id IS NOT DISTINCT FROM CAST(:z AS uuid)
           AND (session_id IS DISTINCT FROM CAST(:s AS uuid) OR session_id IS NULL)
    """), {"site": site_id, "id": event["id"], "since": event["detected_at"] - HISTORY_WINDOW,
           "z": str(event["security_zone_id"]) if event["security_zone_id"] else None,
           "s": str(event["session_id"]) if event["session_id"] else None})).scalar()
    incidents = (await db.execute(text("""
        SELECT count(*) FROM incidents i JOIN alerts a ON a.id = i.alert_id
         WHERE a.site_id = :site AND i.created_at >= :since
    """), {"site": site_id, "since": event["detected_at"] - HISTORY_WINDOW})).scalar()
    situation = ai.Situation(
        local_time=local, zone=zone, on_duty=await _on_duty(db, site_id, event["last_detected_at"]),
        detection_count=count, observed_seconds=observed, max_confidence=max_conf, verified=verified,
        concurrent_modules=concurrent, history_events=int(history or 0), history_incidents=int(incidents or 0),
        cctv=cctv)
    risk = ai.assess(sighting, rule, situation)

    state = "VERIFIED" if verified else "OBSERVING"
    before = (event["risk_level"], event["verification_state"])
    await db.execute(text("""
        UPDATE drone_events SET risk_score = :score, risk_level = :level, risk_factors = CAST(:factors AS jsonb),
               verification_state = CASE WHEN verification_state = 'DISMISSED' THEN verification_state
                                         ELSE CAST(:state AS varchar) END,
               verified_at = CASE WHEN CAST(:state AS varchar) = 'VERIFIED'
                                  THEN COALESCE(verified_at, CAST(:now AS timestamptz)) ELSE verified_at END,
               risk_evaluated_at = CAST(:now AS timestamptz),
               attributes = attributes || CAST(:auth AS jsonb), updated_at = now()
         WHERE id = :id
    """), {"score": risk.score, "level": risk.level, "factors": json.dumps(risk.factors), "state": state,
           "now": now, "auth": json.dumps({"authorisation": risk.authorisation}), "id": event["id"]})
    event.update(risk_score=risk.score, risk_level=risk.level, verification_state=state)

    if created:
        out.add("drone_event_created", {"event_id": str(event["id"]), "session_id": _s(event["session_id"]),
                                        "drone_id": _s(event["drone_id"]), "module_type": event["module_type"],
                                        "risk_level": risk.level, "verification_state": state,
                                        "detected_at": event["detected_at"].isoformat(), "source": event["source"]})
    elif before != (risk.level, state):
        out.add("drone_event_updated", {"event_id": str(event["id"]), "risk_level": risk.level,
                                        "previous_risk_level": before[0], "verification_state": state})

    if event["status"] not in CLOSED_STATUSES:
        await _alert(db, event, ai.alert_severity(risk.level, verified, zone), risk, zone, out)
        await response.on_assessed(db, event, risk.level, verified, zone, rule, risk.factors, now, out)


def _s(v) -> str | None:
    return str(v) if v is not None else None


async def reassess(db: AsyncSession, event_id, now: datetime, out: ds.Announcements) -> None:
    """Score an event again as it stands — after CCTV correlation found (or lost)
    a fixed camera that agrees with it. Same rules, rebuilt from the event and
    the flight it belongs to."""
    e = (await db.execute(text("SELECT * FROM drone_events WHERE id = :id FOR UPDATE"),
                          {"id": event_id})).mappings().first()
    if e is None:
        return
    e = dict(e)
    probe = ObservationIn(source=e["source"], module_type=e["module_type"], detected_at=e["detected_at"],
                          drone_id=str(e["drone_id"]), session_id=_s(e["session_id"]))
    session, drone, zones, profile, rules = await _flight(db, probe)
    zone = next((z for z in zones if str(z.get("id")) == str(e["security_zone_id"])), None)
    rule = ai.rule_for(rules, profile, e["module_type"]) or ai.rule_for(None, None, e["module_type"])
    attrs = e["attributes"] or {}
    sighting = ai.Sighting(module_type=e["module_type"], detected_at=e["detected_at"],
                           confidence=float(e["ai_confidence"]) if e["ai_confidence"] is not None else None,
                           label=e["label"], latitude=e["drone_latitude"], longitude=e["drone_longitude"],
                           watchlist=attrs.get("watchlist"), attributes=attrs)
    tz = await tenant_tz(db)
    await evaluate(db, e, sighting, rule, profile, zone, (session or drone)["site_id"],
                   e["last_detected_at"].astimezone(tz), now, out, created=False)


async def _alert(db: AsyncSession, event: dict, severity: str | None, risk: ai.Risk, zone: dict | None,
                 out: ds.Announcements, *, title_prefix: str = "") -> None:
    """Carry an alert as serious as the event — and no second one. A worker
    alert that already is that serious is linked, not repeated; ours is raised
    only when the drone's context makes the event more serious than that."""
    if severity is None:
        return
    rank = ai.SEVERITY_RANK
    if event.get("alert_id"):
        current = (await db.execute(text("SELECT severity FROM alerts WHERE id = :a"),
                                    {"a": event["alert_id"]})).scalar()
        if current and rank.get(current, 0) >= rank[severity]:
            return
    worker = (await db.execute(text("""
        SELECT a.id, a.severity FROM drone_observations o JOIN alerts a ON a.id = CAST(o.attributes->>'worker_alert_id' AS uuid)
         WHERE o.event_id = :e ORDER BY a.created_at
    """), {"e": event["id"]})).mappings().all()
    best = max(worker, key=lambda w: rank.get(w["severity"], 0), default=None)
    if best is not None and rank.get(best["severity"], 0) >= rank[severity]:
        await db.execute(text("UPDATE drone_events SET alert_id = :a WHERE id = :e"), {"a": best["id"], "e": event["id"]})
        event["alert_id"] = best["id"]
        return

    code = f"drone.{event['module_type']}"
    what = event["module_type"].replace("_", " ") + (f" {event['label']}" if event.get("label") else "")
    where = f" in {zone.get('name')}" if zone else ""
    title = f"{title_prefix}Drone: {what}{where} — {risk.level.lower()} risk"
    reasons = "; ".join(f["detail"] for f in risk.factors if f["points"] > 0)
    params = {"event_id": str(event["id"]), "session_id": _s(event["session_id"]), "drone_id": _s(event["drone_id"]),
              "risk_level": risk.level, "risk_score": risk.score, "severity": severity,
              "zone_id": _s(event.get("security_zone_id"))}
    row = (await db.execute(text("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity, alert_code,
                            message_params, title, message, status)
        SELECT current_setting('app.current_tenant')::uuid, NULL, CAST(:site AS uuid), CAST(:module AS varchar),
               CAST(:sev AS varchar), CAST(:code AS varchar), CAST(:params AS jsonb), CAST(:title AS text),
               CAST(:msg AS text), 'open'
         WHERE NOT EXISTS (SELECT 1 FROM alerts a WHERE a.alert_code = CAST(:code AS varchar)
                              AND a.message_params->>'event_id' = CAST(:eid AS text)
                              AND a.severity = CAST(:sev AS varchar))
        RETURNING id
    """), {"site": str(event["site_id"]), "module": ds.MODULE, "sev": severity, "code": code,
           "params": json.dumps(params), "title": title[:255],
           "msg": f"Risk {risk.score}/100 ({risk.level}). {reasons}.", "eid": str(event["id"])})).first()
    if row is None:
        return
    await db.execute(text("UPDATE drone_events SET alert_id = :a WHERE id = :e"), {"a": row[0], "e": event["id"]})
    event["alert_id"] = row[0]
    # The command-centre card travels with the alert: site, area, drone,
    # mission, site time, AI confidence and risk — kept apart.
    card = await response.event_card(db, event["id"]) or {}
    out.add("alert_created", {"alert_id": str(row[0]), "alert_code": code, "severity": severity,
                              "module_type": ds.MODULE, "site_id": _s(event["site_id"]), "title": title[:255],
                              "drone_event_id": str(event["id"]),
                              "drone": fp.jsonable({k: card.get(k) for k in (
                                  "headline", "site_name", "area", "drone_code", "drone_name", "mission_name",
                                  "session_number", "detected_at_site_time", "ai_confidence", "risk_level",
                                  "risk_score", "verification_state", "latitude", "longitude")})})


# ── Closing sightings that were never confirmed ──────────────────────────────

async def close_unconfirmed(db: AsyncSession, now: datetime, out: ds.Announcements) -> int:
    """An event still OBSERVING after the grouping window has gone quiet was
    never confirmed. It becomes UNVERIFIED and keeps its (lowered) risk. One
    that would have been HIGH or CRITICAL is not simply dropped: it raises a
    LOW alert asking a person to look at it."""
    rows = (await db.execute(text("""
        UPDATE drone_events SET verification_state = 'UNVERIFIED', updated_at = now()
         WHERE verification_state = 'OBSERVING' AND last_detected_at < :cut
        RETURNING *
    """), {"cut": now - ai.GROUPING_WINDOW})).mappings().all()
    for e in rows:
        e = dict(e)
        out.add("drone_event_updated", {"event_id": str(e["id"]), "risk_level": e["risk_level"],
                                        "verification_state": "UNVERIFIED"})
        if e["risk_level"] in ("HIGH", "CRITICAL") and e["status"] not in CLOSED_STATUSES and not e["alert_id"]:
            zone = None
            if e["security_zone_id"]:
                zone = dict((await db.execute(text("SELECT name, alert_policy FROM drone_security_zones WHERE id = :z"),
                                              {"z": e["security_zone_id"]})).mappings().first() or {}) or None
            if zone and zone.get("alert_policy") == "NONE":
                continue
            risk = ai.Risk(score=e["risk_score"] or 0, level=e["risk_level"], factors=e["risk_factors"] or [],
                           authorisation=(e["attributes"] or {}).get("authorisation", "UNKNOWN"))
            await _alert(db, e, "low", risk, zone, out, title_prefix="Unconfirmed — ")
    return len(rows)


# ── Reading a flight's detections ────────────────────────────────────────────

_DETECTIONS = """
    SELECT d.id, d.module_type, d.confidence, d.raw_metadata, d.detected_at,
           l.plate_number, l.watchlist_match AS lpr_match, l.vehicle_type, l.vehicle_color,
           f.watchlist_match AS face_match, f.matched_watchlist_id, f.match_confidence,
           w.weapon_type, fs.detection_type AS fire_type, b.behavior_type, p.items_missing,
           a.id AS worker_alert_id, a.severity AS worker_alert_severity
      FROM detections d
      LEFT JOIN lpr_events l        ON l.detection_id = d.id  AND l.detected_at = d.detected_at
      LEFT JOIN face_events f       ON f.detection_id = d.id  AND f.detected_at = d.detected_at
      LEFT JOIN weapon_events w     ON w.detection_id = d.id  AND w.detected_at = d.detected_at
      LEFT JOIN fire_smoke_events fs ON fs.detection_id = d.id AND fs.detected_at = d.detected_at
      LEFT JOIN behavior_events b   ON b.detection_id = d.id  AND b.detected_at = d.detected_at
      LEFT JOIN ppe_events p        ON p.detection_id = d.id  AND p.detected_at = d.detected_at
      LEFT JOIN LATERAL (SELECT id, severity FROM alerts WHERE detection_id = d.id
                          ORDER BY created_at LIMIT 1) a ON TRUE
     WHERE d.camera_id = :cam AND d.detected_at > :since AND d.detected_at <= :until
     ORDER BY d.detected_at
     LIMIT :n
"""


def _from_detection(r: dict, session: dict) -> ObservationIn:
    m = r["module_type"]
    meta = r["raw_metadata"] or {}
    label, watchlist, attrs = None, None, {}
    if m == "lpr":
        label, watchlist = r["plate_number"], r["lpr_match"]
        attrs = {"vehicle_type": r["vehicle_type"], "vehicle_color": r["vehicle_color"]}
    elif m == "face":
        watchlist = r["face_match"]
        label = str(r["matched_watchlist_id"]) if r["matched_watchlist_id"] else None
        attrs = {"match_confidence": float(r["match_confidence"]) if r["match_confidence"] is not None else None}
    elif m == "weapon":
        label = r["weapon_type"] or meta.get("weapon_type")
    elif m == "fire_smoke":
        label = r["fire_type"]
    elif m == "behavior":
        label = r["behavior_type"]
    elif m == "ppe":
        attrs = {"items_missing": r["items_missing"]}
    return ObservationIn(
        source="CENTRAL", module_type=m, detected_at=r["detected_at"], drone_id=str(session["drone_id"]),
        confidence=float(r["confidence"]) if r["confidence"] is not None else None, label=label,
        watchlist=watchlist, attributes={k: v for k, v in attrs.items() if v is not None},
        session_id=str(session["id"]), detection_id=str(r["id"]),
        worker_alert_id=str(r["worker_alert_id"]) if r["worker_alert_id"] else None,
        worker_alert_severity=r["worker_alert_severity"])


async def read_detections(db: AsyncSession, session: dict, camera_id, now: datetime,
                          out: ds.Announcements) -> dict:
    """Take a flight's new detections from its drone's camera, oldest first,
    each in its own savepoint. Moves the flight's watermark forward."""
    counts = {"accepted": 0, "duplicate": 0, "ignored": 0, "failed": 0}
    start = session.get("launched_at") or session.get("edge_claimed_at") or session["created_at"]
    since = (session.get("ai_watermark_at") or start) - READ_OVERLAP
    until = min(now, (session["ended_at"] + AFTER_LANDING)) if session.get("ended_at") else now
    rows = [dict(r) for r in (await db.execute(text(_DETECTIONS), {
        "cam": camera_id, "since": max(since, start - READ_OVERLAP), "until": until, "n": BATCH})).mappings().all()]
    for r in rows:
        try:
            async with db.begin_nested():
                mine = ds.Announcements()
                outcome, _, _ = await observe(db, _from_detection(r, session), now, mine)
                out.extend(mine)
            counts[outcome] += 1
        except (IntegrityError, DataError, ValueError) as exc:
            counts["failed"] += 1
            logger.warning("drone ai: detection %s not observed: %s", r["id"], exc)
    newest = rows[-1]["detected_at"] if rows else None
    if len(rows) < BATCH and session.get("ended_at"):
        newest = max(newest or until, until)      # nothing more will come for this flight
    if newest is not None:
        await db.execute(text(
            "UPDATE drone_patrol_sessions SET ai_watermark_at = GREATEST(COALESCE(ai_watermark_at, :w), :w) "
            " WHERE id = :s"), {"w": newest, "s": session["id"]})
    return counts


async def sessions_to_read(db: AsyncSession, now: datetime) -> list[dict]:
    """Flights whose drone has a camera and that are in the air, or landed so
    recently that late detections may still arrive."""
    rows = (await db.execute(text(f"""
        SELECT s.*, d.camera_id FROM drone_patrol_sessions s JOIN drones d ON d.id = s.drone_id
         WHERE d.camera_id IS NOT NULL
           AND (s.status IN ({_AIRBORNE_SQL})
                OR (s.ended_at IS NOT NULL AND s.launched_at IS NOT NULL
                    AND (s.ai_watermark_at IS NULL OR s.ai_watermark_at < s.ended_at + make_interval(secs => :after))
                    AND s.ended_at > CAST(:now AS timestamptz) - interval '1 day'))
         ORDER BY s.created_at
    """), {"after": AFTER_LANDING.total_seconds(), "now": now})).mappings().all()
    return [dict(r) for r in rows]
