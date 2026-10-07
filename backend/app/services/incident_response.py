"""Guard response: a sending, the steps a guard takes on it, and who is suggested.

  THE DISPATCH IS A PERSON'S, THROUGH THE EXISTING ENDPOINT. Nothing here sends
  a guard. `ensure` makes the record of a sending that has already happened;
  `advance` records what the guard then did; `rank` SUGGESTS who to send and
  says why, for a person to read.

  WHAT A GUARD DOES IS WRITTEN THROUGH TO THE INCIDENT. Setting off moves the
  incident to `en_route`; arriving moves it to `on_scene` and stamps
  `guard_arrived_at` — the very columns and the very status history the
  incident's own status endpoint writes, so that every screen that already
  reads an incident shows it.

  A GUARD WHO IS NOT COMING GIVES THE INCIDENT BACK. Declining, like being
  stood down, leaves the incident with nobody sent and open again, so that the
  desk sees it needs sending afresh. Nobody else is sent in their place: a
  person does that.

  A SUGGESTION IS NOT A DECISION. `rank` gives each guard on shift a score made
  of stated parts — free or already sent, how far by last recorded position and
  how old that position is, what they have already been sent on this shift,
  whether they hold what the site requires — and returns the parts beside the
  total. It reads the same positions the map reads (services/guard_positions.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import guard_positions

STATES = ("SENT", "ACCEPTED", "DECLINED", "EN_ROUTE", "ARRIVED", "STOOD_DOWN")
#: A response that is over: the guard is not coming, or was called off.
FINAL = ("DECLINED", "STOOD_DOWN")
#: What a guard's step moves a response to, and from which states it may.
MOVES: dict[str, tuple[str, tuple[str, ...]]] = {
    "ACCEPTED": ("ACCEPTED", ("SENT",)),
    "DECLINED": ("DECLINED", ("SENT", "ACCEPTED")),
    "EN_ROUTE": ("EN_ROUTE", ("SENT", "ACCEPTED")),
    "ARRIVED": ("ARRIVED", ("SENT", "ACCEPTED", "EN_ROUTE")),
}
#: The incident statuses a dispatch leaves an incident in. The existing
#: dispatch writes `in_progress`; the status workflow calls the same thing
#: `dispatched`. Both mean a guard has been sent and has not set off.
SENT_STATUSES = ("open", "in_progress", "dispatched")
SUPERSEDED = "The incident was dispatched again."

SUGGESTION_NOTE = ("A suggestion, made of the parts shown beside it. It sends nobody: a person chooses who to "
                   "dispatch. " + guard_positions.NOTE)


class NotAllowed(Exception):
    """A step that cannot be taken from where the response stands."""


def may(step: str, state: str) -> bool:
    return step in MOVES and state in MOVES[step][1]


async def ensure(db: AsyncSession, incident: Mapping) -> dict | None:
    """The response for the incident's current sending — made now if this is
    the first time anything has looked. None when nobody is dispatched.

    A sending that replaced an earlier one closes the earlier response as
    stood down, with that as the reason. `made_now` is true on the answer the
    one time the record is made, which is when the guard is told."""
    if incident.get("dispatched_guard_id") is None or incident.get("dispatched_at") is None:
        return None
    found = (await db.execute(text(
        "SELECT * FROM incident_responses WHERE incident_id = :i AND dispatched_at = :at"),
        {"i": incident["id"], "at": incident["dispatched_at"]})).mappings().first()
    if found is not None:
        return {**found, "made_now": False}
    earlier = (await db.execute(text("""
        UPDATE incident_responses
           SET state = 'STOOD_DOWN', stood_down_at = now(), stand_down_reason = :why, updated_at = now()
         WHERE incident_id = :i AND state NOT IN ('DECLINED','STOOD_DOWN')
        RETURNING id
    """), {"i": incident["id"], "why": SUPERSEDED})).all()
    for (response_id,) in earlier:
        await _step(db, response_id, incident["id"], "STOOD_DOWN", None, None, SUPERSEDED)
    made = (await db.execute(text("""
        INSERT INTO incident_responses (tenant_id, incident_id, site_id, guard_user_id, dispatched_at)
        VALUES (current_setting('app.current_tenant')::uuid, :i, :site, :guard, :at)
        ON CONFLICT (incident_id, dispatched_at) DO NOTHING
        RETURNING *
    """), {"i": incident["id"], "site": incident.get("site_id"), "guard": incident["dispatched_guard_id"],
           "at": incident["dispatched_at"]})).mappings().first()
    if made is None:  # somebody else looked in the same moment
        return {**(await db.execute(text(
            "SELECT * FROM incident_responses WHERE incident_id = :i AND dispatched_at = :at"),
            {"i": incident["id"], "at": incident["dispatched_at"]})).mappings().one(), "made_now": False}
    await _step(db, made["id"], incident["id"], "SENT", None, None, incident.get("dispatch_notes"),
                at=incident["dispatched_at"])
    return {**made, "made_now": True}


_SENDING = """
    SELECT i.id, i.title, i.severity, i.status, i.dispatched_guard_id, i.dispatched_at, i.dispatch_notes,
           c.site_id, s.name AS site_name
      FROM incidents i
      LEFT JOIN cameras c ON c.id = i.camera_id
      LEFT JOIN sites s ON s.id = c.site_id
