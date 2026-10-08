"""Where recorded events gather, the advice made of that, and what a person answered.

Phase 9 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_RISK_ARCHITECTURE.md.

  GET  /patterns          five kinds of record, each counted by hour, day, week and place
  GET  /advice            what stands out, with what it rests on, how much history, and its answer
  POST /advice/answer     a person's answer to one piece: accepted, or not accepted and why
  GET  /advice/answers    the answers that have been given, newest first

EVERYTHING HERE IS COUNTED WHEN IT IS ASKED FOR (services/risk_patterns.py).
Nothing is stored but a person's answer, and nothing is a forecast: every
answer carries the note that says so.

AN ANSWER IS A PERSON'S, AND CHANGES NOTHING ELSE. It is given for one site, by
somebody signed in, to advice the server itself has just counted — the
statement that is kept is the server's, not one the caller sent. Accepting
advice raises no work, moves no guard and alters no roster.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, intel_insight, risk_patterns

PERMISSIONS = ("advice:read", "advice:answer")
#: What the advice of every site together is known by. It is read; it is not answered.
EVERY_SITE = "ALL"
ANSWER_ONE_SITE = "Advice is answered for one site. Choose the site it is about."

router = APIRouter(prefix="/api/v1/security-advice", tags=["security-advice"])
_READ = [Depends(require_permission("advice:read"))]
_ANSWER = [Depends(require_permission("advice:answer"))]


def _a_person(token: TokenPayload) -> None:
    """An answer to advice is somebody's, by name."""
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


