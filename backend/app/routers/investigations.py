"""Smart investigation: search across security records, and keep what was found.

Phase 1 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SMART_INVESTIGATION_ARCHITECTURE.md.

  POST /search          one search across every source the caller may read
  GET  /trail           every place one plate, or one watchlist entry, was seen
  GET  /sources         what can be searched, by this caller, and how to ask
  ...                   investigations: open, read, add to, set aside, close

NOTHING HERE CHANGES AN ALERT, AN INCIDENT OR ANY OTHER EXISTING RECORD. A
search reads; an investigation holds references to records that stay where
they are (app/services/investigation_sources.py says how they are read).

A SEARCH GIVES NOBODY A RECORD THEY COULD NOT ALREADY OPEN. Everything needs
`investigation:read`, and on top of it each source is searched only for someone
holding that source's own reading permission, inside their own sites.

AN INVESTIGATION IS A PERSON'S. An API key is not a person, and the vendor's
support staff are not this organisation's investigators: neither may use any of
this. Opening, adding to and closing need `investigation:manage`.

EVERY SEARCH IS ON THE RECORD. What was asked — the period, the plate, the name
— goes into the tenant's audit log with who asked and how many records came
back. The records themselves do not.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from limits import parse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.rate_limit import STORE_TIMEOUT_S, caller_of
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit
from app.services import investigation_phrase as phrases
from app.services import investigation_sources as sources

DEFAULT_TZ = "Asia/Singapore"
#: The most records one investigation holds.
MAX_ITEMS = 500
#: The most records added in one request.
MAX_ADD = 50
DEFAULT_TRAIL_DAYS = 30
#: A search is heavier than a read, and the default limit counts reads only.
#: Per person, as that one is.
SEARCH_LIMIT = parse("60/minute")


def _a_person(token: TokenPayload = Depends(get_token_payload)) -> None:
    """Who is investigating has to be somebody."""
    if token.via_api_key:
        raise HTTPException(403, "An investigation is carried out by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "An investigation is carried out by the organisation's own staff, "
                                 "not from a support session.")


async def _paced(request: Request) -> None:
    """No more searches a minute from one person than a person makes. If the
    counter cannot be reached the search goes ahead, as with every other limit."""
    if not limiter.enabled:
        return
    try:
        allowed = await asyncio.wait_for(asyncio.to_thread(
            limiter.limiter.hit, SEARCH_LIMIT, caller_of(request), "investigation-search"), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — the counter being down is never the caller's problem
        return
    if not allowed:
        raise HTTPException(429, "Too many searches in a minute. Wait a moment and try again.",
                            headers={"Retry-After": "60"})


router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"],
                   dependencies=[Depends(require_permission("investigation:read")), Depends(_a_person)])
_MANAGE = [Depends(require_permission("investigation:manage"))]


async def _zone(db: AsyncSession) -> str:
    name = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    return name or DEFAULT_TZ


async def _held(db: AsyncSession, token: TokenPayload) -> frozenset[str]:
    return await sources.held_permissions(db, token.role_id)


# ─── What can be searched ────────────────────────────────────────────────────

@router.get("/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The kinds of record a search reads, whether this caller may search each,
    which questions each can answer, and what can be typed in the phrase box."""
    held = await _held(db, token)
    return {
        "sources": [{
            "kind": s.kind, "label": s.label, "permission": s.permission, "may_search": s.permission in held,
            "asked_for": s.asked_for,
            "answers": {"camera": bool(s.camera), "event_type": bool(s.event_type), "severity": bool(s.severity),
                        "risk_level": bool(s.risk), "plate": bool(s.plate),
                        "person": bool(s.person) and (s.label_permission is None or s.label_permission in held),
                        "staff": bool(s.staff)},
        } for s in sources.SOURCES],
        "severities": list(sources.SEVERITIES), "risk_levels": list(sources.RISK_LEVELS),
        "max_days": sources.MAX_DAYS, "default_hours": sources.DEFAULT_HOURS,
        "phrase": phrases.vocabulary(), "note": sources.NOTE,
    }


# ─── Search ──────────────────────────────────────────────────────────────────

