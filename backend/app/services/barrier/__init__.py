"""Barrier / gate actuation.

Vendor drivers behind one interface, so everything above this package —
the router, the decision engine, the tests — is written once against
BarrierDriver and never against a specific device.

    driver = get_driver(BarrierConfig(vendor="hikvision", host="10.0.0.5", ...))
    result = await driver.open()
    if not result.ok:
        ...  # result.error is always populated and human-readable

HARDWARE VERIFICATION STATUS
    simulator  — fully exercised by the test suite.
    hikvision  — written from the published ISAPI spec, NOT tested against a
    dahua      — written from the published HTTP CGI spec, NOT tested against
    relay      — a documented convention, NOT tested against a board.

    The three real drivers are structurally correct (auth, timeouts, error
    handling, honest capability reporting) but their exact request shapes are
    unproven until a device is on the network. Each driver's docstring names
    what is most likely to need adjusting. Confirm against real hardware
    before relying on any of them to control site access.
"""
from __future__ import annotations

from .base import (
    BarrierConfig,
    BarrierDriver,
    BarrierResult,
    BarrierStatus,
    DEFAULT_TIMEOUT_SECONDS,
)
from .camera_io import DahuaCameraIODriver, HikvisionCameraIODriver
from .dahua import DahuaBarrierDriver
from .hikvision import HikvisionBarrierDriver
from .relay import RelayBarrierDriver
from .simulator import SimulatorBarrierDriver

_DRIVERS: dict[str, type[BarrierDriver]] = {
    # Dedicated access controllers
    "hikvision": HikvisionBarrierDriver,
    "dahua": DahuaBarrierDriver,
    # The ANPR camera's own relay output drives the boom — no separate
    # controller. This is how most gate installations are actually wired.
    "hikvision_camera_io": HikvisionCameraIODriver,
    "dahua_camera_io": DahuaCameraIODriver,
    # Standalone network relay board driven by a software call.
    "relay": RelayBarrierDriver,
    "simulator": SimulatorBarrierDriver,
}

# Kept in sync with the vendor CHECK constraint in migration 0076; the router
# validates against this so an unknown vendor is rejected at the API boundary
# with a clear message rather than as a database constraint violation.
SUPPORTED_VENDORS = tuple(_DRIVERS)

# Which commands each vendor can actually perform. Drives the operator UI so
# it doesn't offer a button that is guaranteed to fail — see the capability
# honesty note in base.py.
VENDOR_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "hikvision": ("open", "close", "hold_open", "release_hold", "status"),
    "dahua": ("open", "close", "status"),
    # Camera IO drives a single contact line, so it can pulse and it can latch,
    # but it can never report the boom's real position — status is always
    # 'unknown' rather than a guess.
    "hikvision_camera_io": ("open", "close", "hold_open", "release_hold"),
    "dahua_camera_io": ("open", "close", "hold_open", "release_hold"),
    "relay": ("open", "close", "hold_open", "release_hold", "status"),
    "simulator": ("open", "close", "hold_open", "release_hold", "status"),
}


def get_driver(config: BarrierConfig) -> BarrierDriver:
    """Resolve a driver for this barrier's configured vendor.

    Raises ValueError for an unknown vendor — that is a programming/config
    error rather than a device condition, so unlike every runtime device
    failure it is not squashed into a BarrierResult.
    """
    try:
        driver_cls = _DRIVERS[config.vendor]
    except KeyError:
        raise ValueError(
            f"unknown barrier vendor '{config.vendor}'; supported: {', '.join(SUPPORTED_VENDORS)}"
        ) from None
    return driver_cls(config)


def supports(vendor: str, command: str) -> bool:
    return command in VENDOR_CAPABILITIES.get(vendor, ())


__all__ = [
    "BarrierConfig",
    "BarrierDriver",
    "BarrierResult",
    "BarrierStatus",
    "DEFAULT_TIMEOUT_SECONDS",
    "SUPPORTED_VENDORS",
    "VENDOR_CAPABILITIES",
    "get_driver",
    "supports",
]
