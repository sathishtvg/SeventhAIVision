"""AI security intelligence: what the platform's sources have reported, in one shape.

The layer described in AI_SECURITY_INTELLIGENCE_GAP_ANALYSIS.md, as far as it is
built: whether it is running for this organisation, the security events it has
read, the context of each, the situations those events have been joined into
and why, how each situation has been assessed, what the layer suggests doing
about it, and what an administrator has said about each site.

NOTHING HERE CHANGES AN ALERT, AN INCIDENT OR ANY OTHER EXISTING RECORD. The
events are written by the intelligence runner (app/intelligence_main.py); the
switch that turns it on for a tenant is the ordinary tenant setting
`intel.enabled`, changed through the settings API by someone who may change
settings. The only things written here are the layer's own site and camera
profiles and camera links, by someone with `intel:manage`, and each change is
audited.

Everything needs `intel:read`; what the layer suggests needs
`intel:recommendation:read` as well. A caller restricted to certain sites sees
those sites' events and profiles; an event with no site is not shown to them —
the same rule as every other site-scoped list.

A SUGGESTION IS SERVED AS A SUGGESTION. Reading one changes nothing, and every
response that carries one says `is_decision: false`.
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, intel_config, intel_context, intel_decisions, intel_runner, intel_timeline
from app.services.intel_events import SEVERITIES, SOURCE_TYPES

router = APIRouter(prefix="/api/v1/security-intelligence", tags=["security-intelligence"],
                   dependencies=[Depends(require_permission("intel:read"))])

_EVENT_COLUMNS = """
    e.id, e.site_id, s.name AS site_name, e.source_type, e.source_table, e.source_id, e.event_type,
    e.occurred_at, e.camera_id, c.name AS camera_name, e.drone_id, e.alert_id, e.incident_id,
    e.detection_id, e.subject_kind, e.subject_ref, e.subject_verdict, e.confidence, e.severity,
    e.title, e.latitude, e.longitude, e.location_label, e.attributes, e.status, e.ingested_at
"""
_EVENT_FROM = """
      FROM security_events e
      LEFT JOIN sites s ON s.id = e.site_id
      LEFT JOIN cameras c ON c.id = e.camera_id
"""


def _one_of(value: str | None, allowed: tuple[str, ...], what: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(422, f"Unknown {what} '{value}'. One of: {', '.join(allowed)}.")


@router.get("/status")
async def status(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Whether the layer is on for this organisation, whether its runner is
    alive, how far each source has been read, and how many events arrived in
    the last 24 hours.

    The runner's state is `unknown` when it could not be asked — never
    `running` by default."""
    enabled = await intel_config.is_enabled(db)
    asked, heartbeat = await intel_runner.ask_heartbeat()

    params: dict = {}
    scope = site_scope_clause(allowed, "e.site_id", params)
    where = f"AND {scope}" if scope else ""
    recent = (await db.execute(text(f"""
        SELECT e.source_type, count(*) AS events
          FROM security_events e
         WHERE e.occurred_at > now() - interval '24 hours' {where}
         GROUP BY e.source_type ORDER BY e.source_type
    """), params)).mappings().all()
    sources = (await db.execute(text("""
        SELECT source, read_from, last_run_at, last_count, total_count, last_error
          FROM security_ingest_cursors ORDER BY source
    """))).mappings().all()
    return {
        "enabled": enabled,
        "runner": {
            "state": intel_runner.runner_state(asked, heartbeat),
            "last_seen_at": heartbeat.get("at") if heartbeat else None,
        },
        "last_24_hours": {r["source_type"]: r["events"] for r in recent},
        "sources": [dict(r) for r in sources],
    }


