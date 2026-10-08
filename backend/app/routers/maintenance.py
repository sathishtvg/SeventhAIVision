"""Maintenance: work orders, the schedules that put them forward, and whether health may.

Phase 8 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
DEVICE_HEALTH_ARCHITECTURE.md.

  GET  /work-orders                     the orders, what is put forward first
  POST /work-orders                     raise one — by hand, or for a facility defect
  GET  /options                         what an order can be raised about, and who can be given it
  GET  /work-orders/{id}                one order
  PATCH /work-orders/{id}               change what it says, when it is due, who has it
  POST /work-orders/{id}/accept         a suggestion becomes work
  POST /work-orders/{id}/dismiss        or it does not, with why
  POST /work-orders/{id}/start|complete whoever has it, or somebody who manages maintenance
  POST /work-orders/{id}/cancel         with why
  GET|POST /schedules, PATCH /schedules/{id}    what is done every so many days
  GET|PUT /settings                     whether a device read as down for long puts an order forward

THE PLATFORM SUGGESTS; A PERSON RAISES THE WORK (services/maintenance.py). A
suggested order is accepted or dismissed by somebody who manages maintenance.
Nothing here assigns anybody by itself, and nothing here tells anybody.

A FACILITY DEFECT IS NOT CHANGED BY AN ORDER RAISED FOR IT. The defect is
referred and resolved where it always was; the order only says which defect it
is for.

AN ORDER THAT IS OVER STANDS. Done, cancelled and dismissed orders are not
changed — the database refuses it — and none is ever removed.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit
from app.services import maintenance as work

PERMISSIONS = ("maintenance:read", "maintenance:manage", "asset:read")

router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])
_READ = [Depends(require_permission("maintenance:read"))]
_MANAGE = [Depends(require_permission("maintenance:manage"))]


def _a_person(token: TokenPayload) -> None:
    """Who raised, accepted, did and closed an order are people, by name."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


