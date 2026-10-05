"""Recommendations: what the layer suggests an officer do next.

  an assessment (kind, risk, factors, three confidences)
      + what can actually be done right now
      ─►  suggestions in order, each with its reason  ─►  written once

A SUGGESTION, NEVER AN ACT. Nothing here sends a guard, opens an incident,
escalates, launches a drone or tells anybody anything. It writes rows that say
"the layer suggests this, because of that". A person decides what happens, and
that decision is a different record made by different code (phase 7). This
module imports nothing that could act and writes only its own table.

RULES, NOT A MODEL. Which step is suggested for which kind of situation at which
risk is a table in this file. The same assessment and the same availability
always give the same suggestions.

ONLY WHAT IS POSSIBLE IS OFFERED AS POSSIBLE. A guard cannot be sent from a site
with nobody on shift; a drone that is not ready cannot look. Such a step is
still listed — with `available` false and the reason in words — so that an
officer is not offered what cannot work, nor left wondering why the obvious
step is missing. Availability is what the platform had recorded when the
suggestion was made.

THE FOURTH CONFIDENCE. How sure the layer is of a suggestion. A step that only
looks (watch, view a camera, verify, investigate) is as sure as its rule: looking
is never the wrong thing to do because the picture is unclear. A step that sends
someone or raises something is no surer than the weakest thing it rests on —
the detection, the correlation or the risk confidence — and says which of them
held it down. So when the picture is incomplete, looking comes first by
arithmetic rather than by a special case.

The exception is a guard's SOS: a person asking for help is not a detection, and
sending help is not held back because the site's hours were never filled in.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ENGINE_VERSION = "rules-2"
ACTIONS = ("MONITOR", "VERIFY", "VIEW_CAMERA", "VERIFY_WITH_DRONE", "DISPATCH_GUARD", "ESCALATE", "INVESTIGATE",
           "CONTACT_SITE", "CREATE_INCIDENT")
#: Steps that only look. The rest send someone or raise something.
LOOKING = frozenset({"MONITOR", "VERIFY", "VIEW_CAMERA", "VERIFY_WITH_DRONE", "INVESTIGATE"})
PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")
PRIORITY_OF = {"INFO": "LOW", "LOW": "LOW", "MEDIUM": "MEDIUM", "HIGH": "HIGH", "CRITICAL": "URGENT"}
#: A drone in one of these states can be asked to fly — the drone module's own list.
DRONE_LAUNCHABLE = ("READY", "STANDBY", "CHARGING")
BATCH = int(os.environ.get("INTEL_RECOMMEND_BATCH", "50"))


@dataclass(frozen=True)
class Availability:
    """What could be done about a situation right now, as the platform records
    it. Counts and states only: no guard's name, no contact's number."""

    has_site: bool = False
    #: Cameras that reported, then their neighbours, then the camera of a drone
    #: that saw this and is still in the air: {id, name, state, relation}.
    #: state is online, degraded, offline, disabled or not_known; relation is
    #: reported, neighbour or drone.
    cameras: tuple[dict, ...] = ()
    guards_on_shift: int = 0
    guard_dispatched: bool = False
    drones_at_site: int = 0
    drones_ready: int = 0
    drone_in_flight: bool = False
    #: A drone has already held and looked again at this, because a person asked.
    drone_looked: bool = False
    site_contact: bool = False
    #: An incident already open for one of the situation's events: {id, status}.
    incident: dict | None = None


@dataclass
class Recommendation:
    action: str
    priority: str
    reason: str
    confidence: float
    limited_by: str
    available: bool = True
    unavailable_reason: str | None = None
    supporting: dict = field(default_factory=dict)
    rank: int = 0


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _confidence(base: float, assessment: Mapping, bounded: bool) -> tuple[float, str]:
    """(confidence, what held it down). The rule's own certainty, and for a
    step that acts, no more than the weakest confidence it rests on."""
    candidates = [(float(base), "RULE")]
    if bounded:
        for key, name in (("detection_confidence", "DETECTION"), ("correlation_confidence", "CORRELATION"),
                          ("risk_confidence", "RISK")):
            if assessment.get(key) is not None:
                candidates.append((float(assessment[key]), name))
    value, name = min(candidates, key=lambda c: c[0])
    return round(value, 2), name


def _priority(action: str, level: str) -> str:
    if action == "MONITOR":
        return "LOW"
    if action == "INVESTIGATE":
        return "LOW" if level in ("INFO", "LOW") else "MEDIUM"
    return PRIORITY_OF.get(level, "LOW")


def recommend(assessment: Mapping, avail: Availability) -> list[Recommendation]:
    """The suggestions for one assessment, surest first, those that cannot be
    done now after those that can. Pure: no database, no clock."""
    kind, level = assessment["kind"], assessment["risk_level"]
    factors = _json(assessment.get("risk_factors")) or []
    points: dict[str, int] = {}
    for f in factors:
        points[f["factor"]] = points.get(f["factor"], 0) + f["points"]
    corroborated = points.get("CORROBORATION", 0) > 0
    serious = level in ("HIGH", "CRITICAL")
    watchable = [c for c in avail.cameras if c.get("state") not in ("offline", "disabled")]
    rests_on = [f["detail"] for f in sorted(factors, key=lambda f: -f["points"])[:3] if f["points"] > 0]
    out: list[Recommendation] = []

    def why_not(action: str) -> str | None:
        """Why a step cannot be done right now, or None when it can."""
        if action == "VIEW_CAMERA" and not watchable:
            return "The camera is not sending." if len(avail.cameras) == 1 else "None of the cameras is sending."
        if action == "VERIFY_WITH_DRONE" and not (avail.drone_in_flight or avail.drones_ready):
            return "No drone at this site is ready to fly."
        if action == "DISPATCH_GUARD":
            if not avail.has_site:
                return "The situation has no site, so there is no shift to send a guard from."
            if avail.guard_dispatched:
                return "A guard has already been dispatched to this."
            if kind == "GUARD_EMERGENCY" and avail.guards_on_shift < 2:
                return "No other guard is on shift at this site."
            if avail.guards_on_shift < 1:
                return "No guard is on shift at this site."
        if action == "CONTACT_SITE":
            if not avail.has_site:
                return "The situation has no site."
            if not avail.site_contact:
                return "No contact is recorded for this site."
        if action == "CREATE_INCIDENT" and avail.incident is not None:
            return "An incident is already open for this."
        return None

    def facts_for(action: str) -> dict:
        extra: dict = {}
        if action == "VIEW_CAMERA":
            extra["cameras"] = [dict(c) for c in avail.cameras]
        elif action == "DISPATCH_GUARD" and avail.has_site:
            extra["facts"] = [f"{avail.guards_on_shift} guard(s) on shift at the site."]
        elif action == "VERIFY_WITH_DRONE":
            extra["facts"] = (["A drone is in the air at the site."] if avail.drone_in_flight else
                              [f"{avail.drones_ready} drone(s) at the site ready to fly."])
        elif action == "CREATE_INCIDENT" and avail.incident is not None:
            extra["incident"] = {"id": str(avail.incident["id"]), "status": avail.incident.get("status")}
        return extra

    def offer(action: str, base: float, reason: str, *, bounded: bool | None = None) -> None:
        if any(r.action == action for r in out):
            return  # the first reason given for a step stands
        bounded = (action not in LOOKING) if bounded is None else bounded
        confidence, limited_by = _confidence(base, assessment, bounded)
        blocked = why_not(action)
        out.append(Recommendation(
            action=action, priority=_priority(action, level), reason=reason, confidence=confidence,
            limited_by=limited_by, available=blocked is None, unavailable_reason=blocked,
            supporting={"risk": {"level": level, "score": assessment.get("risk_score")}, "rests_on": rests_on,
                        **facts_for(action)}))

    def look(base: float, reason: str) -> None:
        """Confirm by camera where there is one; otherwise say to confirm another way."""
        if avail.cameras:
            offer("VIEW_CAMERA", base, reason)
        else:
            offer("VERIFY", round(base - 0.15, 2),
                  "No camera shows this place: confirm another way — call the site, or ask a guard.")

    def drone(base: float) -> None:
        # Once a drone has looked, looking again is not suggested: what it saw
        # is among the events, and the question is now what to do about it.
        if avail.drones_at_site and not avail.drone_looked:
            offer("VERIFY_WITH_DRONE", base, "A drone can look from above before anyone is sent.")

    def incident(base: float) -> None:
        offer("CREATE_INCIDENT", base, "Open an incident, so that what is done about this is tracked.")

    if kind == "GUARD_EMERGENCY":
        offer("DISPATCH_GUARD", 0.95, "A guard raised an SOS: send the nearest guard on shift to them.", bounded=False)
        offer("ESCALATE", 0.95, "A guard's SOS goes to a supervisor at once.", bounded=False)
        look(0.8, "Look for the guard on the nearest camera.")
        offer("CONTACT_SITE", 0.6, "Tell the site that a guard needs help.", bounded=False)
    elif kind == "WEAPON":
        look(0.9, "Confirm on camera before anyone is sent towards a possible weapon.")
        offer("ESCALATE", 0.9, "A possible weapon goes to a supervisor.")
        incident(0.8)
        offer("CONTACT_SITE", 0.7, "Warn the site.")
    elif kind == "FIRE_SMOKE":
        look(0.9, "Confirm on camera whether there is fire or smoke.")
        offer("ESCALATE", 0.9, "Possible fire or smoke goes to a supervisor.")
        offer("CONTACT_SITE", 0.8, "Tell the site: people may need to leave.")
        if serious:
            offer("DISPATCH_GUARD", 0.65, "Send a guard to confirm on the ground, if it is safe to.")
        incident(0.75)
    elif kind == "FALL":
        look(0.9, "Check on camera whether someone is hurt.")
        offer("DISPATCH_GUARD", 0.8, "Someone may be hurt: send a guard to them.")
        if serious:
            offer("ESCALATE", 0.75, "A person possibly hurt goes to a supervisor.")
            incident(0.75)
    elif kind == "CAMERA_OFFLINE":
        offer("INVESTIGATE", 0.85, "A camera stopped sending: have it checked.")
        if serious:
            offer("DISPATCH_GUARD", 0.6, "The area is not being watched: send a guard to look.")
        offer("MONITOR", 0.6, "Watch for the camera coming back.")
    elif kind == "PATROL_FINDING":
        offer("INVESTIGATE", 0.8, "An operator on a virtual patrol reported an exception here: look into it.")
        look(0.75, "Look at the camera the patrol reported on.")
        if serious:
            offer("DISPATCH_GUARD", 0.7, "Send a guard to check what the patrol reported.")
            incident(0.7)
    elif level == "INFO":
        offer("MONITOR", 0.9, "Low risk: nothing to do but keep watching.")
    elif level == "LOW":
        offer("MONITOR", 0.85, "Low risk: keep watching.")
        if avail.cameras:
            offer("VIEW_CAMERA", 0.7, "A look at the camera will settle it.")
    elif level == "MEDIUM":
        look(0.85, "Look at the camera to confirm what this is.")
        drone(0.7)
        offer("MONITOR", 0.6, "If it looks ordinary, keep watching.")
    elif level == "HIGH":
        if corroborated:
            offer("DISPATCH_GUARD", 0.85, "More than one kind of source reported this: send a guard.")
            look(0.8, "Follow it on camera while the guard is on the way.")
        else:
            look(0.85, "Only one kind of source reported this: look before sending anyone.")
            offer("DISPATCH_GUARD", 0.7, "Send a guard to check.")
        drone(0.7)
        incident(0.7)
        if kind == "BLOCK_LISTED":
            offer("ESCALATE", 0.8, "A block-listed person or vehicle goes to a supervisor.")
    else:  # CRITICAL
        offer("DISPATCH_GUARD", 0.9 if corroborated else 0.75,
              "More than one kind of source reported this: send a guard now." if corroborated
              else "Send a guard now.")
        offer("ESCALATE", 0.85, "Critical risk goes to a supervisor.")
        incident(0.85)
        look(0.8, "Follow it on camera.")
        drone(0.65)

    # Whatever the kind: with nobody there to send, the site itself is the next call.
    if serious and avail.has_site and avail.guards_on_shift < 1:
        offer("CONTACT_SITE", 0.7, "Nobody is on shift there: call the site's contact.")
    # A camera or a rule that keeps being wrong, or an alert that keeps coming.
    if points.get("HISTORY", 0) <= -15:
        offer("INVESTIGATE", 0.75,
              "Most alerts of this kind from this camera were marked false: have the camera or its rule checked.")
    elif points.get("PERSISTENCE", 0) >= 10:
        offer("INVESTIGATE", 0.7, "The same alert keeps repeating: find out why.")

    # Never a list of nothing but steps that cannot be taken.
    if not any(r.available for r in out):
        if serious:
            offer("ESCALATE", 0.7, "None of the usual steps can be taken from here: take it to a supervisor.",
                  bounded=False)
        else:
            offer("MONITOR", 0.6, "None of the usual steps can be taken from here: keep watching.")

    # Surest first; what cannot be done now after what can. The sort is stable,
    # so equally sure steps stay in the order the rules gave them.
    out.sort(key=lambda r: (not r.available, -r.confidence))
    for rank, r in enumerate(out, start=1):
        r.rank = rank
    return out


# ─── Against the database ────────────────────────────────────────────────────

async def _scope(db: AsyncSession, tenant_id) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


def _camera_state(is_active: bool, health: str | None) -> str:
    """A camera's state from its last health transition. No transition on
    record is not_known — never assumed to be online."""
    if not is_active:
        return "disabled"
    return {"stream_disconnected": "offline", "stream_degraded": "degraded",
            "stream_reconnected": "online"}.get(health or "", "not_known")


async def availability(db: AsyncSession, situation: Mapping, events: list[Mapping], now: datetime) -> Availability:
    """What could be done about this situation at `now`. Read-only."""
    site_id = situation.get("site_id")

    # Cameras that reported, the most recent first, then the neighbours an
    # administrator linked to them.
    reported: list = []
    for e in sorted(events, key=lambda e: e["occurred_at"], reverse=True):
        if e.get("camera_id") is not None and e["camera_id"] not in reported:
            reported.append(e["camera_id"])
    neighbours: list = []
    if reported:
        for r in (await db.execute(text(
                "SELECT camera_a, camera_b FROM security_camera_links "
                " WHERE camera_a = ANY(CAST(:ids AS uuid[])) OR camera_b = ANY(CAST(:ids AS uuid[]))"),
                {"ids": reported})).all():
            for cam in (r.camera_a, r.camera_b):
                if cam not in reported and cam not in neighbours:
                    neighbours.append(cam)
    cameras: list[dict] = []
    if reported or neighbours:
        rows = {r["id"]: r for r in (await db.execute(text("""
            SELECT c.id, c.name, c.is_active,
                   (SELECT h.event_type FROM camera_health_events h
                     WHERE h.camera_id = c.id ORDER BY h.occurred_at DESC, h.id DESC LIMIT 1) AS health
              FROM cameras c WHERE c.id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": reported + neighbours})).mappings().all()}
        for relation, ids in (("reported", reported), ("neighbour", neighbours)):
            for cam in ids:
                if cam in rows:
                    cameras.append({"id": str(cam), "name": rows[cam]["name"], "relation": relation,
                                    "state": _camera_state(rows[cam]["is_active"], rows[cam]["health"])})

    # The drone that saw this, while it is still in the air: its own camera is
    # one more an officer can open. Once it has landed the camera shows a dock,
    # so it is listed only for a drone on a mission now.
    drone_ids = list({e["drone_id"] for e in events if e.get("drone_id") is not None})
    if drone_ids:
        listed = {c["id"] for c in cameras}
        for r in (await db.execute(text("""
            SELECT c.id, c.name, c.is_active,
                   (SELECT h.event_type FROM camera_health_events h
                     WHERE h.camera_id = c.id ORDER BY h.occurred_at DESC, h.id DESC LIMIT 1) AS health
              FROM drones d JOIN cameras c ON c.id = d.camera_id
             WHERE d.id = ANY(CAST(:ids AS uuid[])) AND d.status = 'MISSION_ACTIVE'
             ORDER BY c.name
        """), {"ids": drone_ids})).mappings().all():
            if str(r["id"]) not in listed:
                cameras.append({"id": str(r["id"]), "name": r["name"], "relation": "drone",
                                "state": _camera_state(r["is_active"], r["health"])})

    # An incident already open for one of the situation's events: the one an
    # event carries, or the one the platform opened for an event's alert.
    incident_ids = [e["incident_id"] for e in events if e.get("incident_id") is not None]
    alert_ids = [e["alert_id"] for e in events if e.get("alert_id") is not None]
    incident = None
    if incident_ids or alert_ids:
        row = (await db.execute(text("""
            SELECT i.id, i.status, i.dispatched_guard_id FROM incidents i
             WHERE (i.id = ANY(CAST(:incidents AS uuid[])) OR i.alert_id = ANY(CAST(:alerts AS uuid[])))
               AND i.status NOT IN ('resolved', 'closed')
             ORDER BY (i.dispatched_guard_id IS NOT NULL) DESC, i.created_at DESC LIMIT 1
        """), {"incidents": incident_ids, "alerts": alert_ids})).mappings().first()
        incident = dict(row) if row is not None else None

    looked = any(e.get("event_type") == "drone.verification" for e in events)
    if site_id is None:
        return Availability(has_site=False, cameras=tuple(cameras), incident=incident, drone_looked=looked,
                            guard_dispatched=bool(incident and incident.get("dispatched_guard_id")))

    guards = (await db.execute(text("""
        SELECT count(DISTINCT guard_user_id) FROM shifts
         WHERE site_id = :s AND status = 'active' AND actual_start IS NOT NULL
           AND actual_start <= :now AND actual_end IS NULL
    """), {"s": site_id, "now": now})).scalar() or 0
    drones = (await db.execute(text(f"""
        SELECT count(*) AS at_site,
               count(*) FILTER (WHERE d.status IN ({", ".join(repr(s) for s in DRONE_LAUNCHABLE)})
                                  AND d.communication_status = 'OK'
                                  AND EXISTS (SELECT 1 FROM drone_missions m
                                               WHERE m.drone_id = d.id AND m.enabled)) AS ready,
               count(*) FILTER (WHERE d.status = 'MISSION_ACTIVE') AS flying
          FROM drones d WHERE d.site_id = :s AND d.status NOT IN ('DISABLED', 'MAINTENANCE')
    """), {"s": site_id})).first()
    contact = (await db.execute(text(
        "SELECT btrim(COALESCE(site_contact_phone, '')) <> '' FROM sites WHERE id = :s"), {"s": site_id})).scalar()
    return Availability(
        has_site=True, cameras=tuple(cameras), guards_on_shift=int(guards),
        guard_dispatched=bool(incident and incident.get("dispatched_guard_id")),
        drones_at_site=int(drones.at_site or 0), drones_ready=int(drones.ready or 0),
        drone_in_flight=bool(drones.flying), drone_looked=looked, site_contact=bool(contact), incident=incident)


