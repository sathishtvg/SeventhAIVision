"""Generic relay / GPIO barrier driver.

The universal fallback: almost every barrier ever made will open on a dry
contact closure, so a network relay board wired to the barrier's "open"
terminal works regardless of brand. This is usually the cheapest and most
reliable way to integrate an otherwise-closed barrier.

CONVENTION, NOT A STANDARD. Unlike ISAPI or Dahua's CGI there is no common
relay-board API — KMTronic, Denkovi and the various ESP-based boards all
differ. This driver targets the most common simple REST shape:

    GET {base_url}/relay/{channel}/on
    GET {base_url}/relay/{channel}/off

A momentary "open" is on -> wait pulse_ms -> off, because a barrier's open
input expects a pulse, not a sustained closure. If your board speaks a
different dialect, this file is the single place to change it; nothing above
it in the stack cares.

UNVERIFIED AGAINST HARDWARE.
"""
from __future__ import annotations

import asyncio

import httpx

from .base import BarrierConfig, BarrierDriver, BarrierResult, Stopwatch

# A relay pulse is a physical action; cap it so a bad config value can't latch
# a gate open indefinitely by accident.
MAX_PULSE_MS = 10_000


class RelayBarrierDriver(BarrierDriver):
    vendor = "relay"

    def __init__(self, config: BarrierConfig) -> None:
        super().__init__(config)
        self._channel = config.relay_channel or 1

    def _auth(self) -> httpx.BasicAuth | None:
        # Relay boards that authenticate at all almost always use Basic.
        if not self.config.username:
            return None
        return httpx.BasicAuth(self.config.username, self.config.password or "")

    async def _set(self, state: str) -> BarrierResult:
        url = f"{self.config.base_url}/relay/{self._channel}/{state}"
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(url, auth=self._auth())
            except httpx.TimeoutException:
                return BarrierResult.failure(
                    f"timed out after {self.config.timeout}s contacting {self.config.host}",
                    sw.elapsed_ms,
                )
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"connection failed: {exc}", sw.elapsed_ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"relay board returned HTTP {resp.status_code}", sw.ms)
        return BarrierResult.success("open" if state == "on" else "closed", sw.ms)

    async def open(self) -> BarrierResult:
        """Momentary pulse. If the de-energise leg fails the contact is left
        closed, which on most barriers means held open — that is a safety
        condition the operator must see, so it is reported as a failure even
        though the gate did physically rise."""
        pulse = min(max(self.config.pulse_ms, 0), MAX_PULSE_MS)
        on = await self._set("on")
        if not on.ok:
            return on
        await asyncio.sleep(pulse / 1000)
        off = await self._set("off")
        if not off.ok:
            return BarrierResult.failure(
                f"relay energised but failed to release — barrier may be stuck open ({off.error})",
                on.latency_ms + off.latency_ms,
            )
        return BarrierResult.success("open", on.latency_ms + off.latency_ms)

    async def close(self) -> BarrierResult:
        # A dry-contact barrier closes on its own timer; the relay has no
        # separate "close" line in the standard single-channel wiring.
        return await self._set("off")

    async def hold_open(self) -> BarrierResult:
        """Energise and leave energised — the contact stays closed, holding
        the barrier up until release_hold()."""
        result = await self._set("on")
        if result.ok:
            return BarrierResult.success("held_open", result.latency_ms)
        return result

    async def release_hold(self) -> BarrierResult:
        return await self._set("off")

    async def status(self) -> BarrierResult:
        # A relay board reports its own contact state, which is not the same
        # thing as the barrier's physical position — the boom could be
        # obstructed, or still travelling. Report the contact honestly and
        # let the UI label it as such rather than overstating it.
        url = f"{self.config.base_url}/relay/{self._channel}"
        sw = Stopwatch()
        with sw:
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    resp = await client.get(url, auth=self._auth())
            except httpx.HTTPError as exc:
                return BarrierResult.failure(f"relay board unreachable: {exc}", sw.elapsed_ms)
        if resp.status_code >= 400:
            return BarrierResult.failure(f"relay board returned HTTP {resp.status_code}", sw.ms)
        body = (resp.text or "").strip().lower()
        if "on" in body or body == "1":
            return BarrierResult.success("held_open", sw.ms)
        return BarrierResult.success("closed", sw.ms)
