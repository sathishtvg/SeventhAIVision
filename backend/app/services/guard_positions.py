"""Where each guard on shift last recorded being — and how long ago that was.

THERE IS NO LIVE POSITION OF A GUARD, AND THIS DOES NOT PRETEND TO ONE (owner
decision E3). The phone does not report where it is. What the platform has is
the position a guard recorded when they did something: clocked in, scanned a
patrol checkpoint, changed an incident's status, wrote an occurrence-book
entry, raised an emergency. This module takes the most recent of those since
the guard's shift began and says which it was and when.

EVERY ANSWER CARRIES ITS AGE. "Nearest guard" computed from these is "nearest
by last recorded position", and anything shown from them says how old the
position is. One older than `STALE_AFTER_S` is marked stale.

Read-only. The same reading serves the map and the dispatch recommendation, so
that the two can never disagree about where somebody was.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import site_scope_clause
from app.services.geofence import haversine_meters

#: A position older than this says little about where somebody is now.
STALE_AFTER_S = 3600
NOTE = ("A guard's position is where they last clocked in, scanned a checkpoint or reported from — not where "
        "they are now. Each one says how long ago it was recorded.")


def latest_position(known: Sequence[tuple[datetime | None, float | None, float | None, str]]) -> tuple:
    """The most recent of (when, latitude, longitude, what it was) that has all
    three — or four Nones when there is none."""
    whole = [k for k in known if k[0] is not None and k[1] is not None and k[2] is not None]
    return max(whole, key=lambda k: k[0]) if whole else (None, None, None, None)


def distance_m(a_lat: Any, a_lng: Any, b_lat: Any, b_lng: Any) -> int | None:
    """Metres between two positions, or None when either is not known."""
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    return round(haversine_meters(float(a_lat), float(a_lng), float(b_lat), float(b_lng)))


def describe(row: Mapping, now: datetime) -> dict:
    at, lat, lng, source = latest_position([
        (row["scanned_at"], row["scan_lat"], row["scan_lng"], "checkpoint scan"),
        (row["changed_at"], row["upd_lat"], row["upd_lng"], "incident status update"),
        (row["entry_at"], row["entry_lat"], row["entry_lng"], "occurrence book entry"),
        (row["emergency_at"], row["emergency_lat"], row["emergency_lng"], "guard emergency"),
        (row["actual_start"], row["check_in_lat"], row["check_in_lon"], "shift check-in"),
    ])
    age = round((now - at).total_seconds()) if at else None
    return {
        "user_id": row["user_id"], "full_name": row["full_name"], "role_id": row["role_id"],
        "site_id": row["site_id"], "site_name": row["site_name"], "shift_started_at": row["actual_start"],
        "latitude": float(lat) if lat is not None else None, "longitude": float(lng) if lng is not None else None,
        "position_source": source, "position_at": at, "position_age_s": age,
        "stale": age is not None and age > STALE_AFTER_S,
        "available": row["busy_incident_id"] is None and row["emergency_id"] is None,
        "busy_incident_id": row["busy_incident_id"], "emergency_id": row["emergency_id"],
    }


async def on_shift(db: AsyncSession, now: datetime, allowed: list[str] | None, *, site_id: Any = None) -> list[dict]:
    """Every guard clocked in now, at the sites the caller may see, each with
    the last position they recorded this shift."""
    params: dict = {"now": now}
    where = ["s.actual_start IS NOT NULL", "s.actual_start <= :now", "(s.actual_end IS NULL OR s.actual_end >= :now)"]
    scope = site_scope_clause(allowed, "s.site_id", params)
    if scope:
        where.append(scope)
    if site_id is not None:
        where.append("s.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"""
        SELECT DISTINCT ON (s.guard_user_id)
               s.guard_user_id AS user_id, u.full_name, u.role_id, s.site_id, st.name AS site_name, s.actual_start,
               s.check_in_lat, s.check_in_lon,
               cs.latitude AS scan_lat, cs.longitude AS scan_lng, cs.scanned_at,
               h.latitude AS upd_lat, h.longitude AS upd_lng, h.changed_at,
               ob.latitude AS entry_lat, ob.longitude AS entry_lng, ob.occurred_at AS entry_at,
               md.latitude AS emergency_lat, md.longitude AS emergency_lng, md.detected_at AS emergency_at,
               md.id AS emergency_id,
               (SELECT i.id FROM incidents i WHERE i.dispatched_guard_id = s.guard_user_id
                   AND i.status NOT IN ('resolved','closed') ORDER BY i.dispatched_at DESC NULLS LAST LIMIT 1)
                   AS busy_incident_id
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id AND u.is_active
          LEFT JOIN sites st ON st.id = s.site_id
          LEFT JOIN LATERAL (SELECT latitude, longitude, scanned_at FROM checkpoint_scans
                              WHERE guard_user_id = s.guard_user_id AND latitude IS NOT NULL
                                AND scanned_at >= s.actual_start ORDER BY scanned_at DESC LIMIT 1) cs ON TRUE
          LEFT JOIN LATERAL (SELECT latitude, longitude, changed_at FROM incident_status_history
                              WHERE changed_by_user_id = s.guard_user_id AND latitude IS NOT NULL
                                AND changed_at >= s.actual_start ORDER BY changed_at DESC LIMIT 1) h ON TRUE
          LEFT JOIN LATERAL (SELECT latitude, longitude, occurred_at FROM occurrence_book_entries
                              WHERE author_user_id = s.guard_user_id AND latitude IS NOT NULL
                                AND occurred_at >= s.actual_start ORDER BY occurred_at DESC LIMIT 1) ob ON TRUE
          LEFT JOIN LATERAL (SELECT id, latitude, longitude, detected_at FROM man_down_events
                              WHERE guard_user_id = s.guard_user_id
                                AND status IN ('pending','escalated','acknowledged')
                              ORDER BY detected_at DESC LIMIT 1) md ON TRUE
         WHERE {' AND '.join(where)}
         ORDER BY s.guard_user_id, s.actual_start DESC
    """), params)).mappings().all()
    return [describe(r, now) for r in rows]


def nearest(guards: Sequence[Mapping], latitude: Any, longitude: Any) -> list[dict]:
    """The guards, nearest first by last recorded position: those free before
    those already sent somewhere, and those with no recorded position last."""
    ranked = [{**g, "distance_m": distance_m(g["latitude"], g["longitude"], latitude, longitude)} for g in guards]
    ranked.sort(key=lambda g: (not g["available"], g["distance_m"] is None, g["distance_m"] or 0,
                               str(g["full_name"] or "")))
    return ranked
