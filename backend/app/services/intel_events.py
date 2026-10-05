"""Normalising security events: one shape for every source.

  alerts ──────────────┐   CCTV AI, LPR, face, access control, alarm panels,
                       │   sensors, and the drone module's flight alerts
  drone_events ────────┤   verified drone sightings
  incidents (SOS) ─────┼─► from_*()  ─►  security_events
  virtual patrol ──────┤   an officer's exception on a camera patrol
  camera_health_events ┘   a camera going dark

READ-ONLY ON EVERYTHING THAT EXISTED BEFORE. Each source is selected from and
never written to. The 21 places that insert an alert are not asked to call
anything; an alert is found here after it has been committed, the same way an
operator finds it.

THE MAPPING IS PURE. Every `from_*` takes a row and returns a `Normalised`, with
no database, so each source's rule is a test that needs nothing running. What a
source does not say is left empty: a face that matched nobody has no
`subject_ref`, an alarm panel has no confidence, an SOS has no camera. Nothing
is filled in.

ONCE PER SOURCE RECORD. `security_events` is unique on (tenant, source table,
source id) and the select skips what is already there, so a second pass over
the same rows — after a crash, or from the look-back below — inserts nothing.

WHERE READING STARTS. The first time a tenant's source is read, reading starts
`BACKFILL` back and no further: a tenant that turns this on with nine thousand
old alerts gets today's, not nine thousand situations. After that each pass
re-reads the last `OVERLAP`, because a row is stamped when its transaction
starts and visible only when it commits; without the overlap a slow writer's
alert would fall behind the cursor and never be read.

CONFIDENCE IS THE SOURCE'S OWN. The model's certainty about what it saw, copied
and never adjusted. How much an event matters is risk, decided later and kept in
another place.

AN ALERT'S CAMERA IS NOT ALWAYS WHERE IT HAPPENED. An alarm panel's alert is
attached to its zone's camera when the zone has one — and to an arbitrary camera
of the tenant when it does not, because an alert once needed a camera to exist.
A guard's SOS incident is hung on an arbitrary camera for the same reason. So an
alarm's place is taken from its panel and zone, a sensor's from the sensor, a
vehicle tracker's from the position it reported, and an SOS's from the guard.
Putting an alarm at the wrong site would join it to events it has nothing to do
with.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_TYPES = ("CCTV_AI", "DRONE_PATROL", "VIRTUAL_PATROL", "LPR", "FACE_RECOGNITION",
                "ACCESS_CONTROL", "ALARM", "GUARD", "SENSOR", "SYSTEM", "OTHER")
SEVERITIES = ("info", "low", "medium", "high", "critical")

#: What kind of source raised an alert, by the module that wrote it. A module
#: missing from here is OTHER — read and kept, never dropped for being unknown.
ALERT_SOURCE = {
    "lpr": "LPR",
    "face": "FACE_RECOGNITION",
    "intrusion": "CCTV_AI", "ppe": "CCTV_AI", "crowd": "CCTV_AI", "fire_smoke": "CCTV_AI",
    "weapon": "CCTV_AI", "behavior": "CCTV_AI", "tampering": "CCTV_AI", "abandoned": "CCTV_AI",
    "fall": "CCTV_AI",
    "access": "ACCESS_CONTROL",
    "alarm": "ALARM",
    "iot": "SENSOR", "gps": "SENSOR",
    "drone_patrol": "DRONE_PATROL",
}

#: Alerts that are about the workforce, not about security at a site: a guard
#: projected over the overtime cap, a rest-day breach on the roster. Left to the
#: screens that already handle them.
NOT_SECURITY = ("payroll", "roster")

#: Detections that are of a person, as opposed to a vehicle, an object or a scene.
PERSON_MODULES = frozenset({"face", "intrusion", "ppe", "behavior", "fall"})
VEHICLE_MODULES = frozenset({"lpr"})

BACKFILL = timedelta(minutes=float(os.environ.get("INTEL_BACKFILL_MINUTES", "60")))
OVERLAP = timedelta(seconds=float(os.environ.get("INTEL_OVERLAP_SECONDS", "120")))
BATCH = int(os.environ.get("INTEL_INGEST_BATCH", "200"))


@dataclass(frozen=True)
class Normalised:
    """One security event in the common shape. Field for field, a row of
    `security_events` without the tenant, which the session supplies."""

    source_type: str
    source_table: str
    source_id: Any
    event_type: str
    occurred_at: datetime
    severity: str
    title: str
    site_id: Any = None
    camera_id: Any = None
    drone_id: Any = None
    alert_id: Any = None
    incident_id: Any = None
    detection_id: Any = None
    subject_kind: str = "NONE"
    subject_ref: str | None = None
    subject_verdict: str | None = None
    confidence: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_label: str | None = None
    attributes: dict = field(default_factory=dict)


def _severity(value: Any, default: str = "medium") -> str:
    v = str(value or "").lower()
    return v if v in SEVERITIES else default


def _verdict(match: Any) -> str:
    """A watchlist's answer. No answer is UNKNOWN — which is not 'unauthorised'."""
    m = str(match or "").lower()
    return {"allow": "ALLOW", "block": "BLOCK"}.get(m, "UNKNOWN")


