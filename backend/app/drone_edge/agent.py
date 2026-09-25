"""The site edge gateway: flies its site's drones with the provider adapter
installed here, keeps flying and recording when the link to the centre drops,
and catches the centre up when it returns.

One cycle, every couple of seconds (drone_edge_main), or called directly by a
test with a chosen clock:

  1. Fly. For every flight this gateway has claimed: carry out the operator
     commands waiting for it, then take the provider's latest update. Both are
     written to the local store before anything is sent.
  2. Health. Every so often, ask each idle drone for its health (its heartbeat).
  3. Sync. Send the oldest waiting items as one batch; the answer says what was
     accepted and what the gateway should do now — its drones, the sessions it
     may claim, commands for flights it is flying, files the centre wants.
  4. Claim. A ready session for one of its drones is claimed — which re-runs
     pre-flight centrally — and launched at once.
  5. Upload. The files the centre asked for, within the site's bandwidth limit.

WITHOUT THE CENTRE, steps 1 and 2 carry on and everything is kept. What does not
happen offline is starting a NEW flight: the licence, pre-flight and the
operators' commands live centrally, and a gateway that cannot reach them does
not launch on its own authority. A flight already in the air always finishes.

THE AIRCRAFT'S SAFETY BEHAVIOUR IS THE AIRCRAFT'S. The gateway reports lost
links, low battery and faults; it never tries to overrule the autopilot.

No database and no web framework: this runs on site hardware.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.drone_edge.central import CentralClient, CentralRefused, CentralUnavailable
from app.drone_edge.store import EdgeStore, LocalSession
from app.services import drone_flight_plan as fp
from app.services.drone_edge_wire import MAX_SAMPLES_PER_BATCH, from_flight_update
from app.services.drone_providers import (
    Capability, CapabilityNotSupported, DroneProvider, DroneRef, FlightContext, ProviderError, get_adapter,
)

logger = logging.getLogger("drone_edge")

PROVIDER_TIMEOUT_S = 10.0
SOFTWARE_VERSION = "drone-edge/1.0"
_KIND_OF = {"health": "health", "updates": "update", "commands": "command", "events": "event", "media": "media"}


def _utc(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


class EdgeAgent:
    def __init__(self, store: EdgeStore, central: CentralClient, *, media_dir: Path,
                 provider_secrets: dict[str, dict] | None = None, tenant_id: str = "",
                 health_every_s: float = 15.0, batch_items: int = 300,
                 batch_samples: int = MAX_SAMPLES_PER_BATCH, uploads_per_cycle: int = 3):
        self.store, self.central = store, central
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.provider_secrets = provider_secrets or {}
        self.tenant_id = tenant_id
        self.health_every = timedelta(seconds=health_every_s)
        self.batch_items, self.batch_samples = batch_items, batch_samples
        self.uploads_per_cycle = uploads_per_cycle
        self.online: bool | None = None
        self.started_at = time.monotonic()
        self._last_health: datetime | None = None
        self._uploads: list[str] = []
        self._pending_ready: list[dict] = []
        self._normal_batch_items = batch_items
        self._isolating = 0      # single-item batches sent since a batch was refused as invalid

    # ── what the centre last told us ─────────────────────────────────────────

    @property
    def assignment(self) -> dict:
        return self.store.get_meta("assignment", {}) or {}

    def _drone(self, drone_id: str) -> dict:
        return next((d for d in self.assignment.get("drones", []) if str(d["id"]) == str(drone_id)), {})

    def _adapter(self, drone: dict, snapshot_drone: dict | None = None) -> DroneProvider:
        key = drone.get("provider_key") or (snapshot_drone or {}).get("provider_key")
        cfg_id = str(drone.get("provider_config_id") or (snapshot_drone or {}).get("provider_config_id") or "")
        return get_adapter(key, drone.get("provider_config") or {}, self.provider_secrets.get(cfg_id, {}))

    @staticmethod
    def _ref(drone: dict, drone_id: str) -> DroneRef:
        return DroneRef(id=str(drone_id), code=drone.get("code") or "", provider_drone_ref=drone.get("provider_drone_ref"),
                        status=drone.get("status") or "OFFLINE", battery_level=drone.get("battery_level"),
                        latitude=drone.get("current_latitude"), longitude=drone.get("current_longitude"),
                        altitude_m=float(drone["current_altitude_m"]) if drone.get("current_altitude_m") is not None else None,
                        last_heartbeat_at=_dt(drone.get("last_heartbeat_at")))

    def _context(self, s: LocalSession) -> tuple[DroneProvider, FlightContext]:
        snap = s.snapshot
        drone = self._drone(s.drone_id)
        adapter = self._adapter(drone, snap.get("drone"))
        plan = fp.build_plan(snap["route"], snap["waypoints"])
        ctx = FlightContext(session_id=s.id, tenant_id=self.tenant_id, drone=self._ref(drone, s.drone_id),
                            plan=plan, provider_state=s.provider_state, provider_mission_ref=s.provider_mission_ref)
        return adapter, ctx

    # ── the cycle ────────────────────────────────────────────────────────────

    async def cycle(self, now: datetime | None = None) -> dict:
        now = _utc(now)
        summary: dict[str, Any] = {"flown": 0, "commands": 0, "synced": 0, "claimed": 0, "uploaded": 0}
        for s in self.store.live_sessions():
            summary["commands"] += await self._step(s, now)
            summary["flown"] += 1
        if self._last_health is None or now - self._last_health >= self.health_every:
            await self._poll_health(now)
            self._last_health = now
        synced = await self._sync(now)
        summary["synced"] = synced
        summary["online"] = self.online
        if self.online:
            summary["claimed"] = await self._claim_ready(now)
            summary["uploaded"] = await self._upload()
        self._prune(now)
        return summary

    # ── 1. flying ────────────────────────────────────────────────────────────

    async def _step(self, s: LocalSession, now: datetime) -> int:
        """Commands first — an abort must not wait behind a telemetry pull."""
        try:
            adapter, ctx = self._context(s)
        except Exception as exc:
            logger.error("drone edge: session %s cannot be flown here: %s", s.id, exc)
            return 0
        done = 0
        for cmd in self.store.open_commands(s.id):
            status, result, update = await self._command(adapter, ctx, s, cmd, now)
            if update is not None:
                self._record(s, update, now)
                ctx.provider_state = update.provider_state
                s = self.store.session(s.id)
            self.store.finish_command(cmd["id"], status, result, now)
            done += 1
            if s.outcome:
                return done
        try:
            if not s.started:
                up = await asyncio.wait_for(adapter.start_mission(ctx, now), PROVIDER_TIMEOUT_S)
                self._record(s, up, now, launched=True)
            else:
                up = await asyncio.wait_for(adapter.get_telemetry(ctx, now), PROVIDER_TIMEOUT_S)
                self._record(s, up, now)
            await self._waypoint_snapshots(adapter, ctx, s, up, now)
        except (ProviderError, asyncio.TimeoutError) as exc:
            # The aircraft follows its own safety behaviour; this tick is lost,
            # and the centre's watchdog judges prolonged silence.
            logger.warning("drone edge: session %s provider call failed: %s", s.id, exc or "timeout")
        except Exception:
            # An adapter bug (an update this system cannot record) must cost this
            # tick, not every tick of every flight after it.
            logger.exception("drone edge: session %s update could not be recorded", s.id)
        return done

    def _record(self, s: LocalSession, up, now: datetime, launched: bool = False) -> None:
        self.store.record_update(s.id, from_flight_update(up), at=now, launched=launched)

    async def _command(self, adapter: DroneProvider, ctx: FlightContext, s: LocalSession, cmd: dict,
                       now: datetime):
        """The same rules the central runner applies to a drone it flies."""
        kind, phase = cmd["command"], s.phase
        if s.outcome:
            return "REJECTED", f"The session has already ended ({s.outcome.lower()}).", None
        if kind == "CANCEL":
            return "REJECTED", "The drone has already launched; abort it or return it home instead.", None
        if kind in ("ABORT", "RETURN_TO_HOME") and phase == "RETURNING":
            return "DONE", "The drone is already returning.", None
        if kind == "PAUSE" and phase != "ACTIVE":
            return "REJECTED", f"Only an active mission can be paused (it is {(phase or 'launching').lower()}).", None
        if kind == "RESUME" and phase != "PAUSED":
            return "REJECTED", "The mission is not paused.", None
        method = {"PAUSE": adapter.pause_mission, "RESUME": adapter.resume_mission,
                  "ABORT": adapter.abort_mission, "RETURN_TO_HOME": adapter.return_to_home}[kind]
        try:
            up = await asyncio.wait_for(method(ctx, now), PROVIDER_TIMEOUT_S)
        except CapabilityNotSupported as exc:
            return "REJECTED", str(exc), None
        except (ProviderError, asyncio.TimeoutError) as exc:
            return "FAILED", str(exc) or "The provider did not respond in time.", None
        return "DONE", {"PAUSE": "Holding position.", "RESUME": "Mission resumed.",
                        "ABORT": "Aborted; the drone is returning home.",
                        "RETURN_TO_HOME": "The drone is returning home."}[kind], up

    async def _waypoint_snapshots(self, adapter: DroneProvider, ctx: FlightContext, s: LocalSession, up,
                                  now: datetime) -> None:
        """A waypoint that asks for a snapshot gets one — if the aircraft can
        take one. None is invented for one that cannot."""
        if not adapter.supports(Capability.SNAPSHOT):
            return
        wanted = {w["sequence"] for w in s.snapshot.get("waypoints", []) if w.get("snapshot_required")}
        for e in up.events:
            if e.kind == "WAYPOINT_REACHED" and e.waypoint in wanted:
                try:
                    data = await asyncio.wait_for(adapter.capture_snapshot(ctx.drone, ctx), PROVIDER_TIMEOUT_S)
                except (ProviderError, asyncio.TimeoutError) as exc:
                    logger.warning("drone edge: snapshot at waypoint %s failed: %s", e.waypoint, exc)
                    continue
                if data:
                    last = up.samples[-1] if up.samples else None
                    self.record_media(data, media_kind="SNAPSHOT", captured_at=e.at, session_id=s.id,
                                      waypoint_sequence=e.waypoint,
                                      telemetry_snapshot=({"latitude": last.latitude, "longitude": last.longitude,
                                                           "altitude_m": last.altitude_m} if last else None),
                                      now=now)

    # ── 2. health ────────────────────────────────────────────────────────────

    async def _poll_health(self, now: datetime) -> None:
        flying = {s.drone_id for s in self.store.live_sessions()}
        a = self.assignment
        for d in a.get("drones", []):
            if str(d["id"]) in flying or not d.get("provider_key"):
                continue
            try:
                adapter = self._adapter(d)
                if not adapter.supports(Capability.HEALTH):
                    continue
                h = await asyncio.wait_for(adapter.get_status(self._ref(d, d["id"]), now), PROVIDER_TIMEOUT_S)
            except (ProviderError, asyncio.TimeoutError):
                continue     # silence is for the centre's sweep to judge
            self.store.put_health(str(d["id"]), {
                "drone_id": str(d["id"]), "observed_at": h.observed_at.isoformat(), "battery_level": h.battery_level,
                "battery_health": h.battery_health, "gps_status": h.gps_status,
                "communication_status": h.communication_status, "camera_status": h.camera_status,
                "storage_status": h.storage_status, "temperature_c": h.temperature_c, "latitude": h.latitude,
                "longitude": h.longitude, "altitude_m": h.altitude_m, "status_hint": h.status_hint}, now)
            # What this gateway now knows is what it flies with next.
            d.update(battery_level=h.battery_level, last_heartbeat_at=h.observed_at.isoformat())
        if a:
            self.store.set_meta("assignment", a)

    # ── 3. sync ──────────────────────────────────────────────────────────────

    def _state(self, now: datetime) -> dict:
        depth, oldest = self.store.depth()
        try:
            du = shutil.disk_usage(self.store.path.parent)
            free = round(du.free * 100 / du.total, 2)
        except OSError:
            free = None
        return {"buffer_depth": depth, "oldest_buffered_at": oldest.isoformat() if oldest else None,
                "storage_free_pct": free, "uptime_s": round(time.monotonic() - self.started_at, 1)}

    def _batch(self, batch_id: str, items, now: datetime) -> dict:
        b: dict[str, Any] = {"batch_id": batch_id, "sent_at": now.isoformat(), "software_version": SOFTWARE_VERSION,
                             "state": self._state(now), "have_sessions": self.store.known_session_ids()[-200:],
                             "health": [], "updates": [], "commands": [], "events": [], "media": []}
        plural = {v: k for k, v in _KIND_OF.items()}
        for it in items:
            b[plural[it.kind]].append(it.payload)
        return b

    async def _sync(self, now: datetime) -> int:
        """Send what is waiting; take in the answer. Returns items delivered."""
        batch_id, items = self.store.open_batch(max_items=self.batch_items, max_samples=self.batch_samples)
        try:
            answer = await self.central.sync(self._batch(batch_id, items, now))
        except CentralUnavailable as exc:
            self._went(False, str(exc))
            return 0
        except CentralRefused as exc:
            if exc.status == 422 and len(items) > 1:
                # Something in the batch is malformed. Send items one at a
                # time until the bad one is found, so it cannot block the rest.
                self.store.drop_open_batch()
                self.batch_items, self._isolating = 1, 1
                logger.error("drone edge: batch refused as invalid; isolating the bad item")
                return 0
            if exc.status == 422 and len(items) == 1:
                self.store.close_batch(items, {(items[0].kind, items[0].ref): f"Invalid: {exc.detail}"}, now)
                logger.error("drone edge: set aside an invalid %s item: %s", items[0].kind, exc.detail)
                self.batch_items, self._isolating = self._normal_batch_items, 0
                return 0
            self._went(False, f"refused: {exc}")
            return 0
        self._went(True)
        rejected = {}
        for plural, kind in _KIND_OF.items():
            for r in (answer.get(plural) or {}).get("rejected", []):
                ref = {"health": r.get("drone_id"), "command": r.get("command_id"),
                       "event": r.get("client_ref"), "media": r.get("client_ref"),
                       "update": f"{r.get('session_id')}:{int(r.get('seq') or 0):09d}"}[kind]
                rejected[(kind, str(ref))] = r.get("reason") or "Rejected."
        for (kind, ref), reason in rejected.items():
            logger.warning("drone edge: centre refused %s %s: %s", kind, ref, reason)
        self.store.close_batch(items, rejected, now)
        if self._isolating:
            # Still looking for the bad item; give up looking after a while (the
            # refusal may have been the batch as a whole, not one item in it).
            self._isolating += 1
            if self._isolating > 100:
                self.batch_items, self._isolating = self._normal_batch_items, 0
        # The centre says, file by file, whether it wants the bytes; a file it
        # does not want may be pruned here once local retention runs out.
        wanted = set(answer.get("media_upload_requested") or [])
        sent = [it.ref for it in items if it.kind == "media" and ("media", it.ref) not in rejected]
        self.store.mark_media([r for r in sent if r in wanted], wanted=True)
        self.store.mark_media([r for r in sent if r not in wanted], wanted=False)
        self._take(answer.get("assignment") or {}, now)
        return len(items)

    def _went(self, online: bool, why: str = "") -> None:
        if online != self.online:
            if online:
                logger.info("drone edge: connected to the centre")
            else:
                logger.warning("drone edge: centre unreachable (%s); flying on and buffering", why)
        self.online = online

    def _take(self, a: dict, now: datetime) -> None:
        if not a:
            return
        self.store.set_meta("assignment", {k: v for k, v in a.items() if k != "sessions"}
                            | {"sessions": [{k: v for k, v in s.items() if k != "config_snapshot"}
                                            for s in a.get("sessions", [])]})
        # A session the centre says this gateway claimed but that is not in the
        # store: the gateway restarted between claiming and recording. Adopt it.
        known = set(self.store.known_session_ids())
        for s in a.get("sessions", []):
            if s.get("claimed") and s["id"] not in known and s.get("config_snapshot"):
                self.store.add_session(s, now)
                logger.warning("drone edge: adopted session %s claimed before a restart", s["id"])
        new = self.store.add_commands(a.get("commands", []), now)
        if new:
            logger.info("drone edge: %d new command(s) from the centre", new)
        self._uploads = list(a.get("uploads_wanted") or [])
        self._pending_ready = [s for s in a.get("sessions", []) if not s.get("claimed")
                               and s.get("status") in ("READY", "PRECHECK")]

    # ── 4. claiming ──────────────────────────────────────────────────────────

    async def _claim_ready(self, now: datetime) -> int:
        claimed = 0
        mine = {str(d["id"]) for d in self.assignment.get("drones", [])}
        flying = {s.drone_id for s in self.store.live_sessions()}
        for s in self._pending_ready:
            if s["drone_id"] not in mine or s["drone_id"] in flying:
                continue
            try:
                got = await self.central.claim(s["id"])
            except CentralUnavailable as exc:
                self._went(False, str(exc))
                return claimed
            except CentralRefused as exc:
                logger.warning("drone edge: could not claim %s: %s", s["id"], exc.detail)
                continue
            self.store.add_session(got["session"], now)
            flying.add(s["drone_id"])
            claimed += 1
            logger.info("drone edge: claimed %s (%s)", s["id"], s.get("mission_name"))
            local = self.store.session(s["id"])
            await self._step(local, now)       # launch now, not next cycle
        self._pending_ready = []
        return claimed

    # ── 5. uploads ───────────────────────────────────────────────────────────

    async def _upload(self) -> int:
        done = 0
        limit = (self.assignment.get("policy") or {}).get("bandwidth_limit_kbps")
        for ref in self._uploads[: self.uploads_per_cycle]:
            m = self.store.media(ref)
            if m is None or not Path(m["path"]).is_file():
                logger.error("drone edge: the centre asked for %s, which is not held here", ref)
                continue
            try:
                await self.central.upload(ref, Path(m["path"]), m["checksum"], bandwidth_kbps=limit)
            except CentralUnavailable as exc:
                self._went(False, str(exc))
                break
            except CentralRefused as exc:
                logger.error("drone edge: upload of %s refused: %s", ref, exc.detail)
                continue
            self.store.mark_media([ref], uploaded_at=datetime.now(timezone.utc))
            done += 1
        self._uploads = self._uploads[done:]
        return done

    # ── for edge AI and recorders (phase 6 onwards) ──────────────────────────

    def report_event(self, *, drone_id: str, module_type: str, detected_at: datetime,
                     session_id: str | None = None, ai_confidence: float | None = None,
                     drone_latitude: float | None = None, drone_longitude: float | None = None,
                     drone_altitude_m: float | None = None, waypoint_sequence: int | None = None,
                     observed_seconds: float | None = None, detection_ref: str | None = None,
                     label: str | None = None, watchlist: str | None = None,
                     attributes: dict | None = None, now: datetime | None = None) -> str:
        """Record something seen during a flight. Kept here until the centre has
        it, however long that takes. Returns its client_ref."""
        ref = str(uuid.uuid4())
        self.store.put_event({
            "client_ref": ref, "drone_id": str(drone_id), "session_id": str(session_id) if session_id else None,
            "module_type": module_type, "detected_at": detected_at.isoformat(), "ai_confidence": ai_confidence,
            "drone_latitude": drone_latitude, "drone_longitude": drone_longitude,
            "drone_altitude_m": drone_altitude_m, "waypoint_sequence": waypoint_sequence,
            "observed_seconds": observed_seconds, "detection_ref": detection_ref, "label": label,
            "watchlist": watchlist, "attributes": attributes or {}}, _utc(now))
        return ref

    def record_media(self, data: bytes, *, media_kind: str, captured_at: datetime,
                     event_client_ref: str | None = None, session_id: str | None = None,
                     waypoint_sequence: int | None = None, duration_seconds: float | None = None,
                     telemetry_snapshot: dict | None = None, now: datetime | None = None) -> str:
        """Keep a file at the site and describe it to the centre, which decides —
        by the site's recording policy — whether it wants the bytes."""
        ref = str(uuid.uuid4())
        folder = self.media_dir / captured_at.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{ref}{'.jpg' if media_kind == 'SNAPSHOT' else '.mp4'}"
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        self.store.put_media({
            "client_ref": ref, "event_client_ref": event_client_ref, "session_id": session_id,
            "waypoint_sequence": waypoint_sequence, "media_kind": media_kind,
            "captured_at": captured_at.isoformat(), "checksum_sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), "duration_seconds": duration_seconds,
            "telemetry_snapshot": telemetry_snapshot}, str(path), _utc(now))
        return ref

    # ── housekeeping ─────────────────────────────────────────────────────────

    def _prune(self, now: datetime) -> None:
        self.store.prune_sessions(now - timedelta(days=1))
        days = (self.assignment.get("policy") or {}).get("local_retention_days")
        if days is None:
            return          # no local retention set: keep everything
        for m in self.store.prunable_media(now - timedelta(days=int(days))):
            try:
                Path(m["path"]).unlink(missing_ok=True)
            except OSError:
                continue
            self.store.forget_media(m["client_ref"])
