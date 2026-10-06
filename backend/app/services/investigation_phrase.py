"""Smart investigation: a typed phrase, read by fixed rules into a search.

    "vehicles at the north gate last night between 1 and 3:30"
        kinds       PLATE_READ
        cameras     North Gate
        period      01:00 to 03:30 today

THIS DOES NOT UNDERSTAND LANGUAGE. There is no language model in the platform
and none is called here (owner decision E2). The phrase is matched against a
fixed vocabulary — ways of saying a period, the kinds of record, kinds of
event, severities, number plates, and the names of this organisation's own
sites and cameras — and turned into exactly the search a person could have
built by hand from the filters.

WHAT IT MADE OF THE WORDS IS ALWAYS SHOWN, AND WHAT IT DID NOT USE IS LISTED.
`understood` says which words became which filter, `assumed` says what was
filled in because nothing was said (the last 24 hours), and `not_understood`
lists every word that was not used. A word is never dropped silently unless it
is in `NOISE` — words that ask ("show", "find") or join ("the", "at") and
narrow nothing.

A PHRASE WITH NOTHING IN IT THAT WAS UNDERSTOOD IS REFUSED, NOT GUESSED AT. See
`parse`.

Pure: no database, no clock of its own. The caller passes the time and the
organisation's site and camera names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from app.services.investigation_sources import DEFAULT_HOURS, MAX_DAYS, normalise_plate

MAX_PHRASE = 300

#: Words that ask or join and narrow nothing. Dropped without comment.
NOISE = frozenset("""
    a about all an and any anything are around as at be been by can could did do does during every everything
    find for from get give had happen happened has have i in into is it its list look me of on or over please
    record records search see seen show showing some tell that the their them there these they thing things this
    those to up us was we were what when where which who with within you activity activities event events
    camera cameras cam site sites near
