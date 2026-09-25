"""The central side of a site edge gateway: recognising it, handing it its work,
and taking in everything it recorded — on time or hours late.

A gateway flies the drones at its site with the provider adapter installed
there (the brief: provider code stays out of the central API). It keeps flying
through a lost link and buffers everything; this module is where that buffer
lands. Every rule the central runner applies to a flight it flies itself is
applied here through the same functions (drone_sessions), so where a drone was
flown from never changes what its record says.

IDEMPOTENT, ITEM BY ITEM.
  - A flight update is applied only if its number is above the session's
    edge_seq — resent updates are no-ops, and updates cannot land out of order.
  - Telemetry is unique on (drone_id, recorded_at); events and media on the
    gateway's own client_ref; a command is closed once.
  - A whole batch resent because its answer was lost gets the stored answer
    back from drone_sync_receipts instead of being processed again.

ONE BAD ITEM IS ONE REJECTED ITEM. Each item runs in its own savepoint. A value
the database refuses rejects that item, with a reason, and the rest of the batch
lands — otherwise one malformed sample would fail every retry of its batch
forever and the gateway's buffer would never drain. A failure that is not about
the item (the database going away) fails the batch, and the gateway retries it.

A LATE RECORD CORRECTS A GUESS. If a gateway stays silent long enough, the runner
closes its flight as FAILED / EDGE_UNREACHABLE. When the gateway reconnects with
the real flight, the real record — waypoints, telemetry, outcome — replaces that
guess. No other ended session is ever reopened.

EVERYTHING HERE RUNS INSIDE ONE TRANSACTION THE CALLER OWNS, with the tenant
set, and announces nothing: realtime events are returned for the caller to
publish after its commit.
"""
from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.drone_module import entitlement_problem, load_entitlement
from app.services import drone_flight_plan as fp
from app.services import drone_ai_pipeline as pipeline
from app.services import drone_sessions as ds
from app.services import recording_policy
from app.services.drone_edge_wire import SyncBatch, parse_key, to_flight_update  # noqa: F401  (parse_key re-exported)
from app.services.drone_providers import DroneHealth

#: A gateway whose clock is further out than this is DEGRADED: telemetry and
#: event times are the site's, and a wrong clock makes every one of them wrong.
CLOCK_TOLERANCE_S = 30.0
#: Backlog older than this, or free storage below this, is DEGRADED too.
BACKLOG_TOLERANCE = timedelta(minutes=5)
MIN_FREE_STORAGE_PCT = 10.0
UPLOADS_PER_SYNC = 10
#: The site's recording policy defaults (recording_policies, migration 0079),
#: used when a site has no policy row.
DEFAULT_CLIP_PRE_S, DEFAULT_CLIP_POST_S = 20, 60
#: When neither the mission nor the site says, the brief's default: the full
#: recording stays at the site, important events go to the centre.
DRONE_DEFAULT_SYNC_MODE = "incident_only"
_IN_FLIGHT_SQL = ", ".join(f"'{s}'" for s in ds.IN_FLIGHT)
_AIRBORNE_SQL = ", ".join(f"'{s}'" for s in ds.AIRBORNE)


# ── Recognising a gateway ────────────────────────────────────────────────────

async def find_gateway(db: AsyncSession, digest: str) -> dict | None:
    """The gateway holding this credential, in the tenant already set."""
    row = (await db.execute(text(
        "SELECT * FROM drone_edge_gateways WHERE credential_hash = :h"), {"h": digest})).mappings().first()
    if row is None or not hmac.compare_digest(row["credential_hash"] or "", digest):
        return None
    return dict(row)


# ── Policy ───────────────────────────────────────────────────────────────────

def media_wanted_centrally(sync_mode: str | None, *, event_linked: bool) -> bool:
    """Whether a drone file's bytes should reach the centre.

      central                  everything
      incident_only, scheduled event media; full flight recordings stay at site
      local_only, manual       nothing automatically
    """
    mode = sync_mode or DRONE_DEFAULT_SYNC_MODE
    if mode == "central":
        return True
    if mode in ("incident_only", "scheduled"):
        return event_linked
    return False