def _subject_kind(module: str) -> str:
    if module in VEHICLE_MODULES:
        return "VEHICLE"
    return "PERSON" if module in PERSON_MODULES else "NONE"


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _clean(d: dict) -> dict:
    """Without the entries the source had nothing for."""
    return {k: v for k, v in d.items() if v is not None}


def _obj(value: Any) -> dict:
    """A JSON column as a dict, whether the driver handed back text or an object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _place(name: Any, location: Any) -> str | None:
    parts = [str(p).strip() for p in (name, location) if p and str(p).strip()]
    return " — ".join(parts)[:255] if parts else None


# ─── The mappings ────────────────────────────────────────────────────────────

def from_alert(a: Mapping) -> Normalised:
    """An alert, with what its detection adds. Covers every source that raises
    one: the AI workers, access control, alarm panels, sensors, and the drone
    module's alerts about a flight."""
    module = a["module_type"]
    kind = _subject_kind(module)
    ref = verdict = None
    if module == "lpr":
        ref = a.get("plate_number")
        verdict = _verdict(a.get("plate_match"))
    elif module == "face":
        matched = a.get("matched_watchlist_id")
        ref = str(matched) if matched else None
        verdict = _verdict(a.get("face_match"))

    # Where it happened. For most alerts that is the alert's camera; for the
    # three below the alert's camera says nothing (see the module docstring).
    site_id = a.get("site_id") or a.get("camera_site_id")
    camera_id = a.get("camera_id")
    latitude, longitude = _num(a.get("camera_latitude")), _num(a.get("camera_longitude"))
    label = _place(a.get("camera_name"), a.get("camera_location"))
    extra: dict = {}
    if module == "alarm":
        site_id, camera_id = a.get("alarm_site_id"), a.get("alarm_camera_id")
        latitude, longitude = _num(a.get("alarm_camera_latitude")), _num(a.get("alarm_camera_longitude"))
        label = _place(a.get("alarm_zone_name") or a.get("alarm_panel_name"), a.get("alarm_camera_name"))
        extra = {"panel": a.get("alarm_panel_name"), "zone_number": a.get("alarm_zone_number")}
    elif module == "iot":
        site_id, camera_id, latitude, longitude = a.get("sensor_site_id"), None, None, None
        label = _place(a.get("sensor_name"), a.get("sensor_location"))
        extra = {"sensor_type": a.get("sensor_type")}
    elif module == "gps":
        params = _obj(a.get("gps_params"))
        site_id, camera_id = None, None
        latitude, longitude = _num(params.get("lat")), _num(params.get("lon"))
        label = (str(params["geofence"])[:255] if params.get("geofence") else None)
        extra = {"vehicle_id": params.get("vehicle_id"), "geofence": params.get("geofence")}
    return Normalised(
        source_type=ALERT_SOURCE.get(module, "OTHER"),
        source_table="alerts",
        source_id=a["id"],
        event_type=a.get("alert_code") or f"{module}.alert",
        occurred_at=a["created_at"],
        severity=_severity(a.get("severity")),
        title=str(a["title"])[:255],
        site_id=site_id,
        camera_id=camera_id,
        alert_id=a["id"],
        detection_id=a.get("detection_id"),
        subject_kind=kind,
        subject_ref=ref,
        subject_verdict=verdict,
        confidence=_num(a.get("detection_confidence")),
        latitude=latitude,
        longitude=longitude,
        location_label=label,
        attributes=_clean({
            "module_type": module,
            **extra,
            "zone_id": str(a["zone_id"]) if a.get("zone_id") else None,
            "zone_name": a.get("zone_name"),
            "dwell_time_seconds": _num(a.get("dwell_time_seconds")),
            "plate_confidence": _num(a.get("plate_confidence")),
            "direction": a.get("direction"),
            "vehicle_type": a.get("vehicle_type"),
            "vehicle_color": a.get("vehicle_color"),
            "match_confidence": _num(a.get("match_confidence")),
        }),
    )


