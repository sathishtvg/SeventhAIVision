"""The operations board: what each part of the operation counts, for a period and a set of sites.

  read()      every section the caller may read, for each site and for the sites together
  add()       two sets of figures, summed — for a customer's sites. Pure.
  share()     so many of so many, as a percentage or as nothing. Pure.

SEVEN SECTIONS, each from rows the platform already keeps: incidents; the
response to them; patrols (a guard's tour, virtual, drone); guards on shift;
devices; visitors; maintenance. Nothing is stored: the board is counted when it
is asked for.

EACH SECTION IS READ UNDER ITS OWN EXISTING PERMISSION. Holding `board:read`
shows the board; it does not show the incidents of somebody who may not read
incidents. A section the caller may not read is left out and named, with why.

A FIGURE IS A COUNT, AND SAYS WHAT IT COUNTS. `..._now` is how things stand at
the moment of asking; everything else is what fell inside the period. A time is
the middle one of those measured (a median) with how many it was measured from
beside it, and is None when there were none — not zero.

NOTHING IS SCORED. There is no index, grade or rating of a site, a customer or
a person, and nobody is named: every figure is a number of records.

A SITE'S FIGURES ARE WHAT IS RECORDED AT IT. An incident belongs to the site of
its camera; one with no camera has no site and is counted only for somebody
who is not held to particular sites.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import device_health, response_sla

SECTIONS = ("INCIDENTS", "RESPONSE", "PATROLS", "GUARDS", "DEVICES", "VISITORS", "MAINTENANCE")
TITLE = {"INCIDENTS": "Incidents", "RESPONSE": "Response", "PATROLS": "Patrols", "GUARDS": "Guards on shift",
         "DEVICES": "Devices", "VISITORS": "Visitors", "MAINTENANCE": "Maintenance"}
#: The permission each section is read under. It is the one its own screen asks for.
NEEDS = {"INCIDENTS": "incident:read", "RESPONSE": "response:read", "PATROLS": "patrol:read", "GUARDS": "shift:read",
         "DEVICES": "asset:read", "VISITORS": "visitor:read", "MAINTENANCE": "maintenance:read"}
#: Parts of a section that have a permission of their own.
PART_NEEDS = {"virtual": "vpatrol:read", "drone": "drone:read", "waiting_now": "visitorauth:read"}
PATROL_KINDS = ("tours", "virtual", "drone")
PATROL_LABEL = {"tours": "Guard tours", "virtual": "Virtual patrols", "drone": "Drone patrols"}
SEVERITIES = ("critical", "high", "medium", "low")
PERIOD_DAYS = (1, 7, 30)
#: What each section is counted from, for the screen that shows it.
COUNTED_FROM = {
    "INCIDENTS": "Incidents by when they were opened and resolved, at the site of their camera.",
    "RESPONSE": "Of the incidents opened in the period: the time until somebody first did something with each, and "
                "until it was resolved. Of the guards sent in the period: the time until each arrived. And each "
                "response clock recorded as missed.",
    "PATROLS": "Patrols that fell due in the period, by how each ended.",
    "GUARDS": "Shifts that began in the period, and the shifts being worked now.",
    "DEVICES": "Every device, as it is read now from what it reports.",
    "VISITORS": "Arrivals, departures and refusals logged at the gate in the period, and who is on site now.",
    "MAINTENANCE": "Work orders raised and completed in the period, and those waiting now.",
}
NOTE = ("Counts of what is recorded, for the period and the sites shown. Figures marked “now” are as things stand at "
        "the moment of asking. Nothing here is a score or a forecast.")

_PATROL_ZERO = {"scheduled": 0, "done": 0, "partial": 0, "missed": 0, "failed": 0, "open": 0, "cancelled": 0}

#: The first thing anybody did with an incident — as the response clocks reckon it (services/response_sla.py).
ACKNOWLEDGED_AT = """LEAST(i.dispatched_at,
                 (SELECT min(h.changed_at) FROM incident_status_history h WHERE h.incident_id = i.id),
                 (SELECT min(r.dispatched_at) FROM incident_responses r WHERE r.incident_id = i.id),
                 i.resolved_at)"""

_IN = "x.at >= :start AND x.at < :end"

#: One statement for each part: rows as `x`, then what is counted of them.
_PARTS: dict[str, tuple[str, str]] = {
    "INCIDENTS": ("""
        SELECT c.site_id, i.created_at AS at, i.resolved_at, i.severity, i.status NOT IN ('resolved', 'closed') AS is_open
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id
         WHERE i.created_at >= :start OR i.resolved_at >= :start OR i.status NOT IN ('resolved', 'closed')""", f"""
        count(*) FILTER (WHERE {_IN}) AS opened,
        count(*) FILTER (WHERE {_IN} AND x.severity = 'critical') AS critical,
        count(*) FILTER (WHERE {_IN} AND x.severity = 'high') AS high,
        count(*) FILTER (WHERE {_IN} AND x.severity = 'medium') AS medium,
        count(*) FILTER (WHERE {_IN} AND x.severity = 'low') AS low,
        count(*) FILTER (WHERE x.resolved_at >= :start AND x.resolved_at < :end) AS resolved,
        count(*) FILTER (WHERE {_IN} AND x.is_open) AS opened_still_open,
        count(*) FILTER (WHERE x.is_open) AS open_now"""),
    "RESPONSE": (f"""
        SELECT c.site_id, i.created_at AS at, i.resolved_at, {ACKNOWLEDGED_AT} AS acknowledged_at
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id
         WHERE i.created_at >= :start AND i.created_at < :end""", """
        count(*) AS opened,
        count(x.acknowledged_at) AS acknowledged,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY CAST(EXTRACT(EPOCH FROM x.acknowledged_at - x.at) AS double precision)) AS acknowledge_seconds,
        count(x.resolved_at) AS resolved,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY CAST(EXTRACT(EPOCH FROM x.resolved_at - x.at) AS double precision)) AS resolve_seconds"""),
    "SENT": ("""
        SELECT r.site_id, r.dispatched_at AS at, r.arrived_at, r.state FROM incident_responses r
         WHERE r.dispatched_at >= :start AND r.dispatched_at < :end""", """
        count(*) AS sent,
        count(x.arrived_at) AS arrived,
        count(*) FILTER (WHERE x.state = 'DECLINED') AS declined,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY CAST(EXTRACT(EPOCH FROM x.arrived_at - x.at) AS double precision)) AS arrive_seconds"""),
    "MISSED": ("""
        SELECT e.site_id, e.clock FROM incident_escalations e
         WHERE e.kind = 'SLA_BREACH' AND e.created_at >= :start AND e.created_at < :end""", """
        count(*) FILTER (WHERE x.clock = 'ACKNOWLEDGE') AS acknowledge,
        count(*) FILTER (WHERE x.clock = 'ARRIVAL') AS arrival,
        count(*) FILTER (WHERE x.clock = 'RESOLVE') AS resolve"""),
    "tours": ("""
        SELECT r.site_id, o.status FROM tour_occurrences o
          JOIN tour_schedules t ON t.id = o.schedule_id LEFT JOIN patrol_routes r ON r.id = t.route_id
         WHERE o.scheduled_at >= :start AND o.scheduled_at < :end""", """
        count(*) AS scheduled,
        count(*) FILTER (WHERE x.status = 'completed') AS done,
        0 AS partial,
        count(*) FILTER (WHERE x.status = 'missed') AS missed,
        0 AS failed,
        count(*) FILTER (WHERE x.status IN ('pending', 'in_progress')) AS open,
        0 AS cancelled"""),
    "virtual": ("""
        SELECT v.site_id, v.status FROM virtual_patrol_sessions v
         WHERE v.scheduled_for >= :start AND v.scheduled_for < :end""", """
        count(*) FILTER (WHERE x.status <> 'CANCELLED') AS scheduled,
        count(*) FILTER (WHERE x.status = 'COMPLETED') AS done,
        count(*) FILTER (WHERE x.status = 'PARTIALLY_COMPLETED') AS partial,
        count(*) FILTER (WHERE x.status = 'MISSED') AS missed,
        count(*) FILTER (WHERE x.status = 'FAILED') AS failed,
        count(*) FILTER (WHERE x.status IN ('SCHEDULED', 'STARTED', 'IN_PROGRESS')) AS open,
        count(*) FILTER (WHERE x.status = 'CANCELLED') AS cancelled"""),
    "drone": ("""
        SELECT p.site_id, p.status FROM drone_patrol_sessions p
         WHERE COALESCE(p.scheduled_for, p.created_at) >= :start AND COALESCE(p.scheduled_for, p.created_at) < :end""", """
        count(*) FILTER (WHERE x.status <> 'CANCELLED') AS scheduled,
        count(*) FILTER (WHERE x.status = 'COMPLETED') AS done,
        0 AS partial,
        count(*) FILTER (WHERE x.status = 'MISSED') AS missed,
        count(*) FILTER (WHERE x.status IN ('FAILED', 'BLOCKED', 'ABORTED')) AS failed,
        count(*) FILTER (WHERE x.status NOT IN ('COMPLETED', 'MISSED', 'FAILED', 'BLOCKED', 'ABORTED', 'CANCELLED')) AS open,
        count(*) FILTER (WHERE x.status = 'CANCELLED') AS cancelled"""),
    "GUARDS": ("""
        SELECT s.site_id, s.status, s.scheduled_start AS at, s.scheduled_end, s.actual_start,
               COALESCE(s.is_late, FALSE) AS is_late
          FROM shifts s WHERE s.status = 'active' OR s.scheduled_end > :start""", f"""
        count(*) FILTER (WHERE x.status = 'active') AS on_shift_now,
        count(*) FILTER (WHERE x.status = 'scheduled' AND x.at <= :now AND x.scheduled_end > :now) AS due_not_started_now,
        count(*) FILTER (WHERE {_IN}) AS shifts,
        count(*) FILTER (WHERE {_IN} AND x.actual_start IS NOT NULL) AS worked,
        count(*) FILTER (WHERE {_IN} AND x.is_late) AS late,
        count(*) FILTER (WHERE {_IN} AND x.actual_start IS NULL AND x.status = 'scheduled'
                           AND x.scheduled_end <= :now) AS not_started"""),
    "VISITORS": ("""
        SELECT l.site_id, l.event_type FROM visitor_logs l
         WHERE l.occurred_at >= :start AND l.occurred_at < :end""", """
        count(*) FILTER (WHERE x.event_type = 'arrival') AS arrived,
        count(*) FILTER (WHERE x.event_type = 'departure') AS departed,
        count(*) FILTER (WHERE x.event_type = 'denied') AS refused"""),
    "ON_SITE": ("""
        SELECT v.site_id FROM visitors v
         WHERE v.is_active AND v.departed_at IS NULL AND v.status <> 'departed'
           AND (v.status = 'arrived' OR v.arrived_at IS NOT NULL)""", """
        count(*) AS on_site_now"""),
    "waiting_now": ("""
        SELECT a.site_id FROM visitor_authorizations a WHERE a.state = 'REQUESTED'""", """
        count(*) AS waiting_now"""),
    "MAINTENANCE": ("""
        SELECT w.site_id, w.state, w.due_at, w.completed_at, COALESCE(w.accepted_at, w.raised_at) AS at
          FROM maintenance_work_orders w
         WHERE w.state IN ('SUGGESTED', 'OPEN', 'IN_PROGRESS') OR w.raised_at >= :start OR w.accepted_at >= :start
            OR w.completed_at >= :start""", f"""
        count(*) FILTER (WHERE x.state = 'SUGGESTED') AS suggested_now,
        count(*) FILTER (WHERE x.state = 'OPEN') AS open_now,
        count(*) FILTER (WHERE x.state = 'IN_PROGRESS') AS in_progress_now,
        count(*) FILTER (WHERE x.state IN ('OPEN', 'IN_PROGRESS') AND x.due_at < :now) AS overdue_now,
        count(*) FILTER (WHERE x.state NOT IN ('SUGGESTED', 'DISMISSED') AND {_IN}) AS raised,
        count(*) FILTER (WHERE x.completed_at >= :start AND x.completed_at < :end) AS done"""),
}


def blank(section: str, held: frozenset[str] | set[str]) -> dict:
    """A section with nothing counted in it: every count 0, every time None, and
    None for a part the caller may not read."""
    if section == "INCIDENTS":
        return {"opened": 0, "by_severity": dict.fromkeys(SEVERITIES, 0), "resolved": 0, "opened_still_open": 0,
                "open_now": 0}
    if section == "RESPONSE":
        return {"opened": 0, "acknowledged": 0, "acknowledge_seconds": None, "resolved": 0, "resolve_seconds": None,
                "sent": 0, "arrived": 0, "declined": 0, "arrive_seconds": None,
                "missed": {"acknowledge": 0, "arrival": 0, "resolve": 0}}
    if section == "PATROLS":
        return {kind: dict(_PATROL_ZERO) if kind == "tours" or PART_NEEDS[kind] in held else None
                for kind in PATROL_KINDS}
    if section == "GUARDS":
        return {"on_shift_now": 0, "due_not_started_now": 0, "shifts": 0, "worked": 0, "late": 0, "not_started": 0}
    if section == "DEVICES":
        return {"devices": 0, "by_state": dict.fromkeys(device_health.STATES, 0)}
    if section == "VISITORS":
        return {"on_site_now": 0, "arrived": 0, "departed": 0, "refused": 0,
                "waiting_now": 0 if PART_NEEDS["waiting_now"] in held else None}
    return {"suggested_now": 0, "open_now": 0, "in_progress_now": 0, "overdue_now": 0, "raised": 0, "done": 0}


# ─── Pure ────────────────────────────────────────────────────────────────────

def add(a: Any, b: Any, key: str = "") -> Any:
    """Two sets of figures, summed. A count is added; a part nobody may read
    stays unread; a time is not something that can be added, and is None."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return {k: add(a[k], b.get(k), k) for k in a}
    if key.endswith("_seconds") or a is None or b is None:
        return None
    return a + b


