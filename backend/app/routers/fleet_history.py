"""Fleet journey history and its retention — /api/v1/fleet

Phase 4 of the GPS fleet upgrade (docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 4).

    GET /vehicles/{id}/journeys       journeys, stops and the route over a range of up to 7 days
    GET /vehicles/{id}/journeys.csv   the same, to open in a spreadsheet; audited

(`/vehicles/{id}/history` is the vehicle's history of CHANGES, in routers/fleet.py.)
    GET /retention                    how long positions and archived vehicles are kept
    PUT /retention/{kind}             set or take away one of those periods

Journeys and stops are worked out from the stored positions when they are asked
for (app/services/fleet_journeys.py), and only from two positions or more. A
vehicle somebody may not see is answered 404, as one that does not exist.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import fleet_journeys as fj
from app.services import fleet_retention as fr
from app.services import fleet_vehicles as fv
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])

_READ = [Depends(require_permission("fleet:history:read"))]
_EXPORT = [Depends(require_permission("fleet:history:export"))]
_PERIODS = [Depends(require_permission("gps:read"))]
_SET = [Depends(require_permission("settings:write"))]
MOST_POSITIONS = 200_000


def _as_utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _history(db: AsyncSession, vehicle_id: uuid.UUID, start: datetime, end: datetime,
                   allowed: list[str] | None) -> tuple[dict, fj.History]:
    start, end = _as_utc(start), _as_utc(end)
    try:
        fj.check_range(start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    v = await fv.visible_vehicle(db, str(vehicle_id), allowed)
    rows = (await db.execute(text("""
        SELECT recorded_at, lat, lon, speed, accuracy, ignition FROM vehicle_positions
         WHERE vehicle_id = CAST(:v AS uuid) AND recorded_at >= :start AND recorded_at < :end
         ORDER BY recorded_at LIMIT :most
    """), {"v": str(vehicle_id), "start": start, "end": end, "most": MOST_POSITIONS})).all()
    fixes = [fj.Fix(r.recorded_at, float(r.lat), float(r.lon),
                    float(r.speed) if r.speed is not None else None,
                    float(r.accuracy) if r.accuracy is not None else None, r.ignition) for r in rows]
    return v, fj.work_out(fixes)


@router.get("/vehicles/{vehicle_id}/journeys", dependencies=_READ)
async def vehicle_history(
    vehicle_id: uuid.UUID,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The vehicle's journeys and stops over the range, the route to draw (thinned), and what was left out."""
    v, h = await _history(db, vehicle_id, start, end, allowed)
    drawn = fj.thin(h.kept)
    return {
        "vehicle": {"id": str(v["id"]), "name": v["name"], "plate_number": v["plate_number"]},
        "start": _as_utc(start), "end": _as_utc(end), "enough": h.enough,
        "positions": len(h.kept), "left_out": h.left_out,
        "route": [{"at": f.at, "lat": f.lat, "lon": f.lon, "speed": f.speed} for f in drawn],
        "route_thinned": len(drawn) < len(h.kept),
        "trips": [{"start": t.start, "end": t.end, "minutes": round(t.minutes, 1), "distance_km": round(t.distance_m / 1000, 2),
                   "max_speed": t.max_speed, "avg_kmh": t.avg_kmh,
                   "from": {"lat": t.fixes[0].lat, "lon": t.fixes[0].lon},
                   "to": {"lat": t.fixes[-1].lat, "lon": t.fixes[-1].lon}} for t in h.trips],
        "stops": [{"start": s.start, "end": s.end, "minutes": round(s.minutes, 1), "lat": s.lat, "lon": s.lon,
                   "ignition_off": s.ignition_off} for s in h.stops],
        "gaps": [{"start": g.start, "end": g.end} for g in h.gaps],
        "summary": {"distance_km": round(h.distance_m / 1000, 2),
                    "driving_minutes": round(sum(t.minutes for t in h.trips), 1),
                    "stopped_minutes": round(sum(s.minutes for s in h.stops), 1)},
    }