""".split())

#: Ways of naming a kind of record. Longest first when matched.
KIND_WORDS: dict[str, tuple[str, ...]] = {
    "ALERT": ("alert", "alerts"),
    "INCIDENT": ("incident", "incidents"),
    "PLATE_READ": ("vehicle", "vehicles", "car", "cars", "truck", "trucks", "lorry", "lorries", "van", "vans",
                   "motorcycle", "motorcycles", "number plate", "number plates", "plate read", "plate reads",
                   "plates", "lpr"),
    "FACE_MATCH": ("watchlist match", "watchlist matches", "face match", "face matches", "watchlist", "faces",
                   "face"),
    "DETECTION": ("detection", "detections"),
    "ACCESS": ("access event", "access events", "door event", "door events", "access", "door", "doors", "badge",
               "badges", "swipe", "swipes"),
    "VISITOR": ("visitor", "visitors", "guest", "guests"),
    "OCCURRENCE": ("occurrence book", "occurrence", "occurrences", "logbook", "log book", "dob"),
    "DRONE": ("drone", "drones"),
    "ALARM": ("alarm", "alarms", "alarm panel", "panel"),
    "PATROL_SCAN": ("patrol", "patrols", "checkpoint", "checkpoints", "scan", "scans"),
    "MAN_DOWN": ("man down", "guard emergency", "guard emergencies", "sos", "panic"),
    "SITUATION": ("situation", "situations"),
    "SENSOR": ("sensor", "sensors", "iot"),
}

#: Ways of naming a kind of event, as the sources themselves record it.
EVENT_WORDS: dict[str, tuple[str, ...]] = {
    "intrusion": ("intrusion", "intrusions", "trespass", "trespassing", "breach"),
    "fire_smoke": ("fire", "smoke"),
    "weapon": ("weapon", "weapons", "gun", "knife"),
    "ppe": ("ppe", "helmet", "helmets", "hard hat"),
    "crowd": ("crowd", "crowds", "crowding"),
    "abandoned": ("abandoned", "unattended", "left object", "left bag"),
    "fall": ("fall", "fallen", "fell"),
    "behavior": ("loitering", "fight", "fighting", "behaviour", "behavior"),
    "tampering": ("tamper", "tampering", "tampered"),
    "denied": ("denied", "refused"),
    "forced": ("forced", "forced open"),
    "held_open": ("held open", "propped"),
    "check_in": ("check in", "checked in", "check-in"),
    "check_out": ("check out", "checked out", "check-out"),
}

SEVERITY_WORDS: dict[str, tuple[str, ...]] = {
    "critical": ("critical",),
    "high": ("high",),
    "medium": ("medium", "moderate"),
    "low": ("low", "minor"),
}

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_UNITS = {"minute": 60, "minutes": 60, "min": 60, "mins": 60, "hour": 3600, "hours": 3600, "hr": 3600,
          "hrs": 3600, "h": 3600, "day": 86400, "days": 86400, "d": 86400, "week": 604800, "weeks": 604800}

_CLOCK = r"(midnight|noon|midday|\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)?)"
#: A plate written on its own: letters, then digits, with at least three digits
#: or a letter after them, so that "cam12" and "gate2" are not taken for one.
_PLATE = re.compile(r"^(?:[a-z]{1,3}\d{3,4}[a-z]{0,2}|[a-z]{1,3}\d{1,4}[a-z]{1,2}|[a-z]{2}\d{1,2}[a-z]{1,3}\d{3,4})$")


class NotUnderstood(ValueError):
    """Nothing in the phrase was understood. Carries the words that were not."""

    def __init__(self, words: list[str]):
        super().__init__("Nothing in that was understood.")
        self.words = words


@dataclass
class Place:
    id: str
    name: str


@dataclass
class Parsed:
    since: datetime
    until: datetime
    kinds: list[str] = field(default_factory=list)
    site_ids: list[str] = field(default_factory=list)
    camera_ids: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    plate: str | None = None
    person: str | None = None
    text: str | None = None
    understood: list[dict] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    not_understood: list[str] = field(default_factory=list)


class _Work:
    """The phrase, with what has been used blanked out so that it is not used twice."""

    def __init__(self, phrase: str):
        self.original = phrase
        self.lower = phrase.lower()

    def take(self, pattern: str) -> re.Match | None:
        match = re.search(pattern, self.lower)
        if match:
            self.blank(match.start(), match.end())
        return match

    def blank(self, start: int, end: int) -> None:
        self.lower = self.lower[:start] + " " * (end - start) + self.lower[end:]

    def said(self, match: re.Match) -> str:
        return " ".join(self.original[match.start():match.end()].split())


def _words(alternatives: Sequence[str]) -> str:
    longest_first = sorted(alternatives, key=len, reverse=True)
    return r"(?<![\w-])(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in longest_first) + r")(?![\w-])"


def _clock(said: str) -> time:
    said = said.strip()
    if said == "midnight":
        return time(0, 0)
    if said in ("noon", "midday"):
        return time(12, 0)
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", said)
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError(f"'{said}' is not a time of day.")
    return time(hour, minute)


def _hhmm(at: datetime) -> str:
    return at.strftime("%H:%M")


def _day(at: datetime, today: datetime) -> str:
    days = (today.date() - at.date()).days
    return "today" if days == 0 else "yesterday" if days == 1 else at.strftime("%d %b %Y").lstrip("0")


def _date(day: int, month: int, year: int | None, now: datetime) -> datetime:
    """Midnight on that date. With no year: the most recent such date that is not in the future."""
    try:
        if year is not None:
            return now.replace(year=year if year > 99 else 2000 + year, month=month, day=day, hour=0, minute=0,
                               second=0, microsecond=0)
        candidate = now.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        return candidate if candidate <= now else candidate.replace(year=now.year - 1)
    except ValueError:
        raise ValueError("That is not a date.") from None


def _period(work: _Work, now: datetime, out: Parsed) -> None:
    """Everything that says when. `now` is in the organisation's own time zone."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor: tuple[datetime, datetime] | None = None
    said: list[str] = []

    def at(day: datetime, hour: int) -> datetime:
        return day + timedelta(hours=hour)

    # Rolling periods: "last 3 hours", "past 2 days", "last hour".
    rolling = work.take(r"(?<!\w)(?:in\s+the\s+)?(?:last|past|previous)\s+(\d{1,3})\s*("
                        + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")(?!\w)")
    if rolling:
        anchor = (now - timedelta(seconds=int(rolling.group(1)) * _UNITS[rolling.group(2)]), now)
        said.append(work.said(rolling))
    if anchor is None:
        single = work.take(r"(?<!\w)(?:in\s+the\s+)?(?:last|past)\s+(hour|24\s*hours?)(?!\w)")
        if single:
            anchor = (now - timedelta(hours=1 if single.group(1) == "hour" else 24), now)
            said.append(work.said(single))
    if anchor is None:
        past = work.take(r"(?<!\w)(?:in\s+the\s+)?past\s+(day|week|month)(?!\w)")
        if past:
            anchor = (now - timedelta(days={"day": 1, "week": 7, "month": 30}[past.group(1)]), now)
            said.append(work.said(past))

    # Named periods.
    if anchor is None:
        monday = midnight - timedelta(days=midnight.weekday())
        first = midnight.replace(day=1)
        named = (
            (r"last\s+night|overnight", lambda: (at(midnight, -6), at(midnight, 6))),
            (r"tonight", lambda: ((at(midnight, 18), at(midnight, 30)) if now >= at(midnight, 6)
                                  else (at(midnight, -6), at(midnight, 6)))),
            (r"this\s+morning", lambda: (at(midnight, 6), at(midnight, 12))),
            (r"this\s+afternoon", lambda: (at(midnight, 12), at(midnight, 18))),
            (r"this\s+evening", lambda: (at(midnight, 18), at(midnight, 24))),
            (r"yesterday", lambda: (midnight - timedelta(days=1), midnight)),
            (r"today", lambda: (midnight, midnight + timedelta(days=1))),
            (r"this\s+week", lambda: (monday, monday + timedelta(days=7))),
            (r"last\s+week", lambda: (monday - timedelta(days=7), monday)),
            (r"this\s+month", lambda: (first, now)),
            (r"last\s+month", lambda: ((first - timedelta(days=1)).replace(day=1), first)),
        )
        for pattern, period in named:
            match = work.take(rf"(?<!\w)(?:{pattern})(?!\w)")
            if match:
                anchor = period()
                said.append(work.said(match))
                break

    # Dates: one is a day, two are from the first to the end of the second.
    if anchor is None:
        dates: list[datetime] = []
        month = "(" + "|".join(_MONTHS) + r")[a-z]*"
        for pattern, read in (
            (r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)",
             lambda m: _date(int(m.group(3)), int(m.group(2)), int(m.group(1)), now)),
            (r"(?<![\d:.])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d:])",
             lambda m: _date(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None, now)),
            (rf"(?<!\w)(\d{{1,2}})(?:st|nd|rd|th)?\s+{month}(?:\s+(\d{{4}}))?(?!\w)",
             lambda m: _date(int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)) if m.group(3) else None, now)),
            (rf"(?<!\w){month}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?(?!\w)",
             lambda m: _date(int(m.group(2)), _MONTHS[m.group(1)], int(m.group(3)) if m.group(3) else None, now)),
        ):
            while len(dates) < 2:
                match = work.take(pattern)
                if not match:
                    break
                dates.append(read(match))
                said.append(work.said(match))
        if dates:
            dates.sort()
            anchor = (dates[0], dates[-1] + timedelta(days=1))

    # A day of the week: the most recent one, today included unless "last" is said.
    if anchor is None:
        weekday = work.take(r"(?<!\w)(last\s+)?(" + "|".join(_WEEKDAYS) + r")(?!\w)")
        if weekday:
            back = (midnight.weekday() - _WEEKDAYS.index(weekday.group(2))) % 7
            if weekday.group(1) and back == 0:
                back = 7
            anchor = (midnight - timedelta(days=back), midnight - timedelta(days=back - 1))
            said.append(work.said(weekday))

    # Times of day, inside the day already named or the most recent one.
    def fit(clock: time, after: datetime | None = None) -> datetime:
        """The first `clock` at or after `after` — or, with no `after`, at or
        after the start of the day already named; with no day named, the most
        recent one."""
        base = after if after is not None else (anchor[0] if anchor else midnight)
        moment = base.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        while moment < base:
            moment += timedelta(days=1)
        if anchor is None and after is None and moment > now:
            moment -= timedelta(days=1)
        return moment

    between = work.take(rf"(?<!\w)(?:between|from)\s+{_CLOCK}\s*(?:and|to|-|until|till)\s*{_CLOCK}(?!\w)")
    if between:
        start = fit(_clock(between.group(1)))
        end = fit(_clock(between.group(2)), after=start + timedelta(minutes=1))
        anchor = (start, end)
        said.append(work.said(between))
    else:
        after_ = work.take(rf"(?<!\w)(?:after|since|from)\s+{_CLOCK}(?!\w)")
        before = work.take(rf"(?<!\w)(?:before|until|till|by)\s+{_CLOCK}(?!\w)")
        around = None if (after_ or before) else work.take(rf"(?<!\w)(?:at|around)\s+{_CLOCK}(?!\w)")
        if after_:
            start = fit(_clock(after_.group(1)))
            anchor = (start, anchor[1] if anchor and anchor[1] > start else now)
            said.append(work.said(after_))
        if before:
            end = fit(_clock(before.group(1)), after=anchor[0] + timedelta(minutes=1) if after_ else None)
            floor = anchor[0] if anchor else end.replace(hour=0, minute=0)
            anchor = (floor if floor < end else end - timedelta(days=1), end)
            said.append(work.said(before))
        if around:
            middle = fit(_clock(around.group(1)))
            anchor = (middle - timedelta(minutes=30), middle + timedelta(minutes=30))
            said.append(work.said(around))

    if anchor is None:
        out.since, out.until = now - timedelta(hours=DEFAULT_HOURS), now
        out.assumed.append(f"No period was given, so the last {DEFAULT_HOURS} hours were searched.")
        return
    since, until = anchor
    if since >= now:
        raise ValueError("That period has not happened yet.")
    until = min(until, now)
    if until - since > timedelta(days=MAX_DAYS):
        raise ValueError(f"A search covers at most {MAX_DAYS} days. Narrow the period.")
    out.since, out.until = since, until
    out.understood.append({
        "words": ", ".join(said), "field": "period",
        "as": f"{_day(since, now)} {_hhmm(since)} to {_day(until, now)} {_hhmm(until)}"})


