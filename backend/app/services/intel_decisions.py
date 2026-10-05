"""Decisions: what a person chose to do about a situation.

  the layer suggests  ─►  A PERSON DECIDES  ─►  the platform carries it out
  (intel_recommend)       (this module)         (intel_actions)

A DECISION IS A PERSON'S. It is recorded with who made it, in what role, on
which assessment, whether it followed what was suggested, and why if it did
not. Nothing in the layer makes one on anybody's behalf: there is no default
decision, no timeout that decides, and no code path from the runner to here.

AN OVERRIDE IS NEVER BLOCKED, AND ALWAYS SAYS WHY. An officer who may override
can choose any step, whatever was suggested, including one the layer listed as
not possible. What is asked for is a reason from a fixed list.

WHO MAY DECIDE IS CONFIGURATION, AND ONLY EVER NARROWS. A role needs the
permission `intel:decide` first. The decision policy then says how far that
role may decide alone and how far with a second person's approval, by risk, for
the tenant or for one site. A policy cannot give a role what its permissions do
not.

This module holds the rules and the layer's own records. It carries nothing
out: that is `intel_actions`, called by the API after a decision is saved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.intel_recommend import ACTIONS as STEPS
from app.services.intel_risk import LEVELS

#: What only a person can choose: none of these is ever suggested.
OWN = ("ACKNOWLEDGE", "CONFIRM_INCIDENT", "REQUEST_ASSISTANCE", "FALSE_POSITIVE", "RESOLVE")
DECISIONS = STEPS + OWN
#: The two that end the matter.
CLOSING = ("FALSE_POSITIVE", "RESOLVE")
#: Open to anyone who may decide at all, whatever the policy says: asking the
#: command centre for help is handing the decision up, not taking it.
ALWAYS = ("REQUEST_ASSISTANCE",)
BASES = ("FOLLOWED", "OVERRIDE", "CLOSING", "INDEPENDENT")

REASONS = {
    "AUTHORISED_ACTIVITY": "Authorised activity",
    "ALREADY_HANDLED": "Already handled",
    "FALSE_DETECTION": "False detection",
    "GUARD_RESPONDING": "Guard already responding",
    "MAINTENANCE": "Maintenance activity",
    "EMERGENCY": "Emergency situation",
    "CAMERA_ISSUE": "Camera issue",
    "OTHER": "Other",
}

#: Roles a policy can speak about: the ones that can hold `intel:decide`.
POLICY_ROLES = {"2": "Admin", "8": "Manager", "3": "Supervisor", "4": "Operator", "5": "Guard"}
#: With no policy set: the command centre decides, a guard does not.
DEFAULT_POLICY = {"2": {"alone": "CRITICAL"}, "8": {"alone": "CRITICAL"}, "3": {"alone": "CRITICAL"},
                  "4": {"alone": "CRITICAL"}, "5": {}}
#: Roles a situation can be escalated to.
ESCALATION_ROLES = (2, 3, 8)

STATUSES = ("AWAITING", "ACKNOWLEDGED", "IN_HAND", "PENDING_APPROVAL", "ASSISTANCE_REQUESTED", "RESOLVED",
            "FALSE_POSITIVE")
#: Said to a guard who is neither on shift at the situation's site nor
#: dispatched to it (services/intel_field.py works out which).
OUT_OF_REACH = ("A guard decides and reports at the site of their own shift, or on a situation they were "
                "dispatched to. This one is neither.")


class Refused(Exception):
    """A decision that is not accepted, with the HTTP status that says how."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ─── The policy ──────────────────────────────────────────────────────────────