async def _site(db: AsyncSession, site_id: uuid.UUID | None, allowed) -> dict | None:
    if site_id is None:
        return None
    row = (await db.execute(text("SELECT id, name FROM sites WHERE id = CAST(:s AS uuid)"),
                            {"s": str(site_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["id"]):
        raise HTTPException(404, "Site not found")
    return dict(row)


async def _counted(db: AsyncSession, site: Mapping | None, allowed, weeks: int, now: datetime) -> dict:
    """The period, and every kind counted for it: one site, or every site the caller may see."""
    since = risk_patterns.period(now, weeks)
    zone = await intel_insight.zone_for(db, site["id"] if site else None)
    site_ids = [site["id"]] if site else (None if allowed is None else list(allowed))
    read = await risk_patterns.read(db, zone, since, now, weeks, site_ids,
                                    str(site["id"]) if site else EVERY_SITE)
    return {"period": {"weeks": weeks, "from": since, "to": now, "timezone": zone},
            "site": {"id": site["id"], "name": site["name"]} if site else None, **read}


@router.get("/patterns", dependencies=_READ)
async def read_patterns(
    site_id: uuid.UUID | None = Query(None),
    weeks: int = Query(risk_patterns.DEFAULT_WEEKS, ge=1, le=risk_patterns.MAX_WEEKS),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Five kinds of thing that went wrong, each counted over whole weeks ending
    now: by weekday and hour where the organisation is, by week, and by place.
    For one site, or for every site the caller may see."""
    now = datetime.now(timezone.utc)
    counted = await _counted(db, await _site(db, site_id, allowed), allowed, weeks, now)
    return {"period": counted["period"], "site": counted["site"], "sources": counted["sources"],
            "weekdays": list(risk_patterns.WEEKDAYS), "band_hours": risk_patterns.BAND_HOURS,
            "note": risk_patterns.NOTE, "is_forecast": False}


async def _answers(db: AsyncSession, keys: list[str]) -> dict[str, dict]:
    """The latest answer to each of these pieces of advice."""
    if not keys:
        return {}
    rows = await db.execute(text("""
        SELECT DISTINCT ON (a.advice_key) a.advice_key, a.answer, a.reason, a.statement, a.confidence,
               a.answered_at, u.full_name AS answered_by_name
          FROM risk_advice_answers a LEFT JOIN users u ON u.id = a.answered_by_user_id
         WHERE a.advice_key = ANY(:keys) ORDER BY a.advice_key, a.answered_at DESC, a.id DESC
    """), {"keys": keys})
    return {r["advice_key"]: dict(r) for r in rows.mappings()}


def _with_answer(finding: Mapping, answer: Mapping | None, can_answer: bool) -> dict:
    out = {**finding, "answer": None, "may_answer": can_answer}
    if answer is not None:
        out["answer"] = {"answer": answer["answer"], "reason": answer["reason"], "answered_at": answer["answered_at"],
                         "answered_by_name": answer["answered_by_name"],
                         # What it said when it was answered, when that is not what it says now.
                         "said_then": answer["statement"] if answer["statement"] != finding["statement"] else None}
    return out


@router.get("/advice", dependencies=_READ)
async def read_advice(
    site_id: uuid.UUID | None = Query(None),
    weeks: int = Query(risk_patterns.DEFAULT_WEEKS, ge=1, le=risk_patterns.MAX_WEEKS),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What stands out in the period, by fixed rules: what rests on the most
    history first. Each piece says what it rests on and how much history that
    is, and carries the latest answer a person gave it. Advice for one site can
    be answered; advice for every site together is read."""
    now = datetime.now(timezone.utc)
    site = await _site(db, site_id, allowed)
    counted = await _counted(db, site, allowed, weeks, now)
    held = await _held(db, token.role_id)
    can = site is not None and "advice:answer" in held
    answers = await _answers(db, [f["key"] for f in counted["findings"]]) if site else {}
    return {"period": counted["period"], "site": counted["site"],
            "findings": [_with_answer(f, answers.get(f["key"]), can) for f in counted["findings"]],
            "counted": [{"source": s["source"], "label": s["label"], "total": s["total"]} for s in counted["sources"]],
            "can_answer": "advice:answer" in held, "answer_note": None if site else ANSWER_ONE_SITE,
            "note": risk_patterns.NOTE, "confidence_note": risk_patterns.CONFIDENCE_NOTE,
            "is_advisory": True, "is_forecast": False}


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: uuid.UUID
    weeks: int = Field(risk_patterns.DEFAULT_WEEKS, ge=1, le=risk_patterns.MAX_WEEKS)
    #: Which piece of advice, as the server named it.
    key: str = Field(..., min_length=1, max_length=200)
    answer: Literal["ACCEPTED", "NOT_ACCEPTED"]
    reason: str | None = Field(None, max_length=2000)


@router.post("/advice/answer", status_code=201, dependencies=_READ + _ANSWER)
async def answer_advice(
    body: AnswerBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say what was made of one piece of advice: accepted, or not accepted and
    why. The advice is counted again first, and what is kept is what it says
    now. An answer is added; an earlier one stays. It changes nothing else."""
    _a_person(token)
    reason = (body.reason or "").strip() or None
    if body.answer == "NOT_ACCEPTED" and reason is None:
        raise HTTPException(422, "Say why it is not accepted.")
    now = datetime.now(timezone.utc)
    site = await _site(db, body.site_id, allowed)
    counted = await _counted(db, site, allowed, body.weeks, now)
    finding = next((f for f in counted["findings"] if f["key"] == body.key), None)
    if finding is None:
        raise HTTPException(409, "That no longer stands for this site and period. Look again before answering.")
    await db.execute(text("""
        INSERT INTO risk_advice_answers
               (tenant_id, site_id, advice_key, code, source, statement, rests_on, confidence, period_weeks, period_end,
                answer, reason, answered_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :site, :key, :code, :source, :statement,
                CAST(:rests_on AS jsonb), :confidence, :weeks, :until, :answer, :reason, CAST(:who AS uuid))
    """), {"site": site["id"], "key": finding["key"], "code": finding["code"], "source": finding["source"],
           "statement": finding["statement"], "rests_on": json.dumps(finding["rests_on"]),
           "confidence": finding["confidence"]["level"], "weeks": body.weeks, "until": now, "answer": body.answer,
           "reason": reason, "who": token.user_id})
    await intel_audit.record(db, request, token, "advice.answer", "risk_advice", None, site_id=site["id"],
                             detail={"key": finding["key"], "code": finding["code"], "answer": body.answer,
                                     "confidence": finding["confidence"]["level"]})
    answer = (await _answers(db, [finding["key"]]))[finding["key"]]
    out = _with_answer(finding, answer, True)
    await db.commit()
    return out


@router.get("/advice/answers", dependencies=_READ)
async def list_answers(
    site_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The answers that have been given, newest first: the advice as it was
    shown, what was answered, by whom and why."""
    site = await _site(db, site_id, allowed)
    params: dict = {"limit": limit}
    where = []
    scope = site_scope_clause(allowed, "a.site_id", params)
    if scope:
        where.append(scope)
    if site is not None:
        where.append("a.site_id = :site")
        params["site"] = site["id"]
    rows = (await db.execute(text(f"""
        SELECT a.id, a.site_id, s.name AS site_name, a.advice_key, a.code, a.source, a.statement, a.rests_on,
               a.confidence, a.period_weeks, a.period_end, a.answer, a.reason, a.answered_at,
               u.full_name AS answered_by_name
          FROM risk_advice_answers a
          JOIN sites s ON s.id = a.site_id
          LEFT JOIN users u ON u.id = a.answered_by_user_id
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY a.answered_at DESC, a.id DESC LIMIT :limit
    """), params)).mappings().all()
    return {"items": [{**dict(r), "source_label": risk_patterns.SOURCE_LABEL.get(r["source"], r["source"]),
                       "rests_on": r["rests_on"] if isinstance(r["rests_on"], dict) else json.loads(r["rests_on"])}
                      for r in rows]}
