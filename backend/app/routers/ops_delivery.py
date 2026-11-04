"""Who a published briefing is emailed to, the operations reports' schedules, and what was sent.

Phase 4 of closing the feature reference's known limits (4 November 2026; services/ops_delivery.py does the sending).
Everything here needs report:schedule - Admin and Manager, the right that already governs scheduled reports.

  GET  /api/v1/ops-delivery/options                    the reports, roles, people and sites to choose from
  GET  /api/v1/ops-delivery/briefing-recipients        each list: a site's briefings, or every briefing
  PUT  /api/v1/ops-delivery/briefing-recipients        set one list
  GET  /api/v1/ops-delivery/briefings/{id}/emails      what was sent of one briefing, to whom
  GET  /api/v1/ops-delivery/schedules                  the schedules
  POST /api/v1/ops-delivery/schedules                  make one
  PATCH /api/v1/ops-delivery/schedules/{id}            change one, pause it or start it again
  POST /api/v1/ops-delivery/schedules/{id}/remove      take one away (it is kept, with who and when)
  GET  /api/v1/ops-delivery/schedules/{id}/emails      what was sent of one schedule, to whom

ONLY THE ORGANISATION'S OWN PEOPLE are chosen, by name or by role: there is no field for an address. Whether each may
have what is sent is asked when it is queued and again when it is sent. Somebody held to particular sites sets lists
and schedules for those sites only. Every change is a signed-in person's and is audited; nothing here sends anything.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, ops_delivery, ops_reports

router = APIRouter(prefix="/api/v1/ops-delivery", tags=["operations-reports"],
                   dependencies=[Depends(require_permission("report:schedule"))])
#: Roles a list or a schedule may name: everyone the organisation has but the platform owner.
ROLES = (2, 3, 4, 5, 6, 7, 8)
EVERY_SITE = "Somebody held to particular sites sets this for those sites only."


def _a_person(token: TokenPayload) -> None:
    if token.via_api_key:
        raise HTTPException(403, "This is set by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is set by the organisation's own staff, not from a support session.")


def _site_ok(allowed, site_id) -> None:
    if site_id is None and allowed is not None:
        raise HTTPException(403, EVERY_SITE)
    if site_id is not None and not is_site_allowed(allowed, site_id):
        raise HTTPException(404, "Site not found")


async def _names(db: AsyncSession, user_ids, role_ids) -> tuple[list[dict], list[dict]]:
    users = [dict(r) for r in (await db.execute(text(
        "SELECT id, full_name AS name, is_active FROM users WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY full_name"),
        {"ids": [str(u) for u in user_ids]})).mappings()]
    roles = [dict(r) for r in (await db.execute(text(
        "SELECT id, name FROM roles WHERE id = ANY(CAST(:ids AS int[])) ORDER BY id"),
        {"ids": [int(r) for r in role_ids]})).mappings()]
    return users, roles


async def _check_people(db: AsyncSession, user_ids: list[uuid.UUID], role_ids: list[int]) -> None:
    if any(r not in ROLES for r in role_ids):
        raise HTTPException(422, "A role named is not one of the organisation's.")
    if user_ids:
        found = (await db.execute(text("SELECT count(*) FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                  {"ids": [str(u) for u in set(user_ids)]})).scalar()
        if found != len(set(user_ids)):
            raise HTTPException(422, "A person named is not one of the organisation's.")


@router.get("/options")
async def options(db: AsyncSession = Depends(get_db_with_tenant),
                  allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """What lists and schedules are made of."""
    people = [dict(r) for r in (await db.execute(text("""
        SELECT u.id, u.full_name AS name, r.name AS role FROM users u JOIN roles r ON r.id = u.role_id
         WHERE u.is_active AND u.role_id <> 1 ORDER BY u.full_name, u.id
    """))).mappings()]
    roles = [dict(r) for r in (await db.execute(text(
        "SELECT id, name FROM roles WHERE id = ANY(CAST(:ids AS int[])) ORDER BY id"), {"ids": list(ROLES)})).mappings()]
    sites = [dict(r) for r in (await db.execute(text("SELECT id, name FROM sites WHERE is_active ORDER BY name")))
             .mappings() if is_site_allowed(allowed, r["id"])]
    return {"reports": [{"key": r.key, "title": r.title, "periodic": r.periodic, "needs": list(r.needs)}
                        for r in ops_reports.REPORTS],
            "frequencies": list(ops_delivery.FREQUENCIES), "people": people, "roles": roles, "sites": sites,
            "every_site_allowed": allowed is None}


# ─── Briefing lists ──────────────────────────────────────────────────────────

@router.get("/briefing-recipients")
async def briefing_lists(db: AsyncSession = Depends(get_db_with_tenant),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    rows = (await db.execute(text("""
        SELECT l.site_id, s.name AS site_name, l.user_ids, l.role_ids, l.updated_at, u.full_name AS updated_by_name
          FROM briefing_recipient_lists l LEFT JOIN sites s ON s.id = l.site_id
          LEFT JOIN users u ON u.id = l.updated_by_user_id ORDER BY s.name NULLS FIRST
    """))).mappings().all()
    out = []
    for r in rows:
        if r["site_id"] is not None and not is_site_allowed(allowed, r["site_id"]):
            continue
        users, roles = await _names(db, r["user_ids"], r["role_ids"])
        out.append({"site_id": r["site_id"], "site_name": r["site_name"], "users": users, "roles": roles,
                    "updated_at": r["updated_at"], "updated_by_name": r["updated_by_name"]})
    return {"lists": out, "note": "A published briefing is emailed to the people on its site's list and on the list "
                                  "for every briefing, who may read briefings and are shown its site."}


class ListBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: uuid.UUID | None = None
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    role_ids: list[int] = Field(default_factory=list, max_length=7)


@router.put("/briefing-recipients")
async def set_briefing_list(body: ListBody, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                            token: TokenPayload = Depends(get_token_payload),
                            allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Set who a site's briefings - or every briefing - are emailed to. Empty lists: nobody."""
    _a_person(token)
    _site_ok(allowed, body.site_id)
    await _check_people(db, body.user_ids, body.role_ids)
    users = sorted({str(u) for u in body.user_ids})
    roles = sorted(set(body.role_ids))
    await db.execute(text("""
        INSERT INTO briefing_recipient_lists (tenant_id, site_id, user_ids, role_ids, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), CAST(:u AS uuid[]), CAST(:r AS smallint[]),
                CAST(:who AS uuid))
        ON CONFLICT (tenant_id, COALESCE(site_id, '00000000-0000-0000-0000-000000000000'::uuid))
        DO UPDATE SET user_ids = EXCLUDED.user_ids, role_ids = EXCLUDED.role_ids,
                      updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = now()
    """), {"s": str(body.site_id) if body.site_id else None, "u": users, "r": roles, "who": token.user_id})
    await intel_audit.record(db, request, token, "ops.briefing_recipients.set", "briefing_recipient_list", None,
                             site_id=body.site_id, detail={"user_ids": users, "role_ids": roles})
    out = await briefing_lists(db, allowed)
    await db.commit()
    return out


