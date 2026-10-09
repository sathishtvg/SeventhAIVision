"""How long records are kept, and where a person appears in them.

Phase 13 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
ENTERPRISE_SECURITY_HARDENING.md.

  GET  /retention                  every retention period in force, the holds, and what nothing removes
  PUT  /retention/periods/{kind}   set, change or take away the period of one of the four kinds that may have one
  POST /subjects/find              the members of staff or the visitors a name matches, to choose one
  GET  /subjects/staff/{id}        where one member of staff appears in the expansion's records
  GET  /subjects/visitor/{id}      where one visitor appears
  POST /subjects/written           where a name or a number plate, as typed, is written

THE STATEMENT IS A READING (services/retention_statement.py). It changes no
period and stores nothing. It names no person, so reading it is not audited.

A SUBJECT REPORT IS A PERSON'S, AND IS WRITTEN DOWN (services/subject_records.py).
It is asked for by somebody who is signed in - not an API key, not a support
session: looking across a customer's records for one of their people is the
customer's to do. Each report is one line in the audit log: who asked, about
whom or about what words, and how much was found.

IT COVERS EVERY SITE, so it is read by somebody who is not held to particular
sites. A count for some sites only would be taken for the whole answer.

A NAME TYPED IS SENT IN THE BODY of a request, never in its address: an
address is kept in more places than an answer is.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.pace import paced
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import intel_audit, record_retention, retention_statement, subject_records

#: The most people a name is matched to, to choose from.
FOUND_SHOWN = 20
EVERY_SITE = ("A subject report covers every site, so it is read by somebody who is not held to particular sites.")
PERIOD_EVERY_SITE = ("A retention period is for every site's records, so it is set by somebody who is not held to "
                     "particular sites.")

router = APIRouter(prefix="/api/v1/data-governance", tags=["data-governance"])
_RETENTION = [Depends(require_permission("retention:read"))]
_SET = [Depends(require_permission("retention:read")), Depends(require_permission("settings:write"))]
_PACED = Depends(paced("30/minute", "subject-report", "Too many subject reports in a minute. Wait a moment and try "
                                                       "again."))
_SUBJECT = [Depends(require_permission("subject:report")), _PACED]


def _the_organisations_own(token: TokenPayload, allowed: list[str] | None, what: str = "A subject report",
                           done: str = "asked for", every_site: str = EVERY_SITE) -> None:
    """Who may look across the records for one person, or set a period for every site's."""
    if token.via_api_key:
        raise HTTPException(403, f"{what} is {done} by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, f"{what} is {done} by the organisation's own staff, not from a support session.")
    if allowed is not None:
        raise HTTPException(403, every_site)


@router.get("/retention", dependencies=_RETENTION)
async def read_retention(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every retention period in force for the organisation, where each is set
    and what applies it; each site's own period for its recordings; the holds
    in force; and the records nothing removes. Somebody held to particular
    sites is given those sites and their holds."""
    answer = await retention_statement.read(db, allowed)
    # A period is set by somebody who may change the organisation's settings, and who sees every site.
    held = await held_by(db, token.role_id, ("settings:write",))
    answer["may_set"] = ("settings:write" in held and allowed is None
                         and not token.via_api_key and not token.support_session_id)
    await db.commit()
    return answer


class PeriodBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The period in whole days, or nothing to take the period away and keep the kind as before.
    days: StrictInt | None = Field(..., ge=record_retention.LEAST_DAYS, le=record_retention.MOST_DAYS)
    #: Said back by whoever sets it: how many are already older than the period, as the server last told them.
    #: A period that would remove more than they were told is not set.
    already_older: int | None = Field(None, ge=0)


@router.put("/retention/periods/{kind}", dependencies=_SET)
async def set_period(
    kind: str,
    body: PeriodBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set, change or take away the period of one kind of record that may
    have one. Nothing is removed by this: the scheduler removes what is over
    and older than the period, once a day.

    Setting a period that already has records older than it is asked for
    twice. The first answer says how many (409); the second says that number
    back. So nobody sets a period without having been told what it will
    remove."""
    _the_organisations_own(token, allowed, "A retention period", "set", PERIOD_EVERY_SITE)
    chosen = record_retention.BY_KEY.get(kind)
    if chosen is None:
        raise HTTPException(404, "No such kind of record. One of: " + ", ".join(record_retention.BY_KEY) + ".")
    before = (await record_retention.periods(db))[chosen.key]["days"]
    older = await record_retention.waiting(db, chosen, body.days) if body.days is not None else 0
    if body.days is not None and older and body.already_older != older:
        raise HTTPException(409, {
            "message": (f"{older} of these {'is' if older == 1 else 'are'} already older than {body.days} days and "
                        "will be removed for good when the scheduler next runs. Confirm to set the period."),
            "already_older": older, "days": body.days})
    if body.days is None:
        await db.execute(text("DELETE FROM tenant_settings WHERE setting_key = :k"), {"k": chosen.setting_key})
    else:
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :k, CAST(:v AS jsonb), CAST(:u AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"k": chosen.setting_key, "v": str(body.days), "u": token.user_id})
    await intel_audit.record(db, request, token, "retention.period.set", "record_retention", None, detail={
        "kind": chosen.key, "from_days": before, "to_days": body.days, "already_older": older})
    after = (await record_retention.periods(db))[chosen.key]
    answer = {"kind": chosen.key, "label": chosen.label, "days": after["days"], "set_at": after["set_at"],
              "already_older": older, "removed_by": record_retention.REMOVED_BY}
    await db.commit()
    return answer


class FindBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["STAFF", "VISITOR"]
    words: str = Field(..., min_length=subject_records.TEXT_MIN, max_length=subject_records.TEXT_MAX)


@router.post("/subjects/find", dependencies=_SUBJECT)
async def find_subject(
    body: FindBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The members of staff, or the visitors, whose name holds the words -
    so that one of them can be chosen. It finds; it reports nothing."""
    _the_organisations_own(token, allowed)
    pattern = subject_records.like(body.words.strip())
    if body.kind == "STAFF":
        rows = (await db.execute(text("""
            SELECT u.id, u.full_name AS name, r.name AS detail, u.is_active AS in_use
              FROM users u LEFT JOIN roles r ON r.id = u.role_id
             WHERE u.full_name ILIKE :p ESCAPE '\\' ORDER BY u.full_name, u.id LIMIT :n
        """), {"p": pattern, "n": FOUND_SHOWN + 1})).mappings().all()
    else:
        rows = (await db.execute(text("""
            SELECT v.id, v.full_name AS name, v.company AS detail, TRUE AS in_use
              FROM visitors v WHERE v.full_name ILIKE :p ESCAPE '\\'
             ORDER BY v.created_at DESC, v.id LIMIT :n
        """), {"p": pattern, "n": FOUND_SHOWN + 1})).mappings().all()
    answer = {"kind": body.kind,
              "found": [{"id": str(r["id"]), "name": r["name"], "detail": r["detail"], "in_use": r["in_use"]}
                        for r in rows[:FOUND_SHOWN]],
              "more": len(rows) > FOUND_SHOWN}
    await db.commit()
    return answer


async def _reported(db, request, token, answer: dict, subject_id) -> dict:
    totals = answer["totals"]
    await intel_audit.record(db, request, token, "subject.report", "subject_report", subject_id, detail={
        "kind": answer["subject"]["kind"], "words": answer["subject"].get("text"),
        "kinds_of_record": totals["kinds_of_record"],
        "found": totals.get("matches", totals.get("about", 0) + totals.get("by", 0)),
        "searches": (answer["searches"] or {}).get("count")})
    await db.commit()
    return answer


@router.get("/subjects/staff/{user_id:uuid}", dependencies=_SUBJECT)
async def staff_report(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Where one member of staff appears in what the expansion keeps: for each
    kind of record how many name them, as what, and between which dates; and
    how many investigation searches asked about them."""
    _the_organisations_own(token, allowed)
    answer = await subject_records.staff(db, str(user_id))
    if answer is None:
        raise HTTPException(404, "Nobody of that id is in this organisation.")
    return await _reported(db, request, token, answer, user_id)


@router.get("/subjects/visitor/{visitor_id:uuid}", dependencies=_SUBJECT)
async def visitor_report(
    visitor_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Where one visitor appears in what the expansion keeps."""
    _the_organisations_own(token, allowed)
    answer = await subject_records.visitor(db, str(visitor_id))
    if answer is None:
        raise HTTPException(404, "No visitor of that id is in this organisation.")
    return await _reported(db, request, token, answer, visitor_id)


class WrittenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=subject_records.TEXT_MIN, max_length=subject_records.TEXT_MAX)


@router.post("/subjects/written", dependencies=_SUBJECT)
async def written_report(
    body: WrittenBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Where a name or a number plate, as typed, is written into a case or on
    a work order, and how many investigation searches asked for it. What is
    found is text that matches, not an identification of anybody."""
    _the_organisations_own(token, allowed)
    words = " ".join(body.text.split())
    if len(words) < subject_records.TEXT_MIN:
        raise HTTPException(422, f"Type at least {subject_records.TEXT_MIN} letters or digits.")
    answer = await subject_records.written(db, words)
    return await _reported(db, request, token, answer, None)