"""


async def open_sendings(db: AsyncSession, *, guard_user_id: Any = None) -> list[dict]:
    """Make the record of every sending that has none yet, and return those
    made now — each with its incident's title and site, for telling the guard.

    The dispatch itself is the existing endpoint's and writes no record here;
    this is what notices that it happened. Called by whatever looks first: the
    desk, the guard's own phone, or the scheduler's pass a minute later."""
    params: dict = {}
    mine = ""
    if guard_user_id is not None:
        mine = "AND i.dispatched_guard_id = CAST(:guard AS uuid)"
        params["guard"] = str(guard_user_id)
    rows = (await db.execute(text(f"""{_SENDING}
         WHERE i.dispatched_guard_id IS NOT NULL AND i.dispatched_at IS NOT NULL
           AND i.status NOT IN ('resolved','closed') {mine}
           AND NOT EXISTS (SELECT 1 FROM incident_responses r
                            WHERE r.incident_id = i.id AND r.dispatched_at = i.dispatched_at)
         ORDER BY i.dispatched_at LIMIT 200
    """), params)).mappings().all()
    made = []
    for incident in rows:
        response = await ensure(db, incident)
        if response and response["made_now"]:
            made.append({"response_id": response["id"], "incident_id": incident["id"],
                         "title": incident["title"], "severity": incident["severity"],
                         "site_id": incident["site_id"], "site_name": incident["site_name"],
                         "guard_user_id": incident["dispatched_guard_id"],
                         "dispatched_at": incident["dispatched_at"]})
    return made


async def _step(db: AsyncSession, response_id, incident_id, step: str, actor_user_id, actor_role,
                note: str | None = None, latitude: float | None = None, longitude: float | None = None,
                at: datetime | None = None) -> None:
    await db.execute(text("""
        INSERT INTO incident_response_steps
               (tenant_id, response_id, incident_id, step, actor_user_id, actor_role, note, latitude, longitude,
                occurred_at)
        VALUES (current_setting('app.current_tenant')::uuid, :r, :i, :step, CAST(:who AS uuid), :role, :note,
                :lat, :lng, COALESCE(:at, now()))
    """), {"r": response_id, "i": incident_id, "step": step, "who": str(actor_user_id) if actor_user_id else None,
           "role": actor_role, "note": note, "lat": latitude, "lng": longitude, "at": at})


async def _status(db: AsyncSession, incident: Mapping, to_status: str, user_id: str, note: str | None,
                  latitude: float | None, longitude: float | None, arrived: bool = False) -> None:
    """Move the incident on, exactly as its own status endpoint would: the
    status, and a line of history with where it was reported from."""
    await db.execute(text(
        "UPDATE incidents SET status = :s, updated_at = now()"
        + (", guard_arrived_at = COALESCE(guard_arrived_at, now())" if arrived else "") + " WHERE id = :i"),
        {"s": to_status, "i": incident["id"]})
    await db.execute(text("""
        INSERT INTO incident_status_history
               (tenant_id, incident_id, changed_by_user_id, from_status, to_status, latitude, longitude, notes)
        VALUES (current_setting('app.current_tenant')::uuid, :i, CAST(:u AS uuid), :from, :to, :lat, :lng, :notes)
    """), {"i": incident["id"], "u": user_id, "from": incident["status"], "to": to_status, "lat": latitude,
           "lng": longitude, "notes": note})


async def _release(db: AsyncSession, incident: Mapping, user_id: str, note: str) -> None:
    """Nobody is sent on the incident any more. It goes back to open if the
    guard had not yet arrived, so that it can be dispatched afresh."""
    await db.execute(text("""
        UPDATE incidents SET dispatched_guard_id = NULL, dispatched_at = NULL, sla_deadline_at = NULL,
                             updated_at = now()
         WHERE id = :i
    """), {"i": incident["id"]})
    if incident["status"] in (*SENT_STATUSES[1:], "en_route"):
        await _status(db, incident, "open", user_id, note, None, None)


async def advance(db: AsyncSession, incident: Mapping, response: Mapping, step: str, *, user_id: str, role_id: int,
                  note: str | None = None, latitude: float | None = None, longitude: float | None = None) -> dict:
    """Take one step on a response: ACCEPTED, DECLINED, EN_ROUTE, ARRIVED.
    Raises NotAllowed when the response is not where that step starts from."""
    if not may(step, response["state"]):
        raise NotAllowed(step, response["state"])
    stamps = {"ACCEPTED": "accepted_at = now()", "DECLINED": "declined_at = now(), decline_reason = :note",
              # Setting off, or arriving, without having said yes first is still a yes.
              "EN_ROUTE": "accepted_at = COALESCE(accepted_at, now()), en_route_at = now()",
              "ARRIVED": "accepted_at = COALESCE(accepted_at, now()), arrived_at = now()"}[step]
    row = (await db.execute(text(f"""
        UPDATE incident_responses SET state = :state, {stamps}, updated_at = now() WHERE id = :r RETURNING *
    """), {"state": MOVES[step][0], "r": response["id"], **({"note": note} if step == "DECLINED" else {})}
    )).mappings().one()
    await _step(db, response["id"], incident["id"], step, user_id, role_id, note, latitude, longitude)
    if step == "DECLINED":
        await _release(db, incident, user_id, f"Guard cannot attend: {note}")
    elif step == "EN_ROUTE" and incident["status"] in SENT_STATUSES:
        await _status(db, incident, "en_route", user_id, note, latitude, longitude)
    elif step == "ARRIVED" and incident["status"] in (*SENT_STATUSES, "en_route"):
        await _status(db, incident, "on_scene", user_id, note, latitude, longitude, arrived=True)
    elif step == "ARRIVED":
        # Already further on than "on scene": the arrival is still stamped.
        await db.execute(text("UPDATE incidents SET guard_arrived_at = COALESCE(guard_arrived_at, now()) "
                              "WHERE id = :i"), {"i": incident["id"]})
    return dict(row)


async def report(db: AsyncSession, incident: Mapping, response: Mapping, *, user_id: str, role_id: int, note: str,
                 latitude: float | None = None, longitude: float | None = None) -> None:
    """What the guard found, said from the ground. Changes no state."""
    if response["state"] in FINAL:
        raise NotAllowed("REPORTED", response["state"])
    await _step(db, response["id"], incident["id"], "REPORTED", user_id, role_id, note, latitude, longitude)


async def stand_down(db: AsyncSession, incident: Mapping, response: Mapping, *, user_id: str, role_id: int,
                     reason: str) -> dict:
    """Call a guard off. The incident is nobody's again."""
    if response["state"] in FINAL:
        raise NotAllowed("STOOD_DOWN", response["state"])
    row = (await db.execute(text("""
        UPDATE incident_responses
           SET state = 'STOOD_DOWN', stood_down_at = now(), stood_down_by_user_id = CAST(:u AS uuid),
               stand_down_reason = :why, updated_at = now()
         WHERE id = :r RETURNING *
    """), {"u": user_id, "why": reason, "r": response["id"]})).mappings().one()
    await _step(db, response["id"], incident["id"], "STOOD_DOWN", user_id, role_id, reason)
    await _release(db, incident, user_id, f"Guard stood down: {reason}")
    return dict(row)