def from_drone_event(e: Mapping) -> Normalised:
    """A verified drone sighting. The drone module has already judged it — its
    risk comes along as evidence and is not re-scored here."""
    module = e["module_type"]
    plate = _obj(e.get("attributes")).get("plate")
    lat = e.get("estimated_latitude") if e.get("estimated_latitude") is not None else e.get("drone_latitude")
    lon = e.get("estimated_longitude") if e.get("estimated_longitude") is not None else e.get("drone_longitude")
    return Normalised(
        source_type="DRONE_PATROL",
        source_table="drone_events",
        source_id=e["id"],
        event_type=f"drone.{module}",
        occurred_at=e["detected_at"],
        severity=_severity(e.get("risk_level")),
        title=str(e.get("label") or f"Drone sighting: {module.replace('_', ' ')}")[:255],
        site_id=e.get("site_id"),
        drone_id=e.get("drone_id"),
        alert_id=e.get("alert_id"),
        incident_id=e.get("incident_id"),
        detection_id=e.get("detection_id"),
        subject_kind=_subject_kind(module),
        subject_ref=str(plate) if plate and module == "lpr" else None,
        confidence=_num(e.get("ai_confidence")),
        latitude=_num(lat),
        longitude=_num(lon),
        location_label=(str(e["zone_name"])[:255] if e.get("zone_name") else None),
        attributes=_clean({
            "module_type": module,
            "session_id": str(e["session_id"]) if e.get("session_id") else None,
            "mission_id": str(e["mission_id"]) if e.get("mission_id") else None,
            "zone_type": e.get("zone_type"),
            "drone_risk_score": _num(e.get("risk_score")),
            "drone_risk_level": e.get("risk_level"),
            "verification_state": e.get("verification_state"),
            "detection_count": e.get("detection_count"),
            "observed_seconds": _num(e.get("observed_seconds")),
            "location_method": e.get("location_method"),
        }),
    )


def from_guard_sos(i: Mapping) -> Normalised:
    """A guard's SOS — pressed, or a man-down that nobody cancelled.

    The incident it opens is hung on an arbitrary active camera so that it
    appears in the incident queue; that camera says nothing about where the
    guard is, so neither it nor its site is taken. The place is the position
    the phone reported and the site of the shift the guard was working."""
    p = _obj(i.get("message_params"))
    return Normalised(
        source_type="GUARD",
        source_table="incidents",
        source_id=i["id"],
        event_type="guard.sos",
        occurred_at=i["created_at"],
        severity=_severity(i.get("severity"), "critical"),
        title="Guard SOS",
        site_id=i.get("shift_site_id"),
        incident_id=i["id"],
        subject_kind="PERSON",
        latitude=_num(p.get("latitude")),
        longitude=_num(p.get("longitude")),
        location_label=(str(i["shift_site_name"])[:255] if i.get("shift_site_name") else None),
        attributes=_clean({"guard_user_id": p.get("guard_user_id")}),
    )


def from_patrol_exception(x: Mapping) -> Normalised:
    """An officer's exception on a virtual patrol: a camera question answered
    with something wrong."""
    return Normalised(
        source_type="VIRTUAL_PATROL",
        source_table="virtual_patrol_session_answers",
        source_id=x["id"],
        event_type="vpatrol.exception",
        occurred_at=x["answered_at"],
        severity="high" if x.get("failure_action") == "CREATE_INCIDENT" else "medium",
        title=f"Virtual patrol exception: {str(x.get('question_text') or 'camera check')}"[:255],
        site_id=x.get("site_id"),
        camera_id=x.get("camera_id"),
        incident_id=x.get("incident_id"),
        latitude=_num(x.get("camera_latitude")),
        longitude=_num(x.get("camera_longitude")),
        location_label=_place(x.get("camera_name"), None),
        attributes=_clean({
            "session_id": str(x["session_id"]) if x.get("session_id") else None,
            "patrol_number": x.get("patrol_number"),
            "question": (str(x["question_text"])[:500] if x.get("question_text") else None),
            "exception_reason": (str(x["exception_reason"])[:500] if x.get("exception_reason") else None),
            "officer_user_id": str(x["answered_by_user_id"]) if x.get("answered_by_user_id") else None,
        }),
    )


