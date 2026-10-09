"""A visitor's badge used outside what the visit is authorised for, as an event of the intelligence layer.

Phase 7 sets a visitor's door events against their authorisation and lists
those outside it for a person to look at (services/visitor_authorization.py).
It deliberately did not hand them to the intelligence layer: that changes what
the layer relates and scores, and was left for the owner. Decided on
2026-10-09: the layer may read them - when an organisation asks for that.

OFF UNLESS THE ORGANISATION SWITCHES IT ON (`visitor.movements_to_intelligence`).
With it off this reads nothing, and the layer is exactly as it was.

IT IS AN EVENT, NOT AN ACCUSATION. What is handed over is "a visitor's badge
was used at this door, outside the places or the period the visit was
authorised for" - low severity, something to look at. The event names nobody:
no visitor's name, no badge number, no subject. It carries the authorisation's
id, so that somebody who may read visits can open it.

ONLY WHAT IS STILL TO BE LOOKED AT. A door event a person has already reviewed
is not handed over: they looked, and said what it was.

THE LAYER DOES WITH IT WHAT IT DOES WITH ANY EVENT: places it beside what else
happened there and then - a camera's alert, a door forced - assesses the
situation and suggests. It decides nothing, and neither does this.

THE EVENT'S PLACE IS THE DOOR'S PLACE ON THE SITE'S MAP, when the door is on
it. A door that is not on the map has no coordinates and is related by site
and time only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import intel_events as events
from app.services import visitor_authorization as authorisation

logger = logging.getLogger(__name__)

SETTING = "visitor.movements_to_intelligence"
SOURCE = "visitor_movements"
EVENT_TYPE = "visitor.badge_outside_authorisation"
#: How far back an authorisation's period may have ended and its door events still be read.
LOOK_BACK = timedelta(hours=24)
#: The most authorisations read in one pass for one organisation.
MAX_AUTHORISATIONS = 200


def normalise(movement: dict, site_id, place: dict | None) -> events.Normalised:
    """One door event to look at, in the layer's common shape. Nobody is named."""
    outside = "the places" if movement["within"] is False else "the period"
    if movement["within"] is False and movement["in_period"] is False:
        outside = "the places and the period"
    return events.Normalised(
        source_type="ACCESS_CONTROL",
        source_table="access_events",
        source_id=movement["access_event_id"],
        event_type=EVENT_TYPE,
        occurred_at=movement["occurred_at"],
        severity="low",
        title=f"A visitor's badge was used outside {outside} the visit is authorised for: "
              f"{movement.get('door_name') or 'a door'}"[:255],
        site_id=site_id,
        latitude=float(place["latitude"]) if place and place.get("latitude") is not None else None,
        longitude=float(place["longitude"]) if place and place.get("longitude") is not None else None,
        location_label=(movement.get("place_name") or movement.get("door_name")),
        attributes={k: v for k, v in {
            "authorization_id": str(movement["authorization_id"]),
            "door_id": str(movement["door_id"]) if movement.get("door_id") else None,
            "place_id": str(movement["place_id"]) if movement.get("place_id") else None,
            "door_event": movement.get("event_type"),
            "within_places": movement["within"],
            "within_period": movement["in_period"],
            "note": "Something to look at, not a finding.",
        }.items() if v is not None},
    )


async def enabled(db: AsyncSession) -> bool:
    """Whether the organisation in scope has asked for its visitors' door events to be read."""
    value = (await db.execute(text(
        "SELECT setting_value FROM tenant_settings WHERE setting_key = :k"), {"k": SETTING})).scalar()
    return value is True


async def ingest_tenant(factory, tenant_id, now: datetime | None = None) -> int:
    """Hand the layer one organisation's door events that are still to be looked at. Returns how many were new.
    Nothing is read, and 0 is returned, unless the organisation has switched it on."""
    async with factory() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})
        if not await enabled(db):
            await db.rollback()
            return 0
        moment = now or datetime.now(timezone.utc)
        approved = (await db.execute(text("""
            SELECT a.id, a.site_id FROM visitor_authorizations a
             WHERE a.state = 'APPROVED' AND a.visitor_id IS NOT NULL AND a.valid_until >= :since
             ORDER BY a.valid_until DESC LIMIT :n
        """), {"since": moment - LOOK_BACK, "n": MAX_AUTHORISATIONS})).mappings().all()
        site_of = {str(r["id"]): r["site_id"] for r in approved}
        moved = await authorisation.door_events(db, [r["id"] for r in approved])
        to_hand = [m for m in moved if m["to_look_at"] and m["review"] is None]
        doors = [m["door_id"] for m in to_hand if m.get("door_id")]
        places = {str(r["door_id"]): dict(r) for r in (await db.execute(text("""
            SELECT door_id, latitude, longitude FROM site_places
             WHERE door_id = ANY(:doors) AND is_active
        """), {"doors": doors})).mappings()} if doors else {}
        new = 0
        for movement in to_hand:
            n = normalise(movement, site_of.get(str(movement["authorization_id"])),
                          places.get(str(movement["door_id"])))
            params = asdict(n)
            params["attributes"] = json.dumps(params["attributes"], default=str)
            if (await db.execute(events._INSERT, params)).first() is not None:
                new += 1
        await db.commit()
        return new
