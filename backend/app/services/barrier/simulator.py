"""In-memory barrier simulator.

Exists because the whole ANPR -> registry -> decision -> barrier chain has to
be provable without hardware on the bench. A barrier configured with
vendor='simulator' behaves like a real one — it holds a position, honours
hold/release, and can be told to fail — so the decision engine, the command
audit trail and the operator UI can all be exercised end to end today, and
the vendor field flipped to a real driver later with no other change.

Also the driver the automated tests run against: asserting on real device
I/O would make the suite depend on a gate controller being plugged in.
"""
from __future__ import annotations

from .base import BarrierConfig, BarrierDriver, BarrierResult, BarrierStatus

# Position survives between commands (drivers are constructed per call), keyed
# by the barrier's own id so several simulated gates stay independent.
_STATE: dict[str, BarrierStatus] = {}

# Test hook: add a key here to make that barrier fail every command, so the
# failure path through the router and command log is exercisable on demand.
_FORCED_FAILURES: dict[str, str] = {}


def reset_simulator() -> None:
    """Clear all simulated state — called between tests."""
    _STATE.clear()
    _FORCED_FAILURES.clear()


def force_failure(device_key: str, error: str = "simulated device fault") -> None:
    _FORCED_FAILURES[device_key] = error


def clear_failure(device_key: str) -> None:
    _FORCED_FAILURES.pop(device_key, None)


def peek(device_key: str) -> BarrierStatus:
    return _STATE.get(device_key, "closed")


class SimulatorBarrierDriver(BarrierDriver):
    vendor = "simulator"

    def __init__(self, config: BarrierConfig) -> None:
        super().__init__(config)
        self._key = config.device_key or config.host or "default"

    def _apply(self, status: BarrierStatus) -> BarrierResult:
        if self._key in _FORCED_FAILURES:
            return BarrierResult.failure(_FORCED_FAILURES[self._key], latency_ms=1)
        _STATE[self._key] = status
        return BarrierResult.success(status, latency_ms=1)

    async def open(self) -> BarrierResult:
        # A momentary open does not clear a standing hold — matches how a real
        # latched barrier behaves, so the UI's hold indicator can't be
        # silently cleared by a passing vehicle.
        if _STATE.get(self._key) == "held_open":
            return BarrierResult.success("held_open", latency_ms=1)
        return self._apply("open")

    async def close(self) -> BarrierResult:
        return self._apply("closed")

    async def hold_open(self) -> BarrierResult:
        return self._apply("held_open")

    async def release_hold(self) -> BarrierResult:
        return self._apply("closed")

    async def status(self) -> BarrierResult:
        if self._key in _FORCED_FAILURES:
            return BarrierResult.failure(_FORCED_FAILURES[self._key], latency_ms=1)
        return BarrierResult.success(_STATE.get(self._key, "closed"), latency_ms=1)
