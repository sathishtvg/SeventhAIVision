"""Drone operations: patrol sessions as they happened, and the events they raised.

Read-only for sessions in this phase — starting, pausing and aborting a flight
need the provider layer (Phase 4). Events already have their full human
workflow here, because an operator must be able to act on one the moment it
exists, whatever raised it.

EVENT DECISIONS ARE NOT LICENCE-GATED. A licence that lapses at midnight must
not stop an operator closing out the intrusion a drone reported at 23:58. Only
creating, configuring and flying are gated (dependencies/drone_module.py).

A CLOSED EVENT STAYS CLOSED. Resolved and false-positive events cannot be
re-acknowledged or re-resolved: each decision is recorded with who made it and
when, and a second decision silently overwriting the first would falsify that
record. Two operators acting at once get a 409 that says who got there first.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_ai as ai
from app.services.drone_access import assert_site_visible, audit, scope_sql
from app.services.drone_providers import Capability, capabilities_of

router = APIRouter(prefix="/api/v1", tags=["drone-operations"])

_READ = Depends(require_permission("drone:read"))
_EVENT_READ = Depends(require_permission("drone:event:read"))
_ACK = Depends(require_permission("drone:event:acknowledge"))
_INVESTIGATE = Depends(require_permission("drone:event:investigate"))
_OPERATE = Depends(require_permission("drone:operate"))
_ABORT = Depends(require_permission("drone:mission:abort"))

OPEN = ("NEW", "ACKNOWLEDGED", "INVESTIGATING", "ESCALATED")
CLOSED = ("RESOLVED", "FALSE_POSITIVE")
Risk = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
#: Replaying a flight needs its shape, not every sample; above this the track is
#: thinned evenly so a long mission still loads as one response.
MAX_TRACK_POINTS = 2000


# ═════════════════════════════════════════════════════════════════════════════
# Patrol sessions
# ═════════════════════════════════════════════════════════════════════════════

_SESSION_SELECT = """
    SELECT ps.*, s.name AS site_name
      FROM drone_patrol_sessions ps
      LEFT JOIN sites s ON s.id = ps.site_id
