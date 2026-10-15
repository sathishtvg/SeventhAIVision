"""Evidence around a vehicle's geofence entry, exit or stay: never identity — /api/v1/fleet

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 6; what is looked for is in
app/services/fleet_evidence.py.

    GET  /geofence-events/{id}/evidence   plate reads, barrier commands, door events and incidents at the site
                                          around it: "plate matches" or "near in time only", never confirmed
    GET|PUT /evidence-settings            how far either side to look (fleet:alerts:manage)

Each section is shown only to somebody holding that module's own read permission, and only for a site they may
see. A plate that matches is a plate that matches: not the vehicle, and not who was driving.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import fleet_evidence as fe
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_READ = [Depends(require_permission("gps:read"))]
_ALERTS = [Depends(require_permission("fleet:alerts:manage"))]


@router.get("/geofence-events/{event_id}/evidence", dependencies=_READ)
async def event_evidence(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What else the site recorded around this entry, exit or stay. Somebody limited to sites sees it only for an
    event of their sites' vehicles at a geofence of their sites."""
    params: dict = {"e": str(event_id)}
    clauses = ["e.id = CAST(:e AS uuid)"]
    for column in ("v.site_id", "g.site_id"):
        scope = site_scope_clause(allowed, column, params)
        if scope:
            clauses.append(f"({scope}{' OR g.site_id IS NULL' if column == 'g.site_id' else ''})")
    row = (await db.execute(text(f"""
        SELECT e.id, e.event_type, e.occurred_at, e.between_start, e.between_end, v.name AS vehicle_name,
               v.plate_number, g.name AS geofence_name, g.site_id, s.name AS site_name
          FROM geofence_events e
          JOIN vehicles v ON v.id = e.vehicle_id
          JOIN geofences g ON g.id = e.geofence_id
          LEFT JOIN sites s ON s.id = g.site_id
         WHERE {' AND '.join(clauses)}
    """), params)).mappings().first()
    if row is None:
        raise HTTPException(404, "Event not found")
    held = await held_by(db, token.role_id, fe.PERMISSIONS)
    found = await fe.around(db, row, held, await fe.window_of(db))
    return {"event": {k: (str(v) if k in ("id", "site_id") and v is not None else v) for k, v in row.items()}, **found}


class EvidenceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_s: int = Field(..., ge=fe.LEAST_WINDOW_S, le=fe.MOST_WINDOW_S)


@router.get("/evidence-settings", dependencies=_READ)
async def read_evidence_settings(db: AsyncSession = Depends(get_db_with_tenant)):
    return {"window_s": await fe.window_of(db), "least_s": fe.LEAST_WINDOW_S, "most_s": fe.MOST_WINDOW_S}


@router.put("/evidence-settings", dependencies=_ALERTS)
async def set_evidence_settings(
    body: EvidenceSettingsIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await db.execute(text("""
        INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
        VALUES (:t, :k, CAST(:v AS jsonb), CAST(:u AS uuid))
        ON CONFLICT (tenant_id, setting_key)
        DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                      updated_at = now()
    """), {"t": str(token.tenant_id), "k": fe.WINDOW_KEY, "v": json.dumps(body.window_s), "u": str(token.user_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id),
                          action="fleet.evidence_settings.changed", resource_type="tenant_settings",
                          ip_address=_client_ip(request), detail={"window_s": body.window_s})
    await db.commit()
    return {"window_s": body.window_s, "least_s": fe.LEAST_WINDOW_S, "most_s": fe.MOST_WINDOW_S}
