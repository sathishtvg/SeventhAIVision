"""The operations board: what each part of the operation counts, for a site, a customer or every site.

Phase 10 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_ANALYTICS_ARCHITECTURE.md.

  GET  /          the board for one scope: every section the caller may read, and what stands out
  GET  /sites     the same figures for each site, and summed for each customer

EVERYTHING HERE IS COUNTED WHEN IT IS ASKED FOR (services/ops_board.py), from
rows the platform already keeps. Nothing is stored and nothing is written.

EACH SECTION IS READ UNDER ITS OWN PERMISSION, on top of `board:read`. What the
caller may not read is left out and named.

SOMEBODY HELD TO PARTICULAR SITES SEES THOSE SITES. What has no site — an
incident with no camera, a recorder — is counted only for somebody who is not.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import daily_briefing, intel_insight, ops_board, risk_patterns

#: Every permission a section or a part of one is read under.
SOURCE_PERMISSIONS = (*ops_board.NEEDS.values(), *ops_board.PART_NEEDS.values(), "advice:read")

router = APIRouter(prefix="/api/v1/operations-board", tags=["operations-board"])
_READ = [Depends(require_permission("board:read"))]


async def held_by(db: AsyncSession, role_id: int, codes=SOURCE_PERMISSIONS) -> frozenset[str]:
    """Which of these permissions the caller's role holds."""
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": list(codes)})
    return frozenset(r.code for r in rows)


async def sites_in(db: AsyncSession, allowed, site_id: uuid.UUID | None = None,
                   client_id: uuid.UUID | None = None) -> tuple[list[dict], dict | None, dict | None]:
    """(the sites of the scope the caller may see, the one site asked for, the
    customer asked for). Each site with its customer."""
    client = None
    if client_id is not None:
        client = (await db.execute(text("SELECT id, name FROM billing_clients WHERE id = CAST(:c AS uuid)"),
                                   {"c": str(client_id)})).mappings().first()
        if client is None:
            raise HTTPException(404, "Customer not found")
        client = dict(client)
    rows = (await db.execute(text("""
        SELECT s.id, s.name, s.is_active, s.client_id, b.name AS client_name
          FROM sites s LEFT JOIN billing_clients b ON b.id = s.client_id ORDER BY s.name, s.id
    """))).mappings().all()
    sites = [dict(r) for r in rows if is_site_allowed(allowed, r["id"])
             and (client is None or r["client_id"] == client["id"])]
    site = None
    if site_id is not None:
        site = next((s for s in sites if s["id"] == site_id), None)
        if site is None:
            raise HTTPException(404, "Site not found")
        sites = [site]
    return sites, site, client


def _period(days: int, now: datetime) -> tuple[datetime, dict]:
    if days not in ops_board.PERIOD_DAYS:
        raise HTTPException(422, "The board is read for the last 1, 7 or 30 days.")
    start = now - timedelta(days=days)
    return start, {"days": days, "from": start, "to": now}


def _sections(figures: dict) -> list[dict]:
    return [{"key": key, "title": ops_board.TITLE[key], "counted_from": ops_board.COUNTED_FROM[key],
             "figures": figures[key]} for key in ops_board.SECTIONS if key in figures]


def _site(s: dict) -> dict:
    return {"id": s["id"], "name": s["name"], "is_active": s["is_active"],
            "client": {"id": s["client_id"], "name": s["client_name"]} if s["client_id"] else None}


@router.get("", dependencies=_READ)
async def read_board(
    site_id: uuid.UUID | None = Query(None),
    client_id: uuid.UUID | None = Query(None),
    days: int = Query(1),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The board for one site, one customer's sites, or every site the caller
    may see, over the last 1, 7 or 30 days: each section the caller may read
    with what it is counted from, what was left out and why, and how much
    advice stands for the same sites."""
    now = datetime.now(timezone.utc)
    start, period = _period(days, now)
    sites, site, client = await sites_in(db, allowed, site_id, client_id)
    held = await held_by(db, token.role_id)
    every = site is None and client is None and allowed is None
    site_ids = None if every else [s["id"] for s in sites]
    board = await ops_board.read(db, held, site_ids, start, now, now)
    advice = None
    if "advice:read" in held:
        zone = await intel_insight.zone_for(db, site["id"] if site else None)
        weeks = daily_briefing.ADVICE_WEEKS
        found = (await risk_patterns.read(db, zone, risk_patterns.period(now, weeks), now, weeks, site_ids,
                                          str(site["id"]) if site else "ALL"))["findings"]
        advice = {"weeks": weeks, "standing": len(found),
                  "by_level": {level: sum(1 for f in found if f["confidence"]["level"] == level)
                               for level in risk_patterns.LEVELS},
                  "note": risk_patterns.NOTE}
    return {"period": period, "as_at": now,
            "scope": {"site": _site(site) if site else None, "client": client, "sites": len(sites), "every_site": every},
            "sections": _sections(board["total"]), "not_read": board["not_read"],
            "clocks_on_since": board["clocks_on_since"], "advice": advice, "note": ops_board.NOTE}


@router.get("/sites", dependencies=_READ)
async def read_sites(
    client_id: uuid.UUID | None = Query(None),
    days: int = Query(1),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The board's figures for each site the caller may see — every site that
    is in use, and any other with something counted — and summed for each
    customer. A customer's times are not summed: a middle time is not a sum."""
    now = datetime.now(timezone.utc)
    start, period = _period(days, now)
    sites, _, client = await sites_in(db, allowed, None, client_id)
    held = await held_by(db, token.role_id)
    every = client is None and allowed is None
    board = await ops_board.read(db, held, None if every else [s["id"] for s in sites], start, now, now)
    counted = board["sites"]
    empty = {key: ops_board.blank(key, held) for key in board["total"]}
    rows = [{**_site(s), "figures": counted.get(str(s["id"]), empty)} for s in sites
            if s["is_active"] or str(s["id"]) in counted]
    clients: dict[Any, dict] = {}
    for row in rows:
        c = row["client"]
        if c is None:
            continue
        into = clients.setdefault(c["id"], {"id": c["id"], "name": c["name"], "sites": 0, "figures": None})
        into["sites"] += 1
        into["figures"] = row["figures"] if into["figures"] is None else ops_board.add(into["figures"], row["figures"])
    return {"period": period, "as_at": now, "client": client,
            "sections": [{"key": key, "title": ops_board.TITLE[key]} for key in ops_board.SECTIONS if key in board["total"]],
            "sites": rows, "no_site": counted.get(None) if every else None,
            "clients": sorted(clients.values(), key=lambda c: c["name"]),
            "total": board["total"], "not_read": board["not_read"], "clocks_on_since": board["clocks_on_since"],
            "note": ops_board.NOTE}
