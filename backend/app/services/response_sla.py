"""The response clocks: whether an incident was acknowledged, reached and resolved in time.

The platform has stored three times per severity since migration 0009 —
acknowledge within, arrive within, resolve within — and a column on every
incident, `sla_breached`. Until this phase nothing compared the one with the
other, and the column was never set.

  ACKNOWLEDGE   from when the incident was opened until somebody did something
                with it: changed its status, or sent a guard
  ARRIVAL       from the dispatch until the guard arrived — the deadline the
                existing dispatch has always stamped (`sla_deadline_at`)
  RESOLVE       from when it was opened until it was resolved

OFF UNTIL SWITCHED ON, AND NEVER RETROACTIVE. An organisation switches the
clocks on (`response.sla_enabled`); only incidents opened after that moment are
judged. Switching it on does not reach back through years of incidents nobody
was measuring and mark them all late.

A BREACH IS RECORDED ONCE AND TOLD TO SOMEBODY. The first time a clock is found
to have run out: `incidents.sla_breached` is set, the breach is recorded in
`incident_escalations` and in the existing `escalation_events`, and the person
the severity's settings name is told.

AN ESCALATION POLICY ADDS FURTHER STEPS: "still not acknowledged after ten
minutes — tell every supervisor". Each step is recorded once per incident.

WHO IS TOLD CAN SEE THE INCIDENT. A step addressed to a role goes to the active
people in that role who may see the incident's site; one addressed to a person
goes to that person if they may. How many people that was is part of the record,
so that a step addressed to nobody shows as that.

NOTHING HERE ACTS ON AN INCIDENT. It is not reassigned, re-dispatched, closed
or raised in severity. A person is told; a person decides.

`clocks` and `due_steps` are pure. `evaluate_tenant` writes, on a session scoped
to one tenant, and returns what to tell; `run` does the telling after the commit.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ENABLED_KEY = "response.sla_enabled"
CLOCKS = ("ACKNOWLEDGE", "ARRIVAL", "RESOLVE")
#: What a breach is recorded as in the existing `escalation_events.escalation_type`.
BREACH_TYPE = {"ACKNOWLEDGE": "sla_acknowledge", "ARRIVAL": "sla_arrival", "RESOLVE": "sla_resolve"}
STEP_TYPE = "policy_step"
TRIGGERS = ("NOT_ACKNOWLEDGED", "NOT_ARRIVED", "NOT_RESOLVED")
#: Which clock an escalation policy's trigger watches.
TRIGGER_CLOCK = {"NOT_ACKNOWLEDGED": "ACKNOWLEDGE", "NOT_ARRIVED": "ARRIVAL", "NOT_RESOLVED": "RESOLVE"}
#: The roles a step may be addressed to: never the platform owner, never a client.
NOTIFY_ROLES = (2, 3, 4, 5, 6, 8)
BREACH_EVENT = "incident_sla_breached"
ESCALATION_EVENT = "incident_escalated"
#: The most incidents one pass judges for one organisation.
MAX_PER_PASS = 2000
CLOCK_WORDS = {"ACKNOWLEDGE": "was not acknowledged in time", "ARRIVAL": "was not reached in time",
               "RESOLVE": "was not resolved in time"}
TRIGGER_WORDS = {"NOT_ACKNOWLEDGED": "still not acknowledged", "NOT_ARRIVED": "still not reached",
                 "NOT_RESOLVED": "still not resolved"}
NOTE = ("The clocks tell people. They do not reassign, re-dispatch, close or raise the severity of an incident: "
        "a person does that.")


def _clock(started: datetime | None, due: datetime | None, met: datetime | None, now: datetime) -> dict:
    """One clock: when it started, when it is due, when it was met — and
    whether it ran out first."""
    if started is None or due is None:
        return {"started_at": started, "due_at": None, "met_at": met, "breached": False, "running": False,
                "seconds_left": None}
    breached = (met is None and now > due) or (met is not None and met > due)
    return {"started_at": started, "due_at": due, "met_at": met, "breached": breached, "running": met is None,
            "seconds_left": int((due - now).total_seconds()) if met is None else None}


def clocks(incident: Mapping, config: Mapping | None, now: datetime) -> dict[str, dict]:
    """The three clocks of one incident under its severity's settings. With no
    settings for that severity there is nothing to be late for."""
    opened = incident["created_at"]
    if config is None:
        return {name: _clock(None, None, None, now) for name in CLOCKS}
    sent = incident.get("dispatched_at")
    return {
        "ACKNOWLEDGE": _clock(opened, opened + timedelta(seconds=config["ack_within_seconds"]),
                              incident.get("acknowledged_at"), now),
        # Only once somebody has been sent, and to the deadline the dispatch stamped.
        "ARRIVAL": _clock(sent, incident.get("sla_deadline_at") if sent else None,
                          incident.get("guard_arrived_at") if sent else None, now),
        "RESOLVE": _clock(opened, opened + timedelta(seconds=config["resolve_within_seconds"]),
                          incident.get("resolved_at"), now),
    }


def step_due_at(incident: Mapping, policy: Mapping) -> datetime | None:
    """When a policy's step falls due for an incident — or None when the thing
    it watches has already happened, or (for an arrival) nobody has been sent."""
    trigger = policy["trigger"]
    started = incident.get("dispatched_at") if trigger == "NOT_ARRIVED" else incident["created_at"]
    met = {"NOT_ACKNOWLEDGED": incident.get("acknowledged_at"), "NOT_ARRIVED": incident.get("guard_arrived_at"),
           "NOT_RESOLVED": incident.get("resolved_at")}[trigger]
    if started is None or met is not None:
        return None
    return started + timedelta(seconds=policy["after_seconds"])


def applies(policy: Mapping, incident: Mapping) -> bool:
    """A policy is for one site or for all, and for one severity or for all."""
    if policy["site_id"] is not None and str(policy["site_id"]) != str(incident.get("site_id")):
        return False
    return policy["severity"] is None or policy["severity"] == incident["severity"]


def due_steps(incident: Mapping, policies: Sequence[Mapping], now: datetime) -> list[tuple[Mapping, datetime]]:
    """The policy steps that have fallen due for this incident, each with when."""
    out = []
    for policy in policies:
        if not applies(policy, incident):
            continue
        due = step_due_at(incident, policy)
        if due is not None and now >= due:
            out.append((policy, due))
    return out


#: `acknowledged_at`: the first thing anybody did with it. LEAST passes over NULLs.
INCIDENTS = """
    SELECT i.id, i.title, i.description, i.severity, i.status, i.created_at, i.resolved_at,
           i.dispatched_guard_id, i.dispatched_at, i.dispatch_notes, i.guard_arrived_at, i.sla_deadline_at,
           i.sla_breached, i.escalated_at, i.camera_id, c.name AS camera_name, c.location AS camera_location,
           c.site_id, s.name AS site_name, g.full_name AS dispatched_guard_name,
           COALESCE(p.latitude, c.latitude) AS latitude, COALESCE(p.longitude, c.longitude) AS longitude,
           LEAST(i.dispatched_at,
                 (SELECT min(h.changed_at) FROM incident_status_history h WHERE h.incident_id = i.id),
                 (SELECT min(r.dispatched_at) FROM incident_responses r WHERE r.incident_id = i.id),
                 i.resolved_at) AS acknowledged_at
      FROM incidents i
      LEFT JOIN cameras c ON c.id = i.camera_id
      LEFT JOIN sites s ON s.id = c.site_id
      LEFT JOIN users g ON g.id = i.dispatched_guard_id
      LEFT JOIN LATERAL (SELECT latitude, longitude FROM incident_status_history
                          WHERE incident_id = i.id AND latitude IS NOT NULL
                          ORDER BY changed_at DESC LIMIT 1) p ON TRUE