# ─── Who to send: a suggestion ───────────────────────────────────────────────

def score(guard: Mapping, *, sent_this_shift: int, holds_what_the_site_requires: bool | None) -> dict:
    """One guard's score for one incident, and the parts it is made of."""
    parts: list[dict] = []

    def part(factor: str, points: int, detail: str) -> None:
        parts.append({"factor": factor, "points": points, "detail": detail})

    if guard.get("emergency_id"):
        part("AVAILABILITY", -100, "Has raised an emergency that is still open.")
    elif guard["available"]:
        part("AVAILABILITY", 40, "Free: not sent on anything that is still open.")
    else:
        part("AVAILABILITY", 0, "Already sent on an incident that is still open.")

    d = guard.get("distance_m")
    if d is None:
        part("DISTANCE", 0, "No distance: no position recorded this shift, or the incident has none.")
    else:
        points = 30 if d <= 100 else 20 if d <= 300 else 10 if d <= 1000 else 0
        part("DISTANCE", points, f"{d} m away by last recorded position.")

    age = guard.get("position_age_s")
    if age is not None:
        minutes = max(1, round(age / 60))
        if guard.get("stale"):
            part("POSITION_AGE", -10, f"That position is {minutes} min old: too old to say where they are now.")
        elif age <= 900:
            part("POSITION_AGE", 10, f"That position was recorded {minutes} min ago.")
        else:
            part("POSITION_AGE", 0, f"That position was recorded {minutes} min ago.")

    if sent_this_shift:
        part("WORKLOAD", -min(15, 5 * sent_this_shift),
             f"Already sent on {sent_this_shift} incident{'s' if sent_this_shift != 1 else ''} this shift.")
    else:
        part("WORKLOAD", 0, "Not sent on anything yet this shift.")

    if holds_what_the_site_requires is True:
        part("CERTIFICATION", 10, "Holds every certification this site requires, in date.")
    elif holds_what_the_site_requires is False:
        part("CERTIFICATION", 0, "Does not hold, in date, every certification this site requires.")
    return {"score": sum(p["points"] for p in parts), "parts": parts}


