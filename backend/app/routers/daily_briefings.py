"""The daily briefing: drafted from a day's counts, reviewed by a person, published, and kept.

Phase 10 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_ANALYTICS_ARCHITECTURE.md.

  GET   /                 the briefings the caller may read, newest day first
  POST  /                 draft a briefing for a day: one site, or every site together
  GET   /{id}             one briefing
  PATCH /{id}             what the reviewer leaves out, and their own note — while it is a draft
  POST  /{id}/recount     count the draft again
  POST  /{id}/publish     publish it
  POST  /{id}/discard     set a draft aside

THE PLATFORM DRAFTS; A PERSON PUBLISHES. A draft is read only by whoever
manages briefings. Nothing here publishes one by itself, and publishing tells
nobody: a published briefing is there to be read.

THE COUNTED LINES ARE NOT EDITED. A reviewer leaves a whole section out, and
writes what they have to say in a note that is shown as theirs.

A PUBLISHED BRIEFING IS NOT CHANGED. A correction is a new revision for the
same day; the earlier one stays, marked as replaced.

A DRAFT HOLDS WHAT ITS DRAFTER MAY READ, and says what it does not hold. Once
published it is read whole by whoever may read briefings: publishing it is the
decision to share its counts.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import SOURCE_PERMISSIONS, held_by
from app.services import daily_briefing, intel_audit, intel_insight, ops_board, risk_patterns

PERMISSIONS = ("briefing:read", "briefing:manage")
EVERY_SITE = "A briefing for every site together is drafted by somebody who is not held to particular sites."

router = APIRouter(prefix="/api/v1/daily-briefings", tags=["daily-briefings"])
_READ = [Depends(require_permission("briefing:read"))]
_MANAGE = [Depends(require_permission("briefing:manage"))]

_ROW = """
    SELECT b.id, b.site_id, s.name AS site_name, b.briefing_date, b.revision, b.state, b.period_start, b.period_end,
           b.timezone, b.content, b.left_out, b.note, b.drafted_at, d.full_name AS drafted_by_name, b.published_at,
           p.full_name AS published_by_name, b.discarded_at,
           (SELECT n.id FROM daily_briefings n
             WHERE n.briefing_date = b.briefing_date AND n.site_id IS NOT DISTINCT FROM b.site_id
               AND n.state = 'PUBLISHED' AND n.revision > b.revision
             ORDER BY n.revision DESC LIMIT 1) AS replaced_by
      FROM daily_briefings b
      LEFT JOIN sites s ON s.id = b.site_id
      LEFT JOIN users d ON d.id = b.drafted_by_user_id
      LEFT JOIN users p ON p.id = b.published_by_user_id
"""


def _a_person(token: TokenPayload) -> None:
    """A briefing is reviewed and published by somebody, by name."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


def _may_see(row: Mapping, allowed, manages: bool) -> bool:
    """A reader sees what is published; whoever manages briefings sees drafts
    too. A briefing for every site together is for somebody not held to
    particular sites."""
    if row["state"] != "PUBLISHED" and not manages:
        return False
    return allowed is None if row["site_id"] is None else is_site_allowed(allowed, row["site_id"])


