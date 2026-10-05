"""The person on the ground: what a guard is shown, and what they report.

A GUARD'S REACH IS THEIR OWN SHIFT. Every other role is confined, if at all, by
the sites an administrator assigned to it. A guard usually has none assigned,
which everywhere else on the platform means "all sites" — and for deciding about
a security situation that is too much. So for a guard, and only for a guard, the
layer asks one more thing: is the situation at the site of a shift they are on
right now, or one they were dispatched to? If neither, they may read it like
anyone else and may not decide on it or report from it. This narrows; it grants
nothing.

WHAT A GUARD IS SHOWN is what they were dispatched to, always — and the other
open situations at their site only where the decision policy lets a guard
decide there at all. Where the command centre controls incidents, a guard's
phone shows the ones the command centre sent them.

AN OBSERVATION DECIDES NOTHING. "I have this", "I am there", "this is what I
see" are recorded as that person's and change no alert, incident or dispatch.

Read-only on everything but `security_observations`. It is used by the API, on
a person's request; the runner does not import it.
"""
from __future__ import annotations

import uuid
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import intel_decisions as decisions

GUARD = 5
KINDS = ("ACCEPTED", "ARRIVED", "OBSERVATION")

#: The incidents of a situation: the one a person opened or confirmed for it,
#: those its events carry, and those the platform opened for its alerts.
_INCIDENTS_OF = """
    (i.id = s.incident_id
     OR i.id IN (SELECT e.incident_id FROM security_situation_events l
                   JOIN security_events e ON e.id = l.event_id
                  WHERE l.situation_id = s.id AND e.incident_id IS NOT NULL)
     OR i.alert_id IN (SELECT e.alert_id FROM security_situation_events l
                         JOIN security_events e ON e.id = l.event_id
                        WHERE l.situation_id = s.id AND e.alert_id IS NOT NULL))
"""


async def shift_sites(db: AsyncSession, user_id) -> list:
    """The sites of the shifts this person is on right now."""
    return list((await db.execute(text("""
        SELECT DISTINCT site_id FROM shifts
         WHERE guard_user_id = CAST(:u AS uuid) AND status = 'active'
           AND actual_start IS NOT NULL AND actual_end IS NULL AND site_id IS NOT NULL
    """), {"u": str(user_id)})).scalars().all())


async def dispatch_for(db: AsyncSession, situation_id, user_id) -> dict | None:
    """The open incident of this situation that this person was dispatched to."""
    row = (await db.execute(text(f"""
        SELECT i.id AS incident_id, i.status, i.dispatched_at, i.guard_arrived_at, i.dispatch_notes
          FROM security_situations s JOIN incidents i ON {_INCIDENTS_OF}
         WHERE s.id = :s AND i.dispatched_guard_id = CAST(:u AS uuid) AND i.status NOT IN ('resolved', 'closed')
         ORDER BY i.dispatched_at DESC NULLS LAST LIMIT 1
    """), {"s": situation_id, "u": str(user_id)})).mappings().first()
    return dict(row) if row is not None else None


async def within_reach(db: AsyncSession, situation: Mapping, user_id, role_id: int) -> bool:
    """Whether this person may decide on or report from this situation, as far
    as reach goes. Anyone but a guard: yes — their sites were already checked."""
    if role_id != GUARD:
        return True
    if situation.get("site_id") is not None and situation["site_id"] in await shift_sites(db, user_id):
        return True
    return await dispatch_for(db, situation["id"], user_id) is not None


