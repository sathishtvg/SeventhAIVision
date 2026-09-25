"""The drone runner: the one place that talks to drones.

Three jobs, each a function the worker loop calls on its own cadence and a test
can call directly with a fixed `now`:

  run_tick          every ~2 s — carry out queued commands, then advance every
                    live session: re-check and launch the ready ones, pull
                    telemetry for the airborne ones, record what happened.
  run_schedule_tick every ~30 s — create the sessions schedules owe, each judged
                    by pre-flight on creation.
  run_health_tick   every ~15 s — poll idle drones for health (their heartbeat),
                    then mark any that have gone silent as communication-lost.

ONE TENANT, ONE TRANSACTION, ONE UNIT OF WORK. Each command and each session is
processed in its own transaction with the tenant set at its start — after every
commit or rollback the setting is gone, and a statement without it fails. One
tenant's broken session never stops another's, or the next session's.

EVERY PROVIDER CALL HAS A TIMEOUT. A provider that hangs costs one session one
tick, not the runner.

ROWS ARE LOCKED WITH SKIP LOCKED, so a second runner instance works on different
sessions rather than flying the same one twice.

DRONES BEHIND A SITE EDGE GATEWAY ARE NOT FLOWN FROM HERE. A session that carries
an edge_gateway_id is claimed and flown by that gateway, which reports through
the edge sync (services/drone_edge_sync.py). The runner only watches over those
sessions: one never picked up is recorded as missed, and one whose gateway has
been silent for EDGE_STALE_AFTER is closed as failed — a verdict the gateway's
own record corrects if it catches up later. Commands for a claimed edge session
are left for the gateway to collect.

ANNOUNCED AFTER COMMIT. Alerts and realtime updates are published only once the
transaction that produced them has committed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.services import drone_flight_plan as fp
from app.services import drone_schedule as sched
from app.services import drone_sessions as ds
from app.services.drone_providers import (
    Capability, CapabilityNotSupported, DroneProvider, DroneRef, FlightContext, ProviderError,
    ProviderUnavailable, get_adapter,
)

logger = logging.getLogger(__name__)

#: How far back a schedule's missed launch is still recorded. Long enough to
#: survive a runner outage, short enough not to manufacture history.
LOOKBACK = timedelta(hours=6)
PROVIDER_TIMEOUT_S = 10.0
#: An airborne session nobody has heard from for this long is closed as failed,
#: so it stops holding the drone's one-flight lock and a person is told.
STALE_AFTER = timedelta(minutes=10)
#: A ready edge session its gateway has not claimed within this long is missed.
EDGE_CLAIM_WINDOW = timedelta(minutes=10)
#: An edge gateway keeps flying through an outage, so its silence is given far
#: longer than a directly connected drone's before the flight is given up on.
EDGE_STALE_AFTER = timedelta(minutes=60)
#: Sync receipts exist to replay an answer to a resent batch; a week is far
#: longer than any gateway retries for.
RECEIPT_RETENTION = timedelta(days=7)
LIVE_SQL = ", ".join(f"'{s}'" for s in ds.IN_FLIGHT)
AIRBORNE_SQL = ", ".join(f"'{s}'" for s in ds.AIRBORNE)
#: Statuses in which a drone is expected to be heard from.
HEARD_FROM = ("READY", "STANDBY", "CHARGING", "WARNING", "PREPARING", "CRITICAL",
              "MISSION_ACTIVE", "RETURNING")


class Publisher(Protocol):
    async def publish(self, tenant_id: str, event_type: str, payload: dict) -> None: ...


class RedisPublisher:
    """The existing realtime channel, in the existing envelope."""

    def __init__(self, redis):
        self.redis = redis

    async def publish(self, tenant_id: str, event_type: str, payload: dict) -> None:
        await self.redis.publish(f"tenant_events:{tenant_id}", json.dumps({
            "event_type": event_type, "tenant_id": str(tenant_id), "payload": payload,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }))


class ListPublisher:
    """Collects instead of publishing — for tests and dry runs."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    async def publish(self, tenant_id: str, event_type: str, payload: dict) -> None:
        self.events.append((str(tenant_id), event_type, payload))