async def _emails(db: AsyncSession, column: str, ref: uuid.UUID) -> list[dict]:
    rows = await db.execute(text(f"""
        SELECT e.id, e.user_id, u.full_name AS name, e.email, e.status, e.attempts, e.sent_at, e.last_error,
               e.created_at, e.period_start, e.period_end, e.rows_sent, e.revised, e.subject
          FROM ops_emails e LEFT JOIN users u ON u.id = e.user_id
         WHERE e.{column} = CAST(:r AS uuid) ORDER BY e.created_at DESC, u.full_name LIMIT 200
    """), {"r": str(ref)})
    return [dict(r) for r in rows.mappings()]


@router.get("/briefings/{briefing_id:uuid}/emails")
async def briefing_emails(briefing_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    b = (await db.execute(text("SELECT site_id FROM daily_briefings WHERE id = CAST(:b AS uuid)"),
                          {"b": str(briefing_id)})).mappings().first()
    if b is None or (b["site_id"] is None and allowed is not None) or \
            (b["site_id"] is not None and not is_site_allowed(allowed, b["site_id"])):
        raise HTTPException(404, "Briefing not found")
    return {"emails": await _emails(db, "briefing_id", briefing_id)}


# ─── Report schedules ────────────────────────────────────────────────────────

class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_key: str = Field(..., max_length=40)
    site_id: uuid.UUID | None = None
    frequency: Literal["DAILY", "WEEKLY", "MONTHLY"]
    hour: int = Field(..., ge=0, le=23)
    weekday: int | None = Field(None, ge=0, le=6)
    month_day: int | None = Field(None, ge=1, le=28)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    role_ids: list[int] = Field(default_factory=list, max_length=7)


class ScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency: Literal["DAILY", "WEEKLY", "MONTHLY"] | None = None
    hour: int | None = Field(None, ge=0, le=23)
    weekday: int | None = Field(None, ge=0, le=6)
    month_day: int | None = Field(None, ge=1, le=28)
    user_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    role_ids: list[int] | None = Field(None, max_length=7)
    is_active: bool | None = None


def _timing(frequency: str, weekday: int | None, month_day: int | None) -> tuple[int | None, int | None]:
    if frequency == "DAILY":
        return None, None
    if frequency == "WEEKLY":
        if weekday is None:
            raise HTTPException(422, "A weekly schedule says which day of the week (0 is Monday).")
        return weekday, None
    if month_day is None:
        raise HTTPException(422, "A monthly schedule says which day of the month, 1 to 28.")
    return None, month_day


async def _schedules(db: AsyncSession, allowed, schedule_id: uuid.UUID | None = None) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT x.id, x.report_key, x.site_id, s.name AS site_name, x.frequency, x.hour, x.weekday, x.month_day,
               x.user_ids, x.role_ids, x.is_active, x.next_run_at, x.last_run_at, x.created_at, x.updated_at,
               c.full_name AS created_by_name,
               (SELECT count(*) FROM ops_emails e WHERE e.schedule_id = x.id AND e.status = 'SENT') AS sent,
               (SELECT count(*) FROM ops_emails e WHERE e.schedule_id = x.id AND e.status = 'FAILED') AS failed,
               (SELECT count(*) FROM ops_emails e WHERE e.schedule_id = x.id AND e.status = 'LEFT_OUT') AS left_out
          FROM ops_report_schedules x LEFT JOIN sites s ON s.id = x.site_id
          LEFT JOIN users c ON c.id = x.created_by_user_id
         WHERE x.removed_at IS NULL AND (CAST(:id AS uuid) IS NULL OR x.id = CAST(:id AS uuid))
         ORDER BY x.created_at
    """), {"id": str(schedule_id) if schedule_id else None})).mappings().all()
    out = []
    for r in rows:
        if (r["site_id"] is None and allowed is not None) or \
                (r["site_id"] is not None and not is_site_allowed(allowed, r["site_id"])):
            continue
        users, roles = await _names(db, r["user_ids"], r["role_ids"])
        report = ops_reports.BY_KEY[r["report_key"]]
        out.append({**{k: r[k] for k in ("id", "report_key", "site_id", "site_name", "frequency", "hour", "weekday",
                                         "month_day", "is_active", "next_run_at", "last_run_at", "created_at",
                                         "updated_at", "created_by_name", "sent", "failed", "left_out")},
                    "report_title": report.title, "users": users, "roles": roles})
    return out


@router.get("/schedules")
async def list_schedules(db: AsyncSession = Depends(get_db_with_tenant),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    return {"schedules": await _schedules(db, allowed),
            "note": "Each person named gets the report as they could take it out themselves: their own sites, and "
                    "only if they hold opsreport:export and what its records are read under."}


@router.post("/schedules", status_code=201)
async def make_schedule(body: ScheduleBody, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload),
                        allowed: list[str] | None = Depends(get_allowed_site_ids)):
    _a_person(token)
    if body.report_key not in ops_reports.BY_KEY:
        raise HTTPException(422, "There is no such report.")
    _site_ok(allowed, body.site_id)
    if body.site_id is not None and not (await db.execute(
            text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(body.site_id)})).scalar():
        raise HTTPException(404, "Site not found")
    if not body.user_ids and not body.role_ids:
        raise HTTPException(422, "Name somebody, or a role, to send it to.")
    await _check_people(db, body.user_ids, body.role_ids)
    weekday, month_day = _timing(body.frequency, body.weekday, body.month_day)
    timing = {"frequency": body.frequency, "hour": body.hour, "weekday": weekday, "month_day": month_day}
    zone = await ops_delivery.zone_of(db)
    first = ops_delivery.next_after(timing, datetime.now(timezone.utc), zone)
    new_id = (await db.execute(text("""
        INSERT INTO ops_report_schedules (tenant_id, report_key, site_id, frequency, hour, weekday, month_day,
                                          user_ids, role_ids, next_run_at, created_by_user_id, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :key, CAST(:site AS uuid), :frequency, :hour, :weekday,
                :month_day, CAST(:users AS uuid[]), CAST(:roles AS smallint[]), :next, CAST(:who AS uuid),
                CAST(:who AS uuid))
        RETURNING id
    """), {"key": body.report_key, "site": str(body.site_id) if body.site_id else None, **timing,
           "users": sorted({str(u) for u in body.user_ids}), "roles": sorted(set(body.role_ids)), "next": first,
           "who": token.user_id})).scalar()
    await intel_audit.record(db, request, token, "ops.schedule.create", "ops_report_schedule", new_id,
                             site_id=body.site_id, detail={"report": body.report_key, **timing})
    (out,) = await _schedules(db, allowed, new_id)
    await db.commit()
    return out


async def _own(db: AsyncSession, schedule_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text("""
        SELECT id, site_id, frequency, hour, weekday, month_day, user_ids, role_ids, is_active
          FROM ops_report_schedules WHERE id = CAST(:id AS uuid) AND removed_at IS NULL FOR UPDATE
    """), {"id": str(schedule_id)})).mappings().first()
    if row is None or (row["site_id"] is None and allowed is not None) or \
            (row["site_id"] is not None and not is_site_allowed(allowed, row["site_id"])):
        raise HTTPException(404, "Schedule not found")
    return dict(row)


@router.patch("/schedules/{schedule_id:uuid}")
async def change_schedule(schedule_id: uuid.UUID, body: ScheduleChange, request: Request,
                          db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Change a schedule's timing or who it goes to, or pause it or start it again. Starting it again begins at its
    next time: nothing is sent for the time it was paused."""
    _a_person(token)
    row = await _own(db, schedule_id, allowed)
    given = body.model_dump(exclude_unset=True)
    frequency = given.get("frequency", row["frequency"])
    weekday, month_day = _timing(frequency, given.get("weekday", row["weekday"]), given.get("month_day", row["month_day"]))
    timing = {"frequency": frequency, "hour": given.get("hour", row["hour"]), "weekday": weekday, "month_day": month_day}
    users = sorted({str(u) for u in given["user_ids"]}) if "user_ids" in given else [str(u) for u in row["user_ids"]]
    roles = sorted(set(given["role_ids"])) if "role_ids" in given else list(row["role_ids"])
    await _check_people(db, [uuid.UUID(u) for u in users], roles)
    if not users and not roles:
        raise HTTPException(422, "Name somebody, or a role, to send it to.")
    active = given.get("is_active", row["is_active"])
    zone = await ops_delivery.zone_of(db)
    await db.execute(text("""
        UPDATE ops_report_schedules
           SET frequency = :frequency, hour = :hour, weekday = :weekday, month_day = :month_day,
               user_ids = CAST(:users AS uuid[]), role_ids = CAST(:roles AS smallint[]), is_active = :active,
               next_run_at = :next, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {**timing, "users": users, "roles": roles, "active": active, "who": token.user_id, "id": str(schedule_id),
           "next": ops_delivery.next_after(timing, datetime.now(timezone.utc), zone)})
    await intel_audit.record(db, request, token, "ops.schedule.update", "ops_report_schedule", schedule_id,
                             site_id=row["site_id"], detail={"changed": sorted(given), "is_active": active})
    (out,) = await _schedules(db, allowed, schedule_id)
    await db.commit()
    return out


@router.post("/schedules/{schedule_id:uuid}/remove")
async def remove_schedule(schedule_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Take a schedule away. It is kept, with who took it away and when, and what it sent."""
    _a_person(token)
    row = await _own(db, schedule_id, allowed)
    await db.execute(text("""
        UPDATE ops_report_schedules SET removed_at = now(), removed_by_user_id = CAST(:who AS uuid), is_active = FALSE
         WHERE id = CAST(:id AS uuid)
    """), {"who": token.user_id, "id": str(schedule_id)})
    await intel_audit.record(db, request, token, "ops.schedule.remove", "ops_report_schedule", schedule_id,
                             site_id=row["site_id"], detail={})
    await db.commit()
    return {"removed": True}


@router.get("/schedules/{schedule_id:uuid}/emails")
async def schedule_emails(schedule_id: uuid.UUID, limit: int = Query(100, ge=1, le=200),
                          db: AsyncSession = Depends(get_db_with_tenant),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    row = (await db.execute(text("SELECT site_id FROM ops_report_schedules WHERE id = CAST(:id AS uuid)"),
                            {"id": str(schedule_id)})).mappings().first()
    if row is None or (row["site_id"] is None and allowed is not None) or \
            (row["site_id"] is not None and not is_site_allowed(allowed, row["site_id"])):
        raise HTTPException(404, "Schedule not found")
    return {"emails": (await _emails(db, "schedule_id", schedule_id))[:limit]}
