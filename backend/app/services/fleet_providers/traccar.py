"""Traccar (https://www.traccar.org), the first GPS provider.

Checked on 15 October 2026 against Traccar 6.16 (the image this platform runs):

  - It forwards each position as JSON, with the header we set (FORWARD_TYPE=json,
    FORWARD_URL, FORWARD_HEADER): `{"device": {"uniqueId", ...}, "position":
    {"fixTime", "latitude", "longitude", "speed", "course", "accuracy",
    "altitude", "valid", "attributes", ...}}`. The position's `id` is 0 when it is
    forwarded, so a position is known by its device and its fix time.
  - `speed` is in knots. `fixTime` is the device's own time, as ISO 8601 with an
    offset. Ignition and satellites are attributes (`ignition`, `sat`), when the
    device reports them.
  - A position from a device Traccar has not been told about is refused by
    Traccar itself and never forwarded.
  - Its API accepts a token (`Authorization: Bearer`, from POST /api/session/token)
    or a user and password (Basic) on /api/server, /api/devices and /api/positions.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.fleet_providers import Connection, Diagnosis, ForwardRefused, ParsedPosition

KNOT_KMH = 1.852
#: A fix this far ahead of the server's clock is a device with a wrong clock, not a position.
MOST_AHEAD = timedelta(minutes=5)
TIMEOUT = 10.0


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


class TraccarAdapter:
    name = "traccar"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # A test gives its own transport; the server talks to the network.
        self._transport = transport

    # ── reading what Traccar forwards ────────────────────────────────────────
    def parse_forward(self, body: Any, now: datetime | None = None) -> ParsedPosition:
        if not isinstance(body, dict) or not isinstance(body.get("position"), dict):
            raise ForwardRefused("not_a_position")
        device = body.get("device") if isinstance(body.get("device"), dict) else {}
        identifier = device.get("uniqueId")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 100:
            raise ForwardRefused("no_device_id")
        identifier = identifier.strip()
        p = body["position"]
        lat, lon = _number(p.get("latitude")), _number(p.get("longitude"))
        if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ForwardRefused("bad_coordinates", identifier, {"latitude": p.get("latitude"), "longitude": p.get("longitude")})
        if abs(lat) < 1e-6 and abs(lon) < 1e-6:
            raise ForwardRefused("zero_position", identifier)
        fix_at = _time(p.get("fixTime")) or _time(p.get("deviceTime"))
        if fix_at is None:
            raise ForwardRefused("no_fix_time", identifier)
        if fix_at > (now or datetime.now(timezone.utc)) + MOST_AHEAD:
            raise ForwardRefused("fix_in_future", identifier, {"fix_time": fix_at.isoformat()})
        if p.get("valid") is False:
            raise ForwardRefused("not_a_fix", identifier, {"fix_time": fix_at.isoformat()})
        attributes = p.get("attributes") if isinstance(p.get("attributes"), dict) else {}
        knots = _number(p.get("speed"))
        course = _number(p.get("course"))
        sat = _number(attributes.get("sat"))
        ignition = attributes.get("ignition")
        return ParsedPosition(
            device_identifier=identifier, fix_at=fix_at, lat=lat, lon=lon,
            speed_kmh=round(knots * KNOT_KMH, 2) if knots is not None and knots >= 0 else None,
            heading=int(course) % 360 if course is not None and course >= 0 else None,
            accuracy=_number(p.get("accuracy")) or None,
            altitude=_number(p.get("altitude")),
            ignition=ignition if isinstance(ignition, bool) else None,
            satellites=int(sat) if sat is not None and sat >= 0 else None,
            valid=p.get("valid") if isinstance(p.get("valid"), bool) else None,
            attributes={k: v for k, v in attributes.items() if isinstance(v, (str, int, float, bool))},
        )

    # ── asking Traccar ───────────────────────────────────────────────────────
    def _client(self, conn: Connection) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        auth = None
        if conn.auth_kind == "token" and conn.secret:
            headers["Authorization"] = f"Bearer {conn.secret}"
        elif conn.auth_kind == "basic" and conn.username and conn.secret:
            auth = httpx.BasicAuth(conn.username, conn.secret)
        return httpx.AsyncClient(base_url=conn.base_url.rstrip("/"), headers=headers, auth=auth,
                                 timeout=TIMEOUT, transport=self._transport, follow_redirects=False)

    async def test(self, conn: Connection) -> Diagnosis:
        try:
            async with self._client(conn) as http:
                server = await http.get("/api/server")
                if server.status_code != 200:
                    return Diagnosis(False, f"Traccar answered {server.status_code} at /api/server")
                version = (server.json() or {}).get("version")
                devices = await http.get("/api/devices")
        except httpx.HTTPError as exc:
            return Diagnosis(False, f"Traccar could not be reached: {type(exc).__name__}")
        except ValueError:
            return Diagnosis(False, "Traccar's answer was not JSON")
        if devices.status_code in (401, 403):
            return Diagnosis(False, "Traccar is reachable, but did not accept the sign-in", {"version": version})
        if devices.status_code != 200:
            return Diagnosis(False, f"Traccar answered {devices.status_code} when asked for its devices", {"version": version})
        return Diagnosis(True, "Traccar is reachable and accepts the sign-in",
                         {"version": version, "devices": len(devices.json() or [])})

    async def device(self, conn: Connection, identifier: str) -> Diagnosis:
        try:
            async with self._client(conn) as http:
                r = await http.get("/api/devices", params={"uniqueId": identifier})
        except httpx.HTTPError as exc:
            return Diagnosis(False, f"Traccar could not be reached: {type(exc).__name__}")
        if r.status_code in (401, 403):
            return Diagnosis(False, "Traccar did not accept the sign-in")
        if r.status_code != 200:
            return Diagnosis(False, f"Traccar answered {r.status_code}")
        found = [d for d in (r.json() or []) if d.get("uniqueId") == identifier]
        if not found:
            return Diagnosis(False, "Traccar does not know this device yet: its positions are refused until it does")
        d = found[0]
        return Diagnosis(True, "Traccar knows this device",
                         {"traccar_id": d.get("id"), "status": d.get("status"), "last_update": d.get("lastUpdate"),
                          "disabled": d.get("disabled")})

    async def register_device(self, conn: Connection, identifier: str, name: str) -> Diagnosis:
        known = await self.device(conn, identifier)
        if known.ok:
            return Diagnosis(True, "Traccar already knows this device", known.detail)
        try:
            async with self._client(conn) as http:
                r = await http.post("/api/devices", json={"name": name[:128], "uniqueId": identifier})
        except httpx.HTTPError as exc:
            return Diagnosis(False, f"Traccar could not be reached: {type(exc).__name__}")
        if r.status_code in (401, 403):
            return Diagnosis(False, "Traccar did not accept the sign-in, so the device was not registered there")
        if r.status_code != 200:
            return Diagnosis(False, f"Traccar refused the device ({r.status_code}); it may be registered under another account")
        return Diagnosis(True, "The device was registered in Traccar", {"traccar_id": (r.json() or {}).get("id")})

    async def fetch_range(self, conn: Connection, identifier: str, start: datetime, end: datetime) -> list[ParsedPosition]:
        known = await self.device(conn, identifier)
        if not known.ok:
            return []
        async with self._client(conn) as http:
            r = await http.get("/api/positions", params={
                "deviceId": known.detail["traccar_id"],
                "from": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        if r.status_code != 200:
            return []
        out = []
        for p in r.json() or []:
            try:
                out.append(self.parse_forward({"device": {"uniqueId": identifier}, "position": p}))
            except ForwardRefused:
                continue
        return out