class SearchBody(BaseModel):
    """A search. Either a phrase, or the filters, or both — a filter given here
    replaces whatever the phrase said about the same thing."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    phrase: str | None = Field(None, max_length=phrases.MAX_PHRASE)
    since: datetime | None = Field(None, alias="from")
    until: datetime | None = Field(None, alias="to")
    kinds: list[str] | None = Field(None, max_length=len(sources.KINDS))
    site_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    camera_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    event_types: list[str] | None = Field(None, max_length=20)
    severities: list[str] | None = Field(None, max_length=5)
    risk_levels: list[str] | None = Field(None, max_length=5)
    plate: str | None = Field(None, max_length=16)
    person: str | None = Field(None, max_length=80)
    staff_user_id: uuid.UUID | None = None
    text: str | None = Field(None, max_length=120)
    limit: int = Field(50, ge=1, le=sources.MAX_LIMIT)
    offset: int = Field(0, ge=0, le=sources.MAX_OFFSET)
    oldest_first: bool = False


def _aware(value: datetime | None, what: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise HTTPException(422, f"{what} needs a time zone, as in 2026-10-05T01:00:00+08:00.")
    return value


async def _places(db: AsyncSession, allowed: list[str] | None) -> tuple[list[phrases.Place], list[phrases.Place]]:
    """This organisation's site and camera names, as the caller may see them."""
    params: dict = {}
    site_scope = site_scope_clause(allowed, "id", params)
    camera_scope = site_scope_clause(allowed, "site_id", params)
    site_rows = await db.execute(text(
        "SELECT id, name FROM sites" + (f" WHERE {site_scope}" if site_scope else "")), params)
    camera_rows = await db.execute(text(
        "SELECT id, name FROM cameras" + (f" WHERE {camera_scope}" if camera_scope else "")), params)
    return ([phrases.Place(str(r.id), r.name) for r in site_rows if r.name],
            [phrases.Place(str(r.id), r.name) for r in camera_rows if r.name])


