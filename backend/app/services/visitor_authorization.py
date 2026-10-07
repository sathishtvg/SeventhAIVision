"""Visitor and contractor authorisation: how one stands, what the gate is told, and where a visit's badge was used.

  standing()    where an authorisation stands at a moment: asked and unanswered,
                valid, run out, declined ... Pure.
  says()        the same, in the sentences a guard at the gate reads. Pure.
  within()      whether a place is among the places a visit is authorised for. Pure.
  movements()   the door events of the badge a visitor was given, while they
                had it, each marked as within the places and the period the
                visit is authorised for, outside them, or not able to be said

AN AUTHORISATION INFORMS; IT DOES NOT ADMIT OR REFUSE. `says` is what stands on
the record. The guard decides, and checks the visitor in through the endpoint
that has always done it.

A DOOR EVENT OUTSIDE THE PLACES OR THE PERIOD A VISIT IS AUTHORISED FOR IS NOT
A FINDING. It is listed for somebody who knows the site to look at: the visitor
may have been escorted, or sent there. This module raises nothing — no alert,
no incident — and uses no word for the visitor that a person has not used first.

WHAT IS NOT KNOWN IS SAID TO BE NOT KNOWN. A door that is not a place on the
site's map, a visit that names no places, one that was never approved, a
visitor who was given no badge, and a work permit (which has no badge of its
own) each give `within: null` or no movements at all, with the reason — never a
guess.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Collection, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATES = ("REQUESTED", "APPROVED", "DECLINED", "CANCELLED")
LIVE = ("REQUESTED", "APPROVED")
STANDINGS = ("NOT_ASKED", "AWAITING_HOST", "LAPSED", "DECLINED", "CANCELLED", "NOT_YET_VALID", "VALID", "EXPIRED")
REVIEW_OUTCOMES = ("IN_ORDER", "FOLLOWED_UP")
#: Offered when somebody records the kind of document they saw. Any short text is taken.
ID_KINDS = ("National identity card", "Passport", "Work pass", "Driving licence", "Company pass", "Other")
MAX_PLACES = 50
MAX_MOVEMENTS = 500
GATE_NOTE = ("This is what stands on the record for this visit. It does not check anybody in and it does not "
             "refuse anybody: you decide, and you check the visitor in as you always have.")
MOVEMENT_NOTE = ("A door event outside the places or the period a visit is authorised for is something to look at, "
                 "not a finding against anybody: the visitor may have been escorted, or sent there. Nothing is "
                 "raised by it.")
NO_BADGE = "No badge number was recorded when this visitor was checked in, so no door event can be put to the visit."
NO_PERMIT_BADGE = ("A work permit has no badge of its own on record: the people working under it are not "
                   "followed through doors.")
NOT_APPROVED = "This visit was not approved, so there are no authorised places or period to compare with."
NO_PLACES = "This visit names no particular places."
NOT_ON_MAP = "This door is not a place on the site's map."

#: `standing()` as SQL, for a list that is narrowed by it. `a` is the
#: authorisation and `:now` the moment. The two are held to each other by a test.
STANDING_SQL = """CASE WHEN a.state IN ('DECLINED', 'CANCELLED') THEN a.state
                       WHEN a.state = 'REQUESTED' THEN
                            CASE WHEN :now > a.valid_until THEN 'LAPSED' ELSE 'AWAITING_HOST' END
                       WHEN :now < a.valid_from THEN 'NOT_YET_VALID'
                       WHEN :now > a.valid_until THEN 'EXPIRED'
                       ELSE 'VALID' END"""


def standing(auth: Mapping | None, now: datetime) -> str:
    """Where an authorisation stands now."""
    if auth is None:
        return "NOT_ASKED"
    if auth["state"] in ("DECLINED", "CANCELLED"):
        return auth["state"]
    if auth["state"] == "REQUESTED":
        return "LAPSED" if now > auth["valid_until"] else "AWAITING_HOST"
    if now < auth["valid_from"]:
        return "NOT_YET_VALID"
    return "EXPIRED" if now > auth["valid_until"] else "VALID"


def _when(moment: datetime, now: datetime, zone: ZoneInfo) -> str:
    at, today = moment.astimezone(zone), now.astimezone(zone)
    return at.strftime("%H:%M") if at.date() == today.date() else f"{at.day} {at.strftime('%b %H:%M')}"


def says(auth: Mapping | None, now: datetime, *, places: Sequence[str] = (), timezone: str = "UTC") -> list[str]:
    """What stands, in the sentences a guard at the gate reads. Facts from the
    record, in order: the decision, the escort, the ID, the places."""
    if auth is None:
        return ["No authorisation has been asked for."]
    zone = ZoneInfo(timezone or "UTC")
    where = standing(auth, now)
    asked_of = auth.get("host_name") or "whoever manages visits"
    gone = "somebody no longer on the system"
    decider = auth.get("decided_by_name") or gone
    out = {
        "AWAITING_HOST": [f"Asked of {asked_of} at {_when(auth['requested_at'], now, zone)}. Not yet answered."],
        "LAPSED": [f"Asked of {asked_of} and never answered. The time it was asked for has passed."],
        "DECLINED": [f"Declined by {decider}: {auth.get('decision_note') or ''}".rstrip()],
        "CANCELLED": [f"Cancelled by {auth.get('cancelled_by_name') or gone}: {auth.get('cancel_reason') or ''}".rstrip()],
        "NOT_YET_VALID": [f"Approved by {decider}. Valid from {_when(auth['valid_from'], now, zone)}."],
        "VALID": [f"Approved by {decider}. Valid until {_when(auth['valid_until'], now, zone)}."],
        "EXPIRED": [f"Approved by {decider}. It ran out at {_when(auth['valid_until'], now, zone)}."],
    }[where]
    if auth["state"] in ("DECLINED", "CANCELLED"):
        return out
    if auth.get("escort_required"):
        who = auth.get("escort_name")
        note = f" ({auth['escort_note']})" if auth.get("escort_note") else ""
        out.append(f"To be escorted by {who}{note}." if who else f"To be escorted{note}: nobody is named yet.")
    else:
        out.append("No escort is asked for.")
    if auth.get("id_document_kind"):
        out.append(f"ID seen: {auth['id_document_kind']}, by {auth.get('id_checked_by_name') or gone}.")
    else:
        out.append("Nobody has recorded seeing an ID.")
    out.append(f"{'Authorised for' if auth['state'] == 'APPROVED' else 'Asked for'}: {', '.join(places)}." if places
               else "No particular places are named: the site in general.")
    return out


def within(place_id: Any, authorised: Collection[str], parents: Mapping[str, str | None]) -> bool | None:
    """Whether a place is among the places a visit is authorised for: the place
    itself, or anything it is a part of (a door on a floor of a building that
    is authorised is within it). None when it cannot be said — the door is not
    a place on the map, or the visit names no places."""
    if not authorised or place_id is None:
        return None
    at, seen = str(place_id), set()
    while at is not None and at not in seen:
        if at in authorised:
            return True
        seen.add(at)
        at = parents.get(at)
    return False


async def places_of(db: AsyncSession, authorization_ids: Sequence[Any]) -> dict[str, list[dict]]:
    """The places each of these authorisations names."""
    found: dict[str, list[dict]] = {str(a): [] for a in authorization_ids}
    if not authorization_ids:
        return found
    rows = await db.execute(text("""
        SELECT a.authorization_id, p.id, p.name, p.kind, p.parent_id, b.name AS part_of, p.is_active
          FROM visitor_authorization_places a
          JOIN site_places p ON p.id = a.place_id
          LEFT JOIN site_places b ON b.id = p.parent_id
         WHERE a.authorization_id = ANY(:ids) ORDER BY p.name
    """), {"ids": list(authorization_ids)})
    for r in rows.mappings():
        found[str(r["authorization_id"])].append({k: r[k] for k in ("id", "name", "kind", "parent_id", "part_of",
                                                                    "is_active")})
    return found


async def _parents(db: AsyncSession, site_ids: Sequence[Any]) -> dict[str, str | None]:
    """What each place of these sites is a part of."""
    if not site_ids:
        return {}
    rows = await db.execute(text("SELECT id, parent_id FROM site_places WHERE site_id = ANY(:sites)"),
                            {"sites": list(site_ids)})
    return {str(r.id): (str(r.parent_id) if r.parent_id else None) for r in rows}


#: The door events of the badges given to the visitors of these authorisations.
#: A badge is a visitor's from the moment it was written on their check-in until
#: they left, or until the same number was written against somebody else.
_EVENTS = """
    WITH held AS (
        SELECT a.id AS authorization_id, l.badge_number AS badge, l.occurred_at AS given_at,
               LEAST(COALESCE(v.departed_at, 'infinity'::timestamptz),
                     COALESCE((SELECT min(n.occurred_at) FROM visitor_logs n
                                WHERE n.badge_number = l.badge_number AND n.occurred_at > l.occurred_at
                                  AND n.visitor_id IS DISTINCT FROM l.visitor_id), 'infinity'::timestamptz)) AS given_until
          FROM visitor_authorizations a
          JOIN visitors v ON v.id = a.visitor_id
          JOIN visitor_logs l ON l.visitor_id = v.id AND l.badge_number IS NOT NULL AND btrim(l.badge_number) <> ''
         WHERE a.id = ANY(:ids)
    )
    SELECT DISTINCT ON (h.authorization_id, e.id)
           h.authorization_id, a.state, a.site_id, a.valid_from, a.valid_until,
           e.id, e.occurred_at, e.event_type, e.denial_reason, d.id AS door_id, d.name AS door_name, h.badge,
           p.id AS place_id, p.name AS place_name, p.kind AS place_kind, b.name AS part_of,
           r.outcome AS review_outcome, r.note AS review_note, r.reviewed_at, u.full_name AS reviewed_by_name
      FROM held h
      JOIN visitor_authorizations a ON a.id = h.authorization_id
      JOIN access_credentials cr ON cr.credential_ref = h.badge
      JOIN access_events e ON e.credential_id = cr.id AND e.occurred_at >= h.given_at AND e.occurred_at <= h.given_until
      JOIN access_doors d ON d.id = e.door_id AND d.site_id = a.site_id
      LEFT JOIN site_places p ON p.door_id = d.id AND p.is_active
      LEFT JOIN site_places b ON b.id = p.parent_id
      LEFT JOIN visitor_movement_reviews r ON r.access_event_id = e.id AND r.authorization_id = a.id
      LEFT JOIN users u ON u.id = r.reviewed_by_user_id
     ORDER BY h.authorization_id, e.id