async def mine(db: AsyncSession, user_id, role_id: int, allowed: list[str] | None, limit: int = 50) -> list[dict]:
    """The open situations in front of this person, those they were dispatched
    to first, then by risk. For a guard: what they were dispatched to, and the
    rest of their shift's site only where the policy lets a guard decide there.
    For anyone else: the open situations of the sites they may see."""
    params: dict = {"u": str(user_id), "limit": limit}
    if role_id == GUARD:
        sites = []
        for site_id in await shift_sites(db, user_id):
            rule = (await decisions.policy_for(db, site_id))["roles"].get(str(GUARD)) or {}
            if rule.get("alone") or rule.get("with_approval"):
                sites.append(site_id)
        params["sites"] = sites
        where = "(d.incident_id IS NOT NULL OR s.site_id = ANY(CAST(:sites AS uuid[])))"
    elif allowed is None:
        where = "TRUE"
    else:
        params["sites"] = [uuid.UUID(str(site)) for site in allowed]
        where = "(d.incident_id IS NOT NULL OR s.site_id = ANY(CAST(:sites AS uuid[])))"
    rows = (await db.execute(text(f"""
        SELECT s.id, s.situation_number, s.title, s.severity, s.site_id, st.name AS site_name,
               s.primary_camera_id, c.name AS primary_camera_name, s.location_label, s.latitude, s.longitude,
               s.started_at, s.last_event_at, s.event_count, s.source_types, s.risk_score, s.risk_level,
               s.decision_status, a.kind, a.label,
               d.incident_id AS dispatch_incident_id, d.dispatched_at, d.guard_arrived_at, d.dispatch_notes,
               (SELECT o.kind FROM security_observations o
                 WHERE o.situation_id = s.id AND o.user_id = CAST(:u AS uuid) AND o.kind <> 'OBSERVATION'
                 ORDER BY o.observed_at DESC LIMIT 1) AS my_last
          FROM security_situations s
          LEFT JOIN sites st ON st.id = s.site_id
          LEFT JOIN cameras c ON c.id = s.primary_camera_id
          LEFT JOIN security_assessments a ON a.id = s.assessment_id
          LEFT JOIN LATERAL (
              SELECT i.id AS incident_id, i.dispatched_at, i.guard_arrived_at, i.dispatch_notes FROM incidents i
               WHERE i.dispatched_guard_id = CAST(:u AS uuid) AND i.status NOT IN ('resolved', 'closed')
                 AND {_INCIDENTS_OF}
               ORDER BY i.dispatched_at DESC NULLS LAST LIMIT 1) d ON TRUE
         WHERE s.closed_at IS NULL AND {where}
         ORDER BY (d.incident_id IS NOT NULL) DESC, s.risk_score DESC NULLS LAST, s.last_event_at DESC
         LIMIT :limit
    """), params)).mappings().all()
    return [{**dict(r), "assigned_to_me": r["dispatch_incident_id"] is not None} for r in rows]


async def record(db: AsyncSession, *, situation_id, kind: str, note: str | None, user_id, role_id: int,
                 latitude: float | None, longitude: float | None, via: str, request_id: str | None,
                 client_ref) -> dict:
    row = (await db.execute(text("""
        INSERT INTO security_observations
               (tenant_id, situation_id, kind, note, user_id, actor_role, latitude, longitude, via, request_id,
                client_ref)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :kind, :note, CAST(:u AS uuid), :role, :lat, :lon,
                :via, :rid, :ref)
        RETURNING id, observed_at
    """), {"s": situation_id, "kind": kind, "note": (note or "").strip() or None, "u": str(user_id), "role": role_id,
           "lat": latitude, "lon": longitude, "via": via, "rid": request_id, "ref": client_ref})).mappings().one()
    return dict(row)


async def observations(db: AsyncSession, situation_id) -> list[dict]:
    """What was reported from the ground about a situation, oldest first."""
    rows = (await db.execute(text("""
        SELECT o.id, o.kind, o.note, o.user_id, u.full_name AS name, o.actor_role AS role_id, o.latitude,
               o.longitude, o.via, o.observed_at
          FROM security_observations o LEFT JOIN users u ON u.id = o.user_id
         WHERE o.situation_id = :s ORDER BY o.observed_at, o.id
    """), {"s": situation_id})).mappings().all()
    return [dict(r) for r in rows]