@router.get("/vehicles/{vehicle_id}/journeys.csv", dependencies=_EXPORT)
async def export_history(
    vehicle_id: uuid.UUID,
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    kind: str = Query("trips", pattern="^(trips|stops|positions)$"),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The trips, stops or kept positions as a CSV file. Every export is a line in the audit log."""
    v, h = await _history(db, vehicle_id, start, end, allowed)
    out = io.StringIO()
    w = csv.writer(out)
    if kind == "trips":
        w.writerow(["start (UTC)", "end (UTC)", "minutes", "distance km", "max speed km/h", "average km/h",
                    "from lat", "from lon", "to lat", "to lon"])
        for t in h.trips:
            w.writerow([t.start.isoformat(), t.end.isoformat(), round(t.minutes, 1), round(t.distance_m / 1000, 3),
                        t.max_speed, t.avg_kmh, t.fixes[0].lat, t.fixes[0].lon, t.fixes[-1].lat, t.fixes[-1].lon])
    elif kind == "stops":
        w.writerow(["start (UTC)", "end (UTC)", "minutes", "lat", "lon", "ignition off"])
        for s in h.stops:
            w.writerow([s.start.isoformat(), s.end.isoformat(), round(s.minutes, 1), s.lat, s.lon, s.ignition_off])
    else:
        w.writerow(["fix time (UTC)", "lat", "lon", "speed km/h", "accuracy m", "ignition"])
        for f in h.kept:
            w.writerow([f.at.isoformat(), f.lat, f.lon, f.speed, f.accuracy, f.ignition])
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="fleet.history.exported",
                          resource_type="vehicle", resource_id=str(vehicle_id), ip_address=_client_ip(request),
                          detail={"kind": kind, "start": _as_utc(start).isoformat(), "end": _as_utc(end).isoformat(),
                                  "rows": {"trips": len(h.trips), "stops": len(h.stops), "positions": len(h.kept)}[kind]})
    await db.commit()
    name = f"{(v['plate_number'] or v['name']).replace(' ', '')}-{kind}-{_as_utc(start):%Y%m%d}.csv"
    # A leading byte-order mark so that a spreadsheet opens the file as UTF-8.
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ── how long positions and archived vehicles are kept ─────────────────────────

@router.get("/retention", dependencies=_PERIODS)
async def read_retention(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    held = await held_by(db, token.role_id, ("settings:write",))
    return {"kinds": fr.statement(await fr.periods(db)), "least_days": fr.LEAST_DAYS, "most_days": fr.MOST_DAYS,
            "may_set": ("settings:write" in held and allowed is None and not token.via_api_key
                        and not token.support_session_id)}


class PeriodBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: StrictInt | None = Field(..., ge=fr.LEAST_DAYS, le=fr.MOST_DAYS)
    #: How many are already older than the period, as the server last said. A period that would remove more is not set.
    already_older: int | None = Field(None, ge=0)


@router.put("/retention/{kind}", dependencies=_SET)
async def set_retention(
    kind: str,
    body: PeriodBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set, change or take away a period. Nothing is removed by this; the scheduler removes, once a day. A period
    that already has records older than it is asked for twice: the first answer (409) says how many."""
    if token.via_api_key or token.support_session_id or allowed is not None:
        raise HTTPException(403, "A retention period is for every site's records, so it is set by somebody of the "
                                 "organisation's own staff, signed in, who is not held to particular sites.")
    chosen = fr.BY_KEY.get(kind)
    if chosen is None:
        raise HTTPException(404, "No such kind of record. One of: " + ", ".join(fr.BY_KEY) + ".")
    before = (await fr.periods(db))[chosen.key]["days"]
    older = await fr.waiting(db, chosen, body.days) if body.days is not None else 0
    if body.days is not None and older and body.already_older != older:
        raise HTTPException(409, {
            "message": (f"{older} {'is' if older == 1 else 'are'} already older than {body.days} days and will be "
                        "removed for good when the scheduler next runs. Confirm to set the period."),
            "already_older": older, "days": body.days})
    if body.days is None:
        await db.execute(text("DELETE FROM tenant_settings WHERE setting_key = :k"), {"k": chosen.setting_key})
    else:
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :k, CAST(:v AS jsonb), CAST(:u AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"k": chosen.setting_key, "v": str(body.days), "u": str(token.user_id)})
    await write_audit_log(db, tenant_id=str(token.tenant_id), user_id=str(token.user_id), action="retention.period.set",
                          resource_type="fleet_retention", ip_address=_client_ip(request),
                          detail={"kind": chosen.key, "from_days": before, "to_days": body.days, "already_older": older})
    after = (await fr.periods(db))[chosen.key]
    await db.commit()
    return {"kind": chosen.key, "label": chosen.label, "days": after["days"], "set_at": after["set_at"],
            "already_older": older, "removed_by": fr.REMOVED_BY}
