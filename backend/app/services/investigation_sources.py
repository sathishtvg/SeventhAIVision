"""Smart investigation: every kind of security record, read in one shape.

A search looks in up to fourteen places — alerts, incidents, plate reads,
watchlist face matches, raw detections, door events, visitor movements, the
occurrence book, drone sightings, alarm panels, patrol scans, guard
emergencies, situations and sensor alerts — and returns one list in time
order. Each place is a `Source` below: which table, which of its columns is the
time, the site, the camera, the plate, and which EXISTING permission lets a
person read it.

THREE THINGS HOLD FOR EVERY SOURCE, AND THE BUILDER IS WHERE THEY ARE HELD.

  1. A source is searched only for someone who may already read it. The
     permission is the one its own screen asks for (`alert:read`,
     `visitor:read`, ...). A search gives nobody a record they could not open.
  2. The caller's sites apply to every source. A record with no site is not
     shown to someone restricted to certain sites — the rule of every other
     site-scoped list.
  3. Row-level security applies as it does everywhere: this module issues
     ordinary SELECTs on the caller's own session.

A SOURCE THAT CANNOT ANSWER A QUESTION IS NOT ASKED IT, AND THE ANSWER SAYS SO.
Asking for a camera leaves out the occurrence book, which has no camera; the
response lists it under `not_searched` with that reason, rather than letting an
empty result read as "nothing happened".

NOTHING IS WRITTEN HERE. The records stay where they are; an investigation
holds references to them (app/routers/investigations.py).

`security_events` is not a source: it is the intelligence layer's normalised
copy of several of these, present only where that layer is switched on, and
searching both would show each thing twice.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import site_scope_clause
from app.services.geofence import haversine_meters

#: The longest period one search may cover.
MAX_DAYS = 92
#: What a search covers when no period is given.
DEFAULT_HOURS = 24
MAX_LIMIT = 200
MAX_OFFSET = 5000
#: A search that has not answered in this long is stopped and the person is
#: told to narrow it.
TIMEOUT_MS = 8000
#: The most sightings one trail returns.
MAX_TRAIL = 500

SEVERITIES = ("info", "low", "medium", "high", "critical")
RISK_LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

NOTE = ("A search reads the records each source already holds, under your own permissions and sites. "
        "A source listed as not searched was not looked in — its absence from the results says nothing "
        "about whether anything happened there.")


class TooWide(Exception):
    """The search did not answer in time."""


@dataclass(frozen=True)
class Source:
    """One kind of record. Every string is SQL written here, never a caller's."""
    kind: str
    label: str
    permission: str
    table: str
    id: str
    time: str
    title: str
    site: str | None = None
    camera: str | None = None
    event_type: str | None = None
    summary: str | None = None
    severity: str | None = None
    status: str | None = None
    #: 'VEHICLE' or 'PERSON', when the record is about one.
    subject_kind: str | None = None
    #: A stable reference to the subject: the plate, or an id. Never a name.
    subject_ref: str | None = None
    #: What to call the subject. Shown only to someone holding `label_permission`.
    subject_label: str | None = None
    label_permission: str | None = None
    confidence: str | None = None
    risk: str | None = None
    #: Members of staff the record is tied to (who wrote it, who was sent).
    staff: tuple[str, ...] = ()
    detection: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    plate: str | None = None
    person: str | None = None
    #: Always applied.
    only: str | None = None
    #: Searched only when named: too many rows to be in every search.
    asked_for: bool = False