def _out(row: Mapping, manages: bool, *, whole: bool = True) -> dict:
    content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    draft = row["state"] == "DRAFT"
    out = {"id": row["id"], "site": {"id": row["site_id"], "name": row["site_name"]} if row["site_id"] else None,
           "briefing_date": row["briefing_date"], "revision": row["revision"], "state": row["state"],
           "replaced_by": row["replaced_by"],
           "period": {"from": row["period_start"], "to": row["period_end"], "timezone": row["timezone"],
                      "whole_day": row["period_end"] - row["period_start"] >= timedelta(hours=23)},
           "drafted_at": row["drafted_at"], "drafted_by_name": row["drafted_by_name"],
           "published_at": row["published_at"], "published_by_name": row["published_by_name"],
           "note": row["note"],
           # Which sections the reviewer left out is said to everybody; what was in them is not.
           "left_out": [{"key": s["key"], "title": s["title"]} for s in content.get("sections", [])
                        if s["key"] in (row["left_out"] or [])],
           "may": {"edit": manages and draft, "recount": manages and draft, "publish": manages and draft,
                   "discard": manages and draft,
                   "correct": manages and row["state"] == "PUBLISHED" and row["replaced_by"] is None}}
    if whole:
        # Whoever manages briefings sees a draft's every section; everybody else sees what was not left out.
        out["sections"] = daily_briefing.shown(content, row["left_out"] or [], whole=manages and draft)
        out["not_read"] = content.get("not_read", [])
        out["drafting_note"] = daily_briefing.DRAFTING_NOTE
    return out