def in_window(now_local: time, start: time | None, end: time | None) -> bool:
    """Inside a site-local window; one that ends before it starts runs overnight."""
    if start is None or end is None or start == end:
        return True
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end


async def _site_policy(db: AsyncSession, site_id) -> dict | None:
    p = await recording_policy.get_policy(db, str(site_id))
    return p if (p and p.get("is_active")) else None


async def _tenant_tz(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


# ── The batch ────────────────────────────────────────────────────────────────

class _Rejected(Exception):
    """An item that can never be accepted. The gateway drops it."""


class _Duplicate(Exception):
    """An item already applied. The gateway drops it."""


class _Ignored(Exception):
    """Received and judged not worth recording — a sighting the flight's
    security profile does not look for. Not an error; the reason is returned."""


def _blank() -> dict:
    return {"accepted": 0, "duplicates": 0, "rejected": [], "ignored": []}


async def _item(db: AsyncSession, out: ds.Announcements, tally: dict, ref: dict, fn) -> None:
    """Run one item in its own savepoint; count it; keep its announcements only
    if it landed."""
    mine = ds.Announcements()
    try:
        async with db.begin_nested():
            await fn(mine)
        tally["accepted"] += 1
        out.extend(mine)
    except _Duplicate:
        tally["duplicates"] += 1
    except _Ignored as exc:
        tally.setdefault("ignored", []).append({**ref, "reason": str(exc)})
    except _Rejected as exc:
        tally["rejected"].append({**ref, "reason": str(exc)})
    except (IntegrityError, DataError) as exc:
        tally["rejected"].append({**ref, "reason": f"Refused by the database: {exc.orig.__class__.__name__}"})


async def process_batch(db: AsyncSession, gw: dict, batch: SyncBatch, now: datetime,
                        out: ds.Announcements) -> dict:
    """Apply a batch; return the answer the gateway acts on (and the one a resend
    of the same batch will get back)."""
    stored = (await db.execute(text(
        "SELECT result FROM drone_sync_receipts WHERE gateway_id = :g AND batch_id = :b"),
        {"g": gw["id"], "b": batch.batch_id})).scalar()
    await _record_gateway_state(db, gw, batch, now, out)
    if stored is not None:
        return {**stored, "duplicate_batch": True}

    result = {"batch_id": str(batch.batch_id), "duplicate_batch": False,
              "health": _blank(), "updates": _blank(), "commands": _blank(),
              "events": _blank(), "media": _blank(), "corrected_sessions": [],
              "media_upload_requested": []}

    for h in batch.health:
        await _item(db, out, result["health"], {"drone_id": str(h.drone_id)},
                    lambda o, h=h: _apply_health(db, gw, h, o))
    for u in sorted(batch.updates, key=lambda u: (str(u.session_id), u.seq)):
        await _item(db, out, result["updates"], {"session_id": str(u.session_id), "seq": u.seq},
                    lambda o, u=u: _apply_update(db, gw, u, o, result["corrected_sessions"]))
    for c in batch.commands:
        await _item(db, out, result["commands"], {"command_id": str(c.command_id)},
                    lambda o, c=c: _apply_command(db, gw, c, o))
    for e in batch.events:
        await _item(db, out, result["events"], {"client_ref": str(e.client_ref)},
                    lambda o, e=e: _apply_event(db, gw, e, o, now))
    for m in batch.media:
        await _item(db, out, result["media"], {"client_ref": str(m.client_ref)},
                    lambda o, m=m: _apply_media(db, gw, m, o, result["media_upload_requested"]))

    if batch.item_count():
        counts = [result[k] for k in ("health", "updates", "commands", "events", "media")]
        await db.execute(text("""
            INSERT INTO drone_sync_receipts
                (tenant_id, gateway_id, batch_id, edge_sent_at, clock_offset_s, item_count,
                 accepted, duplicates, rejected, result)
            VALUES (current_setting('app.current_tenant')::uuid, :g, :b, :sent, :off, :n,
                    :acc, :dup, :rej, CAST(:res AS jsonb))
            ON CONFLICT (gateway_id, batch_id) DO NOTHING
        """), {"g": gw["id"], "b": batch.batch_id, "sent": batch.sent_at,
               "off": round((batch.sent_at - now).total_seconds(), 3), "n": batch.item_count(),
               "acc": sum(c["accepted"] for c in counts), "dup": sum(c["duplicates"] for c in counts),
               "rej": sum(len(c["rejected"]) for c in counts), "res": json.dumps(result)})
        await db.execute(text("UPDATE drone_edge_gateways SET last_sync_at = :now WHERE id = :g"),
                         {"now": now, "g": gw["id"]})
        out.add("drone_sync_completed", {"gateway_id": str(gw["id"]), "batch_id": str(batch.batch_id),
                                         "items": batch.item_count(),
                                         "rejected": sum(len(c["rejected"]) for c in counts)})
    return result


async def _record_gateway_state(db: AsyncSession, gw: dict, batch: SyncBatch, now: datetime,
                                out: ds.Announcements) -> None:
    """Hearing from a gateway at all is its heartbeat."""
    st = batch.state
    offset = (batch.sent_at - now).total_seconds()
    problems = []
    if abs(offset) > CLOCK_TOLERANCE_S:
        problems.append(f"clock is {offset:+.0f} s from the server's")
    if st.storage_free_pct is not None and st.storage_free_pct < MIN_FREE_STORAGE_PCT:
        problems.append(f"only {st.storage_free_pct:.0f}% storage free")
    if st.oldest_buffered_at is not None and now - st.oldest_buffered_at > BACKLOG_TOLERANCE:
        problems.append(f"{st.buffer_depth} item(s) waiting since {st.oldest_buffered_at:%H:%M:%S} UTC")
    status = "DEGRADED" if problems else "ONLINE"
    await db.execute(text("""
        UPDATE drone_edge_gateways
           SET status = :st, last_seen_at = :now, software_version = COALESCE(:ver, software_version),
               buffer_depth = :depth, oldest_buffered_at = :oldest, storage_free_pct = :free,
               clock_offset_s = :off, health = CAST(:health AS jsonb), offline_alerted_at = NULL,
               updated_at = now()
         WHERE id = :g
    """), {"st": status, "now": now, "ver": batch.software_version, "depth": st.buffer_depth,
           "oldest": st.oldest_buffered_at, "free": st.storage_free_pct, "off": round(offset, 3),
           "health": json.dumps({"problems": problems, "uptime_s": st.uptime_s}), "g": gw["id"]})
    if gw["status"] != status:
        out.add("drone_gateway_status_changed", {"gateway_id": str(gw["id"]), "status": status,
                                                 "previous_status": gw["status"], "problems": problems})
    gw["status"] = status


async def _apply_health(db: AsyncSession, gw: dict, h, out: ds.Announcements) -> None:
    d = (await db.execute(text("SELECT * FROM drones WHERE id = :id AND edge_gateway_id = :g FOR UPDATE"),
                          {"id": h.drone_id, "g": gw["id"]})).mappings().first()
    if d is None:
        raise _Rejected("That drone is not served by this gateway.")
    d = dict(d)
    if d["last_heartbeat_at"] is not None and h.observed_at <= d["last_heartbeat_at"]:
        raise _Duplicate()   # older than what is already known — a buffered backlog
    flying = (await db.execute(text(
        f"SELECT EXISTS (SELECT 1 FROM drone_patrol_sessions WHERE drone_id = :d AND status IN ({_AIRBORNE_SQL}))"),
        {"d": d["id"]})).scalar()
    if flying:
        raise _Duplicate()   # a flight's own updates speak for a drone in the air
    await ds.apply_health(db, d, DroneHealth(
        observed_at=h.observed_at, battery_level=h.battery_level, battery_health=h.battery_health,
        gps_status=h.gps_status, communication_status=h.communication_status, camera_status=h.camera_status,
        storage_status=h.storage_status, temperature_c=h.temperature_c, latitude=h.latitude,
        longitude=h.longitude, altitude_m=h.altitude_m, status_hint=h.status_hint), out)


async def _apply_update(db: AsyncSession, gw: dict, u, out: ds.Announcements, corrected: list) -> None:
    s = (await db.execute(text("SELECT * FROM drone_patrol_sessions WHERE id = :id FOR UPDATE"),
                          {"id": u.session_id})).mappings().first()
    if s is None or s["edge_gateway_id"] != gw["id"]:
        raise _Rejected("That session is not flown by this gateway.")
    s = dict(s)
    if s["edge_claimed_at"] is None:
        raise _Rejected("That session was never claimed by this gateway.")
    if u.seq <= s["edge_seq"]:
        raise _Duplicate()
    up = to_flight_update(u.update)
    if s["status"] in ds.TERMINAL:
        if s["failure_code"] != "EDGE_UNREACHABLE":
            raise _Rejected(f"The session has already ended ({s['status'].lower()}).")
        # The gateway is catching up on a flight the server had given up on.
        # Record what really happened; the status stays until the real outcome.
        if up.outcome:
            await db.execute(text(
                "UPDATE drone_patrol_sessions SET failure_code = NULL, failure_reason = NULL WHERE id = :id"),
                {"id": s["id"]})
            s["failure_code"] = None
            if str(s["id"]) not in corrected:
                corrected.append(str(s["id"]))
        else:
            up.phase = "LANDED"   # not a status: record_update leaves the status alone
    await ds.record_update(db, s, up, u.at, out, launched=u.launched)
    await db.execute(text("UPDATE drone_patrol_sessions SET edge_seq = :q WHERE id = :id"),
                     {"q": u.seq, "id": s["id"]})


async def _apply_command(db: AsyncSession, gw: dict, c, out: ds.Announcements) -> None:
    cmd = (await db.execute(text("""
        SELECT c.*, s.edge_gateway_id FROM drone_session_commands c
          JOIN drone_patrol_sessions s ON s.id = c.session_id
         WHERE c.id = :id FOR UPDATE OF c
    """), {"id": c.command_id})).mappings().first()
    if cmd is None or cmd["edge_gateway_id"] != gw["id"]:
        raise _Rejected("That command is not for a session this gateway flies.")
    if cmd["status"] != "PENDING":
        raise _Duplicate()
    await ds.finish_command(db, dict(cmd), c.status, c.result, c.at, out)


async def _owned_drone(db: AsyncSession, gw: dict, drone_id, session_id) -> tuple[dict, dict | None]:
    """The drone and session an event or file belongs to — both must be this
    gateway's. A session it flew counts even if the drone has since moved."""
    session = None
    if session_id is not None:
        session = (await db.execute(text(
            "SELECT id, site_id, drone_id, edge_gateway_id, "
            "       config_snapshot->'mission'->>'recording_sync_mode' AS recording_sync_mode_snapshot "
            "  FROM drone_patrol_sessions WHERE id = :id"), {"id": session_id})).mappings().first()
        if session is None or session["edge_gateway_id"] != gw["id"]:
            raise _Rejected("That session is not flown by this gateway.")
        session = dict(session)
    drone = (await db.execute(text("SELECT id, site_id, edge_gateway_id FROM drones WHERE id = :id"),
                              {"id": drone_id})).mappings().first() if drone_id is not None else None
    if drone is None:
        raise _Rejected("Unknown drone.")
    if session is not None and session["drone_id"] != drone["id"]:
        raise _Rejected("The session was not flown by that drone.")
    if session is None and drone["edge_gateway_id"] != gw["id"]:
        raise _Rejected("That drone is not served by this gateway.")
    return dict(drone), session


async def _apply_event(db: AsyncSession, gw: dict, e, out: ds.Announcements, now: datetime) -> None:
    """A sighting from the site goes through the same context, risk and
    verification as a central detection (drone_ai_pipeline)."""
    await _owned_drone(db, gw, e.drone_id, e.session_id)
    if (await db.execute(text("SELECT 1 FROM drone_events WHERE client_ref = :r"), {"r": e.client_ref})).first():
        raise _Duplicate()           # recorded before phase 6, as its own event
    attrs = dict(e.attributes or {})
    if e.detection_ref is not None:
        attrs["edge_detection_ref"] = str(e.detection_ref)
    if e.observed_seconds is not None:
        attrs["edge_observed_seconds"] = e.observed_seconds
    outcome, _, why = await pipeline.observe(db, pipeline.ObservationIn(
        source="EDGE", module_type=e.module_type, detected_at=e.detected_at, drone_id=str(e.drone_id),
        confidence=e.ai_confidence, label=e.label, watchlist=e.watchlist, attributes=attrs,
        session_id=str(e.session_id) if e.session_id else None, client_ref=str(e.client_ref),
        latitude=e.drone_latitude, longitude=e.drone_longitude, altitude_m=e.drone_altitude_m,
        waypoint_sequence=e.waypoint_sequence), now, out)
    if outcome == "duplicate":
        raise _Duplicate()
    if outcome == "ignored":
        raise _Ignored(f"Not recorded: {why}.")


async def _apply_media(db: AsyncSession, gw: dict, m, out: ds.Announcements, wanted_refs: list) -> None:
    event_id, session_id, drone_id = None, m.session_id, None
    if m.event_client_ref is not None:
        # The gateway names the sighting; the sighting belongs to an event.
        ev = (await db.execute(text(
            "SELECT e.id, e.session_id, e.drone_id FROM drone_observations o "
            "  JOIN drone_events e ON e.id = o.event_id WHERE o.client_ref = :r "
            "UNION ALL SELECT id, session_id, drone_id FROM drone_events WHERE client_ref = :r LIMIT 1"),
            {"r": m.event_client_ref})).mappings().first()
        if ev is None:
            raise _Rejected("No event was recorded for the sighting this file belongs to "
                            "(not received yet, or not recorded — see the events' answer).")
        event_id, drone_id = ev["id"], ev["drone_id"]
        session_id = session_id or ev["session_id"]
    if drone_id is None and session_id is not None:
        drone_id = (await db.execute(text("SELECT drone_id FROM drone_patrol_sessions WHERE id = :s"),
                                     {"s": session_id})).scalar()
    drone, session = await _owned_drone(db, gw, drone_id, session_id)
    site_policy = await _site_policy(db, (session or drone)["site_id"])
    mode = ((session or {}).get("recording_sync_mode_snapshot")
            or (site_policy or {}).get("sync_mode"))
    wanted = media_wanted_centrally(mode, event_linked=event_id is not None)
    row = (await db.execute(text("""
        INSERT INTO drone_event_media
            (tenant_id, event_id, session_id, waypoint_sequence, media_kind, storage_path, storage_location,
             sync_state, checksum_sha256, size_bytes, duration_seconds, captured_at, telemetry_snapshot,
             client_ref, edge_gateway_id)
        VALUES (current_setting('app.current_tenant')::uuid, :ev, :s, :wp, :kind, :path, 'local',
                :sync, :sum, :size, :dur, :at, CAST(:tel AS jsonb), :ref, :g)
        ON CONFLICT (client_ref) DO NOTHING
        RETURNING id
    """), {"ev": event_id, "s": session_id, "wp": m.waypoint_sequence, "kind": m.media_kind,
           "path": f"edge://{gw['id']}/{m.client_ref}", "sync": "pending" if wanted else "not_required",
           "sum": m.checksum_sha256, "size": m.size_bytes, "dur": m.duration_seconds, "at": m.captured_at,
           "tel": json.dumps(fp.jsonable(m.telemetry_snapshot)) if m.telemetry_snapshot is not None else None,
           "ref": m.client_ref, "g": gw["id"]})).first()
    if row is None:
        mine = (await db.execute(text("SELECT sync_state FROM drone_event_media WHERE client_ref = :r"),
                                 {"r": m.client_ref})).first()
        if mine:
            if mine[0] in ("pending", "failed", "uploading"):
                wanted_refs.append(str(m.client_ref))   # a resend must not look unwanted
            raise _Duplicate()
        raise _Rejected("That client_ref is already used by another record.")
    if wanted:
        wanted_refs.append(str(m.client_ref))
    out.add("drone_media_recorded", {"media_id": str(row[0]), "event_id": str(event_id) if event_id else None,
                                     "session_id": str(session_id) if session_id else None,
                                     "media_kind": m.media_kind, "held_at": "site",
                                     "upload_requested": wanted})


# ── The assignment ───────────────────────────────────────────────────────────

async def assignment(db: AsyncSession, gw: dict, have_sessions: list, now: datetime) -> dict:
    """What this gateway should be doing: its drones, the sessions it flies or
    may claim, the commands waiting for it and the files the centre wants.
    Never a secret — provider credentials are installed on the gateway itself."""
    ent = await load_entitlement(db)
    policy = await _site_policy(db, gw["site_id"])
    pre, post = await recording_policy.get_effective_clip_bounds(
        db, str(gw["site_id"]), DEFAULT_CLIP_PRE_S, DEFAULT_CLIP_POST_S)
    tz = await _tenant_tz(db)
    drones = [dict(r) for r in (await db.execute(text("""
        SELECT d.id, d.code, d.name, d.status, d.provider_drone_ref, d.heartbeat_timeout_seconds,
               d.battery_level, d.last_heartbeat_at, d.current_latitude, d.current_longitude,
               d.current_altitude_m,
               d.provider_config_id, p.provider_key, p.config AS provider_config,
               COALESCE(p.is_active, FALSE) AS provider_active
          FROM drones d LEFT JOIN drone_provider_configs p ON p.id = d.provider_config_id
         WHERE d.edge_gateway_id = :g AND d.status NOT IN ('DISABLED')
         ORDER BY d.code
    """), {"g": gw["id"]})).mappings().all()]
    known = {str(x) for x in have_sessions}
    sessions = []
    for r in (await db.execute(text(f"""
        SELECT id, status, drone_id, mission_name, edge_claimed_at, edge_seq, provider_state,
               provider_mission_ref, config_snapshot
          FROM drone_patrol_sessions
         WHERE edge_gateway_id = :g AND status IN ({_IN_FLIGHT_SQL})
         ORDER BY created_at
    """), {"g": gw["id"]})).mappings().all():
        item = {"id": str(r["id"]), "status": r["status"], "drone_id": str(r["drone_id"]),
                "mission_name": r["mission_name"], "claimed": r["edge_claimed_at"] is not None,
                "edge_seq": r["edge_seq"], "provider_mission_ref": r["provider_mission_ref"]}
        if item["claimed"]:
            item["provider_state"] = r["provider_state"]
        if item["id"] not in known:
            item["config_snapshot"] = r["config_snapshot"]
        sessions.append(item)
    commands = [dict(r) for r in (await db.execute(text(f"""
        UPDATE drone_session_commands c SET delivered_at = COALESCE(c.delivered_at, :now)
          FROM drone_patrol_sessions s
         WHERE s.id = c.session_id AND s.edge_gateway_id = :g AND s.edge_claimed_at IS NOT NULL
           AND s.status IN ({_IN_FLIGHT_SQL}) AND c.status = 'PENDING'
        RETURNING c.id, c.session_id, c.command, c.reason, c.requested_at
    """), {"g": gw["id"], "now": now})).mappings().all()]
    commands.sort(key=lambda c: c["requested_at"])

    uploads = []
    window_open = True
    if policy and policy["sync_mode"] == "scheduled":
        window_open = in_window(now.astimezone(tz).time(), policy["sync_window_start"], policy["sync_window_end"])
    if window_open:
        uploads = [str(r) for r in (await db.execute(text("""
            SELECT client_ref FROM drone_event_media
             WHERE edge_gateway_id = :g AND sync_state IN ('pending','failed') AND client_ref IS NOT NULL
             ORDER BY captured_at LIMIT :n
        """), {"g": gw["id"], "n": UPLOADS_PER_SYNC})).scalars().all()]

    return fp.jsonable({
        "server_time": now,
        "gateway": {"id": gw["id"], "code": gw["code"], "name": gw["name"], "site_id": gw["site_id"],
                    "heartbeat_timeout_seconds": gw["heartbeat_timeout_seconds"]},
        "licensed": entitlement_problem(ent, now) is None,
        "policy": {"sync_mode": (policy or {}).get("sync_mode") or DRONE_DEFAULT_SYNC_MODE,
                   "clip_pre_seconds": pre, "clip_post_seconds": post,
                   "local_retention_days": (policy or {}).get("local_retention_days"),
                   "sync_window_start": (policy or {}).get("sync_window_start"),
                   "sync_window_end": (policy or {}).get("sync_window_end"),
                   "bandwidth_limit_kbps": (policy or {}).get("bandwidth_limit_kbps"),
                   "timezone": str(tz), "upload_window_open": window_open},
        "drones": drones,
        "sessions": sessions,
        "commands": commands,
        "uploads_wanted": uploads,
    })


# ── Claiming a session ───────────────────────────────────────────────────────

class ClaimRefused(Exception):
    def __init__(self, status: int, reason: str, preflight: dict | None = None):
        super().__init__(reason)
        self.status, self.reason, self.preflight = status, reason, preflight


async def claim(db: AsyncSession, gw: dict, session_id, now: datetime, out: ds.Announcements) -> dict:
    """Take a ready session to fly. Pre-flight runs again first, with the latest
    health this gateway reported, against the session's frozen route — exactly
    the check the central runner makes before it launches. Claiming again is
    answered with the same claim."""
    s = (await db.execute(text("SELECT * FROM drone_patrol_sessions WHERE id = :id FOR UPDATE"),
                          {"id": session_id})).mappings().first()
    if s is None or s["edge_gateway_id"] != gw["id"]:
        raise ClaimRefused(404, "No such session for this gateway.")
    s = dict(s)
    if s["edge_claimed_at"] is not None and s["status"] in ds.IN_FLIGHT:
        return _claimed(s)
    if s["status"] not in ("PRECHECK", "READY"):
        raise ClaimRefused(409, f"The session is {s['status'].lower()}, not ready to fly.")
    stopping = (await db.execute(text(
        "SELECT command FROM drone_session_commands WHERE session_id = :id AND status = 'PENDING' "
        "   AND command IN ('CANCEL','ABORT','RETURN_TO_HOME') LIMIT 1"), {"id": s["id"]})).scalar()
    if stopping:
        raise ClaimRefused(409, f"An operator asked to {stopping.lower().replace('_', ' ')} this "
                                "mission before it launched.")
    result = await ds.recheck_before_launch(db, s, now, out)
    if result is None:
        blocked = (await db.execute(text(
            "SELECT blocked_reason, preflight_result FROM drone_patrol_sessions WHERE id = :id"),
            {"id": s["id"]})).mappings().first()
        raise ClaimRefused(409, blocked["blocked_reason"] or "Pre-flight refused the launch.",
                           blocked["preflight_result"])
    await db.execute(text("""
        UPDATE drone_patrol_sessions
           SET status = 'LAUNCHING', edge_claimed_at = :now, preflight_result = CAST(:p AS jsonb),
               updated_at = now()
         WHERE id = :id
    """), {"now": now, "p": json.dumps(result.as_json()), "id": s["id"]})
    out.add("drone_session_updated", {"session_id": str(s["id"]), "drone_id": str(s["drone_id"]),
                                      "status": "LAUNCHING", "previous_status": s["status"],
                                      "edge_gateway_id": str(gw["id"])})
    s.update(status="LAUNCHING", edge_claimed_at=now)
    return _claimed(s)


def _claimed(s: dict) -> dict:
    return fp.jsonable({"session": {
        "id": s["id"], "status": s["status"], "drone_id": s["drone_id"], "mission_name": s["mission_name"],
        "edge_seq": s["edge_seq"], "provider_state": s["provider_state"],
        "provider_mission_ref": s["provider_mission_ref"], "config_snapshot": s["config_snapshot"],
        "claimed_at": s["edge_claimed_at"]}})