"""


def _movement(row: Mapping, authorised: Collection[str], parents: Mapping[str, str | None]) -> dict:
    """One door event, with what can be said of it."""
    approved = row["state"] == "APPROVED"
    inside = within(row["place_id"], authorised, parents) if approved else None
    in_period = (row["valid_from"] <= row["occurred_at"] <= row["valid_until"]) if approved else None
    why = None
    if inside is None:
        why = NOT_APPROVED if not approved else NO_PLACES if not authorised else NOT_ON_MAP
    return {
        "authorization_id": row["authorization_id"], "access_event_id": row["id"], "occurred_at": row["occurred_at"],
        "event_type": row["event_type"], "denial_reason": row["denial_reason"], "door_id": row["door_id"],
        "door_name": row["door_name"], "badge": row["badge"], "place_id": row["place_id"],
        "place_name": row["place_name"], "part_of": row["part_of"],
        "within": inside, "in_period": in_period, "why_not_known": why,
        # Something for a person to look at. Not a finding.
        "to_look_at": inside is False or in_period is False,
        "review": ({"outcome": row["review_outcome"], "note": row["review_note"], "reviewed_at": row["reviewed_at"],
                    "reviewed_by_name": row["reviewed_by_name"]} if row["review_outcome"] else None),
    }


async def door_events(db: AsyncSession, authorization_ids: Sequence[Any],
                      places: Mapping[str, Sequence[Mapping]] | None = None) -> list[dict]:
    """The door events of these authorisations' badges, oldest first."""
    if not authorization_ids:
        return []
    rows = (await db.execute(text(f"SELECT * FROM ({_EVENTS}) x ORDER BY x.occurred_at, x.id LIMIT :cap"),
                             {"ids": list(authorization_ids), "cap": MAX_MOVEMENTS})).mappings().all()
    if not rows:
        return []
    named = places if places is not None else await places_of(db, authorization_ids)
    parents = await _parents(db, list({r["site_id"] for r in rows}))
    return [_movement(r, {str(p["id"]) for p in named.get(str(r["authorization_id"]), ())}, parents) for r in rows]


async def movements(db: AsyncSession, auth: Mapping, places: Sequence[Mapping]) -> dict:
    """Where the badge this visitor was given was used while they had it. Says
    why when there is nothing to give."""
    answer: dict[str, Any] = {"available": False, "why": None, "badges": [], "items": [], "note": MOVEMENT_NOTE}
    if auth.get("visitor_id") is None:
        answer["why"] = NO_PERMIT_BADGE
        return answer
    badges = [r[0] for r in await db.execute(text("""
        SELECT DISTINCT badge_number FROM visitor_logs
         WHERE visitor_id = :v AND badge_number IS NOT NULL AND btrim(badge_number) <> ''
    """), {"v": auth["visitor_id"]})]
    if not badges:
        answer["why"] = NO_BADGE
        return answer
    answer.update(available=True, badges=sorted(badges),
                  items=await door_events(db, [auth["id"]], {str(auth["id"]): places}))
    return answer