SOURCES: tuple[Source, ...] = (
    Source(
        kind="ALERT", label="Alerts", permission="alert:read",
        table="alerts a LEFT JOIN cameras ac ON ac.id = a.camera_id",
        id="a.id", time="a.created_at", site="COALESCE(a.site_id, ac.site_id)", camera="a.camera_id",
        event_type="a.module_type", title="a.title", summary="LEFT(a.message, 300)", severity="a.severity",
        status="a.status", staff=("a.assigned_to_user_id", "a.acknowledged_by_user_id"),
        detection="a.detection_id", latitude="ac.latitude", longitude="ac.longitude"),
    Source(
        kind="INCIDENT", label="Incidents", permission="incident:read",
        table="incidents i LEFT JOIN cameras ic ON ic.id = i.camera_id LEFT JOIN alerts ia ON ia.id = i.alert_id",
        id="i.id", time="i.created_at", site="ic.site_id", camera="i.camera_id", event_type="ia.module_type",
        title="i.title", summary="LEFT(i.description, 300)", severity="i.severity", status="i.status",
        staff=("i.assigned_to_user_id", "i.dispatched_guard_id"), latitude="ic.latitude",
        longitude="ic.longitude"),
    Source(
        kind="PLATE_READ", label="Number plate reads", permission="detection:read",
        table="lpr_events l LEFT JOIN cameras lc ON lc.id = l.camera_id",
        id="l.detection_id", time="l.detected_at", site="lc.site_id", camera="l.camera_id", event_type="'lpr'",
        title="l.plate_number", summary="concat_ws(' · ', l.direction, l.vehicle_color, l.vehicle_type)",
        status="l.watchlist_match", subject_kind="VEHICLE", subject_ref="l.plate_number",
        subject_label="l.plate_number", confidence="l.plate_confidence", detection="l.detection_id",
        latitude="lc.latitude", longitude="lc.longitude", plate="l.plate_number"),
    Source(
        # Only faces the recogniser matched to a watchlist entry. A face it
        # matched to nobody is a raw detection, and is not a person the
        # platform can name or follow.
        kind="FACE_MATCH", label="Watchlist face matches", permission="detection:read",
        table=("face_events f LEFT JOIN cameras fc ON fc.id = f.camera_id "
               "LEFT JOIN face_watchlist_entries fw ON fw.id = f.matched_watchlist_id"),
        id="f.detection_id", time="f.detected_at", site="fc.site_id", camera="f.camera_id", event_type="'face'",
        title="'A face matched a watchlist entry'", status="f.watchlist_match", subject_kind="PERSON",
        subject_ref="f.matched_watchlist_id", subject_label="fw.person_name", label_permission="watchlist:manage",
        confidence="f.match_confidence", detection="f.detection_id", latitude="fc.latitude",
        longitude="fc.longitude", person="fw.person_name", only="f.matched_watchlist_id IS NOT NULL"),
    Source(
        kind="DETECTION", label="Raw AI detections", permission="detection:read",
        table="detections d LEFT JOIN cameras dc ON dc.id = d.camera_id",
        id="d.id", time="d.detected_at", site="dc.site_id", camera="d.camera_id", event_type="d.module_type",
        title="'Detection: ' || d.module_type", confidence="d.confidence", detection="d.id",
        latitude="dc.latitude", longitude="dc.longitude", asked_for=True),
    Source(
        kind="ACCESS", label="Door and access events", permission="access:read",
        table=("access_events ae JOIN access_doors ad ON ad.id = ae.door_id "
               "LEFT JOIN access_credentials acr ON acr.id = ae.credential_id"),
        id="ae.id", time="ae.occurred_at", site="ad.site_id", camera="ad.camera_id", event_type="ae.event_type",
        title="ad.name", summary="ae.denial_reason", subject_kind="PERSON", subject_ref="ae.credential_id",
        subject_label="acr.holder_name", staff=("acr.user_id",), person="acr.holder_name"),
    Source(
        kind="VISITOR", label="Visitor movements", permission="visitor:read",
        table="visitor_logs vl LEFT JOIN visitors v ON v.id = vl.visitor_id",
        id="vl.id", time="vl.occurred_at", site="vl.site_id", event_type="vl.event_type",
        title="COALESCE(v.full_name, 'Unregistered visitor')",
        summary="LEFT(concat_ws(' · ', v.company, v.purpose, vl.notes), 300)", status="v.status",
        subject_kind="PERSON", subject_ref="vl.visitor_id", subject_label="v.full_name",
        staff=("vl.guard_user_id",), plate="v.vehicle_plate", person="v.full_name"),
    Source(
        kind="OCCURRENCE", label="Occurrence book", permission="dob:read",
        table="occurrence_book_entries ob",
        id="ob.id", time="ob.occurred_at", site="ob.site_id", event_type="ob.entry_type",
        title="'Occurrence book: ' || replace(ob.entry_type, '_', ' ')", summary="LEFT(ob.body, 300)",
        severity="ob.severity", staff=("ob.author_user_id",), latitude="ob.latitude", longitude="ob.longitude"),
    Source(
        kind="DRONE", label="Drone sightings", permission="drone:event:read",
        table="drone_events de",
        id="de.id", time="de.detected_at", site="de.site_id", event_type="de.module_type",
        title="COALESCE(de.label, de.module_type)", summary="de.zone_name", severity="lower(de.risk_level)",
        status="de.status", confidence="de.ai_confidence", risk="de.risk_level", detection="de.detection_id",
        latitude="de.estimated_latitude", longitude="de.estimated_longitude"),
    Source(
        kind="ALARM", label="Alarm panel events", permission="alarm:read",
        table="alarm_events al JOIN alarm_panels ap ON ap.id = al.panel_id",
        id="al.id", time="al.occurred_at", site="ap.site_id", event_type="al.event_type", title="ap.name",
        summary="LEFT(al.description, 300)", severity="al.severity"),
    Source(
        kind="PATROL_SCAN", label="Patrol checkpoint scans", permission="patrol:read",
        table=("checkpoint_scans cs JOIN patrol_checkpoints pc ON pc.id = cs.checkpoint_id "
               "JOIN patrol_routes pr ON pr.id = pc.route_id"),
        id="cs.id", time="cs.scanned_at", site="pr.site_id", event_type="cs.scan_method", title="pc.name",
        summary="LEFT(cs.notes, 300)", status="CASE WHEN cs.verified THEN 'verified' ELSE 'unverified' END",
        staff=("cs.guard_user_id",), latitude="cs.latitude", longitude="cs.longitude"),
    Source(
        kind="MAN_DOWN", label="Guard emergencies", permission="mandown:read",
        table="man_down_events md",
        id="md.id", time="md.detected_at", site="md.site_id", event_type="md.trigger",
        title="'Guard emergency'", summary="md.outcome", severity="'critical'", status="md.status",
        staff=("md.guard_user_id",), latitude="md.latitude", longitude="md.longitude"),
    Source(
        kind="SITUATION", label="Situations", permission="intel:read",
        table="security_situations ss",
        id="ss.id", time="ss.started_at", site="ss.site_id", camera="ss.primary_camera_id", title="ss.title",
        summary="ss.situation_number", severity="ss.severity", status="ss.decision_status", risk="ss.risk_level",
        latitude="ss.latitude", longitude="ss.longitude"),
    Source(
        kind="SENSOR", label="Sensor alerts", permission="iot:read",
        table="iot_alerts ia2 JOIN iot_sensors isn ON isn.id = ia2.sensor_id",
        id="ia2.id", time="ia2.created_at", site="isn.site_id", event_type="ia2.alert_type", title="isn.name",
        summary="LEFT(ia2.message, 300)", severity="ia2.severity", status="ia2.status"),
)

