"""Security cases: opened from an incident or an investigation, worked by the people on them, closed by two.

Phase 12 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_CASE_MANAGEMENT.md.

  GET   /                                   the cases the caller may read
  GET   /options                            what a case may be, who may be put on one, and what may be linked
  POST  /                                   open a case — from an incident, an investigation or an evidence package, or none
  GET   /{id}                               one case, whole
  PATCH /{id}                               its title, what it is about, its kind, its priority
  PUT   /{id}/lead                          who leads it
  POST  /{id}/investigators                 put somebody on it         …/{user}/remove   take them off
  POST  /{id}/notes                         a note, in somebody's own words
  POST  /{id}/tasks                         a task                     …/{task}/done | drop
  POST  /{id}/links                         link a record              …/{link}/remove
  POST  /{id}/parties                       name a person or a vehicle …/{party}/remove
  POST  /{id}/request-close                 ask for it to be closed, saying what was found
  POST  /{id}/approve-close | decline-close somebody else approves, or declines with why
  POST  /{id}/reopen                        reopen a closed case, with why
  GET   /{id}/report | /{id}/report.pdf     the case as a document

A CASE REFERS TO WHAT IT IS ABOUT; IT DOES NOT COPY IT (services/case_files.py).
A linked record is read under its own permission and the reader's sites.

A PERSON WORKS ON A CASE THEY ARE ON. Its lead, its investigators and whoever
manages cases; and somebody given a task finishes that task.

CLOSING TAKES TWO PEOPLE, and a closed case is not changed. Every step is a
signed-in person's — not an API key, not a support session — is written into
the case's own history, and is audited.

NOBODY IS TOLD. Being put on a case, given a task or asked to approve sends no
notification: each is found on the list of cases.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import case_files, intel_audit, intel_insight

PERMISSIONS = ("case:read", "case:work", "case:manage")
#: Those, and every permission a linked record is read under.
HELD = (*PERMISSIONS, *case_files.LINK_NEEDS.values())
CLOSED = "The case is not open. A closed case is reopened before it is added to; one waiting for approval is decided first."

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])
_READ = [Depends(require_permission("case:read"))]
_WORK = [Depends(require_permission("case:work"))]
_MANAGE = [Depends(require_permission("case:manage"))]

_CASE = """
    SELECT c.id, c.site_id, s.name AS site_name, c.case_number, c.title, c.summary, c.category, c.priority, c.status,
           c.lead_user_id, l.full_name AS lead_name, c.opened_by_user_id, o.full_name AS opened_by_name, c.opened_at,
           c.outcome, c.close_requested_by_user_id, r.full_name AS close_requested_by_name, c.close_requested_at,
           c.closed_by_user_id, a.full_name AS closed_by_name, c.closed_at, c.updated_at
      FROM case_files c
      LEFT JOIN sites s ON s.id = c.site_id
      LEFT JOIN users l ON l.id = c.lead_user_id
      LEFT JOIN users o ON o.id = c.opened_by_user_id
      LEFT JOIN users r ON r.id = c.close_requested_by_user_id
      LEFT JOIN users a ON a.id = c.closed_by_user_id
"""


def _a_person(token: TokenPayload) -> None:
    """A step in a case is somebody's, by name."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


def _words(value: str | None, what: str) -> str:
    said = (value or "").strip()
    if not said:
        raise HTTPException(422, what)
    return said


async def _case(db: AsyncSession, case_id: uuid.UUID, allowed, *, lock: bool = False) -> dict:
    """A case the caller may read: at a site they are shown — or at no one
    site, for somebody who is not held to particular sites."""
    if lock:
        await db.execute(text("SELECT 1 FROM case_files WHERE id = CAST(:id AS uuid) FOR UPDATE"), {"id": str(case_id)})
    row = (await db.execute(text(f"{_CASE} WHERE c.id = CAST(:id AS uuid)"), {"id": str(case_id)})).mappings().first()
    if row is None or (row["site_id"] is None and allowed is not None) \
            or (row["site_id"] is not None and not is_site_allowed(allowed, row["site_id"])):
        raise HTTPException(404, "Case not found")
    return dict(row)


async def _investigators(db: AsyncSession, case_id) -> list[dict]:
    rows = await db.execute(text("""
        SELECT i.user_id, u.full_name AS name, i.added_at FROM case_investigators i JOIN users u ON u.id = i.user_id
         WHERE i.case_id = CAST(:c AS uuid) AND i.removed_at IS NULL ORDER BY i.added_at, i.id
    """), {"c": str(case_id)})
    return [dict(r) for r in rows.mappings()]


