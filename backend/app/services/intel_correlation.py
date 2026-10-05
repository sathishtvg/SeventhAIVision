"""Correlation: which events are one thing happening.

  new event ─► the active situations at its site ─► the strongest rule that
              links it to one of their events ─► join that situation,
              or — when no rule does — start a situation of its own

EVERY LINK HAS A NAMED METHOD AND A REASON IN WORDS. `match()` either returns
both, with how sure the link is, or returns nothing. There is no "these seem
related". An event that no rule connects to anything opens a situation of one,
which is the honest answer and also the common one.

WHAT IS NOT CLAIMED. The platform cannot recognise an unknown person from one
camera to the next. "The same person" is asserted only where it has an identity
— a number plate, a watchlist entry. Two cameras seeing somebody a minute apart
is linked as what it is: *nearby, moments later*, with a lower confidence and a
reason that says exactly that.

NEVER ACROSS SITES. Two events at different sites are different matters however
alike they look, and the tenant boundary is the database's (row level security).

PURE RULES, THEN THE DATABASE. Everything that decides is in `match()`,
`best_link()` and `choose()`, which take plain rows. `correlate_tenant()` reads
candidates, applies them, and records the result.

DUPLICATES ARE FOLDED, NOT DROPPED. The same camera raising the same alert again
inside a few minutes joins the situation marked `is_duplicate`. The operator
sees one card with a count; the alert itself is untouched and still in the
alerts list, the audit trail and search.

READS EVENTS, WRITES SITUATIONS. Nothing here changes an alert, an incident or
any record that existed before this layer, and nothing here acts.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

#: Below this a link is not made: the event starts its own situation.
MIN_LINK = 0.5
#: The same camera, the same kind of alert, again.
REPEAT_WINDOW = timedelta(minutes=5)
#: A plate or a known face seen again at the site.
IDENTITY_WINDOW = timedelta(minutes=30)
#: A door, or an alarm, and what a camera saw around then.
DOOR_WINDOW = timedelta(minutes=5)
#: A virtual patrol's finding and what was seen there afterwards (or before).
PATROL_WINDOW = timedelta(minutes=60)
#: A guard's SOS and a serious event at the same site.
SOS_WINDOW = timedelta(minutes=10)
#: Cameras this close are neighbours without being told so.
ADJACENT_METERS = 150.0
#: A slow walk, metres a second, for turning a distance into a time allowance.
WALK_SPEED = 1.2
#: How long a situation goes quiet before a new event is a new matter.
QUIET = timedelta(minutes=float(os.environ.get("INTEL_SITUATION_QUIET_MINUTES", "30")))
#: The widest window any rule uses: how far back a situation can still be joined.
LOOKBACK = max(PATROL_WINDOW, IDENTITY_WINDOW, QUIET)
#: How many of a situation's most recent events a new one is compared with.
MEMBERS = 40
BATCH = int(os.environ.get("INTEL_CORRELATE_BATCH", "100"))

CAMERA_SOURCES = frozenset({"CCTV_AI", "LPR", "FACE_RECOGNITION"})
DEFAULT_TZ = "Asia/Singapore"


@dataclass(frozen=True)
class Link:
    """Why an event belongs with another: the method, the reason an officer
    reads, how sure, and whether it adds nothing new."""

    method: str
    reason: str
    confidence: float
    matched_event_id: Any = None
    duplicate: bool = False


# ─── Small helpers ───────────────────────────────────────────────────────────

def _metres(a: Mapping, b: Mapping) -> float | None:
    """Distance between two events' positions, or None when either has none."""
    if None in (a.get("latitude"), a.get("longitude"), b.get("latitude"), b.get("longitude")):
        return None
    lat1, lon1, lat2, lon2 = (math.radians(float(x)) for x in
                              (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(min(1.0, h)))


def _apart(a: Mapping, b: Mapping) -> timedelta:
    return abs(a["occurred_at"] - b["occurred_at"])


def _ago(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return f"{seconds} s apart"
    return f"{round(seconds / 60)} min apart"


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a) == str(b)


def _where(e: Mapping) -> str:
    return str(e.get("location_label") or "a camera")


def _pair(x: Any, y: Any) -> frozenset:
    return frozenset({str(x), str(y)})


def _moving(e: Mapping) -> bool:
    """Something that goes from place to place: a person or a vehicle."""
    return e.get("subject_kind") in ("PERSON", "VEHICLE")


# ─── The rules ───────────────────────────────────────────────────────────────

def match(event: Mapping, member: Mapping, *, camera_links: Mapping[frozenset, int] | None = None,
          drone_pairs: frozenset | set | None = None) -> Link | None:
    """Whether `event` belongs with `member`, an event already in a situation —
    by the strongest rule that applies, or None when none does.

    `camera_links` maps a pair of camera ids to the walk between them in
    seconds. `drone_pairs` holds (drone event id, alert id) pairs the drone
    module's own CCTV correlation found to agree."""
    camera_links = camera_links or {}
    drone_pairs = drone_pairs or set()
    found: list[Link] = []
    if _same(event.get("id"), member.get("id")):
        return None

    def add(method: str, reason: str, confidence: float, duplicate: bool = False) -> None:
        found.append(Link(method, reason, round(confidence, 4), member.get("id"), duplicate))

    # An exact reference needs no site and no clock: the two rows are about the same thing.
    if _same(event.get("alert_id"), member.get("alert_id")) and not _same(event.get("source_id"),
                                                                           member.get("source_id")):
        add("SAME_ALERT", "Both records carry the same alert.", 0.99, duplicate=True)
    for drone, other in ((event, member), (member, event)):
        if (drone.get("source_table") == "drone_events" and other.get("alert_id") is not None
                and _pair(drone.get("source_id"), other.get("alert_id")) in drone_pairs):
            add("DRONE_CCTV", f"The drone's sighting was corroborated by {_where(other)}.", 0.9)

    # Everything else is about one site.
    site_e, site_m = event.get("site_id"), member.get("site_id")
    if found or site_e is None or site_m is None or str(site_e) != str(site_m):
        return max(found, key=lambda link: link.confidence) if found else None

    apart = _apart(event, member)
    same_camera = _same(event.get("camera_id"), member.get("camera_id"))
    src_e, src_m = event.get("source_type"), member.get("source_type")

    if (event.get("subject_ref") and _same(event.get("subject_ref"), member.get("subject_ref"))
            and event.get("subject_kind") == member.get("subject_kind") and apart <= IDENTITY_WINDOW):
        if event["subject_kind"] == "VEHICLE":
            add("SAME_IDENTITY", f"The same number plate, {event['subject_ref']}, {_ago(apart)}.", 0.95)
        else:
            add("SAME_IDENTITY", f"The same watchlist entry, {_ago(apart)}.", 0.9)

    if event.get("event_type") == member.get("event_type") and apart <= REPEAT_WINDOW:
        if same_camera:
            add("SAME_SOURCE_REPEAT", f"The same alert from {_where(event)} again, {_ago(apart)}.", 0.9, True)
        elif (event.get("camera_id") is None and member.get("camera_id") is None and src_e == src_m
              and event.get("location_label") and event.get("location_label") == member.get("location_label")):
            add("SAME_SOURCE_REPEAT", f"The same alert from {_where(event)} again, {_ago(apart)}.", 0.85, True)

    for door, other in ((event, member), (member, event)):
        if door is other:
            continue
        seen_by_camera = other.get("source_type") in CAMERA_SOURCES or other.get("source_type") == "DRONE_PATROL"
        if door.get("source_type") == "ACCESS_CONTROL" and seen_by_camera and apart <= DOOR_WINDOW:
            if same_camera:
                add("ACCESS_AT_CAMERA", f"An access event at the door this camera watches, {_ago(apart)}.", 0.85)
            else:
                add("ACCESS_AT_CAMERA", f"An access event at the same site, {_ago(apart)}.", 0.6)
        if door.get("source_type") == "ALARM" and seen_by_camera and apart <= DOOR_WINDOW:
            if same_camera:
                add("ALARM_AT_CAMERA", f"An alarm in the zone this camera watches, {_ago(apart)}.", 0.85)
            else:
                add("ALARM_AT_CAMERA", f"An alarm at the same site, {_ago(apart)}.", 0.55)

    both_cameras = event.get("camera_id") is not None and member.get("camera_id") is not None
    if (both_cameras and not same_camera and _moving(event) and event.get("subject_kind") == member.get("subject_kind")
            and src_e in CAMERA_SOURCES and src_m in CAMERA_SOURCES):
        walk = camera_links.get(_pair(event["camera_id"], member["camera_id"]))
        distance = _metres(event, member)
        noun = "person" if event["subject_kind"] == "PERSON" else "vehicle"
        if walk is not None and apart <= timedelta(seconds=max(60, walk * 2)):
            add("ADJACENT_CAMERA", f"A {noun} at {_where(member)}, then at the next camera, {_ago(apart)}. "
                                   f"Nearby and moments later — not identified as the same {noun}.", 0.75)
        elif walk is None and distance is not None and distance <= ADJACENT_METERS:
            allowance = timedelta(seconds=max(60.0, distance / WALK_SPEED * 2 + 30))
            if apart <= allowance:
                add("ADJACENT_CAMERA", f"A {noun} at {_where(member)}, then {round(distance)} m away, {_ago(apart)}. "
                                       f"Nearby and moments later — not identified as the same {noun}.",
                    0.7 - 0.2 * (distance / ADJACENT_METERS))

    for drone, other in ((event, member), (member, event)):
        if (drone.get("source_type") == "DRONE_PATROL" and other.get("source_type") in CAMERA_SOURCES
                and apart <= DOOR_WINDOW):
            distance = _metres(drone, other)
            if distance is not None and distance <= ADJACENT_METERS:
                add("NEAR_POSITION", f"The drone's sighting was {round(distance)} m from {_where(other)}, "
                                     f"{_ago(apart)}.", 0.65)

    for patrol, other in ((event, member), (member, event)):
        if patrol.get("source_type") != "VIRTUAL_PATROL" or other.get("source_type") == "VIRTUAL_PATROL":
            continue
        if apart > PATROL_WINDOW:
            continue
        if same_camera:
            add("PATROL_FINDING", f"A virtual patrol reported an exception on this camera, {_ago(apart)}.", 0.7)
        elif other.get("source_type") == "DRONE_PATROL":
            add("PATROL_FINDING", f"A virtual patrol reported an exception at this site, {_ago(apart)}, "
                                  f"and a drone then saw something there.", 0.5)
        else:
            distance = _metres(patrol, other)
            if distance is not None and distance <= ADJACENT_METERS:
                add("PATROL_FINDING", f"A virtual patrol reported an exception {round(distance)} m away, "
                                      f"{_ago(apart)}.", 0.5)

    for sos, other in ((event, member), (member, event)):
        if (sos.get("source_type") == "GUARD" and other.get("source_type") != "GUARD" and apart <= SOS_WINDOW
                and SEVERITY_RANK.get(str(other.get("severity")), 0) >= SEVERITY_RANK["high"]):
            add("GUARD_SOS_AT_SITE", f"A guard raised an SOS at the site, {_ago(apart)} from a "
                                     f"{other.get('severity')} event there.", 0.55)

    return max(found, key=lambda link: link.confidence) if found else None


def best_link(event: Mapping, members: list[Mapping], **kw) -> Link | None:
    """The strongest link from `event` to any of a situation's events."""
    links = [link for link in (match(event, m, **kw) for m in members) if link is not None]
    return max(links, key=lambda link: link.confidence) if links else None


def choose(event: Mapping, situations: list[tuple[Mapping, list[Mapping]]], **kw):
    """(situation, link) for the situation `event` belongs to, or None. The
    strongest link wins; between equals, the situation heard from most recently."""
    options = []
    for situation, members in situations:
        link = best_link(event, members, **kw)
        if link is not None and link.confidence >= MIN_LINK:
            options.append((link.confidence, situation["last_event_at"], situation, link))
    if not options:
        return None
    _, _, situation, link = max(options, key=lambda o: (o[0], o[1]))
    return situation, link


def promotes(situation: Mapping, event: Mapping) -> bool:
    """Whether `event` becomes what the situation is named after: only when it
    is more severe than anything the situation has had. A situation's severity
    never goes down, however many lesser events follow."""
    return (SEVERITY_RANK.get(str(event.get("severity")), 0)
            > SEVERITY_RANK.get(str(situation.get("severity")), 0))


# ─── Against the database ────────────────────────────────────────────────────

async def _scope(db: AsyncSession, tenant_id) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def settle(db: AsyncSession, now: datetime) -> int:
    """Situations that have been quiet long enough stop taking new events."""
    result = await db.execute(text(
        "UPDATE security_situations SET status = 'SETTLED', settled_at = :n, updated_at = :n "
        " WHERE status = 'ACTIVE' AND last_event_at < :cut RETURNING id"), {"n": now, "cut": now - QUIET})
    return len(result.all())


async def _candidates(db: AsyncSession, event: Mapping) -> list[tuple[dict, list[dict]]]:
    """The active situations `event` might belong to — at its site and recent
    enough, or holding a record of the same alert — each with its latest events."""
    rows = (await db.execute(text("""
        SELECT s.* FROM security_situations s
         WHERE s.status = 'ACTIVE'
           AND s.last_event_at >= :earliest AND s.started_at <= :latest
           AND ((CAST(:site AS uuid) IS NOT NULL AND s.site_id = CAST(:site AS uuid))
                OR (CAST(:alert AS uuid) IS NOT NULL AND EXISTS (
                        SELECT 1 FROM security_situation_events l JOIN security_events m ON m.id = l.event_id
                         WHERE l.situation_id = s.id AND m.alert_id = CAST(:alert AS uuid))))
         ORDER BY s.last_event_at DESC LIMIT 20
    """), {"site": str(event["site_id"]) if event.get("site_id") else None,
           "alert": str(event["alert_id"]) if event.get("alert_id") else None,
           "earliest": event["occurred_at"] - LOOKBACK, "latest": event["occurred_at"] + LOOKBACK})).mappings().all()
    out = []
    for s in rows:
        members = (await db.execute(text("""
            SELECT m.* FROM security_situation_events l JOIN security_events m ON m.id = l.event_id
             WHERE l.situation_id = :s ORDER BY m.occurred_at DESC, m.id LIMIT :n
        """), {"s": s["id"], "n": MEMBERS})).mappings().all()
        out.append((dict(s), [dict(m) for m in members]))
    return out


async def _camera_links(db: AsyncSession, site_id) -> dict[frozenset, int]:
    if site_id is None:
        return {}
    rows = (await db.execute(text("""
        SELECT l.camera_a, l.camera_b, l.walk_seconds FROM security_camera_links l
          JOIN cameras c ON c.id = l.camera_a WHERE c.site_id = :s
    """), {"s": site_id})).all()
    return {_pair(r.camera_a, r.camera_b): r.walk_seconds for r in rows}


async def _drone_pairs(db: AsyncSession, event: Mapping) -> set[frozenset]:
    """(drone event, alert) pairs the drone module's own correlation agreed on,
    for whichever side of such a pair `event` is."""
    if event.get("source_table") == "drone_events":
        rows = (await db.execute(text(
            "SELECT event_id, related_alert_id FROM drone_event_cameras "
            " WHERE event_id = :e AND corroborates AND related_alert_id IS NOT NULL"),
            {"e": event["source_id"]})).all()
    elif event.get("alert_id") is not None:
        rows = (await db.execute(text(
            "SELECT event_id, related_alert_id FROM drone_event_cameras "
            " WHERE related_alert_id = :a AND corroborates"), {"a": event["alert_id"]})).all()
    else:
        return set()
    return {_pair(r.event_id, r.related_alert_id) for r in rows}


async def _number(db: AsyncSession, at: datetime) -> str:
    """SIT-YYYYMMDD-NNNN, counted per tenant per local day. One at a time: the
    lock is the tenant's, and is released when the transaction ends."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('security_situation:' || "
                          "current_setting('app.current_tenant')))"))
    tz = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        day = at.astimezone(ZoneInfo(tz or DEFAULT_TZ)).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001 — an unknown zone name must not stop a situation opening
        day = at.astimezone(ZoneInfo(DEFAULT_TZ)).strftime("%Y%m%d")
    # As a number, not as text: "10000" sorts before "9999".
    last = (await db.execute(text(
        "SELECT max(CAST(split_part(situation_number, '-', 3) AS integer)) FROM security_situations "
        " WHERE situation_number LIKE :p"), {"p": f"SIT-{day}-%"})).scalar()
    return f"SIT-{day}-{(last or 0) + 1:04d}"


async def _open(db: AsyncSession, event: Mapping) -> dict:
    """A new situation of one event."""
    number = await _number(db, event["occurred_at"])
    situation = (await db.execute(text("""
        INSERT INTO security_situations
               (tenant_id, site_id, situation_number, title, severity, started_at, last_event_at, event_count,
                source_types, primary_camera_id, location_label, latitude, longitude)
        VALUES (current_setting('app.current_tenant')::uuid, :site, :number, :title, :severity, :at, :at, 1,
                ARRAY[CAST(:source AS text)], :camera, :label, :lat, :lon)
        RETURNING *
    """), {"site": event.get("site_id"), "number": number, "title": event["title"], "severity": event["severity"],
           "at": event["occurred_at"], "source": event["source_type"], "camera": event.get("camera_id"),
           "label": event.get("location_label"), "lat": event.get("latitude"),
           "lon": event.get("longitude")})).mappings().one()
    await db.execute(text("""
        INSERT INTO security_situation_events (tenant_id, situation_id, event_id, method, reason, confidence)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :e, 'FIRST_EVENT',
                'The first event of the situation.', 1.0)
    """), {"s": situation["id"], "e": event["id"]})
    return dict(situation)


async def _join(db: AsyncSession, situation: Mapping, event: Mapping, link: Link) -> dict:
    """Add `event` to `situation` with its reason, and bring the situation's
    summary up to date."""
    await db.execute(text("""
        INSERT INTO security_situation_events
               (tenant_id, situation_id, event_id, method, reason, confidence, matched_event_id, is_duplicate)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :e, :method, :reason, :confidence, :matched,
                :duplicate)
    """), {"s": situation["id"], "e": event["id"], "method": link.method, "reason": link.reason,
           "confidence": link.confidence, "matched": link.matched_event_id, "duplicate": link.duplicate})
    # A more severe event renames the situation and moves it to where that event
    # was. A lesser one only fills in a place the situation did not have.
    updated = (await db.execute(text("""
        UPDATE security_situations SET
               event_count = event_count + 1,
               duplicate_count = duplicate_count + CAST(:dup AS integer),
               started_at = LEAST(started_at, :at),
               last_event_at = GREATEST(last_event_at, :at),
               source_types = (SELECT array_agg(DISTINCT t ORDER BY t)
                                 FROM unnest(source_types || ARRAY[CAST(:source AS text)]) AS t),
               severity = CASE WHEN :promote THEN CAST(:severity AS varchar) ELSE severity END,
               title = CASE WHEN :promote THEN CAST(:title AS varchar) ELSE title END,
               primary_camera_id = CASE WHEN :promote AND CAST(:camera AS uuid) IS NOT NULL
                                        THEN CAST(:camera AS uuid)
                                        ELSE COALESCE(primary_camera_id, CAST(:camera AS uuid)) END,
               location_label = CASE WHEN :promote AND CAST(:label AS varchar) IS NOT NULL
                                     THEN CAST(:label AS varchar)
                                     ELSE COALESCE(location_label, CAST(:label AS varchar)) END,
               latitude = CASE WHEN :promote AND CAST(:lat AS double precision) IS NOT NULL
                               THEN CAST(:lat AS double precision)
                               ELSE COALESCE(latitude, CAST(:lat AS double precision)) END,
               longitude = CASE WHEN :promote AND CAST(:lon AS double precision) IS NOT NULL
                                THEN CAST(:lon AS double precision)
                                ELSE COALESCE(longitude, CAST(:lon AS double precision)) END,
               correlation_confidence = LEAST(COALESCE(correlation_confidence, 1), CAST(:confidence AS numeric)),
               updated_at = now()
         WHERE id = :s RETURNING *
    """), {"s": situation["id"], "dup": 1 if link.duplicate else 0, "at": event["occurred_at"],
           "source": event["source_type"], "promote": promotes(situation, event),
           "severity": event["severity"], "title": event["title"],
           "camera": str(event["camera_id"]) if event.get("camera_id") else None,
           "label": event.get("location_label"),
           "lat": float(event["latitude"]) if event.get("latitude") is not None else None,
           "lon": float(event["longitude"]) if event.get("longitude") is not None else None,
           "confidence": link.confidence})).mappings().one()
    return dict(updated)


def summary(situation: Mapping, *, opened: bool, link: Link | None = None) -> dict:
    """What is announced on the tenant's live channel: enough to draw a card,
    and the reason this event joined."""
    return {
        "situation_id": str(situation["id"]),
        "situation_number": situation["situation_number"],
        "title": situation["title"],
        "severity": situation["severity"],
        "status": situation["status"],
        "site_id": str(situation["site_id"]) if situation.get("site_id") else None,
        "event_count": situation["event_count"],
        "duplicate_count": situation["duplicate_count"],
        "source_types": list(situation["source_types"] or []),
        "started_at": situation["started_at"].isoformat(),
        "last_event_at": situation["last_event_at"].isoformat(),
        "opened": opened,
        "link": None if link is None else {"method": link.method, "reason": link.reason,
                                           "confidence": link.confidence, "is_duplicate": link.duplicate},
    }


async def correlate_event(db: AsyncSession, event: Mapping) -> tuple[dict, Link | None]:
    """Place one event: (the situation it is now in, the link that put it there
    — None when it opened a situation of its own). The caller commits."""
    situations = await _candidates(db, event)
    picked = choose(event, situations, camera_links=await _camera_links(db, event.get("site_id")),
                    drone_pairs=await _drone_pairs(db, event))
    if picked is None:
        situation, link = await _open(db, event), None
    else:
        target, link = picked
        situation = await _join(db, target, event, link)
    await db.execute(text("UPDATE security_events SET status = 'LINKED' WHERE id = :e"), {"e": event["id"]})
    return situation, link


async def correlate_tenant(factory, tenant_id, now: datetime, batch: int = BATCH) -> dict:
    """Settle what has gone quiet, then place every event not yet placed,
    oldest first. Returns counts and the announcements to publish:
    {"settled", "opened", "joined", "duplicates", "failed", "announce": [(event_type, payload)]}."""
    out: dict = {"settled": 0, "opened": 0, "joined": 0, "duplicates": 0, "failed": 0, "announce": []}
    async with factory() as db:
        await _scope(db, tenant_id)
        out["settled"] = await settle(db, now)
        ids = (await db.execute(text(
            "SELECT id FROM security_events WHERE status = 'NEW' ORDER BY occurred_at, id LIMIT :n"),
            {"n": batch})).scalars().all()
        await db.commit()
    for event_id in ids:
        try:
            async with factory() as db:
                await _scope(db, tenant_id)
                # Locked, so two runners cannot place the same event twice.
                event = (await db.execute(text(
                    "SELECT * FROM security_events WHERE id = :e AND status = 'NEW' FOR UPDATE SKIP LOCKED"),
                    {"e": event_id})).mappings().first()
                if event is None:
                    continue
                situation, link = await correlate_event(db, dict(event))
                await db.commit()
        except Exception:  # noqa: BLE001 — one event's failure is not the batch's
            out["failed"] += 1
            continue
        if link is None:
            out["opened"] += 1
            out["announce"].append(("intel_situation_opened", summary(situation, opened=True)))
        else:
            out["joined"] += 1
            out["duplicates"] += 1 if link.duplicate else 0
            out["announce"].append(("intel_situation_updated", summary(situation, opened=False, link=link)))
    return out
