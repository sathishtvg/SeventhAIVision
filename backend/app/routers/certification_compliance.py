"""Defining what a site requires, and seeing who is rostered without it.

SEPARATE ROUTER FROM training.py ON PURPOSE. That one is about what a guard
has done — courses, attempts, records, certificates. This one is about what the
operation demands and where the roster falls short of it. Same nouns, opposite
direction, and different people use them: training is HR's, this is operations'.

NOTHING HERE BLOCKS ANYTHING. Requirements are reported against, never enforced
at rostering — an ops manager covering a 2am no-show must not be stopped by the
roster tool, or they stop using the roster tool and the record disappears with
them. The findings come from the hourly sweep, which is the only thing that sees
a shift regardless of which of the nine create/reassign paths produced it.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import certification_compliance as cc

router = APIRouter(prefix="/api/v1/certification-compliance",
                   tags=["certification-compliance"])

_READ = Depends(require_permission("training:read"))
_ENFORCE = Depends(require_permission("certification:enforce"))


class RequirementIn(BaseModel):
    certification_type: str = Field(min_length=1, max_length=100)
    #: Omitted or null means the tenant baseline: everywhere, every site.
    site_id: str | None = None
    warn_days_before: int = Field(default=30, ge=0, le=365)
    notes: str | None = None


# ── Requirements ─────────────────────────────────────────────────────────────

@router.get("/requirements", dependencies=[_READ])
async def list_requirements(site_id: str | None = Query(None),
                            db: AsyncSession = Depends(get_db_with_tenant)):
    """Baseline first, then per-site — the order they apply in."""
    rows = (await db.execute(text("""
        SELECT r.id, r.site_id, s.name AS site_name, r.certification_type,
               r.warn_days_before, r.is_active, r.notes, r.created_at
          FROM certification_requirements r
          LEFT JOIN sites s ON s.id = r.site_id
         WHERE (CAST(:site AS uuid) IS NULL
                OR r.site_id = CAST(:site AS uuid) OR r.site_id IS NULL)
         ORDER BY r.site_id NULLS FIRST, r.certification_type
    """), {"site": site_id})).mappings().all()
    return [dict(r) for r in rows]


@router.post("/requirements", status_code=201, dependencies=[_ENFORCE])
async def add_requirement(body: RequirementIn,
                          db: AsyncSession = Depends(get_db_with_tenant)):
    try:
        row = (await db.execute(text("""
            INSERT INTO certification_requirements
                (tenant_id, site_id, certification_type, warn_days_before, notes)
            VALUES ((current_setting('app.current_tenant', true))::uuid,
                    CAST(:site AS uuid), :ct, :warn, :notes)
            RETURNING id, site_id, certification_type, warn_days_before, is_active
        """), {"site": body.site_id, "ct": body.certification_type.strip(),
               "warn": body.warn_days_before, "notes": body.notes})).mappings().first()
    except Exception as exc:
        if "uq_certreq" in str(exc) or "duplicate key" in str(exc).lower():
            await db.rollback()
            where = "this site" if body.site_id else "the whole tenant"
            raise HTTPException(
                409, f"{body.certification_type!r} is already required for {where}.")
        raise
    await db.commit()
    return dict(row)


@router.delete("/requirements/{requirement_id}", dependencies=[_ENFORCE])
async def remove_requirement(requirement_id: str,
                             db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text(
        "DELETE FROM certification_requirements WHERE id = CAST(:id AS uuid) "
        "RETURNING id"), {"id": requirement_id})).first()
    if row is None:
        raise HTTPException(404, "No such requirement")
    await db.commit()
    return {"deleted": requirement_id}


# ── What the roster looks like against them ──────────────────────────────────

@router.get("/findings", dependencies=[_READ])
async def list_findings(
    site_id: str | None = Query(None),
    days: int = Query(45, ge=1, le=365),
    include_resolved: bool = Query(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Upcoming shifts whose guard does not hold what the site requires.

    Site-scoped: a supervisor restricted to two sites must not read the
    certification gaps of a third — that is a list of named people and what they
    lack, which is exactly the sort of detail a site restriction exists to
    contain.
    """
    params: dict = {"site": site_id, "a": date.today(),
                    "b": date.today() + timedelta(days=days)}
    clauses = [
        "(CAST(:site AS uuid) IS NULL OR f.site_id = CAST(:site AS uuid))",
        "f.shift_date BETWEEN :a AND :b",
    ]
    if not include_resolved:
        clauses.append("f.resolved_at IS NULL")

    scope = site_scope_clause(allowed_sites, "f.site_id", params)
    if scope:
        clauses.append(scope)

    rows = (await db.execute(text(f"""
        SELECT f.id, f.shift_id, f.certification_type, f.status, f.shift_date,
               f.detected_at, f.resolved_at,
               u.full_name AS guard_name, u.email AS guard_email,
               s.name AS site_name, sh.scheduled_start
          FROM shift_certification_findings f
          JOIN users u ON u.id = f.guard_user_id
          LEFT JOIN sites s ON s.id = f.site_id
          LEFT JOIN shifts sh ON sh.id = f.shift_id
         WHERE {' AND '.join(clauses)}
         ORDER BY f.shift_date, u.full_name
         LIMIT 500
    """), params)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/summary", dependencies=[_READ])
async def summary(days: int = Query(45, ge=1, le=365),
                  db: AsyncSession = Depends(get_db_with_tenant),
                  allowed_sites: list[str] | None = Depends(get_allowed_site_ids)):
    """Counts by status, and whether anything is configured at all.

    `requirements` is returned deliberately: zero requirements with zero
    findings is not a clean bill of health, it is a system nobody has told what
    to check, and a dashboard that cannot tell those apart is worse than none.
    """
    params: dict = {"a": date.today(), "b": date.today() + timedelta(days=days)}
    clauses = ["f.resolved_at IS NULL", "f.shift_date BETWEEN :a AND :b"]
    scope = site_scope_clause(allowed_sites, "f.site_id", params)
    if scope:
        clauses.append(scope)

    rows = (await db.execute(text(f"""
        SELECT f.status, count(*) AS n
          FROM shift_certification_findings f
         WHERE {' AND '.join(clauses)}
         GROUP BY f.status
    """), params)).mappings().all()

    configured = (await db.execute(text(
        "SELECT count(*) FROM certification_requirements WHERE is_active"))).scalar()

    by_status = {r["status"]: r["n"] for r in rows}
    return {
        "requirements_configured": configured,
        "by_status": by_status,
        "open_total": sum(by_status.values()),
        "horizon_days": days,
    }


@router.get("/shifts/{shift_id}", dependencies=[_READ])
async def assess_one_shift(shift_id: str,
                           db: AsyncSession = Depends(get_db_with_tenant)):
    """The live answer for a single shift, computed now rather than read from
    the last sweep — for a roster screen showing a shift somebody just made."""
    try:
        return await cc.assess_shift_by_id(db, shift_id)
    except ValueError:
        raise HTTPException(404, "Shift not found")