def rank(guards: Sequence[Mapping], *, sent: Mapping[str, int], certified: Mapping[str, bool] | None) -> list[dict]:
    """The guards with their scores, highest first. `certified` is None when
    the site requires no certification, and then nobody is scored on it."""
    ranked = []
    for g in guards:
        key = str(g["user_id"])
        ranked.append({**g, **score(g, sent_this_shift=int(sent.get(key, 0)),
                                    holds_what_the_site_requires=None if certified is None
                                    else bool(certified.get(key, False)))})
    # Among equals: somebody free before somebody already sent, then the nearer.
    ranked.sort(key=lambda g: (-g["score"], not g["available"], g["distance_m"] is None, g["distance_m"] or 0,
                               str(g["full_name"] or "")))
    return ranked


async def recommend(db: AsyncSession, incident: Mapping, now: datetime, allowed: list[str] | None) -> dict:
    """Who is on shift at the incident's site, ranked, with the reasons. For a
    person to read: `is_decision` is false and nothing is sent."""
    site_id = incident.get("site_id")
    answer: dict[str, Any] = {"incident_id": incident["id"], "site_id": site_id, "guards": [], "is_decision": False,
                              "located": incident.get("latitude") is not None, "note": SUGGESTION_NOTE}
    if site_id is None:
        answer["why_nobody"] = "The incident has no site, so there is no shift to look at."
        return answer
    on_shift = guard_positions.nearest(await guard_positions.on_shift(db, now, allowed, site_id=site_id),
                                       incident.get("latitude"), incident.get("longitude"))
    if not on_shift:
        answer["why_nobody"] = "Nobody is clocked in at this site."
        return answer
    ids = [g["user_id"] for g in on_shift]
    sent = {str(r.guard_user_id): r.n for r in await db.execute(text("""
        SELECT r.guard_user_id, count(*) AS n
          FROM incident_responses r
         WHERE r.guard_user_id = ANY(:ids) AND r.incident_id <> :i
           AND r.dispatched_at >= (SELECT max(s.actual_start) FROM shifts s
                                    WHERE s.guard_user_id = r.guard_user_id AND s.actual_start <= :now
                                      AND (s.actual_end IS NULL OR s.actual_end >= :now))
         GROUP BY r.guard_user_id
    """), {"ids": ids, "now": now, "i": incident["id"]})}
    required = [r.certification_type for r in await db.execute(text("""
        SELECT DISTINCT certification_type FROM certification_requirements
         WHERE is_active AND (site_id = :site OR site_id IS NULL)
    """), {"site": site_id})]
    certified: dict[str, bool] | None = None
    if required:
        held: dict[str, set[str]] = {}
        for r in await db.execute(text("""
            SELECT user_id, certification_type FROM guard_certifications
             WHERE user_id = ANY(:ids) AND is_valid AND (expires_at IS NULL OR expires_at >= CURRENT_DATE)
        """), {"ids": ids}):
            held.setdefault(str(r.user_id), set()).add(r.certification_type)
        certified = {str(i): set(required) <= held.get(str(i), set()) for i in ids}
        answer["site_requires"] = sorted(required)
    answer["guards"] = rank(on_shift, sent=sent, certified=certified)
    return answer
