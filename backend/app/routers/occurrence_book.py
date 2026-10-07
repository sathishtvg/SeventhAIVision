"""The occurrence book: searching it, reviewing it, correcting it, and what is handed from one shift to the next.

Phase 5 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
DIGITAL_OCCURRENCE_BOOK.md.

  GET  /kinds                      the kinds of entry there are
  GET  /entries                    the book, searched: by words, kind, site, author, period, review
  GET  /entries/{id}               one entry, its reviews, and what corrects it or it corrects
  POST /entries/{id}/review        a supervisor notes an entry, or says it is to be followed up
  POST /entries/review             note a page of entries at once
  POST /entries/{id}/correct       put an entry right by writing a further entry
  ...  /instructions               what is in force at a site, issued, read and closed
  ...  /shift-summaries            a shift's summary: drafted, corrected, confirmed

WRITING AN ENTRY IS NOT HERE. Entries are written through the endpoint that has
always written them (POST /api/v1/dob), unchanged. Nothing in this router edits
or removes an entry: a mistake is put right by a further entry, and both stay.

A REVIEW IS BY SOMEBODY OTHER THAN WHO WROTE THE ENTRY, and what a reviewer
wrote is shown only to the people who keep the book — not to a client or a
viewer, who read the entries themselves through the existing endpoint.

A SHIFT'S SUMMARY IS DRAFTED BY THE PLATFORM AND CONFIRMED BY A PERSON. Until it
is confirmed it is shown only to whoever is writing it and whoever manages
handovers (services/shift_summary.py).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.dob import VALID_ENTRY_TYPES
from app.services import guard_positions, intel_audit, response_notify, shift_summary

#: The kinds of entry, in the order a guard reads them. The set itself is the
#: existing endpoint's: there is one list of what may be written.
KIND_ORDER = ("general", "incident", "unusual_activity", "delivery", "visitor_arrival", "visitor_departure",
              "patrol_start", "patrol_end", "guard_relief", "handover", "equipment_check", "maintenance",
              "alarm_activation", "fire_drill", "sos")
OUTCOMES = ("NOTED", "FOLLOW_UP", "CLOSED")
REVIEW_STATES = ("unreviewed", "noted", "follow_up", "closed")
MAX_BULK = 200
INSTRUCTION_EVENT = "site_instruction_issued"
PERMISSIONS = ("dob:read", "dob:write", "dob:review", "handover:read", "handover:create")

router = APIRouter(prefix="/api/v1/occurrence-book", tags=["occurrence-book"])
_READ = [Depends(require_permission("dob:read"))]
_WRITE = [Depends(require_permission("dob:write"))]
_REVIEW = [Depends(require_permission("dob:review"))]
_HANDOVER = [Depends(require_permission("handover:read"))]


def _a_person(token: TokenPayload) -> None:
    """What is written in the book, and about it, names who wrote it."""
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


def _keeps_the_book(held: frozenset[str]) -> bool:
    """Whether this person writes or reviews the book — and so sees what reviewers wrote."""
    return bool(held & {"dob:write", "dob:review"})


# ─── The book ────────────────────────────────────────────────────────────────

_ENTRY = """
    SELECT e.id, e.entry_type, e.body, e.severity, e.occurred_at, e.created_at, e.site_id, s.name AS site_name,
           e.shift_id, e.author_user_id, u.full_name AS author_name, e.related_alert_id, e.related_incident_id,
           r.outcome AS review_outcome, r.note AS review_note, r.reviewed_at, rv.full_name AS reviewed_by_name,
           k.corrects_entry_id, k.reason AS correction_reason,
           (SELECT c2.entry_id FROM occurrence_entry_corrections c2
             WHERE c2.corrects_entry_id = e.id ORDER BY c2.created_at DESC LIMIT 1) AS corrected_by_entry_id
      FROM occurrence_book_entries e
      LEFT JOIN users u ON u.id = e.author_user_id
      LEFT JOIN sites s ON s.id = e.site_id
      LEFT JOIN LATERAL (SELECT outcome, note, reviewed_at, reviewer_user_id FROM occurrence_entry_reviews
                          WHERE entry_id = e.id ORDER BY reviewed_at DESC, id DESC LIMIT 1) r ON TRUE
      LEFT JOIN users rv ON rv.id = r.reviewer_user_id
      LEFT JOIN occurrence_entry_corrections k ON k.entry_id = e.id