"""


async def _session_or_404(db: AsyncSession, session_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_SESSION_SELECT + " WHERE ps.id = CAST(:id AS uuid)"),
                            {"id": str(session_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Patrol session not found")
    assert_site_visible(allowed, row["site_id"], "Patrol session")
    return dict(row)


@router.get("/drone-patrols", dependencies=[_READ])
async def list_sessions(
    site_id: uuid.UUID | None = Query(None),
    drone_id: uuid.UUID | None = Query(None),
    mission_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {
        "site": str(site_id) if site_id else None, "drone": str(drone_id) if drone_id else None,
        "mission": str(mission_id) if mission_id else None, "status": status_filter,
        "since": since, "until": until,
    }
    scope = scope_sql(allowed, "ps.site_id", params)
    where = f"""
         WHERE (CAST(:site AS uuid) IS NULL OR ps.site_id = CAST(:site AS uuid))
           AND (CAST(:drone AS uuid) IS NULL OR ps.drone_id = CAST(:drone AS uuid))
           AND (CAST(:mission AS uuid) IS NULL OR ps.mission_id = CAST(:mission AS uuid))
           AND (CAST(:status AS text) IS NULL OR ps.status = CAST(:status AS text))
           AND (CAST(:since AS timestamptz) IS NULL OR ps.created_at >= CAST(:since AS timestamptz))
           AND (CAST(:until AS timestamptz) IS NULL OR ps.created_at <  CAST(:until AS timestamptz))
           {scope}"""
    return await paginate(
        db,
        _SESSION_SELECT + where + " ORDER BY ps.created_at DESC LIMIT :limit OFFSET :offset",
        "SELECT count(*) FROM drone_patrol_sessions ps" + where,
        params, limit, offset,
    )


@router.get("/drone-patrols/{session_id}", dependencies=[_READ])
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    session = await _session_or_404(db, session_id, allowed)
    waypoints = (await db.execute(text(
        "SELECT * FROM drone_session_waypoints WHERE session_id = CAST(:id AS uuid) ORDER BY sequence"),
        {"id": str(session_id)})).mappings().all()
    risk = (await db.execute(text("""
        SELECT risk_level, count(*) AS n FROM drone_events
         WHERE session_id = CAST(:id AS uuid) GROUP BY risk_level
    """), {"id": str(session_id)})).mappings().all()
    media = (await db.execute(text(
        "SELECT count(*) FROM drone_event_media WHERE session_id = CAST(:id AS uuid)"),
        {"id": str(session_id)})).scalar()
    return {**session, "waypoints": [dict(w) for w in waypoints],
            "events_by_risk": {r["risk_level"]: r["n"] for r in risk}, "media_count": media}


@router.get("/drone-patrols/{session_id}/track", dependencies=[_READ])
async def session_track(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                        allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """The path the drone actually flew, for replay. Thinned evenly to at most
    MAX_TRACK_POINTS, always keeping the first and last sample."""
    session = await _session_or_404(db, session_id, allowed)
    total = (await db.execute(text(
        "SELECT count(*) FROM drone_telemetry WHERE session_id = CAST(:id AS uuid)"),
        {"id": str(session_id)})).scalar()
    step = max(1, -(-total // MAX_TRACK_POINTS))  # ceiling division
    rows = (await db.execute(text("""
        SELECT recorded_at, latitude, longitude, altitude_m, heading_deg, speed_mps,
               battery_pct, mission_state, waypoint_sequence
          FROM (SELECT t.*, row_number() OVER (ORDER BY recorded_at) AS rn
                  FROM drone_telemetry t WHERE t.session_id = CAST(:id AS uuid)) x
         WHERE (rn - 1) % :step = 0 OR rn = :total
         ORDER BY recorded_at
    """), {"id": str(session_id), "step": step, "total": total})).mappings().all()
    return {"session_id": str(session_id), "drone_id": session["drone_id"],
            "total_samples": total, "returned": len(rows), "points": [dict(r) for r in rows]}


# ═════════════════════════════════════════════════════════════════════════════
# Flight commands
# ═════════════════════════════════════════════════════════════════════════════
#
# Queued, not executed here: the drone runner carries each out within a couple
# of seconds and records the result on the command. Nothing in this module talks
# to a drone.
#
# NOT LICENCE-GATED. A licence that lapses mid-flight must never stop anyone
# pausing, aborting or bringing a drone home. Only starting a flight is gated.

_BEFORE_LAUNCH = ("SCHEDULED", "PRECHECK", "READY")
_TERMINAL = ("COMPLETED", "FAILED", "ABORTED", "CANCELLED", "BLOCKED", "MISSED")
_NEEDS = {"PAUSE": Capability.PAUSE, "RESUME": Capability.RESUME, "ABORT": Capability.ABORT,
          "RETURN_TO_HOME": Capability.RETURN_TO_HOME}


class CommandIn(BaseModel):
    reason: str | None = Field(None, max_length=2000)


def _command_allowed(command: str, status: str) -> str | None:
    """Why this command cannot apply to a session in this state, or None."""
    if status in _TERMINAL:
        return f"The session has already ended ({status.lower()})."
    if command == "CANCEL" and status not in _BEFORE_LAUNCH:
        return "The drone has already launched; abort it or return it home instead."
    if command == "PAUSE" and status != "ACTIVE":
        return f"Only an active mission can be paused (it is {status.lower()})."
    if command == "RESUME" and status != "PAUSED":
        return "The mission is not paused."
    return None


async def _queue(db: AsyncSession, request: Request, token: TokenPayload, session_id: uuid.UUID,
                 allowed, command: str, reason: str | None) -> dict:
    session = await _session_or_404(db, session_id, allowed)
    problem = _command_allowed(command, session["status"])
    if problem:
        raise HTTPException(409, problem)
    need = _NEEDS.get(command)
    # Before launch, abort and return-home simply cancel: no drone is involved.
    if need and session["status"] not in _BEFORE_LAUNCH:
        key = ((session.get("config_snapshot") or {}).get("drone") or {}).get("provider_key")
        if need not in capabilities_of(key):
            verb = command.lower().replace("_", " ")
            raise HTTPException(409, f"The drone's provider ({key or 'none'}) cannot {verb}.")
    row = (await db.execute(text("""
        INSERT INTO drone_session_commands
            (tenant_id, session_id, command, reason, requested_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :c, :r, CAST(:u AS uuid))
        ON CONFLICT (session_id, command) WHERE status = 'PENDING' DO NOTHING
        RETURNING *
    """), {"s": session_id, "c": command, "r": reason, "u": token.user_id})).mappings().first()
    created = row is not None
    if not created:
        # The same command is already waiting: one press, or two operators at
        # once, is one command.
        row = (await db.execute(text(
            "SELECT * FROM drone_session_commands WHERE session_id = :s AND command = :c "
            " AND status = 'PENDING'"), {"s": session_id, "c": command})).mappings().first()
    else:
        await audit(db, request, token, f"drone.command.{command.lower()}", "drone_patrol_session",
                    session_id, {"reason": reason} if reason else None)
    result = {"command": dict(row), "queued": created, "session_status": session["status"]}
    await db.commit()
    return result


@router.post("/drone-patrols/{session_id}/pause", status_code=202, dependencies=[_OPERATE])
async def pause_session(session_id: uuid.UUID, request: Request, body: CommandIn | None = None,
                        db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload),
                        allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _queue(db, request, token, session_id, allowed, "PAUSE", (body or CommandIn()).reason)


@router.post("/drone-patrols/{session_id}/resume", status_code=202, dependencies=[_OPERATE])
async def resume_session(session_id: uuid.UUID, request: Request, body: CommandIn | None = None,
                         db: AsyncSession = Depends(get_db_with_tenant),
                         token: TokenPayload = Depends(get_token_payload),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _queue(db, request, token, session_id, allowed, "RESUME", (body or CommandIn()).reason)


@router.post("/drone-patrols/{session_id}/abort", status_code=202, dependencies=[_ABORT])
async def abort_session(session_id: uuid.UUID, request: Request, body: CommandIn | None = None,
                        db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload),
                        allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """End the mission now; the drone follows its provider's safe behaviour
    home. Before launch, this cancels."""
    return await _queue(db, request, token, session_id, allowed, "ABORT", (body or CommandIn()).reason)


@router.post("/drone-patrols/{session_id}/return-to-home", status_code=202, dependencies=[_ABORT])
async def return_session_home(session_id: uuid.UUID, request: Request, body: CommandIn | None = None,
                              db: AsyncSession = Depends(get_db_with_tenant),
                              token: TokenPayload = Depends(get_token_payload),
                              allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _queue(db, request, token, session_id, allowed, "RETURN_TO_HOME",
                        (body or CommandIn()).reason)


@router.post("/drone-patrols/{session_id}/cancel", status_code=202, dependencies=[_ABORT])
async def cancel_session(session_id: uuid.UUID, request: Request, body: CommandIn | None = None,
                         db: AsyncSession = Depends(get_db_with_tenant),
                         token: TokenPayload = Depends(get_token_payload),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return await _queue(db, request, token, session_id, allowed, "CANCEL", (body or CommandIn()).reason)


@router.get("/drone-patrols/{session_id}/commands", dependencies=[_READ])
async def list_commands(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                        allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Every command given to this flight, who gave it, and what became of it."""
    await _session_or_404(db, session_id, allowed)
    rows = (await db.execute(text("""
        SELECT c.*, u.full_name AS requested_by_name FROM drone_session_commands c
          LEFT JOIN users u ON u.id = c.requested_by_user_id
         WHERE c.session_id = :s ORDER BY c.requested_at
    """), {"s": session_id})).mappings().all()
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# Events
# ═════════════════════════════════════════════════════════════════════════════

