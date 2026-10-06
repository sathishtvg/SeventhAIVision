"""AI security intelligence: the decisions people make, and what is then done.

The same prefix as the rest of the layer (`routers/security_intelligence.py`),
in its own file because it is the one part of the layer that leads to anything
being done.

A DECISION COMES FROM A PERSON WHO IS SIGNED IN. Not from an API key, not from a
vendor's support session, and never from the layer itself. It needs the
permission `intel:decide`, the site, the authority the decision policy gives
that person's role at this risk, and — to be carried out — the platform's own
permission for each step. Missing any of them it is refused, and nothing is
recorded as decided.

THREE THINGS ARE SERVED, AND KEPT APART: what was suggested (`suggested_action`,
`recommendation_id`), what the person chose (`action`, `basis`, `reason`), and
what the platform then did (`actions`, each with the function it went through
and how it ended).

Every decision, verdict and step is written to the tenant's hash-chained audit
log with the actor, their role, the site, the request and the result.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_actions, intel_audit, intel_drone, intel_feedback, intel_field
from app.services import intel_decisions as decisions
from app.services.intel_risk import LEVELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/security-intelligence", tags=["security-intelligence"],
                   dependencies=[Depends(require_permission("intel:read"))])


def _person(token: TokenPayload) -> None:
    """A decision is a person's. A key is not a person, and the vendor's
    support staff are not this organisation's security officers."""
    if token.via_api_key:
        raise HTTPException(403, "A security decision is made by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "A security decision is made by the organisation's own staff, "
                                 "not from a support session.")


