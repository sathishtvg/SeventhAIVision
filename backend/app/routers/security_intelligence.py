"""AI security intelligence: what the platform's sources have reported, in one shape.

The layer described in AI_SECURITY_INTELLIGENCE_GAP_ANALYSIS.md, as far as it is
built: whether it is running for this organisation, the security events it has
read, the context of each, and what an administrator has said about each site.

NOTHING HERE CHANGES AN ALERT, AN INCIDENT OR ANY OTHER EXISTING RECORD. The
events are written by the intelligence runner (app/intelligence_main.py); the
switch that turns it on for a tenant is the ordinary tenant setting
`intel.enabled`, changed through the settings API by someone who may change
settings. The only things written here are the layer's own site and camera
profiles, by someone with `intel:manage`, and each change is audited.

Everything needs `intel:read`. A caller restricted to certain sites sees those
sites' events and profiles; an event with no site is not shown to them — the
same rule as every other site-scoped list.
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
from app.services import intel_audit, intel_config, intel_context, intel_runner
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
