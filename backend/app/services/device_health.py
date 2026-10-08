"""The health of the devices the platform knows, read from what each one reports.

  camera(), recorder(), sensor(), drone(), gateway(), panel()
                 one device's state, and why, from the facts on its row. Pure.
  readings()     every device of an organisation, each with its state
  record()       keep the state of each device when it differs from the last
                 one kept (device_health_changes)
  availability() how long a device was down in a period, from what was kept

A READING IS MADE OF WHAT THE PLATFORM IS TOLD, AND OF NOTHING ELSE. A camera's
stream is online, degraded or offline, and its disconnections are counted; a
recorder answered its last probe or did not; a sensor's reading arrived within
the time one is expected in, or did not; a drone and a gateway sent a heartbeat
in time and report their own parts; an alarm panel last said it was online or
offline. Frame rate, latency, packet loss, the quality of the picture and gaps
in a recording are measured by nothing here, and `NOT_MEASURED` says so
wherever a reading is shown.

WHAT IS NOT KNOWN IS `NOT_KNOWN`, NOT `OK`. A camera with no stream, a recorder
never probed or not probed for a day, a sensor, drone, gateway or panel that
has never reported.

A SENSOR THAT READS A DANGEROUS VALUE IS A WORKING SENSOR. Its health is
whether its readings arrive, not what they say.

`record` writes; everything else only reads.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import site_scope_clause

STATES = ("OK", "DEGRADED", "DOWN", "NOT_KNOWN", "OFF")
#: The order a list is read in: what wants somebody first.
ATTENTION = {"DOWN": 0, "DEGRADED": 1, "NOT_KNOWN": 2, "OK": 3, "OFF": 4}
KINDS = ("CAMERA", "NVR", "SENSOR", "DRONE", "EDGE_GATEWAY", "ALARM_PANEL")
KIND_LABEL = {"CAMERA": "Camera", "NVR": "Recorder", "SENSOR": "Sensor", "DRONE": "Drone",
              "EDGE_GATEWAY": "Edge gateway", "ALARM_PANEL": "Alarm panel"}
#: A camera that disconnected this many times in a day is not steady, whatever it is doing now.
UNSTEADY_AT = 5
#: A probe older than this says how a recorder was, not how it is.
STALE_PROBE_S = 24 * 3600
#: A sensor is silent when no reading has come for this many times the interval one is expected in.
SILENT_AFTER = 2
#: How often the scheduler looks. An outage shorter than this can pass unrecorded.
LOOK_EVERY_S = 300
NOT_MEASURED = ("Frame rate", "Latency", "Packet loss", "The quality of the picture: darkness, glare, focus",
                "Gaps in a recording")
NOTE = ("Each reading is made from what the device or its connection reports to the platform, as listed with it. "
        "Nothing here measures frame rate, latency, packet loss, the quality of a picture or gaps in a recording.")
AVAILABILITY_NOTE = ("Worked out from the state the platform read every five minutes, and kept only when it changed. "
                     "An outage shorter than that can pass unrecorded, and nothing is known from before the first "
                     "reading that was kept.")


def _reading(state: str, *reasons: str, **facts: Any) -> dict:
    return {"state": state, "reasons": [r for r in reasons if r],
            "facts": {k: v for k, v in facts.items() if v is not None}}


def _age(moment: datetime | None, now: datetime) -> float | None:
    return None if moment is None else (now - moment).total_seconds()


def camera(row: Mapping, now: datetime) -> dict:
    """A camera, from its streams and the disconnections recorded of it."""
    facts = {"streams": row["n_streams"], "last_frame_at": row["last_frame_at"],
             "disconnects_24h": row["disconnects_24h"]}
    if not row["is_active"]:
        return _reading("OFF", "Switched off in the platform.", **facts)
    if not row["n_streams"]:
        return _reading("NOT_KNOWN", "No stream is set up for it.", **facts)
    if not row["n_online"] and not row["n_degraded"]:
        went = row["last_event_at"] if row["last_event_type"] == "stream_disconnected" else None
        return _reading("DOWN", "Every stream is offline.", went_offline_at=went, **facts)
    unsteady = (f"It disconnected {row['disconnects_24h']} times in the last 24 hours."
                if row["disconnects_24h"] >= UNSTEADY_AT else "")
    if row["n_degraded"] or unsteady:
        return _reading("DEGRADED", "A stream is degraded." if row["n_degraded"] else "", unsteady, **facts)
    return _reading("OK", **facts)


def recorder(row: Mapping, now: datetime) -> dict:
    """A recorder, from the last time somebody or something probed it."""
    age = _age(row["last_probe_at"], now)
    facts = {"last_probe_at": row["last_probe_at"], "last_probe_status": row["last_probe_status"]}
    if not row["is_active"]:
        return _reading("OFF", "Switched off in the platform.", **facts)
    if age is None:
        return _reading("NOT_KNOWN", "It has never been probed.", **facts)
    if age > STALE_PROBE_S:
        return _reading("NOT_KNOWN", "It has not been probed for more than a day. Probe it to know how it is now.", **facts)
    if row["last_probe_status"] != "ok":
        return _reading("DOWN", "The last probe failed.", **facts)
    return _reading("OK", **facts)


def sensor(row: Mapping, now: datetime) -> dict:
    """A sensor, from whether its readings arrive. Not from what they say."""
    age = _age(row["last_reading_at"], now)
    facts = {"last_reading_at": row["last_reading_at"], "expected_interval_seconds": row["expected_interval_seconds"]}
    if not row["is_active"]:
        return _reading("OFF", "Switched off in the platform.", **facts)
    if age is None:
        return _reading("NOT_KNOWN", "No reading has ever arrived.", **facts)
    if row["expected_interval_seconds"] and age > SILENT_AFTER * row["expected_interval_seconds"]:
        return _reading("DOWN", "No reading has arrived for more than twice the time one is expected in.", **facts)
    return _reading("OK", **facts)


_PARTS = (("communication_status", "Communication"), ("gps_status", "GPS"), ("camera_status", "Camera"),
          ("storage_status", "Storage"))


def drone(row: Mapping, now: datetime) -> dict:
    """A drone, from its heartbeat, the state it reports and the parts it reports on."""
    age = _age(row["last_heartbeat_at"], now)
    facts = {"last_heartbeat_at": row["last_heartbeat_at"], "reports": row["status"],
             "battery_level": row["battery_level"], "battery_health": row["battery_health"],
             "maintenance_due_at": row["next_maintenance_at"]}
    if row["status"] in ("DISABLED", "MAINTENANCE"):
        return _reading("OFF", "Disabled." if row["status"] == "DISABLED" else "In maintenance.", **facts)
    if age is None:
        return _reading("NOT_KNOWN", "It has never reported.", **facts)
    if row["heartbeat_timeout_seconds"] and age > row["heartbeat_timeout_seconds"]:
        return _reading("DOWN", "No heartbeat within the time one is expected in.", **facts)
    if row["status"] in ("OFFLINE", "COMMUNICATION_LOST", "CRITICAL"):
        return _reading("DOWN", f"It reports {row['status'].replace('_', ' ').lower()}.", **facts)
    parts = [f"{label}: {row[column].lower()}." for column, label in _PARTS if row[column] in ("WARNING", "FAULT")]
    if row["status"] == "WARNING" or parts:
        return _reading("DEGRADED", "It reports a warning." if row["status"] == "WARNING" else "", *parts, **facts)
    return _reading("OK", **facts)


def gateway(row: Mapping, now: datetime) -> dict:
    """An edge gateway, from when it was last seen and the state it reports."""
    age = _age(row["last_seen_at"], now)
    facts = {"last_seen_at": row["last_seen_at"], "reports": row["status"], "storage_free_pct": row["storage_free_pct"],
             "buffer_depth": row["buffer_depth"]}
    if not row["is_active"]:
        return _reading("OFF", "Switched off in the platform.", **facts)
    if age is None:
        return _reading("NOT_KNOWN", "It has never reported.", **facts)
    if row["heartbeat_timeout_seconds"] and age > row["heartbeat_timeout_seconds"]:
        return _reading("DOWN", "No heartbeat within the time one is expected in.", **facts)
    if row["status"] == "OFFLINE":
        return _reading("DOWN", "It reports offline.", **facts)
    if row["status"] == "DEGRADED":
        return _reading("DEGRADED", "It reports degraded.", **facts)
    if row["status"] == "UNKNOWN":
        return _reading("NOT_KNOWN", "It does not say how it is.", **facts)
    return _reading("OK", **facts)


def panel(row: Mapping, now: datetime) -> dict:
    """An alarm panel, from the last thing it said. A panel says nothing while
    nothing happens, so a quiet one is not read as down: how long ago it was
    last in contact is given with it."""
    facts = {"last_contact_at": row["last_contact_at"], "reports": row["status"]}
    if not row["is_active"]:
        return _reading("OFF", "Switched off in the platform.", **facts)
    if row["last_contact_at"] is None:
        return _reading("NOT_KNOWN", "It has never been in contact.", **facts)
    if row["status"] == "offline":
        return _reading("DOWN", "It reported itself offline.", **facts)
    return _reading("OK", **facts)


# ─── Reading every device of an organisation ─────────────────────────────────

#: For each kind: how it is read, the column of the register that names it, and
#: the rows it is read from. Every query gives id, name, site_id, site_name and
#: the asset it is, if it is in the register.
_ASSET = "LEFT JOIN asset_register a ON a.{column} = d.id LEFT JOIN sites s ON s.id = d.site_id"
_COMMON = "d.id, d.name, d.site_id, s.name AS site_name, a.id AS asset_id, a.asset_code"
_SOURCES: dict[str, tuple] = {
    "CAMERA": (camera, "camera_id", f"""
        SELECT {_COMMON}, d.is_active, st.n_streams, st.n_online, st.n_degraded, st.last_frame_at,
               h.last_event_type, h.last_event_at, x.disconnects_24h
          FROM cameras d {_ASSET.format(column='camera_id')}
          LEFT JOIN LATERAL (SELECT count(*) AS n_streams, count(*) FILTER (WHERE status = 'online') AS n_online,
                                    count(*) FILTER (WHERE status = 'degraded') AS n_degraded,
                                    max(last_frame_at) AS last_frame_at
                               FROM streams WHERE camera_id = d.id) st ON TRUE
          LEFT JOIN LATERAL (SELECT event_type AS last_event_type, occurred_at AS last_event_at
                               FROM camera_health_events WHERE camera_id = d.id
                              ORDER BY occurred_at DESC LIMIT 1) h ON TRUE
          LEFT JOIN LATERAL (SELECT count(*) AS disconnects_24h FROM camera_health_events
                              WHERE camera_id = d.id AND event_type = 'stream_disconnected'
                                AND occurred_at >= :day_ago) x ON TRUE"""),
    "SENSOR": (sensor, "iot_sensor_id", f"""
        SELECT {_COMMON}, d.is_active, d.last_reading_at, d.expected_interval_seconds
          FROM iot_sensors d {_ASSET.format(column='iot_sensor_id')}"""),
    "DRONE": (drone, "drone_id", f"""
        SELECT {_COMMON}, d.status, d.last_heartbeat_at, d.heartbeat_timeout_seconds, d.battery_level,
               d.battery_health, d.next_maintenance_at, d.communication_status, d.gps_status, d.camera_status,
               d.storage_status
          FROM drones d {_ASSET.format(column='drone_id')}"""),
    "EDGE_GATEWAY": (gateway, "edge_gateway_id", f"""
        SELECT {_COMMON}, d.is_active, d.status, d.last_seen_at, d.heartbeat_timeout_seconds, d.storage_free_pct,
               d.buffer_depth
          FROM drone_edge_gateways d {_ASSET.format(column='edge_gateway_id')}"""),
    "ALARM_PANEL": (panel, "alarm_panel_id", f"""
        SELECT {_COMMON}, d.is_active, d.status, d.last_contact_at
          FROM alarm_panels d {_ASSET.format(column='alarm_panel_id')}"""),
}
#: A recorder belongs to the organisation, not to a site.
_RECORDERS = """
    SELECT d.id, d.name, NULL::uuid AS site_id, NULL::text AS site_name, a.id AS asset_id, a.asset_code,
           d.is_active, d.last_probe_at, d.last_probe_status
      FROM nvr_connections d LEFT JOIN asset_register a ON a.nvr_connection_id = d.id"""
#: The column of the register that says which device an asset is.
LINK = {**{kind: column for kind, (_, column, _) in _SOURCES.items()}, "NVR": "nvr_connection_id"}


async def readings(db: AsyncSession, now: datetime, *, allowed: Sequence[str] | None = None, site_id: Any = None,
                   kinds: Sequence[str] | None = None, device_id: Any = None) -> list[dict]:
    """Every device the caller may see, each with its state and why, what wants
    somebody first. A recorder has no site, so somebody held to particular
    sites is not shown one."""
    wanted = [k for k in KINDS if kinds is None or k in kinds]
    out: list[dict] = []
    for kind in wanted:
        params: dict = {}
        where = []
        if kind == "NVR":
            if allowed is not None or site_id is not None:
                continue
            read, sql = recorder, _RECORDERS
        else:
            read, _, sql = _SOURCES[kind]
            scope = site_scope_clause(allowed, "d.site_id", params)
            if scope:
                where.append(scope)
            if site_id is not None:
                where.append("d.site_id = CAST(:site AS uuid)")
                params["site"] = str(site_id)
        if kind == "CAMERA":
            params["day_ago"] = now - timedelta(hours=24)
        if device_id is not None:
            where.append("d.id = CAST(:device AS uuid)")
            params["device"] = str(device_id)
        rows = (await db.execute(text(f"{sql} {('WHERE ' + ' AND '.join(where)) if where else ''}"),
                                 params)).mappings().all()
        for row in rows:
            out.append({"kind": kind, "kind_label": KIND_LABEL[kind], "device_id": row["id"], "name": row["name"],
                        "site_id": row["site_id"], "site_name": row["site_name"], "asset_id": row["asset_id"],
                        "asset_code": row["asset_code"], **read(row, now)})
    kept = await _last_kept(db)
    for r in out:
        last = kept.get((r["kind"], str(r["device_id"])))
        # Since when it has been so: a camera's own record of going offline, else when this state was first read.
        r["since"] = r["facts"].get("went_offline_at") or (last["observed_at"] if last and last["state"] == r["state"] else None)
        # The first thing ever kept of a device says when the platform began looking, not when the state began.
        r["since_is_when_first_read"] = bool("went_offline_at" not in r["facts"] and last and last["state"] == r["state"]
                                             and last["n"] == 1)
    out.sort(key=lambda r: (ATTENTION[r["state"]], r["site_name"] or "", r["kind"], r["name"] or ""))
    return out


async def _last_kept(db: AsyncSession) -> dict[tuple[str, str], dict]:
    rows = await db.execute(text("""
        SELECT DISTINCT ON (device_kind, device_id) device_kind, device_id, state, observed_at,
               count(*) OVER (PARTITION BY device_kind, device_id) AS n
          FROM device_health_changes ORDER BY device_kind, device_id, observed_at DESC, id DESC
    """))
    return {(r.device_kind, str(r.device_id)): {"state": r.state, "observed_at": r.observed_at, "n": r.n} for r in rows}


def summary(items: Sequence[Mapping]) -> dict:
    """How many devices are in each state, in all and by kind."""
    by_kind: dict[str, dict[str, int]] = {}
    for r in items:
        by_kind.setdefault(r["kind"], dict.fromkeys(STATES, 0))[r["state"]] += 1
    total = {s: sum(k[s] for k in by_kind.values()) for s in STATES}
    return {"devices": len(items), "by_state": total,
            "by_kind": [{"kind": k, "label": KIND_LABEL[k], "devices": sum(by_kind[k].values()), **by_kind[k]}
                        for k in KINDS if k in by_kind]}


async def record(db: AsyncSession, items: Sequence[Mapping], now: datetime) -> int:
    """Keep the state of each device whose state is not the one last kept. The
    caller commits. Returns how many were kept."""
    kept = await _last_kept(db)
    changed = [r for r in items if (kept.get((r["kind"], str(r["device_id"]))) or {}).get("state") != r["state"]]
    for r in changed:
        await db.execute(text("""
            INSERT INTO device_health_changes (tenant_id, device_kind, device_id, site_id, state, reasons, observed_at)
            VALUES (current_setting('app.current_tenant')::uuid, :kind, :device, :site, :state, CAST(:reasons AS jsonb), :at)
        """), {"kind": r["kind"], "device": r["device_id"], "site": r["site_id"], "state": r["state"],
               "reasons": json.dumps(r["reasons"]), "at": now})
    return len(changed)


async def history(db: AsyncSession, kind: str, device_id: Any, limit: int = 50) -> list[dict]:
    """The states kept of one device, newest first."""
    rows = await db.execute(text("""
        SELECT state, reasons, observed_at FROM device_health_changes
         WHERE device_kind = :kind AND device_id = CAST(:device AS uuid)
         ORDER BY observed_at DESC, id DESC LIMIT :limit
    """), {"kind": kind, "device": str(device_id), "limit": limit})
    return [{"state": r.state, "reasons": r.reasons if isinstance(r.reasons, list) else json.loads(r.reasons),
             "observed_at": r.observed_at} for r in rows]


def down_time(changes: Sequence[Mapping], start: datetime, now: datetime) -> dict:
    """How long a device was down between `start` and now, from the states kept
    of it (oldest first). Nothing is assumed of the time before the first one."""
    if not changes:
        return {"known_from": None, "known_seconds": 0, "down_seconds": 0, "times_down": 0}
    known_from = max(start, changes[0]["observed_at"])
    down = times = 0
    for index, change in enumerate(changes):
        until = changes[index + 1]["observed_at"] if index + 1 < len(changes) else now
        a, b = max(change["observed_at"], known_from), min(until, now)
        if change["state"] == "DOWN" and b > a:
            down += (b - a).total_seconds()
            times += 1
    return {"known_from": known_from, "known_seconds": max(0, int((now - known_from).total_seconds())),
            "down_seconds": int(down), "times_down": times}


async def availability(db: AsyncSession, kind: str, device_id: Any, days: int, now: datetime) -> dict:
    """How long one device was down in the last `days`."""
    start = now - timedelta(days=days)
    rows = (await db.execute(text("""
        (SELECT state, observed_at FROM device_health_changes
          WHERE device_kind = :kind AND device_id = CAST(:device AS uuid) AND observed_at < :start
          ORDER BY observed_at DESC, id DESC LIMIT 1)
        UNION ALL
        (SELECT state, observed_at FROM device_health_changes
          WHERE device_kind = :kind AND device_id = CAST(:device AS uuid) AND observed_at >= :start)
        ORDER BY observed_at
    """), {"kind": kind, "device": str(device_id), "start": start})).mappings().all()
    return {"days": days, **down_time(rows, start, now), "note": AVAILABILITY_NOTE}