BY_KIND: dict[str, Source] = {s.kind: s for s in SOURCES}
KINDS: tuple[str, ...] = tuple(BY_KIND)
#: Every permission a search consults: one a source needs, or one a name needs.
PERMISSIONS: frozenset[str] = frozenset(
    {s.permission for s in SOURCES} | {s.label_permission for s in SOURCES if s.label_permission})

#: The columns of every row, in order. `site_name` and `camera_name` are added
#: to the page of rows after it has been cut.
COLUMNS = ("kind", "id", "occurred_at", "site_id", "camera_id", "event_type", "title", "summary", "severity",
           "status", "subject_kind", "subject_ref", "subject_label", "confidence", "risk_level", "staff_user_id",
           "detection_id", "latitude", "longitude")


@dataclass(frozen=True)
class Query:
    """What is being looked for. Every field narrows; none widens."""
    since: datetime
    until: datetime
    kinds: tuple[str, ...] = ()
    site_ids: tuple[str, ...] = ()
    camera_ids: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    plate: str | None = None
    person: str | None = None
    staff_user_id: str | None = None
    text: str | None = None
    #: An exact subject (a watchlist entry's id). Used by the trail.
    subject_ref: str | None = None

    def as_dict(self) -> dict:
        return {
            "from": self.since, "to": self.until, "kinds": list(self.kinds), "site_ids": list(self.site_ids),
            "camera_ids": list(self.camera_ids), "event_types": list(self.event_types),
            "severities": list(self.severities), "risk_levels": list(self.risk_levels), "plate": self.plate,
            "person": self.person, "staff_user_id": self.staff_user_id, "text": self.text,
        }


