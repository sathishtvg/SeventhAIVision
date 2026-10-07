"""Visitor and contractor authorisation: who said a visit may happen, for where, for how long, and with whom.

Phase 7 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
VISITOR_CONTRACTOR_SECURITY.md.

  GET  /                        the authorisations, searched: by site, where they stand, words, whose
  POST /                        ask for one, of a visit or of a contractor's work permit
  GET  /options                 what there is to ask about at a site: visits, permits, places, people
  GET  /mine                    the ones waiting for the caller's answer
  GET  /standing                what stands for a visit or a permit, in the sentences the gate reads
  GET  /to-review               door events outside what a visit is authorised for, not yet looked at
  GET  /{id}                    one, with its places and where the visitor's badge was used
  POST /{id}/approve|decline    the host's answer (or that of somebody who manages visits)
  POST /{id}/cancel|extend      withdraw it, or let it run longer — each with a reason
  PUT  /{id}/places|escort      where it is for, and who walks with the visitor
  POST /{id}/id-seen            the kind of document somebody saw. Never its number.
  POST /{id}/movements/{event}/review   what a person made of a door event

REGISTERING A VISITOR, CHECKING ONE IN AND APPROVING A WORK PERMIT ARE NOT
HERE. They are done through the endpoints that have always done them,
unchanged, and nothing in this router stops them: an authorisation is what the
guard reads before deciding, not a lock on the gate.

NOTHING HERE ACCUSES ANYBODY. A door event outside the places or the period a
visit is authorised for is listed for a person to look at
(services/visitor_authorization.py). No alert and no incident is raised.

EVERY CHANGE IS BY A PERSON WHO IS SIGNED IN, and is written to the audit log
with who made it.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, response_notify
from app.services import visitor_authorization as authorisation

REQUESTED_EVENT = "visitor_authorization_requested"
DECIDED_EVENT = "visitor_authorization_decided"
PERMISSIONS = ("visitorauth:read", "visitorauth:write", "visitorauth:manage")
#: Where an authorisation stands while it can still be changed.
OPEN = ("AWAITING_HOST", "NOT_YET_VALID", "VALID")
WITHDRAWN_BY_ASKING_AGAIN = "Not answered before the time it was asked for had passed. Asked for again."

router = APIRouter(prefix="/api/v1/visitor-authorizations", tags=["visitor-authorizations"])
_READ = [Depends(require_permission("visitorauth:read"))]
_WRITE = [Depends(require_permission("visitorauth:write"))]
_MANAGE = [Depends(require_permission("visitorauth:manage"))]


def _a_person(token: TokenPayload) -> None:
    """Who asked, who said yes and who saw the ID are people, by name."""
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


def _aware(moment: datetime | None, what: str) -> None:
    if moment is not None and moment.tzinfo is None:
        raise HTTPException(422, f"Say {what} with a time zone.")


# ─── One authorisation, as it is given ───────────────────────────────────────

_AUTH = """
    SELECT a.id, a.site_id, s.name AS site_name, t.timezone, a.visitor_id, a.work_permit_id, a.purpose, a.state,
           a.valid_from, a.valid_until, a.host_user_id, h.full_name AS host_name,
           a.escort_required, a.escort_user_id, es.full_name AS escort_name, a.escort_note,
           a.id_document_kind, a.id_checked_at, ic.full_name AS id_checked_by_name,
           a.requested_by_user_id, rq.full_name AS requested_by_name, a.requested_at,
           a.decided_by_user_id, dc.full_name AS decided_by_name, a.decided_at, a.decision_note,
           a.extended_at, ex.full_name AS extended_by_name, a.extend_reason,
           a.cancelled_at, cn.full_name AS cancelled_by_name, a.cancel_reason, a.updated_at,
           v.full_name AS visitor_name, v.company AS visitor_company, v.status AS visit_status, v.visit_type,
           wp.permit_number, wp.work_description, wp.status AS permit_status, wp.workers_count,
           c.company_name AS contractor_name,
           NOT EXISTS (SELECT 1 FROM visitor_authorizations n
                        WHERE (n.visitor_id = a.visitor_id OR n.work_permit_id = a.work_permit_id)
                          AND (n.requested_at, n.id) > (a.requested_at, a.id)) AS is_latest
      FROM visitor_authorizations a
      JOIN sites s ON s.id = a.site_id
      JOIN tenants t ON t.id = a.tenant_id
      LEFT JOIN visitors v ON v.id = a.visitor_id
      LEFT JOIN work_permits wp ON wp.id = a.work_permit_id
      LEFT JOIN contractors c ON c.id = wp.contractor_id
      LEFT JOIN users h ON h.id = a.host_user_id
      LEFT JOIN users es ON es.id = a.escort_user_id
      LEFT JOIN users ic ON ic.id = a.id_checked_by_user_id
      LEFT JOIN users rq ON rq.id = a.requested_by_user_id
      LEFT JOIN users dc ON dc.id = a.decided_by_user_id
      LEFT JOIN users ex ON ex.id = a.extended_by_user_id
      LEFT JOIN users cn ON cn.id = a.cancelled_by_user_id