async def recommend_situation(db: AsyncSession, situation: Mapping, now: datetime) -> tuple[dict, list[Recommendation]]:
    """Write the suggestions for a situation's latest assessment. Returns (the
    assessment, the suggestions); the caller commits. The caller has checked
    that this assessment has none yet; the unique constraint is what holds it."""
    assessment = dict((await db.execute(text(
        "SELECT * FROM security_assessments WHERE id = :a"), {"a": situation["assessment_id"]})).mappings().one())
    events = [dict(r) for r in (await db.execute(text("""
        SELECT e.id, e.occurred_at, e.camera_id, e.alert_id, e.incident_id, e.event_type, e.drone_id
          FROM security_situation_events l JOIN security_events e ON e.id = l.event_id
         WHERE l.situation_id = :s
    """), {"s": situation["id"]})).mappings().all()]
    recs = recommend(assessment, await availability(db, situation, events, now))
    for r in recs:
        await db.execute(text("""
            INSERT INTO security_recommendations
                   (tenant_id, situation_id, assessment_id, rank, action, priority, reason, confidence,
                    confidence_limited_by, available, unavailable_reason, supporting, engine_version)
            VALUES (current_setting('app.current_tenant')::uuid, :s, :a, :rank, :action, :priority, :reason,
                    :confidence, :limited_by, :available, :why_not, CAST(:supporting AS jsonb), :engine)
        """), {"s": situation["id"], "a": assessment["id"], "rank": r.rank, "action": r.action,
               "priority": r.priority, "reason": r.reason, "confidence": r.confidence,
               "limited_by": r.limited_by, "available": r.available, "why_not": r.unavailable_reason,
               "supporting": json.dumps(r.supporting, default=str), "engine": ENGINE_VERSION})
    return assessment, recs