def normalise_plate(value: str) -> str:
    """Upper case, letters and digits only; `*` kept as "anything"."""
    return re.sub(r"[^A-Z0-9*]", "", value.upper())


def validate(query: Query) -> None:
    """Raises ValueError with something a person can act on."""
    if query.until <= query.since:
        raise ValueError("The period ends before it starts.")
    if query.until - query.since > timedelta(days=MAX_DAYS):
        raise ValueError(f"A search covers at most {MAX_DAYS} days. Narrow the period.")
    for value, allowed, what in ((query.kinds, KINDS, "kind of record"), (query.severities, SEVERITIES, "severity"),
                                 (query.risk_levels, RISK_LEVELS, "risk level")):
        unknown = [v for v in value if v not in allowed]
        if unknown:
            raise ValueError(f"Unknown {what} '{unknown[0]}'. One of: {', '.join(allowed)}.")
    if query.plate is not None:
        plate = normalise_plate(query.plate)
        if len(plate.replace("*", "")) < 3:
            raise ValueError("A number plate needs at least three letters or digits.")
    for value, what in ((query.person, "name"), (query.text, "word")):
        if value is not None and len(value.strip()) < 2:
            raise ValueError(f"A {what} to search for needs at least two characters.")


def why_not(source: Source, query: Query, held: frozenset[str] | set[str]) -> str | None:
    """Why `source` cannot be asked this question — or None when it can."""
    if source.permission not in held:
        return f"You do not hold the permission {source.permission}."
    if query.camera_ids and not source.camera:
        return "These records are not tied to a camera."
    if query.severities and not source.severity:
        return "These records carry no severity."
    if query.risk_levels and not source.risk:
        return "These records carry no risk level."
    if query.event_types and not source.event_type:
        return "These records have no event type."
    if query.plate and not source.plate:
        return "These records carry no number plate."
    if query.person:
        if not source.person:
            return "These records name no person."
        if source.label_permission and source.label_permission not in held:
            return f"Searching these by name needs the permission {source.label_permission}."
    if query.staff_user_id and not source.staff:
        return "These records are not tied to a member of staff."
    if query.subject_ref and not source.subject_ref:
        return "These records are not about a person or a vehicle."
    return None


def plan(query: Query, held: frozenset[str] | set[str]) -> tuple[list[Source], list[dict]]:
    """(the sources to search, the sources left out and why)."""
    searched: list[Source] = []
    left_out: list[dict] = []
    for source in SOURCES:
        if query.kinds and source.kind not in query.kinds:
            continue
        if not query.kinds and source.asked_for:
            left_out.append({"kind": source.kind, "label": source.label,
                             "reason": "Searched only when asked for by name: there are too many to be "
                                       "in every search."})
            continue
        reason = why_not(source, query, held)
        if reason:
            left_out.append({"kind": source.kind, "label": source.label, "reason": reason})
        else:
            searched.append(source)
    return searched, left_out


def _typed(expression: str | None, kind: str) -> str:
    return f"CAST({expression} AS {kind})" if expression else f"CAST(NULL AS {kind})"