async def _held(db: AsyncSession, role_id: int) -> frozenset[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": list(PERMISSIONS)})
    return frozenset(r.code for r in rows)


def _aware(moment: datetime | None) -> None:
    if moment is not None and moment.tzinfo is None:
        raise HTTPException(422, "Say when it is due with a time zone.")


# ─── A work order, as it is given ────────────────────────────────────────────

_ORDER = """
    SELECT w.id, w.number, w.site_id, s.name AS site_name, w.asset_id, a.asset_code, a.name AS asset_name,
           w.schedule_id, w.defect_id, w.title, w.description, w.kind, w.priority, w.state, w.origin, w.origin_key,
           w.suggestion_reason, w.raised_by_user_id, rb.full_name AS raised_by_name, w.raised_at,
           ab.full_name AS accepted_by_name, w.accepted_at, w.assigned_to_user_id, au.full_name AS assigned_to_user_name,
           w.assigned_to_name, w.assigned_at, w.due_at, w.started_at, sb.full_name AS started_by_name, w.completed_at,
           cb.full_name AS completed_by_name, w.completion_note, w.parts_used, w.downtime_minutes, w.closed_at,
           xb.full_name AS closed_by_name, w.closed_reason, w.updated_at,
           (w.state IN ('OPEN', 'IN_PROGRESS') AND w.due_at IS NOT NULL AND w.due_at < now()) AS overdue
      FROM maintenance_work_orders w
      LEFT JOIN sites s ON s.id = w.site_id
      LEFT JOIN asset_register a ON a.id = w.asset_id
      LEFT JOIN users rb ON rb.id = w.raised_by_user_id
      LEFT JOIN users ab ON ab.id = w.accepted_by_user_id
      LEFT JOIN users au ON au.id = w.assigned_to_user_id
      LEFT JOIN users sb ON sb.id = w.started_by_user_id
      LEFT JOIN users cb ON cb.id = w.completed_by_user_id
      LEFT JOIN users xb ON xb.id = w.closed_by_user_id
"""
#: What is put forward first, then what is being done, then what waits; what is over, last.
_RANK = "CASE w.state WHEN 'SUGGESTED' THEN 0 WHEN 'IN_PROGRESS' THEN 1 WHEN 'OPEN' THEN 2 ELSE 3 END"


def _may(row: Mapping, me: str, held: frozenset[str]) -> dict:
    manage = "maintenance:manage" in held
    theirs = manage or str(row["assigned_to_user_id"]) == me
    state = row["state"]
    return {"accept": manage and state == "SUGGESTED", "dismiss": manage and state == "SUGGESTED",
            "change": manage and state in ("OPEN", "IN_PROGRESS"), "start": theirs and state == "OPEN",
            "complete": theirs and state in ("OPEN", "IN_PROGRESS"), "cancel": manage and state in ("OPEN", "IN_PROGRESS")}


def _out(row: Mapping, me: str, held: frozenset[str]) -> dict:
    return {**{k: row[k] for k in row.keys() if k != "origin_key"},
            "assigned_to_me": str(row["assigned_to_user_id"]) == me,
            "note": work.SUGGESTION_NOTE if row["state"] == "SUGGESTED" else None, "may": _may(row, me, held)}


def _may_read(row: Mapping, allowed, me: str) -> bool:
    """An order is read at the sites the reader may see, and by whoever has it."""
    return is_site_allowed(allowed, row["site_id"]) or str(row["assigned_to_user_id"]) == me


async def _one(db: AsyncSession, order_id, allowed, me: str, *, lock: bool = False) -> dict:
    row = (await db.execute(text(f"{_ORDER} WHERE w.id = CAST(:id AS uuid){' FOR UPDATE OF w' if lock else ''}"),
                            {"id": str(order_id)})).mappings().first()
    if row is None or not _may_read(row, allowed, me):
        raise HTTPException(404, "Work order not found")
    return dict(row)


async def _assignee(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Somebody an order is given to is one of the organisation's people, still
    working, and able to read the order they are given."""
    row = (await db.execute(text("""
        SELECT u.id, u.full_name, u.is_active,
               EXISTS (SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                        WHERE rp.role_id = u.role_id AND p.code = 'maintenance:read') AS reads
          FROM users u WHERE u.id = CAST(:id AS uuid)
    """), {"id": str(user_id)})).mappings().first()
    if row is None or not row["is_active"]:
        raise HTTPException(422, "The person named is not one of the organisation's people.")
    if not row["reads"]:
        raise HTTPException(422, f"{row['full_name']} cannot read work orders, so cannot be given one.")
    return dict(row)


async def _asset(db: AsyncSession, asset_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text("SELECT id, site_id, status, asset_code FROM asset_register WHERE id = CAST(:a AS uuid)"),
                            {"a": str(asset_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(422, "No asset of that id is in the register.")
    if row["status"] == "RETIRED":
        raise HTTPException(422, f"{row['asset_code']} is retired. Work is not raised on a retired asset.")
    return dict(row)


# ─── Reading ─────────────────────────────────────────────────────────────────

@router.get("/work-orders", dependencies=_READ)
async def list_orders(
    state: list[str] = Query(default=[]),
    site_id: uuid.UUID | None = Query(None),
    asset_id: uuid.UUID | None = Query(None),
    kind: Literal["CORRECTIVE", "PREVENTIVE", "INSPECTION"] | None = Query(None),
    origin: Literal["PERSON", "DEFECT", "HEALTH", "SCHEDULE"] | None = Query(None),
    mine: bool = Query(False),
    overdue: bool = Query(False),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Work orders: what the platform has put forward first, then what is
    being done, then what waits, each by when it is due. Orders that are over
    are left out unless a state is asked for."""
    unknown = [s for s in state if s not in work.STATES]
    if unknown:
        raise HTTPException(422, f"Unknown state '{unknown[0]}'.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    cap, skip = clamp(limit, offset)
    params: dict = {"limit": cap + 1, "offset": skip}
    scoped = []
    scope = site_scope_clause(allowed, "w.site_id", params)
    if scope:
        # Somebody held to particular sites still reads the orders they were given.
        scoped.append(f"({scope} OR w.assigned_to_user_id = CAST(:me AS uuid))")
    if scope or mine:
        params["me"] = token.user_id
    where = list(scoped)
    if state:
        where.append("w.state = ANY(:states)")
        params["states"] = state
    else:
        where.append("w.state IN ('SUGGESTED', 'OPEN', 'IN_PROGRESS')")
    for column, value, name in (("w.site_id", site_id, "site"), ("w.asset_id", asset_id, "asset")):
        if value is not None:
            where.append(f"{column} = CAST(:{name} AS uuid)")
            params[name] = str(value)
    if kind is not None:
        where.append("w.kind = :kind")
        params["kind"] = kind
    if origin is not None:
        where.append("w.origin = :origin")
        params["origin"] = origin
    if mine:
        where.append("w.assigned_to_user_id = CAST(:me AS uuid)")
    if overdue:
        where.append("w.state IN ('OPEN', 'IN_PROGRESS') AND w.due_at < now()")
    if q and q.strip():
        # The words as typed: a percent sign or an underscore is looked for, not treated as a wildcard.
        params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(w.title ILIKE :q ESCAPE '\\' OR w.number ILIKE :q ESCAPE '\\' OR a.name ILIKE :q ESCAPE '\\' "
                     "OR a.asset_code ILIKE :q ESCAPE '\\')")
    rows = (await db.execute(text(f"""{_ORDER} WHERE {' AND '.join(where)}
         ORDER BY {_RANK}, w.due_at NULLS LAST, w.raised_at DESC, w.id LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    counts = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE w.state = 'SUGGESTED') AS suggested,
               count(*) FILTER (WHERE w.state = 'OPEN') AS open,
               count(*) FILTER (WHERE w.state = 'IN_PROGRESS') AS in_progress,
               count(*) FILTER (WHERE w.state IN ('OPEN', 'IN_PROGRESS') AND w.due_at < now()) AS overdue
          FROM maintenance_work_orders w {('WHERE ' + ' AND '.join(scoped)) if scoped else ''}
    """), {k: params[k] for k in ("allowed_site_ids", "me") if scoped and k in params})).mappings().one()
    held = await _held(db, token.role_id)
    return {"items": [_out(r, token.user_id, held) for r in rows[:cap]], "limit": cap, "offset": skip,
            "has_more": len(rows) > cap, "counts": dict(counts), "can_manage": "maintenance:manage" in held,
            "states": list(work.STATES), "kinds": list(work.KINDS), "priorities": list(work.PRIORITIES)}


@router.get("/options", dependencies=_READ + _MANAGE)
async def what_can_be_raised(
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What an order can be raised about — the assets in the register and the
    facility defects still open, at one site or at all the caller may see —
    and the people who can be given one."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")

    def scoped(column: str) -> tuple[str, dict]:
        params: dict = {}
        where = []
        scope = site_scope_clause(allowed, column, params)
        if scope:
            where.append(scope)
        if site_id is not None:
            where.append(f"{column} = CAST(:site AS uuid)")
            params["site"] = str(site_id)
        return (" AND " + " AND ".join(where)) if where else "", params

    more, params = scoped("a.site_id")
    assets = (await db.execute(text(f"""
        SELECT a.id, a.asset_code, a.name, a.kind, a.site_id, s.name AS site_name
          FROM asset_register a LEFT JOIN sites s ON s.id = a.site_id
         WHERE a.status <> 'RETIRED'{more} ORDER BY a.asset_code LIMIT 500
    """), params)).mappings().all()
    more, params = scoped("d.site_id")
    defects = (await db.execute(text(f"""
        SELECT d.id, d.category, d.location, d.description, d.severity, d.status, d.site_id, s.name AS site_name,
               d.reported_at
          FROM facility_defects d LEFT JOIN sites s ON s.id = d.site_id
         WHERE d.status NOT IN ('resolved', 'closed'){more} ORDER BY d.reported_at DESC LIMIT 200
    """), params)).mappings().all()
    people = (await db.execute(text("""
        SELECT u.id, u.full_name AS name
          FROM users u
         WHERE u.is_active AND EXISTS (
               SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = u.role_id AND p.code = 'maintenance:read')
         ORDER BY u.full_name LIMIT 500
    """))).mappings().all()
    return {"assets": [dict(r) for r in assets], "defects": [dict(r) for r in defects],
            "people": [dict(r) for r in people], "kinds": list(work.KINDS), "priorities": list(work.PRIORITIES)}


@router.get("/work-orders/{order_id:uuid}", dependencies=_READ)
async def read_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One work order: what it is for, where it came from, and what became of it."""
    held = await _held(db, token.role_id)
    return _out(await _one(db, order_id, allowed, token.user_id), token.user_id, held)


# ─── Raising, accepting and dismissing ───────────────────────────────────────

class RaiseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    kind: Literal["CORRECTIVE", "PREVENTIVE", "INSPECTION"] = "CORRECTIVE"
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    site_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    #: The facility defect it is for. The defect itself is not changed.
    defect_id: uuid.UUID | None = None
    due_at: datetime | None = None
    assigned_to_user_id: uuid.UUID | None = None
    assigned_to_name: str | None = Field(None, max_length=200)


@router.post("/work-orders", status_code=201, dependencies=_READ + _MANAGE)
async def raise_order(
    body: RaiseBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Raise a work order: on an asset, for a facility defect, or for a site.
    It is open from the moment it is raised."""
    _a_person(token)
    _aware(body.due_at)
    if not body.title.strip():
        raise HTTPException(422, "A work order says what is to be done.")
    site_id = body.site_id
    if body.asset_id is not None:
        asset = await _asset(db, body.asset_id, allowed)
        if site_id is not None and asset["site_id"] is not None and asset["site_id"] != site_id:
            raise HTTPException(422, "That asset is at another site.")
        site_id = site_id or asset["site_id"]
    if body.defect_id is not None:
        defect = (await db.execute(text("SELECT id, site_id FROM facility_defects WHERE id = CAST(:d AS uuid)"),
                                   {"d": str(body.defect_id)})).mappings().first()
        if defect is None or not is_site_allowed(allowed, defect["site_id"]):
            raise HTTPException(422, "No facility defect of that id is known.")
        if site_id is not None and defect["site_id"] is not None and defect["site_id"] != site_id:
            raise HTTPException(422, "That defect is at another site.")
        site_id = site_id or defect["site_id"]
    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = :s"), {"s": site_id})).scalar()
        if not known or not is_site_allowed(allowed, site_id):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "Say which site the work is at.")
    if body.assigned_to_user_id is not None:
        await _assignee(db, body.assigned_to_user_id)
    given_to = (body.assigned_to_name or "").strip() or None
    assigned = body.assigned_to_user_id is not None or given_to is not None
    new_id = (await db.execute(text("""
        INSERT INTO maintenance_work_orders
               (tenant_id, number, site_id, asset_id, defect_id, title, description, kind, priority, state, origin,
                raised_by_user_id, assigned_to_user_id, assigned_to_name, assigned_at, due_at)
        VALUES (current_setting('app.current_tenant')::uuid, :number, :site, :asset, :defect, :title, :description,
                :kind, :priority, 'OPEN', :origin, CAST(:who AS uuid), :user, :name,
                CASE WHEN :assigned THEN now() END, :due)
        RETURNING id
    """), {"number": await work.next_number(db), "site": site_id, "asset": body.asset_id, "defect": body.defect_id,
           "title": body.title.strip(), "description": (body.description or "").strip() or None, "kind": body.kind,
           "priority": body.priority, "origin": "DEFECT" if body.defect_id else "PERSON", "who": token.user_id,
           "user": body.assigned_to_user_id, "name": given_to, "assigned": assigned, "due": body.due_at})).scalar()
    held = await _held(db, token.role_id)
    answer = _out(await _one(db, new_id, allowed, token.user_id), token.user_id, held)
    await intel_audit.record(db, request, token, "maintenance.order.raise", "maintenance_work_order", new_id,
                             site_id=site_id, detail={"number": answer["number"], "kind": body.kind,
                                                      "origin": answer["origin"],
                                                      "asset_id": str(body.asset_id) if body.asset_id else None})
    await db.commit()
    return answer


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] | None = None
    due_at: datetime | None = None
    assigned_to_user_id: uuid.UUID | None = None
    assigned_to_name: str | None = Field(None, max_length=200)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


async def _locked(db, token, allowed, order_id) -> tuple[dict, frozenset[str], datetime]:
    """An order, locked for a change by a person."""
    _a_person(token)
    held = await _held(db, token.role_id)
    row = await _one(db, order_id, allowed, token.user_id, lock=True)
    return row, held, datetime.now(timezone.utc)


def _over(row: Mapping) -> None:
    if row["state"] in work.OVER:
        raise HTTPException(409, "This work order is over. It stands as it is.")


async def _closed(db, request, token, allowed, row, held, now, state: str, action: str, detail: dict) -> dict:
    """The end of an order — done, cancelled or dismissed — and what that does to its schedule."""
    await work.after_closing(db, row, state, now)
    await intel_audit.record(db, request, token, action, "maintenance_work_order", row["id"], site_id=row["site_id"],
                             detail={"number": row["number"], "was": row["state"], **detail})
    answer = _out(await _one(db, row["id"], allowed, token.user_id), token.user_id, held)
    await db.commit()
    return answer


@router.post("/work-orders/{order_id:uuid}/accept", dependencies=_READ + _MANAGE)
async def accept_order(
    order_id: uuid.UUID,
    request: Request,
    body: AcceptBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Accept what the platform put forward: from now it is work, and whoever
    accepted it is recorded as having raised it into work. It may be given to
    somebody and given a date in the same step."""
    body = body or AcceptBody()
    _aware(body.due_at)
    row, held, _ = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] != "SUGGESTED":
        raise HTTPException(409, "This is already a work order: there is nothing to accept.")
    if body.assigned_to_user_id is not None:
        await _assignee(db, body.assigned_to_user_id)
    given_to = (body.assigned_to_name or "").strip() or None
    assigned = body.assigned_to_user_id is not None or given_to is not None
    await db.execute(text("""
        UPDATE maintenance_work_orders
           SET state = 'OPEN', accepted_by_user_id = CAST(:who AS uuid), accepted_at = now(),
               priority = COALESCE(:priority, priority), due_at = COALESCE(:due, due_at),
               assigned_to_user_id = :user, assigned_to_name = :name, assigned_at = CASE WHEN :assigned THEN now() END,
               updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "priority": body.priority, "due": body.due_at, "user": body.assigned_to_user_id,
           "name": given_to, "assigned": assigned, "id": row["id"]})
    await intel_audit.record(db, request, token, "maintenance.order.accept", "maintenance_work_order", row["id"],
                             site_id=row["site_id"], detail={"number": row["number"], "origin": row["origin"],
                                                             "assigned": assigned})
    answer = _out(await _one(db, row["id"], allowed, token.user_id), token.user_id, held)
    await db.commit()
    return answer


