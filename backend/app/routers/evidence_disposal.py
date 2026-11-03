"""Evidence packages due for removal, removed by a person, and the record of each that is kept.

Phase 2 of closing the feature reference's known limits (3 November 2026; docs/plans/2026-11-03-retention-periods-
and-evidence-disposal-design.md). The owner decided that a package is never removed by a job: its period, set on
the Data Retention screen (services/record_retention.py, EVIDENCE_PACKAGES), makes it due, and a person removes it.

  GET  /api/v1/evidence-disposals/due             the packages due for removal          evidence:package:dispose
  POST /api/v1/evidence-disposals                 remove one, with a reason              evidence:package:dispose
  GET  /api/v1/evidence-disposals                 the records of those removed           evidence:package:read
  GET  /api/v1/evidence-disposals/{id}/custody    a removed package's chain of custody   evidence:custody:read

DUE means past the period, nothing in it on hold, the investigation it was made from closed, the incident it was
made for resolved, and no case that is not closed linking it. One definition, in the database
(`evidence_package_due`, migration 0176), read here and again by the step that removes it.

THE APPLICATION ONLY ASKS. Removing inserts a row into `evidence_package_disposals`; the database checks the package
is due, fills the record from the package itself, links its chain of custody to the record, writes DISPOSED and
removes the package. The application's role cannot change or delete a package. What is checked here first is for a
clear answer, not for safety.

A PERSON, AND A REASON, as everywhere evidence is handled: no API key, no support session, and the package's number
said back so the one meant is the one removed. The pictures and video it pointed to are not touched.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.evidence_packages import _a_person
from app.services import intel_audit, record_retention

KIND = record_retention.BY_KEY["EVIDENCE_PACKAGES"]
NO_PERIOD = ("No period is set for evidence packages, so none is due. A period is set on the Data Retention "
             "screen.")
NOTE = ("Removing a package removes it and its list of items for good. A record of the removal and the package's "
        "chain of custody are kept. The pictures and video it pointed to are not touched: they follow their own "
        "periods.")

router = APIRouter(prefix="/api/v1/evidence-disposals", tags=["evidence-packages"], dependencies=[Depends(_a_person)])
_DISPOSE = [Depends(require_permission("evidence:package:dispose"))]
_READ = [Depends(require_permission("evidence:package:read"))]
_CUSTODY = [Depends(require_permission("evidence:custody:read"))]


async def _period(db: AsyncSession) -> int | None:
    return (await record_retention.periods(db))[KIND.key]["days"]


_DUE = """
    SELECT p.id, p.package_number, p.title, p.status, p.site_id, s.name AS site_name, p.sealed_at, p.updated_at,
           p.investigation_id, v.investigation_number, p.incident_id,
           (SELECT count(*) FROM evidence_package_items i WHERE i.package_id = p.id) AS items,
           COALESCE(p.sealed_at, p.updated_at) AS counted_from
      FROM evidence_packages p
      LEFT JOIN sites s ON s.id = p.site_id
      LEFT JOIN investigations v ON v.id = p.investigation_id
     WHERE evidence_package_due(p.id, :cutoff)
"""


@router.get("/due", dependencies=_DISPOSE)
async def due_for_removal(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The packages that are due for removal, longest due first, at the sites the caller is assigned to."""
    days = await _period(db)
    if days is None:
        return {"period_days": None, "packages": [], "note": NO_PERIOD}
    params: dict = {"cutoff": datetime.now(timezone.utc) - timedelta(days=days)}
    scope = site_scope_clause(allowed, "p.site_id", params)
    rows = (await db.execute(text(_DUE + (f" AND {scope}" if scope else "") +
                                  " ORDER BY counted_from, p.package_number LIMIT 200"), params)).mappings().all()
    return {"period_days": days, "note": NOTE, "packages": [{
        **{k: r[k] for k in ("id", "package_number", "title", "status", "site_id", "site_name", "sealed_at",
                              "updated_at", "investigation_id", "investigation_number", "incident_id", "items")},
        "due_since": r["counted_from"] + timedelta(days=days),
    } for r in rows]}


class DisposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: uuid.UUID
    #: The package's number, said back: the one meant is the one removed.
    package_number: str = Field(..., min_length=1, max_length=24)
    #: Why it is removed. Kept with the record for good.
    reason: str = Field(..., min_length=5, max_length=2000)