"""


def _entry(row: Mapping, inside: bool, me: str) -> dict:
    """An entry as this router gives it. What a reviewer wrote goes only to the
    people who keep the book."""
    out = {key: row[key] for key in ("id", "entry_type", "body", "severity", "occurred_at", "created_at", "site_id",
                                     "site_name", "shift_id", "author_user_id", "author_name", "related_alert_id",
                                     "related_incident_id", "corrects_entry_id", "correction_reason",
                                     "corrected_by_entry_id")}
    out["review"] = None
    if inside:
        state = {None: "unreviewed", "NOTED": "noted", "FOLLOW_UP": "follow_up", "CLOSED": "closed"}[row["review_outcome"]]
        out["review"] = {"state": state, "note": row["review_note"], "reviewed_at": row["reviewed_at"],
                         "reviewed_by_name": row["reviewed_by_name"]}
    out["mine"] = str(row["author_user_id"]) == me
    return out


@router.get("/kinds", dependencies=_READ)
async def list_kinds(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The kinds of entry the book takes, and what this caller may do with it."""
    held = await _held(db, token.role_id)
    known = [k for k in KIND_ORDER if k in VALID_ENTRY_TYPES] + sorted(VALID_ENTRY_TYPES - set(KIND_ORDER))
    return {"kinds": [{"key": k, "label": k.replace("_", " ").capitalize()} for k in known],
            "review_states": list(REVIEW_STATES) if _keeps_the_book(held) else [],
            "can_write": "dob:write" in held, "can_review": "dob:review" in held,
            "can_read_handovers": "handover:read" in held, "can_manage_handovers": "handover:create" in held}