def from_camera_health(h: Mapping) -> Normalised:
    """A camera that stopped sending. Not an alert today — but a camera going
    dark beside an intrusion is exactly what correlation is for."""
    return Normalised(
        source_type="SYSTEM",
        source_table="camera_health_events",
        source_id=h["id"],
        event_type=f"camera.{h['event_type']}",
        occurred_at=h["occurred_at"],
        severity="low",
        title=f"Camera stopped sending: {h.get('camera_name') or 'camera'}"[:255],
        site_id=h.get("camera_site_id"),
        camera_id=h.get("camera_id"),
        latitude=_num(h.get("camera_latitude")),
        longitude=_num(h.get("camera_longitude")),
        location_label=_place(h.get("camera_name"), h.get("camera_location")),
        attributes=_clean({"detail": (str(h["detail"])[:500] if h.get("detail") else None)}),
    )


# ─── The sources ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Source:
    """Where a kind of event is read from: the rows not yet normalised, oldest
    first, from `:floor` on, with the column that says when each was written."""

    name: str
    time_column: str
    select: str
    normalise: Callable[[Mapping], Normalised]


_NOT_YET = ("NOT EXISTS (SELECT 1 FROM security_events e "
            "WHERE e.source_table = '{table}' AND e.source_id = {alias}.id)")