def share(part: int | None, whole: int | None) -> int | None:
    """So many of so many, as a whole percentage — or None when there were none to be a share of."""
    if not whole or part is None:
        return None
    return round(100 * part / whole)


def over(patrols: Mapping) -> int:
    """How many patrols of one kind are over, one way or another: the ones a share can be taken of."""
    return patrols["done"] + patrols["partial"] + patrols["missed"] + patrols["failed"]


def not_read(held: frozenset[str] | set[str]) -> list[dict]:
    """Every section and part the caller may not read, with the permission it wants."""
    out = [{"key": s, "title": TITLE[s], "needs": NEEDS[s],
            "reason": f"Read under {NEEDS[s]}, which you do not hold."} for s in SECTIONS if NEEDS[s] not in held]
    if NEEDS["PATROLS"] in held:
        out += [{"key": kind, "title": PATROL_LABEL[kind], "needs": PART_NEEDS[kind],
                 "reason": f"Read under {PART_NEEDS[kind]}, which you do not hold."}
                for kind in PATROL_KINDS[1:] if PART_NEEDS[kind] not in held]
    if NEEDS["VISITORS"] in held and PART_NEEDS["waiting_now"] not in held:
        out.append({"key": "waiting_now", "title": "Visits waiting for a decision", "needs": PART_NEEDS["waiting_now"],
                    "reason": f"Read under {PART_NEEDS['waiting_now']}, which you do not hold."})
    return out


