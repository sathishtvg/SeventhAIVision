"""A shift's summary: what was recorded during it, counted, and written out in fixed sentences.

  gather()   reads what the platform recorded for one shift — the occurrence
             book, incidents, alerts, patrols, dispatches, visitors, what the
             site has in hand, the instructions in force, what is to be followed
             up — and returns the counts and the few things worth quoting
  write()    turns those into a text. Pure: the same facts give the same words

NO LANGUAGE MODEL (owner decision E2). Every sentence is a count, or a
quotation of something a person wrote, cut to length and never reworded. The
summary can leave something out — it cannot invent anything. What it found
nothing of it says it found nothing of, so that a short summary is not read as
a quiet shift when it was an unrecorded one.

A PERSON CONFIRMS IT. What this module writes is a draft. The guard reads it,
corrects it and confirms it, and only a confirmed summary is handed over
(routers/occurrence_book.py). What the platform drafted is kept beside what the
person made of it.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.handover import gather_site_position

METHOD = "TEMPLATE"
#: Entries quoted in the summary whatever their severity.
NOTABLE_TYPES = ("incident", "unusual_activity", "sos", "alarm_activation")
NOTABLE_SEVERITIES = ("high", "critical")
#: The most of any one thing that is listed. More are counted and said to be more.
MAX_LISTED = 10
#: A quotation is cut here, with an ellipsis. It is never reworded.
QUOTE_CHARS = 200
SEVERITIES = ("critical", "high", "medium", "low", "info")
OPEN_INCIDENT = "i.status NOT IN ('resolved','closed')"
#: The second line of every summary: where it came from. True before and after it is confirmed.
PROVENANCE = ("Drafted by the platform from what was recorded during the shift. It counts and quotes; it does not "
              "interpret.")
#: What the person writing it is told, while it is still a draft.
NOTE = PROVENANCE + " Check it and correct it before you confirm it."


def quote(words: str | None) -> str:
    """Somebody's words on one line, cut to length. Never reworded."""
    flat = " ".join((words or "").split())
    return flat if len(flat) <= QUOTE_CHARS else flat[:QUOTE_CHARS - 1].rstrip() + "…"


def label(entry_type: str) -> str:
    return entry_type.replace("_", " ")


