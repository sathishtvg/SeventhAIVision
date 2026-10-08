"""Where recorded events gather — by hour, by day, by place, by week — and the advice made of that.

  events()      what was recorded of one kind in a period: when, at which site, where
  summarise()   the same, counted: a week of hours, the weeks of the period, the places. Pure.
  advise()      what stands out, by fixed rules, each with what it rests on and how
                much history that is. Pure.
  confidence()  how much history a statement rests on. Pure.

FIVE KINDS OF THING THAT WENT WRONG are counted, each from rows the platform
already keeps: incidents; doors refused, forced or tampered with; patrols missed
or failed (virtual, drone, and a guard's tour); devices going down; response
clocks missed. Situations are the intelligence layer's and are counted there
(services/intel_insight.py); this module does not count them again.

A PATTERN THAT RECURRED IS NOT A FORECAST. Every statement here is a count over
a stated period, with the count under it. Nothing says what will happen, and
`is_forecast` is false on everything this module returns.

CONFIDENCE IS HOW MUCH HISTORY A STATEMENT RESTS ON: how many records, over how
many weeks, and in how many of those weeks it held. It is not a probability,
and the answer says so wherever a confidence is given.

A WEEK COUNTS ONLY WHEN THE PATTERN HOLDS WITHIN THAT WEEK BY ITSELF. One busy
afternoon can put half a month's records into one band of hours; that is one
week in which it held, not four.

NOTHING BEFORE IS NOT THE SAME AS A RISE. When the earlier half of a period has
no records at all, the counts cannot tell a change from the start of recording
— a door that was only just connected, a clock that was only just switched on —
and the statement says exactly that, at LOW confidence.

NOTHING IS STORED AND NOTHING IS LEARNED. Fixed rules over counts, counted when
asked. No model.

NOBODY IS NAMED. A place is a camera, a door, a patrol or a device. No person
is counted, ranked or described.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SOURCES = ("INCIDENT", "ACCESS", "PATROL", "DEVICE", "SLA")
SOURCE_LABEL = {"INCIDENT": "Incidents", "ACCESS": "Doors refused, forced or tampered with",
                "PATROL": "Patrols missed or failed", "DEVICE": "Devices going down", "SLA": "Response clocks missed"}
#: One of them, and several, as a sentence names them.
NOUN = {"INCIDENT": ("incident", "incidents"), "ACCESS": ("door event", "door events"),
        "PATROL": ("missed patrol", "missed patrols"), "DEVICE": ("device outage", "device outages"),
        "SLA": ("missed clock", "missed clocks")}
#: What each kind is counted from, for the screen that shows the counts.
COUNTED_FROM = {
    "INCIDENT": "Every incident, at the site of its camera, when it was opened.",
    "ACCESS": "Door events recorded as denied, forced or tamper, at the door's site.",
    "PATROL": "Virtual patrols missed or failed, drone patrols missed, failed or blocked, and guard tours missed, "
              "each at the time it was due.",
    "DEVICE": "Each time a camera's stream disconnected, and each time another device was read as down.",
    "SLA": "Each response clock recorded as missed, once the organisation has switched the clocks on.",
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_WEEKS = 4
MAX_WEEKS = 12
#: A rule needs at least this many records to speak.
FLOOR = 5
#: The width of the band of hours a rule looks for.
BAND_HOURS = 4
#: A band of hours speaks at this share of the records; a day or a place at the next.
HOURS_SHARE, DAY_SHARE, PLACE_SHARE = 50, 40, 40
#: A device counts as repeatedly down at this many outages in the period.
REPEATED_AT = 3
#: The most records of one kind one answer counts. Past it the answer says it was cut.
MAX_ROWS = 20000
LEVELS = ("HIGH", "MEDIUM", "LOW")
NOTE = ("Counts of what was recorded in the period, and where they gather. A pattern that recurred is not a "
        "forecast: nothing here says what will happen.")
CONFIDENCE_NOTE = ("Confidence says how much history a statement rests on: how many records, over how many weeks, "
                   "and in how many of those weeks the same held by itself. It is not a probability.")

#: Each kind as rows of (at, site_id, place_key, place). The outer statement keeps to the period and the sites.
_ROWS = {
    "INCIDENT": """
        SELECT i.created_at AS at, c.site_id, c.id::text AS place_key, c.name AS place
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id""",
    "ACCESS": """
        SELECT e.occurred_at AS at, d.site_id, d.id::text AS place_key, d.name AS place
          FROM access_events e JOIN access_doors d ON d.id = e.door_id
         WHERE e.event_type IN ('denied', 'forced', 'tamper')""",
    "PATROL": """
        SELECT v.scheduled_for AS at, v.site_id, 'virtual:' || COALESCE(v.schedule_id::text, v.schedule_name) AS place_key,
               COALESCE(v.schedule_name, 'Virtual patrol') AS place
          FROM virtual_patrol_sessions v WHERE v.status IN ('MISSED', 'FAILED')
        UNION ALL
        SELECT COALESCE(p.scheduled_for, p.created_at), p.site_id, 'drone:' || COALESCE(p.mission_id::text, p.mission_name),
               COALESCE(p.mission_name, 'Drone patrol')
          FROM drone_patrol_sessions p WHERE p.status IN ('MISSED', 'FAILED', 'BLOCKED')
        UNION ALL
        SELECT o.scheduled_at, r.site_id, 'tour:' || t.id::text, t.name
          FROM tour_occurrences o JOIN tour_schedules t ON t.id = o.schedule_id
          LEFT JOIN patrol_routes r ON r.id = t.route_id
         WHERE o.status = 'missed'""",
    "DEVICE": """
        SELECT h.occurred_at AS at, c.site_id, 'CAMERA:' || c.id::text AS place_key, c.name AS place
          FROM camera_health_events h JOIN cameras c ON c.id = h.camera_id
         WHERE h.event_type = 'stream_disconnected'
        UNION ALL
        SELECT x.observed_at, x.site_id, x.device_kind || ':' || x.device_id::text,
               COALESCE(s.name, dr.name, g.name, pn.name, n.name, initcap(replace(x.device_kind, '_', ' ')))
          FROM device_health_changes x
          LEFT JOIN iot_sensors s ON x.device_kind = 'SENSOR' AND s.id = x.device_id
          LEFT JOIN drones dr ON x.device_kind = 'DRONE' AND dr.id = x.device_id
          LEFT JOIN drone_edge_gateways g ON x.device_kind = 'EDGE_GATEWAY' AND g.id = x.device_id
          LEFT JOIN alarm_panels pn ON x.device_kind = 'ALARM_PANEL' AND pn.id = x.device_id
          LEFT JOIN nvr_connections n ON x.device_kind = 'NVR' AND n.id = x.device_id
         WHERE x.state = 'DOWN' AND x.device_kind <> 'CAMERA'""",
    "SLA": """
        SELECT e.created_at AS at, e.site_id, e.clock AS place_key, initcap(e.clock) || ' clock' AS place
          FROM incident_escalations e WHERE e.kind = 'SLA_BREACH'""",
}


async def events(db: AsyncSession, source: str, since: datetime, now: datetime,
                 site_ids: Sequence[Any] | None) -> tuple[list[dict], bool]:
    """What was recorded of one kind in the period, oldest first: when, at
    which site, and where. With whether it was cut at `MAX_ROWS`. `site_ids`
    None is every site, and what has no site; a list is those sites only."""
    params: dict = {"since": since, "now": now, "cap": MAX_ROWS + 1}
    scope = "TRUE"
    if site_ids is not None:
        scope = "x.site_id = ANY(CAST(:sites AS uuid[]))"
        params["sites"] = [str(s) for s in site_ids]
    rows = (await db.execute(text(f"""
        SELECT x.at, x.site_id, x.place_key, x.place FROM ({_ROWS[source]}) x
         WHERE x.at >= :since AND x.at <= :now AND {scope}
         ORDER BY x.at LIMIT :cap
    """), params)).mappings().all()
    return [dict(r) for r in rows[:MAX_ROWS]], len(rows) > MAX_ROWS


# ─── Pure: counting ──────────────────────────────────────────────────────────

def summarise(rows: Iterable[Mapping], zone: str, since: datetime, weeks: int) -> dict:
    """One kind's records, counted: by weekday and hour where the organisation
    is, by week of the period, and by place."""
    tz = ZoneInfo(zone)
    grid = [[0] * 24 for _ in range(7)]
    by_week = [0] * weeks
    in_week: list[dict] = [{"total": 0, "cells": Counter(), "days": Counter(), "places": Counter()}
                           for _ in range(weeks)]
    places: Counter = Counter()
    names: dict[str, str] = {}
    total = 0
    for r in rows:
        week = min(weeks - 1, max(0, (r["at"] - since).days // 7))
        local = r["at"].astimezone(tz)
        grid[local.weekday()][local.hour] += 1
        by_week[week] += 1
        in_week[week]["total"] += 1
        in_week[week]["cells"][local.hour] += 1
        in_week[week]["days"][local.weekday()] += 1
        total += 1
        if r.get("place_key"):
            places[r["place_key"]] += 1
            names[r["place_key"]] = r.get("place") or names.get(r["place_key"]) or "Not named"
            in_week[week]["places"][r["place_key"]] += 1
    return {"total": total, "grid": grid, "by_week": by_week, "_in_week": in_week,
            "places": [{"key": key, "name": names[key], "count": n, "share": _pct(n, total)}
                       for key, n in sorted(places.items(), key=lambda kv: (-kv[1], names[kv[0]]))]}


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def busiest_band(by_hour: Sequence[int], width: int = BAND_HOURS) -> tuple[int, int]:
    """(the hour a band of `width` hours starts at, how many fell in it) for
    the band holding the most, wrapping past midnight. The earliest on a tie."""
    best = max(range(24), key=lambda h: (sum(by_hour[(h + k) % 24] for k in range(width)), -h))
    return best, sum(by_hour[(best + k) % 24] for k in range(width))


def confidence(records: int, weeks: int, held: int | None = None, at_most: str = "HIGH",
               held_as: str = "the same held within {held} of those weeks") -> dict:
    """How much history a statement rests on. HIGH needs thirty records over
    four weeks or more, held in three weeks of every four; MEDIUM needs ten
    over two weeks or more, held in half of them. A statement that compares two
    halves of a period has no weeks to hold in and is never HIGH; `at_most`
    holds a statement down that the counts cannot carry further."""
    share = None if held is None else held / weeks
    if records >= 30 and weeks >= 4 and share is not None and share >= 0.75:
        level = "HIGH"
    elif records >= 10 and weeks >= 2 and (share is None or share >= 0.5):
        level = "MEDIUM"
    else:
        level = "LOW"
    level = max(level, at_most, key=LEVELS.index)
    why = f"Rests on {records} record{'' if records == 1 else 's'} over {weeks} week{'' if weeks == 1 else 's'}"
    if held is not None:
        why += "; " + held_as.format(held=held)
    return {"level": level, "why": why + ".", "records": records, "weeks": weeks, "held_in_weeks": held}


def _held(in_week: Sequence[Mapping], count, share: int) -> int:
    """In how many weeks a pattern holds within that week by itself: at least
    `share` percent of the week's own records. A week with none does not hold."""
    return sum(1 for w in in_week if w["total"] and _pct(count(w), w["total"]) >= share)