# ─── Reading ─────────────────────────────────────────────────────────────────

async def _counted(db: AsyncSession, part: str, params: Mapping, scope: str) -> dict[Any, dict]:
    """One part, counted for each site and for the sites together. The key is
    a site's id, None for what has no site, and "*" for the total."""
    rows, counts = _PARTS[part]
    wanted = {k: v for k, v in params.items() if f":{k}" in rows + counts + scope}
    result = await db.execute(text(f"""
        SELECT x.site_id, GROUPING(x.site_id) AS is_total, {counts}
          FROM ({rows}) x WHERE {scope}
         GROUP BY GROUPING SETS ((x.site_id), ())
    """), wanted)
    out: dict[Any, dict] = {}
    for r in result.mappings():
        figures = {k: (float(v) if k.endswith("_seconds") and v is not None else v)
                   for k, v in r.items() if k not in ("site_id", "is_total")}
        out["*" if r["is_total"] else (str(r["site_id"]) if r["site_id"] else None)] = figures
    return out


async def read(db: AsyncSession, held: frozenset[str] | set[str], site_ids: Sequence[Any] | None,
               start: datetime, end: datetime, now: datetime) -> dict:
    """Every section the caller may read, for the period: `total` for the sites
    together and `sites` for each site that has anything. `site_ids` None is
    every site and what has no site; a list is those sites only. `now` is the
    moment the `..._now` figures are as at."""
    params: dict = {"start": start, "end": end, "now": now}
    scope = "TRUE"
    if site_ids is not None:
        scope = "x.site_id = ANY(CAST(:sites AS uuid[]))"
        params["sites"] = [str(s) for s in site_ids]
    sections = [s for s in SECTIONS if NEEDS[s] in held]
    board: dict[Any, dict] = {"*": {s: blank(s, held) for s in sections}}

    def at(key: Any) -> dict:
        return board.setdefault(key, {s: blank(s, held) for s in sections})

    async def fill(part: str, into) -> None:
        for key, figures in (await _counted(db, part, params, scope)).items():
            into(at(key), figures)

    if "INCIDENTS" in sections:
        await fill("INCIDENTS", lambda b, f: b["INCIDENTS"].update(
            {**{k: v for k, v in f.items() if k not in SEVERITIES}, "by_severity": {k: f[k] for k in SEVERITIES}}))
    if "RESPONSE" in sections:
        await fill("RESPONSE", lambda b, f: b["RESPONSE"].update(f))
        await fill("SENT", lambda b, f: b["RESPONSE"].update(f))
        await fill("MISSED", lambda b, f: b["RESPONSE"].update({"missed": f}))
    if "PATROLS" in sections:
        for kind in PATROL_KINDS:
            if kind == "tours" or PART_NEEDS[kind] in held:
                await fill(kind, lambda b, f, kind=kind: b["PATROLS"].update({kind: f}))
    if "GUARDS" in sections:
        await fill("GUARDS", lambda b, f: b["GUARDS"].update(f))
    if "VISITORS" in sections:
        await fill("VISITORS", lambda b, f: b["VISITORS"].update(f))
        await fill("ON_SITE", lambda b, f: b["VISITORS"].update(f))
        if PART_NEEDS["waiting_now"] in held:
            await fill("waiting_now", lambda b, f: b["VISITORS"].update(f))
    if "MAINTENANCE" in sections:
        await fill("MAINTENANCE", lambda b, f: b["MAINTENANCE"].update(f))
    if "DEVICES" in sections:
        items = await device_health.readings(db, now, allowed=None if site_ids is None else [str(s) for s in site_ids])
        by_site: dict[Any, Counter] = {}
        for item in items:
            key = str(item["site_id"]) if item.get("site_id") else None
            by_site.setdefault(key, Counter())[item["state"]] += 1
            by_site.setdefault("*", Counter())[item["state"]] += 1
        for key, states in by_site.items():
            at(key)["DEVICES"] = {"devices": sum(states.values()),
                                  "by_state": {s: states.get(s, 0) for s in device_health.STATES}}
    total = board.pop("*")
    return {"total": total, "sites": board, "not_read": not_read(held),
            "clocks_on_since": await response_sla.enabled_since(db) if "RESPONSE" in sections else None}