def validate_policy(roles: Any) -> None:
    """`roles` is {role id: {"alone": level?, "with_approval": level?}}. A level
    is the highest risk the role may decide at; left out, the role may not."""
    if not isinstance(roles, dict):
        raise ValueError("roles must be an object of {role id: {alone, with_approval}}")
    for role, rule in roles.items():
        if role not in POLICY_ROLES:
            raise ValueError(f"'{role}' is not a role that can decide; use {', '.join(POLICY_ROLES)}")
        if not isinstance(rule, dict) or set(rule) - {"alone", "with_approval"}:
            raise ValueError(f"role {role}: only 'alone' and 'with_approval' can be set")
        for key, level in rule.items():
            if level is not None and level not in LEVELS:
                raise ValueError(f"role {role}: '{key}' must be one of {', '.join(LEVELS)}, or left out")
        alone, approval = rule.get("alone"), rule.get("with_approval")
        if alone and approval and LEVELS.index(approval) <= LEVELS.index(alone):
            raise ValueError(f"role {role}: 'with_approval' must be a higher risk than 'alone', or left out")


def effective(roles: Mapping | None) -> dict:
    """A stored policy over the defaults: a role a policy does not mention
    keeps what it has by default."""
    return {**DEFAULT_POLICY, **{k: dict(v) for k, v in (roles or {}).items()}}


def authority(roles: Mapping | None, role_id: int, risk_level: str | None, action: str) -> tuple[str | None, str]:
    """How this role may take this decision at this risk: ("ALONE" |
    "WITH_APPROVAL" | None, the reason in words). A situation not yet assessed
    is treated as the highest risk: what is not known asks for the most
    authority, not the least."""
    if action in ALWAYS:
        return "ALONE", "Asking the command centre for help is open to anyone who may decide."
    rule = effective(roles).get(str(role_id)) or {}
    level = risk_level if risk_level in LEVELS else LEVELS[-1]
    rank = LEVELS.index(level)
    name = POLICY_ROLES.get(str(role_id), f"Role {role_id}")
    said = level if risk_level in LEVELS else f"{level} (not yet assessed)"
    alone, approval = rule.get("alone"), rule.get("with_approval")
    if alone in LEVELS and rank <= LEVELS.index(alone):
        return "ALONE", f"{name} may decide alone up to {alone}; this is {said}."
    if approval in LEVELS and rank <= LEVELS.index(approval):
        return "WITH_APPROVAL", f"{name} may decide up to {approval} with approval; this is {said}."
    if alone in LEVELS or approval in LEVELS:
        return None, f"{name} may decide up to {approval or alone} under the decision policy; this is {said}."
    return None, f"The decision policy does not let a {name.lower()} decide here."


# ─── What kind of decision it is ─────────────────────────────────────────────

def classify(action: str, current: list[Mapping]) -> tuple[str, Mapping | None]:
    """(basis, the suggestion it relates to). `current` is the set suggested
    for the situation's latest assessment.

    FOLLOWED: a step the layer suggested and said could be taken.
    OVERRIDE: a step it did not suggest, or listed as not possible.
    CLOSING: false positive or resolved — the matter is ended, with a reason.
    INDEPENDENT: neither for nor against a suggestion — acknowledging, asking
    for help, confirming an incident, or deciding before anything was suggested."""
    if action in CLOSING:
        return "CLOSING", None
    if action not in STEPS or not current:
        return "INDEPENDENT", None
    match = next((r for r in current if r["action"] == action), None)
    if match is not None and match["available"]:
        return "FOLLOWED", match
    return "OVERRIDE", match


def first_suggestion(current: list[Mapping]) -> str | None:
    """The step the layer put first among those that could be taken."""
    return next((r["action"] for r in sorted(current, key=lambda r: r["rank"]) if r["available"]), None)


def status_after(action: str, how: str, before: str) -> str:
    """Where the situation stands once this decision is recorded."""
    if how == "WITH_APPROVAL":
        return "PENDING_APPROVAL"
    if action == "RESOLVE":
        return "RESOLVED"
    if action == "FALSE_POSITIVE":
        return "FALSE_POSITIVE"
    if action == "REQUEST_ASSISTANCE":
        return "ASSISTANCE_REQUESTED"
    if action == "ACKNOWLEDGE":
        return "IN_HAND" if before == "IN_HAND" else "ACKNOWLEDGED"   # seen is not a step back from acting
    return "IN_HAND"