def parse(phrase: str, *, now: datetime, zone: str, sites: Sequence[Place] = (),
          cameras: Sequence[Place] = ()) -> Parsed:
    """What the phrase asks for. Raises ValueError when it says something that
    cannot be (a period in the future, a time that is not one), and
    NotUnderstood when none of it was understood."""
    phrase = " ".join(phrase.split())
    if not phrase:
        raise NotUnderstood([])
    if len(phrase) > MAX_PHRASE:
        raise ValueError(f"A phrase is at most {MAX_PHRASE} characters.")
    local = now.astimezone(ZoneInfo(zone))
    work = _Work(phrase)
    out = Parsed(since=local, until=local)

    # Somebody's name, said to be one: 'named Tan Wei Ming', 'called "Raj Kumar"'.
    named = re.search(r"(?<!\w)(?:named|called|name\s+is|by\s+the\s+name(?:\s+of)?)\s+", work.lower)
    if named:
        rest = work.original[named.end():]
        name = re.match(r'"([^"]{2,80})"', rest) or re.match(
            r"([A-Z][\w'’.-]*(?:\s+(?:(?:binte|bin|d/o|s/o)(?!\w)|[A-Z][\w'’.-]*)){0,3})", rest)
        if name:
            out.person = name.group(1).strip()
            work.blank(named.start(), named.end() + name.end())
            out.understood.append({"words": f"{work.original[named.start():named.end()].strip()} {out.person}",
                                   "field": "person", "as": f"a name containing “{out.person}”"})

    # Words in quotes are looked for as written.
    quoted = work.take(r'"([^"]{2,80})"')
    if quoted:
        out.text = work.original[quoted.start() + 1:quoted.end() - 1].strip()
        out.understood.append({"words": f'"{out.text}"', "field": "text", "as": f"the words “{out.text}”"})

    # This organisation's own places, longest name first so that "North Gate 2"
    # is not taken for "North Gate".
    for places, target, what in ((cameras, out.camera_ids, "camera"), (sites, out.site_ids, "site")):
        for place in sorted(places, key=lambda p: len(p.name), reverse=True):
            name = " ".join(place.name.lower().split())
            if len(name) < 3:
                continue
            match = work.take(r"(?<![\w-])" + re.escape(name).replace(r"\ ", r"\s+") + r"(?![\w-])")
            if match and place.id not in target:
                target.append(place.id)
                out.understood.append({"words": work.said(match), "field": what, "as": f"the {what} {place.name}"})

    # A plate said to be one, then anything written like one.
    plate = work.take(r"(?<!\w)(?:number\s+plate|licen[cs]e\s+plate|plate|registration)\s+(?:no\.?\s+|number\s+)?"
                      r"(?=[a-z*-]*[0-9]|[a-z0-9-]*[*])([a-z0-9*][a-z0-9*-]{2,11})(?!\w)")
    if plate:
        out.plate = normalise_plate(plate.group(1))
    else:
        for token in re.finditer(r"(?<![\w-])[a-z]{1,3}\d[a-z0-9]{2,8}(?![\w-])", work.lower):
            if _PLATE.match(token.group(0)):
                out.plate = normalise_plate(token.group(0))
                work.blank(token.start(), token.end())
                break
    if out.plate:
        out.understood.append({"words": out.plate, "field": "plate", "as": f"the number plate {out.plate}"})

    _period(work, local, out)

    for vocabulary, target, what in ((KIND_WORDS, out.kinds, "kind"), (EVENT_WORDS, out.event_types, "event_type"),
                                     (SEVERITY_WORDS, out.severities, "severity")):
        # Longest phrase first across the whole vocabulary: "alarm panel"
        # before "alarm", "man down" before nothing at all.
        for value, alternatives in sorted(vocabulary.items(), key=lambda kv: -max(len(a) for a in kv[1])):
            while True:
                match = work.take(_words(alternatives))
                if not match:
                    break
                if value not in target:
                    target.append(value)
                    out.understood.append({"words": work.said(match), "field": what, "as": value})

    left = [w for w in re.findall(r"[a-z0-9][a-z0-9'’-]*", work.lower) if w not in NOISE]
    out.not_understood = list(dict.fromkeys(left))
    if not out.understood:
        raise NotUnderstood(out.not_understood)
    return out


def vocabulary() -> dict:
    """What can be said, for the screen to show beside the box."""
    return {
        "periods": ["today", "yesterday", "last night", "this morning", "this week", "last week",
                    "last 3 hours", "past 2 days", "5 Oct", "2026-10-05", "monday",
                    "between 1am and 3:30am", "after 22:00", "before 6am", "around 14:30"],
        "kinds": {kind: list(words[:3]) for kind, words in KIND_WORDS.items()},
        "event_types": {event: list(words[:3]) for event, words in EVENT_WORDS.items()},
        "severities": list(SEVERITY_WORDS),
        "plates": ["plate SGX1234A", "SGA1234B"],
        "people": ['named Tan Wei Ming', 'called "Raj Kumar"'],
        "words": ['"blue lorry"'],
        "places": "The names of your own sites and cameras, as they are written in the platform.",
        "limits": ("Read by fixed rules, not understood: a word outside this vocabulary is listed as not "
                   "understood and narrows nothing."),
    }