"""


async def enabled_since(db: AsyncSession) -> datetime | None:
    """When the calling organisation switched the clocks on — or None when they are off."""
    row = (await db.execute(text(
        "SELECT setting_value, updated_at FROM tenant_settings WHERE setting_key = :k"),
        {"k": ENABLED_KEY})).first()
    if row is None:
        return None
    value = row.setting_value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return row.updated_at if value is True else None


async def configs(db: AsyncSession) -> dict[str, dict]:
    rows = await db.execute(text("""
        SELECT sc.severity, sc.ack_within_seconds, sc.dispatch_within_seconds, sc.resolve_within_seconds,
               sc.escalation_user_id, u.full_name AS escalation_user_name
          FROM sla_configs sc LEFT JOIN users u ON u.id = sc.escalation_user_id
    """))
    return {r["severity"]: dict(r) for r in rows.mappings()}


async def policies(db: AsyncSession, *, with_retired: bool = False) -> list[dict]:
    rows = await db.execute(text(f"""
        SELECT p.id, p.name, p.site_id, s.name AS site_name, p.severity, p.trigger, p.after_seconds,
               p.notify_role_id, p.notify_user_id, u.full_name AS notify_user_name, p.is_active,
               p.created_at, p.updated_at
          FROM escalation_policies p
          LEFT JOIN sites s ON s.id = p.site_id
          LEFT JOIN users u ON u.id = p.notify_user_id
         {'' if with_retired else 'WHERE p.is_active'}
         ORDER BY p.is_active DESC, p.trigger, p.after_seconds, p.created_at
    """))
    return [dict(r) for r in rows.mappings()]


async def people(db: AsyncSession, site_id: Any, *, role_id: int | None = None, user_id: Any = None) -> list[str]:
    """The active people a step is addressed to who may see an incident at this
    site: a person with no sites assigned sees every site, one with sites
    assigned sees those. Administrators see everything."""
    if role_id is None and user_id is None:
        return []
    rows = await db.execute(text("""
        SELECT u.id FROM users u
         WHERE u.is_active AND u.role_id = ANY(:roles)
           AND (CAST(:role AS int) IS NULL OR u.role_id = :role)
           AND (CAST(:who AS uuid) IS NULL OR u.id = CAST(:who AS uuid))
           AND (u.role_id = 2
                OR NOT EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id)
                OR EXISTS (SELECT 1 FROM user_sites us
                            WHERE us.user_id = u.id AND us.site_id = CAST(:site AS uuid)))
    """), {"roles": list(NOTIFY_ROLES), "role": role_id if user_id is None else None,
           "who": str(user_id) if user_id else None, "site": str(site_id) if site_id else None})
    return [str(r[0]) for r in rows]


async def _record(db: AsyncSession, incident: Mapping, *, kind: str, clock: str, due_at: datetime,
                  policy: Mapping | None, role_id: int | None, user_id: Any, recipients: int) -> Any:
    """Record one breach or one step — once. Returns the id of its row in the
    existing escalation log, or None when it was already recorded."""
    sending = incident.get("dispatched_at") if clock == "ARRIVAL" else None
    # One statement: the record, and — only if the record was made — its row in
    # the platform's own escalation log, which nothing has written since 0009.
    # Whether the telling went out is marked on that row afterwards; the record
    # itself is never changed.
    event_id = (await db.execute(text("""
        WITH made AS (
            INSERT INTO incident_escalations
                   (tenant_id, incident_id, site_id, kind, clock, policy_id, policy_name, sending_at, due_at,
                    notify_role_id, notify_user_id, recipients, escalation_event_id)
            VALUES (current_setting('app.current_tenant')::uuid, :i, :site, :kind, :clock, :policy, :policy_name,
                    :sending, :due, :role, :who, :n, :event)
            ON CONFLICT DO NOTHING
            RETURNING id
        ), logged AS (
            INSERT INTO escalation_events (id, tenant_id, incident_id, escalation_type, escalated_to_user_id)
            SELECT :event, current_setting('app.current_tenant')::uuid, :i, :type, :who FROM made
            RETURNING id
        )
        SELECT id FROM logged
    """), {"i": incident["id"], "site": incident.get("site_id"), "kind": kind, "clock": clock,
           "policy": policy["id"] if policy else None, "policy_name": policy["name"] if policy else None,
           "sending": sending, "due": due_at, "role": role_id, "who": user_id, "n": recipients,
           "event": uuid.uuid4(), "type": BREACH_TYPE[clock] if kind == "SLA_BREACH" else STEP_TYPE})).scalar()
    if event_id is None:
        return None
    await db.execute(text("""
        UPDATE incidents
           SET escalated_at = COALESCE(escalated_at, now()),
               escalated_to_user_id = COALESCE(escalated_to_user_id, :who)
         WHERE id = :i
    """), {"who": user_id, "i": incident["id"]})
    return event_id


async def evaluate_tenant(db: AsyncSession, now: datetime | None = None) -> list[dict]:
    """Judge this organisation's incidents and record what has newly run out.
    Returns what to tell people — the caller sends it after committing. The
    session must be scoped to the tenant."""
    now = now or datetime.now(timezone.utc)
    since = await enabled_since(db)
    if since is None:
        return []
    by_severity = await configs(db)
    steps = await policies(db)
    if not by_severity and not steps:
        return []
    # Still open, or closed in the last day: a clock that ran out and was then
    # met late is a breach all the same, and is recorded once.
    rows = (await db.execute(text(f"""{INCIDENTS}
         WHERE i.created_at >= :since
           AND (i.status NOT IN ('resolved','closed') OR i.resolved_at >= :recent)
         ORDER BY i.created_at LIMIT {MAX_PER_PASS}
    """), {"since": since, "recent": now - timedelta(hours=24)})).mappings().all()
    if not rows:
        return []
    recorded: set[tuple] = set()
    for r in await db.execute(text("""
        SELECT incident_id, kind, clock, policy_id, sending_at FROM incident_escalations
         WHERE incident_id = ANY(:ids)
    """), {"ids": [r["id"] for r in rows]}):
        recorded.add((r.incident_id, r.kind, r.clock, r.policy_id, r.sending_at))

    tell: list[dict] = []
    for incident in rows:
        config = by_severity.get(incident["severity"])
        state = clocks(incident, config, now)
        subject = {"incident_id": str(incident["id"]), "title": incident["title"], "severity": incident["severity"],
                   "site_id": str(incident["site_id"]) if incident["site_id"] else None,
                   "site_name": incident["site_name"]}

        for name in CLOCKS:
            sending = incident["dispatched_at"] if name == "ARRIVAL" else None
            if not state[name]["breached"] or (incident["id"], "SLA_BREACH", name, None, sending) in recorded:
                continue
            to_user = config["escalation_user_id"] if config else None
            to = await people(db, incident["site_id"], user_id=to_user)
            event_id = await _record(db, incident, kind="SLA_BREACH", clock=name, due_at=state[name]["due_at"],
                                     policy=None, role_id=None, user_id=to_user, recipients=len(to))
            if event_id is None:
                continue
            await db.execute(text("UPDATE incidents SET sla_breached = TRUE WHERE id = :i"), {"i": incident["id"]})
            tell.append({"event_type": BREACH_EVENT, **subject, "clock": name, "kind": "SLA_BREACH",
                         "message": f"“{incident['title']}” {CLOCK_WORDS[name]}.",
                         "due_at": state[name]["due_at"].isoformat(), "notify_user_ids": to,
                         "escalation_event_id": str(event_id)})

        if incident["status"] in ("resolved", "closed"):
            continue
        for policy, due in due_steps(incident, steps, now):
            clock = TRIGGER_CLOCK[policy["trigger"]]
            sending = incident["dispatched_at"] if clock == "ARRIVAL" else None
            if (incident["id"], "POLICY_STEP", clock, policy["id"], sending) in recorded:
                continue
            to = await people(db, incident["site_id"], role_id=policy["notify_role_id"],
                              user_id=policy["notify_user_id"])
            event_id = await _record(db, incident, kind="POLICY_STEP", clock=clock, due_at=due, policy=policy,
                                     role_id=policy["notify_role_id"], user_id=policy["notify_user_id"],
                                     recipients=len(to))
            if event_id is None:
                continue
            minutes = max(1, round(policy["after_seconds"] / 60))
            tell.append({"event_type": ESCALATION_EVENT, **subject, "clock": clock, "kind": "POLICY_STEP",
                         "policy": policy["name"], "trigger": policy["trigger"],
                         "message": f"“{incident['title']}” is {TRIGGER_WORDS[policy['trigger']]} after "
                                    f"{minutes} min ({policy['name']}).",
                         "due_at": due.isoformat(), "notify_user_ids": to, "escalation_event_id": str(event_id)})
    return tell


async def run(session_factory, redis=None, now: datetime | None = None) -> dict:
    """One pass over every organisation. For each: note the sendings that have
    no record yet and tell those guards; then, where the clocks are switched on,
    judge them. Each organisation is a transaction of its own, and what was
    found is told after it commits, so that a telling that fails cannot undo
    the record of the breach."""
    from app.services import incident_response, response_notify

    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    counts = {"sendings": 0, "escalations": 0}
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                sendings = await incident_response.open_sendings(db)
                tell = await evaluate_tenant(db, now)
                await db.commit()
        except Exception:
            logger.exception("response clocks: could not judge tenant %s", tenant_id)
            continue
        counts["sendings"] += len(sendings)
        counts["escalations"] += len(tell)
        for sending in sendings:
            await response_notify.sent(redis, tenant_id, sending, now=now)
        for item in tell:
            reached = await response_notify.escalation(redis, session_factory, tenant_id, item, now=now)
            if not reached:
                continue
            try:
                async with session_factory() as db:
                    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                    await db.execute(text("UPDATE escalation_events SET notification_sent = TRUE WHERE id = :e"),
                                     {"e": item["escalation_event_id"]})
                    await db.commit()
            except Exception:
                logger.exception("response clocks: could not mark %s as told", item["escalation_event_id"])
    return counts
