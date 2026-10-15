"""What else was recorded at a site around a vehicle's geofence entry or exit: evidence, never identity.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 6.

For an entry, exit or stay at a geofence that belongs to a site, worked out when it is looked at (nothing is
stored), within a window either side of it (two minutes unless the organisation sets 30 seconds to 15 minutes):

    plate reads       at the site's cameras                         detection:read
    barrier commands  at the site's barriers                        barrier:read
    door events       at the site's doors                           access:read
    incidents         at the site, open at the time                 incident:read

A plate read or barrier command whose plate is the vehicle's (compared letters and digits only, upper case) is
said to be "plate matches"; anything else is "near in time only". Each says how many seconds before or after it
was, and a plate read its confidence. Nothing is ever called confirmed: a plate that matches is a plate that
matches, not the vehicle, and certainly not who was driving. A section somebody may not read is left out and
says so; an empty one says that none was found.

A geofence that belongs to the whole organisation has no site cameras, barriers or doors to look at, and says so.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fleet_vehicles import plate_key

WINDOW_KEY = "fleet.evidence_window_s"
DEFAULT_WINDOW_S, LEAST_WINDOW_S, MOST_WINDOW_S = 120, 30, 900
MOST_EACH = 50
PLATE_MATCHES, NEAR_ONLY = "plate_matches", "near_in_time_only"
MATCH_WORDS = {PLATE_MATCHES: "Plate matches", NEAR_ONLY: "Near in time only"}
SECTIONS = {
    "plate_reads": ("detection:read", "Plate reads at the site's cameras"),
    "barrier_commands": ("barrier:read", "Barrier commands at the site"),
    "door_events": ("access:read", "Door events at the site"),
    "incidents": ("incident:read", "Incidents at the site, open at the time"),
}
PERMISSIONS = tuple(code for code, _ in SECTIONS.values())


def check_window(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not LEAST_WINDOW_S <= value <= MOST_WINDOW_S:
        raise ValueError(f"The window is {LEAST_WINDOW_S} to {MOST_WINDOW_S} seconds either side")
    return value


async def window_of(db: AsyncSession) -> int:
    row = (await db.execute(text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"), {"k": WINDOW_KEY})).first()
    if row is None:
        return DEFAULT_WINDOW_S
    value = row.setting_value
    try:
        return check_window(json.loads(value) if isinstance(value, str) else value)
    except ValueError:
        return DEFAULT_WINDOW_S


def _gap(at: datetime, start: datetime, end: datetime) -> int:
    """Seconds before (negative) or after (positive) the event; 0 inside a "between" stretch."""
    if at < start:
        return round((at - start).total_seconds())
    if at > end:
        return round((at - end).total_seconds())
    return 0


def _match(vehicle_key: str | None, plate: str | None) -> str:
    return PLATE_MATCHES if vehicle_key and plate_key(plate) == vehicle_key else NEAR_ONLY


async def around(db: AsyncSession, event: Mapping[str, Any], held: frozenset[str] | set[str], window_s: int) -> dict:
    """`event` carries the event's times, the vehicle's plate and the geofence's site."""
    start = event["between_start"] or event["occurred_at"]
    end = event["between_end"] or event["occurred_at"]
    out: dict = {"window_s": window_s, "site_id": event["site_id"], "site_name": event.get("site_name"),
                 "plate_number": event.get("plate_number"), "match_words": MATCH_WORDS, "sections": {}}
    if event["site_id"] is None:
        out["said"] = ("This geofence belongs to the whole organisation, not to a site: there are no site cameras, "
                       "barriers or doors to look at.")
        return out
    key = plate_key(event.get("plate_number"))
    params = {"site": str(event["site_id"]), "lo": start - timedelta(seconds=window_s),
              "hi": end + timedelta(seconds=window_s), "n": MOST_EACH}
    queries = {
        "plate_reads": """
            SELECT le.detected_at AS at, le.plate_number, le.plate_confidence AS confidence, le.direction,
                   c.name AS place
              FROM lpr_events le JOIN cameras c ON c.id = le.camera_id
             WHERE c.site_id = CAST(:site AS uuid) AND le.detected_at BETWEEN :lo AND :hi
             ORDER BY le.detected_at LIMIT :n""",
        "barrier_commands": """
            SELECT bc.created_at AS at, bc.plate_number, bc.command, bc.source, bc.decision, bc.succeeded,
                   b.name AS place
              FROM barrier_commands bc JOIN barriers b ON b.id = bc.barrier_id
             WHERE b.site_id = CAST(:site AS uuid) AND bc.created_at BETWEEN :lo AND :hi
             ORDER BY bc.created_at LIMIT :n""",
        "door_events": """
            SELECT ae.occurred_at AS at, ae.event_type, ae.denial_reason, d.name AS place
              FROM access_events ae JOIN access_doors d ON d.id = ae.door_id
             WHERE d.site_id = CAST(:site AS uuid) AND ae.occurred_at BETWEEN :lo AND :hi
             ORDER BY ae.occurred_at LIMIT :n""",
        "incidents": """
            SELECT i.id, i.created_at AS at, i.title, i.status, i.severity
              FROM incidents i
              LEFT JOIN cameras c ON c.id = i.camera_id
              LEFT JOIN alerts a ON a.id = i.alert_id
             WHERE COALESCE(c.site_id, a.site_id) = CAST(:site AS uuid)
               AND i.created_at <= :hi AND (i.resolved_at IS NULL OR i.resolved_at >= :lo)
             ORDER BY i.created_at LIMIT :n""",
    }
    for name, (code, label) in SECTIONS.items():
        if code not in held:
            out["sections"][name] = {"label": label, "allowed": False, "items": [],
                                     "said": f"Not shown: it needs {code}."}
            continue
        items = []
        for row in (await db.execute(text(queries[name]), params)).mappings():
            item = {k: (str(v) if k == "id" else v) for k, v in row.items()}
            item["gap_s"] = _gap(row["at"], start, end)
            if name in ("plate_reads", "barrier_commands"):
                item["match"] = _match(key, row["plate_number"])
            elif name == "door_events":
                item["match"] = NEAR_ONLY  # a door event carries no plate
            if "confidence" in item and item["confidence"] is not None:
                item["confidence"] = float(item["confidence"])
            items.append(item)
        if name in ("plate_reads", "barrier_commands"):
            items.sort(key=lambda i: (i["match"] != PLATE_MATCHES, abs(i["gap_s"])))
        out["sections"][name] = {"label": label, "allowed": True, "items": items,
                                 "said": None if items else "None found."}
    return out
