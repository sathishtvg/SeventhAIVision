"""The operations reports: records the platform already keeps, taken out as a file.

Phase 10 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_ANALYTICS_ARCHITECTURE.md.

  GET  /          the reports there are: what each holds, what it is read under, whether the caller may have it
  GET  /{key}     one report as a CSV file, for a period and a site, a customer's sites or every site

A REPORT IS MADE WHEN IT IS ASKED FOR (services/ops_reports.py). Nothing is
stored of it but the line in the audit log that says who took which report,
for where, and how many records.

TWO PERMISSIONS, BOTH NEEDED: `opsreport:export` to take any report out, and
the permission the report's own records are read under.

A REPORT IS TAKEN OUT BY THE ORGANISATION'S OWN PEOPLE. A support session reads
a customer's account to answer a question; it does not carry the customer's
records away.

SOMEBODY HELD TO PARTICULAR SITES IS GIVEN THOSE SITES' RECORDS, and nothing of
what has no site.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import SOURCE_PERMISSIONS, held_by, sites_in
from app.services import intel_audit, intel_insight, ops_reports

#: Every permission a report's records are read under, with the board's own for its sections.
PERMISSIONS = tuple(dict.fromkeys((*SOURCE_PERMISSIONS, *(code for r in ops_reports.REPORTS for code in r.needs))))
NOTE = ("Each report is made now, from the records as they are. Times are where the organisation is. Taking one out "
        "is written in the audit log.")

router = APIRouter(prefix="/api/v1/operations-reports", tags=["operations-reports"])
_EXPORT = [Depends(require_permission("opsreport:export"))]


def _why_not(report: ops_reports.Report, held: frozenset[str]) -> str | None:
    missing = [code for code in report.needs if code not in held]
    return f"Its records are read under {' and '.join(missing)}, which you do not hold." if missing else None


@router.get("", dependencies=_EXPORT)
async def list_reports(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The reports there are: what a row of each is, the columns it has, the
    permission its records are read under, and whether the caller may have it."""
    held = await held_by(db, token.role_id, PERMISSIONS)
    zone = await intel_insight.zone_for(db, None)
    return {"reports": [{"key": r.key, "title": r.title, "holds": r.holds, "needs": list(r.needs),
                         "columns": ops_reports.headings(r, zone), "periodic": r.periodic,
                         "may": ops_reports.may_have(r, held), "why_not": _why_not(r, held)}
                        for r in ops_reports.REPORTS],
            "periods": list(ops_reports.PERIOD_DAYS), "max_rows": ops_reports.MAX_ROWS, "timezone": zone, "note": NOTE}


@router.get("/{key}", dependencies=_EXPORT)
async def take_report(
    key: str,
    request: Request,
    site_id: uuid.UUID | None = Query(None),
    client_id: uuid.UUID | None = Query(None),
    days: int = Query(7),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One report as a CSV file: a line of headings, a line for each record,
    oldest first. For the last 1, 7, 30 or 90 days — a report of how things are
    now has no period — and for one site, a customer's sites or every site the
    caller may see. What was taken is written in the audit log."""
    if token.support_session_id:
        raise HTTPException(403, "A report is taken out by the organisation's own staff, not from a support session.")
    report = ops_reports.BY_KEY.get(key)
    if report is None:
        raise HTTPException(404, "There is no such report")
    if report.periodic and days not in ops_reports.PERIOD_DAYS:
        raise HTTPException(422, "A report is for the last 1, 7, 30 or 90 days.")
    held = await held_by(db, token.role_id, PERMISSIONS)
    refused = _why_not(report, held)
    if refused:
        raise HTTPException(403, refused)
    now = datetime.now(timezone.utc)
    sites, site, client = await sites_in(db, allowed, site_id, client_id)
    every = site is None and client is None and allowed is None
    zone = await intel_insight.zone_for(db, site["id"] if site else None)
    found, cut = await ops_reports.rows(db, report, held, sites, None if every else [s["id"] for s in sites], zone,
                                        now - timedelta(days=days), now, now, site["id"] if site else None)
    content = ops_reports.as_csv(report, found, zone, cut)
    await intel_audit.record(db, request, token, "report.export", "operations_report", None,
                             site_id=site["id"] if site else None,
                             detail={"report": report.key, "rows": len(found), "cut": cut,
                                     "days": days if report.periodic else None,
                                     "client_id": str(client["id"]) if client else None,
                                     "every_site": every})
    await db.commit()
    # A byte-order mark, so that a spreadsheet reads names in any script as they were written.
    return Response(content=content.encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="operations-{report.key}-{now:%Y%m%d}.csv"',
        "X-Report-Rows": str(len(found)), "X-Report-Cut": "true" if cut else "false"})