# ─── What carrying it out would take ─────────────────────────────────────────

@dataclass(frozen=True)
class Step:
    """One thing the platform would do for a decision, through one existing
    function, needing one existing permission."""

    action: str
    permission: str | None = None
    target_type: str | None = None
    target_id: Any = None
    ids: tuple = ()


def drone_choice(params: Mapping | None) -> dict | None:
    """How a VERIFY_WITH_DRONE decision said the drone should look, from the
    decision's stored `params`: {"event_id", "hold_seconds"} to hold the flight
    that saw a sighting, {"mission_id"} to start a mission the site already
    has, or None when the officer chose neither and will fly it themselves."""
    params = params or {}
    if params.get("drone_event_id"):
        return {"event_id": params["drone_event_id"], "hold_seconds": params.get("hold_seconds")}
    if params.get("drone_mission_id"):
        return {"mission_id": params["drone_mission_id"]}
    return None


def plan(action: str, *, alerts: list[Mapping], incident: Mapping | None,
         drone: Mapping | None = None) -> list[Step]:
    """The steps a decision would be carried out by. Pure: it reads what the
    situation has and says what would be done, in order. An empty plan means
    the decision is a record and nothing more.

    `drone` is the officer's own choice of how a drone should look
    (`drone_choice`). The layer never makes that choice: with none, verifying
    with a drone is a record and the officer flies it from the drone screens."""
    open_ = tuple(str(a["id"]) for a in alerts if a["status"] == "open")
    live = tuple(str(a["id"]) for a in alerts if a["status"] in ("open", "acknowledged"))
    has_incident = incident is not None and incident.get("status") not in ("resolved", "closed")
    incident_id = incident["id"] if incident is not None else None
    steps: list[Step] = []
    if action == "ACKNOWLEDGE" and open_:
        steps.append(Step("ALERT_ACKNOWLEDGE", "alert:acknowledge", "alert", ids=open_))
    elif action == "FALSE_POSITIVE" and live:
        steps.append(Step("ALERT_FALSE_POSITIVE", "alert:acknowledge", "alert", ids=live))
    elif action == "RESOLVE":
        if live:
            steps.append(Step("ALERT_DISMISS", "alert:acknowledge", "alert", ids=live))
        if has_incident:
            steps.append(Step("INCIDENT_RESOLVE", "incident:resolve", "incident", incident_id))
    elif action == "ESCALATE":
        if live:
            steps.append(Step("ALERT_ASSIGN", "alert:acknowledge", "alert", ids=live))
        if has_incident:
            steps.append(Step("INCIDENT_ASSIGN", "incident:assign", "incident", incident_id))
    elif action == "CREATE_INCIDENT":
        steps.append(Step("INCIDENT_CREATE", "incident:create", "incident"))
    elif action == "CONFIRM_INCIDENT":
        steps.append(Step("INCIDENT_CONFIRM", None, "incident", incident_id))
    elif action == "DISPATCH_GUARD":
        if not has_incident:
            steps.append(Step("INCIDENT_CREATE", "incident:create", "incident"))
        steps.append(Step("INCIDENT_DISPATCH", "incident:dispatch", "incident", incident_id if has_incident else None))
    elif action == "VERIFY_WITH_DRONE" and drone:
        # The drone module's own permissions: the ones its own buttons ask for.
        if drone.get("event_id"):
            steps.append(Step("DRONE_HOLD", "drone:operate", "drone_event", drone["event_id"]))
        elif drone.get("mission_id"):
            steps.append(Step("DRONE_LAUNCH", "drone:mission:execute", "drone_mission", drone["mission_id"]))
    return steps


