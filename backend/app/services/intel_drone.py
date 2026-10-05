"""Drones and a situation: what a person could ask a drone to do, and what came back.

  officer decides VERIFY_WITH_DRONE ─► the drone module's own function
     ─► the drone looks ─► what it saw is an event in the situation
     ─► the situation is assessed again ─► the officer decides

THIS MODULE ONLY READS. It answers three questions for the officer's screen and
for the checks made when a decision is recorded:

  - which of the situation's drone sightings came from a flight that is still
    in the air, and so could be asked to hold and look again;
  - which missions the site already has that could be started now;
  - what was asked for from this situation, and what came of it.

THE ASKING IS A DECISION. A person chooses VERIFY_WITH_DRONE and says how — hold
this flight, or start that mission. `intel_actions` then calls the drone
module's own function, under that person's own drone permission, and the drone
module makes its own checks: distance, battery, the provider, pre-flight. This
layer does not fly, does not steer, makes no mission and changes none.

WHAT IS SAID HERE IS A FIRST ANSWER, NOT THE LAST. "Can hold" means the flight
is active and not already holding. Whether the drone is still near enough, has
the battery, and can pause at all is the drone module's to say when it is
asked — and when it says no, its reason is kept on the action's record.

THE RUNNER NEVER IMPORTS THIS FOR ANYTHING BUT READING. Nothing here writes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.drone_module import entitlement_problem, load_entitlement

#: The permissions the drone module itself asks for these two things.
HOLD_PERMISSION = "drone:operate"
LAUNCH_PERMISSION = "drone:mission:execute"
#: How long a hold may be: the drone module's own bounds, and its default.
HOLD_MIN, HOLD_MAX, HOLD_DEFAULT = 5, 120, 30
#: A drone in one of these states can be asked to fly — the drone module's own list.
LAUNCHABLE = ("READY", "STANDBY", "CHARGING")
#: A flight in one of these states has its drone committed.
COMMITTED = ("PRECHECK", "READY", "LAUNCHING", "ACTIVE", "PAUSED", "EVENT_DETECTED", "RETURNING")

LATER = "The drone module checks distance, battery and the provider when it is asked."
PREFLIGHT = "Pre-flight runs when it is asked, and can still stop it."


def _obj(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# ─── Pure: could it be asked ─────────────────────────────────────────────────

def hold_state(sighting: Mapping) -> tuple[bool, str | None]:
    """(could this sighting's flight be asked to hold, why not). The first
    answer only: the drone module decides when it is asked."""
    if sighting.get("session_id") is None:
        return False, "This sighting did not come from a flight."
    status = sighting.get("flight_status")
    if status is None:
        return False, "The flight no longer exists."
    if sighting.get("holding"):
        return False, "This flight is already holding for a look."
    if status != "ACTIVE":
        return False, f"The flight is {str(status).lower()}; only an active flight can hold to look again."
    return True, None


def launch_state(mission: Mapping) -> tuple[bool, str | None]:
    """(could this mission be started now, why not). The first answer only:
    pre-flight decides when it is asked."""
    if mission.get("drone_id") is None:
        return False, "The mission has no drone."
    if mission.get("drone_committed"):
        return False, "Its drone is already committed to another flight."
    status = mission.get("drone_status")
    if status not in LAUNCHABLE:
        return False, f"Its drone is {str(status or 'not known').lower().replace('_', ' ')}."
    if mission.get("communication_status") != "OK":
        return False, "Its drone is not in contact."
    return True, None


def hold_seconds(value: Any) -> int:
    """The hold a decision asks for, or ValueError saying what is allowed."""
    if value is None:
        return HOLD_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int) or not HOLD_MIN <= value <= HOLD_MAX:
        raise ValueError(f"hold_seconds is a whole number from {HOLD_MIN} to {HOLD_MAX}.")
    return value


def blocked_reason(preflight: Mapping | None) -> str:
    """Why pre-flight stopped a flight, in the drone module's own words."""
    checks = (preflight or {}).get("checks") or []
    failed = [str(c.get("detail") or c.get("label") or c.get("code"))
              for c in checks if not c.get("passed") and str(c.get("severity")).upper() == "BLOCK"]
    if not failed:
        failed = [str(code) for code in (preflight or {}).get("blocking") or []]
    return "; ".join(failed) or "no reason was given"


# ─── Reading ─────────────────────────────────────────────────────────────────

async def licence_problem(db: AsyncSession, now: datetime | None = None) -> str | None:
    """Why this organisation cannot start a flight, or None when it can: the
    drone module's own licence check, asked and not copied."""
    return entitlement_problem(await load_entitlement(db), now or datetime.now(timezone.utc))


async def sightings(db: AsyncSession, situation_id) -> list[dict]:
    """The situation's drone sightings, newest first, each with the state of
    the flight that saw it."""
    rows = (await db.execute(text("""
        SELECT e.id AS event_id, e.title, e.occurred_at, e.location_label,
               de.id AS drone_event_id, de.session_id, de.drone_id, de.risk_level AS drone_risk_level,
               de.detection_count, d.name AS drone_name,
               ps.session_number, ps.mission_name, ps.status AS flight_status,
               EXISTS (SELECT 1 FROM drone_verification_requests v
                        WHERE v.session_id = de.session_id AND v.status IN ('REQUESTED', 'HOLDING')) AS holding
          FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id AND e.source_table = 'drone_events'
          JOIN drone_events de ON de.id = e.source_id
          LEFT JOIN drone_patrol_sessions ps ON ps.id = de.session_id
          LEFT JOIN drones d ON d.id = de.drone_id
         WHERE l.situation_id = :s
         ORDER BY e.occurred_at DESC, e.id
    """), {"s": situation_id})).mappings().all()
    out = []
    for r in rows:
        can, why = hold_state(r)
        out.append({**dict(r), "can_hold": can, "why_not": why})
    return out


async def sighting_in(db: AsyncSession, situation_id, drone_event_id) -> bool:
    """Whether this drone sighting is one of the situation's events."""
    return (await db.execute(text("""
        SELECT 1 FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
         WHERE l.situation_id = :s AND e.source_table = 'drone_events' AND e.source_id = CAST(:d AS uuid)
         LIMIT 1
    """), {"s": situation_id, "d": str(drone_event_id)})).first() is not None


_MISSIONS = f"""
    SELECT m.id AS mission_id, m.name, m.site_id, m.drone_id, d.name AS drone_name, d.status AS drone_status,
           d.communication_status, d.battery_level, r.name AS route_name,
           EXISTS (SELECT 1 FROM drone_patrol_sessions ps
                    WHERE ps.drone_id = m.drone_id
                      AND ps.status IN ({", ".join(repr(s) for s in COMMITTED)})) AS drone_committed
      FROM drone_missions m
      LEFT JOIN drones d ON d.id = m.drone_id
      LEFT JOIN drone_routes r ON r.id = m.route_id
"""


async def missions(db: AsyncSession, site_id) -> list[dict]:
    """The missions this site already has switched on, those that could start
    now first."""
    if site_id is None:
        return []
    rows = (await db.execute(text(
        _MISSIONS + " WHERE m.site_id = :s AND m.enabled ORDER BY m.name LIMIT 50"), {"s": site_id})).mappings().all()
    out = []
    for r in rows:
        can, why = launch_state(r)
        out.append({**dict(r), "can_launch": can, "why_not": why})
    out.sort(key=lambda m: (not m["can_launch"], m["name"]))
    return out


async def mission_at(db: AsyncSession, mission_id, site_id) -> dict | None:
    """A mission that is switched on at this site, or None."""
    if site_id is None:
        return None
    row = (await db.execute(text(
        _MISSIONS + " WHERE m.id = CAST(:m AS uuid) AND m.site_id = :s AND m.enabled"),
        {"m": str(mission_id), "s": site_id})).mappings().first()
    return dict(row) if row is not None else None


async def asked(db: AsyncSession, situation_id) -> list[dict]:
    """What was asked of a drone by a decision on this situation, oldest first:
    the step, how it ended, and what has come of it since."""
    rows = (await db.execute(text("""
        SELECT a.id AS action_id, a.decision_id, a.action, a.result, a.detail, a.target_type, a.target_id,
               a.executed_at AS asked_at,
               v.id AS look_id, v.event_id AS look_event_id, v.status AS look_status, v.hold_seconds,
               v.started_at AS look_started_at, v.ends_at AS look_ends_at, v.completed_at AS look_completed_at,
               v.result AS look_result,
               ps.id AS flight_id, ps.session_number, ps.mission_name, ps.status AS flight_status,
               ps.started_at AS flight_started_at, ps.ended_at AS flight_ended_at,
               ps.event_count AS flight_event_count, ps.blocked_reason
          FROM security_actions a
          LEFT JOIN drone_verification_requests v ON a.target_type = 'drone_look' AND v.id = a.target_id
          LEFT JOIN drone_patrol_sessions ps ON a.target_type = 'drone_flight' AND ps.id = a.target_id
         WHERE a.situation_id = :s AND a.action IN ('DRONE_HOLD', 'DRONE_LAUNCH')
         ORDER BY a.executed_at, a.sequence
    """), {"s": situation_id})).mappings().all()
    out = []
    for r in rows:
        look = flight = None
        if r["look_id"] is not None:
            look = {"id": r["look_id"], "drone_event_id": r["look_event_id"], "status": r["look_status"],
                    "hold_seconds": r["hold_seconds"], "started_at": r["look_started_at"],
                    "ends_at": r["look_ends_at"], "completed_at": r["look_completed_at"],
                    "result": _obj(r["look_result"])}
        if r["flight_id"] is not None:
            flight = {"id": r["flight_id"], "session_number": r["session_number"], "mission_name": r["mission_name"],
                      "status": r["flight_status"], "started_at": r["flight_started_at"],
                      "ended_at": r["flight_ended_at"], "event_count": r["flight_event_count"],
                      "blocked_reason": r["blocked_reason"]}
        out.append({"action_id": r["action_id"], "decision_id": r["decision_id"], "action": r["action"],
                    "result": r["result"], "detail": r["detail"], "asked_at": r["asked_at"],
                    "look": look, "flight": flight})
    return out


async def other_looks(db: AsyncSession, situation_id) -> list[dict]:
    """Looks at this situation's sightings that were asked for from the drone
    screens, not by a decision here. They are part of the same picture."""
    rows = (await db.execute(text("""
        SELECT v.id, v.event_id AS drone_event_id, v.status, v.hold_seconds, v.created_at AS asked_at,
               v.started_at, v.ends_at, v.completed_at, v.result
          FROM drone_verification_requests v
         WHERE v.event_id IN (SELECT e.source_id FROM security_situation_events l
                                JOIN security_events e ON e.id = l.event_id
                               WHERE l.situation_id = :s AND e.source_table = 'drone_events')
           AND NOT EXISTS (SELECT 1 FROM security_actions a
                            WHERE a.target_type = 'drone_look' AND a.target_id = v.id)
         ORDER BY v.created_at
    """), {"s": situation_id})).mappings().all()
    return [{**dict(r), "result": _obj(r["result"])} for r in rows]


async def picture(db: AsyncSession, situation: Mapping, mine: set[str]) -> dict:
    """Everything the officer's screen shows about drones and this situation.
    `mine` is the caller's permissions: missions are listed only for someone
    who may see the drone module at all."""
    problem = await licence_problem(db)
    sees = "drone:read" in mine
    return {
        "situation_id": situation["id"],
        "closed": situation.get("closed_at") is not None,
        "licence": {"ok": problem is None, "problem": problem},
        "may": {"hold": HOLD_PERMISSION in mine, "launch": LAUNCH_PERMISSION in mine and problem is None,
                "see_missions": sees},
        "hold_seconds": {"min": HOLD_MIN, "max": HOLD_MAX, "default": HOLD_DEFAULT},
        "notes": {"hold": LATER, "launch": PREFLIGHT},
        "sightings": await sightings(db, situation["id"]),
        "missions": await missions(db, situation.get("site_id")) if sees else [],
        "asked": await asked(db, situation["id"]),
        "other_looks": await other_looks(db, situation["id"]),
    }
