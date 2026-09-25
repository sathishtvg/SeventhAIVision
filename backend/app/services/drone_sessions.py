"""Patrol sessions: creating them, deciding whether they may fly, and recording
what happened while they did.

Shared by the API (a manual "run now") and the drone runner (scheduled launches
and every flight tick), so a mission is judged by one set of rules however it was
started.

EVERYTHING HERE RUNS INSIDE ONE TRANSACTION THE CALLER OWNS. Nothing commits
and nothing reads after a commit — the tenant setting is transaction-local, and a
query after commit() runs with no tenant. Realtime events and alerts that should
be announced are RETURNED, not published: the caller publishes them after its
commit succeeds, so no one is told about a row that was then rolled back.

ALERTS GO INTO THE EXISTING alerts TABLE, as module_type 'drone_patrol', and are
announced as ordinary alert_created events — so the existing notification
rules, push, webhooks and every alert feed pick them up without a change.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.drone_module import entitlement_problem, load_entitlement
from app.services import drone_flight_plan as fp
from app.services import drone_geometry as geo
from app.services.drone_preflight import PreflightFacts, PreflightResult, evaluate
from app.services.drone_providers import FlightUpdate, capabilities_of

MODULE = "drone_patrol"
IN_FLIGHT = ("PRECHECK", "READY", "LAUNCHING", "ACTIVE", "PAUSED", "EVENT_DETECTED", "RETURNING")
_IN_FLIGHT_SQL = ", ".join(f"'{s}'" for s in IN_FLIGHT)
AIRBORNE = ("LAUNCHING", "ACTIVE", "PAUSED", "EVENT_DETECTED", "RETURNING")
TERMINAL = ("COMPLETED", "FAILED", "ABORTED", "CANCELLED", "BLOCKED", "MISSED")
PHASE_TO_STATUS = {"LAUNCHING": "LAUNCHING", "ACTIVE": "ACTIVE", "PAUSED": "PAUSED",
                   "RETURNING": "RETURNING"}


@dataclass
class Announcements:
    """What to publish once the caller's transaction has committed."""
    events: list[tuple[str, dict]] = field(default_factory=list)   # (event_type, payload)

    def add(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))

    def extend(self, other: "Announcements") -> None:
        self.events.extend(other.events)


@dataclass
class MissionBundle:
    mission: dict
    site: dict | None
    drone: dict | None
    provider: dict | None
    gateway: dict | None
    route: dict | None
    waypoints: list[dict]
    profile: dict | None
    rules: list[dict]
    zones: list[dict]


# ── Loading ──────────────────────────────────────────────────────────────────

async def load_bundle(db: AsyncSession, mission_id) -> MissionBundle | None:
    """Everything a flight of this mission would use, as it stands right now."""
    one = lambda sql, p: db.execute(text(sql), p)  # noqa: E731
    m = (await one("SELECT * FROM drone_missions WHERE id = CAST(:id AS uuid)",
                   {"id": str(mission_id)})).mappings().first()
    if m is None:
        return None
    m = dict(m)
    site = (await one("SELECT id, name, is_active, latitude, longitude, geofence_radius_meters, "
                      "geofence_polygon FROM sites WHERE id = :id", {"id": m["site_id"]})).mappings().first()
    drone = provider = gateway = route = profile = None
    if m["drone_id"]:
        drone = (await one("SELECT * FROM drones WHERE id = :id", {"id": m["drone_id"]})).mappings().first()
    if drone and drone["provider_config_id"]:
        provider = (await one("SELECT id, name, provider_key, config, secret_encrypted, is_active "
                              "FROM drone_provider_configs WHERE id = :id",
                              {"id": drone["provider_config_id"]})).mappings().first()
    if drone and drone["edge_gateway_id"]:
        gateway = (await one("SELECT id, name, status, is_active, last_seen_at FROM drone_edge_gateways "
                             "WHERE id = :id", {"id": drone["edge_gateway_id"]})).mappings().first()
    waypoints: list[dict] = []
    if m["route_id"]:
        route = (await one("SELECT * FROM drone_routes WHERE id = :id", {"id": m["route_id"]})).mappings().first()
        waypoints = [dict(w) for w in (await one(
            "SELECT * FROM drone_waypoints WHERE route_id = :id ORDER BY sequence",
            {"id": m["route_id"]})).mappings().all()]
    rules: list[dict] = []
    if m["security_profile_id"]:
        profile = (await one("SELECT * FROM drone_security_profiles WHERE id = :id",
                             {"id": m["security_profile_id"]})).mappings().first()
        rules = [dict(r) for r in (await one(
            "SELECT * FROM drone_profile_rules WHERE profile_id = :id ORDER BY module_type",
            {"id": m["security_profile_id"]})).mappings().all()]
    zones = [dict(z) for z in (await one(
        "SELECT * FROM drone_security_zones WHERE site_id = :id AND is_active ORDER BY name",
        {"id": m["site_id"]})).mappings().all()]
    as_dict = lambda r: dict(r) if r is not None else None  # noqa: E731
    return MissionBundle(m, as_dict(site), as_dict(drone), as_dict(provider), as_dict(gateway),
                         as_dict(route), waypoints, as_dict(profile), rules, zones)