def permissions_needed(steps: list[Step]) -> list[str]:
    return sorted({s.permission for s in steps if s.permission})


# ─── Reading what a decision rests on ────────────────────────────────────────

async def scope(db: AsyncSession, tenant_id) -> None:
    """Scope the session to the tenant again. A commit ends the transaction
    the setting lived in; every statement after one needs this first."""
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def permissions_of(db: AsyncSession, role_id: int) -> set[str]:
    return set((await db.execute(text(
        "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        " WHERE rp.role_id = :r"), {"r": role_id})).scalars().all())


async def policy_for(db: AsyncSession, site_id) -> dict:
    """The policy in force at a site: the site's own if it has one, else the
    tenant's, else the default. {"source", "roles"} — roles already laid over
    the defaults."""
    row = None
    if site_id is not None:
        row = (await db.execute(text(
            "SELECT roles FROM security_decision_policies WHERE site_id = :s"), {"s": site_id})).first()
    source = "site" if row is not None else "tenant"
    if row is None:
        row = (await db.execute(text(
            "SELECT roles FROM security_decision_policies WHERE site_id IS NULL"))).first()
    if row is None:
        return {"source": "default", "roles": effective(None)}
    roles = json.loads(row.roles) if isinstance(row.roles, str) else row.roles
    return {"source": source, "roles": effective(roles)}


@dataclass
class Facts:
    """What a decision on a situation is judged against, read in one place."""

    assessment: dict | None
    current: list[dict]
    alerts: list[dict]
    incident: dict | None


async def facts(db: AsyncSession, situation: Mapping) -> Facts:
    assessment = None
    current: list[dict] = []
    if situation.get("assessment_id") is not None:
        row = (await db.execute(text(
            "SELECT id, sequence, kind, label, risk_level, risk_score, detection_confidence, "
            "       correlation_confidence, risk_confidence FROM security_assessments WHERE id = :a"),
            {"a": situation["assessment_id"]})).mappings().first()
        assessment = dict(row) if row is not None else None
        current = [dict(r) for r in (await db.execute(text(
            "SELECT id, rank, action, available, unavailable_reason, confidence "
            "  FROM security_recommendations WHERE assessment_id = :a ORDER BY rank"),
            {"a": situation["assessment_id"]})).mappings().all()]
    alerts = [dict(r) for r in (await db.execute(text("""
        SELECT DISTINCT a.id, a.status FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
          JOIN alerts a ON a.id = e.alert_id
         WHERE l.situation_id = :s
    """), {"s": situation["id"]})).mappings().all()]
    # The situation's incident: the one a person opened or confirmed here; else
    # one its events carry or that the platform opened for one of its alerts.
    incident = None
    if situation.get("incident_id") is not None:
        row = (await db.execute(text(
            "SELECT id, status, is_auto_created, dispatched_guard_id FROM incidents WHERE id = :i"),
            {"i": situation["incident_id"]})).mappings().first()
        incident = dict(row) if row is not None else None
    if incident is None:
        row = (await db.execute(text("""
            SELECT i.id, i.status, i.is_auto_created, i.dispatched_guard_id FROM incidents i
             WHERE (i.id IN (SELECT e.incident_id FROM security_situation_events l
                               JOIN security_events e ON e.id = l.event_id
                              WHERE l.situation_id = :s AND e.incident_id IS NOT NULL)
                    OR i.alert_id IN (SELECT e.alert_id FROM security_situation_events l
                                        JOIN security_events e ON e.id = l.event_id
                                       WHERE l.situation_id = :s AND e.alert_id IS NOT NULL))
               AND i.status NOT IN ('resolved', 'closed')
             ORDER BY i.created_at DESC LIMIT 1
        """), {"s": situation["id"]})).mappings().first()
        incident = dict(row) if row is not None else None
    return Facts(assessment=assessment, current=current, alerts=alerts, incident=incident)


def incident_state(situation: Mapping, incident: Mapping | None) -> str:
    """NONE: an AI event and nothing more. PRELIMINARY: the platform opened an
    incident by itself and no person has confirmed it. CONFIRMED: a person
    opened it, or confirmed it here. An incident software opened is never
    shown as one a person stands behind."""
    if incident is None:
        return "NONE"
    if situation.get("incident_confirmed_at") is not None or not incident.get("is_auto_created"):
        return "CONFIRMED"
    return "PRELIMINARY"


# ─── May this person take this decision ──────────────────────────────────────

@dataclass
class Check:
    """Whether a decision would be accepted, and everything that answer rests
    on. `refusal` is None when it would be, else (HTTP status, why)."""

    action: str
    basis: str
    recommendation: Mapping | None
    how: str | None
    said: str
    steps: list
    refusal: tuple[int, str] | None


def check(action: str, *, situation: Mapping, f: Facts, mine: set[str], roles: Mapping, role_id: int,
          in_reach: bool = True, drone: Mapping | None = None) -> Check:
    """Judge one decision without recording anything. Pure, so that the answer
    given to "what may I do here?" and the answer given when the button is
    pressed are the same code. `in_reach` is false for a guard who is neither
    on shift at the situation's site nor dispatched to it. `drone` is how the
    officer said a drone should look, when they said."""
    basis, recommendation = classify(action, f.current)
    level = f.assessment["risk_level"] if f.assessment else None
    how, said = authority(roles, role_id, level, action)
    steps = plan(action, alerts=f.alerts, incident=f.incident, drone=drone)
    open_incident = f.incident is not None and f.incident.get("status") not in ("resolved", "closed")
    missing = [p for p in permissions_needed(steps) if p not in mine]
    refusal = None
    if situation.get("closed_at") is not None:
        refusal = (409, "This situation is closed. Nothing more can be decided on it.")
    elif "intel:decide" not in mine:
        refusal = (403, "Deciding needs the permission intel:decide.")
    elif not in_reach:
        refusal = (403, OUT_OF_REACH)
    elif how is None:
        refusal = (403, said)
    elif basis == "OVERRIDE" and "intel:override" not in mine:
        refusal = (403, "This step was not suggested, or was listed as not possible. Choosing it is an override, "
                        "which needs the permission intel:override.")
    elif action == "CREATE_INCIDENT" and open_incident:
        refusal = (409, "An incident is already open for this situation. Confirm it instead.")
    elif action == "CONFIRM_INCIDENT" and not open_incident:
        refusal = (409, "There is no open incident to confirm.")
    elif action == "CONFIRM_INCIDENT" and incident_state(situation, f.incident) == "CONFIRMED":
        refusal = (409, "This incident is already one a person stands behind.")
    elif how == "ALONE" and missing:
        # Proposed for approval, it is the approver who must hold these.
        refusal = (403, f"Carrying this out needs the permission {', '.join(missing)}.")
    return Check(action=action, basis=basis, recommendation=recommendation, how=how, said=said, steps=steps,
                 refusal=refusal)


# ─── Writing the records ─────────────────────────────────────────────────────

async def record_review(db: AsyncSession, situation: Mapping, user_id, role_id: int, via: str) -> bool:
    """That this person looked at what was suggested for the situation's
    latest assessment. Once per person per assessment. True if it was new."""
    if situation.get("assessment_id") is None:
        return False
    row = (await db.execute(text("""
        INSERT INTO security_reviews (tenant_id, situation_id, assessment_id, user_id, actor_role, via)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :a, CAST(:u AS uuid), :r, :via)
        ON CONFLICT (assessment_id, user_id) DO NOTHING RETURNING id
    """), {"s": situation["id"], "a": situation["assessment_id"], "u": str(user_id), "r": role_id,
           "via": via})).first()
    return row is not None


async def insert_decision(db: AsyncSession, *, situation: Mapping, f: Facts, action: str, basis: str,
                          recommendation: Mapping | None, reason_code: str | None, note: str | None,
                          user_id, role_id: int, how: str, policy: Mapping, params: Mapping, via: str,
                          request_id: str | None, client_ref, seen_assessment_id) -> dict:
    a = f.assessment or {}
    row = (await db.execute(text("""
        INSERT INTO security_decisions
               (tenant_id, situation_id, assessment_id, seen_assessment_id, recommendation_id, suggested_action,
                action, basis, reason_code, note, actor_user_id, actor_role, risk_level, risk_score, authority,
                policy, params, via, request_id, client_ref)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :a, :seen, :rec, :suggested, :action, :basis,
                :reason, :note, CAST(:u AS uuid), :role, :level, :score, :how, CAST(:policy AS jsonb),
                CAST(:params AS jsonb), :via, :request_id, :client_ref)
        RETURNING id, decided_at
    """), {"s": situation["id"], "a": a.get("id"), "seen": seen_assessment_id,
           "rec": recommendation["id"] if recommendation else None, "suggested": first_suggestion(f.current),
           "action": action, "basis": basis, "reason": reason_code, "note": (note or "").strip() or None,
           "u": str(user_id), "role": role_id, "level": a.get("risk_level"), "score": a.get("risk_score"),
           "how": how, "policy": json.dumps(policy), "params": json.dumps(params, default=str), "via": via,
           "request_id": request_id, "client_ref": client_ref})).mappings().one()
    return dict(row)


async def set_status(db: AsyncSession, situation_id, status: str, decision_id) -> None:
    """Where the situation now stands. Closing it also settles it, so that
    nothing new is joined to a matter a person has ended."""
    closed = status in ("RESOLVED", "FALSE_POSITIVE")
    await db.execute(text("""
        UPDATE security_situations
           SET decision_status = :status, last_decision_id = :d, last_decided_at = now(),
               closed_at = CASE WHEN :closed THEN now() ELSE NULL END,
               status = CASE WHEN :closed THEN 'SETTLED' ELSE status END,
               settled_at = CASE WHEN :closed THEN COALESCE(settled_at, now()) ELSE settled_at END
         WHERE id = :s
    """), {"status": status, "d": decision_id, "closed": closed, "s": situation_id})


async def link_incident(db: AsyncSession, situation_id, incident_id) -> None:
    """The incident a person opened or confirmed for this situation. Recorded
    on the situation; the incident itself is not touched."""
    await db.execute(text(
        "UPDATE security_situations SET incident_id = :i, "
        "       incident_confirmed_at = COALESCE(incident_confirmed_at, now()) WHERE id = :s"),
        {"i": incident_id, "s": situation_id})


async def insert_action(db: AsyncSession, *, decision_id, situation_id, sequence: int, action: str,
                        through: str | None, target_type: str | None, target_id, result: str,
                        detail: str | None, user_id) -> None:
    await db.execute(text("""
        INSERT INTO security_actions
               (tenant_id, decision_id, situation_id, sequence, action, through, target_type, target_id,
                result, detail, executed_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, :d, :s, :seq, :action, :through, :tt, :tid,
                :result, :detail, CAST(:u AS uuid))
    """), {"d": decision_id, "s": situation_id, "seq": sequence, "action": action, "through": through,
           "tt": target_type, "tid": target_id, "result": result, "detail": (detail or "")[:2000] or None,
           "u": str(user_id)})


# ─── Reading them back ───────────────────────────────────────────────────────

_DECISION = """
    SELECT d.id, d.situation_id, s.situation_number, s.site_id, d.assessment_id, d.seen_assessment_id,
           d.recommendation_id, d.suggested_action, d.action, d.basis, d.reason_code, d.note,
           d.actor_user_id, u.full_name AS actor_name, d.actor_role, d.risk_level, d.risk_score,
           d.authority, d.policy, d.params, d.via, d.decided_at,
           p.verdict AS approval_verdict, p.note AS approval_note, p.approver_user_id,
           pu.full_name AS approver_name, p.approver_role, p.decided_at AS approval_at
      FROM security_decisions d
      JOIN security_situations s ON s.id = d.situation_id
      LEFT JOIN users u ON u.id = d.actor_user_id
      LEFT JOIN security_decision_approvals p ON p.decision_id = d.id
      LEFT JOIN users pu ON pu.id = p.approver_user_id
"""


def _loads(value):
    return json.loads(value) if isinstance(value, str) else value


def view(row: Mapping, actions: list[Mapping]) -> dict:
    """A decision as it is served: the person's choice, the verdict of a second
    person if one was needed, and — separately — what was then carried out."""
    needs = row["authority"] == "WITH_APPROVAL"
    state = ("EFFECTIVE" if not needs else
             {"APPROVED": "APPROVED", "REJECTED": "REJECTED"}.get(row["approval_verdict"] or "", "PENDING_APPROVAL"))
    return {
        "id": row["id"], "situation_id": row["situation_id"], "situation_number": row["situation_number"],
        "decided_at": row["decided_at"], "action": row["action"], "basis": row["basis"],
        "is_override": row["basis"] == "OVERRIDE",
        "reason_code": row["reason_code"], "reason": REASONS.get(row["reason_code"] or ""), "note": row["note"],
        "decided_by": {"user_id": row["actor_user_id"], "name": row["actor_name"], "role_id": row["actor_role"]},
        "via": row["via"],
        "suggested_action": row["suggested_action"], "recommendation_id": row["recommendation_id"],
        "assessment_id": row["assessment_id"],
        "decided_on_an_earlier_assessment": (row["seen_assessment_id"] is not None
                                             and row["seen_assessment_id"] != row["assessment_id"]),
        "risk_level": row["risk_level"], "risk_score": row["risk_score"],
        "authority": row["authority"], "state": state, "policy": _loads(row["policy"]),
        "params": _loads(row["params"]),
        "approval": None if row["approval_verdict"] is None else {
            "verdict": row["approval_verdict"], "note": row["approval_note"], "at": row["approval_at"],
            "by": {"user_id": row["approver_user_id"], "name": row["approver_name"],
                   "role_id": row["approver_role"]}},
        "actions": [{"sequence": a["sequence"], "action": a["action"], "through": a["through"],
                     "target_type": a["target_type"], "target_id": a["target_id"], "result": a["result"],
                     "detail": a["detail"], "executed_at": a["executed_at"],
                     "executed_by_user_id": a["executed_by_user_id"]} for a in actions],
    }


async def _actions(db: AsyncSession, decision_ids: list) -> dict:
    out: dict = {}
    if decision_ids:
        for a in (await db.execute(text(
                "SELECT * FROM security_actions WHERE decision_id = ANY(CAST(:ids AS uuid[])) "
                " ORDER BY decision_id, sequence"), {"ids": decision_ids})).mappings().all():
            out.setdefault(a["decision_id"], []).append(a)
    return out


async def get(db: AsyncSession, decision_id) -> dict | None:
    row = (await db.execute(text(_DECISION + " WHERE d.id = :d"), {"d": decision_id})).mappings().first()
    if row is None:
        return None
    actions = await _actions(db, [row["id"]])
    return {**view(row, actions.get(row["id"], [])), "site_id": row["site_id"]}


async def trail(db: AsyncSession, situation_id) -> list[dict]:
    """Every decision on a situation, oldest first, each with its actions."""
    rows = (await db.execute(text(_DECISION + " WHERE d.situation_id = :s ORDER BY d.decided_at, d.id"),
                             {"s": situation_id})).mappings().all()
    actions = await _actions(db, [r["id"] for r in rows])
    return [view(r, actions.get(r["id"], [])) for r in rows]