SOURCES: tuple[Source, ...] = (
    Source(
        name="alerts", time_column="created_at", normalise=from_alert,
        # The module tables are joined only for their own module's alerts. A
        # drone event's alert is skipped: the event itself is read below and
        # says more.
        select=f"""
            SELECT a.id, a.site_id, a.camera_id, a.detection_id, a.module_type, a.severity,
                   a.alert_code, a.title, a.created_at,
                   c.name AS camera_name, c.location AS camera_location, c.site_id AS camera_site_id,
                   c.latitude AS camera_latitude, c.longitude AS camera_longitude,
                   d.confidence AS detection_confidence,
                   l.plate_number, l.plate_confidence, l.watchlist_match AS plate_match,
                   l.direction, l.vehicle_type, l.vehicle_color,
                   f.matched_watchlist_id, f.match_confidence, f.watchlist_match AS face_match,
                   i.zone_id, i.dwell_time_seconds, z.name AS zone_name,
                   ale.zone_number AS alarm_zone_number, az.name AS alarm_zone_name,
                   az.linked_camera_id AS alarm_camera_id, ap.site_id AS alarm_site_id,
                   ap.name AS alarm_panel_name, lc.name AS alarm_camera_name,
                   lc.latitude AS alarm_camera_latitude, lc.longitude AS alarm_camera_longitude,
                   ios.site_id AS sensor_site_id, ios.name AS sensor_name, ios.location AS sensor_location,
                   ios.sensor_type,
                   CASE WHEN a.module_type = 'gps' THEN a.message_params END AS gps_params
              FROM alerts a
              LEFT JOIN cameras c ON c.id = a.camera_id
              LEFT JOIN detections d ON d.id = a.detection_id
              LEFT JOIN lpr_events l ON a.module_type = 'lpr' AND l.detection_id = a.detection_id
              LEFT JOIN face_events f ON a.module_type = 'face' AND f.detection_id = a.detection_id
              LEFT JOIN intrusion_events i ON a.module_type = 'intrusion' AND i.detection_id = a.detection_id
              LEFT JOIN restricted_zones z ON z.id = i.zone_id
              LEFT JOIN LATERAL (
                    SELECT e.panel_id, e.zone_id, e.zone_number FROM alarm_events e
                     WHERE a.module_type = 'alarm' AND e.alert_id = a.id
                     ORDER BY e.occurred_at LIMIT 1) ale ON TRUE
              LEFT JOIN alarm_zones az ON az.id = ale.zone_id
              LEFT JOIN alarm_panels ap ON ap.id = ale.panel_id
              LEFT JOIN cameras lc ON lc.id = az.linked_camera_id
              LEFT JOIN iot_sensors ios ON a.module_type = 'iot'
                                       AND ios.id::text = a.message_params->>'sensor_id'
             WHERE a.created_at > :floor
               AND a.module_type NOT IN ({", ".join(repr(m) for m in NOT_SECURITY)})
               AND NOT (a.module_type = 'drone_patrol' AND a.message_params->>'event_id' IS NOT NULL)
               AND {_NOT_YET.format(table="alerts", alias="a")}
             ORDER BY a.created_at, a.id
             LIMIT :batch
        """),
    Source(
        name="drone_events", time_column="written_at", normalise=from_drone_event,
        # Only what the drone module has verified: an unverified sighting has
        # raised nothing there and raises nothing here.
        select=f"""
            SELECT de.*, COALESCE(de.verified_at, de.created_at) AS written_at
              FROM drone_events de
             WHERE de.verification_state = 'VERIFIED'
               AND COALESCE(de.verified_at, de.created_at) > :floor
               AND {_NOT_YET.format(table="drone_events", alias="de")}
             ORDER BY COALESCE(de.verified_at, de.created_at), de.id
             LIMIT :batch
        """),
    Source(
        name="guard_sos", time_column="created_at", normalise=from_guard_sos,
        select=f"""
            SELECT i.id, i.severity, i.created_at, i.message_params,
                   sh.site_id AS shift_site_id, st.name AS shift_site_name
              FROM incidents i
              LEFT JOIN LATERAL (
                    SELECT s.site_id FROM shifts s
                     WHERE s.guard_user_id::text = i.message_params->>'guard_user_id'
                       AND COALESCE(s.actual_start, s.scheduled_start) <= i.created_at
                       AND COALESCE(s.actual_end, s.scheduled_end) >= i.created_at
                     ORDER BY COALESCE(s.actual_start, s.scheduled_start) DESC
                     LIMIT 1) sh ON TRUE
              LEFT JOIN sites st ON st.id = sh.site_id
             WHERE i.alert_code = 'guard.sos'
               AND i.created_at > :floor
               AND {_NOT_YET.format(table="incidents", alias="i")}
             ORDER BY i.created_at, i.id
             LIMIT :batch
        """),
    Source(
        name="virtual_patrol", time_column="answered_at", normalise=from_patrol_exception,
        select=f"""
            SELECT ans.id, ans.answered_at, ans.answered_by_user_id, ans.exception_reason, ans.incident_id,
                   q.question_text, q.failure_action,
                   sc.camera_id, sc.camera_name, sc.session_id,
                   vs.site_id, vs.patrol_number,
                   c.latitude AS camera_latitude, c.longitude AS camera_longitude
              FROM virtual_patrol_session_answers ans
              JOIN virtual_patrol_session_questions q ON q.id = ans.session_question_id
              JOIN virtual_patrol_session_cameras sc ON sc.id = q.session_camera_id
              JOIN virtual_patrol_sessions vs ON vs.id = sc.session_id
              LEFT JOIN cameras c ON c.id = sc.camera_id
             WHERE ans.is_exception
               AND ans.answered_at > :floor
               AND {_NOT_YET.format(table="virtual_patrol_session_answers", alias="ans")}
             ORDER BY ans.answered_at, ans.id
             LIMIT :batch
        """),
    Source(
        name="camera_health", time_column="occurred_at", normalise=from_camera_health,
        select=f"""
            SELECT h.id, h.camera_id, h.event_type, h.detail, h.occurred_at,
                   c.name AS camera_name, c.location AS camera_location, c.site_id AS camera_site_id,
                   c.latitude AS camera_latitude, c.longitude AS camera_longitude
              FROM camera_health_events h
              LEFT JOIN cameras c ON c.id = h.camera_id
             WHERE h.event_type = 'stream_disconnected'
               AND h.occurred_at > :floor
               AND {_NOT_YET.format(table="camera_health_events", alias="h")}
             ORDER BY h.occurred_at, h.id
             LIMIT :batch
        """),
)