"""


def _subject(row: Mapping) -> dict:
    """Whose authorisation it is: a visit, or a contractor's work permit. A
    visitor's ID number is on the visit and is not given here."""
    if row["visitor_id"] is not None:
        return {"kind": "visit", "id": row["visitor_id"], "name": row["visitor_name"],
                "company": row["visitor_company"], "detail": row["visit_type"], "status": row["visit_status"]}
    number = f"Permit {row['permit_number']}: " if row["permit_number"] else ""
    return {"kind": "work_permit", "id": row["work_permit_id"], "name": row["contractor_name"],
            "company": row["contractor_name"], "detail": f"{number}{row['work_description'] or ''}".strip(),
            "status": row["permit_status"], "workers_count": row["workers_count"]}


def _theirs(row: Mapping, me: str) -> tuple[bool, bool]:
    """Whether the caller is the host, and whether they are who asked."""
    return str(row["host_user_id"]) == me, str(row["requested_by_user_id"]) == me


def _may(row: Mapping, where: str, me: str, held: frozenset[str]) -> dict:
    host, asked = _theirs(row, me)
    manage, write = "visitorauth:manage" in held, "visitorauth:write" in held
    theirs = manage or host or (asked and row["state"] == "REQUESTED")
    return {
        "approve": where == "AWAITING_HOST" and (host or manage),
        "decline": where == "AWAITING_HOST" and (host or manage),
        "cancel": where in OPEN + ("LAPSED",) and theirs,
        "extend": row["state"] == "APPROVED" and bool(row["is_latest"]) and (host or manage),
        "places": where in OPEN and theirs,
        "escort": where in OPEN and write,
        "id_seen": where in OPEN and write,
        "review_movements": manage,
    }


def _out(row: Mapping, now: datetime, places: Sequence[Mapping], me: str, held: frozenset[str]) -> dict:
    where = authorisation.standing(row, now)
    host, asked = _theirs(row, me)
    kept = ("id", "site_id", "site_name", "timezone", "purpose", "state", "valid_from", "valid_until", "host_user_id",
            "host_name", "escort_required", "escort_user_id", "escort_name", "escort_note", "id_document_kind",
            "id_checked_at", "id_checked_by_name", "requested_by_user_id", "requested_by_name", "requested_at",
            "decided_by_name", "decided_at", "decision_note", "extended_at", "extended_by_name", "extend_reason",
            "cancelled_at", "cancelled_by_name", "cancel_reason", "updated_at", "is_latest")
    return {**{k: row[k] for k in kept}, "subject": _subject(row), "standing": where,
            "says": authorisation.says(row, now, places=[p["name"] for p in places], timezone=row["timezone"]),
            "places": [dict(p) for p in places], "asked_of_me": host, "asked_by_me": asked,
            "may": _may(row, where, me, held)}


def _may_read(row: Mapping, allowed, me: str) -> bool:
    """An authorisation is read at the sites the reader may see, and by whoever
    is named on it: the host, who asked, the escort."""
    return is_site_allowed(allowed, row["site_id"]) or me in (
        str(row["host_user_id"]), str(row["requested_by_user_id"]), str(row["escort_user_id"]))


async def _one(db: AsyncSession, authorization_id, allowed, me: str, *, lock: bool = False) -> dict:
    row = (await db.execute(text(f"{_AUTH} WHERE a.id = CAST(:id AS uuid){' FOR UPDATE OF a' if lock else ''}"),
                            {"id": str(authorization_id)})).mappings().first()
    if row is None or not _may_read(row, allowed, me):
        raise HTTPException(404, "Authorisation not found")
    return dict(row)


async def _given(db: AsyncSession, authorization_id, allowed, token: TokenPayload, held: frozenset[str],
                 now: datetime) -> dict:
    """An authorisation as it now is. Read before the commit, like everything else."""
    row = await _one(db, authorization_id, allowed, token.user_id)
    places = (await authorisation.places_of(db, [row["id"]]))[str(row["id"])]
    return _out(row, now, places, token.user_id, held)


async def _can_answer(db: AsyncSession, user_id: uuid.UUID, as_what: str) -> dict:
    """Somebody named as host or escort is one of the organisation's people,
    still working, and able to read the authorisation they are named on."""
    row = (await db.execute(text("""
        SELECT u.id, u.full_name, u.is_active,
               EXISTS (SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                        WHERE rp.role_id = u.role_id AND p.code = 'visitorauth:read') AS reads
          FROM users u WHERE u.id = CAST(:id AS uuid)
    """), {"id": str(user_id)})).mappings().first()
    if row is None or not row["is_active"]:
        raise HTTPException(422, f"The {as_what} named is not one of the organisation's people.")
    if not row["reads"]:
        raise HTTPException(422, f"{row['full_name']} cannot read visitor authorisations, so cannot be the {as_what}.")
    return dict(row)