async def _one(db: AsyncSession, briefing_id: uuid.UUID, allowed, manages: bool, *, lock: bool = False) -> dict:
    if lock:
        await db.execute(text("SELECT 1 FROM daily_briefings WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                         {"id": str(briefing_id)})
    row = (await db.execute(text(f"{_ROW} WHERE b.id = CAST(:id AS uuid)"), {"id": str(briefing_id)})).mappings().first()
    if row is None or not _may_see(row, allowed, manages):
        raise HTTPException(404, "Briefing not found")
    return dict(row)


async def _draft_of(db: AsyncSession, briefing_id: uuid.UUID, allowed) -> dict:
    row = await _one(db, briefing_id, allowed, True, lock=True)
    if row["state"] != "DRAFT":
        raise HTTPException(409, "A briefing that is published or discarded is not changed. "
                                 "A correction is a new draft for the same day.")
    return row


async def _counted(db: AsyncSession, token: TokenPayload, site_id, on: date, now: datetime) -> dict:
    """A day's content, as the caller may read it: the period and what was counted for it."""
    zone = await intel_insight.zone_for(db, site_id)
    start, end, _ = daily_briefing.day(on, zone, now)
    if start >= now:
        raise HTTPException(422, "That day has not begun.")
    if start < now - timedelta(days=daily_briefing.MAX_DAYS_BACK + 1):
        raise HTTPException(422, f"A briefing is drafted for a day in the last {daily_briefing.MAX_DAYS_BACK} days.")
    held = await held_by(db, token.role_id, SOURCE_PERMISSIONS)
    site_ids = [site_id] if site_id else None
    board = await ops_board.read(db, held, site_ids, start, end, now)
    advice = None
    if "advice:read" in held:
        weeks = daily_briefing.ADVICE_WEEKS
        advice = (await risk_patterns.read(db, zone, risk_patterns.period(now, weeks), now, weeks, site_ids,
                                           str(site_id) if site_id else "ALL"))["findings"]
    content = daily_briefing.draft(board["total"], board["clocks_on_since"] is not None, advice, board["not_read"])
    return {"start": start, "end": end, "zone": zone, "content": content}


@router.get("", dependencies=_READ)
async def list_briefings(
    site_id: uuid.UUID | None = Query(None),
    state: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The briefings the caller may read, newest day first: what is published,
    and for whoever manages briefings the drafts too. Discarded drafts are
    given only when asked for by state."""
    if state is not None and state not in daily_briefing.STATES:
        raise HTTPException(422, f"state is one of {', '.join(daily_briefing.STATES)}")
    manages = "briefing:manage" in await held_by(db, token.role_id, PERMISSIONS)
    params: dict = {"limit": limit}
    where = ["b.state = :state"] if state else ["b.state <> 'DISCARDED'"]
    if state:
        params["state"] = state
    if site_id is not None:
        where.append("b.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"""
        {_ROW} WHERE {' AND '.join(where)}
         ORDER BY b.briefing_date DESC, s.name NULLS FIRST, b.revision DESC LIMIT :limit
    """), params)).mappings().all()
    return {"items": [_out(r, manages, whole=False) for r in rows if _may_see(r, allowed, manages)],
            "can_manage": manages, "max_days_back": daily_briefing.MAX_DAYS_BACK}


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: One site, or none for every site together.
    site_id: uuid.UUID | None = None
    briefing_date: date


@router.post("", status_code=201, dependencies=_READ + _MANAGE)
async def draft_briefing(
    body: DraftBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Draft the briefing for a day. It is counted now, from what the caller may
    read. When that day already has a published briefing, the draft is its
    correction: the next revision. A day has one draft at a time."""
    _a_person(token)
    now = datetime.now(timezone.utc)
    site = None
    if body.site_id is None:
        if allowed is not None:
            raise HTTPException(403, EVERY_SITE)
    else:
        site = (await db.execute(text("SELECT id, name FROM sites WHERE id = CAST(:s AS uuid)"),
                                 {"s": str(body.site_id)})).mappings().first()
        if site is None or not is_site_allowed(allowed, site["id"]):
            raise HTTPException(404, "Site not found")
    counted = await _counted(db, token, site["id"] if site else None, body.briefing_date, now)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('briefing:' || current_setting('app.current_tenant')))"))
    same = "briefing_date = :day AND site_id IS NOT DISTINCT FROM CAST(:site AS uuid)"
    key = {"day": body.briefing_date, "site": str(site["id"]) if site else None}
    existing = (await db.execute(text(f"SELECT id FROM daily_briefings WHERE {same} AND state = 'DRAFT'"), key)).scalar()
    if existing is not None:
        raise HTTPException(409, "That day already has a draft. Open it, or discard it first.")
    revision = (await db.execute(text(f"SELECT COALESCE(max(revision), 0) + 1 FROM daily_briefings WHERE {same}"),
                                 key)).scalar()
    new_id = (await db.execute(text("""
        INSERT INTO daily_briefings (tenant_id, site_id, briefing_date, revision, period_start, period_end, timezone,
                                     content, drafted_by_user_id, drafted_at)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :day, :revision, :start, :end, :zone,
                CAST(:content AS jsonb), CAST(:who AS uuid), :now)
        RETURNING id
    """), {**key, "revision": revision, "start": counted["start"], "end": counted["end"], "zone": counted["zone"],
           "content": json.dumps(counted["content"]), "who": token.user_id, "now": now})).scalar()
    await intel_audit.record(db, request, token, "briefing.draft", "daily_briefing", new_id,
                             site_id=site["id"] if site else None,
                             detail={"briefing_date": body.briefing_date.isoformat(), "revision": revision})
    out = _out(await _one(db, new_id, allowed, True), True)
    await db.commit()
    return out


@router.get("/{briefing_id:uuid}", dependencies=_READ)
async def read_briefing(
    briefing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One briefing: its sections of counted lines, what was not counted, the
    reviewer's note, and who drafted and published it. A reader is not given a
    draft, or a section the reviewer left out."""
    manages = "briefing:manage" in await held_by(db, token.role_id, PERMISSIONS)
    return _out(await _one(db, briefing_id, allowed, manages), manages)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The sections the reviewer leaves out of what is published.
    left_out: list[str] | None = Field(None, max_length=len(daily_briefing.SECTIONS))
    #: The reviewer's own words. An empty note is no note.
    note: str | None = Field(None, max_length=4000)


@router.patch("/{briefing_id:uuid}", dependencies=_READ + _MANAGE)
async def review_briefing(
    briefing_id: uuid.UUID,
    body: ReviewBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Leave sections out of a draft, and write a note of one's own. The
    counted lines themselves are not edited."""
    _a_person(token)
    row = await _draft_of(db, briefing_id, allowed)
    sets, params = ["updated_at = now()"], {"id": str(briefing_id)}
    detail: dict = {}
    if "left_out" in body.model_fields_set:
        wanted = list(dict.fromkeys(body.left_out or []))
        content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
        held = {s["key"] for s in content.get("sections", [])}
        unknown = [k for k in wanted if k not in held]
        if unknown:
            raise HTTPException(422, f"This briefing has no section {', '.join(unknown)}.")
        sets.append("left_out = CAST(:left_out AS text[])")
        params["left_out"] = wanted
        detail["left_out"] = wanted
    if "note" in body.model_fields_set:
        sets.append("note = :note")
        params["note"] = (body.note or "").strip() or None
        detail["note"] = params["note"] is not None
    if len(sets) > 1:
        await db.execute(text(f"UPDATE daily_briefings SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"), params)
        await intel_audit.record(db, request, token, "briefing.review", "daily_briefing", briefing_id,
                                 site_id=row["site_id"], detail=detail)
    out = _out(await _one(db, briefing_id, allowed, True), True)
    await db.commit()
    return out


@router.post("/{briefing_id:uuid}/recount", dependencies=_READ + _MANAGE)
async def recount_briefing(
    briefing_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Count a draft again, as the caller may read it now. The reviewer's note
    stays; a section that is no longer in it is no longer left out."""
    _a_person(token)
    row = await _draft_of(db, briefing_id, allowed)
    now = datetime.now(timezone.utc)
    counted = await _counted(db, token, row["site_id"], row["briefing_date"], now)
    held = {s["key"] for s in counted["content"]["sections"]}
    await db.execute(text("""
        UPDATE daily_briefings
           SET content = CAST(:content AS jsonb), period_end = :end, drafted_at = :now,
               drafted_by_user_id = CAST(:who AS uuid), left_out = CAST(:left_out AS text[]), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(briefing_id), "content": json.dumps(counted["content"]), "end": counted["end"], "now": now,
           "who": token.user_id, "left_out": [k for k in (row["left_out"] or []) if k in held]})
    await intel_audit.record(db, request, token, "briefing.recount", "daily_briefing", briefing_id,
                             site_id=row["site_id"], detail={"revision": row["revision"]})
    out = _out(await _one(db, briefing_id, allowed, True), True)
    await db.commit()
    return out


@router.post("/{briefing_id:uuid}/publish", dependencies=_READ + _MANAGE)
async def publish_briefing(
    briefing_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Publish a draft as it stands: what was counted, less what the reviewer
    left out, with their note. From then it is not changed. Nobody is told."""
    _a_person(token)
    row = await _draft_of(db, briefing_id, allowed)
    content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    if not daily_briefing.shown(content, row["left_out"] or [], whole=False) and not row["note"]:
        raise HTTPException(422, "There is nothing in it to publish: every section is left out and there is no note.")
    await db.execute(text("""
        UPDATE daily_briefings
           SET state = 'PUBLISHED', published_by_user_id = CAST(:who AS uuid), published_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(briefing_id), "who": token.user_id})
    await intel_audit.record(db, request, token, "briefing.publish", "daily_briefing", briefing_id,
                             site_id=row["site_id"],
                             detail={"briefing_date": row["briefing_date"].isoformat(), "revision": row["revision"],
                                     "left_out": list(row["left_out"] or [])})
    out = _out(await _one(db, briefing_id, allowed, True), True)
    await db.commit()
    return out


@router.post("/{briefing_id:uuid}/discard", dependencies=_READ + _MANAGE)
async def discard_briefing(
    briefing_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set a draft aside. It is kept, and the day can be drafted again."""
    _a_person(token)
    row = await _draft_of(db, briefing_id, allowed)
    await db.execute(text("""
        UPDATE daily_briefings
           SET state = 'DISCARDED', discarded_by_user_id = CAST(:who AS uuid), discarded_at = now(), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(briefing_id), "who": token.user_id})
    await intel_audit.record(db, request, token, "briefing.discard", "daily_briefing", briefing_id,
                             site_id=row["site_id"], detail={"revision": row["revision"]})
    out = _out(await _one(db, briefing_id, allowed, True), True)
    await db.commit()
    return out