async def _ctx(db: AsyncSession, token: TokenPayload, case: Mapping) -> tuple[frozenset[str], bool, dict]:
    """(what the caller holds, whether they are on the case, what they may do to it)."""
    held = await held_by(db, token.role_id, HELD)
    is_on = case_files.on_case(case, [i["user_id"] for i in await _investigators(db, case["id"])], token.user_id)
    return held, is_on, case_files.may(case, token.user_id, held, is_on)


async def _working(db: AsyncSession, token: TokenPayload, case_id: uuid.UUID, allowed, what: str = "work") -> tuple[dict, frozenset[str]]:
    """The case, locked, for somebody who may do this to it — or the refusal that says why not."""
    _a_person(token)
    case = await _case(db, case_id, allowed, lock=True)
    held, is_on, may = await _ctx(db, token, case)
    if not may[what]:
        if what == "work" and case["status"] == "OPEN":
            raise HTTPException(403, case_files.NOT_YOURS)
        if what == "approve_close" and case["status"] == "AWAITING_APPROVAL" and "case:manage" in held:
            raise HTTPException(409, case_files.TWO_PEOPLE)
        raise HTTPException(409, CLOSED if what in ("work", "assign", "request_close") else
                            "The case is not at the step this is done at.")
    return case, held


async def _may_work(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """One of the organisation's people who may be put on a case: in use, and holding `case:work`."""
    row = (await db.execute(text("""
        SELECT u.id, u.full_name FROM users u
         WHERE u.id = CAST(:u AS uuid) AND u.is_active AND EXISTS (
               SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = u.role_id AND p.code = 'case:work')
    """), {"u": str(user_id)})).mappings().first()
    if row is None:
        raise HTTPException(422, "That person cannot be put on a case: they are not in use, or may not work on cases.")
    return dict(row)


async def _entry(db: AsyncSession, case_id, kind: str, body: str | None, who) -> None:
    await db.execute(text("""
        INSERT INTO case_entries (tenant_id, case_id, kind, body, actor_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :kind, :body, CAST(:who AS uuid))
    """), {"c": str(case_id), "kind": kind, "body": body, "who": str(who)})


async def _detail(db: AsyncSession, case: Mapping, held, allowed, token: TokenPayload) -> dict:
    cid = {"c": str(case["id"])}
    investigators = await _investigators(db, case["id"])
    tasks = [dict(r) for r in (await db.execute(text("""
        SELECT t.id, t.title, t.detail, t.assigned_to_user_id, a.full_name AS assigned_to_name, t.due_at, t.state,
               t.created_at, c.full_name AS created_by_name, t.done_at, d.full_name AS done_by_name, t.done_note,
               t.dropped_reason
          FROM case_tasks t LEFT JOIN users a ON a.id = t.assigned_to_user_id
          LEFT JOIN users c ON c.id = t.created_by_user_id LEFT JOIN users d ON d.id = t.done_by_user_id
         WHERE t.case_id = CAST(:c AS uuid) ORDER BY t.created_at, t.id
    """), cid)).mappings()]
    links = [dict(r) for r in (await db.execute(text("""
        SELECT x.id, x.kind, x.ref_id, x.note, x.linked_at, u.full_name AS linked_by_name FROM case_links x
          LEFT JOIN users u ON u.id = x.linked_by_user_id
         WHERE x.case_id = CAST(:c AS uuid) AND x.removed_at IS NULL ORDER BY x.linked_at, x.id
    """), cid)).mappings()]
    parties = [dict(r) for r in (await db.execute(text("""
        SELECT x.id, x.kind, x.label, x.connection, x.note, x.added_at, u.full_name AS added_by_name FROM case_parties x
          LEFT JOIN users u ON u.id = x.added_by_user_id
         WHERE x.case_id = CAST(:c AS uuid) AND x.removed_at IS NULL ORDER BY x.added_at, x.id
    """), cid)).mappings()]
    entries = [dict(r) for r in (await db.execute(text("""
        SELECT e.id, e.kind, e.body, e.occurred_at, u.full_name AS actor_name FROM case_entries e
          LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.case_id = CAST(:c AS uuid) ORDER BY e.occurred_at, e.id
    """), cid)).mappings()]
    is_on = case_files.on_case(case, [i["user_id"] for i in investigators], token.user_id)
    may = case_files.may(case, token.user_id, held, is_on)
    mine = str(token.user_id)
    return {**{k: v for k, v in case.items() if k not in ("site_id", "site_name")},
            "site": {"id": case["site_id"], "name": case["site_name"]} if case["site_id"] else None,
            "status_label": case_files.STATUS_LABEL[case["status"]],
            "category_label": case_files.CATEGORY_LABEL[case["category"]],
            "investigators": investigators,
            # Somebody given a task may finish it, whether or not they are on the case.
            "tasks": [{**t, "may_finish": t["state"] == "OPEN" and case["status"] == "OPEN" and "case:work" in held
                       and (may["work"] or str(t["assigned_to_user_id"]) == mine)} for t in tasks],
            "tasks_open": case_files.unfinished(tasks),
            "links": await case_files.linked(db, links, held, allowed),
            "parties": [{**x, "connection_label": case_files.CONNECTION_LABEL[x["connection"]]} for x in parties],
            "entries": [{**e, "words": case_files.ENTRY_WORDS[e["kind"]]} for e in entries],
            "on_case": is_on, "may": may, "party_note": case_files.PARTY_NOTE, "two_people_note": case_files.TWO_PEOPLE}


async def _answer(db: AsyncSession, case_id, allowed, token: TokenPayload, held) -> dict:
    """The case as it now stands, read before the commit."""
    return await _detail(db, await _case(db, case_id, allowed), held, allowed, token)


@router.get("", dependencies=_READ)
async def list_cases(
    status: str | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    mine: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The cases the caller may read, the newest first. `mine` keeps to those
    they lead, investigate or have an open task on."""
    if status is not None and status not in case_files.STATUSES:
        raise HTTPException(422, f"status is one of {', '.join(case_files.STATUSES)}")
    limit, offset = clamp(limit, offset)
    params: dict = {"limit": limit, "offset": offset, "me": str(token.user_id)}
    where = []
    scope = site_scope_clause(allowed, "c.site_id", params)
    if scope:
        where.append(scope)
    if status:
        where.append("c.status = :status")
        params["status"] = status
    if site_id is not None:
        where.append("c.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if mine:
        where.append("""(c.lead_user_id = CAST(:me AS uuid)
            OR EXISTS (SELECT 1 FROM case_investigators i WHERE i.case_id = c.id AND i.user_id = CAST(:me AS uuid)
                          AND i.removed_at IS NULL)
            OR EXISTS (SELECT 1 FROM case_tasks t WHERE t.case_id = c.id AND t.assigned_to_user_id = CAST(:me AS uuid)
                          AND t.state = 'OPEN'))""")
    rows = (await db.execute(text(f"""
        SELECT c.id, c.case_number, c.title, c.category, c.priority, c.status, c.site_id, s.name AS site_name,
               c.lead_user_id, l.full_name AS lead_name, c.opened_at, c.closed_at, c.close_requested_at,
               (SELECT count(*) FROM case_tasks t WHERE t.case_id = c.id AND t.state = 'OPEN') AS tasks_open,
               (SELECT count(*) FROM case_links x WHERE x.case_id = c.id AND x.removed_at IS NULL) AS links,
               count(*) OVER () AS total
          FROM case_files c LEFT JOIN sites s ON s.id = c.site_id LEFT JOIN users l ON l.id = c.lead_user_id
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY c.opened_at DESC, c.id LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    held = await held_by(db, token.role_id, PERMISSIONS)
    return {"items": [{**{k: v for k, v in r.items() if k != "total"},
                       "status_label": case_files.STATUS_LABEL[r["status"]],
                       "category_label": case_files.CATEGORY_LABEL[r["category"]]} for r in rows],
            "total": rows[0]["total"] if rows else 0, "limit": limit, "offset": offset,
            "can_open": "case:work" in held, "can_manage": "case:manage" in held}


@router.get("/options", dependencies=_READ)
async def read_options(
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What a case may be, who may be put on one, and the recent records the
    caller may read that a case might be linked to."""
    held = await held_by(db, token.role_id, HELD)
    people = [dict(r) for r in (await db.execute(text("""
        SELECT u.id, u.full_name AS name FROM users u
         WHERE u.is_active AND EXISTS (SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                                        WHERE rp.role_id = u.role_id AND p.code = 'case:work')
         ORDER BY u.full_name, u.id
    """))).mappings()]
    recent: dict[str, list] = {}
    for kind, table, at in (("INCIDENT", "incidents", "created_at"), ("INVESTIGATION", "investigations", "opened_at"),
                            ("EVIDENCE_PACKAGE", "evidence_packages", "created_at")):
        ids = [r[0] for r in await db.execute(text(f"SELECT id FROM {table} ORDER BY {at} DESC LIMIT 40"))]
        found = await case_files.records(db, kind, ids, held, allowed)
        recent[kind] = [{"id": r["id"], "label": r["label"], "detail": r["detail"], "at": r["at"], "site_id": r["site_id"]}
                        for r in sorted(found.values(), key=lambda r: r["at"], reverse=True)
                        if site_id is None or r["site_id"] == site_id][:25]
    return {"categories": [{"key": k, "label": case_files.CATEGORY_LABEL[k]} for k in case_files.CATEGORIES],
            "priorities": list(case_files.PRIORITIES),
            "connections": [{"key": k, "label": case_files.CONNECTION_LABEL[k]} for k in case_files.CONNECTIONS],
            "link_kinds": [{"key": k, "label": case_files.LINK_LABEL[k], "needs": case_files.LINK_NEEDS[k],
                            "may": case_files.LINK_NEEDS[k] in held} for k in case_files.LINK_KINDS],
            "people": people, "recent": recent, "party_note": case_files.PARTY_NOTE}


class OpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    #: What the case is about, and why it is opened.
    summary: str = Field(..., min_length=1, max_length=8000)
    site_id: uuid.UUID | None = None
    category: Literal["THEFT", "TRESPASS", "DAMAGE", "SAFETY", "ACCESS", "OTHER"] = "OTHER"
    priority: Literal["LOW", "NORMAL", "HIGH"] = "NORMAL"
    #: Somebody else to lead it: for whoever manages cases to say. Otherwise whoever opens it leads it.
    lead_user_id: uuid.UUID | None = None
    #: The record it is opened from, which becomes its first link.
    from_kind: Literal["INCIDENT", "INVESTIGATION", "EVIDENCE_PACKAGE"] | None = None
    from_id: uuid.UUID | None = None


@router.post("", status_code=201, dependencies=_READ + _WORK)
async def open_case(
    body: OpenBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Open a case. Whoever opens it leads it, unless somebody who manages
    cases names another lead. Opened from a record, that record is its first
    link, and its site is the case's unless one is given."""
    _a_person(token)
    held = await held_by(db, token.role_id, HELD)
    title, summary = _words(body.title, "Give the case a title."), _words(body.summary, "Say what the case is about.")
    if (body.from_kind is None) != (body.from_id is None):
        raise HTTPException(422, "Say both what kind of record the case is opened from and which one.")
    first = None
    if body.from_kind is not None:
        first = await case_files.record(db, body.from_kind, body.from_id, held, allowed)
        if first is None:
            raise HTTPException(404, f"{case_files.LINK_LABEL[body.from_kind]} not found")
    site_id = body.site_id or (first["site_id"] if first else None)
    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(site_id)})).scalar()
        if not known or not is_site_allowed(allowed, site_id):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "Choose the site this case is about: you are assigned to certain sites and cannot "
                                 "open one that spans them all.")
    lead = token.user_id
    if body.lead_user_id is not None and str(body.lead_user_id) != str(token.user_id):
        if "case:manage" not in held:
            raise HTTPException(403, "Naming somebody else to lead a case is for whoever manages cases.")
        lead = (await _may_work(db, body.lead_user_id))["id"]
    number = await case_files.next_number(db)
    case_id = (await db.execute(text("""
        INSERT INTO case_files (tenant_id, site_id, case_number, title, summary, category, priority, lead_user_id,
                                opened_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :number, :title, :summary, :category,
                :priority, CAST(:lead AS uuid), CAST(:who AS uuid))
        RETURNING id
    """), {"site": str(site_id) if site_id else None, "number": number, "title": title, "summary": summary,
           "category": body.category, "priority": body.priority, "lead": str(lead), "who": str(token.user_id)})).scalar()
    await _entry(db, case_id, "OPENED", None, token.user_id)
    if first is not None:
        await db.execute(text("""
            INSERT INTO case_links (tenant_id, case_id, kind, ref_id, note, linked_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :kind, CAST(:ref AS uuid),
                    'What this case was opened from.', CAST(:who AS uuid))
        """), {"c": str(case_id), "kind": body.from_kind, "ref": str(body.from_id), "who": str(token.user_id)})
    await intel_audit.record(db, request, token, "case.open", "case", case_id, site_id=site_id,
                             detail={"number": number, "category": body.category, "from_kind": body.from_kind,
                                     "from_id": str(body.from_id) if body.from_id else None, "lead": str(lead)})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.get("/{case_id:uuid}", dependencies=_READ)