def plan_of(b: MissionBundle) -> tuple[fp.MissionPlan | None, fp.Estimate | None]:
    if not b.route or not b.waypoints:
        return None, None
    plan = fp.build_plan(b.route, b.waypoints)
    return plan, fp.estimate(plan)


async def preflight(db: AsyncSession, b: MissionBundle, now: datetime,
                    exclude_session_id=None) -> PreflightResult:
    ent = await load_entitlement(db)
    in_flight = False
    if b.drone:
        in_flight = (await db.execute(text(f"""
            SELECT EXISTS (SELECT 1 FROM drone_patrol_sessions
                            WHERE drone_id = :d AND status IN ({_IN_FLIGHT_SQL})
                              AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid)))
        """), {"d": b.drone["id"], "x": str(exclude_session_id) if exclude_session_id else None})).scalar()
    _, est = plan_of(b)
    outside = geo.waypoints_outside_site(b.site or {}, b.waypoints) if b.waypoints else []
    return evaluate(PreflightFacts(
        now=now, licence_problem=entitlement_problem(ent, now), mission=b.mission, site=b.site,
        drone=b.drone, provider=b.provider,
        provider_capabilities=frozenset(c.value for c in capabilities_of((b.provider or {}).get("provider_key"))),
        gateway=b.gateway, route=b.route, waypoint_count=len(b.waypoints), outside_site=outside,
        profile=b.profile, drone_in_flight=bool(in_flight), estimate=est,
    ))


# ── Creating a session ───────────────────────────────────────────────────────