def advise(source: str, s: Mapping, weeks: int, scope: str) -> list[dict]:
    """What stands out in one kind's records, by fixed rules. Each says what it
    rests on, how much history that is, and something a person might consider;
    each is advisory and none is a forecast. `scope` is what the advice is
    about — a site's id — and is part of what each piece is known by."""
    out: list[dict] = []
    one, many = NOUN[source]
    total, period = s["total"], f"the last {weeks} week{'' if weeks == 1 else 's'}"
    in_week = s["_in_week"]

    def add(code: str, subject: str, statement: str, consider: str, sure: Mapping, **rests_on) -> None:
        out.append({"key": f"{code}:{source}:{scope}:{subject}", "code": code, "source": source,
                    "source_label": SOURCE_LABEL[source], "statement": statement, "consider": consider,
                    "rests_on": {"weeks": weeks, **rests_on}, "confidence": dict(sure),
                    "is_advisory": True, "is_forecast": False})

    if total >= FLOOR:
        by_hour = [sum(day[h] for day in s["grid"]) for h in range(24)]
        start, n = busiest_band(by_hour)
        if _pct(n, total) >= HOURS_SHARE:
            band = [(start + k) % 24 for k in range(BAND_HOURS)]
            held = _held(in_week, lambda w: sum(w["cells"][h] for h in band), HOURS_SHARE)
            add("RECURRING_HOURS", f"{start:02d}",
                f"{_pct(n, total)}% of the {many} of {period} fell between {start:02d}:00 and "
                f"{(start + BAND_HOURS) % 24:02d}:00 ({n} of {total}).",
                "Whether those hours have the people, patrols and attention the rest of the day has.",
                confidence(total, weeks, held), from_hour=start, to_hour=(start + BAND_HOURS) % 24, in_band=n, of=total)
        by_day = [sum(day) for day in s["grid"]]
        day = max(range(7), key=lambda d: (by_day[d], -d))
        if weeks >= 2 and _pct(by_day[day], total) >= DAY_SHARE:
            held = _held(in_week, lambda w: w["days"][day], DAY_SHARE)
            add("RECURRING_DAY", WEEKDAYS[day].upper(),
                f"{_pct(by_day[day], total)}% of the {many} of {period} fell on a {WEEKDAYS[day]} "
                f"({by_day[day]} of {total}).",
                f"What is different about a {WEEKDAYS[day]} at this site: who is on, what is delivered, what is closed.",
                confidence(total, weeks, held), weekday=WEEKDAYS[day], on_day=by_day[day], of=total)
        top = s["places"][0] if s["places"] else None
        if top and top["share"] >= PLACE_SHARE and len(s["places"]) > 1:
            held = _held(in_week, lambda w: w["places"][top["key"]], PLACE_SHARE)
            add("RECURRING_PLACE", top["key"],
                f"{top['name']} accounts for {top['share']}% of the {many} of {period} ({top['count']} of {total}).",
                "What is at that place, and whether what watches it or what is done there needs to change.",
                confidence(total, weeks, held), place=top["name"], at_place=top["count"], of=total)
    half = weeks // 2
    if half:
        before, after = sum(s["by_week"][weeks - 2 * half:weeks - half]), sum(s["by_week"][weeks - half:])
        span = f"{half} week{'' if half == 1 else 's'}"
        if after >= FLOOR and before and after >= 2 * before:
            add("RISING", "RECENT",
                f"{after} {many} in the last {span}, against {before} in the {span} before.",
                "What changed between the two: a new tenant, a works programme, a camera, a rule, a roster.",
                confidence(before + after, 2 * half), recent=after, before=before, each_weeks=half)
        elif after >= FLOOR and not before:
            add("FIRST_RECORDED", "RECENT",
                f"{after} {many} in the last {span}; none were recorded in the {span} before. Whether that is a "
                "change, or only when recording began, cannot be told from the counts.",
                "Whether anything was being recorded before: when the device, the door or the clock was switched on.",
                confidence(after, 2 * half, at_most="LOW"), recent=after, before=0, each_weeks=half)
    if source == "DEVICE":
        for place in [p for p in s["places"] if p["count"] >= REPEATED_AT][:3]:
            held = sum(1 for w in in_week if w["places"][place["key"]])
            add("REPEATED_DEVICE", place["key"],
                f"{place['name']} went down {place['count']} times in {period}.",
                "A work order to have it looked at. What is recorded of it is in the asset register.",
                confidence(place["count"], weeks, held, held_as="it went down in {held} of those weeks"),
                device=place["name"], outages=place["count"])
    return out


