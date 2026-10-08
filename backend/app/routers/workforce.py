"""Workforce readings and recommendations: what is recorded of each guard's work, and what a manager might consider.

Phase 11 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
SECURITY_ANALYTICS_ARCHITECTURE.md.

  GET  /readings                  a reading for each guard and each site, for a period
  GET  /readings/{user_id}        one guard's reading, site by site, with what is recommended for them
  GET  /me                        the caller's own reading
  GET  /recommendations           training for guards and cover for sites, each with its latest answer
  POST /recommendations/answer    a manager's answer to one: accepted, or not accepted and why
  GET  /recommendations/answers   the answers that have been given, newest first

A READING IS COUNTED WHEN IT IS ASKED FOR (services/workforce_readings.py).
Nothing is stored of it, nothing in it is a score, and no list is in any order
but that of name.

A RECOMMENDATION IS NEVER AN EMPLOYMENT DECISION AND NEVER A CHANGE TO A ROSTER
(services/workforce_advice.py). An answer to one is a manager's, is added and
never rewritten, and changes nothing else: no course is assigned and no shift
is moved by it.

ANOTHER PERSON'S READING IS READ BY THE ORGANISATION'S OWN PEOPLE, AND THAT IT
WAS READ IS WRITTEN DOWN. A support session reads none. A person's own reading
is theirs, and is given whole.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.operations_board import held_by, sites_in
from app.services import intel_audit, intel_insight, workforce_advice, workforce_readings

PERMISSIONS = ("workforce:read", "workforce:answer", "workforce:own")
#: Every permission a section, or a kind of recommendation, is read under.
SOURCE_PERMISSIONS = (*workforce_readings.NEEDS.values(), "incident:read")
#: What each kind of recommendation is made of, and so is read under.
KIND_NEEDS = {"TRAINING": "training:read", "COVERAGE": "shift:read"}
NO_LONGER = "That no longer stands. Look again before answering."

router = APIRouter(prefix="/api/v1/workforce", tags=["workforce"])
_READ = [Depends(require_permission("workforce:read"))]
_ANSWER = [Depends(require_permission("workforce:answer"))]
_OWN = [Depends(require_permission("workforce:own"))]


def _own_staff(token: TokenPayload) -> None:
    """What is recorded of a named person is read by the organisation's own people."""
    if token.support_session_id:
        raise HTTPException(403, "A person's reading is read by the organisation's own staff, not from a support session.")


def _a_person(token: TokenPayload) -> None:
    """An answer to a recommendation is somebody's, by name."""
    _own_staff(token)
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")


def _period(days: int, now: datetime) -> tuple[datetime, dict]:
    if days not in workforce_readings.PERIOD_DAYS:
        raise HTTPException(422, "A reading is for the last 7, 28 or 90 days.")
    start = now - timedelta(days=days)
    return start, {"days": days, "from": start, "to": now}


def _sections(held) -> list[dict]:
    return [{"key": s, "title": workforce_readings.TITLE[s], "counted_from": workforce_readings.COUNTED_FROM[s],
             "personal": s in workforce_readings.PERSONAL}
            for s in workforce_readings.SECTIONS if workforce_readings.NEEDS[s] in held]


async def _in_scope(db: AsyncSession, user_id: uuid.UUID, site_ids, since: datetime) -> dict | None:
    """A person the caller may read the reading of: one of the organisation's
    people — and, for somebody held to particular sites, one posted to those
    sites or with a shift at them in the period."""
    params: dict = {"u": str(user_id)}
    seen = "TRUE"
    if site_ids is not None:
        params.update(sites=[str(s) for s in site_ids], since=since)
        seen = ("(u.primary_site_id = ANY(CAST(:sites AS uuid[])) "
                "OR EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id AND us.site_id = ANY(CAST(:sites AS uuid[]))) "
                "OR EXISTS (SELECT 1 FROM shifts s WHERE s.guard_user_id = u.id AND s.site_id = ANY(CAST(:sites AS uuid[])) "
                "           AND s.scheduled_start >= :since))")
    row = (await db.execute(text(f"""
        SELECT u.id, u.full_name, u.role_id, u.is_active FROM users u WHERE u.id = CAST(:u AS uuid) AND {seen}
    """), params)).mappings().first()
    return dict(row) if row else None


def _person(u: Mapping) -> dict:
    return {"id": u["id"], "name": u["full_name"], "role_id": u["role_id"], "is_active": u["is_active"]}


