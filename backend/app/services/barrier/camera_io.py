"""Barrier control through the ANPR camera's own alarm/relay output.

The common real-world wiring: the camera that reads the plate also has a relay
output wired to the barrier's open terminal. No separate access controller, no
extra IP address — the same device that sees the vehicle lets it in.

`relay_channel` selects the camera's output port (Hikvision numbers from 1,
Dahua indexes from 0 — each driver handles its own convention so the operator
just enters the port number printed on the camera).

An open is a PULSE — energise, wait pulse_ms, de-energise — because a barrier's
open input expects a momentary contact, not a sustained one. If the release leg
fails the contact stays closed, which on most barriers means held open; that is
reported as a failure even though the boom physically rose, because a control
room must know the gate is stuck.

UNVERIFIED AGAINST HARDWARE — written from the published ISAPI / Dahua CGI
specs. Structure, auth and error handling are sound; the exact request shapes
need confirming against a real camera. See services/barrier/__init__.py.
"""
from __future__ import annotations

import asyncio

import httpx

from .base import BarrierConfig, BarrierDriver, BarrierResult, Stopwatch

MAX_PULSE_MS = 10_000


class _CameraIODriver(BarrierDriver):
    """Shared pulse/hold semantics; subclasses supply the vendor's HTTP call."""

    async def _set_output(self, energised: bool) -> BarrierResult:  # pragma: no cover
        raise NotImplementedError

    async def open(self) -> BarrierResult:
        pulse = min(max(self.config.pulse_ms, 0), MAX_PULSE_MS)
        on = await self._set_output(True)
        if not on.ok:
            return on
        await asyncio.sleep(pulse / 1000)
        off = await self._set_output(False)
        if not off.ok:
            return BarrierResult.failure(
                f"camera output energised but failed to release — barrier may be "
                f"stuck open ({off.error})",
                on.latency_ms + off.latency_ms,
            )
        return BarrierResult.success("open", on.latency_ms + off.latency_ms)

    async def close(self) -> BarrierResult:
        # A single output line has no separate close signal; the barrier drops
        # on its own timer. De-energising is the honest equivalent.
        return await self._set_output(False)

    async def hold_open(self) -> BarrierResult:
        result = await self._set_output(True)
        return BarrierResult.success("held_open", result.latency_ms) if result.ok else result

    async def release_hold(self) -> BarrierResult:
        return await self._set_output(False)

    async def status(self) -> BarrierResult:
        # The camera can report its own output state, but that is the contact,
        # not the boom's physical position — it could be obstructed or still
        # travelling. Never claim more than the contact state.
        return BarrierResult.success("unknown", 0)


class HikvisionCameraIODriver(_CameraIODriver):
    """ISAPI: PUT /ISAPI/System/IO/outputs/{port}/trigger with an
    <IOPortData><outputState>high|low</outputState></IOPortData> body."""

    vendor = "hikvision_camera_io"

    async def _set_output(self, energised: bool) -> BarrierResult:
        port = self.config.relay_channel or 1
        url = f"{self.config.base_url}/ISAPI/System/IO/outputs/{port}/trigger"
        body = (
            "<IOPortData><outputState>"
            f"{'high' if energised else 'low'}"
            "</outputState></IOPortData>"
        )
        auth = (
            httpx.DigestAuth(self.config.username, self.config.password or "")
            if self.config.username else None
        )
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.put(
                        url, content=body,
                        headers={"Content-Type": "application/xml"}, auth=auth,
                    )
            except httpx.TimeoutException:
                return BarrierResult.failure(
                    f"timed out after {self.config.timeout}s contacting camera "
                    f"{self.config.host}", sw.elapsed_ms,
                )
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"camera unreachable: {exc}", sw.elapsed_ms)
        if resp.status_code == 401:
            return BarrierResult.failure("camera rejected credentials", sw.ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"camera returned HTTP {resp.status_code}", sw.ms)
        text = resp.text or ""
        if "<statusCode>" in text and "<statusCode>1</statusCode>" not in text:
            return BarrierResult.failure(f"camera rejected command: {text.strip()[:200]}", sw.ms)
        return BarrierResult.success("open" if energised else "closed", sw.ms)


class DahuaCameraIODriver(_CameraIODriver):
    """CGI: /cgi-bin/configManager.cgi?action=setConfig&AlarmOut[N].Mode=1|0
    (Dahua indexes alarm outputs from 0, so the operator's port 1 is index 0)."""

    vendor = "dahua_camera_io"

    async def _set_output(self, energised: bool) -> BarrierResult:
        index = max((self.config.relay_channel or 1) - 1, 0)
        url = f"{self.config.base_url}/cgi-bin/configManager.cgi"
        params = {"action": "setConfig", f"AlarmOut[{index}].Mode": 1 if energised else 0}
        auth = (
            httpx.DigestAuth(self.config.username, self.config.password or "")
            if self.config.username else None
        )
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(url, params=params, auth=auth)
            except httpx.TimeoutException:
                return BarrierResult.failure(
                    f"timed out after {self.config.timeout}s contacting camera "
                    f"{self.config.host}", sw.elapsed_ms,
                )
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"camera unreachable: {exc}", sw.elapsed_ms)
        if resp.status_code == 401:
            return BarrierResult.failure("camera rejected credentials", sw.ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"camera returned HTTP {resp.status_code}", sw.ms)
        if not (resp.text or "").strip().upper().startswith("OK"):
            return BarrierResult.failure(
                f"camera rejected command: {(resp.text or '').strip()[:200]}", sw.ms
            )
        return BarrierResult.success("open" if energised else "closed", sw.ms)
