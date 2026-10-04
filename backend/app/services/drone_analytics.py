"""Drone patrol analytics: what the flights and their events add up to.

COUNTED FROM WHAT HAPPENED. Every figure is an aggregate of drone events and
patrol sessions already recorded — nothing here is stored, modelled or trained.
Ask twice and the same rows give the same answer.

THE RISK MAP IS AN ANALYTICAL SCORE, NOT A PREDICTION. An area's score is a rate:
its events in the period, weighted by how serious each was assessed to be, per
week. It says where the drones have been finding things, which is worth knowing
and is not the same as where something will happen. The response says so, and so
must anything that shows it.

RECOMMENDATIONS ARE SUGGESTIONS WITH THEIR EVIDENCE ATTACHED. Each one states the
observation it rests on and the numbers behind it, and is marked system-generated.
They are fixed rules over the same aggregates — repeatable, and explainable to the
person asked to act on one — not conclusions.

HOURS AND DAYS ARE THE ORGANISATION'S OWN. "Events at night" computed in UTC
would call a Singapore afternoon the small hours.

A false positive is an event an officer marked as one. It is left out of
"suspicious", out of the risk score and out of the hot spots: a place is not
risky because the AI was wrong there. It stays in the totals, and in the
false-positive rate, which is the point of tracking it.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.drone_ai_pipeline import tenant_tz
from app.services.drone_reports import MODULE_NAMES, RISK_LEVELS, SUSPICIOUS, period_bounds

#: The longest period one analysis may cover.
MAX_DAYS = 366
#: How much one event of each level adds to an area's weighted count.
WEIGHT = {"CRITICAL": 10, "HIGH": 6, "MEDIUM": 3, "LOW": 1, "INFO": 0}
#: Weighted events per week, times this, is the 0–100 score (capped).
SCORE_PER_WEEKLY_POINT = 5
#: Score at or above which an area is labelled each level.
AREA_LEVELS = (("HIGH", 60), ("MEDIUM", 25), ("LOW", 1))
#: Night, for "during night patrols": from this hour to before that one, local.
NIGHT_FROM, NIGHT_TO = 19, 7
#: Hot-spot grid: this many degrees a side — about 28 m.
CELL_DEG = 0.00025
MAX_CELLS = 200
#: Flights that count towards a mission's success rate: those that should have
#: flown to the end. A cancelled one was a person's decision, not a failure.
DUE = ("COMPLETED", "FAILED", "ABORTED", "BLOCKED", "MISSED")
NO_ZONE = "Outside any zone"

DISCLAIMER_SCORE = ("An analytical score from recorded drone events in the period — where the drones have been "
                    "finding things. It is not a prediction of where something will happen.")
DISCLAIMER_RECOMMENDATIONS = ("System-generated suggestions from recorded events and flights. Each is a prompt for "
                              "review with its evidence attached, not a conclusion.")

_SUSPICIOUS_SQL = "e.risk_level IN ('MEDIUM','HIGH','CRITICAL') AND e.status <> 'FALSE_POSITIVE'"
_WEIGHT_SQL = ("CASE WHEN e.status = 'FALSE_POSITIVE' THEN 0 WHEN e.risk_level = 'CRITICAL' THEN 10 "
               "WHEN e.risk_level = 'HIGH' THEN 6 WHEN e.risk_level = 'MEDIUM' THEN 3 "
               "WHEN e.risk_level = 'LOW' THEN 1 ELSE 0 END")
_SCOPE = """
       AND (CAST(:site AS uuid) IS NULL OR {t}.site_id = CAST(:site AS uuid))
       AND (CAST(:mission AS uuid) IS NULL OR {t}.mission_id = CAST(:mission AS uuid))
       AND (CAST(:allowed AS text[]) IS NULL OR {t}.site_id::text = ANY(CAST(:allowed AS text[])))"""
_EVENTS = "e.detected_at >= :since AND e.detected_at < :until" + _SCOPE.format(t="e")
_SESSIONS = "ps.created_at >= :since AND ps.created_at < :until" + _SCOPE.format(t="ps")


def _rate(part: int, whole: int) -> float | None:
    """A share as a fraction, or None when there is nothing to take a share of —
    which is not the same as zero."""
    return round(part / whole, 4) if whole else None


def area_score(weighted: float, days: int) -> int:
    """Weighted events per week, scaled to 0–100."""
    weekly = weighted * 7 / max(days, 1)
    return min(100, round(weekly * SCORE_PER_WEEKLY_POINT))


def area_level(score: int) -> str:
    return next((name for name, floor in AREA_LEVELS if score >= floor), "NONE")


def is_night(hour: int) -> bool:
    return hour >= NIGHT_FROM or hour < NIGHT_TO


async def _params(db: AsyncSession, start: date, end: date, site_id, mission_id, allowed) -> tuple[dict, str, int]:
    tz = await tenant_tz(db)
    since, until = period_bounds(start, end, tz)
    return ({"since": since, "until": until, "site": str(site_id) if site_id else None,
             "mission": str(mission_id) if mission_id else None, "allowed": allowed, "tz": tz.key},
            tz.key, (end - start).days + 1)


# ═════════════════════════════════════════════════════════════════════════════
# Overview: patrol statistics, trends, rates
# ═════════════════════════════════════════════════════════════════════════════

async def overview(db: AsyncSession, *, start: date, end: date, site_id=None, mission_id=None,
                   allowed_site_ids: list[str] | None = None) -> dict:
    p, tz, days = await _params(db, start, end, site_id, mission_id, allowed_site_ids)

    flights = (await db.execute(text(f"""
        SELECT ps.status, count(*) AS n,
               COALESCE(sum(EXTRACT(epoch FROM (ps.ended_at - ps.launched_at)))
                        FILTER (WHERE ps.launched_at IS NOT NULL AND ps.ended_at IS NOT NULL), 0) AS seconds,
               COALESCE(sum(ps.distance_m), 0) AS metres
          FROM drone_patrol_sessions ps WHERE {_SESSIONS} GROUP BY ps.status
    """), p)).mappings().all()
    by_status = {r["status"]: r["n"] for r in flights}
    total_flights = sum(by_status.values())
    due = sum(by_status.get(s, 0) for s in DUE)
    completed = by_status.get("COMPLETED", 0)

    reasons = (await db.execute(text(f"""
        SELECT ps.status, COALESCE(ps.blocked_reason, ps.failure_reason, ps.abort_reason) AS reason, count(*) AS n
          FROM drone_patrol_sessions ps
         WHERE {_SESSIONS} AND ps.status IN ('FAILED','ABORTED','BLOCKED','MISSED')
           AND COALESCE(ps.blocked_reason, ps.failure_reason, ps.abort_reason) IS NOT NULL
         GROUP BY 1, 2 ORDER BY n DESC, reason LIMIT 5
    """), p)).mappings().all()

    totals = (await db.execute(text(f"""
        SELECT count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               count(*) FILTER (WHERE e.status = 'FALSE_POSITIVE') AS false_positives,
               count(*) FILTER (WHERE e.incident_id IS NOT NULL) AS with_incident,
               count(DISTINCT e.incident_id) AS incidents,
               count(*) FILTER (WHERE e.verification_state = 'VERIFIED') AS verified,
               count(*) FILTER (WHERE e.status IN ('NEW','ACKNOWLEDGED','INVESTIGATING','ESCALATED')) AS open,
               count(*) FILTER (WHERE e.status = 'NEW' AND e.detected_at < now() - interval '1 day') AS unreviewed
          FROM drone_events e WHERE {_EVENTS}
    """), p)).mappings().first()
    risk_rows = (await db.execute(text(
        f"SELECT e.risk_level, count(*) AS n FROM drone_events e WHERE {_EVENTS} GROUP BY e.risk_level"),
        p)).mappings().all()
    by_risk = {level: 0 for level in RISK_LEVELS} | {r["risk_level"]: r["n"] for r in risk_rows}

    types = (await db.execute(text(f"""
        SELECT e.module_type, count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               count(*) FILTER (WHERE e.status = 'FALSE_POSITIVE') AS false_positives,
               count(*) FILTER (WHERE e.incident_id IS NOT NULL) AS with_incident
          FROM drone_events e WHERE {_EVENTS}
         GROUP BY e.module_type ORDER BY events DESC, e.module_type
    """), p)).mappings().all()

    # By mission and by drone: the flight's own frozen names, so a renamed
    # mission still counts as the mission it was when it flew.
    mission_flights = (await db.execute(text(f"""
        SELECT COALESCE(ps.mission_name, 'No mission') AS name, count(*) AS flights,
               count(*) FILTER (WHERE ps.status = 'COMPLETED') AS completed,
               count(*) FILTER (WHERE ps.status IN ('COMPLETED','FAILED','ABORTED','BLOCKED','MISSED')) AS due,
               mode() WITHIN GROUP (ORDER BY COALESCE(ps.blocked_reason, ps.failure_reason, ps.abort_reason))
                   FILTER (WHERE ps.status IN ('FAILED','ABORTED','BLOCKED','MISSED')) AS common_reason
          FROM drone_patrol_sessions ps WHERE {_SESSIONS} GROUP BY 1
    """), p)).mappings().all()
    mission_events = (await db.execute(text(f"""
        SELECT COALESCE(ps.mission_name, 'No mission') AS name, count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               count(*) FILTER (WHERE e.incident_id IS NOT NULL) AS with_incident
          FROM drone_events e LEFT JOIN drone_patrol_sessions ps ON ps.id = e.session_id
         WHERE {_EVENTS} GROUP BY 1
    """), p)).mappings().all()
    drone_flights = (await db.execute(text(f"""
        SELECT COALESCE(ps.drone_name, 'No drone') AS name, count(*) AS flights,
               count(*) FILTER (WHERE ps.status = 'COMPLETED') AS completed,
               count(*) FILTER (WHERE ps.status IN ('COMPLETED','FAILED','ABORTED','BLOCKED','MISSED')) AS due
          FROM drone_patrol_sessions ps WHERE {_SESSIONS} GROUP BY 1
    """), p)).mappings().all()
    drone_events = (await db.execute(text(f"""
        SELECT COALESCE(ps.drone_name, 'No drone') AS name, count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious
          FROM drone_events e LEFT JOIN drone_patrol_sessions ps ON ps.id = e.session_id
         WHERE {_EVENTS} GROUP BY 1
    """), p)).mappings().all()

    def merged(flight_rows, event_rows) -> list[dict]:
        out: dict[str, dict] = {}
        for r in flight_rows:
            out[r["name"]] = {"name": r["name"], "flights": r["flights"], "completed": r["completed"],
                              "success_rate": _rate(r["completed"], r["due"]), "due": r["due"],
                              "common_reason": r.get("common_reason"), "events": 0, "suspicious": 0,
                              "with_incident": 0}
        for r in event_rows:
            row = out.setdefault(r["name"], {"name": r["name"], "flights": 0, "completed": 0, "success_rate": None,
                                             "due": 0, "common_reason": None, "events": 0, "suspicious": 0,
                                             "with_incident": 0})
            row["events"], row["suspicious"] = r["events"], r["suspicious"]
            row["with_incident"] = r.get("with_incident", 0)
        return sorted(out.values(), key=lambda x: (-x["suspicious"], -x["events"], -x["flights"], x["name"]))

    hours = (await db.execute(text(f"""
        SELECT EXTRACT(hour FROM e.detected_at AT TIME ZONE :tz)::int AS hour, count(*) AS n
          FROM drone_events e WHERE {_EVENTS} AND {_SUSPICIOUS_SQL} GROUP BY 1
    """), p)).mappings().all()
    weekdays = (await db.execute(text(f"""
        SELECT EXTRACT(isodow FROM e.detected_at AT TIME ZONE :tz)::int AS dow, count(*) AS n
          FROM drone_events e WHERE {_EVENTS} AND {_SUSPICIOUS_SQL} GROUP BY 1
    """), p)).mappings().all()
    by_hour = {r["hour"]: r["n"] for r in hours}
    by_dow = {r["dow"]: r["n"] for r in weekdays}

    day_events = (await db.execute(text(f"""
        SELECT (e.detected_at AT TIME ZONE :tz)::date AS day, count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               count(*) FILTER (WHERE e.status = 'FALSE_POSITIVE') AS false_positives,
               count(DISTINCT e.incident_id) AS incidents
          FROM drone_events e WHERE {_EVENTS} GROUP BY 1
    """), p)).mappings().all()
    day_flights = (await db.execute(text(f"""
        SELECT (ps.created_at AT TIME ZONE :tz)::date AS day, count(*) AS flights,
               count(*) FILTER (WHERE ps.status = 'COMPLETED') AS completed
          FROM drone_patrol_sessions ps WHERE {_SESSIONS} GROUP BY 1
    """), p)).mappings().all()
    de = {r["day"]: r for r in day_events}
    df = {r["day"]: r for r in day_flights}
    daily = []
    for i in range(days):
        d = start + timedelta(days=i)
        e, f = de.get(d), df.get(d)
        daily.append({"day": d, "flights": f["flights"] if f else 0, "completed": f["completed"] if f else 0,
                      "events": e["events"] if e else 0, "suspicious": e["suspicious"] if e else 0,
                      "false_positives": e["false_positives"] if e else 0, "incidents": e["incidents"] if e else 0})

    events = totals["events"]
    return {
        "from": start, "to": end, "days": days, "timezone": tz,
        "flights": {
            "total": total_flights, "by_status": by_status, "completed": completed, "due": due,
            # Mission success: of the flights that should have flown to the end,
            # the share that did.
            "success_rate": _rate(completed, due),
            "flight_seconds": float(sum(r["seconds"] for r in flights)),
            "distance_m": float(sum(r["metres"] for r in flights)),
            "did_not_complete": [{"status": r["status"], "reason": r["reason"], "flights": r["n"]} for r in reasons],
        },
        "events": {
            "total": events, "suspicious": totals["suspicious"], "verified": totals["verified"],
            "open": totals["open"], "unreviewed_over_a_day": totals["unreviewed"], "by_risk": by_risk,
            "false_positives": totals["false_positives"],
            "false_positive_rate": _rate(totals["false_positives"], events),
            "incidents": totals["incidents"], "with_incident": totals["with_incident"],
            # Incident conversion: the share of events that became (or joined) an incident.
            "incident_conversion_rate": _rate(totals["with_incident"], events),
            "per_flight": round(events / total_flights, 2) if total_flights else None,
        },
        "detection_types": [{
            "module_type": r["module_type"], "name": MODULE_NAMES.get(r["module_type"], r["module_type"]),
            "events": r["events"], "suspicious": r["suspicious"], "false_positives": r["false_positives"],
            "false_positive_rate": _rate(r["false_positives"], r["events"]),
            "incident_conversion_rate": _rate(r["with_incident"], r["events"]),
        } for r in types],
        "missions": merged(mission_flights, mission_events),
        "drones": merged(drone_flights, drone_events),
        "suspicious_by_hour": [{"hour": h, "events": by_hour.get(h, 0), "night": is_night(h)} for h in range(24)],
        "suspicious_by_weekday": [{"weekday": d, "name": name, "events": by_dow.get(d, 0)}
                                  for d, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), start=1)],
        "daily": daily,
    }


# ═════════════════════════════════════════════════════════════════════════════
# The risk map
# ═════════════════════════════════════════════════════════════════════════════

async def risk_map(db: AsyncSession, *, start: date, end: date, site_id=None,
                   allowed_site_ids: list[str] | None = None) -> dict:
    """Areas ranked by an analytical score, the spots where events cluster, and
    the places intrusion keeps being seen."""
    p, tz, days = await _params(db, start, end, site_id, None, allowed_site_ids)
    p |= {"cell": CELL_DEG, "night_from": NIGHT_FROM, "night_to": NIGHT_TO, "max": MAX_CELLS}
    night = ("(EXTRACT(hour FROM e.detected_at AT TIME ZONE :tz)::int >= CAST(:night_from AS int) "
             " OR EXTRACT(hour FROM e.detected_at AT TIME ZONE :tz)::int < CAST(:night_to AS int))")

    rows = (await db.execute(text(f"""
        SELECT e.site_id, s.name AS site_name, e.zone_name, e.zone_type,
               -- The zone as it is now, if it still exists, so the map can colour it.
               (array_agg(e.security_zone_id) FILTER (WHERE e.security_zone_id IS NOT NULL))[1] AS zone_id,
               count(*) AS events,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL} AND {night}) AS night_suspicious,
               count(*) FILTER (WHERE e.status = 'FALSE_POSITIVE') AS false_positives,
               count(DISTINCT e.incident_id) AS incidents,
               count(*) FILTER (WHERE e.risk_level = 'CRITICAL' AND e.status <> 'FALSE_POSITIVE') AS critical,
               count(*) FILTER (WHERE e.risk_level = 'HIGH' AND e.status <> 'FALSE_POSITIVE') AS high,
               count(*) FILTER (WHERE e.risk_level = 'MEDIUM' AND e.status <> 'FALSE_POSITIVE') AS medium,
               count(*) FILTER (WHERE e.risk_level = 'LOW' AND e.status <> 'FALSE_POSITIVE') AS low,
               -- Suspicious events no fixed camera was found for, covering or near.
               count(*) FILTER (WHERE {_SUSPICIOUS_SQL} AND NOT EXISTS (
                   SELECT 1 FROM drone_event_cameras c WHERE c.event_id = e.id)) AS suspicious_without_cctv,
               sum({_WEIGHT_SQL}) AS weighted, max(e.detected_at) AS last_event_at
          FROM drone_events e LEFT JOIN sites s ON s.id = e.site_id
         WHERE {_EVENTS}
         GROUP BY e.site_id, s.name, e.zone_name, e.zone_type
    """), p)).mappings().all()
    areas = []
    for r in rows:
        score = area_score(float(r["weighted"] or 0), days)
        areas.append({
            "site_id": str(r["site_id"]) if r["site_id"] else None, "site_name": r["site_name"],
            "area": r["zone_name"] or NO_ZONE, "zone_type": r["zone_type"],
            "zone_id": str(r["zone_id"]) if r["zone_id"] else None,
            "score": score, "level": area_level(score), "weighted_events": float(r["weighted"] or 0),
            "events": r["events"], "suspicious": r["suspicious"], "night_suspicious": r["night_suspicious"],
            "false_positives": r["false_positives"], "incidents": r["incidents"],
            "by_risk": {"CRITICAL": r["critical"], "HIGH": r["high"], "MEDIUM": r["medium"], "LOW": r["low"]},
            "suspicious_without_cctv": r["suspicious_without_cctv"], "last_event_at": r["last_event_at"],
        })
    areas.sort(key=lambda a: (-a["score"], -a["suspicious"], -a["events"], a["area"]))

    cells = (await db.execute(text(f"""
        SELECT floor(e.drone_latitude / CAST(:cell AS double precision)) AS gy, floor(e.drone_longitude / CAST(:cell AS double precision)) AS gx,
               avg(e.drone_latitude) AS latitude, avg(e.drone_longitude) AS longitude,
               count(*) AS events, count(*) FILTER (WHERE {_SUSPICIOUS_SQL}) AS suspicious,
               sum({_WEIGHT_SQL}) AS weighted
          FROM drone_events e
         WHERE {_EVENTS} AND e.drone_latitude IS NOT NULL AND e.drone_longitude IS NOT NULL
           AND e.status <> 'FALSE_POSITIVE'
         GROUP BY 1, 2 ORDER BY weighted DESC, events DESC LIMIT CAST(:max AS int)
    """), p)).mappings().all()

    repeated = (await db.execute(text(f"""
        SELECT avg(e.drone_latitude) AS latitude, avg(e.drone_longitude) AS longitude,
               mode() WITHIN GROUP (ORDER BY e.zone_name) AS zone_name, max(s.name) AS site_name,
               count(*) AS events, count(DISTINCT (e.detected_at AT TIME ZONE :tz)::date) AS days,
               count(*) FILTER (WHERE {night}) AS at_night,
               min(e.detected_at) AS first_seen_at, max(e.detected_at) AS last_seen_at
          FROM drone_events e LEFT JOIN sites s ON s.id = e.site_id
         WHERE {_EVENTS} AND e.module_type = 'intrusion' AND e.status <> 'FALSE_POSITIVE'
           AND e.drone_latitude IS NOT NULL AND e.drone_longitude IS NOT NULL
         GROUP BY e.site_id, floor(e.drone_latitude / CAST(:cell AS double precision)), floor(e.drone_longitude / CAST(:cell AS double precision))
        HAVING count(*) >= 2
         ORDER BY events DESC, last_seen_at DESC LIMIT 25
    """), p)).mappings().all()

    return {
        "from": start, "to": end, "days": days, "timezone": tz,
        "disclaimer": DISCLAIMER_SCORE,
        "method": {
            "description": ("Each event that was not marked a false positive adds its weight; the total per week, "
                            f"times {SCORE_PER_WEEKLY_POINT}, is the score, capped at 100."),
            "weights": WEIGHT, "levels": {name: floor for name, floor in AREA_LEVELS},
            "cell_metres": round(CELL_DEG * 111_320),
        },
        "areas": areas,
        "hot_spots": [{"latitude": c["latitude"], "longitude": c["longitude"], "events": c["events"],
                       "suspicious": c["suspicious"], "weighted_events": float(c["weighted"] or 0)} for c in cells],
        "repeated_intrusion_locations": [dict(r) for r in repeated],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Recommendations
# ═════════════════════════════════════════════════════════════════════════════

#: The thresholds, in one place, so a suggestion can be traced to its rule.
RULES = {
    "night_suspicious_in_area": 3,      # suspicious night events in one area
    "repeated_intrusion_events": 3,     # intrusion at one spot, on…
    "repeated_intrusion_days": 2,       # …at least this many days
    "false_positive_events": 5,         # events of one type before its rate means anything
    "false_positive_rate": 0.40,
    "mission_flights": 5,               # flights of one mission before its rate means anything
    "mission_success_rate": 0.80,
    "suspicious_without_cctv": 3,
}


def recommend(over: dict, risk: dict) -> list[dict]:
    """Suggestions from the aggregates above. Pure: the same numbers always give
    the same list, most pressing first."""
    out: list[dict] = []

    def add(priority: int, code: str, subject: str, observation: str, suggestion: str, basis: dict) -> None:
        out.append({"code": code, "subject": subject, "observation": observation, "suggestion": suggestion,
                    "basis": basis, "system_generated": True, "_priority": priority})

    for a in risk["areas"]:
        where = a["area"] if a["area"] != NO_ZONE else "The area outside any security zone"
        at = f" at {a['site_name']}" if a["site_name"] else ""
        if a["night_suspicious"] >= RULES["night_suspicious_in_area"]:
            add(1, "NIGHT_ACTIVITY_IN_AREA", a["area"],
                f"{where}{at} generated {a['night_suspicious']} suspicious events during night patrols.",
                "Review perimeter CCTV coverage there, or increase the approved patrol frequency.",
                {"area": a["area"], "site_name": a["site_name"], "night_suspicious": a["night_suspicious"],
                 "suspicious": a["suspicious"], "score": a["score"], "threshold": RULES["night_suspicious_in_area"]})
        if a["suspicious_without_cctv"] >= RULES["suspicious_without_cctv"]:
            add(3, "NO_CCTV_FOR_AREA", a["area"],
                f"No fixed camera covers or is near {where[0].lower() + where[1:]}{at}, where "
                f"{a['suspicious_without_cctv']} suspicious events were seen by drone alone.",
                "Consider fixed camera coverage there, so a sighting can be corroborated and replayed.",
                {"area": a["area"], "site_name": a["site_name"],
                 "suspicious_without_cctv": a["suspicious_without_cctv"], "threshold": RULES["suspicious_without_cctv"]})

    for r in risk["repeated_intrusion_locations"]:
        if r["events"] >= RULES["repeated_intrusion_events"] and r["days"] >= RULES["repeated_intrusion_days"]:
            near = f" near {r['zone_name']}" if r["zone_name"] else ""
            add(2, "REPEATED_INTRUSION_AT_SPOT", r["zone_name"] or "A repeated location",
                f"Intrusion was detected {r['events']} times on {r['days']} days at the same spot{near}"
                f"{' at ' + r['site_name'] if r['site_name'] else ''}.",
                "Inspect that spot for a way in, and consider a fixed camera, lighting or a barrier.",
                {"latitude": r["latitude"], "longitude": r["longitude"], "events": r["events"], "days": r["days"],
                 "at_night": r["at_night"], "threshold_events": RULES["repeated_intrusion_events"],
                 "threshold_days": RULES["repeated_intrusion_days"]})

    for t in over["detection_types"]:
        rate = t["false_positive_rate"]
        if t["events"] >= RULES["false_positive_events"] and rate is not None and rate >= RULES["false_positive_rate"]:
            add(4, "HIGH_FALSE_POSITIVE_RATE", t["name"],
                f"{t['false_positives']} of {t['events']} {t['name'].lower()} events were marked false positives.",
                f"Review the security profile's confidence threshold for {t['name'].lower()}, and the zones "
                "it is applied in.",
                {"module_type": t["module_type"], "events": t["events"], "false_positives": t["false_positives"],
                 "false_positive_rate": rate, "threshold": RULES["false_positive_rate"]})

    for m in over["missions"]:
        rate = m["success_rate"]
        if m["due"] >= RULES["mission_flights"] and rate is not None and rate < RULES["mission_success_rate"]:
            why = f" The most common reason: {m['common_reason']}" if m["common_reason"] else ""
            add(2, "MISSION_OFTEN_NOT_COMPLETED", m["name"],
                f"{m['name']} completed {m['completed']} of {m['due']} flights.{why}",
                "Check the drone's readiness and battery ahead of its schedule, or adjust the schedule.",
                {"mission": m["name"], "completed": m["completed"], "flights": m["due"], "success_rate": rate,
                 "common_reason": m["common_reason"], "threshold": RULES["mission_success_rate"]})

    waiting = over["events"]["unreviewed_over_a_day"]
    if waiting:
        add(5, "EVENTS_NOT_REVIEWED", "Unreviewed events",
            f"{waiting} event{'s have' if waiting != 1 else ' has'} had no officer action for more than a day.",
            "Review them in Drone Events: acknowledge, resolve or mark each a false positive.",
            {"unreviewed_over_a_day": waiting})

    out.sort(key=lambda r: (r["_priority"], r["subject"]))
    for r in out:
        del r["_priority"]
    return out


async def recommendations(db: AsyncSession, *, start: date, end: date, site_id=None,
                          allowed_site_ids: list[str] | None = None) -> dict:
    over = await overview(db, start=start, end=end, site_id=site_id, allowed_site_ids=allowed_site_ids)
    risk = await risk_map(db, start=start, end=end, site_id=site_id, allowed_site_ids=allowed_site_ids)
    return {"from": start, "to": end, "days": over["days"], "timezone": over["timezone"],
            "disclaimer": DISCLAIMER_RECOMMENDATIONS, "rules": RULES,
            "recommendations": recommend(over, risk)}