async def _announce(pub: Publisher | None, tenant_id, out: ds.Announcements) -> None:
    if pub is None:
        return
    for event_type, payload in out.events:
        try:
            await pub.publish(str(tenant_id), event_type, payload)
        except Exception:
            # The row is committed; a missed realtime nudge is recovered by the
            # next poll. Never let publishing undo or block the work.
            logger.exception("drone runner: publish %s failed", event_type)


#: Public name for the edge router, which announces the same way.
announce = _announce


async def _scope(db: AsyncSession, tenant_id) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def _tenants(factory) -> list[str]:
    async with factory() as db:
        rows = (await db.execute(text("SELECT tenant_id FROM drone_runner_tenants()"))).scalars().all()
    return [str(r) for r in rows]


def _utc(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _adapter(provider: dict | None) -> DroneProvider:
    if not provider:
        raise ProviderUnavailable("The drone has no provider configuration.")
    if not provider.get("is_active"):
        raise ProviderUnavailable("The drone's provider configuration is disabled.")
    secrets = json.loads(decrypt_secret(provider["secret_encrypted"])) if provider.get("secret_encrypted") else {}
    return get_adapter(provider["provider_key"], provider.get("config") or {}, secrets)


def _drone_ref(d: dict) -> DroneRef:
    return DroneRef(
        id=str(d["id"]), code=d["code"], provider_drone_ref=d.get("provider_drone_ref"),
        status=d["status"], battery_level=d.get("battery_level"),
        latitude=d.get("current_latitude"), longitude=d.get("current_longitude"),
        altitude_m=float(d["current_altitude_m"]) if d.get("current_altitude_m") is not None else None,
        last_heartbeat_at=d.get("last_heartbeat_at"),
    )


def _plan_from_snapshot(session: dict) -> fp.MissionPlan | None:
    snap = session.get("config_snapshot") or {}
    if not snap.get("route") or not snap.get("waypoints"):
        return None
    return fp.build_plan(snap["route"], snap["waypoints"])


async def _call(coro):
    return await asyncio.wait_for(coro, timeout=PROVIDER_TIMEOUT_S)


async def _load_drone_and_provider(db: AsyncSession, drone_id) -> tuple[dict | None, dict | None]:
    if drone_id is None:
        return None, None
    d = (await db.execute(text("SELECT * FROM drones WHERE id = :id"), {"id": drone_id})).mappings().first()
    if d is None:
        return None, None
    p = None
    if d["provider_config_id"]:
        p = (await db.execute(text(
            "SELECT id, provider_key, config, secret_encrypted, is_active FROM drone_provider_configs "
            " WHERE id = :id"), {"id": d["provider_config_id"]})).mappings().first()
    return dict(d), (dict(p) if p else None)


_end_session = ds.end_session


# ═════════════════════════════════════════════════════════════════════════════
# Commands
# ═════════════════════════════════════════════════════════════════════════════

_BEFORE_LAUNCH = ("SCHEDULED", "PRECHECK", "READY")


async def _process_command(db: AsyncSession, cmd: dict, now: datetime, out: ds.Announcements) -> tuple[str, str]:
    session = (await db.execute(text(
        "SELECT * FROM drone_patrol_sessions WHERE id = :id FOR UPDATE"), {"id": cmd["session_id"]}
    )).mappings().first()
    if session is None:
        return "REJECTED", "The session no longer exists."
    session = dict(session)
    st, kind = session["status"], cmd["command"]

    if st in ds.TERMINAL:
        return "REJECTED", f"The session has already ended ({st.lower()})."
    if kind in ("CANCEL", "ABORT", "RETURN_TO_HOME") and st in _BEFORE_LAUNCH:
        await _end_session(db, session, "CANCELLED", now, out,
                           reason=cmd.get("reason") or "Cancelled before launch.")
        return "DONE", "Cancelled before launch."
    if kind == "CANCEL":
        return "REJECTED", "The drone has already launched; abort it or return it home instead."
    if kind in ("ABORT", "RETURN_TO_HOME") and st == "RETURNING":
        return "DONE", "The drone is already returning."
    if kind == "PAUSE" and st != "ACTIVE":
        return "REJECTED", f"Only an active mission can be paused (it is {st.lower()})."
    if kind == "RESUME" and st != "PAUSED":
        return "REJECTED", "The mission is not paused."

    drone, provider = await _load_drone_and_provider(db, session["drone_id"])
    plan = _plan_from_snapshot(session)
    if drone is None or plan is None:
        return "FAILED", "The session has no drone or no flight plan to command."
    adapter = _adapter(provider)
    ctx = FlightContext(session_id=str(session["id"]), tenant_id=str(session["tenant_id"]),
                        drone=_drone_ref(drone), plan=plan, provider_state=session["provider_state"] or {},
                        provider_mission_ref=session.get("provider_mission_ref"))
    method = {"PAUSE": adapter.pause_mission, "RESUME": adapter.resume_mission,
              "ABORT": adapter.abort_mission, "RETURN_TO_HOME": adapter.return_to_home}[kind]
    up = await _call(method(ctx, now))
    await ds.record_update(db, session, up, now, out)
    return "DONE", {"PAUSE": "Holding position.", "RESUME": "Mission resumed.",
                    "ABORT": "Aborted; the drone is returning home.",
                    "RETURN_TO_HOME": "The drone is returning home."}[kind]


async def process_commands(factory, pub: Publisher | None, tenant_id: str, now: datetime) -> dict:
    counts = {"done": 0, "rejected": 0, "failed": 0}
    async with factory() as db:
        await _scope(db, tenant_id)
        # A claimed edge session's commands are the gateway's to collect.
        ids = (await db.execute(text(
            "SELECT c.id FROM drone_session_commands c JOIN drone_patrol_sessions s ON s.id = c.session_id "
            " WHERE c.status = 'PENDING' AND (s.edge_gateway_id IS NULL OR s.edge_claimed_at IS NULL) "
            " ORDER BY c.requested_at"
        ))).scalars().all()
        await db.rollback()
        for cid in ids:
            out = ds.Announcements()
            try:
                await _scope(db, tenant_id)
                cmd = (await db.execute(text(
                    "SELECT * FROM drone_session_commands WHERE id = :id AND status = 'PENDING' "
                    "   FOR UPDATE SKIP LOCKED"), {"id": cid})).mappings().first()
                if cmd is None:
                    await db.rollback()
                    continue
                cmd = dict(cmd)
                claimed = (await db.execute(text(
                    "SELECT edge_claimed_at IS NOT NULL AND edge_gateway_id IS NOT NULL "
                    "  FROM drone_patrol_sessions WHERE id = :id FOR UPDATE"), {"id": cmd["session_id"]})).scalar()
                if claimed:
                    # Claimed by its gateway since the list was read.
                    await db.rollback()
                    continue
                try:
                    status, result = await _process_command(db, cmd, now, out)
                except CapabilityNotSupported as exc:
                    status, result = "REJECTED", str(exc)
                except (ProviderError, asyncio.TimeoutError) as exc:
                    status = "FAILED"
                    result = str(exc) or "The provider did not respond in time."
                await ds.finish_command(db, cmd, status, result, now, out)
                await db.commit()
                await _announce(pub, tenant_id, out)
                counts[status.lower()] = counts.get(status.lower(), 0) + 1
            except Exception:
                await db.rollback()
                logger.exception("drone runner: command %s failed", cid)
    return counts


# ═════════════════════════════════════════════════════════════════════════════
# Flights
# ═════════════════════════════════════════════════════════════════════════════

async def _launch(db: AsyncSession, session: dict, now: datetime, out: ds.Announcements) -> str:
    """Re-check with fresh facts against the FROZEN route, then launch."""
    result = await ds.recheck_before_launch(db, session, now, out)
    if result is None:
        return "BLOCKED"

    drone, provider = await _load_drone_and_provider(db, session["drone_id"])
    plan = _plan_from_snapshot(session)
    adapter = _adapter(provider)
    ctx = FlightContext(session_id=str(session["id"]), tenant_id=str(session["tenant_id"]),
                        drone=_drone_ref(drone), plan=plan)
    up = await _call(adapter.start_mission(ctx, now))
    await db.execute(text("UPDATE drone_patrol_sessions SET preflight_result = CAST(:p AS jsonb) WHERE id = :id"),
                     {"p": json.dumps(result.as_json()), "id": session["id"]})
    return await ds.record_update(db, session, up, now, out, launched=True)


async def _fly(db: AsyncSession, session: dict, now: datetime, out: ds.Announcements) -> str:
    drone, provider = await _load_drone_and_provider(db, session["drone_id"])
    plan = _plan_from_snapshot(session)
    if drone is None or plan is None:
        await _end_session(db, session, "FAILED", now, out,
                           reason="The drone or its flight plan disappeared mid-flight.")
        return "FAILED"
    adapter = _adapter(provider)
    ctx = FlightContext(session_id=str(session["id"]), tenant_id=str(session["tenant_id"]),
                        drone=_drone_ref(drone), plan=plan, provider_state=session["provider_state"] or {},
                        provider_mission_ref=session.get("provider_mission_ref"))
    up = await _call(adapter.get_telemetry(ctx, now))
    return await ds.record_update(db, session, up, now, out)


async def advance_flights(factory, pub: Publisher | None, tenant_id: str, now: datetime) -> dict:
    counts: dict[str, int] = {}
    async with factory() as db:
        await _scope(db, tenant_id)
        ids = (await db.execute(text(
            f"SELECT id FROM drone_patrol_sessions WHERE status IN ({LIVE_SQL}) "
            f"   AND edge_gateway_id IS NULL ORDER BY created_at"
        ))).scalars().all()
        await db.rollback()
        for sid in ids:
            out = ds.Announcements()
            try:
                await _scope(db, tenant_id)
                s = (await db.execute(text(
                    "SELECT * FROM drone_patrol_sessions WHERE id = :id FOR UPDATE SKIP LOCKED"),
                    {"id": sid})).mappings().first()
                if s is None or s["status"] not in ds.IN_FLIGHT or s["edge_gateway_id"] is not None:
                    await db.rollback()
                    continue
                s = dict(s)
                try:
                    if s["status"] in ("PRECHECK", "READY"):
                        new = await _launch(db, s, now, out)
                    else:
                        new = await _fly(db, s, now, out)
                except (ProviderError, asyncio.TimeoutError) as exc:
                    if s["status"] in ("PRECHECK", "READY"):
                        # Never left the ground: the launch failed, safely.
                        await _end_session(db, s, "FAILED", now, out,
                                           reason=f"Launch failed: {exc or 'the provider did not respond.'}")
                        await ds.raise_alert(db, out, code="drone.mission_failed", severity="high",
                                             site_id=s["site_id"], session_id=s["id"], drone_id=s["drone_id"],
                                             title=f"Drone launch failed: {s.get('mission_name')}",
                                             message=f"{s.get('mission_name')} could not launch: {exc}")
                        new = "FAILED"
                    else:
                        # Airborne and not answering this tick. The heartbeat
                        # sweep raises the lost-link alert; this only gives up
                        # on the session once silence has lasted STALE_AFTER.
                        heard = s.get("last_tick_at") or s.get("launched_at") or s["created_at"]
                        if now - heard > STALE_AFTER:
                            mins = int(STALE_AFTER.total_seconds() // 60)
                            await _end_session(db, s, "FAILED", now, out,
                                               reason=f"No contact with the drone for {mins} minutes.")
                            await ds.raise_alert(
                                db, out, code="drone.mission_failed", severity="high", site_id=s["site_id"],
                                session_id=s["id"], drone_id=s["drone_id"],
                                title=f"Drone out of contact: {s.get('drone_name') or 'drone'}",
                                message=f"Nothing has been heard from {s.get('drone_name') or 'the drone'} "
                                        f"on {s.get('mission_name')} for {mins} minutes. The flight record "
                                        "has been closed; locate the aircraft.")
                            new = "FAILED"
                        else:
                            logger.warning("drone runner: session %s provider call failed: %s", sid, exc)
                            new = s["status"]
                await db.commit()
                await _announce(pub, tenant_id, out)
                counts[new] = counts.get(new, 0) + 1
            except Exception:
                await db.rollback()
                logger.exception("drone runner: session %s tick failed", sid)
    return counts


async def watch_edge_sessions(factory, pub: Publisher | None, tenant_id: str, now: datetime) -> dict:
    """The only things the runner does to a session a gateway flies: give up on
    one never picked up, and on one whose gateway has gone silent for too long."""
    counts = {"missed": 0, "unreachable": 0}
    async with factory() as db:
        await _scope(db, tenant_id)
        ids = (await db.execute(text(f"""
            SELECT s.id FROM drone_patrol_sessions s
             WHERE s.edge_gateway_id IS NOT NULL AND s.status IN ({LIVE_SQL})
               AND ((s.edge_claimed_at IS NULL AND s.created_at < CAST(:claim AS timestamptz))
                 OR (s.edge_claimed_at IS NOT NULL
                     AND COALESCE(s.last_tick_at, s.edge_claimed_at) < CAST(:stale AS timestamptz)))
        """), {"claim": now - EDGE_CLAIM_WINDOW, "stale": now - EDGE_STALE_AFTER})).scalars().all()
        await db.rollback()
        for sid in ids:
            out = ds.Announcements()
            try:
                await _scope(db, tenant_id)
                s = (await db.execute(text("""
                    SELECT s.*, g.name AS gateway_name FROM drone_patrol_sessions s
                      LEFT JOIN drone_edge_gateways g ON g.id = s.edge_gateway_id
                     WHERE s.id = :id FOR UPDATE OF s SKIP LOCKED"""), {"id": sid})).mappings().first()
                if s is None or s["status"] not in ds.IN_FLIGHT:
                    await db.rollback()
                    continue
                s = dict(s)
                gw = s.get("gateway_name") or "the site edge gateway"
                if s["edge_claimed_at"] is None:
                    if s["created_at"] >= now - EDGE_CLAIM_WINDOW:
                        await db.rollback()
                        continue
                    mins = int(EDGE_CLAIM_WINDOW.total_seconds() // 60)
                    reason = f"{gw} did not pick the mission up within {mins} minutes."
                    await _end_session(db, s, "MISSED", now, out, reason=reason, failure_code="EDGE_NOT_CLAIMED")
                    await ds.raise_alert(db, out, code="drone.mission_missed", severity="medium",
                                         site_id=s["site_id"], session_id=s["id"], drone_id=s["drone_id"],
                                         title=f"Drone mission missed: {s.get('mission_name')}",
                                         message=f"{s.get('mission_name')} did not start: {reason}")
                    counts["missed"] += 1
                else:
                    heard = s.get("last_tick_at") or s["edge_claimed_at"]
                    if heard >= now - EDGE_STALE_AFTER:
                        await db.rollback()
                        continue
                    mins = int(EDGE_STALE_AFTER.total_seconds() // 60)
                    reason = (f"Nothing has been heard from {gw} about this flight for {mins} minutes. "
                              "If the gateway reconnects, its own record of the flight replaces this one.")
                    await _end_session(db, s, "FAILED", now, out, reason=reason, failure_code="EDGE_UNREACHABLE")
                    await ds.raise_alert(db, out, code="drone.mission_failed", severity="high",
                                         site_id=s["site_id"], session_id=s["id"], drone_id=s["drone_id"],
                                         title=f"Drone flight out of contact: {s.get('drone_name') or 'drone'}",
                                         message=f"{s.get('mission_name')}: {reason} Locate the aircraft.",
                                         params={"failure_code": "EDGE_UNREACHABLE"})
                    counts["unreachable"] += 1
                await db.commit()
                await _announce(pub, tenant_id, out)
            except Exception:
                await db.rollback()
                logger.exception("drone runner: edge session %s watch failed", sid)
    return counts


async def run_tick(factory, pub: Publisher | None = None, now: datetime | None = None) -> dict:
    """Commands first — an abort must not wait behind a flight tick."""
    now = _utc(now)
    totals: dict[str, Any] = {"tenants": 0, "commands": {}, "sessions": {}, "edge": {}}
    for tid in await _tenants(factory):
        totals["tenants"] += 1
        for k, v in (await process_commands(factory, pub, tid, now)).items():
            totals["commands"][k] = totals["commands"].get(k, 0) + v
        for k, v in (await advance_flights(factory, pub, tid, now)).items():
            totals["sessions"][k] = totals["sessions"].get(k, 0) + v
        for k, v in (await watch_edge_sessions(factory, pub, tid, now)).items():
            totals["edge"][k] = totals["edge"].get(k, 0) + v
    return totals


# ═════════════════════════════════════════════════════════════════════════════
# Schedules
# ═════════════════════════════════════════════════════════════════════════════

async def create_due_sessions(factory, pub: Publisher | None, tenant_id: str, now: datetime) -> dict:
    counts = {"ready": 0, "blocked": 0, "missed": 0, "existing": 0}
    async with factory() as db:
        await _scope(db, tenant_id)
        rows = [dict(r) for r in (await db.execute(text("""
            SELECT sc.*, GREATEST(sc.created_at, sc.updated_at, m.updated_at) AS armed_at
              FROM drone_schedules sc JOIN drone_missions m ON m.id = sc.mission_id
             WHERE sc.enabled AND m.enabled
        """))).mappings().all()]
        await db.rollback()
    for row in rows:
        s = sched.Schedule.from_row(row)
        # Never owe a launch from before the schedule (or its mission) was last
        # switched on or changed: creating a schedule at 14:00 for 09:00 daily is
        # not a missed patrol this morning.
        since = max(now - LOOKBACK, row["armed_at"])
        for run in sched.due_between(s, since, now):
            async with factory() as db:
                out = ds.Announcements()
                try:
                    await _scope(db, tenant_id)
                    exists = (await db.execute(text(
                        "SELECT 1 FROM drone_patrol_sessions WHERE schedule_id = :s AND scheduled_for = :f"),
                        {"s": row["id"], "f": run})).first()
                    if exists:
                        counts["existing"] += 1
                        continue
                    bundle = await ds.load_bundle(db, row["mission_id"])
                    if bundle is None:
                        continue
                    if not ds.within_grace(run, row["grace_minutes"], now):
                        status, result = "MISSED", None
                    else:
                        result = await ds.preflight(db, bundle, now)
                        status = "READY" if result.passed else "BLOCKED"
                    try:
                        sid = await ds.create_session(db, bundle, now=now, trigger="SCHEDULE", status=status,
                                                      schedule_id=row["id"], scheduled_for=run, result=result)
                    except IntegrityError:
                        # Another flight took the drone between the check and the
                        # insert. Record the run as blocked rather than lose it.
                        await db.rollback()
                        await _scope(db, tenant_id)
                        status = "BLOCKED"
                        result = None
                        sid = await ds.create_session(db, bundle, now=now, trigger="SCHEDULE", status=status,
                                                      schedule_id=row["id"], scheduled_for=run)
                        if sid is not None:
                            await db.execute(text(
                                "UPDATE drone_patrol_sessions SET blocked_reason = :r WHERE id = :id"),
                                {"r": "The drone was already committed to another flight.", "id": sid})
                    if sid is None:
                        await db.rollback()
                        counts["existing"] += 1
                        continue
                    name = bundle.mission["name"]
                    if status == "BLOCKED":
                        reason = result.reason() if result else "The drone was already committed to another flight."
                        await ds.raise_alert(db, out, code="drone.preflight_blocked", severity="medium",
                                             site_id=bundle.mission["site_id"], session_id=sid,
                                             drone_id=(bundle.drone or {}).get("id"),
                                             title=f"Drone mission blocked: {name}",
                                             message=f"{name} did not launch at {run:%H:%M} UTC. {reason}")
                    elif status == "MISSED":
                        await ds.raise_alert(db, out, code="drone.mission_missed", severity="medium",
                                             site_id=bundle.mission["site_id"], session_id=sid,
                                             drone_id=(bundle.drone or {}).get("id"),
                                             title=f"Drone mission missed: {name}",
                                             message=f"{name} was due at {run:%H:%M} UTC and did not start "
                                                     f"within its {row['grace_minutes']}-minute grace period.")
                    out.add("drone_session_updated", {"session_id": str(sid), "status": status,
                                                      "previous_status": None, "scheduled_for": run.isoformat()})
                    await db.commit()
                    await _announce(pub, tenant_id, out)
                    counts[status.lower()] += 1
                except Exception:
                    await db.rollback()
                    logger.exception("drone runner: schedule %s run %s failed", row["id"], run)
    return counts


async def run_schedule_tick(factory, pub: Publisher | None = None, now: datetime | None = None) -> dict:
    now = _utc(now)
    totals = {"ready": 0, "blocked": 0, "missed": 0, "existing": 0}
    for tid in await _tenants(factory):
        for k, v in (await create_due_sessions(factory, pub, tid, now)).items():
            totals[k] += v
    return totals


# ═════════════════════════════════════════════════════════════════════════════
# Health
# ═════════════════════════════════════════════════════════════════════════════

async def check_health(factory, pub: Publisher | None, tenant_id: str, now: datetime) -> dict:
    counts = {"polled": 0, "recovered": 0, "lost": 0, "gateways_offline": 0}
    async with factory() as db:
        await _scope(db, tenant_id)
        drones = [dict(r) for r in (await db.execute(text(f"""
            SELECT d.*, p.provider_key, p.config, p.secret_encrypted, p.is_active AS provider_active
              FROM drones d JOIN drone_provider_configs p ON p.id = d.provider_config_id
             WHERE d.status NOT IN ('DISABLED','MAINTENANCE') AND p.is_active
               AND d.edge_gateway_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM drone_patrol_sessions s
                                WHERE s.drone_id = d.id AND s.status IN ({AIRBORNE_SQL}))
        """))).mappings().all()]
        await db.rollback()

    for d in drones:
        out = ds.Announcements()
        async with factory() as db:
            try:
                adapter = _adapter({"provider_key": d["provider_key"], "config": d["config"],
                                    "secret_encrypted": d["secret_encrypted"], "is_active": d["provider_active"]})
                if not adapter.supports(Capability.HEALTH):
                    continue
                h = await _call(adapter.get_status(_drone_ref(d), now))
                await _scope(db, tenant_id)
                new_status = await ds.apply_health(db, d, h, out)
                if d["status"] != new_status and d["status"] == "COMMUNICATION_LOST":
                    counts["recovered"] += 1
                await db.commit()
                await _announce(pub, tenant_id, out)
                counts["polled"] += 1
            except (ProviderError, asyncio.TimeoutError):
                await db.rollback()      # silence is for the sweep below to judge
            except Exception:
                await db.rollback()
                logger.exception("drone runner: health poll for drone %s failed", d["id"])

    # The sweep: anyone expected to be heard from, who has not been, within
    # their own timeout. One alert per outage — comms_alerted_at is cleared
    # when the drone is heard from again.
    #
    # Gateways first. A drone behind a gateway that has gone silent is marked
    # lost like any other, but the gateway's one alert speaks for all of them:
    # one broken site link is one thing to fix, not one alarm per aircraft.
    out = ds.Announcements()
    async with factory() as db:
        try:
            await _scope(db, tenant_id)
            gone = (await db.execute(text("""
                UPDATE drone_edge_gateways SET status = 'OFFLINE', offline_alerted_at = :now, updated_at = now()
                 WHERE is_active AND status <> 'OFFLINE' AND offline_alerted_at IS NULL
                   AND last_seen_at IS NOT NULL
                   AND last_seen_at < CAST(:now AS timestamptz) - make_interval(secs => heartbeat_timeout_seconds)
                RETURNING id, name, site_id, status,
                          (SELECT count(*) FROM drones d WHERE d.edge_gateway_id = drone_edge_gateways.id) AS drones
            """), {"now": now})).mappings().all()
            for g in gone:
                await ds.raise_alert(db, out, code="drone.gateway_offline", severity="high", site_id=g["site_id"],
                                     title=f"Drone edge gateway offline: {g['name']}",
                                     message=f"Nothing has been heard from {g['name']} within its heartbeat "
                                             f"timeout. Its {g['drones']} drone(s) cannot be seen or commanded "
                                             "from here; flights in progress continue under the gateway's "
                                             "control and catch up when it reconnects.",
                                     gateway_id=g["id"])
                out.add("drone_gateway_status_changed", {"gateway_id": str(g["id"]), "status": "OFFLINE"})
            heard = ", ".join(f"'{s}'" for s in HEARD_FROM)
            lost = (await db.execute(text(f"""
                UPDATE drones SET status = 'COMMUNICATION_LOST', communication_status = 'FAULT',
                       comms_alerted_at = :now, updated_at = now()
                 WHERE status IN ({heard}) AND comms_alerted_at IS NULL
                   AND last_heartbeat_at IS NOT NULL
                   AND last_heartbeat_at < CAST(:now AS timestamptz) - make_interval(secs => heartbeat_timeout_seconds)
                RETURNING id, name, site_id, status,
                          EXISTS (SELECT 1 FROM drone_edge_gateways g
                                   WHERE g.id = drones.edge_gateway_id AND g.status = 'OFFLINE') AS behind_offline
            """), {"now": now})).mappings().all()
            for d in lost:
                if not d["behind_offline"]:
                    await ds.raise_alert(db, out, code="drone.comms_lost", severity="high", site_id=d["site_id"],
                                         drone_id=d["id"], title=f"Drone not responding: {d['name']}",
                                         message=f"Nothing has been heard from {d['name']} within its heartbeat "
                                                 "timeout. Check its power and link.")
                out.add("drone_status_changed", {"drone_id": str(d["id"]), "status": "COMMUNICATION_LOST"})
            await db.execute(text("DELETE FROM drone_sync_receipts WHERE received_at < :cut"),
                             {"cut": now - RECEIPT_RETENTION})
            await db.commit()
            await _announce(pub, tenant_id, out)
            counts["lost"] = len(lost)
            counts["gateways_offline"] = len(gone)
        except Exception:
            await db.rollback()
            logger.exception("drone runner: heartbeat sweep failed for tenant %s", tenant_id)
    return counts


async def run_health_tick(factory, pub: Publisher | None = None, now: datetime | None = None) -> dict:
    now = _utc(now)
    totals = {"polled": 0, "recovered": 0, "lost": 0, "gateways_offline": 0}
    for tid in await _tenants(factory):
        for k, v in (await check_health(factory, pub, tid, now)).items():
            totals[k] = totals.get(k, 0) + v
    return totals