@router.get("/events")
async def list_events(
    site_id: uuid.UUID | None = Query(None),
    camera_id: uuid.UUID | None = Query(None),
    source_type: str | None = Query(None),
    severity: str | None = Query(None),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Normalised security events, newest first."""
    _one_of(source_type, SOURCE_TYPES, "source type")
    _one_of(severity, SEVERITIES, "severity")
    if since is not None and until is not None and until < since:
        raise HTTPException(422, "The period ends before it starts.")

    where: list[str] = []
    params: dict = {}
    scope = site_scope_clause(allowed, "e.site_id", params)
    if scope:
        where.append(scope)
    for column, value, name in (("e.site_id", site_id, "site"), ("e.camera_id", camera_id, "camera")):
        if value is not None:
            where.append(f"{column} = CAST(:{name} AS uuid)")
            params[name] = str(value)
    for column, value, name in (("e.source_type", source_type, "source_type"), ("e.severity", severity, "severity")):
        if value is not None:
            where.append(f"{column} = :{name}")
            params[name] = value
    if since is not None:
        where.append("e.occurred_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("e.occurred_at <= :until")
        params["until"] = until
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    return await paginate(
        db,
        f"SELECT {_EVENT_COLUMNS} {_EVENT_FROM} {clause} "
        "ORDER BY e.occurred_at DESC, e.id DESC LIMIT :limit OFFSET :offset",
        f"SELECT count(*) FROM security_events e {clause}",
        params, limit, offset,
    )


@router.get("/events/{event_id}")
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One event. 404 when it does not exist or belongs to a site the caller
    cannot see — the two are not told apart."""
    row = (await db.execute(
        text(f"SELECT {_EVENT_COLUMNS} {_EVENT_FROM} WHERE e.id = CAST(:id AS uuid)"),
        {"id": str(event_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Event not found")
    return dict(row)


@router.get("/events/{event_id}/context")
async def get_event_context(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What was expected at the event's place at the moment it happened: the
    site's hours, zones in force, who was on site, doors and alarms nearby in
    time, patrols under way, and the camera's record.

    Every statement names the table or setting it came from. What the platform
    does not know is listed under `unknowns` and is not guessed."""
    row = (await db.execute(
        text("SELECT * FROM security_events WHERE id = CAST(:id AS uuid)"), {"id": str(event_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Event not found")
    return {"event_id": str(event_id), **(await intel_context.context_for(db, row))}


# ─── Site and camera profiles ────────────────────────────────────────────────

Criticality = Literal["low", "medium", "high", "critical"]


class SiteProfileIn(BaseModel):
    """Everything an administrator says about a site, replaced as a whole. A
    field left out, or null, is *not set* — which the context engine reports as
    not known rather than assuming a default."""

    model_config = ConfigDict(extra="forbid")

    timezone: str | None = Field(None, max_length=64)
    business_hours: dict | None = None
    closed_on_public_holidays: bool = True
    criticality: Criticality | None = None
    notes: str | None = Field(None, max_length=2000)


class CameraProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_label: str | None = Field(None, max_length=120)
    criticality: Criticality | None = None
    is_restricted_area: bool = False


_SITE_PROFILE = """
    SELECT s.id AS site_id, s.name AS site_name,
           p.timezone, p.business_hours, COALESCE(p.closed_on_public_holidays, TRUE) AS closed_on_public_holidays,
           p.criticality, p.notes, p.updated_at, p.updated_by_user_id, (p.id IS NOT NULL) AS has_profile
      FROM sites s LEFT JOIN security_site_profiles p ON p.site_id = s.id
"""


def _profile(row) -> dict:
    out = dict(row)
    if isinstance(out.get("business_hours"), str):
        out["business_hours"] = json.loads(out["business_hours"])
    return out


async def _site(db: AsyncSession, site_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_SITE_PROFILE + " WHERE s.id = CAST(:id AS uuid)"),
                            {"id": str(site_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Site not found")
    return _profile(row)


async def _cameras(db: AsyncSession, site_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT c.id AS camera_id, c.name AS camera_name, c.location,
               cp.area_label, cp.criticality, COALESCE(cp.is_restricted_area, FALSE) AS is_restricted_area
          FROM cameras c LEFT JOIN security_camera_profiles cp ON cp.camera_id = c.id
         WHERE c.site_id = CAST(:s AS uuid) ORDER BY c.name
    """), {"s": str(site_id)})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/site-profiles")
async def list_site_profiles(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every site the caller may see, with what has been said about it. A site
    nobody has described yet is listed with `has_profile` false and empty
    fields, so it is obvious what is still not known."""
    params: dict = {}
    scope = site_scope_clause(allowed, "s.id", params)
    rows = (await db.execute(text(
        _SITE_PROFILE + (f" WHERE {scope}" if scope else "") + " ORDER BY s.name"), params)).mappings().all()
    return [_profile(r) for r in rows]


@router.get("/site-profiles/{site_id}")
async def get_site_profile(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One site's profile and its cameras' profiles."""
    return {**(await _site(db, site_id, allowed)), "cameras": await _cameras(db, site_id)}


@router.put("/site-profiles/{site_id}", dependencies=[Depends(require_permission("intel:manage"))])
async def put_site_profile(
    site_id: uuid.UUID,
    body: SiteProfileIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
    token: TokenPayload = Depends(get_token_payload),
):
    """Set a site's hours, time zone and criticality. Replaces the profile."""
    await _site(db, site_id, allowed)
    try:
        hours = intel_context.validate_business_hours(body.business_hours)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except Exception as exc:  # noqa: BLE001 — any failure means "not a zone"
            raise HTTPException(422, f"'{body.timezone}' is not a time zone name, e.g. Asia/Singapore.") from exc
    await db.execute(text("""
        INSERT INTO security_site_profiles
               (tenant_id, site_id, timezone, business_hours, closed_on_public_holidays, criticality, notes,
                updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), :tz, CAST(:h AS jsonb), :closed,
                :crit, :notes, CAST(:u AS uuid))
        ON CONFLICT (site_id) DO UPDATE SET
               timezone = EXCLUDED.timezone, business_hours = EXCLUDED.business_hours,
               closed_on_public_holidays = EXCLUDED.closed_on_public_holidays,
               criticality = EXCLUDED.criticality, notes = EXCLUDED.notes,
               updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = now()
    """), {"s": str(site_id), "tz": body.timezone, "h": json.dumps(hours) if hours is not None else None,
           "closed": body.closed_on_public_holidays, "crit": body.criticality, "notes": body.notes,
           "u": token.user_id})
    await intel_audit.record(db, request, token, "intel.site_profile.update", "security_site_profile", site_id,
                             site_id=site_id, detail={
                                 "criticality": body.criticality, "timezone": body.timezone,
                                 "hours_defined": hours is not None,
                                 "closed_on_public_holidays": body.closed_on_public_holidays})
    await db.commit()
    return {"site_id": str(site_id), "timezone": body.timezone, "business_hours": hours,
            "closed_on_public_holidays": body.closed_on_public_holidays, "criticality": body.criticality,
            "notes": body.notes, "has_profile": True}


@router.put("/site-profiles/{site_id}/cameras/{camera_id}",
            dependencies=[Depends(require_permission("intel:manage"))])
async def put_camera_profile(
    site_id: uuid.UUID,
    camera_id: uuid.UUID,
    body: CameraProfileIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
    token: TokenPayload = Depends(get_token_payload),
):
    """Say what a camera watches: an area name, how sensitive it is, and
    whether it is a restricted area. The camera must belong to the site."""
    await _site(db, site_id, allowed)
    owner = (await db.execute(text("SELECT site_id FROM cameras WHERE id = CAST(:c AS uuid)"),
                              {"c": str(camera_id)})).scalar_one_or_none()
    if owner is None or str(owner) != str(site_id):
        raise HTTPException(404, "Camera not found at this site")
    await db.execute(text("""
        INSERT INTO security_camera_profiles
               (tenant_id, camera_id, area_label, criticality, is_restricted_area, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :area, :crit, :restricted,
                CAST(:u AS uuid))
        ON CONFLICT (camera_id) DO UPDATE SET
               area_label = EXCLUDED.area_label, criticality = EXCLUDED.criticality,
               is_restricted_area = EXCLUDED.is_restricted_area,
               updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = now()
    """), {"c": str(camera_id), "area": body.area_label, "crit": body.criticality,
           "restricted": body.is_restricted_area, "u": token.user_id})
    await intel_audit.record(db, request, token, "intel.camera_profile.update", "security_camera_profile",
                             camera_id, site_id=site_id, detail={
                                 "criticality": body.criticality, "area_label": body.area_label,
                                 "is_restricted_area": body.is_restricted_area})
    await db.commit()
    return {"camera_id": str(camera_id), "site_id": str(site_id), "area_label": body.area_label,
            "criticality": body.criticality, "is_restricted_area": body.is_restricted_area}


# ─── Situations ──────────────────────────────────────────────────────────────

SITUATION_STATUSES = ("ACTIVE", "SETTLED")
RISK_LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

_SITUATION_COLUMNS = """
    x.id, x.situation_number, x.title, x.status, x.severity, x.site_id, s.name AS site_name,
    x.started_at, x.last_event_at, x.event_count, x.duplicate_count, x.source_types,
    x.primary_camera_id, c.name AS primary_camera_name, x.location_label, x.latitude, x.longitude,
    x.correlation_confidence, x.settled_at, x.created_at, x.updated_at,
    x.risk_score, x.risk_level, x.assessed_at,
    x.decision_status, x.last_decided_at, x.closed_at, x.incident_id, x.incident_confirmed_at,
    (x.closed_at IS NULL AND x.last_decision_id IS NOT NULL
        AND x.assessment_id IS DISTINCT FROM (SELECT ld.assessment_id FROM security_decisions ld
                                               WHERE ld.id = x.last_decision_id)) AS reassessed_since_decision
"""
_SITUATION_FROM = """
      FROM security_situations x
      LEFT JOIN sites s ON s.id = x.site_id
      LEFT JOIN cameras c ON c.id = x.primary_camera_id
"""


@router.get("/situations")
async def list_situations(
    status: str | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    severity: str | None = Query(None),
    source_type: str | None = Query(None),
    risk_level: str | None = Query(None),
    decision_status: str | None = Query(None),
    open_only: bool = Query(False, alias="open"),
    sort: Literal["recent", "risk"] = Query("recent"),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Security situations. Each is one matter, however many alerts fed it:
    `event_count` says how many, and `duplicate_count` how many of those added
    nothing new.

    `sort=recent` (the default) puts the one heard from most recently first;
    `sort=risk` puts the highest assessed risk first, and those not yet
    assessed last. `source_type` finds situations that include a source of that
    kind; `from`/`to` are about when a situation was last heard from.

    `severity` is the most severe event's own severity. `risk_level` is this
    layer's assessment in context. They are different things.

    `decision_status` is where the situation stands with the people responsible
    for it — `AWAITING` until someone has decided anything — and `open=true`
    leaves out those a person has closed. Neither is the layer's opinion: both
    are set only by a person's decision.

    `reassessed_since_decision` is true when the layer has assessed the
    situation again since the last decision on it — new events arrived, a drone
    looked — and the situation is still open. The decision stands; it is a
    prompt for a person to look again, and nothing more."""
    _one_of(status, SITUATION_STATUSES, "status")
    _one_of(severity, SEVERITIES, "severity")
    _one_of(source_type, SOURCE_TYPES, "source type")
    _one_of(risk_level, RISK_LEVELS, "risk level")
    _one_of(decision_status, intel_decisions.STATUSES, "decision status")
    if since is not None and until is not None and until < since:
        raise HTTPException(422, "The period ends before it starts.")

    where: list[str] = []
    params: dict = {}
    scope = site_scope_clause(allowed, "x.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("x.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    for column, value, name in (("x.status", status, "status"), ("x.severity", severity, "severity"),
                                ("x.risk_level", risk_level, "risk_level"),
                                ("x.decision_status", decision_status, "decision_status")):
        if value is not None:
            where.append(f"{column} = :{name}")
            params[name] = value
    if open_only:
        where.append("x.closed_at IS NULL")
    if source_type is not None:
        where.append("CAST(:source_type AS text) = ANY(x.source_types)")
        params["source_type"] = source_type
    if since is not None:
        where.append("x.last_event_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("x.last_event_at <= :until")
        params["until"] = until
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = ("x.risk_score DESC NULLS LAST, x.last_event_at DESC, x.id DESC" if sort == "risk"
             else "x.last_event_at DESC, x.id DESC")
    return await paginate(
        db,
        f"SELECT {_SITUATION_COLUMNS} {_SITUATION_FROM} {clause} ORDER BY {order} LIMIT :limit OFFSET :offset",
        f"SELECT count(*) FROM security_situations x {clause}",
        params, limit, offset,
    )


@router.get("/situations/{situation_id}")
async def get_situation(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One situation with every event in it, oldest first, and for each event
    why it is here: the method, the reason in words, how sure the link is, and
    whether it added anything new. `sources` is the short list an officer reads
    first — which cameras and which other sources reported. Each event carries
    its `attributes` as its source recorded them: for a virtual patrol's
    exception the question, the officer's answer and what they noted; for a
    drone look a person asked for, how long it held and what more it saw.

    `assessment` is the latest assessment, or null when the situation has not
    been assessed yet: what it appears to be, the risk and every factor behind
    it, how unusual it is for the place and hour, the three confidences kept
    apart, and what was not known.

    `incident.state` says what stands behind any incident: `NONE` — an AI event
    and nothing more; `PRELIMINARY` — the platform opened an incident by itself
    and no person has confirmed it; `CONFIRMED` — a person opened it, or
    confirmed it. An incident software opened is never shown as one a person
    stands behind."""
    row = (await db.execute(
        text(f"SELECT {_SITUATION_COLUMNS} {_SITUATION_FROM} WHERE x.id = CAST(:id AS uuid)"),
        {"id": str(situation_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Situation not found")
    events = (await db.execute(text("""
        SELECT e.id, e.source_type, e.source_table, e.source_id, e.event_type, e.occurred_at, e.severity,
               e.title, e.camera_id, cam.name AS camera_name, e.drone_id, e.alert_id, e.incident_id,
               e.subject_kind, e.subject_ref, e.subject_verdict, e.confidence, e.location_label,
               e.attributes,
               l.method, l.reason, l.confidence AS link_confidence, l.is_duplicate, l.matched_event_id,
               l.linked_at
          FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
          LEFT JOIN cameras cam ON cam.id = e.camera_id
         WHERE l.situation_id = CAST(:id AS uuid)
         ORDER BY e.occurred_at, e.id
    """), {"id": str(situation_id)})).mappings().all()

    sources: dict[tuple, dict] = {}
    for e in events:
        key = (e["source_type"], str(e["camera_id"]) if e["camera_id"] else e["location_label"])
        entry = sources.setdefault(key, {
            "source_type": e["source_type"], "camera_id": e["camera_id"],
            "label": e["camera_name"] or e["location_label"], "events": 0,
            "first_at": e["occurred_at"], "last_at": e["occurred_at"]})
        entry["events"] += 1
        entry["last_at"] = e["occurred_at"]
    latest = (await db.execute(text(
        "SELECT * FROM security_assessments WHERE situation_id = CAST(:id AS uuid) ORDER BY sequence DESC LIMIT 1"),
        {"id": str(situation_id)})).mappings().first()
    found = (await intel_decisions.facts(db, {**dict(row), "assessment_id": None})).incident
    return {**dict(row), "sources": list(sources.values()),
            "events": [{**dict(e), "attributes": _loads(e["attributes"]) or {}} for e in events],
            "assessment": _assessment(latest, with_context=True) if latest is not None else None,
            "incident": {"state": intel_decisions.incident_state(row, found),
                         "id": found["id"] if found else None,
                         "opened_by_the_platform": bool(found["is_auto_created"]) if found else None}}


def _loads(value):
    return json.loads(value) if isinstance(value, str) else value


def _assessment(row, *, with_context: bool) -> dict:
    """An assessment as it is served. The three confidences are three fields,
    never one. With its context: the statements it rested on, each with its
    source, and what was not known."""
    context = _loads(row["context"]) or {}
    out = {
        "id": row["id"], "sequence": row["sequence"], "assessed_at": row["assessed_at"],
        "kind": row["kind"], "label": row["label"], "summary": row["summary"],
        "risk_score": row["risk_score"], "risk_level": row["risk_level"],
        "risk_factors": _loads(row["risk_factors"]),
        "normality_score": row["normality_score"], "anomaly_score": row["anomaly_score"],
        "normality_factors": _loads(row["normality_factors"]),
        "normality_basis": context.get("normality_basis"),
        "confidence": {
            "detection": row["detection_confidence"],
            "correlation": row["correlation_confidence"],
            "risk": row["risk_confidence"],
        },
        "unknowns": context.get("unknowns", []),
        "event_count": row["event_count"], "engine_version": row["engine_version"],
    }
    if with_context:
        out["statements"] = context.get("statements", [])
        out["expected"] = context.get("expected", [])
    return out


@router.get("/situations/{situation_id}/assessments")
async def list_assessments(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every assessment of a situation, oldest first. They are never edited:
    what was believed before the drone arrived stays beside what was believed
    after, each with the factors behind it."""
    site = (await db.execute(text("SELECT site_id FROM security_situations WHERE id = CAST(:id AS uuid)"),
                             {"id": str(situation_id)})).first()
    if site is None or not is_site_allowed(allowed, site.site_id):
        raise HTTPException(404, "Situation not found")
    rows = (await db.execute(text(
        "SELECT * FROM security_assessments WHERE situation_id = CAST(:id AS uuid) ORDER BY sequence"),
        {"id": str(situation_id)})).mappings().all()
    return [_assessment(r, with_context=False) for r in rows]


# ─── The timeline ────────────────────────────────────────────────────────────

@router.get("/situations/{situation_id}/timeline")
async def get_timeline(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One situation, in the order it happened: what the sources reported, what
    the layer made of it and suggested, what people looked at, decided and
    reported, what the platform then did, and how it ended.

    Nothing is stored for the timeline. Each entry is read from the record it
    describes and points back at it (`ref`), and says whose it is (`actor`):
    `SOURCE`, `AI`, `PERSON` or `PLATFORM`. What the layer suggested carries
    `is_decision: false`; what a person decided carries `who`; what was
    carried out names the function it went `through` and how it ended.

    Repeats of one alert are one entry with a `count`. A caller who may not
    read suggestions gets the same timeline without them, and
    `suggestions_shown` says so."""
    row = (await db.execute(text(
        "SELECT id, situation_number, site_id, started_at, closed_at, decision_status, incident_id "
        "  FROM security_situations WHERE id = CAST(:id AS uuid)"), {"id": str(situation_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Situation not found")
    shown = "intel:recommendation:read" in await intel_decisions.permissions_of(db, token.role_id)
    entries = await intel_timeline.load(db, dict(row), with_suggestions=shown)
    return {"situation_id": row["id"], "situation_number": row["situation_number"], "started_at": row["started_at"],
            "closed_at": row["closed_at"], "decision_status": row["decision_status"], "suggestions_shown": shown,
            "counts": intel_timeline.counts(entries), "entries": entries}


# ─── Recommendations ─────────────────────────────────────────────────────────

@router.get("/situations/{situation_id}/recommendations",
            dependencies=[Depends(require_permission("intel:recommendation:read"))])
async def list_recommendations(
    situation_id: uuid.UUID,
    assessment_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the layer suggests an officer do about a situation, surest first,
    with the reason for each and what each rests on. A step that cannot be
    taken right now is listed after those that can, `available` false, with the
    reason in `unavailable_reason`.

    These are suggestions. Nothing has been done, and nothing will be until a
    person decides: `is_decision` is always false here.

    Without `assessment_id` the set for the situation's latest assessment is
    returned and `current` is true. With it, the set that was suggested for
    that earlier assessment — what was suggested then is kept.

    Four confidences, each under its own name: `assessment.confidence` holds
    the detection, correlation and risk confidences, and each suggestion has
    its own `recommendation_confidence` with what held it down in `limited_by`."""
    situation = (await db.execute(text(
        "SELECT site_id, assessment_id FROM security_situations WHERE id = CAST(:id AS uuid)"),
        {"id": str(situation_id)})).first()
    if situation is None or not is_site_allowed(allowed, situation.site_id):
        raise HTTPException(404, "Situation not found")
    target = assessment_id or situation.assessment_id
    out = {"situation_id": situation_id, "is_decision": False, "current": True, "assessment": None,
           "recommendations": []}
    if target is None:
        return out          # not assessed yet, so nothing has been suggested
    a = (await db.execute(text(
        "SELECT id, sequence, assessed_at, kind, label, risk_level, risk_score, detection_confidence, "
        "       correlation_confidence, risk_confidence "
        "  FROM security_assessments WHERE id = CAST(:a AS uuid) AND situation_id = CAST(:s AS uuid)"),
        {"a": str(target), "s": str(situation_id)})).mappings().first()
    if a is None:
        raise HTTPException(404, "Assessment not found")
    rows = (await db.execute(text(
        "SELECT id, rank, action, priority, reason, confidence, confidence_limited_by, available, "
        "       unavailable_reason, supporting, created_at "
        "  FROM security_recommendations WHERE assessment_id = CAST(:a AS uuid) ORDER BY rank"),
        {"a": str(target)})).mappings().all()
    out["current"] = situation.assessment_id is not None and str(target) == str(situation.assessment_id)
    out["assessment"] = {
        "id": a["id"], "sequence": a["sequence"], "assessed_at": a["assessed_at"], "kind": a["kind"],
        "label": a["label"], "risk_level": a["risk_level"], "risk_score": a["risk_score"],
        "confidence": {"detection": a["detection_confidence"], "correlation": a["correlation_confidence"],
                       "risk": a["risk_confidence"]},
    }
    out["recommendations"] = [{
        "id": r["id"], "rank": r["rank"], "action": r["action"], "priority": r["priority"], "reason": r["reason"],
        "recommendation_confidence": r["confidence"], "limited_by": r["confidence_limited_by"],
        "available": r["available"], "unavailable_reason": r["unavailable_reason"],
        "supporting": _loads(r["supporting"]), "created_at": r["created_at"],
    } for r in rows]
    return out


# ─── Camera links ────────────────────────────────────────────────────────────

class CameraLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_a: uuid.UUID
    camera_b: uuid.UUID
    walk_seconds: int = Field(..., ge=1, le=3600)
    note: str | None = Field(None, max_length=255)


class CameraLinksIn(BaseModel):
    """Every link between this site's cameras, replaced as a whole."""

    model_config = ConfigDict(extra="forbid")

    links: list[CameraLinkIn] = Field(..., max_length=500)


async def _links(db: AsyncSession, site_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT l.camera_a, a.name AS camera_a_name, l.camera_b, b.name AS camera_b_name, l.walk_seconds, l.note
          FROM security_camera_links l
          JOIN cameras a ON a.id = l.camera_a
          JOIN cameras b ON b.id = l.camera_b
         WHERE a.site_id = CAST(:s AS uuid)
         ORDER BY a.name, b.name
    """), {"s": str(site_id)})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/site-profiles/{site_id}/camera-links")
async def get_camera_links(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Which of the site's cameras an administrator has said are next to each
    other, and the walk between them in seconds. Cameras with coordinates are
    related by distance without being listed here."""
    await _site(db, site_id, allowed)
    return {"site_id": str(site_id), "links": await _links(db, site_id)}


@router.put("/site-profiles/{site_id}/camera-links", dependencies=[Depends(require_permission("intel:manage"))])
async def put_camera_links(
    site_id: uuid.UUID,
    body: CameraLinksIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
    token: TokenPayload = Depends(get_token_payload),
):
    """Say which cameras are next to each other. Replaces every link between
    this site's cameras. Both cameras of a link must belong to the site."""
    await _site(db, site_id, allowed)
    owned = {str(r) for r in (await db.execute(
        text("SELECT id FROM cameras WHERE site_id = CAST(:s AS uuid)"), {"s": str(site_id)})).scalars().all()}
    pairs: dict[tuple[str, str], CameraLinkIn] = {}
    for link in body.links:
        a, b = sorted((str(link.camera_a), str(link.camera_b)))
        if a == b:
            raise HTTPException(422, "A camera cannot be linked to itself.")
        if a not in owned or b not in owned:
            raise HTTPException(422, "Both cameras of a link must belong to this site.")
        if (a, b) in pairs:
            raise HTTPException(422, "The same pair of cameras is listed twice.")
        pairs[(a, b)] = link
    await db.execute(text("""
        DELETE FROM security_camera_links l USING cameras c
         WHERE c.id = l.camera_a AND c.site_id = CAST(:s AS uuid)
    """), {"s": str(site_id)})
    for (a, b), link in pairs.items():
        await db.execute(text("""
            INSERT INTO security_camera_links (tenant_id, camera_a, camera_b, walk_seconds, note, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:a AS uuid), CAST(:b AS uuid), :walk, :note,
                    CAST(:u AS uuid))
        """), {"a": a, "b": b, "walk": link.walk_seconds, "note": link.note, "u": token.user_id})
    await intel_audit.record(db, request, token, "intel.camera_links.update", "security_camera_links", site_id,
                             site_id=site_id, detail={"links": len(pairs)})
    listed = await _links(db, site_id)
    await db.commit()
    return {"site_id": str(site_id), "links": listed}