async def create_session(db: AsyncSession, b: MissionBundle, *, now: datetime, trigger: str,
                         status: str, user_id: str | None = None, schedule_id=None,
                         scheduled_for: datetime | None = None, result: PreflightResult | None = None,
                         ) -> uuid.UUID | None:
    """Insert the session with its configuration frozen, and its waypoints copied.

    Returns None when a session for this (schedule, scheduled_for) already exists
    — the unique constraint, not a prior check, is what decides.
    """
    sid = uuid.uuid4()
    _, est = plan_of(b)
    snapshot = fp.snapshot_config(
        mission=b.mission, route=b.route or {}, waypoints=b.waypoints,
        drone={**b.drone, "provider_key": (b.provider or {}).get("provider_key")} if b.drone else None,
        profile=b.profile, rules=b.rules, zones=b.zones,
        est=est or fp.Estimate(0.0, 0.0, 0.0))
    ended = status in TERMINAL
    row = (await db.execute(text("""
        INSERT INTO drone_patrol_sessions
            (id, tenant_id, session_number, mission_id, schedule_id, site_id, drone_id, route_id,
             security_profile_id, edge_gateway_id, mission_name, drone_name, route_name, profile_name,
             config_snapshot, triggered_by, triggered_by_user_id, scheduled_for, status,
             preflight_result, blocked_reason, failure_reason, started_at, ended_at)
        VALUES (:id, current_setting('app.current_tenant')::uuid, :num, :m, CAST(:sch AS uuid), :site,
                :drone, :route, :profile, :gw, :mname, :dname, :rname, :pname,
                CAST(:snap AS jsonb), :trig, CAST(:uid AS uuid), :sfor, :status,
                CAST(:pre AS jsonb), :blocked, :failure, :started, :ended)
        ON CONFLICT (schedule_id, scheduled_for) DO NOTHING
        RETURNING id
    """), {
        "id": sid, "num": f"DP-{(scheduled_for or now):%Y%m%d}-{sid.hex[:8].upper()}",
        "m": b.mission["id"], "sch": str(schedule_id) if schedule_id else None,
        "site": b.mission["site_id"], "drone": (b.drone or {}).get("id"), "route": (b.route or {}).get("id"),
        "profile": (b.profile or {}).get("id"), "gw": (b.gateway or {}).get("id"),
        "mname": b.mission["name"], "dname": (b.drone or {}).get("name"),
        "rname": (b.route or {}).get("name"), "pname": (b.profile or {}).get("name"),
        "snap": json.dumps(snapshot), "trig": trigger, "uid": user_id, "sfor": scheduled_for,
        "status": status, "pre": json.dumps(result.as_json()) if result else None,
        "blocked": result.reason() if (result and status == "BLOCKED") else None,
        "failure": "Not started within its grace period." if status == "MISSED" else None,
        "started": now, "ended": now if ended else None,
    })).first()
    if row is None:
        return None
    for w in b.waypoints:
        await db.execute(text("""
            INSERT INTO drone_session_waypoints
                (tenant_id, session_id, sequence, name, latitude, longitude, altitude_m,
                 hover_seconds, observe_seconds, snapshot_required, security_zone_id)
            VALUES (current_setting('app.current_tenant')::uuid, :s, :seq, :n, :lat, :lng, :alt,
                    :hov, :obs, :snap, :zone)
        """), {"s": sid, "seq": w["sequence"], "n": w.get("name"), "lat": w["latitude"],
               "lng": w["longitude"], "alt": w.get("altitude_m"), "hov": w.get("hover_seconds") or 0,
               "obs": w.get("observe_seconds") or 0, "snap": bool(w.get("snapshot_required")),
               "zone": w.get("security_zone_id")})
    return sid


# ── Alerts ───────────────────────────────────────────────────────────────────