def announcement(situation: Mapping, assessment: Mapping, recs: list[Recommendation]) -> dict:
    """What goes on the tenant's live channel: that suggestions are ready, and
    the first of them. It says in so many words that it is not a decision."""
    first = next((r for r in recs if r.available), recs[0])
    return {
        "situation_id": str(situation["id"]),
        "situation_number": situation["situation_number"],
        "assessment_id": str(assessment["id"]),
        "kind": assessment["kind"],
        "label": assessment["label"],
        "risk_level": assessment["risk_level"],
        "risk_score": assessment["risk_score"],
        "is_decision": False,
        "suggested": {"action": first.action, "priority": first.priority, "reason": first.reason,
                      "recommendation_confidence": first.confidence, "limited_by": first.limited_by},
        "count": len(recs),
        "not_available": sum(1 for r in recs if not r.available),
    }


async def recommend_tenant(factory, tenant_id, now: datetime, batch: int = BATCH) -> dict:
    """Write suggestions for every situation whose latest assessment has none.
    Returns {"recommended", "failed", "announce"}."""
    out: dict = {"recommended": 0, "failed": 0, "announce": []}
    async with factory() as db:
        await _scope(db, tenant_id)
        ids = (await db.execute(text("""
            SELECT s.id FROM security_situations s
             WHERE s.assessment_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM security_recommendations r WHERE r.assessment_id = s.assessment_id)
             ORDER BY s.assessed_at LIMIT :n
        """), {"n": batch})).scalars().all()
        await db.rollback()
    for situation_id in ids:
        try:
            async with factory() as db:
                await _scope(db, tenant_id)
                situation = (await db.execute(text("""
                    SELECT s.* FROM security_situations s
                     WHERE s.id = :s AND s.assessment_id IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM security_recommendations r
                                        WHERE r.assessment_id = s.assessment_id)
                       FOR UPDATE OF s SKIP LOCKED
                """), {"s": situation_id})).mappings().first()
                if situation is None:
                    continue
                assessment, recs = await recommend_situation(db, dict(situation), now)
                await db.commit()
        except Exception:  # noqa: BLE001 — one situation's failure is not the batch's
            out["failed"] += 1
            continue
        out["recommended"] += 1
        out["announce"].append(("intel_recommendation_ready", announcement(situation, assessment, recs)))
    return out