async def gather(db: AsyncSession, shift: Mapping, now: datetime) -> dict:
    """What was recorded for this shift, up to `now` or to when it ended. With
    a site, everything is that site's; a shift with no site is the guard's own
    entries and the organisation's totals, as the existing handover counts them."""
    start = shift["actual_start"] or shift["scheduled_start"]
    end = shift["actual_end"] or now
    site, guard = shift["site_id"], shift["guard_user_id"]
    window = {"a": start, "b": end}
    at_site = {"site": site} if site else {}
    camera_site = "AND c.site_id = :site" if site else ""

    theirs = "e.site_id = :site" if site else "e.author_user_id = :guard"
    entries = (await db.execute(text(f"""
        SELECT e.occurred_at, e.entry_type, e.severity, e.body, u.full_name AS author_name
          FROM occurrence_book_entries e LEFT JOIN users u ON u.id = e.author_user_id
         WHERE e.shift_id = :shift OR (e.occurred_at >= :a AND e.occurred_at <= :b AND {theirs})
         ORDER BY e.occurred_at
    """), {"shift": shift["id"], **window, **({"site": site} if site else {"guard": guard})})).mappings().all()
    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e["entry_type"]] = by_type.get(e["entry_type"], 0) + 1
    notable = [e for e in entries if e["entry_type"] in NOTABLE_TYPES or e["severity"] in NOTABLE_SEVERITIES]

    incidents = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE i.created_at >= :a AND i.created_at <= :b) AS opened,
               count(*) FILTER (WHERE i.resolved_at >= :a AND i.resolved_at <= :b) AS resolved,
               count(*) FILTER (WHERE {OPEN_INCIDENT}) AS open_now
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id
         WHERE TRUE {camera_site}
    """), {**window, **at_site})).mappings().one()
    still_open = (await db.execute(text(f"""
        SELECT i.title, i.severity, i.status
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id
         WHERE {OPEN_INCIDENT} {camera_site}
         ORDER BY CASE i.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3
                                  WHEN 'low' THEN 4 ELSE 5 END, i.created_at DESC
         LIMIT {MAX_LISTED}
    """), at_site)).mappings().all()

    alert_site = "AND COALESCE(a.site_id, c.site_id) = :site" if site else ""
    alerts = (await db.execute(text(f"""
        SELECT a.severity, count(*) AS n, count(*) FILTER (WHERE a.status = 'open') AS open_now
          FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id
         WHERE a.created_at >= :a AND a.created_at <= :b {alert_site}
         GROUP BY a.severity
    """), {**window, **at_site})).mappings().all()

    patrols = (await db.execute(text("""
        SELECT count(*) AS sessions, count(*) FILTER (WHERE status = 'completed') AS completed,
               COALESCE(sum(scanned_checkpoints), 0) AS scanned, COALESCE(sum(total_checkpoints), 0) AS total
          FROM patrol_sessions WHERE shift_id = :shift
    """), {"shift": shift["id"]})).mappings().one()

    responses = (await db.execute(text("""
        SELECT count(*) AS sent, count(*) FILTER (WHERE arrived_at IS NOT NULL) AS arrived,
               count(*) FILTER (WHERE state = 'DECLINED') AS declined
          FROM incident_responses
         WHERE guard_user_id = :guard AND dispatched_at >= :a AND dispatched_at <= :b
    """), {"guard": guard, **window})).mappings().one()

    visitors = {"arrived": 0, "left": 0, "turned_away": 0}
    if site:
        for r in await db.execute(text("""
            SELECT event_type, count(*) AS n FROM visitor_logs
             WHERE site_id = :site AND occurred_at >= :a AND occurred_at <= :b GROUP BY event_type
        """), {"site": site, **window}):
            key = {"arrival": "arrived", "check_in": "arrived", "departure": "left", "check_out": "left",
                   "denied": "turned_away"}.get(r.event_type)
            if key:
                visitors[key] += r.n

    instructions, follow_ups = [], []
    if site:
        instructions = (await db.execute(text(f"""
            SELECT n.body, n.issued_at, n.expires_at, u.full_name AS issued_by_name
              FROM site_instructions n LEFT JOIN users u ON u.id = n.issued_by_user_id
             WHERE n.site_id = :site AND n.closed_at IS NULL AND (n.expires_at IS NULL OR n.expires_at > :now)
             ORDER BY n.issued_at LIMIT {MAX_LISTED}
        """), {"site": site, "now": end})).mappings().all()
        follow_ups = (await db.execute(text(f"""
            SELECT e.occurred_at, e.entry_type, e.body, r.note
              FROM occurrence_book_entries e
              JOIN LATERAL (SELECT outcome, note FROM occurrence_entry_reviews
                             WHERE entry_id = e.id ORDER BY reviewed_at DESC, id DESC LIMIT 1) r ON TRUE
             WHERE e.site_id = :site AND r.outcome = 'FOLLOW_UP'
             ORDER BY e.occurred_at DESC LIMIT {MAX_LISTED}
        """), {"site": site})).mappings().all()

    def when(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "shift": {"id": str(shift["id"]), "guard_name": shift.get("guard_name"),
                  "site_id": str(site) if site else None, "site_name": shift.get("site_name"),
                  "start": when(start), "end": when(end), "ended": shift["actual_end"] is not None,
                  "timezone": shift.get("timezone") or "UTC"},
        "entries": {"total": len(entries), "by_type": by_type, "of_note_total": len(notable),
                    "of_note": [{"at": when(e["occurred_at"]), "type": e["entry_type"], "severity": e["severity"],
                                 "body": quote(e["body"]), "author_name": e["author_name"]}
                                for e in notable[:MAX_LISTED]]},
        "incidents": {"opened": incidents["opened"], "resolved": incidents["resolved"],
                      "open_now": incidents["open_now"],
                      "still_open": [{"title": quote(i["title"]), "severity": i["severity"], "status": i["status"]}
                                     for i in still_open]},
        "alerts": {"raised": sum(a["n"] for a in alerts), "open_now": sum(a["open_now"] for a in alerts),
                   "by_severity": {a["severity"]: a["n"] for a in alerts}},
        "patrols": {"sessions": patrols["sessions"], "completed": patrols["completed"],
                    "checkpoints_scanned": int(patrols["scanned"]), "checkpoints_total": int(patrols["total"])},
        "dispatches": {"sent": responses["sent"], "arrived": responses["arrived"], "declined": responses["declined"]},
        "visitors": visitors if site else None,
        "site": await gather_site_position(db, site),
        "instructions": [{"body": quote(n["body"]), "issued_by_name": n["issued_by_name"],
                          "issued_at": when(n["issued_at"]), "expires_at": when(n["expires_at"])}
                         for n in instructions],
        "follow_ups": [{"at": when(f["occurred_at"]), "type": f["entry_type"], "body": quote(f["body"]),
                        "note": quote(f["note"])} for f in follow_ups],
    }


# ─── The words ───────────────────────────────────────────────────────────────

def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def write(facts: Mapping[str, Any]) -> str:
    """The summary of a shift, from its facts. Fixed sentences: nothing here
    weighs, explains or guesses."""
    shift = facts["shift"]
    zone = ZoneInfo(shift.get("timezone") or "UTC")

    def local(iso: str) -> datetime:
        return datetime.fromisoformat(iso).astimezone(zone)

    def clock(iso: str) -> str:
        return local(iso).strftime("%H:%M")

    def day(iso: str) -> str:
        at = local(iso)
        return f"{at.day} {at.strftime('%b')}"

    start, end = local(shift["start"]), local(shift["end"])
    until = end.strftime("%H:%M") if end.date() == start.date() else f"{end.day} {end.strftime('%b')} {end.strftime('%H:%M')}"
    lines = [
        f"Shift summary: {shift.get('site_name') or 'No site'}, {start.day} {start.strftime('%b %Y')}, "
        f"{start.strftime('%H:%M')} to {until}{'' if shift.get('ended') else ' (shift still running)'}"
        f" — {shift.get('guard_name') or 'a guard'}",
        PROVENANCE,
    ]

    entries = facts["entries"]
    lines += ["", "OCCURRENCE BOOK"]
    if not entries["total"]:
        lines.append("No entries were written.")
    else:
        counted = sorted(entries["by_type"].items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append(f"{_n(entries['total'], 'entry', 'entries')}: "
                     + ", ".join(f"{label(kind)} {n}" for kind, n in counted) + ".")
        for e in entries["of_note"]:
            grave = f" ({e['severity']})" if e.get("severity") else ""
            lines.append(f"- {clock(e['at'])} {label(e['type']).capitalize()}{grave}: {e['body']}")
        more = entries["of_note_total"] - len(entries["of_note"])
        if more > 0:
            lines.append(f"…and {more} more of note, in the book.")

    incidents = facts["incidents"]
    lines += ["", "INCIDENTS"]
    lines.append(f"{incidents['opened']} opened and {incidents['resolved']} resolved during the shift. "
                 + ("None open at the site." if not incidents["open_now"]
                    else f"{incidents['open_now']} open at the site now:"))
    for i in incidents["still_open"]:
        lines.append(f"- {i['title']} ({i['severity']}, {i['status'].replace('_', ' ')})")
    more = incidents["open_now"] - len(incidents["still_open"])
    if more > 0:
        lines.append(f"…and {more} more.")

    alerts = facts["alerts"]
    lines += ["", "ALERTS"]
    if not alerts["raised"]:
        lines.append("None raised.")
    else:
        grades = ", ".join(f"{alerts['by_severity'][s]} {s}" for s in SEVERITIES if alerts["by_severity"].get(s))
        lines.append(f"{alerts['raised']} raised: {grades}. "
                     + ("All dealt with." if not alerts["open_now"] else f"{alerts['open_now']} of them still open."))

    patrols = facts["patrols"]
    lines += ["", "PATROLS"]
    if not patrols["sessions"]:
        lines.append("No patrol was started on this shift.")
    else:
        lines.append(f"{patrols['completed']} of {_n(patrols['sessions'], 'patrol')} completed; "
                     f"{patrols['checkpoints_scanned']} of {_n(patrols['checkpoints_total'], 'checkpoint')} scanned.")

    sent = facts["dispatches"]
    lines += ["", "DISPATCHES"]
    if not sent["sent"]:
        lines.append("Not sent to any incident.")
    else:
        lines.append(f"Sent to {_n(sent['sent'], 'incident')}; arrived at {sent['arrived']}"
                     + (f"; could not attend {sent['declined']}." if sent["declined"] else "."))

    visitors = facts.get("visitors")
    if visitors is not None:
        lines += ["", "VISITORS"]
        if not any(visitors.values()):
            lines.append("None recorded.")
        else:
            lines.append(f"{visitors['arrived']} arrived, {visitors['left']} left"
                         + (f", {visitors['turned_away']} turned away." if visitors["turned_away"] else "."))

    site = facts["site"]
    lines += ["", "IN HAND AT THE SITE",
              f"{_n(site['keys_outstanding'], 'key')} out"
              + (f" ({site['keys_overdue']} overdue)" if site["keys_overdue"] else "") + ". "
              f"{_n(site['lost_found_held'], 'item')} of lost property held. "
              f"{_n(site['equipment_out_count'], 'item')} of kit signed out. "
              f"{_n(site['open_defects_count'], 'defect')} open."]

    lines += ["", "INSTRUCTIONS IN FORCE"]
    if not facts["instructions"]:
        lines.append("None.")
    for n in facts["instructions"]:
        whose = ", ".join(x for x in (n.get("issued_by_name"), day(n["issued_at"])) if x)
        until = f"; until {day(n['expires_at'])}" if n.get("expires_at") else ""
        lines.append(f"- {n['body']} ({whose}{until})")

    if facts["follow_ups"]:
        lines += ["", "TO BE FOLLOWED UP"]
        for f in facts["follow_ups"]:
            lines.append(f"- {day(f['at'])} {clock(f['at'])} {label(f['type']).capitalize()}: {f['body']} "
                         f"— to follow up: {f['note']}")
    return "\n".join(lines)