_EVENT_SELECT = """
    SELECT e.*, s.name AS site_name, d.name AS drone_name, d.code AS drone_code,
           ps.session_number, ps.mission_name,
           au.full_name AS acknowledged_by_name, ru.full_name AS resolved_by_name
      FROM drone_events e
      LEFT JOIN sites s                  ON s.id = e.site_id
      LEFT JOIN drones d                 ON d.id = e.drone_id
      LEFT JOIN drone_patrol_sessions ps ON ps.id = e.session_id
      LEFT JOIN users au                 ON au.id = e.acknowledged_by_user_id
      LEFT JOIN users ru                 ON ru.id = e.resolved_by_user_id
"""


async def _event_or_404(db: AsyncSession, event_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_EVENT_SELECT + " WHERE e.id = CAST(:id AS uuid)"),
                            {"id": str(event_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Drone event not found")
    assert_site_visible(allowed, row["site_id"], "Drone event")
    return dict(row)


@router.get("/drone-events", dependencies=[_EVENT_READ])
async def list_events(
    site_id: uuid.UUID | None = Query(None),
    session_id: uuid.UUID | None = Query(None),
    drone_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    open_only: bool = Query(False, description="Only NEW, ACKNOWLEDGED, INVESTIGATING or ESCALATED"),
    risk_level: Risk | None = Query(None),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    params: dict = {
        "site": str(site_id) if site_id else None, "session": str(session_id) if session_id else None,
        "drone": str(drone_id) if drone_id else None, "status": status_filter, "open": open_only,
        "risk": risk_level, "since": since, "until": until,
    }
    scope = scope_sql(allowed, "e.site_id", params)
    where = f"""
         WHERE (CAST(:site AS uuid) IS NULL OR e.site_id = CAST(:site AS uuid))
           AND (CAST(:session AS uuid) IS NULL OR e.session_id = CAST(:session AS uuid))
           AND (CAST(:drone AS uuid) IS NULL OR e.drone_id = CAST(:drone AS uuid))
           AND (CAST(:status AS text) IS NULL OR e.status = CAST(:status AS text))
           AND (NOT CAST(:open AS boolean) OR e.status IN ('NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED'))
           AND (CAST(:risk AS text) IS NULL OR e.risk_level = CAST(:risk AS text))
           AND (CAST(:since AS timestamptz) IS NULL OR e.detected_at >= CAST(:since AS timestamptz))
           AND (CAST(:until AS timestamptz) IS NULL OR e.detected_at <  CAST(:until AS timestamptz))
           {scope}"""
    return await paginate(
        db,
        _EVENT_SELECT + where + " ORDER BY e.detected_at DESC LIMIT :limit OFFSET :offset",
        "SELECT count(*) FROM drone_events e" + where,
        params, limit, offset,
    )


@router.get("/drone-events/{event_id}", dependencies=[_EVENT_READ])
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                    allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Everything an investigator needs on one screen: the event, its media,
    the detections it rests on, the fixed cameras correlated with it, and the
    alert and incident it fed."""
    event = await _event_or_404(db, event_id, allowed)
    observations = (await db.execute(text("""
        SELECT id, source, detection_id, module_type, label, ai_confidence, detected_at,
               drone_latitude, drone_longitude, drone_altitude_m, attributes
          FROM drone_observations WHERE event_id = CAST(:id AS uuid)
         ORDER BY detected_at LIMIT 200
    """), {"id": str(event_id)})).mappings().all()
    media = (await db.execute(text("""
        SELECT id, media_kind, storage_location, sync_state, checksum_sha256, size_bytes,
               duration_seconds, captured_at, telemetry_snapshot
          FROM drone_event_media WHERE event_id = CAST(:id AS uuid) ORDER BY captured_at
    """), {"id": str(event_id)})).mappings().all()
    cameras = (await db.execute(text("""
        SELECT id, camera_id, camera_name, distance_m, correlation_method, related_detection_id,
               related_alert_id, window_start, window_end
          FROM drone_event_cameras WHERE event_id = CAST(:id AS uuid)
         ORDER BY distance_m NULLS LAST
    """), {"id": str(event_id)})).mappings().all()
    incident = None
    if event["incident_id"]:
        incident = (await db.execute(text(
            "SELECT id, title, severity, status, assigned_to_user_id, dispatched_guard_id, created_at "
            "  FROM incidents WHERE id = CAST(:id AS uuid)"),
            {"id": str(event["incident_id"])})).mappings().first()
    alert = None
    if event["alert_id"]:
        alert = (await db.execute(text(
            "SELECT id, title, severity, status, created_at FROM alerts WHERE id = CAST(:id AS uuid)"),
            {"id": str(event["alert_id"])})).mappings().first()
    return {**event, "media": [dict(m) for m in media], "observations": [dict(o) for o in observations],
            "cameras": [dict(c) for c in cameras],
            "incident": dict(incident) if incident else None, "alert": dict(alert) if alert else None}


class Decision(BaseModel):
    note: str | None = Field(None, max_length=2000)


class FalsePositive(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


def _refuse_if_closed(event: dict) -> None:
    if event["status"] in CLOSED:
        who = event.get("resolved_by_name") or "another user"
        when = event.get("resolved_at")
        stamp = f" on {when:%d %b %Y %H:%M} UTC" if when else ""
        label = "marked a false positive" if event["status"] == "FALSE_POSITIVE" else "resolved"
        raise HTTPException(409, f"This event was already {label} by {who}{stamp}.")


async def _transition(db: AsyncSession, request: Request, token: TokenPayload, event_id: uuid.UUID,
                      allowed, *, to: str, action: str, sets: str = "", extra: dict | None = None,
                      detail: dict | None = None) -> dict:
    """Move an event to `to` only if it is still open — checked again in the
    UPDATE itself, so two operators cannot both win."""
    event = await _event_or_404(db, event_id, allowed)
    _refuse_if_closed(event)
    row = (await db.execute(text(f"""
        UPDATE drone_events SET status = :to, updated_at = now() {sets}
         WHERE id = CAST(:id AS uuid)
           AND status IN ('NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED')
        RETURNING id
    """), {"to": to, "id": str(event_id), **(extra or {})})).first()
    if row is None:
        # No rollback here: the UPDATE matched nothing, so there is nothing to
        # undo — and a rollback would drop the transaction-local tenant setting,
        # making the re-read below fail instead of explaining the conflict.
        _refuse_if_closed(await _event_or_404(db, event_id, allowed))
        raise HTTPException(409, "This event changed while you were looking at it. Reload it.")
    await audit(db, request, token, action, "drone_event", event_id,
                {"from": event["status"], "to": to, **(detail or {})})
    result = await _event_or_404(db, event_id, allowed)
    await db.commit()
    return result


@router.post("/drone-events/{event_id}/acknowledge", dependencies=[_ACK])
async def acknowledge_event(
    event_id: uuid.UUID, body: Decision, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    event = await _event_or_404(db, event_id, allowed)
    if event["status"] != "NEW":
        _refuse_if_closed(event)
        who = event.get("acknowledged_by_name") or "another user"
        raise HTTPException(409, f"This event was already acknowledged by {who}.")
    return await _transition(
        db, request, token, event_id, allowed, to="ACKNOWLEDGED", action="drone.event.acknowledge",
        sets=", acknowledged_by_user_id = CAST(:by AS uuid), acknowledged_at = now()",
        extra={"by": token.user_id}, detail={"note": body.note} if body.note else None)


@router.post("/drone-events/{event_id}/investigate", dependencies=[_INVESTIGATE])
async def investigate_event(
    event_id: uuid.UUID, body: Decision, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Taking an event on also acknowledges it, if nobody had."""
    return await _transition(
        db, request, token, event_id, allowed, to="INVESTIGATING", action="drone.event.investigate",
        sets=(", acknowledged_by_user_id = COALESCE(acknowledged_by_user_id, CAST(:by AS uuid)), "
              "  acknowledged_at = COALESCE(acknowledged_at, now())"),
        extra={"by": token.user_id}, detail={"note": body.note} if body.note else None)


@router.post("/drone-events/{event_id}/escalate", dependencies=[_INVESTIGATE])
async def escalate_event(
    event_id: uuid.UUID, body: Decision, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    return await _transition(
        db, request, token, event_id, allowed, to="ESCALATED", action="drone.event.escalate",
        sets=(", acknowledged_by_user_id = COALESCE(acknowledged_by_user_id, CAST(:by AS uuid)), "
              "  acknowledged_at = COALESCE(acknowledged_at, now())"),
        extra={"by": token.user_id}, detail={"note": body.note} if body.note else None)


@router.post("/drone-events/{event_id}/resolve", dependencies=[_INVESTIGATE])
async def resolve_event(
    event_id: uuid.UUID, body: Decision, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    return await _transition(
        db, request, token, event_id, allowed, to="RESOLVED", action="drone.event.resolve",
        sets=", resolved_by_user_id = CAST(:by AS uuid), resolved_at = now()",
        extra={"by": token.user_id}, detail={"note": body.note} if body.note else None)


@router.post("/drone-events/{event_id}/false-positive", dependencies=[_INVESTIGATE])
async def mark_false_positive(
    event_id: uuid.UUID, body: FalsePositive, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A reason is required: false-positive rates feed patrol intelligence, and
    "operator dismissed it" with no reason teaches nothing."""
    return await _transition(
        db, request, token, event_id, allowed, to="FALSE_POSITIVE", action="drone.event.false_positive",
        sets=(", false_positive_reason = :reason, resolved_by_user_id = CAST(:by AS uuid), "
              "  resolved_at = now()"),
        extra={"by": token.user_id, "reason": body.reason}, detail={"reason": body.reason})


@router.get("/drone-ai/modules", dependencies=[_READ])
async def ai_modules():
    """What each existing AI module can do on a drone's moving camera, and how
    risk is weighed — for the profile editor and anyone asking why an event
    scored what it did."""
    return {
        "modules": [{"module_type": m, "suitability": k, "note": note,
                     "default_base_severity": ai.DEFAULT_BASE_SEVERITY.get(m)}
                    for m, (k, note) in ai.SUITABILITY.items()],
        "risk_levels": [{"level": lvl, "from_score": ai.THRESHOLDS.get(lvl, 0)} for lvl in ai.LEVELS],
        "verification": {"detections": ai.VERIFY_DETECTIONS, "default_seconds": ai.DEFAULT_VERIFY_SECONDS,
                         "grouping_window_seconds": int(ai.GROUPING_WINDOW.total_seconds()),
                         "immediate_modules": sorted(ai.IMMEDIATE_MODULES),
                         "immediate_confidence": ai.IMMEDIATE_CONFIDENCE},
    }


# ═════════════════════════════════════════════════════════════════════════════
# Media
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/drone-media/{media_id}/file", dependencies=[_EVENT_READ])
async def get_media_file(media_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """The file itself, if the centre holds a copy. Footage the recording policy
    keeps at the site is answered 409 with where it is, not a broken link."""
    m = (await db.execute(text("""
        SELECT m.*, COALESCE(e.site_id, s.site_id) AS owner_site_id, g.name AS gateway_name
          FROM drone_event_media m
          LEFT JOIN drone_events e ON e.id = m.event_id
          LEFT JOIN drone_patrol_sessions s ON s.id = m.session_id
          LEFT JOIN drone_edge_gateways g ON g.id = m.edge_gateway_id
         WHERE m.id = CAST(:id AS uuid)
    """), {"id": str(media_id)})).mappings().first()
    if m is None:
        raise HTTPException(404, "Drone media not found")
    assert_site_visible(allowed, m["owner_site_id"], "Drone media")
    if m["storage_location"] == "local":
        where = m["gateway_name"] or "the site's edge gateway"
        state = {"pending": "An upload has been requested.", "failed": "Its last upload failed; it will be retried.",
                 "uploading": "It is being uploaded."}.get(m["sync_state"],
                                                          "The recording policy keeps it at the site.")
        raise HTTPException(409, f"This file is held at {where} and has not been uploaded. {state}")
    image = m["media_kind"] == "SNAPSHOT"
    if settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import presign_url
        return RedirectResponse(await presign_url(m["storage_path"], settings.S3_PRESIGN_TTL_SECONDS), 307)
    root = Path(settings.EVIDENCE_ROOT).resolve()
    path = (root / m["storage_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "The file is not in central storage.")
    return FileResponse(str(path), media_type="image/jpeg" if image else "video/mp4")