@router.get("/entries", dependencies=_READ)
async def search_entries(
    q: str | None = Query(None, max_length=200),
    entry_type: list[str] = Query(default=[]),
    site_id: uuid.UUID | None = Query(None),
    author_user_id: uuid.UUID | None = Query(None),
    shift_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_until: datetime | None = Query(None),
    review: Literal["unreviewed", "noted", "follow_up", "closed"] | None = Query(None),
    corrected: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The book, newest first, narrowed by any of: words in an entry, its kind,
    site, author, shift, period, where its review stands, and whether it has
    been corrected."""
    held = await _held(db, token.role_id)
    inside = _keeps_the_book(held)
    if review is not None and not inside:
        raise HTTPException(403, "Where an entry's review stands is for the people who keep the book.")
    unknown = [k for k in entry_type if k not in VALID_ENTRY_TYPES]
    if unknown:
        raise HTTPException(422, f"Unknown kind of entry '{unknown[0]}'.")
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    cap, skip = clamp(limit, offset)
    params: dict = {"limit": cap + 1, "offset": skip}
    where = []
    scope = site_scope_clause(allowed, "e.site_id", params)
    if scope:
        # Somebody held to particular sites still reads what they wrote themselves.
        where.append(f"({scope} OR e.author_user_id = CAST(:me AS uuid))")
        params["me"] = token.user_id
    if q and q.strip():
        # The words as typed: a percent sign or an underscore is looked for, not treated as a wildcard.
        where.append("e.body ILIKE :q ESCAPE '\\'")
        params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if entry_type:
        where.append("e.entry_type = ANY(:kinds)")
        params["kinds"] = entry_type
    for column, value, name in (("e.site_id", site_id, "site"), ("e.author_user_id", author_user_id, "author"),
                                ("e.shift_id", shift_id, "shift")):
        if value is not None:
            where.append(f"{column} = CAST(:{name} AS uuid)")
            params[name] = str(value)
    if date_from is not None:
        where.append("e.occurred_at >= :date_from")
        params["date_from"] = date_from
    if date_until is not None:
        where.append("e.occurred_at <= :date_until")
        params["date_until"] = date_until
    if review is not None:
        where.append("r.outcome IS NULL" if review == "unreviewed" else "r.outcome = :outcome")
        if review != "unreviewed":
            params["outcome"] = review.upper()
    if corrected is not None:
        exists = "EXISTS (SELECT 1 FROM occurrence_entry_corrections c3 WHERE c3.corrects_entry_id = e.id)"
        where.append(exists if corrected else f"NOT {exists}")
    rows = (await db.execute(text(f"""{_ENTRY}
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY e.occurred_at DESC, e.id LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    return {"items": [_entry(r, inside, token.user_id) for r in rows[:cap]], "limit": cap, "offset": skip,
            "has_more": len(rows) > cap, "can_review": "dob:review" in held, "can_write": "dob:write" in held}


def _may_read(row: Mapping, allowed, me: str) -> bool:
    """An entry is read at the sites the reader may see, and by whoever wrote it.
    One with no site is read by whoever is not held to particular sites."""
    return allowed is None or is_site_allowed(allowed, row["site_id"]) or str(row["author_user_id"]) == me


async def _one_entry(db: AsyncSession, entry_id, allowed, me: str) -> dict:
    row = (await db.execute(text(f"{_ENTRY} WHERE e.id = CAST(:id AS uuid)"), {"id": str(entry_id)})).mappings().first()
    if row is None or not _may_read(row, allowed, me):
        raise HTTPException(404, "Entry not found")
    return dict(row)


@router.get("/entries/{entry_id:uuid}", dependencies=_READ)
async def read_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One entry: what it says, every review of it, the entry it corrects and
    the entries that correct it."""
    held = await _held(db, token.role_id)
    inside = _keeps_the_book(held)
    row = await _one_entry(db, entry_id, allowed, token.user_id)
    reviews = []
    if inside:
        reviews = [dict(r) for r in (await db.execute(text("""
            SELECT r.id, r.outcome, r.note, r.reviewed_at, r.reviewer_user_id, u.full_name AS reviewed_by_name
              FROM occurrence_entry_reviews r LEFT JOIN users u ON u.id = r.reviewer_user_id
             WHERE r.entry_id = :e ORDER BY r.reviewed_at, r.id
        """), {"e": row["id"]})).mappings()]
    links = (await db.execute(text(f"""{_ENTRY}
         WHERE e.id = :corrects OR e.id IN (SELECT entry_id FROM occurrence_entry_corrections
                                             WHERE corrects_entry_id = :e)
         ORDER BY e.occurred_at
    """), {"corrects": row["corrects_entry_id"], "e": row["id"]})).mappings().all()
    seen = [x for x in links if _may_read(x, allowed, token.user_id)]
    return {**_entry(row, inside, token.user_id), "reviews": reviews,
            "corrects": next((_entry(x, inside, token.user_id) for x in seen if x["id"] == row["corrects_entry_id"]), None),
            "corrected_by": [_entry(x, inside, token.user_id) for x in seen if x["id"] != row["corrects_entry_id"]]}


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["NOTED", "FOLLOW_UP", "CLOSED"] = "NOTED"
    note: str | None = Field(None, max_length=2000)


async def _review(db: AsyncSession, request: Request, token: TokenPayload, row: Mapping, outcome: str,
                  note: str | None) -> None:
    await db.execute(text("""
        INSERT INTO occurrence_entry_reviews (tenant_id, entry_id, site_id, reviewer_user_id, reviewer_role, outcome, note)
        VALUES (current_setting('app.current_tenant')::uuid, :e, :site, CAST(:who AS uuid), :role, :outcome, :note)
    """), {"e": row["id"], "site": row["site_id"], "who": token.user_id, "role": token.role_id, "outcome": outcome,
           "note": note})
    await intel_audit.record(db, request, token, "dob.review", "occurrence_book_entry", row["id"],
                             site_id=row["site_id"], detail={"outcome": outcome, "was": row["review_outcome"]})


@router.post("/entries/{entry_id:uuid}/review", dependencies=_READ + _REVIEW)
async def review_entry(
    entry_id: uuid.UUID,
    body: ReviewBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Note an entry as read, say it is to be followed up and what is to be
    done, or close a follow-up and say what was done. Each is added to the
    entry's reviews; none is rewritten. Not by whoever wrote the entry."""
    _a_person(token)
    row = await _one_entry(db, entry_id, allowed, token.user_id)
    if str(row["author_user_id"]) == token.user_id:
        raise HTTPException(403, "An entry is reviewed by somebody other than who wrote it.")
    note = (body.note or "").strip() or None
    if body.outcome != "NOTED" and note is None:
        raise HTTPException(422, "Say what is to be followed up." if body.outcome == "FOLLOW_UP"
                            else "Say what was done about it.")
    if body.outcome == "CLOSED" and row["review_outcome"] != "FOLLOW_UP":
        raise HTTPException(409, "There is no follow-up on this entry to close.")
    if body.outcome == "NOTED" and row["review_outcome"] == "FOLLOW_UP":
        raise HTTPException(409, "This entry is to be followed up. Close the follow-up, and say what was done.")
    await _review(db, request, token, row, body.outcome, note)
    answer = _entry(await _one_entry(db, entry_id, allowed, token.user_id), True, token.user_id)
    await db.commit()
    return answer


class BulkReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=MAX_BULK)