_INSERT = text("""
    INSERT INTO security_events
           (tenant_id, site_id, source_type, source_table, source_id, event_type, occurred_at,
            camera_id, drone_id, alert_id, incident_id, detection_id, subject_kind, subject_ref,
            subject_verdict, confidence, severity, title, latitude, longitude, location_label, attributes)
    VALUES (current_setting('app.current_tenant')::uuid, :site_id, :source_type, :source_table, :source_id,
            :event_type, :occurred_at, :camera_id, :drone_id, :alert_id, :incident_id, :detection_id,
            :subject_kind, :subject_ref, :subject_verdict, :confidence, :severity, :title, :latitude,
            :longitude, :location_label, CAST(:attributes AS jsonb))
    ON CONFLICT (tenant_id, source_table, source_id) DO NOTHING
    RETURNING id
""")


async def _scope(db: AsyncSession, tenant_id) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def _floor(db: AsyncSession, source: str, now: datetime) -> datetime:
    """Where this tenant's source is read from, starting the cursor if it has none."""
    row = (await db.execute(text(
        "INSERT INTO security_ingest_cursors (tenant_id, source, read_from) "
        "VALUES (current_setting('app.current_tenant')::uuid, :s, :f) "
        "ON CONFLICT (tenant_id, source) DO UPDATE SET source = EXCLUDED.source "
        "RETURNING read_from"), {"s": source, "f": now - BACKFILL})).first()
    return row.read_from


async def ingest_source(db: AsyncSession, tenant_id, source: Source, now: datetime,
                        batch: int = BATCH) -> int:
    """Read one source for one tenant, once. Returns how many events were new.
    Commits; the caller's tenant scope ends with it."""
    await _scope(db, tenant_id)
    floor = await _floor(db, source.name, now)
    rows = (await db.execute(text(source.select), {"floor": floor, "batch": batch})).mappings().all()
    new = 0
    for row in rows:
        n = source.normalise(row)
        params = asdict(n)
        params["attributes"] = json.dumps(params["attributes"], default=str)
        if (await db.execute(_INSERT, params)).first() is not None:
            new += 1
    if len(rows) >= batch:
        # More waiting. Everything older than this batch's first row is done —
        # but not everything stamped at the same instant, so stop just short of it.
        read_from = max(floor, rows[0][source.time_column] - timedelta(microseconds=1))
    else:
        # Caught up: next time, from a little before now.
        read_from = max(floor, now - OVERLAP)
    await db.execute(text(
        "UPDATE security_ingest_cursors SET read_from = :f, last_run_at = :n, "
        "       last_count = CAST(:c AS integer), total_count = total_count + CAST(:c AS integer), "
        "       last_error = NULL, updated_at = :n "
        " WHERE source = :s"), {"f": read_from, "n": now, "c": new, "s": source.name})
    await db.commit()
    return new


async def _record_failure(factory, tenant_id, source: str, now: datetime, exc: Exception) -> None:
    """Say on the cursor that this source could not be read, so the status page
    shows it. Only the kind of error: its text can carry row contents."""
    async with factory() as db:
        await _scope(db, tenant_id)
        await _floor(db, source, now)
        await db.execute(text(
            "UPDATE security_ingest_cursors SET last_error = :e, last_run_at = :n, updated_at = :n "
            " WHERE source = :s"), {"e": type(exc).__name__, "n": now, "s": source})
        await db.commit()


async def database_now(db: AsyncSession) -> datetime:
    """The database's clock, which stamped the rows being read. Reading with the
    application's clock would skip rows whenever the two hosts disagree."""
    return (await db.execute(text("SELECT now()"))).scalar_one()


async def ingest_tenant(factory, tenant_id, now: datetime | None = None, batch: int = BATCH) -> dict:
    """Every source for one tenant. A source that fails is recorded and the
    others still run: a broken join on one table must not stop alerts arriving.
    Returns {source: new events} with failures as -1."""
    counts: dict[str, int] = {}
    async with factory() as db:
        if now is None:
            now = await database_now(db)
    for source in SOURCES:
        try:
            async with factory() as db:
                counts[source.name] = await ingest_source(db, tenant_id, source, now, batch)
        except Exception as exc:  # noqa: BLE001 — one source's failure is not the others'
            counts[source.name] = -1
            try:
                await _record_failure(factory, tenant_id, source.name, now, exc)
            except Exception:  # noqa: BLE001 — the database itself may be what failed
                pass
    return counts
