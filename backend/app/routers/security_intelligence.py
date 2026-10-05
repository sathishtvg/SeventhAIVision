"""AI security intelligence: what the platform's sources have reported, in one shape.

Phase 2 of the layer described in AI_SECURITY_INTELLIGENCE_GAP_ANALYSIS.md. So
far it answers two questions: is the layer running for this organisation, and
what security events has it read.

READ-ONLY. Nothing here changes an alert, an incident or anything else. The
events are written by the intelligence runner (app/intelligence_main.py); the
switch that turns it on for a tenant is the ordinary tenant setting
`intel.enabled`, changed through the settings API by someone who may change
settings.

Needs `intel:read`. A caller restricted to certain sites sees those sites'
events, and an event with no site is not shown to them — the same rule as every
other site-scoped list.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_config, intel_runner
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
