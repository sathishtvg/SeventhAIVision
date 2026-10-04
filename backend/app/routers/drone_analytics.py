"""Drone patrol analytics: patrol statistics, the risk map and recommendations.

Read-only, and computed on request from recorded events and flights — see
services/drone_analytics.py for what each figure means. Needs
`drone:report:read`, like the reports these numbers summarise, and is never
licence-gated: history stays readable.

A caller restricted to certain sites gets those sites' figures, and 404 for a
site or mission outside them.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_analytics as analytics
from app.services.drone_access import assert_site_visible, site_or_404

router = APIRouter(prefix="/api/v1/drone-analytics", tags=["drone-analytics"],
                   dependencies=[Depends(require_permission("drone:report:read"))])


async def _scope(db: AsyncSession, allowed, start: date, end: date, site_id: uuid.UUID | None,
                 mission_id: uuid.UUID | None = None) -> str:
    """Validate the period and the scope; say in words what the figures cover."""
    if end < start:
        raise HTTPException(422, "The period ends before it starts.")
    if (end - start).days + 1 > analytics.MAX_DAYS:
        raise HTTPException(422, f"An analysis covers at most {analytics.MAX_DAYS} days.")
    if mission_id is not None:
        mission = (await db.execute(text("SELECT name, site_id FROM drone_missions WHERE id = CAST(:id AS uuid)"),
                                    {"id": str(mission_id)})).mappings().first()
        if mission is None:
            raise HTTPException(404, "Mission not found")
        assert_site_visible(allowed, mission["site_id"], "Mission")
        return f"mission {mission['name']}"
    if site_id is not None:
        return (await site_or_404(db, site_id, allowed))["name"]
    return "all sites" if allowed is None else "your sites"


@router.get("/overview")
async def overview(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
    site_id: uuid.UUID | None = Query(None), mission_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Patrol statistics for a period of whole days in the organisation's zone:
    flights and mission success, events and their trend by day, suspicious
    events by hour and weekday, detection types, each mission and drone, the
    false-positive rate and the incident conversion rate."""
    scope = await _scope(db, allowed, start, end, site_id, mission_id)
    data = await analytics.overview(db, start=start, end=end, site_id=site_id, mission_id=mission_id,
                                    allowed_site_ids=allowed)
    return {"scope": scope, **data}


@router.get("/risk-map")
async def risk_map(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Areas ranked by an analytical score from recorded events, the spots where
    events cluster, and the places intrusion keeps being seen. The score is
    labelled as what it is: not a prediction."""
    scope = await _scope(db, allowed, start, end, site_id)
    data = await analytics.risk_map(db, start=start, end=end, site_id=site_id, allowed_site_ids=allowed)
    return {"scope": scope, **data}


@router.get("/recommendations")
async def recommendations(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """System-generated suggestions, each with the observation and the numbers
    it rests on, and the rule thresholds that produced it."""
    scope = await _scope(db, allowed, start, end, site_id)
    data = await analytics.recommendations(db, start=start, end=end, site_id=site_id, allowed_site_ids=allowed)
    return {"scope": scope, **data}
