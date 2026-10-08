"""The daily briefing, as it is drafted: a day's counts put into sentences by fixed rules.

  day()       the calendar day a briefing is for, as a period where the site is. Pure.
  draft()     the board's figures for that day, and what stands out, as sections of lines. Pure.

NO MODEL WRITES A BRIEFING (owner decision E2). Each line is a fixed sentence
with a count in it, and every section carries the figures its lines were made
of. The same figures give the same lines.

A LINE SAYS WHEN IT IS TRUE OF. `PERIOD` is what fell inside the day.
`DRAFTING` is how things stood when the briefing was counted — what is open,
who is on shift, how the devices read — because the platform does not keep how
those stood at an earlier moment, and does not pretend to.

NOBODY IS NAMED. A line counts incidents, shifts, visitors and orders; it does
not say whose they were.

WHAT STANDS OUT IS NOT A FORECAST. Those lines are the advice of
services/risk_patterns.py, word for word, each with what it rests on, and the
section says they are counts of the weeks before.

Read-only: nothing here writes to the database, and nothing here reads it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from app.services import ops_board

#: The sections of a briefing, in the order they are read.
SECTIONS = (*ops_board.SECTIONS, "ADVICE")
TITLE = {**ops_board.TITLE, "ADVICE": "What stands out"}
WHEN = ("PERIOD", "DRAFTING", "WEEKS")
#: How far back a briefing may be drafted for, in days.
MAX_DAYS_BACK = 31
#: How many pieces of advice a briefing carries, and over how many weeks they are counted.
ADVICE_LINES = 3
ADVICE_WEEKS = 4
ADVICE_NOTE = ("Counted over the 4 weeks before the briefing was drafted. A pattern that recurred is not a forecast: "
               "nothing here says what will happen.")
DRAFTING_NOTE = "As things stood when this was drafted, not at the end of the day."
STATES = ("DRAFT", "PUBLISHED", "DISCARDED")


def day(on: date, zone: str, now: datetime) -> tuple[datetime, datetime, bool]:
    """(from, to, whether that is the whole day) for a calendar day where the
    site is, as moments in UTC. A day that is not over is counted up to now. A
    day on which the clocks change is as long as it is."""
    tz = ZoneInfo(zone)
    start = datetime.combine(on, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(on + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, min(end, now), end <= now


def count(n: int, one: str, many: str) -> str:
    """`1 incident was`, `3 incidents were`, `No incidents were`."""
    if n == 0:
        return f"No {many}"
    return f"{n} {one if n == 1 else many}"


def lasting(seconds: float | None) -> str:
    """A length of time in words: `under a minute`, `4 minutes`, `1 hour 20 minutes`, `2 days 3 hours`."""
    if seconds is None:
        return "an unmeasured time"
    minutes = int(round(seconds / 60))
    if seconds < 60:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hour{'' if hours == 1 else 's'}" + (f" {rest} minute{'' if rest == 1 else 's'}" if rest else "")
    days, left = divmod(hours, 24)
    return f"{days} day{'' if days == 1 else 's'}" + (f" {left} hour{'' if left == 1 else 's'}" if left else "")


def _listed(parts: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _incidents(f: Mapping) -> list[tuple[str, str]]:
    lines = []
    if not f["opened"]:
        lines.append(("No incidents were opened.", "PERIOD"))
    else:
        by = [f"{f['by_severity'][s]} {s}" for s in ops_board.SEVERITIES if f["by_severity"][s]]
        lines.append((f"{count(f['opened'], 'incident was', 'incidents were')} opened"
                      + (f": {_listed(by)}." if by else "."), "PERIOD"))
        if f["opened_still_open"]:
            lines.append((f"{f['opened_still_open']} of those opened {'is' if f['opened_still_open'] == 1 else 'are'} "
                          "still open.", "DRAFTING"))
    if f["resolved"]:
        lines.append((f"{count(f['resolved'], 'incident was', 'incidents were')} resolved.", "PERIOD"))
    lines.append((f"{count(f['open_now'], 'incident is', 'incidents are')} open in all.", "DRAFTING"))
    return lines


def _response(f: Mapping, clocks_on: bool) -> list[tuple[str, str]]:
    lines = []
    if not f["opened"]:
        lines.append(("No incidents were opened, so no response was timed.", "PERIOD"))
    elif not f["acknowledged"]:
        lines.append((f"Nothing has yet been done with any of the {f['opened']} opened.", "DRAFTING"))
    else:
        lines.append((f"Somebody acted on {f['acknowledged']} of the {f['opened']} opened; on half of them within "
                      f"{lasting(f['acknowledge_seconds'])}.", "PERIOD"))
        if f["resolved"]:
            lines.append((f"{f['resolved']} of the {f['opened']} {'was' if f['resolved'] == 1 else 'were'} resolved; "
                          f"half within {lasting(f['resolve_seconds'])}.", "PERIOD"))
    if f["sent"]:
        sent = f"{count(f['sent'], 'guard was', 'guards were')} sent; {f['arrived']} arrived"
        if f["arrived"]:
            sent += f", half within {lasting(f['arrive_seconds'])}"
        if f["declined"]:
            sent += f"; {f['declined']} declined"
        lines.append((sent + ".", "PERIOD"))
    missed = [(f["missed"][k], word) for k, word in (("acknowledge", "to acknowledge"), ("arrival", "to arrive"),
                                                    ("resolve", "to resolve")) if f["missed"][k]]
    if not clocks_on:
        lines.append(("The response clocks are switched off, so nothing was measured against them.", "PERIOD"))
    elif not missed:
        lines.append(("No response clock was missed.", "PERIOD"))
    else:
        total = sum(n for n, _ in missed)
        lines.append((f"{count(total, 'response clock was', 'response clocks were')} missed: "
                      f"{_listed([f'{n} {word}' for n, word in missed])}.", "PERIOD"))
    return lines


def _patrols(f: Mapping) -> list[tuple[str, str]]:
    lines = []
    for kind in ops_board.PATROL_KINDS:
        p = f[kind]
        if p is None:
            continue
        label, over = ops_board.PATROL_LABEL[kind], ops_board.over(p)
        if not over and not p["open"]:
            lines.append((f"{label}: none fell due.", "PERIOD"))
            continue
        if not over:
            lines.append((f"{label}: {p['open']} still to be done; none is over yet.", "DRAFTING"))
            continue
        said = f"{label}: {p['done']} done of {over} that {'is' if over == 1 else 'are'} over"
        rest = [f"{n} {word}" for n, word in ((p["partial"], "done in part"), (p["missed"], "missed"),
                                             (p["failed"], "failed")) if n]
        lines.append((said + (f"; {_listed(rest)}." if rest else "."), "PERIOD"))
        if p["open"]:
            lines.append((f"{label}: {p['open']} still to be done.", "DRAFTING"))
    return lines


def _guards(f: Mapping) -> list[tuple[str, str]]:
    lines = []
    if not f["shifts"]:
        lines.append(("No shifts were due to begin.", "PERIOD"))
    else:
        said = f"{count(f['shifts'], 'shift was', 'shifts were')} due to begin: {f['worked']} worked"
        if f["late"]:
            said += f", {f['late']} of them started late"
        if f["not_started"]:
            said += f", {f['not_started']} not started"
        lines.append((said + ".", "PERIOD"))
    now = f"{count(f['on_shift_now'], 'guard is', 'guards are')} on shift"
    if f["due_not_started_now"]:
        now += (f"; {count(f['due_not_started_now'], 'shift that is due has', 'shifts that are due have')} "
                "not been started")
    lines.append((now + ".", "DRAFTING"))
    return lines


def _devices(f: Mapping) -> list[tuple[str, str]]:
    if not f["devices"]:
        return [("No devices are known.", "DRAFTING")]
    by = f["by_state"]
    parts = [f"{by[state]} {word}" for state, word in (("DOWN", "read as down"), ("DEGRADED", "degraded"),
                                                       ("NOT_KNOWN", "with no reading"), ("OFF", "switched off"))
             if by[state]]
    said = f"Of {count(f['devices'], 'device', 'devices')}, {by['OK']} read as working"
    return [(said + (f"; {_listed(parts)}." if parts else "."), "DRAFTING")]


def _visitors(f: Mapping) -> list[tuple[str, str]]:
    said = (f"{count(f['arrived'], 'arrival', 'arrivals')} and "
            f"{count(f['departed'], 'departure', 'departures').lower()} were logged at the gate")
    if f["refused"]:
        said += f"; {count(f['refused'], 'visitor was', 'visitors were')} refused"
    lines = [(said + ".", "PERIOD")]
    now = f"{count(f['on_site_now'], 'visitor is', 'visitors are')} on site"
    if f["waiting_now"]:
        now += f"; {count(f['waiting_now'], 'visit is', 'visits are')} waiting for a decision"
    lines.append((now + ".", "DRAFTING"))
    return lines


def _maintenance(f: Mapping) -> list[tuple[str, str]]:
    done = "none was" if not f["done"] else f"{f['done']} {'was' if f['done'] == 1 else 'were'}"
    lines = [(f"{count(f['raised'], 'work order was', 'work orders were')} raised, and {done} completed.", "PERIOD")]
    in_hand = f["open_now"] + f["in_progress_now"]
    now = f"{count(in_hand, 'order is', 'orders are')} in hand"
    if f["overdue_now"]:
        now += f", {f['overdue_now']} of them overdue"
    if f["suggested_now"]:
        now += f"; {count(f['suggested_now'], 'suggestion is', 'suggestions are')} waiting for a person"
    lines.append((now + ".", "DRAFTING"))
    return lines


_WRITERS = {"INCIDENTS": _incidents, "PATROLS": _patrols, "GUARDS": _guards, "DEVICES": _devices,
            "VISITORS": _visitors, "MAINTENANCE": _maintenance}


def draft(total: Mapping, clocks_on: bool, advice: Sequence[Mapping] | None, not_read: Sequence[Mapping]) -> dict:
    """A briefing's content: a section for each part of the board that was
    read, its lines and the figures they were made of; what stands out, when
    the drafter may read advice; and what was not read, with the permission it
    wants. `advice` None is advice not read; an empty list is nothing standing
    out."""
    sections = []
    for key in ops_board.SECTIONS:
        if key not in total:
            continue
        lines = _response(total[key], clocks_on) if key == "RESPONSE" else _WRITERS[key](total[key])
        sections.append({"key": key, "title": TITLE[key], "figures": total[key],
                         "lines": [{"text": text, "as_at": when} for text, when in lines]})
    if advice is not None:
        lines = [{"text": f"{f['statement']} {f['confidence']['why']}", "as_at": "WEEKS"} for f in advice[:ADVICE_LINES]]
        if not lines:
            lines = [{"text": "Nothing stands out in the last 4 weeks by the rules that are applied.", "as_at": "WEEKS"}]
        sections.append({"key": "ADVICE", "title": TITLE["ADVICE"], "note": ADVICE_NOTE, "lines": lines,
                         "figures": {"standing": len(advice), "shown": min(len(advice), ADVICE_LINES)}})
    return {"sections": sections,
            "not_read": [{"key": n["key"], "title": n["title"], "needs": n["needs"]} for n in not_read]}


def shown(content: Mapping, left_out: Sequence[str], *, whole: bool) -> list[dict]:
    """A briefing's sections as they are given. Whoever manages briefings is
    given every section, each saying whether it is left out; a reader is given
    only what was not."""
    out = []
    for s in content.get("sections", []):
        if s["key"] in left_out and not whole:
            continue
        out.append({**s, "left_out": s["key"] in left_out})
    return out