@router.post("/search", dependencies=[Depends(_paced)])
async def search(
    body: SearchBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One search across every source the caller may read, newest first.

    `query` in the answer is the search that was actually run — when a phrase
    was typed, what was made of it — so that it can be read and corrected.
    `not_searched` lists each source that was left out, and why."""
    now = datetime.now(timezone.utc)
    held = await _held(db, token)
    said = None
    fields: dict = {}

    if body.phrase and body.phrase.strip():
        site_names, camera_names = await _places(db, allowed)
        try:
            parsed = phrases.parse(body.phrase, now=now, zone=await _zone(db), sites=site_names,
                                   cameras=camera_names)
        except phrases.NotUnderstood as exc:
            raise HTTPException(422, {
                "message": "Nothing in that was understood. Try a period, a kind of record, a number plate or "
                           "the name of one of your sites or cameras — or use the filters.",
                "not_understood": exc.words}) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        said = {"text": " ".join(body.phrase.split()), "understood": parsed.understood, "assumed": parsed.assumed,
                "not_understood": parsed.not_understood}
        fields = {"since": parsed.since, "until": parsed.until, "kinds": parsed.kinds, "site_ids": parsed.site_ids,
                  "camera_ids": parsed.camera_ids, "event_types": parsed.event_types,
                  "severities": parsed.severities, "plate": parsed.plate, "person": parsed.person,
                  "text": parsed.text}

    since, until = _aware(body.since, "The start of the period"), _aware(body.until, "The end of the period")
    given = {
        "since": since, "until": until, "kinds": body.kinds,
        "site_ids": None if body.site_ids is None else [str(s) for s in body.site_ids],
        "camera_ids": None if body.camera_ids is None else [str(c) for c in body.camera_ids],
        "event_types": body.event_types, "severities": body.severities, "risk_levels": body.risk_levels,
        "plate": body.plate, "person": body.person,
        "staff_user_id": None if body.staff_user_id is None else str(body.staff_user_id), "text": body.text,
    }
    fields.update({k: v for k, v in given.items() if v is not None})
    fields.setdefault("until", now)
    fields.setdefault("since", fields["until"] - timedelta(hours=sources.DEFAULT_HOURS))
    for name in ("plate", "person", "text"):
        if isinstance(fields.get(name), str) and not fields[name].strip():
            fields[name] = None

    query = sources.Query(**{k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()})
    try:
        found = await sources.search(db, query, held, allowed, limit=body.limit, offset=body.offset,
                                     oldest_first=body.oldest_first)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sources.TooWide as exc:
        raise HTTPException(422, "That search took too long to answer. Narrow the period, or choose fewer "
                                 "kinds of record.") from exc

    asked = {k: v for k, v in query.as_dict().items() if v not in (None, [], ())}
    await intel_audit.record(db, request, token, "investigation.search", "investigation_search", None, detail={
        "query": {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asked.items()},
        "phrase": said["text"] if said else None, "searched": found["searched"], "found": found["total"],
        # Each page asked for is a request, and is recorded as one.
        "offset": found["offset"]})
    answer = {"query": query.as_dict(), "phrase": said, **found}
    await db.commit()
    return answer


@router.get("/trail", dependencies=[Depends(_paced)])
async def trail(
    request: Request,
    plate: str | None = Query(None, min_length=3, max_length=16),
    watchlist_entry_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every place one number plate, or one face-watchlist entry, was seen in
    the period, oldest first, with the time and distance between sightings.

    A plate is followed by what the plate recogniser read; a watchlist entry by
    the matches the face recogniser reported. Nobody else can be followed, and
    the answer says so."""
    until = _aware(until, "The end of the period") or datetime.now(timezone.utc)
    since = _aware(since, "The start of the period") or until - timedelta(days=DEFAULT_TRAIL_DAYS)
    held = await _held(db, token)
    try:
        answer = await sources.trail(
            db, since=since, until=until, held=held, allowed=allowed, plate=plate,
            watchlist_entry_id=None if watchlist_entry_id is None else str(watchlist_entry_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sources.TooWide as exc:
        raise HTTPException(422, "That took too long to answer. Narrow the period.") from exc
    await intel_audit.record(db, request, token, "investigation.trail", "investigation_search", None, detail={
        "subject": answer["subject"] | {"name": None}, "from": since.isoformat(), "to": until.isoformat(),
        "found": answer["summary"]["sightings"]})
    await db.commit()
    return answer


# ─── Investigations ──────────────────────────────────────────────────────────

_FILE = """
    v.id, v.investigation_number, v.title, v.reason, v.status, v.site_id, s.name AS site_name, v.incident_id,
    v.situation_id, v.opened_by_user_id, ou.full_name AS opened_by_name, v.opened_at, v.closed_by_user_id,
    cu.full_name AS closed_by_name, v.closed_at, v.closing_note, v.updated_at
"""
_FILE_FROM = """
      FROM investigations v
      LEFT JOIN sites s ON s.id = v.site_id
      LEFT JOIN users ou ON ou.id = v.opened_by_user_id
      LEFT JOIN users cu ON cu.id = v.closed_by_user_id
"""


async def _file(db: AsyncSession, investigation_id: uuid.UUID, allowed: list[str] | None, *,
                lock: bool = False) -> dict:
    """The investigation, or 404 — also when it is at a site the caller is not
    assigned to, or spans sites and the caller is restricted to some."""
    row = (await db.execute(text(
        "SELECT * FROM investigations WHERE id = CAST(:id AS uuid)" + (" FOR UPDATE" if lock else "")),
        {"id": str(investigation_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Investigation not found")
    return dict(row)


def _open(investigation: dict) -> None:
    if investigation["status"] != "OPEN":
        raise HTTPException(409, "This investigation is closed. Reopen it to change it.")


async def _number(db: AsyncSession, at: datetime) -> str:
    """INV-YYYYMMDD-NNNN, counted per organisation per local day. One at a
    time: the lock is the organisation's and ends with the transaction."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('investigation:' || "
                          "current_setting('app.current_tenant')))"))
    try:
        day = at.astimezone(ZoneInfo(await _zone(db))).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop an investigation opening
        day = at.strftime("%Y%m%d")
    last = (await db.execute(text(
        "SELECT max(CAST(split_part(investigation_number, '-', 3) AS integer)) FROM investigations "
        " WHERE investigation_number LIKE :p"), {"p": f"INV-{day}-%"})).scalar()
    return f"INV-{day}-{(last or 0) + 1:04d}"


async def _add(db: AsyncSession, investigation_id, kind: str, ref_id, occurred_at: datetime, site_id, note,
               user_id: str) -> uuid.UUID | None:
    """File one record. None when it is already in the file."""
    return (await db.execute(text("""
        INSERT INTO investigation_items
               (tenant_id, investigation_id, kind, ref_id, occurred_at, site_id, note, added_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :file, :kind, :ref, :at, :site, :note,
                CAST(:who AS uuid))
        ON CONFLICT (investigation_id, kind, ref_id) WHERE ref_id IS NOT NULL DO NOTHING
        RETURNING id
    """), {"file": investigation_id, "kind": kind, "ref": ref_id, "at": occurred_at, "site": site_id,
           "note": note, "who": user_id})).scalar()


async def _remark(db: AsyncSession, investigation_id, note: str, user_id: str, at: datetime | None = None) -> None:
    """A note in the file: a person's, or the file's own record of being closed or reopened."""
    await _add(db, investigation_id, "NOTE", None, at or datetime.now(timezone.utc), None, note, user_id)


@router.get("")
async def list_investigations(
    status: Literal["OPEN", "CLOSED"] | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    mine: bool = Query(False),
    q: str | None = Query(None, min_length=2, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Investigations, most recently opened first."""
    where: list[str] = []
    params: dict = {}
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        where.append(scope)
    if status:
        where.append("v.status = :status")
        params["status"] = status
    if site_id:
        where.append("v.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if mine:
        where.append("v.opened_by_user_id = CAST(:me AS uuid)")
        params["me"] = token.user_id
    if q:
        where.append("(strpos(lower(v.title), :q) > 0 OR strpos(lower(v.investigation_number), :q) > 0)")
        params["q"] = q.strip().lower()
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return await paginate(
        db,
        f"""SELECT {_FILE},
                   (SELECT count(*) FROM investigation_items i
                     WHERE i.investigation_id = v.id AND i.kind <> 'NOTE' AND i.set_aside_at IS NULL) AS records
            {_FILE_FROM} {clause}
            ORDER BY v.opened_at DESC, v.id LIMIT :limit OFFSET :offset""",
        f"SELECT count(*) FROM investigations v {clause}", params, limit, offset)


class OpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=200)
    #: Why it is being opened. Kept, and never changed.
    reason: str = Field(..., min_length=5, max_length=2000)
    site_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    situation_id: uuid.UUID | None = None


@router.post("", status_code=201, dependencies=_MANAGE)
async def open_investigation(
    body: OpenBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Open an investigation. Started from an incident or a situation, that
    record — and the incident's alert — are the first things in the file."""
    held = await _held(db, token)
    site_id = str(body.site_id) if body.site_id else None
    first: list[dict] = []

    for kind, ref_id, what in (("INCIDENT", body.incident_id, "Incident"), ("SITUATION", body.situation_id,
                                                                            "Situation")):
        if ref_id is None:
            continue
        # Found the way a search would find it, so that an investigation is
        # never a way into a record the caller could not open.
        at = (await db.execute(text(
            ("SELECT created_at FROM incidents" if kind == "INCIDENT" else "SELECT started_at FROM security_situations")
            + " WHERE id = CAST(:id AS uuid)"), {"id": str(ref_id)})).scalar()
        record = None if at is None else (await sources.resolve(db, [(kind, ref_id, at)], held, allowed)).get(
            (kind, str(ref_id)))
        if record is None:
            raise HTTPException(404, f"{what} not found")
        first.append(record)
        site_id = site_id or (str(record["site_id"]) if record["site_id"] else None)

    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": site_id})).scalar()
        if not known or not is_site_allowed(allowed, site_id):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "Choose the site this investigation is about: you are assigned to certain sites "
                                 "and cannot open one that spans them all.")

    if body.incident_id is not None:
        alert_id = (await db.execute(text("SELECT alert_id FROM incidents WHERE id = CAST(:id AS uuid)"),
                                     {"id": str(body.incident_id)})).scalar()
        if alert_id is not None:
            at = (await db.execute(text("SELECT created_at FROM alerts WHERE id = :id"), {"id": alert_id})).scalar()
            alert = None if at is None else (await sources.resolve(db, [("ALERT", alert_id, at)], held, allowed)).get(
                ("ALERT", str(alert_id)))
            if alert is not None:
                first.append(alert)

    now = datetime.now(timezone.utc)
    number = await _number(db, now)
    row = (await db.execute(text("""
        INSERT INTO investigations
               (tenant_id, site_id, investigation_number, title, reason, incident_id, situation_id,
                opened_by_user_id, opened_at)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :number, :title, :reason,
                CAST(:incident AS uuid), CAST(:situation AS uuid), CAST(:who AS uuid), :now)
        RETURNING id, investigation_number, title, status, site_id, opened_at
    """), {"site": site_id, "number": number, "title": body.title.strip(), "reason": body.reason.strip(),
           "incident": str(body.incident_id) if body.incident_id else None,
           "situation": str(body.situation_id) if body.situation_id else None, "who": token.user_id,
           "now": now})).mappings().one()
    for record in first:
        await _add(db, row["id"], record["kind"], uuid.UUID(record["id"]), record["occurred_at"], record["site_id"],
                   "What this investigation was opened from.", token.user_id)
    await intel_audit.record(db, request, token, "investigation.open", "investigation", row["id"], site_id=site_id,
                             detail={"number": number, "title": body.title.strip(),
                                     "incident_id": str(body.incident_id) if body.incident_id else None,
                                     "situation_id": str(body.situation_id) if body.situation_id else None,
                                     "records": len(first)})
    answer = {**dict(row), "records": len(first)}
    await db.commit()
    return answer


def _state(item: dict, record: dict | None, held: frozenset[str]) -> str:
    if item["kind"] == "NOTE":
        return "NOTE"
    if record is not None:
        return "SHOWN"
    return "NOT_PERMITTED" if sources.BY_KIND[item["kind"]].permission not in held else "NOT_AVAILABLE"


@router.get("/{investigation_id:uuid}")
async def get_investigation(
    investigation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """An investigation and everything in it, in the order it happened.

    Each record is read where it lives, now, as this caller may see it. One the
    caller may not read is listed as `NOT_PERMITTED` and nothing of it is
    shown; one that is no longer held, or is outside the caller's sites, as
    `NOT_AVAILABLE`."""
    await _file(db, investigation_id, allowed)
    held = await _held(db, token)
    investigation = (await db.execute(text(
        f"SELECT {_FILE} {_FILE_FROM} WHERE v.id = CAST(:id AS uuid)"),
        {"id": str(investigation_id)})).mappings().one()
    items = [dict(r) for r in (await db.execute(text("""
        SELECT i.id, i.kind, i.ref_id, i.occurred_at, i.site_id, i.note, i.added_by_user_id,
               au.full_name AS added_by_name, i.added_at, i.set_aside_at, i.set_aside_by_user_id,
               su.full_name AS set_aside_by_name, i.set_aside_reason
          FROM investigation_items i
          LEFT JOIN users au ON au.id = i.added_by_user_id
          LEFT JOIN users su ON su.id = i.set_aside_by_user_id
         WHERE i.investigation_id = CAST(:id AS uuid)
         ORDER BY i.occurred_at, i.added_at, i.id
    """), {"id": str(investigation_id)})).mappings()]
    records = await sources.resolve(db, [(i["kind"], i["ref_id"], i["occurred_at"]) for i in items
                                         if i["kind"] != "NOTE"], held, allowed)
    for item in items:
        record = records.get((item["kind"], str(item["ref_id"]))) if item["ref_id"] else None
        item["state"] = _state(item, record, held)
        item["record"] = record
        item["label"] = "Note" if item["kind"] == "NOTE" else sources.BY_KIND[item["kind"]].label
    shown = [i for i in items if i["set_aside_at"] is None]
    return {
        **dict(investigation), "items": items,
        "counts": {"records": sum(1 for i in shown if i["kind"] != "NOTE"),
                   "notes": sum(1 for i in shown if i["kind"] == "NOTE"),
                   "set_aside": len(items) - len(shown),
                   "not_shown": sum(1 for i in shown if i["state"] in ("NOT_PERMITTED", "NOT_AVAILABLE"))},
        "can_manage": "investigation:manage" in await _mine(db, token),
    }


async def _mine(db: AsyncSession, token: TokenPayload) -> set[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code LIKE 'investigation:%'
    """), {"role": token.role_id})
    return {r.code for r in rows}


class Ref(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    id: uuid.UUID
    #: When the record happened, as the search gave it: it is how a record in a
    #: table partitioned by time is found again.
    occurred_at: datetime


class AddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[Ref] = Field(..., min_length=1, max_length=MAX_ADD)
    #: Why these matter to the investigation.
    note: str | None = Field(None, max_length=2000)


@router.post("/{investigation_id:uuid}/items", status_code=201, dependencies=_MANAGE)
async def add_records(
    investigation_id: uuid.UUID,
    body: AddBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """File records in an investigation. All of them or none: a record the
    caller cannot read is refused, and then nothing is added."""
    investigation = await _file(db, investigation_id, allowed, lock=True)
    _open(investigation)
    for ref in body.records:
        if ref.kind not in sources.BY_KIND:
            raise HTTPException(422, f"Unknown kind of record '{ref.kind}'. One of: {', '.join(sources.KINDS)}.")
        _aware(ref.occurred_at, "When a record happened")
    held = await _held(db, token)
    records = await sources.resolve(db, [(r.kind, r.id, r.occurred_at) for r in body.records], held, allowed)
    missing = [r for r in body.records if (r.kind, str(r.id)) not in records]
    if missing:
        raise HTTPException(404, {
            "message": "Some of these records were not found, or are not yours to read. Nothing was added.",
            "not_found": [{"kind": r.kind, "id": str(r.id)} for r in missing]})

    filed = (await db.execute(text(
        "SELECT count(*) FROM investigation_items WHERE investigation_id = :id"),
        {"id": investigation["id"]})).scalar()
    if filed + len(records) > MAX_ITEMS:
        raise HTTPException(409, f"An investigation holds at most {MAX_ITEMS} entries. This one has {filed}.")

    note = body.note.strip() if body.note and body.note.strip() else None
    added, already = [], []
    for record in records.values():
        new = await _add(db, investigation["id"], record["kind"], uuid.UUID(record["id"]), record["occurred_at"],
                         record["site_id"], note, token.user_id)
        (added if new else already).append({"kind": record["kind"], "id": record["id"]})
    if added:
        await db.execute(text("UPDATE investigations SET updated_at = now() WHERE id = :id"),
                         {"id": investigation["id"]})
        await intel_audit.record(db, request, token, "investigation.item.add", "investigation", investigation["id"],
                                 site_id=investigation["site_id"],
                                 detail={"number": investigation["investigation_number"], "added": added})
    await db.commit()
    return {"added": added, "already_filed": already}


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(..., min_length=2, max_length=4000)
    #: The moment the note is about, when it is about one. Otherwise now.
    occurred_at: datetime | None = None


@router.post("/{investigation_id:uuid}/notes", status_code=201, dependencies=_MANAGE)
async def add_note(
    investigation_id: uuid.UUID,
    body: NoteBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Write a note in an investigation."""
    investigation = await _file(db, investigation_id, allowed, lock=True)
    _open(investigation)
    if not body.note.strip():
        raise HTTPException(422, "A note has to say something.")
    at = _aware(body.occurred_at, "The moment a note is about")
    if at is not None and at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(422, "A note cannot be about a moment that has not happened yet.")
    await _remark(db, investigation["id"], body.note.strip(), token.user_id, at)
    await db.execute(text("UPDATE investigations SET updated_at = now() WHERE id = :id"), {"id": investigation["id"]})
    await intel_audit.record(db, request, token, "investigation.note.add", "investigation", investigation["id"],
                             site_id=investigation["site_id"],
                             detail={"number": investigation["investigation_number"]})
    await db.commit()
    return {"added": True}


class WhyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3, max_length=2000)


@router.post("/{investigation_id:uuid}/items/{item_id:uuid}/set-aside", dependencies=_MANAGE)
async def set_aside(
    investigation_id: uuid.UUID,
    item_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set an entry aside, with the reason. It stays in the file, marked: what
    was once thought relevant is part of the record."""
    investigation = await _file(db, investigation_id, allowed, lock=True)
    _open(investigation)
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is being set aside.")
    item = (await db.execute(text(
        "SELECT id, kind, ref_id, set_aside_at FROM investigation_items "
        " WHERE id = CAST(:item AS uuid) AND investigation_id = :file"),
        {"item": str(item_id), "file": investigation["id"]})).mappings().first()
    if item is None:
        raise HTTPException(404, "Entry not found")
    if item["set_aside_at"] is not None:
        raise HTTPException(409, "This entry has already been set aside.")
    await db.execute(text("""
        UPDATE investigation_items
           SET set_aside_at = now(), set_aside_by_user_id = CAST(:who AS uuid), set_aside_reason = :why
         WHERE id = :item
    """), {"who": token.user_id, "why": body.reason.strip(), "item": item["id"]})
    await db.execute(text("UPDATE investigations SET updated_at = now() WHERE id = :id"), {"id": investigation["id"]})
    await intel_audit.record(db, request, token, "investigation.item.set_aside", "investigation",
                             investigation["id"], site_id=investigation["site_id"],
                             detail={"number": investigation["investigation_number"], "kind": item["kind"],
                                     "record_id": str(item["ref_id"]) if item["ref_id"] else None,
                                     "reason": body.reason.strip()})
    await db.commit()
    return {"set_aside": True}


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: What was found, or why it is being left.
    note: str = Field(..., min_length=5, max_length=4000)


@router.post("/{investigation_id:uuid}/close", dependencies=_MANAGE)
async def close_investigation(
    investigation_id: uuid.UUID,
    body: CloseBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Close an investigation, saying what was found."""
    investigation = await _file(db, investigation_id, allowed, lock=True)
    _open(investigation)
    if not body.note.strip():
        raise HTTPException(422, "Say what was found, or why it is being left.")
    await db.execute(text("""
        UPDATE investigations
           SET status = 'CLOSED', closed_at = now(), closed_by_user_id = CAST(:who AS uuid),
               closing_note = :note, updated_at = now()
         WHERE id = :id
    """), {"who": token.user_id, "note": body.note.strip(), "id": investigation["id"]})
    await _remark(db, investigation["id"], f"Closed: {body.note.strip()}", token.user_id)
    await intel_audit.record(db, request, token, "investigation.close", "investigation", investigation["id"],
                             site_id=investigation["site_id"],
                             detail={"number": investigation["investigation_number"], "note": body.note.strip()})
    await db.commit()
    return {"status": "CLOSED"}


@router.post("/{investigation_id:uuid}/reopen", dependencies=_MANAGE)
async def reopen_investigation(
    investigation_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Reopen a closed investigation, with the reason. How it was closed stays
    in the file as a note, and in the audit log."""
    investigation = await _file(db, investigation_id, allowed, lock=True)
    if investigation["status"] != "CLOSED":
        raise HTTPException(409, "This investigation is already open.")
    if not body.reason.strip():
        raise HTTPException(422, "Say why it is being reopened.")
    await db.execute(text("""
        UPDATE investigations
           SET status = 'OPEN', closed_at = NULL, closed_by_user_id = NULL, closing_note = NULL, updated_at = now()
         WHERE id = :id
    """), {"id": investigation["id"]})
    await _remark(db, investigation["id"], f"Reopened: {body.reason.strip()}", token.user_id)
    await intel_audit.record(db, request, token, "investigation.reopen", "investigation", investigation["id"],
                             site_id=investigation["site_id"],
                             detail={"number": investigation["investigation_number"],
                                     "reason": body.reason.strip()})
    await db.commit()
    return {"status": "OPEN"}