@router.post("/work-orders/{order_id:uuid}/dismiss", dependencies=_READ + _MANAGE)
async def dismiss_order(
    order_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set aside what the platform put forward, and say why. It is kept, and
    the same thing is not put forward again. A schedule moves on to its next
    date."""
    row, held, now = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] != "SUGGESTED":
        raise HTTPException(409, "This has been accepted as work. Cancel it instead, and say why.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is dismissed.")
    await db.execute(text("""
        UPDATE maintenance_work_orders
           SET state = 'DISMISSED', closed_at = now(), closed_by_user_id = CAST(:who AS uuid), closed_reason = :why,
               updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "why": body.reason.strip(), "id": row["id"]})
    return await _closed(db, request, token, allowed, row, held, now, "DISMISSED", "maintenance.order.dismiss",
                         {"origin": row["origin"]})


# ─── Doing the work ──────────────────────────────────────────────────────────

class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] | None = None
    due_at: datetime | None = None
    asset_id: uuid.UUID | None = None
    assigned_to_user_id: uuid.UUID | None = None
    assigned_to_name: str | None = Field(None, max_length=200)


@router.patch("/work-orders/{order_id:uuid}", dependencies=_READ + _MANAGE)
async def change_order(
    order_id: uuid.UUID,
    body: ChangeBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change what an order says, when it is due, which asset it is on, or who
    has it. What is left out stays; what is given as nothing is cleared. Not a
    suggestion — accept it first — and not an order that is over."""
    _aware(body.due_at)
    row, held, _ = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] == "SUGGESTED":
        raise HTTPException(409, "Accept it first: until then it is the platform's suggestion, as it was made.")
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing to change.")
    for must in ("title", "priority"):
        if must in given and getattr(body, must) is None:
            raise HTTPException(422, f"A work order has a {must}.")
    if "title" in given and not body.title.strip():
        raise HTTPException(422, "A work order says what is to be done.")
    if "asset_id" in given and body.asset_id is not None:
        asset = await _asset(db, body.asset_id, allowed)
        if row["site_id"] is not None and asset["site_id"] is not None and asset["site_id"] != row["site_id"]:
            raise HTTPException(422, "That asset is at another site.")
    if "assigned_to_user_id" in given and body.assigned_to_user_id is not None:
        await _assignee(db, body.assigned_to_user_id)
    sets, params = [], {"id": row["id"]}
    for column in ("title", "description", "priority", "due_at", "asset_id", "assigned_to_user_id", "assigned_to_name"):
        if column in given:
            value = getattr(body, column)
            sets.append(f"{column} = :{column}")
            params[column] = ((value or "").strip() or None) if column in ("title", "description", "assigned_to_name") else value
    if given & {"assigned_to_user_id", "assigned_to_name"}:
        user = params.get("assigned_to_user_id", row["assigned_to_user_id"])
        name = params.get("assigned_to_name", row["assigned_to_name"])
        sets.append("assigned_at = CASE WHEN :has THEN now() END")
        params["has"] = user is not None or name is not None
    await db.execute(text(f"UPDATE maintenance_work_orders SET {', '.join(sets)}, updated_at = now() WHERE id = :id"),
                     params)
    await intel_audit.record(db, request, token, "maintenance.order.update", "maintenance_work_order", row["id"],
                             site_id=row["site_id"], detail={"number": row["number"], "changed": sorted(given)})
    answer = _out(await _one(db, row["id"], allowed, token.user_id), token.user_id, held)
    await db.commit()
    return answer


@router.post("/work-orders/{order_id:uuid}/start", dependencies=_READ)
async def start_order(
    order_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say the work has started. By whoever has the order, or by somebody who
    manages maintenance."""
    row, held, _ = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] != "OPEN":
        raise HTTPException(409, "Accept it first." if row["state"] == "SUGGESTED" else "This work has already started.")
    if not _may(row, token.user_id, held)["start"]:
        raise HTTPException(403, "Work is started by whoever has the order, or by somebody who manages maintenance.")
    await db.execute(text("""
        UPDATE maintenance_work_orders
           SET state = 'IN_PROGRESS', started_at = now(), started_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "id": row["id"]})
    await intel_audit.record(db, request, token, "maintenance.order.start", "maintenance_work_order", row["id"],
                             site_id=row["site_id"], detail={"number": row["number"]})
    answer = _out(await _one(db, row["id"], allowed, token.user_id), token.user_id, held)
    await db.commit()
    return answer


class CompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completion_note: str = Field(..., min_length=1, max_length=5000)
    parts_used: str | None = Field(None, max_length=2000)
    #: How long the thing was out of use, as whoever did the work states it.
    downtime_minutes: int | None = Field(None, ge=0, le=5_256_000)


@router.post("/work-orders/{order_id:uuid}/complete", dependencies=_READ)
async def complete_order(
    order_id: uuid.UUID,
    body: CompleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say the work is done, and what was done. By whoever has the order, or
    by somebody who manages maintenance. An order completed without having
    been started is started and completed at the same moment. If it came from a
    schedule, the schedule runs again from today."""
    row, held, now = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] == "SUGGESTED":
        raise HTTPException(409, "Accept it first.")
    if not _may(row, token.user_id, held)["complete"]:
        raise HTTPException(403, "Work is completed by whoever has the order, or by somebody who manages maintenance.")
    if not body.completion_note.strip():
        raise HTTPException(422, "Say what was done.")
    await db.execute(text("""
        UPDATE maintenance_work_orders
           SET state = 'DONE', started_at = COALESCE(started_at, now()),
               started_by_user_id = COALESCE(started_by_user_id, CAST(:who AS uuid)), completed_at = now(),
               completed_by_user_id = CAST(:who AS uuid), completion_note = :note, parts_used = :parts,
               downtime_minutes = :downtime, updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "note": body.completion_note.strip(), "parts": (body.parts_used or "").strip() or None,
           "downtime": body.downtime_minutes, "id": row["id"]})
    return await _closed(db, request, token, allowed, row, held, now, "DONE", "maintenance.order.complete",
                         {"downtime_minutes": body.downtime_minutes, "from_schedule": row["schedule_id"] is not None})


@router.post("/work-orders/{order_id:uuid}/cancel", dependencies=_READ + _MANAGE)
async def cancel_order(
    order_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Cancel an order that is open or in progress, and say why. It is kept. A
    schedule moves on to its next date."""
    row, held, now = await _locked(db, token, allowed, order_id)
    _over(row)
    if row["state"] == "SUGGESTED":
        raise HTTPException(409, "This is a suggestion. Dismiss it instead, and say why.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is cancelled.")
    await db.execute(text("""
        UPDATE maintenance_work_orders
           SET state = 'CANCELLED', closed_at = now(), closed_by_user_id = CAST(:who AS uuid), closed_reason = :why,
               updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "why": body.reason.strip(), "id": row["id"]})
    return await _closed(db, request, token, allowed, row, held, now, "CANCELLED", "maintenance.order.cancel", {})


# ─── Schedules ───────────────────────────────────────────────────────────────

_SCHEDULE = """
    SELECT m.id, COALESCE(m.site_id, a.site_id) AS site_id, s.name AS site_name, m.asset_id, a.asset_code,
           a.name AS asset_name, m.title,
           m.instructions, m.every_days, m.lead_days, m.next_due_on, m.last_done_on, m.is_active,
           m.next_due_on - (now() AT TIME ZONE t.timezone)::date AS days_until_due,
           cb.full_name AS created_by_name, m.created_at, m.updated_at
      FROM maintenance_schedules m
      JOIN tenants t ON t.id = m.tenant_id
      LEFT JOIN asset_register a ON a.id = m.asset_id
      LEFT JOIN sites s ON s.id = COALESCE(m.site_id, a.site_id)
      LEFT JOIN users cb ON cb.id = m.created_by_user_id
"""
_SCHEDULE_SITE = "COALESCE(m.site_id, a.site_id)"


async def _schedule(db: AsyncSession, schedule_id, allowed) -> dict:
    row = (await db.execute(text(f"{_SCHEDULE} WHERE m.id = CAST(:id AS uuid)"), {"id": str(schedule_id)})).mappings().first()
    # A schedule is at its own site, or at the site of the asset it is for.
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Schedule not found")
    return dict(row)


@router.get("/schedules", dependencies=_READ)
async def list_schedules(
    site_id: uuid.UUID | None = Query(None),
    active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What is done every so many days, soonest due first."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    params: dict = {}
    where = []
    scope = site_scope_clause(allowed, _SCHEDULE_SITE, params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append(f"{_SCHEDULE_SITE} = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if active is not None:
        where.append("m.is_active" if active else "NOT m.is_active")
    rows = (await db.execute(text(f"""{_SCHEDULE} {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY m.is_active DESC, m.next_due_on, m.title LIMIT 500"""), params)).mappings().all()
    held = await _held(db, token.role_id)
    return {"items": [dict(r) for r in rows], "can_manage": "maintenance:manage" in held}


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    instructions: str | None = Field(None, max_length=5000)
    site_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    every_days: int = Field(..., ge=1, le=3650)
    lead_days: int = Field(7, ge=0, le=90)
    next_due_on: date


@router.post("/schedules", status_code=201, dependencies=_READ + _MANAGE)
async def add_schedule(
    body: ScheduleBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say that something is to be done every so many days, on an asset or at a
    site. So many days before each date an order is put forward for somebody to
    accept; it is not raised by itself."""
    _a_person(token)
    if not body.title.strip():
        raise HTTPException(422, "A schedule says what is to be done.")
    site_id = body.site_id
    if body.asset_id is not None:
        asset = await _asset(db, body.asset_id, allowed)
        if site_id is not None and asset["site_id"] is not None and asset["site_id"] != site_id:
            raise HTTPException(422, "That asset is at another site.")
        site_id = site_id or asset["site_id"]
    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = :s"), {"s": site_id})).scalar()
        if not known or not is_site_allowed(allowed, site_id):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "Say which site the work is at.")
    new_id = (await db.execute(text("""
        INSERT INTO maintenance_schedules (tenant_id, site_id, asset_id, title, instructions, every_days, lead_days,
                                           next_due_on, created_by_user_id, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :site, :asset, :title, :instructions, :every, :lead,
                :due, CAST(:who AS uuid), CAST(:who AS uuid))
        RETURNING id
    """), {"site": body.site_id, "asset": body.asset_id, "title": body.title.strip(),
           "instructions": (body.instructions or "").strip() or None, "every": body.every_days, "lead": body.lead_days,
           "due": body.next_due_on, "who": token.user_id})).scalar()
    await intel_audit.record(db, request, token, "maintenance.schedule.create", "maintenance_schedule", new_id,
                             site_id=site_id, detail={"title": body.title.strip(), "every_days": body.every_days,
                                                      "next_due_on": body.next_due_on.isoformat()})
    answer = await _schedule(db, new_id, allowed)
    await db.commit()
    return answer


class ScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    instructions: str | None = Field(None, max_length=5000)
    every_days: int | None = Field(None, ge=1, le=3650)
    lead_days: int | None = Field(None, ge=0, le=90)
    next_due_on: date | None = None
    is_active: StrictBool | None = None


@router.patch("/schedules/{schedule_id:uuid}", dependencies=_READ + _MANAGE)
async def change_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleChange,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change a schedule, or switch it off. A schedule is switched off, never
    removed, and what it has already put forward stays as it is."""
    _a_person(token)
    row = await _schedule(db, schedule_id, allowed)
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing to change.")
    for must in ("title", "every_days", "lead_days", "next_due_on", "is_active"):
        if must in given and getattr(body, must) is None:
            raise HTTPException(422, f"A schedule has a {must.replace('_', ' ')}.")
    if "title" in given and not body.title.strip():
        raise HTTPException(422, "A schedule says what is to be done.")
    sets, params = [], {"id": row["id"], "who": token.user_id}
    for column in ("title", "instructions", "every_days", "lead_days", "next_due_on", "is_active"):
        if column in given:
            value = getattr(body, column)
            sets.append(f"{column} = :{column}")
            params[column] = ((value or "").strip() or None) if column in ("title", "instructions") else value
    await db.execute(text(f"""
        UPDATE maintenance_schedules SET {', '.join(sets)}, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = :id
    """), params)
    await intel_audit.record(db, request, token, "maintenance.schedule.update", "maintenance_schedule", row["id"],
                             site_id=row["site_id"], detail={"title": row["title"], "changed": sorted(given)})
    answer = await _schedule(db, row["id"], allowed)
    await db.commit()
    return answer


# ─── Whether health may put an order forward ─────────────────────────────────

@router.get("/settings", dependencies=_READ)
async def read_settings(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Whether a device read as down for long puts a work order forward, and
    after how many hours."""
    held = await _held(db, token.role_id)
    return {**await work.settings(db), "default_after_hours": work.DEFAULT_AFTER_HOURS,
            "can_manage": "maintenance:manage" in held, "note": work.SUGGESTION_NOTE}


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggest_from_health: StrictBool
    suggest_after_hours: int = Field(work.DEFAULT_AFTER_HOURS, ge=1, le=168)


@router.put("/settings", dependencies=_READ + _MANAGE)
async def write_settings(
    body: SettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Switch suggestions from health on or off for the whole organisation, and
    say after how many hours down a device is put forward. Switched on, each
    outage is put forward once; what it puts forward is still only a
    suggestion."""
    _a_person(token)
    if allowed is not None:
        raise HTTPException(403, "This is set for the whole organisation, by somebody who is not held to "
                                 "particular sites.")
    was = await work.settings(db)
    for key, value in ((work.SUGGEST_KEY, body.suggest_from_health), (work.AFTER_KEY, body.suggest_after_hours)):
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :k, CAST(:v AS jsonb), CAST(:who AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"k": key, "v": json.dumps(value), "who": token.user_id})
    await intel_audit.record(db, request, token, "maintenance.settings", "tenant_setting", None,
                             detail={"was": was, "now": body.model_dump()})
    answer = {**await work.settings(db), "changed": was != body.model_dump()}
    await db.commit()
    return answer