@router.get("/readings", dependencies=_READ)
async def read_readings(
    request: Request,
    site_id: uuid.UUID | None = Query(None),
    days: int = Query(workforce_readings.DEFAULT_DAYS),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A reading for each guard and for each site, over the last 7, 28 or 90
    days: every section the caller may read, as counts. Guards are in order of
    name; nothing is totalled into a score."""
    _own_staff(token)
    now = datetime.now(timezone.utc)
    start, period = _period(days, now)
    sites, site, _ = await sites_in(db, allowed, site_id)
    held = await held_by(db, token.role_id, SOURCE_PERMISSIONS)
    every = site is None and allowed is None
    site_ids = None if every else [s["id"] for s in sites]
    reading = await workforce_readings.read(db, held, site_ids, start, now, now)
    guards = await workforce_readings.people(db, list(reading["guards"]), site_ids)
    personal = (await workforce_readings.personal(db, [g["id"] for g in guards], start, now, now.date())
                if "training:read" in held else {})
    empty = {s: workforce_readings.blank(s) for s in reading["total"]}
    out = {"period": period, "site": {"id": site["id"], "name": site["name"]} if site else None,
           "sections": _sections(held),
           "guards": [{**_person(g), "figures": {**reading["guards"].get(str(g["id"]), empty),
                                                 **({"TRAINING": personal[str(g["id"])]} if personal else {})}}
                      for g in guards],
           "sites": [{"id": s["id"], "name": s["name"], "figures": reading["sites"].get(str(s["id"]), empty)}
                     for s in sites if s["is_active"] or str(s["id"]) in reading["sites"]],
           "no_site": reading["sites"].get(None) if every else None,
           "total": reading["total"], "not_read": reading["not_read"], "note": workforce_readings.NOTE}
    await intel_audit.record(db, request, token, "workforce.readings.read", "workforce_reading", None,
                             site_id=site["id"] if site else None, detail={"days": days, "guards": len(guards)})
    await db.commit()
    return out


@router.get("/readings/{user_id:uuid}", dependencies=_READ)
async def read_one(
    user_id: uuid.UUID,
    request: Request,
    days: int = Query(workforce_readings.DEFAULT_DAYS),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One guard's reading: each section, the same site by site, and what is
    recommended for them with its latest answer. That it was read is written
    in the audit log."""
    _own_staff(token)
    now = datetime.now(timezone.utc)
    start, period = _period(days, now)
    sites, _, _ = await sites_in(db, allowed)
    site_ids = None if allowed is None else [s["id"] for s in sites]
    person = await _in_scope(db, user_id, site_ids, now - timedelta(days=max(workforce_readings.PERIOD_DAYS)))
    if person is None:
        raise HTTPException(404, "Guard not found")
    held = await held_by(db, token.role_id, (*SOURCE_PERMISSIONS, *PERMISSIONS))
    out = await _reading_of(db, person, held, sites, site_ids, start, now, period)
    recommended = (await workforce_advice.training(db, [person], site_ids, now, now.date())
                   if KIND_NEEDS["TRAINING"] in held else [])
    answers = await _answers(db, [r["key"] for r in recommended])
    out["recommendations"] = [_with_answer(r, answers.get(r["key"]), "workforce:answer" in held) for r in recommended]
    out["recommendations_note"] = workforce_advice.NOTE
    await intel_audit.record(db, request, token, "workforce.reading.read", "workforce_reading", user_id,
                             detail={"days": days})
    await db.commit()
    return out


async def _reading_of(db: AsyncSession, person: Mapping, held, sites, site_ids, start: datetime, now: datetime,
                      period: dict) -> dict:
    reading = await workforce_readings.read(db, held, site_ids, start, now, now, guard=person["id"])
    figures = dict(reading["total"])
    if "training:read" in held:
        figures["TRAINING"] = (await workforce_readings.personal(db, [person["id"]], start, now, now.date()))[str(person["id"])]
    names = {str(s["id"]): s["name"] for s in sites}
    return {"period": period, "guard": _person(person), "sections": _sections(held), "figures": figures,
            "by_site": [{"id": key, "name": names.get(key, "A site you are not shown") if key else "At no site",
                         "figures": f} for key, f in sorted(reading["sites"].items(), key=lambda kv: names.get(kv[0] or "", ""))],
            "not_read": reading["not_read"], "note": workforce_readings.NOTE}


@router.get("/me", dependencies=_OWN)
async def read_mine(
    days: int = Query(workforce_readings.DEFAULT_DAYS),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The caller's own reading, whole: what is recorded of their work at
    every site, and what is recommended for them. Nobody else's."""
    now = datetime.now(timezone.utc)
    start, period = _period(days, now)
    person = await _in_scope(db, uuid.UUID(str(token.user_id)), None, start)
    if person is None:
        raise HTTPException(404, "Guard not found")
    everything = frozenset(workforce_readings.NEEDS.values())
    sites = [dict(r) for r in (await db.execute(text("SELECT id, name FROM sites"))).mappings()]
    out = await _reading_of(db, person, everything, sites, None, start, now, period)
    recommended = await workforce_advice.training(db, [person], None, now, now.date())
    out["recommendations"] = [_with_answer(r, None, False) for r in recommended]
    out["recommendations_note"] = workforce_advice.NOTE
    return out


async def _answers(db: AsyncSession, keys: list[str]) -> dict[str, dict]:
    """The latest answer to each of these recommendations."""
    if not keys:
        return {}
    rows = await db.execute(text("""
        SELECT DISTINCT ON (a.advice_key) a.advice_key, a.answer, a.reason, a.statement, a.answered_at,
               u.full_name AS answered_by_name
          FROM workforce_advice_answers a LEFT JOIN users u ON u.id = a.answered_by_user_id
         WHERE a.advice_key = ANY(:keys) ORDER BY a.advice_key, a.answered_at DESC, a.id DESC
    """), {"keys": keys})
    return {r["advice_key"]: dict(r) for r in rows.mappings()}


def _with_answer(rec: Mapping, answer: Mapping | None, can_answer: bool) -> dict:
    out = {**rec, "answer": None, "may_answer": can_answer}
    if answer is not None:
        out["answer"] = {"answer": answer["answer"], "reason": answer["reason"], "answered_at": answer["answered_at"],
                         "answered_by_name": answer["answered_by_name"],
                         # What it said when it was answered, when that is not what it says now.
                         "said_then": answer["statement"] if answer["statement"] != rec["statement"] else None}
    return out


async def _recommended(db: AsyncSession, held, sites, site_ids, now: datetime, *, only_guard: Mapping | None = None,
                       kinds=workforce_advice.KINDS) -> list[dict]:
    """Every recommendation of the scope the caller may read the records of."""
    out: list[dict] = []
    if "TRAINING" in kinds and KIND_NEEDS["TRAINING"] in held:
        if only_guard is not None:
            guards = [only_guard]
        else:
            since = now - timedelta(weeks=workforce_advice.WEEKS)
            everything = frozenset(workforce_readings.NEEDS.values())
            counted = await workforce_readings.read(db, everything, site_ids, since, now, now)
            guards = await workforce_readings.people(db, list(counted["guards"]), site_ids)
        out += await workforce_advice.training(db, guards, site_ids, now, now.date())
    if "COVERAGE" in kinds and KIND_NEEDS["COVERAGE"] in held:
        async def zone_of(site_id):
            return await intel_insight.zone_for(db, site_id)
        out += await workforce_advice.coverage(db, [s for s in sites if s["is_active"]], zone_of, now)
    return out


@router.get("/recommendations", dependencies=_READ)
async def read_recommendations(
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Training for the guards and cover for the sites the caller may see, from
    the last 4 weeks' records: each a statement with its counts and one thing
    to consider, with the latest answer a manager gave it."""
    _own_staff(token)
    now = datetime.now(timezone.utc)
    sites, site, _ = await sites_in(db, allowed, site_id)
    held = await held_by(db, token.role_id, (*SOURCE_PERMISSIONS, *PERMISSIONS))
    site_ids = None if (site is None and allowed is None) else [s["id"] for s in sites]
    found = await _recommended(db, held, sites, site_ids, now)
    answers = await _answers(db, [r["key"] for r in found])
    can = "workforce:answer" in held
    return {"weeks": workforce_advice.WEEKS, "site": {"id": site["id"], "name": site["name"]} if site else None,
            "training": [_with_answer(r, answers.get(r["key"]), can) for r in found if r["kind"] == "TRAINING"],
            "coverage": [_with_answer(r, answers.get(r["key"]), can) for r in found if r["kind"] == "COVERAGE"],
            "not_read": [{"kind": kind, "needs": needs} for kind, needs in KIND_NEEDS.items() if needs not in held],
            "can_answer": can, "note": workforce_advice.NOTE, "is_advisory": True, "is_decision": False}


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Which recommendation, as the server named it.
    key: str = Field(..., min_length=1, max_length=200)
    answer: Literal["ACCEPTED", "NOT_ACCEPTED"]
    reason: str | None = Field(None, max_length=2000)


@router.post("/recommendations/answer", status_code=201, dependencies=_READ + _ANSWER)
async def answer_recommendation(
    body: AnswerBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Say what was made of one recommendation: accepted, or not accepted and
    why. It is counted again first, and what is kept is what it says now. An
    answer is added; an earlier one stays. It assigns no course and changes no
    roster."""
    _a_person(token)
    reason = (body.reason or "").strip() or None
    if body.answer == "NOT_ACCEPTED" and reason is None:
        raise HTTPException(422, "Say why it is not accepted.")
    now = datetime.now(timezone.utc)
    parts = body.key.split(":")
    try:
        subject = uuid.UUID(parts[1])
    except (IndexError, ValueError):
        raise HTTPException(409, NO_LONGER) from None
    held = await held_by(db, token.role_id, (*SOURCE_PERMISSIONS, *PERMISSIONS))
    sites, _, _ = await sites_in(db, allowed)
    site_ids = None if allowed is None else [s["id"] for s in sites]
    if parts[0] in workforce_advice.CODES["COVERAGE"]:
        site = next((s for s in sites if s["id"] == subject), None)
        if site is None or not is_site_allowed(allowed, subject):
            raise HTTPException(409, NO_LONGER)
        found = await _recommended(db, held, [site], [site["id"]], now, kinds=("COVERAGE",))
    else:
        person = await _in_scope(db, subject, site_ids, now - timedelta(days=max(workforce_readings.PERIOD_DAYS)))
        if person is None:
            raise HTTPException(409, NO_LONGER)
        found = await _recommended(db, held, sites, site_ids, now, only_guard=person, kinds=("TRAINING",))
    rec = next((r for r in found if r["key"] == body.key), None)
    if rec is None:
        raise HTTPException(409, NO_LONGER)
    await db.execute(text("""
        INSERT INTO workforce_advice_answers
               (tenant_id, kind, subject_user_id, site_id, advice_key, code, statement, rests_on, answer, reason,
                answered_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :kind, CAST(:guard AS uuid), CAST(:site AS uuid), :key,
                :code, :statement, CAST(:rests_on AS jsonb), :answer, :reason, CAST(:who AS uuid))
    """), {"kind": rec["kind"], "guard": str(rec["subject"]["user_id"]) if rec["subject"] else None,
           "site": str(rec["site"]["id"]) if rec["site"] else None, "key": rec["key"], "code": rec["code"],
           "statement": rec["statement"], "rests_on": json.dumps(rec["rests_on"], default=str),
           "answer": body.answer, "reason": reason, "who": token.user_id})
    await intel_audit.record(db, request, token, "workforce.answer", "workforce_advice",
                             rec["subject"]["user_id"] if rec["subject"] else None,
                             site_id=rec["site"]["id"] if rec["site"] else None,
                             detail={"key": rec["key"], "code": rec["code"], "kind": rec["kind"], "answer": body.answer})
    out = _with_answer(rec, (await _answers(db, [rec["key"]]))[rec["key"]], True)
    await db.commit()
    return out


@router.get("/recommendations/answers", dependencies=_READ)
async def list_answers(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The answers that have been given, newest first: the recommendation as
    it was shown, what was answered, by whom and why. Somebody held to
    particular sites is given the answers about those sites, and about the
    guards posted to them or with a shift at them in the last 90 days."""
    _own_staff(token)
    params: dict = {"limit": limit}
    where = []
    if allowed is not None:
        scope = site_scope_clause(allowed, "a.site_id", params)
        posted = site_scope_clause(allowed, "us.site_id", params)
        worked = site_scope_clause(allowed, "s.site_id", params)
        params["since"] = datetime.now(timezone.utc) - timedelta(days=max(workforce_readings.PERIOD_DAYS))
        where.append(f"(({scope}) OR (a.kind = 'TRAINING' AND (EXISTS (SELECT 1 FROM user_sites us "
                     f"WHERE us.user_id = a.subject_user_id AND {posted}) OR EXISTS (SELECT 1 FROM shifts s "
                     f"WHERE s.guard_user_id = a.subject_user_id AND {worked} AND s.scheduled_start >= :since))))")
    rows = (await db.execute(text(f"""
        SELECT a.id, a.kind, a.code, a.advice_key, a.statement, a.rests_on, a.answer, a.reason, a.answered_at,
               u.full_name AS answered_by_name, g.full_name AS guard_name, s.name AS site_name
          FROM workforce_advice_answers a
          LEFT JOIN users u ON u.id = a.answered_by_user_id
          LEFT JOIN users g ON g.id = a.subject_user_id
          LEFT JOIN sites s ON s.id = a.site_id
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY a.answered_at DESC, a.id DESC LIMIT :limit
    """), params)).mappings().all()
    return {"items": [{**dict(r), "rests_on": r["rests_on"] if isinstance(r["rests_on"], dict) else json.loads(r["rests_on"])}
                      for r in rows]}
