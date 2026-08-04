"""Barrier driver contract.

Every vendor driver implements this interface, so the router, the decision
engine, and the tests all talk to one shape regardless of what hardware is
(or isn't) on the other end.

Two rules every driver must follow:

1. NEVER RAISE. A gate controller is a flaky network device on someone
   else's VLAN — timeouts, refused connections and malformed replies are
   normal operating conditions, not exceptions. Drivers return a
   BarrierResult describing what happened. This mirrors the convention
   already used by the camera stream-validation endpoint, which returns
   {valid: false, error} rather than a 500.

2. BE HONEST ABOUT CAPABILITY. If a device family genuinely cannot do
   something (several Dahua barrier models expose no remote "close"), the
   driver returns `unsupported()` — it does not pretend the command
   succeeded. A control room that thinks it closed a gate it did not close
   is worse than one that knows it failed.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

BarrierStatus = Literal["open", "closed", "held_open", "unknown", "error"]

# Bounded so a hung gate controller can never tie up an API worker. A barrier
# command is a human-in-the-loop action; if it hasn't landed in 5s it has
# failed as far as the operator is concerned.
#
# NOTE (measured, not assumed): this is a PER-REQUEST timeout, and the
# Hikvision/Dahua drivers authenticate with HTTP Digest, which costs two round
# trips — an unreachable host was observed taking ~1.96s against a 1.0s
# timeout. Budget for roughly 2x this value of wall-clock time on the digest
# drivers when sizing request timeouts upstream.
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BarrierConfig:
    """Device binding for one barrier. `password` arrives already decrypted —
    callers pull it through app.core.crypto.decrypt_secret; it is never held
    in plaintext at rest."""
    vendor: str
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    relay_channel: int | None = None
    pulse_ms: int = 1000
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # The barrier's own row id. Drivers are constructed per command and hold no
    # state, so anything that needs to tell two gates apart across calls (the
    # simulator's position, log correlation) keys off this rather than host —
    # a simulated barrier has no host at all.
    device_key: str | None = None

    @property
    def base_url(self) -> str:
        port = f":{self.port}" if self.port else ""
        return f"http://{self.host}{port}"


@dataclass(frozen=True)
class BarrierResult:
    ok: bool
    status: BarrierStatus = "unknown"
    error: str | None = None
    latency_ms: int = 0

    @staticmethod
    def success(status: BarrierStatus = "unknown", latency_ms: int = 0) -> "BarrierResult":
        return BarrierResult(ok=True, status=status, latency_ms=latency_ms)

    @staticmethod
    def failure(error: str, latency_ms: int = 0) -> "BarrierResult":
        return BarrierResult(ok=False, status="error", error=error, latency_ms=latency_ms)

    @staticmethod
    def unsupported(command: str, vendor: str) -> "BarrierResult":
        return BarrierResult(
            ok=False,
            status="unknown",
            error=f"'{command}' is not supported by the {vendor} driver",
        )


class Stopwatch:
    """Millisecond timer so every result carries a real latency figure —
    the command log is the only place a slow-degrading gate controller
    becomes visible before it fails outright."""

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc) -> None:
        self.ms = int((time.perf_counter() - self._start) * 1000)

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)


class BarrierDriver(ABC):
    """One instance per command; drivers hold no cross-call state so they are
    safe to construct per request."""

    vendor: str = "abstract"

    def __init__(self, config: BarrierConfig) -> None:
        self.config = config

    @abstractmethod
    async def open(self) -> BarrierResult:
        """Raise the boom / unlock the gate for a single vehicle."""

    @abstractmethod
    async def close(self) -> BarrierResult:
        """Lower the boom now."""

    @abstractmethod
    async def hold_open(self) -> BarrierResult:
        """Latch open until explicitly released — used for convoys, fire
        drills and emergency egress."""

    @abstractmethod
    async def release_hold(self) -> BarrierResult:
        """Return to normal per-vehicle operation after hold_open."""

    @abstractmethod
    async def status(self) -> BarrierResult:
        """Best-effort current position. Many barrier controllers expose no
        position sensor at all, in which case 'unknown' is the honest
        answer and the driver must return exactly that."""