async def _places_at(db: AsyncSession, site_id, place_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(place_ids))
    if not wanted:
        return []
    known = {r.id for r in await db.execute(text(
        "SELECT id FROM site_places WHERE id = ANY(:ids) AND site_id = :site AND is_active"),
        {"ids": wanted, "site": site_id})}
    if len(known) != len(wanted):
        raise HTTPException(422, "A place named is not an active place of this site.")
    return wanted


async def _set_places(db: AsyncSession, authorization_id, place_ids: Sequence[uuid.UUID]) -> None:
    await db.execute(text("DELETE FROM visitor_authorization_places WHERE authorization_id = :a"),
                     {"a": authorization_id})
    if place_ids:
        await db.execute(text("""
            INSERT INTO visitor_authorization_places (tenant_id, authorization_id, place_id)
            SELECT current_setting('app.current_tenant')::uuid, :a, p FROM unnest(CAST(:places AS uuid[])) AS p
        """), {"a": authorization_id, "places": list(place_ids)})


async def _tell(request: Request, token: TokenPayload, event: str, answer: Mapping, people: Sequence, words: str) -> None:
    """After the commit: the organisation's screens, and the phones of the people it concerns."""
    redis = getattr(request.app.state, "redis", None)
    await response_notify.announce(redis, token.tenant_id, event, {
        "authorization_id": str(answer["id"]), "site_id": str(answer["site_id"]), "site_name": answer["site_name"],
        "subject_name": answer["subject"]["name"], "state": answer["state"], "host_user_id": answer["host_user_id"]})
    await response_notify.push(redis, token.tenant_id,
                               [p for p in dict.fromkeys(people) if p is not None and str(p) != token.user_id],
                               words, {"type": event, "authorization_id": str(answer["id"])})


# ─── Reading ─────────────────────────────────────────────────────────────────