@router.post("", status_code=201, dependencies=_DISPOSE)
async def remove_package(
    body: DisposeBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Remove one evidence package that is due, with a reason. Its record and its chain of custody are kept."""
    package = (await db.execute(text(
        "SELECT id, package_number, site_id FROM evidence_packages WHERE id = :p FOR UPDATE"),
        {"p": body.package_id})).mappings().first()
    if package is None or not is_site_allowed(allowed, package["site_id"]):
        raise HTTPException(404, "Evidence package not found")
    if body.package_number.strip().upper() != package["package_number"].upper():
        raise HTTPException(409, f"That is not this package's number. It is {package['package_number']}.")
    days = await _period(db)
    if days is None:
        raise HTTPException(409, NO_PERIOD)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    if not (await db.execute(text("SELECT evidence_package_due(:p, :cutoff)"),
                             {"p": body.package_id, "cutoff": cutoff})).scalar():
        raise HTTPException(409, f"{package['package_number']} is not due for removal: it is within the period of "
                                 f"{days} days, something in it is on hold, or what it was made for is not over.")
    reason = body.reason.strip()
    try:
        record = (await db.execute(text("""
            INSERT INTO evidence_package_disposals
                   (tenant_id, package_id, reason, removed_by_user_id, ip_address, request_id)
            VALUES (current_setting('app.current_tenant')::uuid, :p, :reason, CAST(:who AS uuid), :ip, :request)
            RETURNING id, package_number, title, was_sealed, sealed_at, manifest_sha256, items, period_days,
                      removed_at
        """), {"p": body.package_id, "reason": reason, "who": token.user_id, "ip": _client_ip(request),
               "request": getattr(request.state, "request_id", None)})).mappings().one()
    except DBAPIError as e:
        await db.rollback()
        raise HTTPException(409, f"{package['package_number']} was not removed: the database found it is not due "
                                 "for removal.") from e
    await intel_audit.record(db, request, token, "evidence.package.dispose", "evidence_package", body.package_id,
                             site_id=package["site_id"],
                             detail={"number": record["package_number"], "items": record["items"],
                                     "period_days": record["period_days"],
                                     "manifest_sha256": record["manifest_sha256"], "disposal_id": str(record["id"])})
    await db.commit()
    return {**dict(record), "note": NOTE}


_RECORD = """
    d.id, d.package_id, d.package_number, d.title, d.site_id, s.name AS site_name, d.was_sealed, d.sealed_at,
    d.manifest_sha256, d.items, d.investigation_id, d.investigation_number, d.incident_id, d.period_days, d.reason,
    d.removed_by_user_id, u.full_name AS removed_by_name, d.removed_at
"""
_RECORD_FROM = """
      FROM evidence_package_disposals d
      LEFT JOIN sites s ON s.id = d.site_id
      LEFT JOIN users u ON u.id = d.removed_by_user_id
"""


@router.get("", dependencies=_READ)
async def list_records(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The records of the packages removed, most recent first."""
    params: dict = {}
    scope = site_scope_clause(allowed, "d.site_id", params)
    clause = f"WHERE {scope}" if scope else ""
    return await paginate(
        db, f"SELECT {_RECORD} {_RECORD_FROM} {clause} ORDER BY d.removed_at DESC, d.id LIMIT :limit OFFSET :offset",
        f"SELECT count(*) FROM evidence_package_disposals d {clause}", params, limit, offset)


@router.get("/{disposal_id:uuid}/custody", dependencies=_CUSTODY)
async def removed_package_custody(
    disposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A removed package's chain of custody, oldest first, ending with its removal."""
    record = (await db.execute(text(f"SELECT {_RECORD} {_RECORD_FROM} WHERE d.id = :d"),
                               {"d": disposal_id})).mappings().first()
    if record is None or not is_site_allowed(allowed, record["site_id"]):
        raise HTTPException(404, "No record of a removed evidence package")
    chain = []
    for r in (await db.execute(text("""
        SELECT e.step, e.occurred_at, e.kind, e.ref_id, u.full_name AS actor_name, e.actor_role, e.reason, e.detail
          FROM evidence_custody_events e LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.disposal_id = :d ORDER BY e.occurred_at, e.id
    """), {"d": disposal_id})).mappings():
        detail = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
        chain.append({"step": r["step"], "at": r["occurred_at"], "kind": r["kind"], "ref_id": r["ref_id"],
                      "actor_name": r["actor_name"], "actor_role": r["actor_role"], "reason": r["reason"],
                      "detail": detail})
    return {"record": dict(record), "chain": chain}
