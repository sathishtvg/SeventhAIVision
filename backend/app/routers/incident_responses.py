"""Guard response: what a guard does on a dispatch, the clocks, and who is told when it is late.

Phase 4 of LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md; the design is in
GUARD_RESPONSE_ARCHITECTURE.md.

  GET  /desk                       open incidents, each with its response and its clocks
  GET  /recommend                  who is on shift at an incident's site, ranked, with the reasons
  GET  /mine                       what the caller has been sent on
  GET  /{incident}                 one incident's responses, steps, clocks and escalations
  POST /{incident}/accept | decline | en-route | arrived | report     the guard who was sent
  POST /{incident}/stand-down      whoever may dispatch
  GET  PUT /settings               the clocks: on or off, and the times they run to
  ...  /policies                   who else is told, and after how long
  GET  /escalations                what was told, to whom

THE DISPATCH IS NOT HERE. A person sends a guard through the endpoint that has
always done it (POST /api/v1/dispatch/incidents/{id}); `/recommend` only lists
who could be sent and why. Nothing in this router sends anybody anywhere.

ONLY THE GUARD WHO WAS SENT ANSWERS FOR A RESPONSE, and as a signed-in person:
never an API key, never a support session. What they do is written through to
the incident's own status, history and arrival time (services/incident_response.py).

THE CLOCKS TELL PEOPLE AND CHANGE NOTHING ELSE (services/response_sla.py).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import incident_response as responses
from app.services import intel_audit, response_notify
from app.services import response_sla as sla

DEFAULT_HOURS = 24
MAX_HOURS = 168
MAX_DESK = 200
SEVERITIES = ("critical", "high", "medium", "low", "info")
ROLE_NAMES = {2: "Administrators", 3: "Supervisors", 4: "Operators", 5: "Guards", 6: "Viewers", 8: "Managers"}
OVER = ("resolved", "closed")
PERMISSIONS = ("response:read", "response:act", "incident:dispatch", "sla:manage")

router = APIRouter(prefix="/api/v1/incident-responses", tags=["incident-responses"])
_READ = [Depends(require_permission("response:read"))]
_ACT = [Depends(require_permission("response:act"))]
_DISPATCH = [Depends(require_permission("incident:dispatch"))]
_MANAGE = [Depends(require_permission("sla:manage"))]


def _a_person(token: TokenPayload) -> None:
    """A step on a response, and a change to who is told, names who made it."""
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


async def _incident(db: AsyncSession, incident_id: uuid.UUID, *, lock: bool = False) -> dict | None:
    row = (await db.execute(text(f"{sla.INCIDENTS} WHERE i.id = CAST(:id AS uuid) {'FOR UPDATE OF i' if lock else ''}"),
                            {"id": str(incident_id)})).mappings().first()
    return dict(row) if row else None


_RESPONSE = """
    SELECT r.id, r.incident_id, r.site_id, r.guard_user_id, u.full_name AS guard_name, r.dispatched_at, r.state,
           r.accepted_at, r.declined_at, r.decline_reason, r.en_route_at, r.arrived_at, r.stood_down_at,
           r.stood_down_by_user_id, b.full_name AS stood_down_by_name, r.stand_down_reason
      FROM incident_responses r
      LEFT JOIN users u ON u.id = r.guard_user_id
      LEFT JOIN users b ON b.id = r.stood_down_by_user_id
"""


def _summary(incident: Mapping) -> dict:
    """An incident as this router gives it."""
    return {key: incident[key] for key in (
        "id", "title", "description", "severity", "status", "created_at", "resolved_at", "site_id", "site_name",
        "camera_id", "camera_name", "camera_location", "dispatched_guard_id", "dispatched_guard_name",
        "dispatched_at", "dispatch_notes", "guard_arrived_at", "sla_deadline_at", "sla_breached", "escalated_at",
        "acknowledged_at")}


def _may(incident: Mapping, response: Mapping | None, *, is_guard: bool, can_dispatch: bool) -> dict:
    """What this caller may do on the incident's current response."""
    live = (response is not None and response["state"] not in responses.FINAL and incident["status"] not in OVER)
    state = response["state"] if response else None
    return {
        "accept": bool(is_guard and live and responses.may("ACCEPTED", state)),
        "decline": bool(is_guard and live and responses.may("DECLINED", state)),
        "en_route": bool(is_guard and live and responses.may("EN_ROUTE", state)),
        "arrived": bool(is_guard and live and responses.may("ARRIVED", state)),
        "report": bool(is_guard and live),
        "stand_down": bool(can_dispatch and live),
    }