def _branch(source: Source, query: Query, held, allowed: list[str] | None, params: dict, *,
            since: str = "since", until: str = "until", ids: str | None = None, cap: str | None = None,
            ascending: bool = False) -> str:
    """One source's SELECT in the common shape. Adds what it binds to `params`."""
    may_name = source.subject_label and (source.label_permission is None or source.label_permission in held)
    staff = f"COALESCE({', '.join(source.staff)})" if source.staff else None
    subject_kind = f"'{source.subject_kind}'" if source.subject_kind else None
    if source.subject_kind and source.subject_ref:
        # Somebody at a door with no credential, a visitor not registered: no subject.
        subject_kind = f"CASE WHEN {source.subject_ref} IS NOT NULL THEN '{source.subject_kind}' END"

    where = [f"{source.time} >= :{since}", f"{source.time} < :{until}"]
    if source.only:
        where.append(source.only)
    scope = site_scope_clause(allowed, source.site or "CAST(NULL AS uuid)", params)
    if scope:
        where.append(scope)
    if ids:
        where.append(f"CAST({source.id} AS uuid) = ANY(:{ids})")
    if query.site_ids:
        where.append(f"{source.site or 'CAST(NULL AS uuid)'} = ANY(:site_ids)")
        params["site_ids"] = [uuid.UUID(s) for s in query.site_ids]
    if query.camera_ids:
        where.append(f"{source.camera} = ANY(:camera_ids)")
        params["camera_ids"] = [uuid.UUID(c) for c in query.camera_ids]
    if query.event_types:
        where.append(f"lower(CAST({source.event_type} AS text)) = ANY(CAST(:event_types AS text[]))")
        params["event_types"] = [e.lower() for e in query.event_types]
    if query.severities:
        where.append(f"lower(CAST({source.severity} AS text)) = ANY(CAST(:severities AS text[]))")
        params["severities"] = list(query.severities)
    if query.risk_levels:
        where.append(f"upper(CAST({source.risk} AS text)) = ANY(CAST(:risk_levels AS text[]))")
        params["risk_levels"] = list(query.risk_levels)
    if query.plate:
        plate = normalise_plate(query.plate)
        read = f"regexp_replace(upper({source.plate}), '[^A-Z0-9]', '', 'g')"
        if "*" in plate:
            where.append(f"{read} LIKE :plate")
            params["plate"] = plate.replace("*", "%")
        else:
            where.append(f"{read} = :plate")
            params["plate"] = plate
    if query.person:
        where.append(f"strpos(lower({source.person}), :person) > 0")
        params["person"] = query.person.strip().lower()
    if query.staff_user_id:
        where.append("(" + " OR ".join(f"{column} = :staff" for column in source.staff) + ")")
        params["staff"] = uuid.UUID(query.staff_user_id)
    if query.subject_ref:
        where.append(f"CAST({source.subject_ref} AS text) = :subject_ref")
        params["subject_ref"] = query.subject_ref
    if query.text:
        searched = [f"strpos(lower(CAST({column} AS text)), :needle) > 0"
                    for column in (source.title, source.summary) if column]
        where.append("(" + " OR ".join(searched) + ")")
        params["needle"] = query.text.strip().lower()

    order = ""
    if cap:
        order = f" ORDER BY {source.time} {'ASC' if ascending else 'DESC'} LIMIT :{cap}"
    return f"""
        SELECT '{source.kind}'::text AS kind, CAST({source.id} AS text) AS id, {source.time} AS occurred_at,
               {_typed(source.site, 'uuid')} AS site_id, {_typed(source.camera, 'uuid')} AS camera_id,
               {_typed(source.event_type, 'text')} AS event_type, CAST({source.title} AS text) AS title,
               {_typed(source.summary, 'text')} AS summary, {_typed(source.severity, 'text')} AS severity,
               {_typed(source.status, 'text')} AS status, {_typed(subject_kind, 'text')} AS subject_kind,
               {_typed(source.subject_ref, 'text')} AS subject_ref,
               {_typed(source.subject_label if may_name else None, 'text')} AS subject_label,
               {_typed(source.confidence, 'double precision')} AS confidence,
               {_typed(source.risk, 'text')} AS risk_level, {_typed(staff, 'uuid')} AS staff_user_id,
               {_typed(source.detection, 'uuid')} AS detection_id,
               {_typed(source.latitude, 'double precision')} AS latitude,
               {_typed(source.longitude, 'double precision')} AS longitude
          FROM {source.table}
         WHERE {' AND '.join(where)}{order}"""


_NAMED = """
    SELECT p.*, s.name AS site_name, c.name AS camera_name
      FROM page p
      LEFT JOIN sites s ON s.id = p.site_id
      LEFT JOIN cameras c ON c.id = p.camera_id
"""


