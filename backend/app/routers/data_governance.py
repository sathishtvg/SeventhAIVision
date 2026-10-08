"""How long records are kept, and where a person appears in them.

Phase 13 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
ENTERPRISE_SECURITY_HARDENING.md.

  GET  /retention                  every retention period in force, the holds, and what nothing removes
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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.pace import paced
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, retention_statement, subject_records

#: The most people a name is matched to, to choose from.
FOUND_SHOWN = 20
EVERY_SITE = ("A subject report covers every site, so it is read by somebody who is not held to particular sites.")

router = APIRouter(prefix="/api/v1/data-governance", tags=["data-governance"])
_RETENTION = [Depends(require_permission("retention:read"))]
_PACED = Depends(paced("30/minute", "subject-report", "Too many subject reports in a minute. Wait a moment and try "
                                                       "again."))
_SUBJECT = [Depends(require_permission("subject:report")), _PACED]


def _the_organisations_own(token: TokenPayload, allowed: list[str] | None) -> None:
    """Who may look across the records for one person."""
    if token.via_api_key:
        raise HTTPException(403, "A subject report is asked for by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "A subject report is asked for by the organisation's own staff, not from a "
                                 "support session.")
    if allowed is not None:
        raise HTTPException(403, EVERY_SITE)


@router.get("/retention", dependencies=_RETENTION)
async def read_retention(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every retention period in force for the organisation, where each is set
    and what applies it; each site's own period for its recordings; the holds
    in force; and the records nothing removes. Somebody held to particular
    sites is given those sites and their holds."""
    answer = await retention_statement.read(db, allowed)
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
