"""Where a fleet vehicle's positions come from: one set of steps every GPS provider is plugged into.

A provider adapter does five things, and nothing else:

    test             can the server be reached, and does it accept our sign-in
    device           what the provider knows of one device (its last update)
    register_device  tell the provider about a device, where it allows that
    parse_forward    read one position the provider forwarded to us
    fetch_range      the positions of one device over a time range (catching up)

Traccar is the first (`traccar.py`). Another provider is a module with the
same five, added to `ADAPTERS`. Credentials are passed in as a `Connection`
already decrypted, by the server, and are never returned by any of these.

A position the provider forwards is refused - not stored, and counted with its
reason - when it is not a real fix: no device ID, coordinates out of range,
exactly 0,0, a fix time missing or more than a few minutes in the future, or a
fix the provider itself marks as not valid. Nothing here makes up a position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.core.config import settings


class ForwardRefused(ValueError):
    """A forwarded position that is not stored, with the reason it is counted under."""

    def __init__(self, reason: str, device_identifier: str | None = None, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.device_identifier = device_identifier
        self.detail = detail or {}


@dataclass(frozen=True)
class ParsedPosition:
    device_identifier: str
    fix_at: datetime
    lat: float
    lon: float
    speed_kmh: float | None = None
    heading: int | None = None
    accuracy: float | None = None
    altitude: float | None = None
    ignition: bool | None = None
    satellites: int | None = None
    valid: bool | None = None
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Connection:
    """How to reach one provider server. `secret` is a token or a password, decrypted, for this call only."""
    base_url: str
    auth_kind: str = "token"
    username: str | None = None
    secret: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    ok: bool
    message: str
    detail: dict = field(default_factory=dict)


class ProviderAdapter(Protocol):
    name: str

    async def test(self, conn: Connection) -> Diagnosis: ...

    async def device(self, conn: Connection, identifier: str) -> Diagnosis: ...

    async def register_device(self, conn: Connection, identifier: str, name: str) -> Diagnosis: ...

    def parse_forward(self, body: Any) -> ParsedPosition: ...

    async def fetch_range(self, conn: Connection, identifier: str, start: datetime, end: datetime) -> list[ParsedPosition]: ...


def builtin_traccar() -> Connection | None:
    """The platform's own Traccar, from the server's settings; None when it has none."""
    if not settings.TRACCAR_URL:
        return None
    if settings.TRACCAR_API_TOKEN:
        return Connection(settings.TRACCAR_URL, "token", None, settings.TRACCAR_API_TOKEN)
    if settings.TRACCAR_API_USER and settings.TRACCAR_API_PASSWORD:
        return Connection(settings.TRACCAR_URL, "basic", settings.TRACCAR_API_USER, settings.TRACCAR_API_PASSWORD)
    return Connection(settings.TRACCAR_URL, "token", None, None)


def adapter(provider: str) -> ProviderAdapter:
    from app.services.fleet_providers.traccar import TraccarAdapter
    adapters: dict[str, ProviderAdapter] = {"traccar": TraccarAdapter()}
    if provider not in adapters:
        raise KeyError(provider)
    return adapters[provider]


#: The key a device is registered under: the platform's Traccar, or one organisation's own connection.
BUILTIN = "builtin"


def source_key(connection_id: str | None) -> str:
    return f"connection:{connection_id}" if connection_id else BUILTIN