async def _bounded(db: AsyncSession, run: Callable[[], Awaitable[Any]]) -> Any:
    """Run a search with a time limit of its own, and leave the session as it
    was found whether it answers, runs out of time or fails."""
    prior = (await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar()
    try:
        async with db.begin_nested():
            await db.execute(text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(TIMEOUT_MS)})
            result = await run()
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code == "57014" or "statement timeout" in str(exc).lower():
            raise TooWide() from exc
        raise
    await db.execute(text("SELECT set_config('statement_timeout', :was, true)"), {"was": prior or "0"})
    return result


async def search(db: AsyncSession, query: Query, held: frozenset[str] | set[str], allowed: list[str] | None, *,
                 limit: int = 50, offset: int = 0, oldest_first: bool = False) -> dict:
    """The rows, how many of each kind there are, and what was and was not searched."""
    validate(query)
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, min(offset, MAX_OFFSET))
    sources, left_out = plan(query, held)
    answer = {"searched": [s.kind for s in sources], "not_searched": left_out, "found": {}, "items": [],
              "total": 0, "limit": limit, "offset": offset, "has_more": False, "note": NOTE}
    if not sources:
        return answer

    params: dict = {"since": query.since, "until": query.until}
    counted = "\nUNION ALL\n".join(f"({_branch(s, query, held, allowed, params)})" for s in sources)
    direction = "ASC" if oldest_first else "DESC"
    page_params = {**params, "cap": limit + offset, "limit": limit, "offset": offset}
    capped = "\nUNION ALL\n".join(
        f"({_branch(s, query, held, allowed, page_params, cap='cap', ascending=oldest_first)})" for s in sources)

    async def run():
        found = (await db.execute(text(
            f"SELECT kind, count(*) AS n FROM ({counted}) f GROUP BY kind"), params)).mappings().all()
        rows = (await db.execute(text(f"""
            WITH found AS ({capped}),
                 page AS (SELECT * FROM found ORDER BY occurred_at {direction}, kind, id
                          LIMIT :limit OFFSET :offset)
            {_NAMED} ORDER BY p.occurred_at {direction}, p.kind, p.id
        """), page_params)).mappings().all()
        return found, rows

    found, rows = await _bounded(db, run)
    answer["found"] = {r["kind"]: r["n"] for r in found}
    answer["total"] = sum(answer["found"].values())
    answer["items"] = [dict(r) for r in rows]
    answer["has_more"] = offset + limit < answer["total"]
    return answer


async def resolve(db: AsyncSession, refs: Sequence[tuple[str, Any, datetime]], held: frozenset[str] | set[str],
                  allowed: list[str] | None) -> dict[tuple[str, str], dict]:
    """The records `refs` point at — (kind, id, when it happened) — as the
    caller may see them now, keyed by (kind, id).

    A reference that comes back with nothing is a record the caller may not
    read, one outside their sites, or one that is no longer held. The caller of
    this function decides how to say which."""
    by_kind: dict[str, list[tuple[uuid.UUID, datetime]]] = {}
    for kind, ref_id, at in refs:
        source = BY_KIND.get(kind)
        if source is not None and source.permission in held and ref_id is not None:
            by_kind.setdefault(kind, []).append((uuid.UUID(str(ref_id)), at))
    if not by_kind:
        return {}

    params: dict = {}
    branches = []
    for kind, wanted in by_kind.items():
        source = BY_KIND[kind]
        # The time is given back to the database so that a table partitioned by
        # time is read in the right partitions only.
        params[f"since_{kind}"] = min(at for _, at in wanted) - timedelta(seconds=1)
        params[f"until_{kind}"] = max(at for _, at in wanted) + timedelta(seconds=1)
        params[f"ids_{kind}"] = [ref_id for ref_id, _ in wanted]
        everything = replace(_ANY, since=params[f"since_{kind}"], until=params[f"until_{kind}"])
        branches.append("(" + _branch(source, everything, held, allowed, params, since=f"since_{kind}",
                                      until=f"until_{kind}", ids=f"ids_{kind}") + ")")
    union = "\nUNION ALL\n".join(branches)
    rows = (await db.execute(text(f"WITH page AS ({union}) {_NAMED}"), params)).mappings().all()
    return {(r["kind"], r["id"]): dict(r) for r in rows}


