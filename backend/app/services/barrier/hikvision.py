"""Hikvision barrier driver — ISAPI over HTTP Digest.

Protocol reference: Hikvision ISAPI Access Control specification. The
remote-control endpoint is
    PUT /ISAPI/AccessControl/RemoteControl/door/{doorNo}
with an XML body carrying one of: open | close | alwaysOpen | alwaysClose.
`relay_channel` is reused as doorNo (Hikvision numbers doors from 1).

UNVERIFIED AGAINST HARDWARE. This was written from the published protocol,
not tested against a physical unit — see the module note in
services/barrier/__init__.py. The request shape and auth are the parts most
likely to be right; the status endpoint is the part most likely to need
adjusting per firmware generation.
"""
from __future__ import annotations

import httpx

from .base import BarrierConfig, BarrierDriver, BarrierResult, Stopwatch

_CMD_BODY = '<RemoteControlDoor><cmd>{cmd}</cmd></RemoteControlDoor>'


class HikvisionBarrierDriver(BarrierDriver):
    vendor = "hikvision"

    def __init__(self, config: BarrierConfig) -> None:
        super().__init__(config)
        self._door_no = config.relay_channel or 1

    def _auth(self) -> httpx.DigestAuth | None:
        if not self.config.username:
            return None
        return httpx.DigestAuth(self.config.username, self.config.password or "")

    async def _send(self, cmd: str, resulting_status: str) -> BarrierResult:
        url = f"{self.config.base_url}/ISAPI/AccessControl/RemoteControl/door/{self._door_no}"
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.put(
                        url,
                        content=_CMD_BODY.format(cmd=cmd),
                        headers={"Content-Type": "application/xml"},
                        auth=self._auth(),
                    )
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
        # ISAPI answers 200 with a statusCode of 1 on success; a 200 carrying a
        # different statusCode means the device parsed the request and refused
        # it, which must not be reported as success.
        body = resp.text or ""
        if "<statusCode>" in body and "<statusCode>1</statusCode>" not in body:
            return BarrierResult.failure(f"device rejected command: {body.strip()[:200]}", sw.ms)
        return BarrierResult.success(resulting_status, sw.ms)

    async def open(self) -> BarrierResult:
        return await self._send("open", "open")

    async def close(self) -> BarrierResult:
        return await self._send("close", "closed")

    async def hold_open(self) -> BarrierResult:
        return await self._send("alwaysOpen", "held_open")

    async def release_hold(self) -> BarrierResult:
        # alwaysClose ends the always-open latch and restores per-vehicle
        # operation; it does not mean "stay shut forever".
        return await self._send("alwaysClose", "closed")

    async def status(self) -> BarrierResult:
        url = f"{self.config.base_url}/ISAPI/AccessControl/Door/status/{self._door_no}"
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(url, auth=self._auth())
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"status query failed: {exc}", sw.elapsed_ms)

        if resp.status_code >= 400:
            # Plenty of Hikvision barrier models have no position sensor and
            # simply do not serve this endpoint. Report reachable-but-unknown
            # rather than inventing a position.
            return BarrierResult.success("unknown", sw.ms)
        text = (resp.text or "").lower()
        if "open" in text:
            return BarrierResult.success("open", sw.ms)
        if "close" in text:
            return BarrierResult.success("closed", sw.ms)
        return BarrierResult.success("unknown", sw.ms)