async def read_case(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One case: what it is about, who is on it, its tasks, its linked records
    as the caller may see each, who is named in it, its history, and what the
    caller may do to it."""
    case = await _case(db, case_id, allowed)
    return await _detail(db, case, await held_by(db, token.role_id, HELD), allowed, token)


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=8000)
    category: Literal["THEFT", "TRESPASS", "DAMAGE", "SAFETY", "ACCESS", "OTHER"] | None = None
    priority: Literal["LOW", "NORMAL", "HIGH"] | None = None


@router.patch("/{case_id:uuid}", dependencies=_READ + _WORK)
async def change_case(
    case_id: uuid.UUID,
    body: ChangeBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change a case's title, what it is about, its kind or its priority."""
    case, held = await _working(db, token, case_id, allowed)
    sets, params, changed = ["updated_at = now()"], {"id": str(case_id)}, []
    for field in ("title", "summary", "category", "priority"):
        if field in body.model_fields_set and getattr(body, field) is not None:
            value = getattr(body, field)
            if field in ("title", "summary"):
                value = _words(value, f"A case's {field} is not empty.")
            sets.append(f"{field} = :{field}")
            params[field] = value
            changed.append(field)
    if changed:
        await db.execute(text(f"UPDATE case_files SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"), params)
        await intel_audit.record(db, request, token, "case.update", "case", case_id, site_id=case["site_id"],
                                 detail={"changed": changed})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class PersonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


@router.put("/{case_id:uuid}/lead", dependencies=_READ + _MANAGE)
async def set_lead(
    case_id: uuid.UUID,
    body: PersonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say who leads a case."""
    case, held = await _working(db, token, case_id, allowed, "assign")
    person = await _may_work(db, body.user_id)
    await db.execute(text("UPDATE case_files SET lead_user_id = CAST(:u AS uuid), updated_at = now() WHERE id = CAST(:id AS uuid)"),
                     {"u": str(person["id"]), "id": str(case_id)})
    await _entry(db, case_id, "LEAD_SET", person["full_name"], token.user_id)
    await intel_audit.record(db, request, token, "case.lead", "case", case_id, site_id=case["site_id"],
                             detail={"lead": str(person["id"])})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/investigators", status_code=201, dependencies=_READ + _MANAGE)
async def add_investigator(
    case_id: uuid.UUID,
    body: PersonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put somebody on a case as an investigator."""
    case, held = await _working(db, token, case_id, allowed, "assign")
    person = await _may_work(db, body.user_id)
    if str(person["id"]) in {str(i["user_id"]) for i in await _investigators(db, case_id)}:
        raise HTTPException(409, "They are on the case already.")
    await db.execute(text("""
        INSERT INTO case_investigators (tenant_id, case_id, user_id, added_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), CAST(:u AS uuid), CAST(:who AS uuid))
    """), {"c": str(case_id), "u": str(person["id"]), "who": str(token.user_id)})
    await _entry(db, case_id, "INVESTIGATOR_ADDED", person["full_name"], token.user_id)
    await intel_audit.record(db, request, token, "case.investigator.add", "case", case_id, site_id=case["site_id"],
                             detail={"user_id": str(person["id"])})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/investigators/{user_id:uuid}/remove", dependencies=_READ + _MANAGE)
async def remove_investigator(
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Take an investigator off a case. That they were on it stays in its history."""
    case, held = await _working(db, token, case_id, allowed, "assign")
    name = (await db.execute(text("""
        UPDATE case_investigators i SET removed_at = now(), removed_by_user_id = CAST(:who AS uuid)
          FROM users u
         WHERE i.case_id = CAST(:c AS uuid) AND i.user_id = CAST(:u AS uuid) AND i.removed_at IS NULL AND u.id = i.user_id
        RETURNING u.full_name
    """), {"c": str(case_id), "u": str(user_id), "who": str(token.user_id)})).scalar()
    if name is None:
        raise HTTPException(404, "They are not on the case.")
    await _entry(db, case_id, "INVESTIGATOR_REMOVED", name, token.user_id)
    await intel_audit.record(db, request, token, "case.investigator.remove", "case", case_id, site_id=case["site_id"],
                             detail={"user_id": str(user_id)})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=8000)


@router.post("/{case_id:uuid}/notes", status_code=201, dependencies=_READ + _WORK)
async def add_note(
    case_id: uuid.UUID,
    body: NoteBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Add a note to a case, in one's own words. A note is not changed or removed afterwards."""
    case, held = await _working(db, token, case_id, allowed)
    await _entry(db, case_id, "NOTE", _words(body.body, "A note is not empty."), token.user_id)
    await intel_audit.record(db, request, token, "case.note", "case", case_id, site_id=case["site_id"], detail={})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class TaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    detail: str | None = Field(None, max_length=4000)
    assigned_to_user_id: uuid.UUID | None = None
    due_at: datetime | None = None


@router.post("/{case_id:uuid}/tasks", status_code=201, dependencies=_READ + _WORK)
async def add_task(
    case_id: uuid.UUID,
    body: TaskBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Add a task to a case, given to somebody or to nobody yet."""
    case, held = await _working(db, token, case_id, allowed)
    given = (await _may_work(db, body.assigned_to_user_id))["id"] if body.assigned_to_user_id else None
    task_id = (await db.execute(text("""
        INSERT INTO case_tasks (tenant_id, case_id, title, detail, assigned_to_user_id, due_at, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :title, :detail, CAST(:to AS uuid), :due,
                CAST(:who AS uuid))
        RETURNING id
    """), {"c": str(case_id), "title": _words(body.title, "Say what the task is."),
           "detail": (body.detail or "").strip() or None, "to": str(given) if given else None, "due": body.due_at,
           "who": str(token.user_id)})).scalar()
    await intel_audit.record(db, request, token, "case.task.add", "case", case_id, site_id=case["site_id"],
                             detail={"task_id": str(task_id), "assigned_to": str(given) if given else None})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class EndBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What was done, or why it is dropped.
    note: str | None = Field(None, max_length=4000)


async def _finish(db: AsyncSession, request: Request, token: TokenPayload, allowed, case_id: uuid.UUID,
                  task_id: uuid.UUID, state: str, note: str | None) -> dict:
    """Finish a task: done, or dropped with why. By whoever works on the case, or whoever the task was given to."""
    _a_person(token)
    case = await _case(db, case_id, allowed, lock=True)
    held, _, may = await _ctx(db, token, case)
    task = (await db.execute(text("SELECT id, state, assigned_to_user_id FROM case_tasks WHERE id = CAST(:t AS uuid) "
                                  "AND case_id = CAST(:c AS uuid)"), {"t": str(task_id), "c": str(case_id)})).mappings().first()
    if task is None:
        raise HTTPException(404, "Task not found")
    if case["status"] != "OPEN":
        raise HTTPException(409, CLOSED)
    if not (may["work"] or ("case:work" in held and str(task["assigned_to_user_id"]) == str(token.user_id))):
        raise HTTPException(403, case_files.NOT_YOURS)
    if task["state"] != "OPEN":
        raise HTTPException(409, "That task is finished already.")
    if state == "DONE":
        await db.execute(text("""
            UPDATE case_tasks SET state = 'DONE', done_at = now(), done_by_user_id = CAST(:who AS uuid), done_note = :note,
                   updated_at = now() WHERE id = CAST(:t AS uuid)
        """), {"t": str(task_id), "who": str(token.user_id), "note": (note or "").strip() or None})
    else:
        await db.execute(text("UPDATE case_tasks SET state = 'DROPPED', dropped_reason = :note, updated_at = now() "
                              "WHERE id = CAST(:t AS uuid)"),
                         {"t": str(task_id), "note": _words(note, "Say why the task is dropped.")})
    await intel_audit.record(db, request, token, f"case.task.{'done' if state == 'DONE' else 'drop'}", "case", case_id,
                             site_id=case["site_id"], detail={"task_id": str(task_id)})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/tasks/{task_id:uuid}/done", dependencies=_READ + _WORK)
async def finish_task(case_id: uuid.UUID, task_id: uuid.UUID, body: EndBody, request: Request,
                      db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Mark a task done, with what was done."""
    return await _finish(db, request, token, allowed, case_id, task_id, "DONE", body.note)


@router.post("/{case_id:uuid}/tasks/{task_id:uuid}/drop", dependencies=_READ + _WORK)
async def drop_task(case_id: uuid.UUID, task_id: uuid.UUID, body: EndBody, request: Request,
                    db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
                    allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Drop a task that will not be done, with why."""
    return await _finish(db, request, token, allowed, case_id, task_id, "DROPPED", body.note)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["INCIDENT", "INVESTIGATION", "EVIDENCE_PACKAGE"]
    ref_id: uuid.UUID
    note: str | None = Field(None, max_length=2000)


@router.post("/{case_id:uuid}/links", status_code=201, dependencies=_READ + _WORK)
async def add_link(
    case_id: uuid.UUID,
    body: LinkBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Link an incident, an investigation or an evidence package to a case —
    one the caller may read. It is referred to, not copied."""
    case, held = await _working(db, token, case_id, allowed)
    if await case_files.record(db, body.kind, body.ref_id, held, allowed) is None:
        raise HTTPException(404, f"{case_files.LINK_LABEL[body.kind]} not found")
    already = (await db.execute(text("SELECT 1 FROM case_links WHERE case_id = CAST(:c AS uuid) AND kind = :k "
                                     "AND ref_id = CAST(:r AS uuid) AND removed_at IS NULL"),
                                {"c": str(case_id), "k": body.kind, "r": str(body.ref_id)})).scalar()
    if already:
        raise HTTPException(409, "It is linked to the case already.")
    await db.execute(text("""
        INSERT INTO case_links (tenant_id, case_id, kind, ref_id, note, linked_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :k, CAST(:r AS uuid), :note,
                CAST(:who AS uuid))
    """), {"c": str(case_id), "k": body.kind, "r": str(body.ref_id), "note": (body.note or "").strip() or None,
           "who": str(token.user_id)})
    await intel_audit.record(db, request, token, "case.link.add", "case", case_id, site_id=case["site_id"],
                             detail={"kind": body.kind, "ref_id": str(body.ref_id)})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class RemoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


async def _remove(db: AsyncSession, request: Request, token: TokenPayload, allowed, case_id: uuid.UUID, table: str,
                  row_id: uuid.UUID, reason: str, what: str, action: str) -> dict:
    """Take a link or a name off a case, with why. The row stays; the record it referred to is untouched."""
    case, held = await _working(db, token, case_id, allowed)
    done = (await db.execute(text(f"""
        UPDATE {table} SET removed_at = now(), removed_by_user_id = CAST(:who AS uuid), remove_reason = :reason
         WHERE id = CAST(:id AS uuid) AND case_id = CAST(:c AS uuid) AND removed_at IS NULL RETURNING id
    """), {"id": str(row_id), "c": str(case_id), "who": str(token.user_id),
           "reason": _words(reason, "Say why it is taken off the case.")})).scalar()
    if done is None:
        raise HTTPException(404, f"{what} not found")
    await intel_audit.record(db, request, token, action, "case", case_id, site_id=case["site_id"],
                             detail={"id": str(row_id)})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/links/{link_id:uuid}/remove", dependencies=_READ + _WORK)
async def remove_link(case_id: uuid.UUID, link_id: uuid.UUID, body: RemoveBody, request: Request,
                      db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Take a link off a case, with why. The linked record is not touched."""
    return await _remove(db, request, token, allowed, case_id, "case_links", link_id, body.reason, "Link", "case.link.remove")


class PartyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["PERSON", "VEHICLE"]
    #: A person's name, or a vehicle's plate.
    label: str = Field(..., min_length=1, max_length=200)
    connection: Literal["REPORTED_IT", "WITNESS", "AFFECTED", "NAMED", "OTHER"]
    note: str | None = Field(None, max_length=2000)


@router.post("/{case_id:uuid}/parties", status_code=201, dependencies=_READ + _WORK)
async def add_party(
    case_id: uuid.UUID,
    body: PartyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Name a person or a vehicle in a case, with how it is connected. Being
    named is not an accusation, and nothing here names anybody by itself."""
    case, held = await _working(db, token, case_id, allowed)
    party_id = (await db.execute(text("""
        INSERT INTO case_parties (tenant_id, case_id, kind, label, connection, note, added_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:c AS uuid), :kind, :label, :connection, :note,
                CAST(:who AS uuid))
        RETURNING id
    """), {"c": str(case_id), "kind": body.kind, "label": _words(body.label, "Give the name or the plate."),
           "connection": body.connection, "note": (body.note or "").strip() or None, "who": str(token.user_id)})).scalar()
    await intel_audit.record(db, request, token, "case.party.add", "case", case_id, site_id=case["site_id"],
                             detail={"id": str(party_id), "kind": body.kind, "connection": body.connection})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/parties/{party_id:uuid}/remove", dependencies=_READ + _WORK)
async def remove_party(case_id: uuid.UUID, party_id: uuid.UUID, body: RemoveBody, request: Request,
                       db: AsyncSession = Depends(get_db_with_tenant), token: TokenPayload = Depends(get_token_payload),
                       allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Take a person or a vehicle off a case, with why."""
    return await _remove(db, request, token, allowed, case_id, "case_parties", party_id, body.reason,
                         "Person or vehicle", "case.party.remove")


class OutcomeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What was found, and what was done.
    outcome: str = Field(..., min_length=1, max_length=8000)


@router.post("/{case_id:uuid}/request-close", dependencies=_READ + _WORK)
async def request_close(
    case_id: uuid.UUID,
    body: OutcomeBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Ask for a case to be closed, saying what was found. Every task is done
    or dropped first. Somebody else approves."""
    case, held = await _working(db, token, case_id, allowed, "request_close")
    outcome = _words(body.outcome, "Say what was found and what was done.")
    still = (await db.execute(text("SELECT count(*) FROM case_tasks WHERE case_id = CAST(:c AS uuid) AND state = 'OPEN'"),
                              {"c": str(case_id)})).scalar()
    if still:
        raise HTTPException(409, f"{still} task{' is' if still == 1 else 's are'} still open. Finish or drop "
                                 f"{'it' if still == 1 else 'them'} first.")
    await db.execute(text("""
        UPDATE case_files SET status = 'AWAITING_APPROVAL', outcome = :outcome, close_requested_at = now(),
               close_requested_by_user_id = CAST(:who AS uuid), updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"id": str(case_id), "outcome": outcome, "who": str(token.user_id)})
    await _entry(db, case_id, "CLOSE_REQUESTED", outcome, token.user_id)
    await intel_audit.record(db, request, token, "case.close.request", "case", case_id, site_id=case["site_id"], detail={})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/approve-close", dependencies=_READ + _MANAGE)
async def approve_close(
    case_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Approve the closing of a case. Not by whoever asked for it. From then it is not changed."""
    case, held = await _working(db, token, case_id, allowed, "approve_close")
    # The history first: a closed case takes no more of it from here.
    await _entry(db, case_id, "CLOSE_APPROVED", None, token.user_id)
    await db.execute(text("""
        UPDATE case_files SET status = 'CLOSED', closed_at = now(), closed_by_user_id = CAST(:who AS uuid),
               updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"id": str(case_id), "who": str(token.user_id)})
    await intel_audit.record(db, request, token, "case.close.approve", "case", case_id, site_id=case["site_id"], detail={})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=4000)


@router.post("/{case_id:uuid}/decline-close", dependencies=_READ + _MANAGE)
async def decline_close(
    case_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Decline the closing of a case, with why. It is open again; what was
    written as found stays in its history."""
    case, held = await _working(db, token, case_id, allowed, "decline_close")
    reason = _words(body.reason, "Say why its closing is declined.")
    await db.execute(text("""
        UPDATE case_files SET status = 'OPEN', outcome = NULL, close_requested_at = NULL,
               close_requested_by_user_id = NULL, updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"id": str(case_id)})
    await _entry(db, case_id, "CLOSE_DECLINED", reason, token.user_id)
    await intel_audit.record(db, request, token, "case.close.decline", "case", case_id, site_id=case["site_id"], detail={})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


@router.post("/{case_id:uuid}/reopen", dependencies=_READ + _MANAGE)
async def reopen_case(
    case_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Reopen a closed case, with why. What it was closed with stays in its history."""
    case, held = await _working(db, token, case_id, allowed, "reopen")
    reason = _words(body.reason, "Say why the case is reopened.")
    await db.execute(text("""
        UPDATE case_files SET status = 'OPEN', outcome = NULL, close_requested_at = NULL,
               close_requested_by_user_id = NULL, closed_at = NULL, closed_by_user_id = NULL, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(case_id)})
    await _entry(db, case_id, "REOPENED", reason, token.user_id)
    await intel_audit.record(db, request, token, "case.reopen", "case", case_id, site_id=case["site_id"], detail={})
    out = await _answer(db, case_id, allowed, token, held)
    await db.commit()
    return out


async def _report(db: AsyncSession, request: Request, token: TokenPayload, allowed, case_id: uuid.UUID, form: str) -> tuple[dict, str, str]:
    """The case as a document's worth of record, for the caller, with who made it and when."""
    if token.support_session_id:
        raise HTTPException(403, "A case's report is taken by the organisation's own staff, not from a support session.")
    case = await _case(db, case_id, allowed)
    held = await held_by(db, token.role_id, HELD)
    detail = await _detail(db, case, held, allowed, token)
    zone = await intel_insight.zone_for(db, case["site_id"])
    made_by = (await db.execute(text("SELECT full_name FROM users WHERE id = CAST(:u AS uuid)"),
                                {"u": str(token.user_id)})).scalar() or "somebody no longer on the system"
    await intel_audit.record(db, request, token, "case.report", "case", case_id, site_id=case["site_id"],
                             detail={"form": form, "number": case["case_number"]})
    await db.commit()
    return detail, zone, made_by


@router.get("/{case_id:uuid}/report", dependencies=_READ)
async def read_report(case_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                      token: TokenPayload = Depends(get_token_payload),
                      allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """A case as a report: the whole of it, in order, as the caller may see it.
    That it was taken is written in the audit log."""
    detail, zone, made_by = await _report(db, request, token, allowed, case_id, "json")
    return {"case": detail, "timezone": zone, "made_by": made_by, "made_at": datetime.now(timezone.utc)}


@router.get("/{case_id:uuid}/report.pdf", dependencies=_READ)
async def read_report_pdf(case_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """The same report as a PDF file."""
    detail, zone, made_by = await _report(db, request, token, allowed, case_id, "pdf")
    content = case_files.report_pdf(detail, zone, made_by, datetime.now(timezone.utc))
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{detail["case_number"]}.pdf"'})