#: A question that narrows nothing; `resolve` fills in the period.
_ANY = Query(since=datetime.min, until=datetime.max)


# ─── Where was this seen ─────────────────────────────────────────────────────

PLATE_BASIS = ("Each row is a read the plate recogniser made of this plate. A plate it misread is not here, "
               "and a read says where the vehicle was, not who was driving it.")
FACE_BASIS = ("Each row is a match the face recogniser reported against this watchlist entry, with its "
              "confidence. A match is the recogniser's opinion, not an identification.")
NOT_FOLLOWED = ("A person who is on no watchlist cannot be followed from camera to camera: the platform has "
                "no way of saying that two faces it could not name are the same person.")


def legs(sightings: Sequence[Mapping]) -> list[dict]:
    """What lies between each sighting and the one before it: how long, and how
    far when both places are known. Sightings are in time order."""
    out: list[dict] = []
    for before, after in zip(sightings, sightings[1:]):
        metres = None
        if None not in (before.get("latitude"), before.get("longitude"), after.get("latitude"),
                        after.get("longitude")):
            metres = round(haversine_meters(before["latitude"], before["longitude"], after["latitude"],
                                            after["longitude"]))
        same = before.get("camera_id") is not None and before.get("camera_id") == after.get("camera_id")
        out.append({
            "from_id": before["id"], "to_id": after["id"],
            "seconds": int((after["occurred_at"] - before["occurred_at"]).total_seconds()),
            "metres": metres, "same_camera": same,
            "same_site": before.get("site_id") is not None and before.get("site_id") == after.get("site_id"),
        })
    return out


def summarise(sightings: Sequence[Mapping]) -> dict:
    return {
        "sightings": len(sightings),
        "first_at": sightings[0]["occurred_at"] if sightings else None,
        "last_at": sightings[-1]["occurred_at"] if sightings else None,
        "cameras": len({s["camera_id"] for s in sightings if s.get("camera_id")}),
        "sites": len({s["site_id"] for s in sightings if s.get("site_id")}),
    }


async def trail(db: AsyncSession, *, since: datetime, until: datetime, held: frozenset[str] | set[str],
                allowed: list[str] | None, plate: str | None = None,
                watchlist_entry_id: str | None = None) -> dict:
    """Every place one plate, or one watchlist entry, was seen in the period,
    in order, with what lies between one sighting and the next."""
    if (plate is None) == (watchlist_entry_id is None):
        raise ValueError("Give a number plate or a watchlist entry — one of them.")
    if plate is not None:
        if "*" in plate:
            raise ValueError("A trail follows one plate. Give it in full.")
        query = Query(since=since, until=until, kinds=("PLATE_READ", "VISITOR"), plate=plate)
        basis, subject = PLATE_BASIS, {"kind": "VEHICLE", "plate": normalise_plate(plate)}
    else:
        query = Query(since=since, until=until, kinds=("FACE_MATCH",), subject_ref=str(watchlist_entry_id))
        basis, subject = FACE_BASIS, {"kind": "PERSON", "watchlist_entry_id": str(watchlist_entry_id)}
    found = await search(db, query, held, allowed, limit=MAX_LIMIT, offset=0, oldest_first=True)
    sightings = list(found["items"])
    while found["has_more"] and len(sightings) < MAX_TRAIL:
        found = await search(db, query, held, allowed, limit=MAX_LIMIT, offset=len(sightings), oldest_first=True)
        sightings.extend(found["items"])
    sightings = sightings[:MAX_TRAIL]
    if subject["kind"] == "PERSON":
        subject["name"] = next((s["subject_label"] for s in sightings if s.get("subject_label")), None)
    return {
        "subject": subject, "from": since, "to": until, "sightings": sightings, "legs": legs(sightings),
        "summary": summarise(sightings), "complete": found["total"] <= MAX_TRAIL,
        "searched": found["searched"], "not_searched": found["not_searched"], "basis": basis,
        "not_followed": NOT_FOLLOWED,
    }


async def held_permissions(db: AsyncSession, role_id: int) -> frozenset[str]:
    """Which of the permissions a search consults this role holds."""
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": sorted(PERMISSIONS)})
    return frozenset(r.code for r in rows)