async def raise_alert(db: AsyncSession, out: Announcements, *, code: str, severity: str, title: str,
                      message: str, site_id=None, session_id=None, drone_id=None, gateway_id=None,
                      params: dict | None = None) -> str | None:
    """One alert per (code, session) — or per (code, drone), or (code, gateway),
    while one is open outside a flight — however many ticks notice the same thing."""
    if session_id:
        key_col, key_val = "session_id", str(session_id)
    elif drone_id:
        key_col, key_val = "drone_id", str(drone_id)
    else:
        key_col, key_val = "gateway_id", str(gateway_id)
    body = {"session_id": str(session_id) if session_id else None,
            "drone_id": str(drone_id) if drone_id else None, **(params or {})}
    if gateway_id:
        body["gateway_id"] = str(gateway_id)
    row = (await db.execute(text("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity, alert_code,
                            message_params, title, message, status)
        SELECT current_setting('app.current_tenant')::uuid, NULL, CAST(:site AS uuid), :module, :sev,
               CAST(:code AS varchar), CAST(:params AS jsonb), :title, :msg, 'open'
         WHERE NOT EXISTS (
             SELECT 1 FROM alerts a
              WHERE a.tenant_id = current_setting('app.current_tenant')::uuid
                AND a.alert_code = CAST(:code AS varchar) AND a.message_params->>CAST(:kcol AS text) = CAST(:kval AS text)
                AND (CAST(:kcol AS text) = 'session_id' OR a.status = 'open')
         )
        RETURNING id
    """), {"site": str(site_id) if site_id else None, "module": MODULE, "sev": severity, "code": code,
           "params": json.dumps(fp.jsonable(body)), "title": title, "msg": message,
           "kcol": key_col, "kval": key_val})).first()
    if row is None:
        return None
    out.add("alert_created", {"alert_id": str(row[0]), "alert_code": code, "severity": severity,
                              "module_type": MODULE, "site_id": str(site_id) if site_id else None,
                              "title": title})
    return str(row[0])


# ── Recording a flight ───────────────────────────────────────────────────────

async def record_update(db: AsyncSession, session: dict, up: FlightUpdate, now: datetime,
                        out: Announcements, *, launched: bool = False) -> str:
    """Persist one provider update: samples, waypoint progress, the session's and
    the drone's state, and — once it has landed — the outcome. Returns the
    session's new status."""
    sid, drone_id, tid = session["id"], session["drone_id"], session["tenant_id"]
    if up.samples:
        await db.execute(text("""
            INSERT INTO drone_telemetry
                (tenant_id, drone_id, session_id, recorded_at, latitude, longitude, altitude_m,
                 heading_deg, speed_mps, battery_pct, gps_fix, signal_quality, mission_state,
                 waypoint_sequence)
            VALUES (current_setting('app.current_tenant')::uuid, :d, :s, :at, :lat, :lng, :alt,
                    :hdg, :spd, :bat, :gps, :sig, :state, :wp)
            ON CONFLICT (drone_id, recorded_at) DO NOTHING
        """), [{"d": drone_id, "s": sid, "at": x.recorded_at, "lat": x.latitude, "lng": x.longitude,
                "alt": x.altitude_m, "hdg": x.heading_deg, "spd": x.speed_mps, "bat": round(x.battery_pct),
                "gps": x.gps_fix, "sig": x.signal_quality, "state": x.mission_state,
                "wp": x.waypoint_sequence} for x in up.samples])

    comms_lost = comms_back = False
    for e in up.events:
        if e.kind == "WAYPOINT_REACHED":
            await db.execute(text(
                "UPDATE drone_session_waypoints SET status = 'REACHED', reached_at = :at, updated_at = now() "
                " WHERE session_id = :s AND sequence = :q AND status = 'PENDING'"),
                {"at": e.at, "s": sid, "q": e.waypoint})
        elif e.kind == "WAYPOINT_DEPARTED":
            await db.execute(text(
                "UPDATE drone_session_waypoints SET status = 'OBSERVED', departed_at = :at, "
                "       reached_at = COALESCE(reached_at, :at), updated_at = now() "
                " WHERE session_id = :s AND sequence = :q AND status IN ('PENDING','REACHED')"),
                {"at": e.at, "s": sid, "q": e.waypoint})
        elif e.kind == "COMMS_LOST":
            comms_lost = True
        elif e.kind == "COMMS_RESTORED":
            comms_back = True

    status = session["status"]
    if up.outcome:
        status = up.outcome
    elif up.phase in PHASE_TO_STATUS:
        status = PHASE_TO_STATUS[up.phase]

    last = up.samples[-1] if up.samples else None
    await db.execute(text("""
        UPDATE drone_patrol_sessions SET
            status = :status, provider_state = CAST(:ps AS jsonb), last_tick_at = CAST(:now AS timestamptz),
            provider_mission_ref = COALESCE(CAST(:ref AS text), provider_mission_ref),
            launched_at = CASE WHEN CAST(:launched AS boolean) THEN CAST(:now AS timestamptz) ELSE launched_at END,
            last_waypoint_sequence = COALESCE(
                (SELECT max(sequence) FROM drone_session_waypoints
                  WHERE session_id = :s AND status IN ('REACHED','OBSERVED')), last_waypoint_sequence),
            comms_lost_at = CASE WHEN CAST(:lost AS boolean) THEN COALESCE(comms_lost_at, CAST(:now AS timestamptz)) ELSE comms_lost_at END,
            failure_code = COALESCE(CAST(:fcode AS text), failure_code),
            failure_reason = CASE WHEN CAST(:outcome AS text) IN ('FAILED') OR CAST(:fcode AS text) IS NOT NULL
                                  THEN COALESCE(CAST(:freason AS text), failure_reason) ELSE failure_reason END,
            ended_at  = CASE WHEN CAST(:outcome AS text) IS NOT NULL THEN CAST(:now AS timestamptz) ELSE ended_at END,
            landed_at = CASE WHEN CAST(:outcome AS text) IS NOT NULL THEN COALESCE(CAST(:last_at AS timestamptz), CAST(:now AS timestamptz)) ELSE landed_at END,
            updated_at = now()
        WHERE id = :s
    """), {"status": status, "ps": json.dumps(fp.jsonable(up.provider_state)), "now": now,
           "ref": up.provider_mission_ref, "launched": launched, "s": sid, "lost": comms_lost,
           "fcode": up.failure_code, "freason": up.failure_reason, "outcome": up.outcome,
           "last_at": last.recorded_at if last else None})

    # The drone mirrors its flight. Receiving telemetry is hearing from it.
    if up.outcome:
        drone_status = "READY" if (last and last.battery_pct >= 95) else "CHARGING"
    elif comms_lost:
        drone_status = "COMMUNICATION_LOST"
    elif status == "RETURNING":
        drone_status = "RETURNING"
    else:
        drone_status = "MISSION_ACTIVE"
    await db.execute(text("""
        UPDATE drones SET
            status = :st,
            current_latitude  = COALESCE(:lat, current_latitude),
            current_longitude = COALESCE(:lng, current_longitude),
            current_altitude_m = COALESCE(:alt, current_altitude_m),
            current_heading_deg = COALESCE(:hdg, current_heading_deg),
            current_speed_mps = COALESCE(:spd, current_speed_mps),
            battery_level = COALESCE(:bat, battery_level),
            last_heartbeat_at = CASE WHEN CAST(:heard AS boolean) THEN CAST(:now AS timestamptz) ELSE last_heartbeat_at END,
            communication_status = CASE WHEN CAST(:lost AS boolean) THEN 'FAULT' WHEN CAST(:heard AS boolean) THEN 'OK' ELSE communication_status END,
            comms_alerted_at = CASE WHEN CAST(:lost AS boolean) THEN COALESCE(comms_alerted_at, CAST(:now AS timestamptz))
                                    WHEN CAST(:heard AS boolean) THEN NULL ELSE comms_alerted_at END,
            last_flight_at = CASE WHEN CAST(:outcome AS text) IS NOT NULL THEN CAST(:now AS timestamptz) ELSE last_flight_at END,
            total_flight_seconds = total_flight_seconds + CASE WHEN CAST(:outcome AS text) IS NOT NULL THEN
                GREATEST(0, EXTRACT(EPOCH FROM (COALESCE(CAST(:last_at AS timestamptz), CAST(:now AS timestamptz)) -
                    COALESCE((SELECT launched_at FROM drone_patrol_sessions WHERE id = :s), CAST(:now AS timestamptz))))::bigint)
                ELSE 0 END,
            updated_at = now()
        WHERE id = :d
    """), {"st": drone_status, "lat": last.latitude if last else None, "lng": last.longitude if last else None,
           "alt": last.altitude_m if last else None, "hdg": last.heading_deg if last else None,
           "spd": last.speed_mps if last else None, "bat": round(last.battery_pct) if last else None,
           "heard": bool(up.samples) and not comms_lost, "now": now, "lost": comms_lost,
           "outcome": up.outcome, "last_at": last.recorded_at if last else None, "s": sid, "d": drone_id})

    who = session.get("drone_name") or "The drone"
    what = session.get("mission_name") or "its mission"
    if comms_lost:
        await raise_alert(db, out, code="drone.comms_lost", severity="high", site_id=session["site_id"],
                          session_id=sid, drone_id=drone_id, title=f"Drone link lost: {who}",
                          message=f"{who} lost its link during {what}. "
                                  "It is following its lost-link safety behaviour.")
    if up.outcome == "FAILED":
        await raise_alert(db, out, code="drone.mission_failed", severity="high", site_id=session["site_id"],
                          session_id=sid, drone_id=drone_id, title=f"Drone mission failed: {what}",
                          message=f"{what} did not complete. {up.failure_reason or ''}".strip(),
                          params={"failure_code": up.failure_code})
    elif up.outcome and up.failure_code:
        await raise_alert(db, out, code="drone.flight_fault", severity="medium", site_id=session["site_id"],
                          session_id=sid, drone_id=drone_id, title=f"Drone fault after patrol: {who}",
                          message=f"{what} completed its waypoints, but {who} reported: {up.failure_reason}",
                          params={"failure_code": up.failure_code})

    if last:
        out.add("drone_telemetry", {"drone_id": str(drone_id), "session_id": str(sid),
                                    "latitude": last.latitude, "longitude": last.longitude,
                                    "altitude_m": last.altitude_m, "heading_deg": last.heading_deg,
                                    "speed_mps": last.speed_mps, "battery_pct": last.battery_pct,
                                    "mission_state": last.mission_state,
                                    "recorded_at": last.recorded_at.isoformat()})
    if status != session["status"] or comms_lost or comms_back:
        out.add("drone_session_updated", {"session_id": str(sid), "drone_id": str(drone_id),
                                          "status": status, "previous_status": session["status"],
                                          "failure_code": up.failure_code, "comms_lost": comms_lost})
    return status


# ── Shared by the central runner and the edge sync ───────────────────────────
#
# A drone flown by the central runner and one flown by a site edge gateway are
# judged, recorded and alerted on by the same functions, so where a drone is
# flown from never changes what its record says.

async def end_session(db: AsyncSession, session: dict, status: str, now: datetime, out: Announcements,
                      *, reason: str | None = None, blocked: bool = False, preflight: dict | None = None,
                      failure_code: str | None = None) -> None:
    await db.execute(text("""
        UPDATE drone_patrol_sessions
           SET status = :st, ended_at = :now, updated_at = now(),
               blocked_reason = CASE WHEN CAST(:blocked AS boolean) THEN :reason ELSE blocked_reason END,
               failure_reason = CASE WHEN CAST(:blocked AS boolean) THEN failure_reason
                                     ELSE COALESCE(:reason, failure_reason) END,
               failure_code = COALESCE(CAST(:fcode AS text), failure_code),
               preflight_result = COALESCE(CAST(:pre AS jsonb), preflight_result)
         WHERE id = :id
    """), {"st": status, "now": now, "blocked": blocked, "reason": reason, "id": session["id"],
           "pre": json.dumps(preflight) if preflight else None, "fcode": failure_code})
    out.add("drone_session_updated", {"session_id": str(session["id"]), "status": status,
                                      "previous_status": session["status"], "reason": reason})


async def recheck_before_launch(db: AsyncSession, session: dict, now: datetime,
                                out: Announcements) -> PreflightResult | None:
    """Pre-flight again, with the freshest facts, against the session's FROZEN
    route. Returns the passing result, or None after recording the session as
    BLOCKED and alerting."""
    bundle = await load_bundle(db, session["mission_id"]) if session["mission_id"] else None
    if bundle is None:
        await end_session(db, session, "BLOCKED", now, out, blocked=True,
                          reason="The mission was deleted before launch.")
        return None
    snap = session.get("config_snapshot") or {}
    bundle.route = snap.get("route") or bundle.route
    bundle.waypoints = snap.get("waypoints") or bundle.waypoints
    result = await preflight(db, bundle, now, exclude_session_id=session["id"])
    if result.passed:
        return result
    await end_session(db, session, "BLOCKED", now, out, blocked=True, reason=result.reason(),
                      preflight=result.as_json())
    await raise_alert(db, out, code="drone.preflight_blocked", severity="medium",
                      site_id=session["site_id"], session_id=session["id"], drone_id=session["drone_id"],
                      title=f"Drone mission blocked: {session.get('mission_name')}",
                      message=f"{session.get('mission_name')} did not launch. {result.reason()}")
    return None


async def apply_health(db: AsyncSession, drone: dict, h, out: Announcements) -> str:
    """Record a drone's reported health — its heartbeat. A drone that answers
    is, by definition, not lost; its status follows what it reports, except
    where a person set it. Returns the drone's new status."""
    new_status = h.status_hint or drone["status"]
    if drone["status"] not in ("OFFLINE", "COMMUNICATION_LOST", "READY", "CHARGING", "STANDBY"):
        new_status = drone["status"]
    await db.execute(text("""
        UPDATE drones SET status = :st, battery_level = COALESCE(:bat, battery_level),
               battery_health = COALESCE(:bh, battery_health),
               gps_status = :gps, communication_status = :comms, camera_status = :cam,
               storage_status = :sto, temperature_c = COALESCE(:temp, temperature_c),
               current_latitude = COALESCE(:lat, current_latitude),
               current_longitude = COALESCE(:lng, current_longitude),
               current_altitude_m = COALESCE(:alt, current_altitude_m),
               last_heartbeat_at = :at, comms_alerted_at = NULL, updated_at = now()
         WHERE id = :id
    """), {"st": new_status, "bat": round(h.battery_level) if h.battery_level is not None else None,
           "bh": round(h.battery_health) if h.battery_health is not None else None,
           "gps": h.gps_status, "comms": h.communication_status, "cam": h.camera_status,
           "sto": h.storage_status, "temp": h.temperature_c, "lat": h.latitude, "lng": h.longitude,
           "alt": h.altitude_m, "at": h.observed_at, "id": drone["id"]})
    if drone["status"] != new_status:
        out.add("drone_status_changed", {"drone_id": str(drone["id"]), "status": new_status,
                                         "previous_status": drone["status"]})
    return new_status


async def finish_command(db: AsyncSession, cmd: dict, status: str, result: str, now: datetime,
                         out: Announcements) -> None:
    """Close a command with its outcome. An abort or return-home that could not
    be delivered is the one failure that must reach a person at once."""
    if status == "FAILED" and cmd["command"] in ("ABORT", "RETURN_TO_HOME"):
        s = (await db.execute(text("SELECT site_id, drone_id, mission_name, drone_name "
                                   "FROM drone_patrol_sessions WHERE id = :id"),
                              {"id": cmd["session_id"]})).mappings().first() or {}
        await raise_alert(
            db, out, code="drone.command_failed", severity="critical",
            site_id=s.get("site_id"), session_id=cmd["session_id"], drone_id=s.get("drone_id"),
            title=f"{cmd['command'].replace('_', ' ').title()} failed: {s.get('drone_name') or 'drone'}",
            message=f"The {cmd['command'].lower().replace('_', ' ')} command could not be "
                    f"delivered: {result}. Take manual control if you can.")
    await db.execute(text(
        "UPDATE drone_session_commands SET status = :st, result = :r, processed_at = :now "
        " WHERE id = :id"), {"st": status, "r": result, "now": now, "id": cmd["id"]})
    out.add("drone_command_processed", {"command_id": str(cmd["id"]), "session_id": str(cmd["session_id"]),
                                        "command": cmd["command"], "status": status, "result": result})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def within_grace(scheduled_for: datetime, grace_minutes: int, now: datetime) -> bool:
    return now - scheduled_for <= timedelta(minutes=int(grace_minutes or 0))