def shown(s: Mapping) -> dict:
    """A summary as it is given: without what was kept only to judge the weeks."""
    return {k: v for k, v in s.items() if not k.startswith("_")}


def ranked(findings: Iterable[Mapping]) -> list[dict]:
    """What rests on the most history first; then what rests on the most records."""
    return sorted((dict(f) for f in findings),
                  key=lambda f: (LEVELS.index(f["confidence"]["level"]), -f["confidence"]["records"], f["key"]))


async def read(db: AsyncSession, zone: str, since: datetime, now: datetime, weeks: int,
               site_ids: Sequence[Any] | None, scope: str) -> dict:
    """Every kind, counted for the period, with the advice made of it."""
    sources, findings = [], []
    for source in SOURCES:
        rows, cut = await events(db, source, since, now, site_ids)
        s = summarise(rows, zone, since, weeks)
        sources.append({"source": source, "label": SOURCE_LABEL[source], "counted_from": COUNTED_FROM[source],
                        "cut_at": MAX_ROWS if cut else None, **shown(s), "places": s["places"][:8]})
        findings += advise(source, s, weeks, scope)
    return {"sources": sources, "findings": ranked(findings)}


def period(now: datetime, weeks: int) -> datetime:
    """When a period of whole weeks ending now began."""
    return now - timedelta(weeks=weeks)
