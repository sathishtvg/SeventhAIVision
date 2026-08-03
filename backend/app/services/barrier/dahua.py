"""Dahua barrier driver — HTTP CGI over Digest auth.

Protocol reference: Dahua HTTP API for access controllers (ASI/ASC family).
    GET /cgi-bin/accessControl.cgi?action=openDoor&channel={n}&UserID=101&Type=Remote
Success is a plain-text body starting "OK".

CAPABILITY HONESTY: `open` and `close` are well-documented across the Dahua
access-controller range. A latched always-open state is NOT consistently
exposed — the path differs between firmware generations and several barrier
models don't implement it at all. Rather than ship a call that will silently
400 on most units, hold_open/release_hold report `unsupported`, so a control
room is never told it latched a gate open when it didn't. Wire the
model-specific configManager call here once there's a unit to test against.

UNVERIFIED AGAINST HARDWARE — written from the published protocol.
"""
from __future__ import annotations

import httpx

from .base import BarrierConfig, BarrierDriver, BarrierResult, Stopwatch


class DahuaBarrierDriver(BarrierDriver):
    vendor = "dahua"

    def __init__(self, config: BarrierConfig) -> None:
        super().__init__(config)
        self._channel = config.relay_channel or 1

    def _auth(self) -> httpx.DigestAuth | None:
        if not self.config.username:
            return None
        return httpx.DigestAuth(self.config.username, self.config.password or "")

    async def _cgi(self, action: str, resulting_status: str) -> BarrierResult:
        url = f"{self.config.base_url}/cgi-bin/accessControl.cgi"
        params = {
            "action": action,
            "channel": self._channel,
            "UserID": "101",
            "Type": "Remote",
        }
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(url, params=params, auth=self._auth())
            except httpx.TimeoutException:
                return BarrierResult.failure(
                    f"timed out after {self.config.timeout}s contacting {self.config.host}",
                    sw.elapsed_ms,
                )
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"connection failed: {exc}", sw.elapsed_ms)

        if resp.status_code == 401:
            return BarrierResult.failure("authentication rejected by device", sw.ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"device returned HTTP {resp.status_code}", sw.ms)
        body = (resp.text or "").strip()
        if not body.upper().startswith("OK"):
            return BarrierResult.failure(f"device rejected command: {body[:200]}", sw.ms)
        return BarrierResult.success(resulting_status, sw.ms)

    async def open(self) -> BarrierResult:
        return await self._cgi("openDoor", "open")

    async def close(self) -> BarrierResult:
        return await self._cgi("closeDoor", "closed")

    async def hold_open(self) -> BarrierResult:
        return BarrierResult.unsupported("hold_open", self.vendor)

    async def release_hold(self) -> BarrierResult:
        return BarrierResult.unsupported("release_hold", self.vendor)

    async def status(self) -> BarrierResult:
        # The Dahua access CGI exposes no reliable position read across the
        # barrier range. Probe reachability via the magicBox device query so
        # the UI can still distinguish "device online" from "device down",
        # and report the position itself as unknown rather than guessing.
        url = f"{self.config.base_url}/cgi-bin/magicBox.cgi"
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(
                        url, params={"action": "getDeviceType"}, auth=self._auth()
                    )
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"device unreachable: {exc}", sw.elapsed_ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"device returned HTTP {resp.status_code}", sw.ms)
        return BarrierResult.success("unknown", sw.ms)