async def _situation(db: AsyncSession, situation_id: uuid.UUID, allowed, *, lock: bool = False) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM security_situations WHERE id = CAST(:id AS uuid)" + (" FOR UPDATE" if lock else "")),
        {"id": str(situation_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Situation not found")
    return dict(row)


async def _announce(request: Request, token: TokenPayload, event_type: str, payload: dict) -> None:
    """Tell the tenant's own screens. A nudge: the record is already saved."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(f"tenant_events:{token.tenant_id}", json.dumps({
            "event_type": event_type, "tenant_id": str(token.tenant_id), "payload": payload,
            "occurred_at": datetime.now(timezone.utc).isoformat()}, default=str))
    except Exception as exc:  # noqa: BLE001 — the decision stands whether or not anyone was nudged
        logger.warning("could not announce %s: %s", event_type, type(exc).__name__)


def _said(decision: dict) -> dict:
    """What goes on the wire about a decision: ids, codes and where things
    stand. Never a name, never a note."""
    return {"situation_id": decision["situation_id"], "situation_number": decision["situation_number"],
            "decision_id": decision["id"], "action": decision["action"], "basis": decision["basis"],
            "authority": decision["authority"], "state": decision["state"],
            "decided_by_role": decision["decided_by"]["role_id"],
            "actions": [{"action": a["action"], "result": a["result"]} for a in decision["actions"]]}


# ─── That an officer looked ──────────────────────────────────────────────────

class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    via: Literal["web", "mobile"] = "web"


@router.post("/situations/{situation_id}/reviews",
             dependencies=[Depends(require_permission("intel:recommendation:read"))])
async def record_review(
    situation_id: uuid.UUID,
    request: Request,
    body: ReviewIn | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Record that the caller has looked at what the layer suggests for this
    situation. Once per person per assessment: opening it again adds nothing.
    Looking decides nothing."""
    situation = await _situation(db, situation_id, allowed)
    if token.via_api_key or token.support_session_id or situation["assessment_id"] is None:
        return {"recorded": False, "assessment_id": situation["assessment_id"]}
    new = await decisions.record_review(db, situation, token.user_id, token.role_id, body.via if body else "web")
    if new:
        await intel_audit.record(db, request, token, "intel.recommendation.view", "security_situation",
                                 situation["id"], site_id=situation["site_id"],
                                 detail={"assessment_id": str(situation["assessment_id"])})
        await db.commit()
    return {"recorded": new, "assessment_id": situation["assessment_id"]}


# ─── The person on the ground ────────────────────────────────────────────────

@router.get("/my-situations")
async def my_situations(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The open situations in front of the caller: those they were dispatched
    to first, then by risk. `assigned_to_me` marks the first kind, with when
    they were dispatched and what the dispatcher wrote.

    For a guard this is what they were dispatched to, and the other open
    situations at the site of the shift they are on — the latter only where the
    decision policy lets a guard decide there at all. For anyone else it is the
    open situations of the sites they may see.

    `my_last` is the caller's own last report on it — `ACCEPTED` or `ARRIVED` —
    so the phone knows which button comes next."""
    return await intel_field.mine(db, token.user_id, token.role_id, allowed, limit)


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ACCEPTED", "ARRIVED", "OBSERVATION"]
    note: str | None = Field(None, max_length=2000)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    via: Literal["web", "mobile"] = "mobile"
    #: Sent again with a retry, so that one report is one observation.
    client_ref: uuid.UUID | None = None


@router.post("/situations/{situation_id}/observations", status_code=201,
             dependencies=[Depends(require_permission("intel:decide"))])
async def record_observation(
    situation_id: uuid.UUID,
    body: ObservationIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Report from the ground: `ACCEPTED` (I have this), `ARRIVED` (I am
    there), or `OBSERVATION` with a `note` saying what was seen. A position is
    kept when the phone gives one.

    It is recorded as the caller's and changes nothing else: no alert, no
    incident, no dispatch. An arrival here is this layer's record; the
    incident's own arrival time is still set by the command centre. What to do
    about the situation remains a decision.

    A guard reports from the site of the shift they are on, or from a situation
    they were dispatched to."""
    _person(token)
    situation = await _situation(db, situation_id, allowed)
    if situation["closed_at"] is not None:
        raise HTTPException(409, "This situation is closed. Nothing more can be reported on it.")
    if not await intel_field.within_reach(db, situation, token.user_id, token.role_id):
        raise HTTPException(403, decisions.OUT_OF_REACH)
    if (body.latitude is None) != (body.longitude is None):
        raise HTTPException(422, "A position is a latitude and a longitude together, or neither.")
    if body.kind == "OBSERVATION" and not (body.note or "").strip():
        raise HTTPException(422, "An observation says what was seen: a note is needed.")
    if body.client_ref is not None:
        earlier = (await db.execute(text(
            "SELECT id, situation_id, user_id FROM security_observations WHERE client_ref = :c"),
            {"c": body.client_ref})).first()
        if earlier is not None:
            if earlier.situation_id != situation["id"] or str(earlier.user_id) != str(token.user_id):
                raise HTTPException(409, "This reference was already used for another report.")
            response.status_code = 200
            return {"id": earlier.id, "kind": body.kind, "replayed": True}
    row = await intel_field.record(
        db, situation_id=situation["id"], kind=body.kind, note=body.note, user_id=token.user_id,
        role_id=token.role_id, latitude=body.latitude, longitude=body.longitude, via=body.via,
        request_id=getattr(request.state, "request_id", None), client_ref=body.client_ref)
    await intel_audit.record(db, request, token, "intel.observation.record", "security_situation", situation["id"],
                             site_id=situation["site_id"],
                             detail={"observation_id": str(row["id"]), "kind": body.kind, "via": body.via,
                                     "with_position": body.latitude is not None})
    await db.commit()
    await _announce(request, token, "intel_observation_recorded", {
        "situation_id": situation["id"], "situation_number": situation["situation_number"],
        "observation_id": row["id"], "kind": body.kind, "by_role": token.role_id})
    return {"id": row["id"], "kind": body.kind, "observed_at": row["observed_at"]}


@router.get("/situations/{situation_id}/observations")
async def list_observations(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What was reported from the ground about a situation, oldest first: who,
    in what role, what, when, and from where if a position was given."""
    situation = await _situation(db, situation_id, allowed)
    return await intel_field.observations(db, situation["id"])


# ─── What the caller may decide ──────────────────────────────────────────────

NEEDS = {"DISPATCH_GUARD": ["guard_user_id"], "ESCALATE": ["escalate_to_user_id"]}


@router.get("/situations/{situation_id}/authority")
async def my_authority(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the caller may decide about this situation right now, and for each
    decision they may not take, why not. The same judgement that is made when a
    decision is recorded.

    `how` is `ALONE`, or `WITH_APPROVAL` when the decision would wait for a
    second person. `basis` says whether choosing it would follow what was
    suggested or override it; `needs_reason` whether a reason will be asked
    for. `carries_out` lists what the platform would then do."""
    situation = await _situation(db, situation_id, allowed)
    f = await decisions.facts(db, situation)
    mine = await decisions.permissions_of(db, token.role_id)
    policy = await decisions.policy_for(db, situation["site_id"])
    not_a_person = token.via_api_key or bool(token.support_session_id)
    in_reach = await intel_field.within_reach(db, situation, token.user_id, token.role_id)
    actions = []
    for action in decisions.DECISIONS:
        c = decisions.check(action, situation=situation, f=f, mine=mine, roles=policy["roles"], role_id=token.role_id,
                            in_reach=in_reach)
        why_not = ("A security decision is made by a person who is signed in." if not_a_person
                   else c.refusal[1] if c.refusal else None)
        actions.append({"action": action, "allowed": why_not is None, "how": c.how if why_not is None else None,
                        "basis": c.basis, "needs_reason": c.basis in ("OVERRIDE", "CLOSING"),
                        "needs": NEEDS.get(action, []), "why_not": why_not,
                        "carries_out": [s.action for s in c.steps]})
    return {
        "situation_id": situation["id"], "decision_status": situation["decision_status"],
        "closed": situation["closed_at"] is not None,
        "risk_level": f.assessment["risk_level"] if f.assessment else None,
        "incident": {"state": decisions.incident_state(situation, f.incident),
                     "id": f.incident["id"] if f.incident else None},
        "policy": {"source": policy["source"], "rule": policy["roles"].get(str(token.role_id)) or {}},
        "may_override": "intel:override" in mine, "may_approve": "intel:approve" in mine,
        "in_reach": in_reach,
        "suggested_action": decisions.first_suggestion(f.current),
        "reasons": [{"code": code, "label": label} for code, label in decisions.REASONS.items()],
        "actions": actions,
    }


@router.get("/situations/{situation_id}/responders", dependencies=[Depends(require_permission("intel:decide"))])
async def list_responders(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The people a decision can name, for someone who may decide: the guards
    one could be dispatched from — those on shift at the situation's site
    first, then those on shift elsewhere — and the admins, managers and
    supervisors it could be escalated to, other than the caller.

    A list to choose from. The layer does not choose, and does not say who is
    nearest or free: that is the officer's to judge."""
    situation = await _situation(db, situation_id, allowed)
    on_shift = ("EXISTS (SELECT 1 FROM shifts sh WHERE sh.guard_user_id = u.id AND sh.status = 'active' "
                "AND sh.actual_start IS NOT NULL AND sh.actual_end IS NULL{here})")
    guards = (await db.execute(text(f"""
        SELECT u.id AS user_id, u.full_name AS name,
               {on_shift.format(here=" AND sh.site_id = CAST(:site AS uuid)")} AS on_shift_here,
               {on_shift.format(here="")} AS on_shift
          FROM users u
         WHERE u.is_active AND u.role_id = 5
         ORDER BY 3 DESC, 4 DESC, u.full_name LIMIT 200
    """), {"site": str(situation["site_id"]) if situation["site_id"] else None})).mappings().all()
    seniors = (await db.execute(text("""
        SELECT u.id AS user_id, u.full_name AS name, u.role_id FROM users u
         WHERE u.is_active AND u.role_id = ANY(:roles) AND u.id <> CAST(:me AS uuid)
         ORDER BY u.role_id, u.full_name LIMIT 200
    """), {"roles": list(decisions.ESCALATION_ROLES), "me": str(token.user_id)})).mappings().all()
    return {"guards": [dict(g) for g in guards], "escalation": [dict(s) for s in seniors]}


# ─── What a drone could be asked, and what came back ─────────────────────────

@router.get("/situations/{situation_id}/aerial")
async def drone_picture(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Drones and this situation: what an officer could ask a drone to do, and
    what came of anything already asked.

    `sightings` are the situation's drone sightings, each with the state of the
    flight that saw it and whether that flight could be asked to hold and look
    again. `missions` are the missions the site already has switched on, and
    whether each could start now — listed for someone who may see the drone
    module. Both are a first answer: the drone module makes its own checks when
    it is asked, and when it refuses, its reason is on the step's record.

    `asked` is every look a decision on this situation asked for — the step,
    how it ended, and the look or flight since. `other_looks` are looks at
    these sightings asked for from the drone screens.

    Nothing is asked of a drone from here. That is a decision:
    `VERIFY_WITH_DRONE` with `drone_event_id` or `drone_mission_id`.

    The path says "aerial", not "drone", on purpose: every path with "drone" in
    it is the drone module's, held to that module's own permissions by its own
    tests. This one is the intelligence layer's, and reads under `intel:read`."""
    situation = await _situation(db, situation_id, allowed)
    return await intel_drone.picture(db, situation, await decisions.permissions_of(db, token.role_id))


# ─── Deciding ────────────────────────────────────────────────────────────────

class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    reason_code: str | None = None
    note: str | None = Field(None, max_length=2000)
    #: The assessment that was on the officer's screen, so the record can say
    #: if a newer one existed by the time they decided.
    seen_assessment_id: uuid.UUID | None = None
    guard_user_id: uuid.UUID | None = None
    escalate_to_user_id: uuid.UUID | None = None
    #: With VERIFY_WITH_DRONE, how the drone should look — the officer's own
    #: choice of one: hold the flight that saw this sighting, or start this
    #: mission. With neither, the decision is a record.
    drone_event_id: uuid.UUID | None = None
    drone_mission_id: uuid.UUID | None = None
    hold_seconds: int | None = Field(None, ge=intel_drone.HOLD_MIN, le=intel_drone.HOLD_MAX)
    via: Literal["web", "mobile"] = "web"
    #: Sent again with a retry, so that one press is one decision.
    client_ref: uuid.UUID | None = None


async def _drone_params(db: AsyncSession, body: DecisionIn, situation: dict) -> dict:
    """The decision's stored `params` for what it asks of a drone, or {} when
    it asks nothing. Refuses a choice that is not this situation's to make."""
    given = [name for name in ("drone_event_id", "drone_mission_id", "hold_seconds")
             if getattr(body, name) is not None]
    if not given:
        return {}
    if body.action != "VERIFY_WITH_DRONE":
        raise HTTPException(422, f"{', '.join(given)} can be given only with VERIFY_WITH_DRONE.")
    if body.drone_event_id is not None and body.drone_mission_id is not None:
        raise HTTPException(422, "Choose one: hold the flight that saw a sighting (drone_event_id), or start a "
                                 "mission (drone_mission_id).")
    if body.drone_event_id is not None:
        if not await intel_drone.sighting_in(db, situation["id"], body.drone_event_id):
            raise HTTPException(422, "drone_event_id must be a drone sighting that is one of this situation's "
                                     "events.")
        return {"drone_event_id": str(body.drone_event_id),
                "hold_seconds": intel_drone.hold_seconds(body.hold_seconds)}
    if body.drone_mission_id is None or body.hold_seconds is not None:
        raise HTTPException(422, "hold_seconds goes with drone_event_id: it is how long a flight holds.")
    if await intel_drone.mission_at(db, body.drone_mission_id, situation["site_id"]) is None:
        raise HTTPException(422, "drone_mission_id must be a mission that is switched on at this situation's site.")
    return {"drone_mission_id": str(body.drone_mission_id)}


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> dict | None:
    row = (await db.execute(text(
        "SELECT id, role_id FROM users WHERE id = CAST(:u AS uuid) AND is_active"), {"u": str(user_id)})).mappings().first()
    return dict(row) if row is not None else None


@router.post("/situations/{situation_id}/decisions", status_code=201,
             dependencies=[Depends(require_permission("intel:decide"))])
async def record_decision(
    situation_id: uuid.UUID,
    body: DecisionIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Record what the caller has decided to do about a situation, and carry it
    out through the platform's existing functions.

    `action` is one of the nine steps the layer can suggest, or `ACKNOWLEDGE`,
    `CONFIRM_INCIDENT`, `REQUEST_ASSISTANCE`, `FALSE_POSITIVE`, `RESOLVE`.

    Choosing a step that was not suggested, or one listed as not possible, is an
    override: it is accepted from someone who may override, and it needs
    `reason_code`. So do `FALSE_POSITIVE` and `RESOLVE`. `OTHER` needs a `note`.

    Where the decision policy lets the caller's role decide at this risk only
    with approval, the decision is recorded, nothing is carried out, and it
    waits for a second person. Otherwise each step is carried out now and the
    answer lists what was done, through which function, and how it ended.

    `DISPATCH_GUARD` needs `guard_user_id`; `ESCALATE` needs
    `escalate_to_user_id`, an admin, manager or supervisor other than the
    caller.

    `VERIFY_WITH_DRONE` may say how the drone should look: `drone_event_id`, one
    of the situation's drone sightings, asks the flight that saw it to hold for
    `hold_seconds` and look again; `drone_mission_id`, a mission switched on at
    the situation's site, starts it. Each is the drone module's own function
    and needs its own permission (`drone:operate`, `drone:mission:execute`).
    With neither, the decision is recorded and nothing is asked of a drone."""
    _person(token)
    if body.action not in decisions.DECISIONS:
        raise HTTPException(422, f"Unknown decision '{body.action}'. One of: {', '.join(decisions.DECISIONS)}.")
    if body.reason_code is not None and body.reason_code not in decisions.REASONS:
        raise HTTPException(422, f"Unknown reason '{body.reason_code}'. One of: {', '.join(decisions.REASONS)}.")
    situation = await _situation(db, situation_id, allowed, lock=True)

    # A retried request is answered with the decision it already recorded.
    if body.client_ref is not None:
        earlier = (await db.execute(text(
            "SELECT id, situation_id, actor_user_id FROM security_decisions WHERE client_ref = :c"),
            {"c": body.client_ref})).first()
        if earlier is not None:
            if earlier.situation_id != situation["id"] or str(earlier.actor_user_id) != str(token.user_id):
                raise HTTPException(409, "This reference was already used for another decision.")
            response.status_code = 200
            again = await decisions.get(db, earlier.id)
            again.pop("site_id", None)
            return {**again, "replayed": True}

    f = await decisions.facts(db, situation)
    mine = await decisions.permissions_of(db, token.role_id)
    policy = await decisions.policy_for(db, situation["site_id"])
    params: dict = await _drone_params(db, body, situation)
    c = decisions.check(body.action, situation=situation, f=f, mine=mine, roles=policy["roles"],
                        role_id=token.role_id, drone=decisions.drone_choice(params),
                        in_reach=await intel_field.within_reach(db, situation, token.user_id, token.role_id))
    if c.refusal is not None:
        raise HTTPException(*c.refusal)
    if params.get("drone_mission_id"):
        # The drone module's own licence gate, asked before anything is recorded.
        problem = await intel_drone.licence_problem(db)
        if problem is not None:
            raise HTTPException(403, problem)
    if c.basis in ("OVERRIDE", "CLOSING") and body.reason_code is None:
        what = "Closing a situation" if c.basis == "CLOSING" else "Choosing a step that was not suggested"
        raise HTTPException(422, f"{what} needs a reason. One of: {', '.join(decisions.REASONS)}.")
    if body.reason_code == "OTHER" and not (body.note or "").strip():
        raise HTTPException(422, "The reason 'OTHER' needs a note saying what it was.")

    if body.action == "DISPATCH_GUARD":
        guard = await _active_user(db, body.guard_user_id) if body.guard_user_id else None
        if guard is None:
            raise HTTPException(422, "Dispatching needs guard_user_id: an active user of this organisation.")
        params["guard_user_id"] = str(guard["id"])
    if body.action == "ESCALATE":
        target = await _active_user(db, body.escalate_to_user_id) if body.escalate_to_user_id else None
        if target is None or target["role_id"] not in decisions.ESCALATION_ROLES \
                or str(target["id"]) == str(token.user_id):
            raise HTTPException(422, "Escalating needs escalate_to_user_id: an active admin, manager or "
                                     "supervisor of this organisation, other than yourself.")
        params["escalate_to_user_id"] = str(target["id"])

    row = await decisions.insert_decision(
        db, situation=situation, f=f, action=body.action, basis=c.basis, recommendation=c.recommendation,
        reason_code=body.reason_code, note=body.note, user_id=token.user_id, role_id=token.role_id, how=c.how,
        policy={"source": policy["source"], "rule": policy["roles"].get(str(token.role_id)) or {}, "said": c.said},
        params=params, via=body.via, request_id=getattr(request.state, "request_id", None),
        client_ref=body.client_ref, seen_assessment_id=body.seen_assessment_id)
    await decisions.set_status(db, situation["id"],
                               decisions.status_after(body.action, c.how, situation["decision_status"]), row["id"])
    a = f.assessment or {}
    await intel_audit.record(
        db, request, token, "intel.decision.record", "security_situation", situation["id"],
        site_id=situation["site_id"], result="pending_approval" if c.how == "WITH_APPROVAL" else "ok",
        detail={"decision_id": str(row["id"]), "situation_number": situation["situation_number"],
                "decision": body.action, "basis": c.basis, "override": c.basis == "OVERRIDE",
                "reason_code": body.reason_code, "suggested_action": decisions.first_suggestion(f.current),
                "recommendation_id": str(c.recommendation["id"]) if c.recommendation else None,
                "assessment_id": str(a["id"]) if a else None, "risk_level": a.get("risk_level"),
                "risk_score": a.get("risk_score"), "authority": c.how, "via": body.via,
                "asked_of_a_drone": ("hold" if params.get("drone_event_id") else
                                     "launch" if params.get("drone_mission_id") else None)})
    await db.commit()

    if c.how == "ALONE":
        await intel_actions.carry_out(
            db, request, token, situation=situation, f=f, params=params, allowed=allowed,
            decision={"id": row["id"], "action": body.action, "reason_code": body.reason_code, "note": body.note,
                      "via": body.via})
    await decisions.scope(db, token.tenant_id)
    decision = await decisions.get(db, row["id"])
    decision.pop("site_id", None)
    await _announce(request, token, "intel_decision_pending_approval" if c.how == "WITH_APPROVAL"
                    else "intel_decision_recorded", _said(decision))
    return decision


@router.get("/situations/{situation_id}/decisions")
async def list_situation_decisions(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The decision trail of a situation, oldest first: who looked, what each
    person decided and why, any second person's verdict, and what was carried
    out — each kept apart from the others — and where it all ended: where the
    situation stands, and what stands behind any incident."""
    situation = await _situation(db, situation_id, allowed)
    reviews = (await db.execute(text(
        "SELECT r.user_id, u.full_name AS name, r.actor_role AS role_id, r.assessment_id, r.via, r.viewed_at "
        "  FROM security_reviews r LEFT JOIN users u ON u.id = r.user_id "
        " WHERE r.situation_id = :s ORDER BY r.viewed_at"), {"s": situation["id"]})).mappings().all()
    incident = (await decisions.facts(db, {**situation, "assessment_id": None})).incident
    return {"situation_id": situation["id"], "decision_status": situation["decision_status"],
            "closed_at": situation["closed_at"],
            "incident": {"state": decisions.incident_state(situation, incident),
                         "id": incident["id"] if incident else None,
                         "status": incident["status"] if incident else None},
            "reviews": [dict(r) for r in reviews],
            "decisions": await decisions.trail(db, situation["id"])}


@router.get("/decisions")
async def list_decisions(
    state: Literal["pending_approval"] | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    basis: str | None = Query(None),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Decisions across situations, the most recent first.
    `state=pending_approval` is the approver's queue: decisions that wait for a
    second person and are still the latest on their situation."""
    if action is not None and action not in decisions.DECISIONS:
        raise HTTPException(422, f"Unknown decision '{action}'. One of: {', '.join(decisions.DECISIONS)}.")
    if basis is not None and basis not in decisions.BASES:
        raise HTTPException(422, f"Unknown basis '{basis}'. One of: {', '.join(decisions.BASES)}.")
    where, params = [], {}
    scope = site_scope_clause(allowed, "s.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("s.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = str(site_id)
    for column, value, name in (("d.action", action, "action"), ("d.basis", basis, "basis")):
        if value is not None:
            where.append(f"{column} = :{name}")
            params[name] = value
    if since is not None:
        where.append("d.decided_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("d.decided_at <= :until")
        params["until"] = until
    if state == "pending_approval":
        where.append("d.authority = 'WITH_APPROVAL' AND p.id IS NULL AND s.last_decision_id = d.id "
                     "AND s.decision_status = 'PENDING_APPROVAL'")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    cap, start = clamp(limit, offset)
    rows = (await db.execute(text(
        decisions._DECISION + f" {clause} ORDER BY d.decided_at DESC, d.id DESC LIMIT :limit OFFSET :offset"),
        {**params, "limit": cap, "offset": start})).mappings().all()
    total = (await db.execute(text(
        "SELECT count(*) FROM security_decisions d JOIN security_situations s ON s.id = d.situation_id "
        f"  LEFT JOIN security_decision_approvals p ON p.decision_id = d.id {clause}"), params)).scalar_one()
    done = await decisions._actions(db, [r["id"] for r in rows])
    return {"items": [decisions.view(r, done.get(r["id"], [])) for r in rows], "total": total, "limit": cap,
            "offset": start, "has_more": start + cap < total}


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One decision, with any verdict on it and what was carried out."""
    d = await decisions.get(db, decision_id)
    if d is None or not is_site_allowed(allowed, d.pop("site_id")):
        raise HTTPException(404, "Decision not found")
    return d


# ─── A second person's verdict ───────────────────────────────────────────────

class VerdictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=2000)


async def _verdict(verdict: str, decision_id: uuid.UUID, body: VerdictIn | None, request: Request,
                   db: AsyncSession, token: TokenPayload, allowed) -> dict:
    _person(token)
    note = ((body.note if body else None) or "").strip() or None
    d = await decisions.get(db, decision_id)
    if d is None or not is_site_allowed(allowed, d["site_id"]):
        raise HTTPException(404, "Decision not found")
    situation = await _situation(db, d["situation_id"], allowed, lock=True)
    if d["authority"] != "WITH_APPROVAL":
        raise HTTPException(409, "This decision did not need approval.")
    if d["approval"] is not None:
        raise HTTPException(409, f"This decision has already been {d['approval']['verdict'].lower()}.")
    if situation["last_decision_id"] != d["id"] or situation["decision_status"] != "PENDING_APPROVAL":
        raise HTTPException(409, "A later decision has been made on this situation. This one can no longer "
                                 "be approved or rejected.")
    if str(d["decided_by"]["user_id"]) == str(token.user_id):
        raise HTTPException(403, "A decision is approved by a second person, not by the one who made it.")
    if verdict == "REJECTED" and note is None:
        raise HTTPException(422, "Rejecting needs a note saying why.")

    f = await decisions.facts(db, situation)
    mine = await decisions.permissions_of(db, token.role_id)
    policy = await decisions.policy_for(db, situation["site_id"])
    # The approver answers for it, so it is their authority that is asked: at
    # the risk the decision was made at, or the risk now if that is higher.
    now = f.assessment["risk_level"] if f.assessment else None
    was = d["risk_level"]
    level = max((lvl for lvl in (now, was) if lvl in LEVELS), key=LEVELS.index, default=None)
    how, said = decisions.authority(policy["roles"], token.role_id, level, d["action"])
    if how != "ALONE":
        raise HTTPException(403, f"Approving needs the authority to take this decision alone. {said}")
    if verdict == "APPROVED":
        steps = decisions.plan(d["action"], alerts=f.alerts, incident=f.incident,
                               drone=decisions.drone_choice(d["params"]))
        missing = [p for p in decisions.permissions_needed(steps) if p not in mine]
        if missing:
            raise HTTPException(403, f"Carrying this out needs the permission {', '.join(missing)}.")

    await db.execute(text("""
        INSERT INTO security_decision_approvals
               (tenant_id, decision_id, verdict, note, approver_user_id, approver_role, request_id)
        VALUES (current_setting('app.current_tenant')::uuid, :d, :verdict, :note, CAST(:u AS uuid), :role, :rid)
    """), {"d": d["id"], "verdict": verdict, "note": note, "u": str(token.user_id), "role": token.role_id,
           "rid": getattr(request.state, "request_id", None)})
    # Rejected, the situation is back to wanting a decision.
    after = decisions.status_after(d["action"], "ALONE", "AWAITING") if verdict == "APPROVED" else "AWAITING"
    await decisions.set_status(db, situation["id"], after, d["id"])
    await intel_audit.record(
        db, request, token, f"intel.decision.{'approve' if verdict == 'APPROVED' else 'reject'}",
        "security_situation", situation["id"], site_id=situation["site_id"],
        detail={"decision_id": str(d["id"]), "decision": d["action"], "risk_level": level,
                "decided_by_role": d["decided_by"]["role_id"]})
    await db.commit()

    if verdict == "APPROVED":
        await intel_actions.carry_out(
            db, request, token, situation=situation, f=f, params=d["params"] or {}, allowed=allowed,
            decision={"id": d["id"], "action": d["action"], "reason_code": d["reason_code"], "note": d["note"],
                      "via": d["via"]})
    await decisions.scope(db, token.tenant_id)
    out = await decisions.get(db, d["id"])
    out.pop("site_id", None)
    await _announce(request, token, f"intel_decision_{verdict.lower()}", _said(out))
    return out


@router.post("/decisions/{decision_id}/approve", dependencies=[Depends(require_permission("intel:approve"))])
async def approve_decision(
    decision_id: uuid.UUID,
    request: Request,
    body: VerdictIn | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Approve a decision that is waiting for a second person. It is then
    carried out, under the approver's own permissions. The approver must be
    someone other than the person who decided, and must have the authority to
    take that decision alone."""
    return await _verdict("APPROVED", decision_id, body, request, db, token, allowed)


@router.post("/decisions/{decision_id}/reject", dependencies=[Depends(require_permission("intel:approve"))])
async def reject_decision(
    decision_id: uuid.UUID,
    request: Request,
    body: VerdictIn | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Reject a decision that is waiting for a second person, with a note
    saying why. Nothing is carried out, and the situation is back to wanting a
    decision."""
    return await _verdict("REJECTED", decision_id, body, request, db, token, allowed)


# ─── Who may decide ──────────────────────────────────────────────────────────

class PolicyIn(BaseModel):
    """How far each role may decide. Replaces the policy as a whole."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, dict]
    note: str | None = Field(None, max_length=255)


def _policy_row(row) -> dict:
    stored = json.loads(row["roles"]) if isinstance(row["roles"], str) else row["roles"]
    return {"roles": stored, "effective": decisions.effective(stored), "note": row["note"],
            "updated_at": row["updated_at"], "updated_by_user_id": row["updated_by_user_id"]}


async def _policy_view(db: AsyncSession, allowed) -> dict:
    tenant = (await db.execute(text(
        "SELECT roles, note, updated_at, updated_by_user_id FROM security_decision_policies "
        " WHERE site_id IS NULL"))).mappings().first()
    params: dict = {}
    scope = site_scope_clause(allowed, "p.site_id", params)
    sites = (await db.execute(text(
        "SELECT p.site_id, s.name AS site_name, p.roles, p.note, p.updated_at, p.updated_by_user_id "
        "  FROM security_decision_policies p JOIN sites s ON s.id = p.site_id "
        f" WHERE p.site_id IS NOT NULL {('AND ' + scope) if scope else ''} ORDER BY s.name"), params)).mappings().all()
    return {
        "roles": decisions.POLICY_ROLES, "levels": list(LEVELS), "default": decisions.DEFAULT_POLICY,
        "always_allowed": list(decisions.ALWAYS),
        "tenant": _policy_row(tenant) if tenant is not None else None,
        "sites": [{"site_id": r["site_id"], "site_name": r["site_name"], **_policy_row(r)} for r in sites],
    }


@router.get("/decision-policy")
async def get_decision_policy(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Who may decide, and how far: the default, the organisation's own policy
    if it has set one, and each site that has its own. For each role, `alone`
    is the highest risk it may decide by itself and `with_approval` the highest
    it may decide with a second person's approval; a role with neither may not
    decide. A policy narrows what `intel:decide` allows and grants nothing to a
    role without it."""
    return await _policy_view(db, allowed)


async def _put_policy(db: AsyncSession, request: Request, token: TokenPayload, body: PolicyIn, site_id) -> None:
    try:
        decisions.validate_policy(body.roles)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    conflict = "(site_id) WHERE site_id IS NOT NULL" if site_id is not None else "(tenant_id) WHERE site_id IS NULL"
    await db.execute(text(f"""
        INSERT INTO security_decision_policies (tenant_id, site_id, roles, note, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :site, CAST(:roles AS jsonb), :note, CAST(:u AS uuid))
        ON CONFLICT {conflict} DO UPDATE
           SET roles = EXCLUDED.roles, note = EXCLUDED.note, updated_by_user_id = EXCLUDED.updated_by_user_id,
               updated_at = now()
    """), {"site": site_id, "roles": json.dumps(body.roles), "note": body.note, "u": str(token.user_id)})
    # The audit log's resource id is a UUID: a site's policy is filed under the
    # site, the organisation's own under none, and `scope` says which it was.
    await intel_audit.record(db, request, token, "intel.decision_policy.update", "security_decision_policy",
                             site_id, site_id=site_id,
                             detail={"scope": "site" if site_id is not None else "tenant", "roles": body.roles})
    await db.commit()


@router.put("/decision-policy", dependencies=[Depends(require_permission("intel:manage"))])
async def put_decision_policy(
    body: PolicyIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Set the organisation's decision policy. A role left out keeps the
    default. Audited."""
    await _put_policy(db, request, token, body, None)
    await decisions.scope(db, token.tenant_id)
    return await _policy_view(db, allowed)


async def _site_or_404(db: AsyncSession, site_id: uuid.UUID, allowed) -> None:
    found = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(site_id)})).first()
    if found is None or not is_site_allowed(allowed, site_id):
        raise HTTPException(404, "Site not found")


@router.put("/decision-policy/sites/{site_id}", dependencies=[Depends(require_permission("intel:manage"))])
async def put_site_decision_policy(
    site_id: uuid.UUID,
    body: PolicyIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Give one site its own decision policy, in place of the organisation's.
    Audited."""
    await _site_or_404(db, site_id, allowed)
    await _put_policy(db, request, token, body, site_id)
    await decisions.scope(db, token.tenant_id)
    return await _policy_view(db, allowed)


@router.delete("/decision-policy/sites/{site_id}", dependencies=[Depends(require_permission("intel:manage"))])
async def delete_site_decision_policy(
    site_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Remove a site's own policy, so that the organisation's applies there
    again. Audited."""
    await _site_or_404(db, site_id, allowed)
    removed = (await db.execute(text(
        "DELETE FROM security_decision_policies WHERE site_id = CAST(:s AS uuid) RETURNING id"),
        {"s": str(site_id)})).first()
    if removed is None:
        raise HTTPException(404, "This site has no policy of its own")
    await intel_audit.record(db, request, token, "intel.decision_policy.delete", "security_decision_policy",
                             site_id, site_id=site_id, detail={"scope": "site"})
    await db.commit()
    await decisions.scope(db, token.tenant_id)
    return await _policy_view(db, allowed)


# ─── Feedback: what it turned out to be, and how the suggestions fared ───────

class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    assessment_verdict: str | None = None
    recommendation_verdict: str | None = None
    note: str | None = Field(None, max_length=2000)


@router.post("/situations/{situation_id}/feedback", status_code=201,
             dependencies=[Depends(require_permission("intel:approve"))])
async def record_feedback(
    situation_id: uuid.UUID,
    body: FeedbackIn,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """A review of a closed situation by someone who may approve decisions:
    what it turned out to be (`outcome`), whether the layer's assessment was
    about right (`assessment_verdict`), and whether what it suggested was
    useful (`recommendation_verdict`).

    A review is a person's statement and nothing more. It changes no
    situation, decision, alert or incident; nothing in the platform is trained
    on it or adjusts itself because of it. One per reviewer per situation, and
    it cannot be edited afterwards. `UNDETERMINED` needs a note saying what is
    still not known."""
    _person(token)
    try:
        intel_feedback.validate(body.outcome, body.assessment_verdict, body.recommendation_verdict, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    situation = await _situation(db, situation_id, allowed)
    if situation["closed_at"] is None:
        raise HTTPException(409, "This situation is still open. What it turned out to be is said once it is closed.")
    try:
        row = await intel_feedback.record(
            db, situation=situation, outcome=body.outcome, assessment_verdict=body.assessment_verdict,
            recommendation_verdict=body.recommendation_verdict, note=body.note, user_id=token.user_id,
            role_id=token.role_id, request_id=getattr(request.state, "request_id", None))
    except IntegrityError as exc:
        await db.rollback()
        if "uq_secfb_reviewer" in str(exc.orig):
            raise HTTPException(409, "You have already reviewed this situation. A review is not edited; a second "
                                     "view is a second person's.") from exc
        raise
    await intel_audit.record(
        db, request, token, "intel.feedback.record", "security_situation", situation["id"],
        site_id=situation["site_id"],
        detail={"feedback_id": str(row["id"]), "situation_number": situation["situation_number"],
                "outcome": body.outcome, "assessment_verdict": body.assessment_verdict,
                "recommendation_verdict": body.recommendation_verdict})
    await db.commit()
    return {"id": row["id"], "situation_id": situation["id"], "reviewed_at": row["reviewed_at"],
            "outcome": body.outcome, "assessment_verdict": body.assessment_verdict,
            "recommendation_verdict": body.recommendation_verdict}


@router.get("/situations/{situation_id}/feedback")
async def list_feedback(
    situation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The reviews of a situation, oldest first, and what a review may say.
    `may_review` is whether the caller could add one now: they may approve
    decisions, the situation is closed, and they have not reviewed it yet."""
    situation = await _situation(db, situation_id, allowed)
    reviews = await intel_feedback.for_situation(db, situation["id"])
    mine = await decisions.permissions_of(db, token.role_id)
    already = any(str(r["user_id"]) == str(token.user_id) for r in reviews)
    return {
        "situation_id": situation["id"], "closed": situation["closed_at"] is not None,
        "may_review": ("intel:approve" in mine and situation["closed_at"] is not None and not already
                       and not token.via_api_key and not token.support_session_id),
        "outcomes": [{"code": c, "label": label} for c, label in intel_feedback.OUTCOMES.items()],
        "assessment_verdicts": [{"code": c, "label": label} for c, label in intel_feedback.ASSESSMENT_VERDICTS.items()],
        "recommendation_verdicts": [{"code": c, "label": label}
                                    for c, label in intel_feedback.RECOMMENDATION_VERDICTS.items()],
        "reviews": reviews,
    }


def _period(since: datetime | None, until: datetime | None, days: int) -> tuple[datetime, datetime]:
    until = until or datetime.now(timezone.utc)
    since = since or until - timedelta(days=days)
    if since > until:
        raise HTTPException(422, "The period ends before it starts.")
    return since, until


_FEEDBACK_DAYS = Query(30, ge=1, le=366, description="How many days back, when `from` is not given")


@router.get("/feedback/dataset", dependencies=[Depends(require_permission("intel:feedback:export"))])
async def export_feedback_dataset(
    request: Request,
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    days: int = _FEEDBACK_DAYS,
    site_id: uuid.UUID | None = Query(None),
    fmt: Literal["json", "csv"] = Query("json", alias="format"),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The feedback dataset: one row for each situation closed in the period
    — what the layer suggested first, what a person decided first and on what
    basis, how the matter was closed, and the latest review of it.

    For people to read. **Nothing in the platform is trained on it or changes
    because of it.** It carries roles, codes, numbers and times: no user's name
    or id, and none of the free text anyone wrote. Each export is in the audit
    log with who took it, the period and how many rows. At most 5,000 rows;
    `truncated` says when the period held more."""
    start, end = _period(since, until, days)
    if site_id is not None and not is_site_allowed(allowed, site_id):
        raise HTTPException(404, "Site not found")
    rows = await intel_feedback.dataset(db, allowed, since=start, until=end, site_id=site_id,
                                        limit=intel_feedback.MAX_ROWS + 1)
    truncated = len(rows) > intel_feedback.MAX_ROWS
    rows = rows[:intel_feedback.MAX_ROWS]
    await intel_audit.record(
        db, request, token, "intel.feedback.export", "security_feedback", None, site_id=site_id,
        detail={"rows": len(rows), "from": start.isoformat(), "to": end.isoformat(), "format": fmt,
                "truncated": truncated})
    await db.commit()
    if fmt == "csv":
        return PlainTextResponse(intel_feedback.to_csv(rows), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="security-feedback-{start:%Y%m%d}-{end:%Y%m%d}.csv"'})
    return {"from": start, "to": end, "columns": list(intel_feedback.COLUMNS), "rows": rows, "count": len(rows),
            "truncated": truncated, "use": intel_feedback.USE}


@router.get("/feedback/analytics")
async def feedback_analytics(
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    days: int = _FEEDBACK_DAYS,
    site_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """How the suggestions fared over the situations closed in the period:
    how often the first decision followed what was suggested and how often it
    went against it (and for which reasons), how many were closed as false
    positives, by suggested step and by kind of situation, and what reviewers
    said. Counts and rates only; a rate with nothing to divide by is null.

    It is a description for people, and it says so (`use`): nothing in the
    platform adjusts itself from these numbers."""
    start, end = _period(since, until, days)
    if site_id is not None and not is_site_allowed(allowed, site_id):
        raise HTTPException(404, "Site not found")
    rows = await intel_feedback.dataset(db, allowed, since=start, until=end, site_id=site_id)
    return {"from": start, "to": end, **intel_feedback.analyse(rows)}