@router.get("", dependencies=_READ)
async def list_authorizations(
    site_id: uuid.UUID | None = Query(None),
    standing: list[str] = Query(default=[]),
    subject: Literal["visit", "work_permit"] | None = Query(None),
    whose: Literal["asked_of_me", "asked_by_me"] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    history: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Authorisations, newest first: at the sites the caller may see, and the
    ones the caller is named on. Only the latest of each visit unless `history`
    is asked for."""
    unknown = [s for s in standing if s not in authorisation.STANDINGS[1:]]
    if unknown:
        raise HTTPException(422, f"Unknown standing '{unknown[0]}'.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    now = datetime.now(timezone.utc)
    cap, skip = clamp(limit, offset)
    params: dict = {"limit": cap + 1, "offset": skip}
    where = []
    scope = site_scope_clause(allowed, "a.site_id", params)
    if scope:
        # Somebody held to particular sites still reads the ones they are named on.
        where.append(f"({scope} OR CAST(:me AS uuid) IN (a.host_user_id, a.requested_by_user_id, a.escort_user_id))")
    if scope or whose is not None:
        params["me"] = token.user_id
    if site_id is not None:
        where.append("a.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if standing:
        where.append(f"({authorisation.STANDING_SQL}) = ANY(:standing)")
        params.update(standing=standing, now=now)
    if subject is not None:
        where.append("a.visitor_id IS NOT NULL" if subject == "visit" else "a.work_permit_id IS NOT NULL")
    if whose is not None:
        where.append(f"a.{'host_user_id' if whose == 'asked_of_me' else 'requested_by_user_id'} = CAST(:me AS uuid)")
    if q and q.strip():
        # The words as typed: a percent sign or an underscore is looked for, not treated as a wildcard.
        params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(v.full_name ILIKE :q ESCAPE '\\' OR v.company ILIKE :q ESCAPE '\\' "
                     "OR c.company_name ILIKE :q ESCAPE '\\' OR wp.permit_number ILIKE :q ESCAPE '\\')")
    rows = (await db.execute(text(f"""SELECT * FROM ({_AUTH}
         {('WHERE ' + ' AND '.join(where)) if where else ''}) x
         {'' if history else 'WHERE x.is_latest'}
         ORDER BY x.requested_at DESC, x.id LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    held = await _held(db, token.role_id)
    places = await authorisation.places_of(db, [r["id"] for r in rows[:cap]])
    return {"items": [_out(r, now, places[str(r["id"])], token.user_id, held) for r in rows[:cap]],
            "limit": cap, "offset": skip, "has_more": len(rows) > cap,
            "can_ask": "visitorauth:write" in held, "can_manage": "visitorauth:manage" in held}


@router.get("/options", dependencies=_READ + _WRITE)
async def what_can_be_asked(
    site_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What there is to ask about at a site: the visits and work permits that
    are not over, the site's places, and the people who can be a host or an
    escort."""
    if not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    site = {"site": str(site_id)}
    visits = (await db.execute(text("""
        SELECT v.id, v.full_name AS name, v.company, v.status, v.visit_type, v.expected_from, v.expected_until,
               v.host_user_id, COALESCE(u.full_name, v.host_name) AS host_name, v.purpose, v.site_id
          FROM visitors v LEFT JOIN users u ON u.id = v.host_user_id
         WHERE v.is_active AND v.status IN ('pending', 'arrived')
           AND (v.site_id = CAST(:site AS uuid) OR v.site_id IS NULL)
         ORDER BY v.expected_from DESC NULLS LAST, v.created_at DESC LIMIT 200
    """), site)).mappings().all()
    permits = (await db.execute(text("""
        SELECT wp.id, c.company_name AS name, wp.permit_number, wp.work_description, wp.status, wp.start_at, wp.end_at,
               wp.workers_count
          FROM work_permits wp JOIN contractors c ON c.id = wp.contractor_id
         WHERE wp.site_id = CAST(:site AS uuid) AND wp.status IN ('pending', 'approved', 'active')
         ORDER BY wp.start_at DESC LIMIT 200
    """), site)).mappings().all()
    places = (await db.execute(text("""
        SELECT p.id, p.name, p.kind, b.name AS part_of
          FROM site_places p LEFT JOIN site_places b ON b.id = p.parent_id
         WHERE p.site_id = CAST(:site AS uuid) AND p.is_active ORDER BY p.kind, p.name
    """), site)).mappings().all()
    people = (await db.execute(text("""
        SELECT u.id, u.full_name AS name
          FROM users u
         WHERE u.is_active AND EXISTS (
               SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = u.role_id AND p.code = 'visitorauth:read')
         ORDER BY u.full_name LIMIT 500
    """))).mappings().all()
    return {"visits": [dict(r) for r in visits], "permits": [dict(r) for r in permits],
            "places": [dict(r) for r in places], "people": [dict(r) for r in people],
            "id_kinds": list(authorisation.ID_KINDS)}


@router.get("/mine", dependencies=_READ)
async def waiting_for_me(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The authorisations waiting for the caller's answer: the ones they are
    the host of and — for somebody who manages visits — the ones that name no
    host, at their sites. Oldest first: whoever has waited longest."""
    held = await _held(db, token.role_id)
    now = datetime.now(timezone.utc)
    params: dict = {"me": token.user_id, "now": now}
    mine = "a.host_user_id = CAST(:me AS uuid)"
    if "visitorauth:manage" in held:
        scope = site_scope_clause(allowed, "a.site_id", params)
        mine = f"({mine} OR (a.host_user_id IS NULL{(' AND ' + scope) if scope else ''}))"
    rows = (await db.execute(text(f"""{_AUTH}
         WHERE a.state = 'REQUESTED' AND a.valid_until >= :now AND {mine}
         ORDER BY a.requested_at LIMIT 50
    """), params)).mappings().all()
    places = await authorisation.places_of(db, [r["id"] for r in rows])
    return {"items": [_out(r, now, places[str(r["id"])], token.user_id, held) for r in rows]}


@router.get("/standing", dependencies=_READ)
async def what_stands(
    visitor_id: uuid.UUID | None = Query(None),
    work_permit_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What stands on the record for a visit or a work permit, in the sentences
    a guard at the gate reads. It informs: it admits nobody and refuses nobody."""
    if (visitor_id is None) == (work_permit_id is None):
        raise HTTPException(422, "Name a visit or a work permit: one of the two.")
    column, table = ("visitor_id", "visitors") if visitor_id is not None else ("work_permit_id", "work_permits")
    subject = {"id": str(visitor_id or work_permit_id)}
    row = (await db.execute(text(f"""{_AUTH}
         WHERE a.{column} = CAST(:id AS uuid) ORDER BY a.requested_at DESC, a.id DESC LIMIT 1
    """), subject)).mappings().first()
    now = datetime.now(timezone.utc)
    if row is None:
        if not (await db.execute(text(f"SELECT 1 FROM {table} WHERE id = CAST(:id AS uuid)"), subject)).scalar():
            raise HTTPException(404, "Not found")
        return {"standing": "NOT_ASKED", "says": authorisation.says(None, now), "authorization": None,
                "note": authorisation.GATE_NOTE}
    if not _may_read(row, allowed, token.user_id):
        raise HTTPException(404, "Authorisation not found")
    held = await _held(db, token.role_id)
    places = (await authorisation.places_of(db, [row["id"]]))[str(row["id"])]
    answer = _out(row, now, places, token.user_id, held)
    return {"standing": answer["standing"], "says": answer["says"], "authorization": answer,
            "note": authorisation.GATE_NOTE}


@router.get("/to-review", dependencies=_READ + _MANAGE)
async def movements_to_review(
    days: int = Query(7, ge=1, le=90),
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Door events of visitors' badges outside the places or the period their
    visit was authorised for, that nobody has looked at yet. Something to look
    at, not findings."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    params: dict = {"since": datetime.now(timezone.utc) - timedelta(days=days)}
    where = ["a.state = 'APPROVED'", "a.visitor_id IS NOT NULL", "a.valid_until >= :since"]
    scope = site_scope_clause(allowed, "a.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("a.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"""{_AUTH} WHERE {' AND '.join(where)}
         ORDER BY a.valid_until DESC LIMIT 200"""), params)).mappings().all()
    of = {str(r["id"]): r for r in rows}
    events = await authorisation.door_events(db, [r["id"] for r in rows])
    items = [{**e, "subject_name": of[str(e["authorization_id"])]["visitor_name"],
              "site_name": of[str(e["authorization_id"])]["site_name"],
              "valid_from": of[str(e["authorization_id"])]["valid_from"],
              "valid_until": of[str(e["authorization_id"])]["valid_until"]}
             for e in events if e["to_look_at"] and e["review"] is None]
    return {"items": sorted(items, key=lambda e: e["occurred_at"], reverse=True), "days": days,
            "note": authorisation.MOVEMENT_NOTE}


@router.get("/{authorization_id:uuid}", dependencies=_READ)
async def read_authorization(
    authorization_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One authorisation: what stands, its places and — for somebody who
    manages visits — where the badge the visitor was given was used."""
    held = await _held(db, token.role_id)
    now = datetime.now(timezone.utc)
    row = await _one(db, authorization_id, allowed, token.user_id)
    places = (await authorisation.places_of(db, [row["id"]]))[str(row["id"])]
    seen = await authorisation.movements(db, row, places) if "visitorauth:manage" in held else None
    return {**_out(row, now, places, token.user_id, held), "movements": seen, "note": authorisation.GATE_NOTE}


# ─── Asking ──────────────────────────────────────────────────────────────────

class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: uuid.UUID | None = None
    work_permit_id: uuid.UUID | None = None
    #: Only for a visit that names no site of its own.
    site_id: uuid.UUID | None = None
    host_user_id: uuid.UUID | None = None
    purpose: str | None = Field(None, max_length=2000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    escort_required: StrictBool = False
    escort_user_id: uuid.UUID | None = None
    escort_note: str | None = Field(None, max_length=500)
    place_ids: list[uuid.UUID] = Field(default_factory=list, max_length=authorisation.MAX_PLACES)


@router.post("", status_code=201, dependencies=_READ + _WRITE)
async def ask(
    body: AskBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Ask for a visit, or a contractor's work permit, to be authorised. The
    host says yes or no; when no host is named, somebody who manages visits
    does. Left out, the host and the period are the visit's own."""
    _a_person(token)
    if (body.visitor_id is None) == (body.work_permit_id is None):
        raise HTTPException(422, "Name a visit or a work permit: one of the two.")
    _aware(body.valid_from, "when it starts")
    _aware(body.valid_until, "when it runs out")
    now = datetime.now(timezone.utc)
    if body.visitor_id is not None:
        column, subject_id, what = "visitor_id", body.visitor_id, "visit"
        subject = (await db.execute(text("""
            SELECT site_id, host_user_id, purpose, expected_from AS starts, expected_until AS ends,
                   (is_active AND status IN ('pending', 'arrived')) AS open
              FROM visitors WHERE id = CAST(:id AS uuid)
        """), {"id": str(subject_id)})).mappings().first()
    else:
        column, subject_id, what = "work_permit_id", body.work_permit_id, "work permit"
        subject = (await db.execute(text("""
            SELECT site_id, NULL::uuid AS host_user_id, work_description AS purpose, start_at AS starts, end_at AS ends,
                   status IN ('pending', 'approved', 'active') AS open
              FROM work_permits WHERE id = CAST(:id AS uuid)
        """), {"id": str(subject_id)})).mappings().first()
    if subject is None:
        raise HTTPException(404, f"{what.capitalize()} not found")
    site_id = subject["site_id"] or body.site_id
    if site_id is None:
        raise HTTPException(422, f"This {what} names no site. Say which site the authorisation is for.")
    if body.site_id is not None and subject["site_id"] is not None and subject["site_id"] != body.site_id:
        raise HTTPException(422, f"This {what} is at another site.")
    known = (await db.execute(text("SELECT 1 FROM sites WHERE id = :s"), {"s": site_id})).scalar()
    if not known or not is_site_allowed(allowed, site_id):
        raise HTTPException(404, f"{what.capitalize()} not found")
    if not subject["open"]:
        raise HTTPException(409, f"This {what} is over. There is nothing to authorise.")
    valid_from = body.valid_from or subject["starts"] or now
    valid_until = body.valid_until or subject["ends"]
    if valid_until is None:
        raise HTTPException(422, "Say until when the authorisation is asked for.")
    if valid_until <= valid_from:
        raise HTTPException(422, "An authorisation runs out after it starts.")
    if valid_until <= now:
        raise HTTPException(422, "The time asked for has already passed.")
    host_id = body.host_user_id or subject["host_user_id"]
    if host_id is not None:
        await _can_answer(db, host_id, "host")
    if body.escort_user_id is not None:
        if not body.escort_required:
            raise HTTPException(422, "An escort is named but none is asked for.")
        await _can_answer(db, body.escort_user_id, "escort")
    place_ids = await _places_at(db, site_id, body.place_ids)

    # One at a time for a visit: whoever asks second sees what the first one did.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('visitorauth:' || :subject))"),
                     {"subject": str(subject_id)})
    latest = (await db.execute(text(f"""
        SELECT id, state, valid_from, valid_until FROM visitor_authorizations
         WHERE {column} = CAST(:id AS uuid) ORDER BY requested_at DESC, id DESC LIMIT 1
    """), {"id": str(subject_id)})).mappings().first()
    stands = authorisation.standing(latest, now)
    if stands == "AWAITING_HOST":
        raise HTTPException(409, f"An authorisation of this {what} is already waiting for an answer.")
    if stands in ("NOT_YET_VALID", "VALID"):
        raise HTTPException(409, f"This {what} is already authorised. Extend that authorisation, or cancel it and "
                                 "ask again.")
    if stands == "LAPSED":
        await db.execute(text("""
            UPDATE visitor_authorizations
               SET state = 'CANCELLED', cancelled_by_user_id = CAST(:who AS uuid), cancelled_at = now(),
                   cancel_reason = :why, updated_at = now()
             WHERE id = :id
        """), {"who": token.user_id, "why": WITHDRAWN_BY_ASKING_AGAIN, "id": latest["id"]})
    new_id = (await db.execute(text("""
        INSERT INTO visitor_authorizations
               (tenant_id, site_id, visitor_id, work_permit_id, purpose, host_user_id, valid_from, valid_until,
                escort_required, escort_user_id, escort_note, requested_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :site, :visitor, :permit, :purpose, :host, :a, :b,
                :escort, :escort_user, :escort_note, CAST(:who AS uuid))
        RETURNING id
    """), {"site": site_id, "visitor": body.visitor_id, "permit": body.work_permit_id,
           "purpose": (body.purpose or "").strip() or subject["purpose"], "host": host_id, "a": valid_from,
           "b": valid_until, "escort": body.escort_required, "escort_user": body.escort_user_id,
           "escort_note": (body.escort_note or "").strip() or None, "who": token.user_id})).scalar()
    await _set_places(db, new_id, place_ids)
    await intel_audit.record(db, request, token, "visitorauth.request", "visitor_authorization", new_id,
                             site_id=site_id, detail={"subject": what, "subject_id": str(subject_id),
                                                      "host_user_id": str(host_id) if host_id else None,
                                                      "places": len(place_ids),
                                                      "asked_again": stands in ("LAPSED", "EXPIRED", "DECLINED",
                                                                                "CANCELLED")})
    held = await _held(db, token.role_id)
    answer = await _given(db, new_id, allowed, token, held, now)
    await db.commit()
    await _tell(request, token, REQUESTED_EVENT, answer, [host_id],
                f"{answer['subject']['name']} at {answer['site_name']}: your answer is asked for.")
    return answer


# ─── The answer ──────────────────────────────────────────────────────────────

class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=2000)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


async def _decide(db, request, token, allowed, authorization_id, state: str, note: str | None) -> dict:
    _a_person(token)
    held = await _held(db, token.role_id)
    row = await _one(db, authorization_id, allowed, token.user_id, lock=True)
    now = datetime.now(timezone.utc)
    stands = authorisation.standing(row, now)
    if stands == "LAPSED":
        raise HTTPException(409, "The time it was asked for has passed. It has to be asked for again.")
    if stands != "AWAITING_HOST":
        raise HTTPException(409, "This has already been answered.")
    host, _ = _theirs(row, token.user_id)
    if not (host or "visitorauth:manage" in held):
        raise HTTPException(403, "The answer is the host's, or that of somebody who manages visits.")
    await db.execute(text("""
        UPDATE visitor_authorizations
           SET state = :state, decided_by_user_id = CAST(:who AS uuid), decided_at = now(), decision_note = :note,
               updated_at = now()
         WHERE id = :id
    """), {"state": state, "who": token.user_id, "note": note, "id": row["id"]})
    action = "visitorauth.approve" if state == "APPROVED" else "visitorauth.decline"
    await intel_audit.record(db, request, token, action, "visitor_authorization", row["id"], site_id=row["site_id"],
                             detail={"as_host": host, "valid_until": row["valid_until"].isoformat()})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    word = "approved" if state == "APPROVED" else "declined"
    await _tell(request, token, DECIDED_EVENT, answer, [row["requested_by_user_id"]],
                f"{answer['subject']['name']} at {answer['site_name']}: {word} by {answer['decided_by_name']}.")
    return answer


@router.post("/{authorization_id:uuid}/approve", dependencies=_READ)
async def approve(
    authorization_id: uuid.UUID,
    request: Request,
    body: ApproveBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say yes. By the host, or by somebody who manages visits. It is valid for
    the period that was asked for; it lets nobody in by itself."""
    note = ((body.note if body else None) or "").strip() or None
    return await _decide(db, request, token, allowed, authorization_id, "APPROVED", note)


@router.post("/{authorization_id:uuid}/decline", dependencies=_READ)
async def decline(
    authorization_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say no, and why. By the host, or by somebody who manages visits."""
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is declined.")
    return await _decide(db, request, token, allowed, authorization_id, "DECLINED", body.reason.strip())


@router.post("/{authorization_id:uuid}/cancel", dependencies=_READ)
async def cancel(
    authorization_id: uuid.UUID,
    body: ReasonBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Withdraw an authorisation, and say why. By the host, by somebody who
    manages visits, or — while it is unanswered — by whoever asked."""
    _a_person(token)
    held = await _held(db, token.role_id)
    row = await _one(db, authorization_id, allowed, token.user_id, lock=True)
    now = datetime.now(timezone.utc)
    stands = authorisation.standing(row, now)
    if stands not in OPEN + ("LAPSED",):
        raise HTTPException(409, "There is nothing standing to cancel.")
    if not _may(row, stands, token.user_id, held)["cancel"]:
        raise HTTPException(403, "It is cancelled by the host, by somebody who manages visits, or — while it is "
                                 "unanswered — by whoever asked.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is cancelled.")
    await db.execute(text("""
        UPDATE visitor_authorizations
           SET state = 'CANCELLED', cancelled_by_user_id = CAST(:who AS uuid), cancelled_at = now(),
               cancel_reason = :why, updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "why": body.reason.strip(), "id": row["id"]})
    await intel_audit.record(db, request, token, "visitorauth.cancel", "visitor_authorization", row["id"],
                             site_id=row["site_id"], detail={"was": row["state"], "stood": stands})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    await _tell(request, token, DECIDED_EVENT, answer, [row["requested_by_user_id"], row["host_user_id"]],
                f"{answer['subject']['name']} at {answer['site_name']}: the authorisation was cancelled.")
    return answer


class ExtendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_until: datetime
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/{authorization_id:uuid}/extend", dependencies=_READ)
async def extend(
    authorization_id: uuid.UUID,
    body: ExtendBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Let an approved authorisation run longer, and say why. By the host, or
    by somebody who manages visits. One that has run out can be extended; one
    that a newer authorisation has replaced cannot."""
    _a_person(token)
    _aware(body.valid_until, "until when")
    held = await _held(db, token.role_id)
    row = await _one(db, authorization_id, allowed, token.user_id, lock=True)
    now = datetime.now(timezone.utc)
    if row["state"] != "APPROVED":
        raise HTTPException(409, "Only an authorisation that was approved can be extended.")
    if not row["is_latest"]:
        raise HTTPException(409, "A newer authorisation of this visit stands. Extend that one.")
    host, _ = _theirs(row, token.user_id)
    if not (host or "visitorauth:manage" in held):
        raise HTTPException(403, "It is extended by the host, or by somebody who manages visits.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is extended.")
    if body.valid_until <= row["valid_until"] or body.valid_until <= now:
        raise HTTPException(422, "An extension runs out later than it does now, and in the future.")
    await db.execute(text("""
        UPDATE visitor_authorizations
           SET valid_until = :until, extended_by_user_id = CAST(:who AS uuid), extended_at = now(),
               extend_reason = :why, updated_at = now()
         WHERE id = :id
    """), {"until": body.valid_until, "who": token.user_id, "why": body.reason.strip(), "id": row["id"]})
    await intel_audit.record(db, request, token, "visitorauth.extend", "visitor_authorization", row["id"],
                             site_id=row["site_id"], detail={"was": row["valid_until"].isoformat(),
                                                             "now": body.valid_until.isoformat()})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    return answer


# ─── Where it is for, with whom, and the ID ──────────────────────────────────

async def _open(db, token, allowed, authorization_id) -> tuple[dict, frozenset[str], datetime, str]:
    """An authorisation that can still be changed, locked for the change."""
    _a_person(token)
    held = await _held(db, token.role_id)
    row = await _one(db, authorization_id, allowed, token.user_id, lock=True)
    now = datetime.now(timezone.utc)
    stands = authorisation.standing(row, now)
    if stands not in OPEN:
        raise HTTPException(409, "This authorisation is over. It stands as it was.")
    return row, held, now, stands


class PlacesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place_ids: list[uuid.UUID] = Field(..., max_length=authorisation.MAX_PLACES)


@router.put("/{authorization_id:uuid}/places", dependencies=_READ)
async def set_places(
    authorization_id: uuid.UUID,
    body: PlacesBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say which places of the site the visit is for; none means the site in
    general. While it is unanswered, by whoever asked; once approved, only by
    the host or somebody who manages visits — it changes what was approved."""
    row, held, now, stands = await _open(db, token, allowed, authorization_id)
    if not _may(row, stands, token.user_id, held)["places"]:
        raise HTTPException(403, "Once approved, the places are changed by the host or by somebody who manages visits."
                            if row["state"] == "APPROVED" else
                            "The places are set by whoever asked, by the host, or by somebody who manages visits.")
    place_ids = await _places_at(db, row["site_id"], body.place_ids)
    was = (await db.execute(text("SELECT count(*) FROM visitor_authorization_places WHERE authorization_id = :a"),
                            {"a": row["id"]})).scalar()
    await _set_places(db, row["id"], place_ids)
    await db.execute(text("UPDATE visitor_authorizations SET updated_at = now() WHERE id = :id"), {"id": row["id"]})
    await intel_audit.record(db, request, token, "visitorauth.places", "visitor_authorization", row["id"],
                             site_id=row["site_id"], detail={"was": was, "now": len(place_ids), "stood": stands,
                                                             "place_ids": [str(p) for p in place_ids]})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    return answer


class EscortBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    escort_required: StrictBool
    escort_user_id: uuid.UUID | None = None
    escort_note: str | None = Field(None, max_length=500)


@router.put("/{authorization_id:uuid}/escort", dependencies=_READ + _WRITE)
async def set_escort(
    authorization_id: uuid.UUID,
    body: EscortBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say whether the visitor is to be escorted, and name who walks with
    them. Whether an escort is asked for at all is part of what was approved:
    once approved, that is changed only by the host or somebody who manages
    visits. Naming the escort is done by whoever is at the gate."""
    row, held, now, stands = await _open(db, token, allowed, authorization_id)
    host, _ = _theirs(row, token.user_id)
    if (body.escort_required != row["escort_required"] and row["state"] == "APPROVED"
            and not (host or "visitorauth:manage" in held)):
        raise HTTPException(403, "Once approved, whether an escort is asked for is changed by the host or by "
                                 "somebody who manages visits.")
    if body.escort_user_id is not None:
        if not body.escort_required:
            raise HTTPException(422, "An escort is named but none is asked for.")
        await _can_answer(db, body.escort_user_id, "escort")
    await db.execute(text("""
        UPDATE visitor_authorizations
           SET escort_required = :required, escort_user_id = :who, escort_note = :note, updated_at = now()
         WHERE id = :id
    """), {"required": body.escort_required, "who": body.escort_user_id,
           "note": (body.escort_note or "").strip() or None, "id": row["id"]})
    await intel_audit.record(db, request, token, "visitorauth.escort", "visitor_authorization", row["id"],
                             site_id=row["site_id"],
                             detail={"required": body.escort_required, "was_required": row["escort_required"],
                                     "escort_user_id": str(body.escort_user_id) if body.escort_user_id else None})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    return answer


class IdSeenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1, max_length=30)


@router.post("/{authorization_id:uuid}/id-seen", dependencies=_READ + _WRITE)
async def id_seen(
    authorization_id: uuid.UUID,
    body: IdSeenBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Record that the caller saw the visitor's ID, and what kind of document
    it was. The number on it is not taken: what is kept is that a named person
    looked."""
    row, held, now, _ = await _open(db, token, allowed, authorization_id)
    kind = body.kind.strip()
    if not kind:
        raise HTTPException(422, "Say what kind of document it was.")
    if re.search(r"\d{4,}", kind):
        raise HTTPException(422, "Say the kind of document, not its number. The number is not kept here.")
    await db.execute(text("""
        UPDATE visitor_authorizations
           SET id_document_kind = :kind, id_checked_by_user_id = CAST(:who AS uuid), id_checked_at = now(),
               updated_at = now()
         WHERE id = :id
    """), {"kind": kind, "who": token.user_id, "id": row["id"]})
    await intel_audit.record(db, request, token, "visitorauth.id_seen", "visitor_authorization", row["id"],
                             site_id=row["site_id"], detail={"kind": kind, "seen_before": row["id_document_kind"]})
    answer = await _given(db, row["id"], allowed, token, held, now)
    await db.commit()
    return answer


# ─── What a person made of a door event ──────────────────────────────────────

class MovementReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["IN_ORDER", "FOLLOWED_UP"]
    note: str | None = Field(None, max_length=2000)


@router.post("/{authorization_id:uuid}/movements/{access_event_id:uuid}/review", status_code=201,
             dependencies=_READ + _MANAGE)
async def review_movement(
    authorization_id: uuid.UUID,
    access_event_id: uuid.UUID,
    body: MovementReviewBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say what was made of a door event of this visit's badge: that it was in
    order, or that it was followed up and how. Said once, and kept."""
    _a_person(token)
    row = await _one(db, authorization_id, allowed, token.user_id)
    note = (body.note or "").strip() or None
    if body.outcome == "FOLLOWED_UP" and note is None:
        raise HTTPException(422, "Say what was done about it.")
    places = (await authorisation.places_of(db, [row["id"]]))[str(row["id"])]
    seen = await authorisation.movements(db, row, places)
    event = next((e for e in seen["items"] if e["access_event_id"] == access_event_id), None)
    if event is None:
        raise HTTPException(404, "That is not a door event of this visit's badge.")
    if event["review"] is not None:
        raise HTTPException(409, "Somebody has already said what they made of this.")
    await db.execute(text("""
        INSERT INTO visitor_movement_reviews (tenant_id, authorization_id, access_event_id, outcome, note,
                                              reviewed_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :a, :e, :outcome, :note, CAST(:who AS uuid))
    """), {"a": row["id"], "e": access_event_id, "outcome": body.outcome, "note": note, "who": token.user_id})
    await intel_audit.record(db, request, token, "visitorauth.movement_review", "visitor_authorization", row["id"],
                             site_id=row["site_id"],
                             detail={"access_event_id": str(access_event_id), "outcome": body.outcome,
                                     "within": event["within"], "in_period": event["in_period"]})
    after = await authorisation.movements(db, row, places)
    answer = next(e for e in after["items"] if e["access_event_id"] == access_event_id)
    await db.commit()
    return answer