@router.post("/entries/review", dependencies=_READ + _REVIEW)
async def review_entries(
    body: BulkReview,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Note several entries as read at once — the ones that have not been
    reviewed and that somebody else wrote. The rest are left, each with why."""
    _a_person(token)
    rows = {r["id"]: r for r in (await db.execute(text(f"{_ENTRY} WHERE e.id = ANY(:ids)"),
                                                  {"ids": list(dict.fromkeys(body.entry_ids))})).mappings()}
    reviewed, left = [], []
    for entry_id in dict.fromkeys(body.entry_ids):
        row = rows.get(entry_id)
        if row is None or not _may_read(row, allowed, token.user_id):
            left.append({"id": entry_id, "why": "Not found."})
        elif str(row["author_user_id"]) == token.user_id:
            left.append({"id": entry_id, "why": "You wrote it."})
        elif row["review_outcome"] is not None:
            left.append({"id": entry_id, "why": "Already reviewed."})
        else:
            await _review(db, request, token, row, "NOTED", None)
            reviewed.append(entry_id)
    await db.commit()
    return {"reviewed": reviewed, "left": left}


class CorrectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(..., min_length=1, max_length=10000)
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/entries/{entry_id:uuid}/correct", status_code=201, dependencies=_READ + _WRITE)
async def correct_entry(
    entry_id: uuid.UUID,
    body: CorrectBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put an entry right by writing a further entry, and say why. The entry
    that was wrong stays in the book exactly as it was written. By whoever
    wrote it, or by somebody who reviews the book."""
    _a_person(token)
    row = await _one_entry(db, entry_id, allowed, token.user_id)
    held = await _held(db, token.role_id)
    if str(row["author_user_id"]) != token.user_id and "dob:review" not in held:
        raise HTTPException(403, "An entry is corrected by whoever wrote it, or by somebody who reviews the book.")
    if not body.body.strip() or not body.reason.strip():
        raise HTTPException(422, "A correction says what is right, and why the entry is being corrected.")
    new_id = (await db.execute(text("""
        INSERT INTO occurrence_book_entries
               (tenant_id, author_user_id, entry_type, body, severity, site_id, related_alert_id, related_incident_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:who AS uuid), :kind, :body, :severity, :site,
                :alert, :incident)
        RETURNING id
    """), {"who": token.user_id, "kind": row["entry_type"], "body": body.body.strip(), "severity": row["severity"],
           "site": row["site_id"], "alert": row["related_alert_id"], "incident": row["related_incident_id"]})).scalar()
    await db.execute(text("""
        INSERT INTO occurrence_entry_corrections (tenant_id, entry_id, corrects_entry_id, reason, created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :new, :old, :reason, CAST(:who AS uuid))
    """), {"new": new_id, "old": row["id"], "reason": body.reason.strip(), "who": token.user_id})
    await intel_audit.record(db, request, token, "dob.correct", "occurrence_book_entry", new_id,
                             site_id=row["site_id"], detail={"corrects_entry_id": str(row["id"])})
    answer = _entry(await _one_entry(db, new_id, allowed, token.user_id), _keeps_the_book(held), token.user_id)
    await db.commit()
    return answer


# ─── Instructions in force at a site ─────────────────────────────────────────

_INSTRUCTION = """
    SELECT n.id, n.site_id, s.name AS site_name, n.body, n.issued_by_user_id, u.full_name AS issued_by_name,
           n.issued_at, n.expires_at, n.closed_at, c.full_name AS closed_by_name, n.close_note,
           (n.closed_at IS NULL AND (n.expires_at IS NULL OR n.expires_at > now())) AS in_force,
           (SELECT count(*) FROM site_instruction_reads x WHERE x.instruction_id = n.id) AS reads,
           EXISTS (SELECT 1 FROM site_instruction_reads x
                    WHERE x.instruction_id = n.id AND x.user_id = CAST(:me AS uuid)) AS read_by_me
      FROM site_instructions n
      JOIN sites s ON s.id = n.site_id
      LEFT JOIN users u ON u.id = n.issued_by_user_id
      LEFT JOIN users c ON c.id = n.closed_by_user_id
"""


async def _instruction(db: AsyncSession, instruction_id, allowed, me: str) -> dict:
    row = (await db.execute(text(f"{_INSTRUCTION} WHERE n.id = CAST(:id AS uuid)"),
                            {"id": str(instruction_id), "me": me})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Instruction not found")
    return dict(row)


@router.get("/instructions", dependencies=_HANDOVER)
async def list_instructions(
    site_id: uuid.UUID | None = Query(None),
    state: Literal["in_force", "ended", "all"] = Query("in_force"),
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The instructions in force at a site — or at every site the caller may
    see — newest first, each with whether the caller has read it. `ended` gives
    those that were closed or have run out."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    params: dict = {"me": token.user_id, "limit": limit}
    where = []
    scope = site_scope_clause(allowed, "n.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("n.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    in_force = "(n.closed_at IS NULL AND (n.expires_at IS NULL OR n.expires_at > now()))"
    if state != "all":
        where.append(in_force if state == "in_force" else f"NOT {in_force}")
    rows = (await db.execute(text(f"""{_INSTRUCTION}
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY n.issued_at DESC LIMIT :limit
    """), params)).mappings().all()
    held = await _held(db, token.role_id)
    return {"items": [dict(r) for r in rows], "can_issue": "dob:review" in held}


class InstructionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: uuid.UUID
    body: str = Field(..., min_length=1, max_length=2000)
    expires_at: datetime | None = None


@router.post("/instructions", status_code=201, dependencies=_HANDOVER + _REVIEW)
async def issue_instruction(
    body: InstructionBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Issue an instruction for a site. It is in force until it is closed or
    runs out, and is carried into every shift's summary until then. The guards
    on shift at the site are told."""
    _a_person(token)
    known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(body.site_id)})).scalar()
    if not known or not is_site_allowed(allowed, str(body.site_id)):
        raise HTTPException(404, "Site not found")
    if not body.body.strip():
        raise HTTPException(422, "An instruction says something.")
    now = datetime.now(timezone.utc)
    if body.expires_at is not None:
        if body.expires_at.tzinfo is None:
            raise HTTPException(422, "Say when it runs out with a time zone.")
        if body.expires_at <= now:
            raise HTTPException(422, "An instruction runs out in the future, or not at all.")
    new_id = (await db.execute(text("""
        INSERT INTO site_instructions (tenant_id, site_id, body, issued_by_user_id, expires_at)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), :body, CAST(:who AS uuid), :until)
        RETURNING id
    """), {"site": str(body.site_id), "body": body.body.strip(), "who": token.user_id, "until": body.expires_at})).scalar()
    await intel_audit.record(db, request, token, "dob.instruction.issue", "site_instruction", new_id,
                             site_id=body.site_id, detail={"expires_at": body.expires_at.isoformat()
                                                           if body.expires_at else None})
    answer = await _instruction(db, new_id, allowed, token.user_id)
    on_shift = [g["user_id"] for g in await guard_positions.on_shift(db, now, None, site_id=body.site_id)]
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    payload = {"instruction_id": str(new_id), "site_id": str(body.site_id), "site_name": answer["site_name"],
               "body": shift_summary.quote(answer["body"])}
    await response_notify.announce(redis, token.tenant_id, INSTRUCTION_EVENT, payload, now)
    await response_notify.push(redis, token.tenant_id, [u for u in on_shift if str(u) != token.user_id],
                               f"New instruction at {answer['site_name']}: {payload['body']}",
                               {"type": INSTRUCTION_EVENT, "instruction_id": str(new_id)})
    return answer


@router.post("/instructions/{instruction_id:uuid}/read", dependencies=_HANDOVER)
async def read_instruction(
    instruction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say that the caller has read an instruction. Said once; saying it again
    changes nothing."""
    _a_person(token)
    await _instruction(db, instruction_id, allowed, token.user_id)
    await db.execute(text("""
        INSERT INTO site_instruction_reads (tenant_id, instruction_id, user_id)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:n AS uuid), CAST(:me AS uuid))
        ON CONFLICT (instruction_id, user_id) DO NOTHING
    """), {"n": str(instruction_id), "me": token.user_id})
    answer = await _instruction(db, instruction_id, allowed, token.user_id)
    await db.commit()
    return answer


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(..., min_length=1, max_length=2000)


@router.post("/instructions/{instruction_id:uuid}/close", dependencies=_HANDOVER + _REVIEW)
async def close_instruction(
    instruction_id: uuid.UUID,
    body: CloseBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Close an instruction, and say why. It stops being carried forward; it is
    kept, with who closed it."""
    _a_person(token)
    current = await _instruction(db, instruction_id, allowed, token.user_id)
    if current["closed_at"] is not None:
        raise HTTPException(409, "This instruction is already closed.")
    if not body.note.strip():
        raise HTTPException(422, "Say why it is being closed.")
    await db.execute(text("""
        UPDATE site_instructions SET closed_at = now(), closed_by_user_id = CAST(:who AS uuid), close_note = :note
         WHERE id = CAST(:id AS uuid)
    """), {"who": token.user_id, "note": body.note.strip(), "id": str(instruction_id)})
    await intel_audit.record(db, request, token, "dob.instruction.close", "site_instruction", instruction_id,
                             site_id=current["site_id"])
    answer = await _instruction(db, instruction_id, allowed, token.user_id)
    await db.commit()
    return answer


# ─── A shift's summary ───────────────────────────────────────────────────────

_SUMMARY = """
    SELECT m.id, m.shift_id, m.site_id, s.name AS site_name, m.guard_user_id, g.full_name AS guard_name,
           m.period_start, m.period_end, m.facts, m.drafted_text, m.final_text, m.method, m.state,
           m.drafted_by_user_id, d.full_name AS drafted_by_name, m.drafted_at, m.confirmed_by_user_id,
           c.full_name AS confirmed_by_name, m.confirmed_at, m.updated_at,
           (SELECT h.id FROM shift_handovers h WHERE h.shift_id = m.shift_id
             ORDER BY h.created_at DESC LIMIT 1) AS handover_id
      FROM shift_handover_summaries m
      LEFT JOIN sites s ON s.id = m.site_id
      LEFT JOIN users g ON g.id = m.guard_user_id
      LEFT JOIN users d ON d.id = m.drafted_by_user_id
      LEFT JOIN users c ON c.id = m.confirmed_by_user_id
"""


def _theirs(row: Mapping, token: TokenPayload, held: frozenset[str]) -> bool:
    """Whether this person writes this summary: the shift's own guard, whoever
    drafted it, or somebody who manages handovers."""
    return token.user_id in (str(row["guard_user_id"]), str(row.get("drafted_by_user_id"))) or "handover:create" in held


def _summary(row: Mapping, token: TokenPayload, held: frozenset[str]) -> dict:
    facts = row["facts"] if isinstance(row["facts"], dict) else json.loads(row["facts"])
    writes = _theirs(row, token, held)
    return {**{k: row[k] for k in row.keys() if k != "facts"}, "facts": facts,
            "edited": row["final_text"] != row["drafted_text"], "note": shift_summary.NOTE,
            "may": {"edit": writes and row["state"] == "DRAFT", "confirm": writes and row["state"] == "DRAFT",
                    "discard": writes and row["state"] == "DRAFT"}}


async def _one_summary(db: AsyncSession, summary_id, token: TokenPayload, held: frozenset[str], allowed) -> dict:
    row = (await db.execute(text(f"{_SUMMARY} WHERE m.id = CAST(:id AS uuid)"), {"id": str(summary_id)})).mappings().first()
    # A draft that was set aside is kept in the database and shown to nobody.
    if row is None or row["state"] == "DISCARDED":
        raise HTTPException(404, "Summary not found")
    mine = _theirs(row, token, held)
    # Somebody held to particular sites still reads the summary of their own shift.
    if not is_site_allowed(allowed, row["site_id"]) and token.user_id != str(row["guard_user_id"]):
        raise HTTPException(404, "Summary not found")
    # Until it is confirmed it is nobody's to read but whoever is writing it.
    if row["state"] != "CONFIRMED" and not mine:
        raise HTTPException(404, "Summary not found")
    return dict(row)


@router.get("/shift-summaries", dependencies=_HANDOVER)
async def list_summaries(
    shift_id: uuid.UUID | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Shift summaries, newest first: the confirmed ones, and the drafts the
    caller is writing or — for somebody who manages handovers — anybody's."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    held = await _held(db, token.role_id)
    params: dict = {"me": token.user_id, "limit": limit}
    where = ["m.state <> 'DISCARDED'"]
    scope = site_scope_clause(allowed, "m.site_id", params)
    if scope:
        where.append(f"({scope} OR m.guard_user_id = CAST(:me AS uuid))")
    if "handover:create" not in held:
        where.append("(m.state = 'CONFIRMED' OR m.guard_user_id = CAST(:me AS uuid) "
                     "OR m.drafted_by_user_id = CAST(:me AS uuid))")
    if shift_id is not None:
        where.append("m.shift_id = CAST(:shift AS uuid)")
        params["shift"] = str(shift_id)
    if site_id is not None:
        where.append("m.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"{_SUMMARY} WHERE {' AND '.join(where)} ORDER BY m.period_end DESC LIMIT :limit"),
                             params)).mappings().all()
    return {"items": [_summary(r, token, held) for r in rows], "can_manage": "handover:create" in held}


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shift_id: uuid.UUID


@router.post("/shift-summaries", status_code=201, dependencies=_HANDOVER)
async def draft_summary(
    body: DraftBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Draft the summary of a shift from what was recorded during it. By the
    guard whose shift it is, or by somebody who manages handovers. A draft that
    is already there is set aside and drafted again from what is recorded now;
    a summary that has been confirmed stands, and is not drafted over."""
    _a_person(token)
    held = await _held(db, token.role_id)
    shift = (await db.execute(text("""
        SELECT sh.id, sh.site_id, s.name AS site_name, sh.guard_user_id, u.full_name AS guard_name,
               sh.scheduled_start, sh.actual_start, sh.actual_end, t.timezone
          FROM shifts sh
          LEFT JOIN sites s ON s.id = sh.site_id
          LEFT JOIN users u ON u.id = sh.guard_user_id
          JOIN tenants t ON t.id = sh.tenant_id
         WHERE sh.id = CAST(:id AS uuid) FOR UPDATE OF sh
    """), {"id": str(body.shift_id)})).mappings().first()
    own = shift is not None and str(shift["guard_user_id"]) == token.user_id
    if shift is None or not (own or is_site_allowed(allowed, shift["site_id"])):
        raise HTTPException(404, "Shift not found")
    if not own and "handover:create" not in held:
        raise HTTPException(403, "A shift's summary is drafted by the guard whose shift it is, or by somebody who "
                                 "manages handovers.")
    if shift["actual_start"] is None:
        raise HTTPException(409, "This shift has not started: there is nothing to summarise.")
    standing = (await db.execute(text(
        "SELECT id, state FROM shift_handover_summaries WHERE shift_id = :s AND state <> 'DISCARDED'"),
        {"s": shift["id"]})).first()
    if standing is not None and standing.state == "CONFIRMED":
        raise HTTPException(409, "This shift's summary has been confirmed. It stands as it is.")
    if standing is not None:
        await db.execute(text("UPDATE shift_handover_summaries SET state = 'DISCARDED', updated_at = now() "
                              "WHERE id = :id"), {"id": standing.id})
    now = datetime.now(timezone.utc)
    facts = await shift_summary.gather(db, shift, now)
    words = shift_summary.write(facts)
    new_id = (await db.execute(text("""
        INSERT INTO shift_handover_summaries
               (tenant_id, shift_id, site_id, guard_user_id, period_start, period_end, facts, drafted_text, final_text,
                method, drafted_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :shift, :site, :guard, :a, :b, CAST(:facts AS jsonb),
                :words, :words, :method, CAST(:who AS uuid))
        RETURNING id
    """), {"shift": shift["id"], "site": shift["site_id"], "guard": shift["guard_user_id"],
           "a": shift["actual_start"], "b": shift["actual_end"] or now, "facts": json.dumps(facts), "words": words,
           "method": shift_summary.METHOD, "who": token.user_id})).scalar()
    await intel_audit.record(db, request, token, "dob.summary.draft", "shift_handover_summary", new_id,
                             site_id=shift["site_id"], detail={"shift_id": str(shift["id"]),
                                                               "drafted_again": standing is not None})
    answer = {**_summary(await _one_summary(db, new_id, token, held, allowed), token, held),
              "drafted_again": standing is not None}
    await db.commit()
    return answer


@router.get("/shift-summaries/{summary_id:uuid}", dependencies=_HANDOVER)
async def read_summary(
    summary_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One summary: what the platform drafted, what the person made of it, and
    the counts it was drafted from."""
    held = await _held(db, token.role_id)
    return _summary(await _one_summary(db, summary_id, token, held, allowed), token, held)


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_text: str = Field(..., min_length=1, max_length=20000)


async def _draft_of(db, summary_id, token, held, allowed) -> dict:
    row = await _one_summary(db, summary_id, token, held, allowed)
    if not _theirs(row, token, held):
        raise HTTPException(403, "A shift's summary is written by the guard whose shift it is, or by somebody who "
                                 "manages handovers.")
    if row["state"] != "DRAFT":
        raise HTTPException(409, "This summary has been confirmed. It stands as it is.")
    return row


@router.patch("/shift-summaries/{summary_id:uuid}", dependencies=_HANDOVER)
async def edit_summary(
    summary_id: uuid.UUID,
    body: EditBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Correct a draft. What the platform drafted is kept beside it."""
    _a_person(token)
    held = await _held(db, token.role_id)
    await _draft_of(db, summary_id, token, held, allowed)
    if not body.final_text.strip():
        raise HTTPException(422, "A summary says something.")
    await db.execute(text("UPDATE shift_handover_summaries SET final_text = :words, updated_at = now() "
                          "WHERE id = CAST(:id AS uuid)"), {"words": body.final_text.strip(), "id": str(summary_id)})
    answer = _summary(await _one_summary(db, summary_id, token, held, allowed), token, held)
    await db.commit()
    return answer


async def _settle(db, request, token, summary_id, allowed, state: str) -> dict:
    _a_person(token)
    held = await _held(db, token.role_id)
    row = await _draft_of(db, summary_id, token, held, allowed)
    await db.execute(text(
        "UPDATE shift_handover_summaries SET state = :state, updated_at = now()"
        + (", confirmed_by_user_id = CAST(:who AS uuid), confirmed_at = now()" if state == "CONFIRMED" else "")
        + " WHERE id = CAST(:id AS uuid)"),
        {"state": state, "id": str(summary_id), **({"who": token.user_id} if state == "CONFIRMED" else {})})
    await intel_audit.record(db, request, token, f"dob.summary.{'confirm' if state == 'CONFIRMED' else 'discard'}",
                             "shift_handover_summary", summary_id, site_id=row["site_id"],
                             detail={"shift_id": str(row["shift_id"]),
                                     "edited": row["final_text"] != row["drafted_text"]})
    if state == "DISCARDED":
        await db.commit()
        return {"state": "DISCARDED"}
    answer = _summary(await _one_summary(db, summary_id, token, held, allowed), token, held)
    await db.commit()
    return answer


@router.post("/shift-summaries/{summary_id:uuid}/confirm", dependencies=_HANDOVER)
async def confirm_summary(
    summary_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Confirm a draft as the summary of the shift. From then on it is read by
    whoever reads handovers, and it cannot be changed."""
    return await _settle(db, request, token, summary_id, allowed, "CONFIRMED")


@router.post("/shift-summaries/{summary_id:uuid}/discard", dependencies=_HANDOVER)
async def discard_summary(
    summary_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set a draft aside. The shift can be drafted again."""
    return await _settle(db, request, token, summary_id, allowed, "DISCARDED")