def _needs(incident: Mapping, response: Mapping | None) -> str:
    """What an open incident is waiting for."""
    if incident["dispatched_guard_id"] is None:
        return "DISPATCH" if incident["status"] == "open" else "RESOLVE"
    state = response["state"] if response else "SENT"
    return {"SENT": "ANSWER", "ACCEPTED": "ARRIVAL", "EN_ROUTE": "ARRIVAL"}.get(state, "RESOLVE")


async def _tell_sent(request: Request, tenant_id: str, sendings: list[dict]) -> None:
    redis = getattr(request.app.state, "redis", None)
    for sending in sendings:
        await response_notify.sent(redis, tenant_id, sending)


# ─── The desk ────────────────────────────────────────────────────────────────

@router.get("/desk", dependencies=_READ)
async def desk(
    request: Request,
    site_id: uuid.UUID | None = Query(None),
    view: Literal["active", "waiting", "sent", "late"] = Query("active"),
    hours: int = Query(DEFAULT_HOURS, ge=1, le=MAX_HOURS),
    limit: int = Query(100, ge=1, le=MAX_DESK),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Open incidents with who has been sent, where that response stands, and
    the three clocks. `active` is everything with a guard sent plus what was
    opened in the last `hours`; `waiting` has nobody sent; `sent` has; `late`
    has a clock that has run out."""
    if site_id is not None and not is_site_allowed(allowed, str(site_id)):
        raise HTTPException(404, "Site not found")
    now = datetime.now(timezone.utc)
    sendings = await responses.open_sendings(db)

    params: dict = {"since": now - timedelta(hours=hours)}
    where = ["i.status NOT IN ('resolved','closed')"]
    scope = site_scope_clause(allowed, "c.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("c.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    if view == "waiting":
        where += ["i.dispatched_guard_id IS NULL", "i.created_at >= :since"]
    elif view == "sent":
        where.append("i.dispatched_guard_id IS NOT NULL")
    else:
        where.append("(i.dispatched_guard_id IS NOT NULL OR i.created_at >= :since)")
    rows = (await db.execute(text(f"""{sla.INCIDENTS}
         WHERE {' AND '.join(where)}
         ORDER BY CASE i.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3
                                  WHEN 'low' THEN 4 ELSE 5 END, i.created_at DESC
         LIMIT {MAX_DESK + 1}
    """), params)).mappings().all()

    ids = [r["id"] for r in rows]
    latest: dict[Any, dict] = {}
    told: dict[Any, int] = {}
    if ids:
        for r in (await db.execute(text(f"""
            SELECT DISTINCT ON (r.incident_id) * FROM ({_RESPONSE}) r
             WHERE r.incident_id = ANY(:ids) ORDER BY r.incident_id, r.dispatched_at DESC
        """), {"ids": ids})).mappings():
            latest[r["incident_id"]] = dict(r)
        for r in await db.execute(text(
            "SELECT incident_id, count(*) AS n FROM incident_escalations WHERE incident_id = ANY(:ids) "
            "GROUP BY incident_id"), {"ids": ids}):
            told[r.incident_id] = r.n

    since = await sla.enabled_since(db)
    by_severity = await sla.configs(db)
    mine = await _held(db, token.role_id)
    items = []
    for incident in rows:
        last = latest.get(incident["id"])
        # The response of the sending that stands now — not one that is over.
        current = last if last and incident["dispatched_at"] and last["dispatched_at"] == incident["dispatched_at"] \
            else None
        state = sla.clocks(incident, by_severity.get(incident["severity"]), now)
        late = [name for name in sla.CLOCKS if state[name]["breached"]]
        if view == "late" and not late:
            continue
        items.append({
            **_summary(incident), "response": current, "last_response": last, "clocks": state, "late": late,
            "judged": since is not None and incident["created_at"] >= since,
            "needs": _needs(incident, current), "escalations": told.get(incident["id"], 0),
            "may": _may(incident, current, is_guard=str(incident["dispatched_guard_id"]) == token.user_id,
                        can_dispatch="incident:dispatch" in mine),
        })
    more = len(rows) > MAX_DESK or len(items) > limit
    items = items[:limit]
    answer = {
        "items": items, "has_more": more, "view": view, "hours": hours, "as_of": now,
        "counts": {"waiting": sum(1 for i in items if i["needs"] == "DISPATCH"),
                   "unanswered": sum(1 for i in items if i["needs"] == "ANSWER"),
                   "on_the_way": sum(1 for i in items if i["needs"] == "ARRIVAL"),
                   "late": sum(1 for i in items if i["late"])},
        "sla_enabled": since is not None, "sla_since": since,
        "can_dispatch": "incident:dispatch" in mine, "can_manage": "sla:manage" in mine,
        "note": sla.NOTE,
    }
    await db.commit()
    await _tell_sent(request, token.tenant_id, sendings)
    return answer


@router.get("/recommend", dependencies=_READ + _DISPATCH)
async def recommend(
    incident_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Who is on shift at the incident's site, ranked, each with the parts their
    score is made of. A suggestion for a person to read: nobody is sent by it.
    The person dispatches through the dispatch endpoint, as before."""
    incident = await _incident(db, incident_id)
    if incident is None or not is_site_allowed(allowed, incident["site_id"]):
        raise HTTPException(404, "Incident not found")
    answer = await responses.recommend(db, incident, datetime.now(timezone.utc), allowed)
    return {**answer, "incident": _summary(incident)}


@router.get("/mine", dependencies=_ACT)
async def mine(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """What the caller has been sent on and is still on: each incident, where
    their response stands, and what they may do next."""
    now = datetime.now(timezone.utc)
    await responses.open_sendings(db, guard_user_id=token.user_id)
    rows = (await db.execute(text(f"""{sla.INCIDENTS}
         WHERE i.dispatched_guard_id = CAST(:me AS uuid) AND i.status NOT IN ('resolved','closed')
         ORDER BY i.dispatched_at DESC LIMIT 50
    """), {"me": token.user_id})).mappings().all()
    by_severity = await sla.configs(db)
    items = []
    for incident in rows:
        current = (await db.execute(text(f"{_RESPONSE} WHERE r.incident_id = :i AND r.dispatched_at = :at"),
                                    {"i": incident["id"], "at": incident["dispatched_at"]})).mappings().first()
        if current is None or current["state"] in responses.FINAL:
            continue
        items.append({**_summary(incident), "response": dict(current),
                      "arrival": sla.clocks(incident, by_severity.get(incident["severity"]), now)["ARRIVAL"],
                      "may": _may(incident, current, is_guard=True, can_dispatch=False)})
    answer = {"items": items, "as_of": now}
    await db.commit()
    return answer


# ─── The clocks, and who is told ─────────────────────────────────────────────

@router.get("/settings", dependencies=_READ)
async def read_settings(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Whether the clocks are on, since when, and the times they run to."""
    since = await sla.enabled_since(db)
    by_severity = await sla.configs(db)
    held = await _held(db, token.role_id)
    return {
        "sla_enabled": since is not None, "sla_since": since,
        "times": [{"severity": s, "set": s in by_severity, **({
            "ack_within_seconds": by_severity[s]["ack_within_seconds"],
            "dispatch_within_seconds": by_severity[s]["dispatch_within_seconds"],
            "resolve_within_seconds": by_severity[s]["resolve_within_seconds"],
            "escalation_user_id": by_severity[s]["escalation_user_id"],
            "escalation_user_name": by_severity[s]["escalation_user_name"]} if s in by_severity else {})}
            for s in SEVERITIES],
        "triggers": list(sla.TRIGGERS), "severities": list(SEVERITIES),
        "notify_roles": [{"role_id": r, "name": ROLE_NAMES[r]} for r in sla.NOTIFY_ROLES],
        "can_manage": "sla:manage" in held, "note": sla.NOTE,
    }


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sla_enabled: StrictBool


@router.put("/settings", dependencies=_READ + _MANAGE)
async def switch_clocks(
    body: SwitchBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Switch the clocks on or off for the whole organisation. Switched on,
    they judge the incidents opened from that moment: nothing older is marked
    late. Saying what is already so changes nothing, and keeps that moment."""
    _a_person(token)
    if allowed is not None:
        raise HTTPException(403, "The clocks are switched on and off for the whole organisation, by somebody "
                                 "who is not held to particular sites.")
    was = await sla.enabled_since(db)
    if (was is not None) != body.sla_enabled:
        await db.execute(text("""
            INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, :k, CAST(:v AS jsonb), CAST(:who AS uuid))
            ON CONFLICT (tenant_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_by_user_id = EXCLUDED.updated_by_user_id,
                          updated_at = now()
        """), {"k": sla.ENABLED_KEY, "v": json.dumps(body.sla_enabled), "who": token.user_id})
        await intel_audit.record(db, request, token,
                                 "response.sla.enable" if body.sla_enabled else "response.sla.disable",
                                 "tenant_setting", None, detail={"setting": sla.ENABLED_KEY})
    since = await sla.enabled_since(db)
    answer = {"sla_enabled": since is not None, "sla_since": since, "changed": (was is not None) != body.sla_enabled}
    await db.commit()
    return answer


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    site_id: uuid.UUID | None = None
    severity: str | None = None
    trigger: str
    after_seconds: int = Field(..., ge=30, le=604800)
    notify_role_id: int | None = None
    notify_user_id: uuid.UUID | None = None


class PolicyChange(BaseModel):
    """Only what is given is changed. What a policy watches is what it is:
    retire it and write another."""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=120)
    site_id: uuid.UUID | None = None
    severity: str | None = None
    after_seconds: int | None = Field(None, ge=30, le=604800)
    notify_role_id: int | None = None
    notify_user_id: uuid.UUID | None = None


async def _checked_policy(db: AsyncSession, allowed, *, name, site_id, severity, role_id, user_id) -> None:
    """What a policy has to be before it is saved."""
    if not name or not name.strip():
        raise HTTPException(422, "A policy has a name.")
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(422, f"Unknown severity '{severity}'. One of: {', '.join(SEVERITIES)}.")
    if (role_id is None) == (user_id is None):
        raise HTTPException(422, "A policy tells everybody in one role, or one person: give one of the two.")
    if role_id is not None and role_id not in sla.NOTIFY_ROLES:
        raise HTTPException(422, "A policy cannot be addressed to that role.")
    if site_id is not None:
        known = (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"),
                                  {"s": str(site_id)})).scalar()
        if not known or not is_site_allowed(allowed, str(site_id)):
            raise HTTPException(404, "Site not found")
    elif allowed is not None:
        raise HTTPException(422, "You are held to particular sites: a policy you write names one of them.")
    if user_id is not None:
        person = (await db.execute(text("SELECT role_id, is_active FROM users WHERE id = CAST(:u AS uuid)"),
                                   {"u": str(user_id)})).first()
        if person is None or not person.is_active or person.role_id not in sla.NOTIFY_ROLES:
            raise HTTPException(422, "That person cannot be told: they are not an active member of staff here.")
        if site_id is not None and not await sla.people(db, site_id, user_id=user_id):
            raise HTTPException(422, "That person may not see this site, so they would never be told.")


async def _policy(db: AsyncSession, policy_id, allowed) -> dict:
    found = [p for p in await sla.policies(db, with_retired=True) if str(p["id"]) == str(policy_id)]
    # One for every site is seen by everybody; one for a site, by whoever may see that site.
    if not found or (found[0]["site_id"] is not None and not is_site_allowed(allowed, found[0]["site_id"])):
        raise HTTPException(404, "Policy not found")
    return found[0]


@router.get("/policies", dependencies=_READ)
async def list_policies(
    include_retired: bool = Query(False),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Who else is told, and after how long. Somebody held to particular sites
    sees the policies of those sites and the ones that are for every site."""
    held = await _held(db, token.role_id)
    found = await sla.policies(db, with_retired=include_retired and "sla:manage" in held)
    return {"items": [p for p in found if p["site_id"] is None or is_site_allowed(allowed, p["site_id"])],
            "can_manage": "sla:manage" in held}


@router.post("/policies", status_code=201, dependencies=_READ + _MANAGE)
async def write_policy(
    body: PolicyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Write a policy: when something has still not happened after so long,
    tell this role or this person. It tells; it does nothing else."""
    _a_person(token)
    if body.trigger not in sla.TRIGGERS:
        raise HTTPException(422, f"Unknown trigger '{body.trigger}'. One of: {', '.join(sla.TRIGGERS)}.")
    await _checked_policy(db, allowed, name=body.name, site_id=body.site_id, severity=body.severity,
                          role_id=body.notify_role_id, user_id=body.notify_user_id)
    new_id = (await db.execute(text("""
        INSERT INTO escalation_policies
               (tenant_id, name, site_id, severity, trigger, after_seconds, notify_role_id, notify_user_id,
                created_by_user_id, updated_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :name, CAST(:site AS uuid), :severity, :trigger,
                :after, :role, CAST(:person AS uuid), CAST(:who AS uuid), CAST(:who AS uuid))
        RETURNING id
    """), {"name": body.name.strip(), "site": str(body.site_id) if body.site_id else None,
           "severity": body.severity, "trigger": body.trigger, "after": body.after_seconds,
           "role": body.notify_role_id, "person": str(body.notify_user_id) if body.notify_user_id else None,
           "who": token.user_id})).scalar()
    await intel_audit.record(db, request, token, "response.policy.create", "escalation_policy", new_id,
                             site_id=body.site_id, detail={"name": body.name.strip(), "trigger": body.trigger,
                                                           "after_seconds": body.after_seconds})
    answer = await _policy(db, new_id, allowed)
    await db.commit()
    return answer


@router.patch("/policies/{policy_id:uuid}", dependencies=_READ + _MANAGE)
async def change_policy(
    policy_id: uuid.UUID,
    body: PolicyChange,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Change a policy. Only what is given is changed; naming a role removes the
    person, and naming a person removes the role. Steps already recorded keep
    the name the policy had then."""
    _a_person(token)
    current = await _policy(db, policy_id, allowed)
    if current["site_id"] is None and allowed is not None:
        raise HTTPException(403, "A policy for every site is changed by somebody who is not held to "
                                 "particular sites.")
    if not current["is_active"]:
        raise HTTPException(409, "This policy is retired. Restore it to change it.")
    given = body.model_fields_set
    if not given:
        raise HTTPException(422, "Nothing was given to change.")
    role, person = current["notify_role_id"], current["notify_user_id"]
    if "notify_role_id" in given and body.notify_role_id is not None:
        role, person = body.notify_role_id, body.notify_user_id if "notify_user_id" in given else None
    elif "notify_user_id" in given and body.notify_user_id is not None:
        role, person = None, body.notify_user_id
    merged = {
        "name": body.name if "name" in given else current["name"],
        "site_id": body.site_id if "site_id" in given else current["site_id"],
        "severity": body.severity if "severity" in given else current["severity"],
        "after_seconds": body.after_seconds if "after_seconds" in given and body.after_seconds is not None
        else current["after_seconds"],
    }
    await _checked_policy(db, allowed, name=merged["name"], site_id=merged["site_id"],
                          severity=merged["severity"], role_id=role, user_id=person)
    await db.execute(text("""
        UPDATE escalation_policies
           SET name = :name, site_id = CAST(:site AS uuid), severity = :severity, after_seconds = :after,
               notify_role_id = :role, notify_user_id = CAST(:person AS uuid),
               updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"name": merged["name"].strip(), "site": str(merged["site_id"]) if merged["site_id"] else None,
           "severity": merged["severity"], "after": merged["after_seconds"], "role": role,
           "person": str(person) if person else None, "who": token.user_id, "id": str(policy_id)})
    await intel_audit.record(db, request, token, "response.policy.update", "escalation_policy", policy_id,
                             site_id=merged["site_id"], detail={"name": current["name"], "changed": sorted(given)})
    answer = await _policy(db, policy_id, allowed)
    await db.commit()
    return answer


async def _set_policy_active(db, request, token, policy_id, allowed, active: bool) -> dict:
    _a_person(token)
    current = await _policy(db, policy_id, allowed)
    if current["site_id"] is None and allowed is not None:
        raise HTTPException(403, "A policy for every site is changed by somebody who is not held to "
                                 "particular sites.")
    if current["is_active"] == active:
        raise HTTPException(409, "This policy is already active." if active else "This policy is already retired.")
    await db.execute(text("""
        UPDATE escalation_policies SET is_active = :active, updated_by_user_id = CAST(:who AS uuid),
               updated_at = now() WHERE id = CAST(:id AS uuid)
    """), {"active": active, "who": token.user_id, "id": str(policy_id)})
    await intel_audit.record(db, request, token,
                             "response.policy.restore" if active else "response.policy.retire",
                             "escalation_policy", policy_id, site_id=current["site_id"],
                             detail={"name": current["name"]})
    await db.commit()
    return {"is_active": active}


@router.post("/policies/{policy_id:uuid}/retire", dependencies=_READ + _MANAGE)
async def retire_policy(
    policy_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Stop a policy telling anybody. It is kept, and what it told is kept."""
    return await _set_policy_active(db, request, token, policy_id, allowed, False)


@router.post("/policies/{policy_id:uuid}/restore", dependencies=_READ + _MANAGE)
async def restore_policy(
    policy_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Put a retired policy back to work."""
    return await _set_policy_active(db, request, token, policy_id, allowed, True)


_ESCALATION = """
    SELECT e.id, e.incident_id, i.title AS incident_title, i.severity, e.site_id, s.name AS site_name, e.kind,
           e.clock, e.policy_id, e.policy_name, e.sending_at, e.due_at, e.notify_role_id, e.notify_user_id,
           u.full_name AS notify_user_name, e.recipients, e.created_at,
           COALESCE(ev.notification_sent, FALSE) AS notification_sent
      FROM incident_escalations e
      JOIN incidents i ON i.id = e.incident_id
      LEFT JOIN sites s ON s.id = e.site_id
      LEFT JOIN users u ON u.id = e.notify_user_id
      LEFT JOIN escalation_events ev ON ev.id = e.escalation_event_id
"""


def _escalation(row: Mapping) -> dict:
    return {**row, "notify_role_name": ROLE_NAMES.get(row["notify_role_id"])}


@router.get("/escalations", dependencies=_READ)
async def list_escalations(
    incident_id: uuid.UUID | None = Query(None),
    hours: int = Query(DEFAULT_HOURS, ge=1, le=MAX_HOURS),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What ran out or fell due, when, and who it was addressed to — newest
    first. `recipients` is how many people that was; `notification_sent`
    whether the telling went out."""
    params: dict = {"limit": limit}
    where = []
    if incident_id is not None:
        where.append("e.incident_id = CAST(:incident AS uuid)")
        params["incident"] = str(incident_id)
    else:
        where.append("e.created_at >= :since")
        params["since"] = datetime.now(timezone.utc) - timedelta(hours=hours)
    scope = site_scope_clause(allowed, "e.site_id", params)
    if scope:
        where.append(scope)
    rows = (await db.execute(text(f"{_ESCALATION} WHERE {' AND '.join(where)} "
                                  "ORDER BY e.created_at DESC LIMIT :limit"), params)).mappings().all()
    return {"items": [_escalation(r) for r in rows]}


# ─── One incident's response ─────────────────────────────────────────────────

async def _detail(db: AsyncSession, incident: Mapping, token: TokenPayload, held: frozenset[str],
                  *, only_guard: str | None = None) -> dict:
    """An incident's responses, steps, clocks and escalations. `only_guard`
    narrows it to that guard's own responses, with nothing of who else was told."""
    now = datetime.now(timezone.utc)
    params: dict = {"i": incident["id"]}
    narrow = ""
    if only_guard:
        narrow = "AND r.guard_user_id = CAST(:guard AS uuid)"
        params["guard"] = only_guard
    found = [dict(r) for r in (await db.execute(text(
        f"{_RESPONSE} WHERE r.incident_id = :i {narrow} ORDER BY r.dispatched_at DESC"), params)).mappings()]
    steps = (await db.execute(text("""
        SELECT st.id, st.response_id, st.step, st.actor_user_id, a.full_name AS actor_name, st.actor_role, st.note,
               st.latitude, st.longitude, st.occurred_at
          FROM incident_response_steps st LEFT JOIN users a ON a.id = st.actor_user_id
         WHERE st.response_id = ANY(:ids) ORDER BY st.occurred_at, st.id
    """), {"ids": [r["id"] for r in found]})).mappings().all() if found else []
    current = next((r for r in found if incident["dispatched_at"] and r["dispatched_at"] == incident["dispatched_at"]),
                   None)
    since = await sla.enabled_since(db)
    by_severity = await sla.configs(db)
    told = [] if only_guard else [_escalation(r) for r in (await db.execute(text(
        f"{_ESCALATION} WHERE e.incident_id = :i ORDER BY e.created_at"), {"i": incident["id"]})).mappings()]
    return {
        "incident": _summary(incident), "response": current, "responses": found,
        "steps": [dict(s) for s in steps],
        "clocks": sla.clocks(incident, by_severity.get(incident["severity"]), now),
        "judged": since is not None and incident["created_at"] >= since, "sla_enabled": since is not None,
        "escalations": told, "as_of": now,
        "may": _may(incident, current, is_guard=str(incident["dispatched_guard_id"]) == token.user_id,
                    can_dispatch="incident:dispatch" in held),
    }


@router.get("/{incident_id:uuid}")
async def read_response(
    incident_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One incident's responses and their steps, its clocks and what was told.

    For whoever may read responses, at the sites they may see. A guard who was
    sent on it reads their own response to it and no more."""
    held = await _held(db, token.role_id)
    if not held & {"response:read", "response:act"}:
        raise HTTPException(403, "Missing permission: response:read")
    incident = await _incident(db, incident_id)
    reader = (incident is not None and "response:read" in held and is_site_allowed(allowed, incident["site_id"]))
    sendings = await responses.open_sendings(db, guard_user_id=incident["dispatched_guard_id"]) \
        if incident and incident["dispatched_guard_id"] else []
    if reader:
        answer = await _detail(db, incident, token, held)
    else:
        was_sent = incident is not None and "response:act" in held and (await db.execute(text(
            "SELECT 1 FROM incident_responses WHERE incident_id = :i AND guard_user_id = CAST(:me AS uuid) LIMIT 1"),
            {"i": incident["id"], "me": token.user_id})).scalar()
        if not was_sent:
            raise HTTPException(404, "Incident not found")
        answer = await _detail(db, incident, token, held, only_guard=token.user_id)
    await db.commit()
    await _tell_sent(request, token.tenant_id, [s for s in sendings if str(s["guard_user_id"]) != token.user_id])
    return answer


class StepBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=2000)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class DeclineBody(StepBody):
    reason: str = Field(..., min_length=1, max_length=2000)


class ReportBody(StepBody):
    note: str = Field(..., min_length=1, max_length=2000)


class StandDownBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000)


_STEP_WORDS = {"ACCEPTED": "accept", "DECLINED": "decline", "EN_ROUTE": "en_route", "ARRIVED": "arrive",
               "REPORTED": "report"}
_REFUSALS = {
    "ACCEPTED": "This dispatch has already been answered.",
    "DECLINED": "You have already set off or arrived: ask to be stood down instead.",
    "EN_ROUTE": "You are already on the way, or there.",
    "ARRIVED": "Your arrival is already recorded.",
    "REPORTED": "This response is over: there is nothing to report on.",
}


async def _take_step(db: AsyncSession, request: Request, token: TokenPayload, incident_id: uuid.UUID, step: str,
                     body: StepBody, note: str | None) -> dict:
    """One step by the guard who was sent."""
    _a_person(token)
    if (body.latitude is None) != (body.longitude is None):
        raise HTTPException(422, "A position needs both a latitude and a longitude.")
    incident = await _incident(db, incident_id, lock=True)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    if str(incident["dispatched_guard_id"]) != token.user_id:
        raise HTTPException(403, "Only the guard who was sent on this incident can answer for the response.")
    if incident["status"] in OVER:
        raise HTTPException(409, "This incident is already resolved.")
    response = await responses.ensure(db, incident)
    try:
        if step == "REPORTED":
            await responses.report(db, incident, response, user_id=token.user_id, role_id=token.role_id,
                                   note=note, latitude=body.latitude, longitude=body.longitude)
        else:
            await responses.advance(db, incident, response, step, user_id=token.user_id, role_id=token.role_id,
                                    note=note, latitude=body.latitude, longitude=body.longitude)
    except responses.NotAllowed as exc:
        raise HTTPException(409, {"message": _REFUSALS[step], "state": response["state"]}) from exc
    await intel_audit.record(db, request, token, f"response.{_STEP_WORDS[step]}", "incident_response",
                             response["id"], site_id=incident["site_id"],
                             detail={"incident_id": str(incident["id"]), "from_state": response["state"]})
    after = await _incident(db, incident_id)
    held = await _held(db, token.role_id)
    answer = await _detail(db, after, token, held, only_guard=token.user_id)
    name = (await db.execute(text("SELECT full_name FROM users WHERE id = CAST(:u AS uuid)"),
                             {"u": token.user_id})).scalar() if step == "DECLINED" else None
    await db.commit()
    if step == "DECLINED":
        await response_notify.declined(getattr(request.app.state, "redis", None), token.tenant_id, incident, name,
                                       note or "")
    return answer


@router.post("/{incident_id:uuid}/accept", dependencies=_ACT)
async def accept(
    incident_id: uuid.UUID,
    request: Request,
    body: StepBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The guard who was sent says they are taking it."""
    body = body or StepBody()
    return await _take_step(db, request, token, incident_id, "ACCEPTED", body, body.note)


@router.post("/{incident_id:uuid}/decline", dependencies=_ACT)
async def decline(
    incident_id: uuid.UUID,
    body: DeclineBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The guard who was sent says they cannot attend, and why. The incident
    goes back to having nobody sent, and the desk is told. Nobody else is sent
    in their place: a person does that."""
    if not body.reason.strip():
        raise HTTPException(422, "Say why you cannot attend.")
    return await _take_step(db, request, token, incident_id, "DECLINED", body, body.reason.strip())


@router.post("/{incident_id:uuid}/en-route", dependencies=_ACT)
async def en_route(
    incident_id: uuid.UUID,
    request: Request,
    body: StepBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The guard who was sent says they have set off. The incident moves to
    `en_route`, with where it was said from."""
    body = body or StepBody()
    return await _take_step(db, request, token, incident_id, "EN_ROUTE", body, body.note)


@router.post("/{incident_id:uuid}/arrived", dependencies=_ACT)
async def arrived(
    incident_id: uuid.UUID,
    request: Request,
    body: StepBody | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The guard who was sent says they are there. The incident moves to
    `on_scene` and its arrival time is stamped — which stops the arrival clock."""
    body = body or StepBody()
    return await _take_step(db, request, token, incident_id, "ARRIVED", body, body.note)


@router.post("/{incident_id:uuid}/report", dependencies=_ACT)
async def report(
    incident_id: uuid.UUID,
    body: ReportBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """What the guard who was sent found, said from the ground. It is added to
    the response's steps and changes nothing else."""
    if not body.note.strip():
        raise HTTPException(422, "A report says something.")
    return await _take_step(db, request, token, incident_id, "REPORTED", body, body.note.strip())


@router.post("/{incident_id:uuid}/stand-down", dependencies=_READ + _DISPATCH)
async def stand_down(
    incident_id: uuid.UUID,
    body: StandDownBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Call off the guard who was sent, and say why. The incident goes back to
    having nobody sent — open again, if the guard had not arrived. The guard is
    told. Nobody else is sent by this."""
    _a_person(token)
    if not body.reason.strip():
        raise HTTPException(422, "Say why the guard is being stood down.")
    incident = await _incident(db, incident_id, lock=True)
    if incident is None or not is_site_allowed(allowed, incident["site_id"]):
        raise HTTPException(404, "Incident not found")
    if incident["dispatched_guard_id"] is None:
        raise HTTPException(409, "Nobody is sent on this incident.")
    if incident["status"] in OVER:
        raise HTTPException(409, "This incident is already resolved.")
    response = await responses.ensure(db, incident)
    try:
        await responses.stand_down(db, incident, response, user_id=token.user_id, role_id=token.role_id,
                                   reason=body.reason.strip())
    except responses.NotAllowed as exc:
        raise HTTPException(409, {"message": "That response is already over.", "state": response["state"]}) from exc
    await intel_audit.record(db, request, token, "response.stand_down", "incident_response", response["id"],
                             site_id=incident["site_id"],
                             detail={"incident_id": str(incident["id"]), "from_state": response["state"],
                                     "guard_user_id": str(incident["dispatched_guard_id"])})
    after = await _incident(db, incident_id)
    held = await _held(db, token.role_id)
    answer = await _detail(db, after, token, held)
    await db.commit()
    await response_notify.stood_down(getattr(request.app.state, "redis", None), token.tenant_id, incident,
                                     body.reason.strip())
    return answer
