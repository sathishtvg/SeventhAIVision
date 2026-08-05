"""Hardware protocol catalogue — what each device protocol needs configured.

WHY THIS EXISTS
    Every site runs different hardware. One car park has a Hikvision ANPR
    camera driving the boom off its own relay output; the next has a Dahua
    access controller; the next has a no-name relay board on the LAN; the next
    has a Modbus PLC or an RS-485 loop. Each of those needs DIFFERENT
    parameters, and until now the platform stored barrier config as a fixed
    set of columns — host, port, username, password, relay_channel, pulse_ms.

    Anything that didn't fit those seven fields could not be configured at
    all without a database migration, and the list of allowed vendors was a
    hardcoded CHECK constraint, so even *naming* a new protocol needed a
    schema change. That made "this customer's site uses X" a development
    task rather than an administration task.

    This module is the single source of truth for what each protocol needs.
    It lives in shared/ because three consumers must agree on it:

      - the API, which validates submitted config and encrypts secrets
      - the admin UI, which RENDERS THE FORM FROM THIS DECLARATION rather
        than hardcoding fields per vendor — so adding a protocol needs no
        frontend change at all
      - the drivers, which read the values back out

DESIGN: COLUMNS FOR THE COMMON, JSONB FOR THE TAIL
    Fields with `column=` map to real table columns (host, port, username,
    password, relay_channel, pulse_ms). They are near-universal, worth having
    queryable, and `password` already has encryption wiring behind it.

    Every other field lands in the row's `config` JSONB. That is the part
    that varies per protocol, and it is the reason a new protocol no longer
    needs a migration.

HONESTY NOTE
    The six protocols declared here are the drivers that exist. Their field
    lists were read out of the driver implementations, not invented. Only the
    simulator has been exercised against a real device — see
    services/barrier/__init__.py's HARDWARE VERIFICATION STATUS block. This
    module makes protocols *configurable*; it does not make an unverified
    driver correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FieldType = Literal["string", "int", "bool", "select", "secret"]

# Device families. Each names a table that stores devices of that kind, so a
# family is also the scope within which protocol keys must be unique.
FAMILY_BARRIER = "barrier"
FAMILY_ALARM_PANEL = "alarm_panel"
FAMILIES = (FAMILY_BARRIER, FAMILY_ALARM_PANEL)


@dataclass(frozen=True)
class ProtocolField:
    """One configurable parameter, described well enough for a UI to render an
    input for it without knowing anything about the protocol."""

    name: str
    label: str
    type: FieldType = "string"
    required: bool = False
    default: Any = None
    help: str | None = None
    # select only
    options: tuple[str, ...] = ()
    # int only — rendered as input bounds AND enforced server-side.
    min: int | None = None
    max: int | None = None
    # When set, this field is stored in a real table column of that name
    # rather than in the `config` JSONB. See DESIGN note above.
    column: str | None = None

    @property
    def is_secret(self) -> bool:
        return self.type == "secret"


@dataclass(frozen=True)
class ProtocolSpec:
    key: str
    family: str
    label: str
    description: str
    fields: tuple[ProtocolField, ...]
    # Surfaced to the UI so an admin isn't offered a "Close" button for a
    # device family that physically cannot close under remote command.
    supports_close: bool = True
    # False for drivers written from a published spec but never run against
    # real hardware. The UI shows this as a warning next to the protocol.
    hardware_verified: bool = False


# ── Reusable field definitions ───────────────────────────────────────────────
# Defined once so "port" means the same thing, with the same bounds, in every
# protocol that has one.

_HOST = ProtocolField(
    name="host", label="IP address / hostname", type="string", required=True,
    column="host", help="Device address on the site LAN.",
)
_USERNAME = ProtocolField(
    name="username", label="Username", type="string", required=True, column="username",
)
_PASSWORD = ProtocolField(
    name="password", label="Password", type="secret", required=True, column="password",
    help="Stored encrypted at rest; never returned by the API once saved.",
)
_PULSE = ProtocolField(
    name="pulse_ms", label="Pulse length (ms)", type="int", default=1000,
    min=50, max=30000, column="pulse_ms",
    help="How long the open signal is held. Too short and the boom won't latch.",
)


def _port(default: int) -> ProtocolField:
    return ProtocolField(
        name="port", label="Port", type="int", default=default,
        min=1, max=65535, column="port",
    )


def _relay_channel(maximum: int, help_text: str) -> ProtocolField:
    return ProtocolField(
        name="relay_channel", label="Relay / output channel", type="int",
        required=True, default=1, min=1, max=maximum, column="relay_channel",
        help=help_text,
    )


# ── Barrier protocols ────────────────────────────────────────────────────────

_BARRIER_PROTOCOLS: tuple[ProtocolSpec, ...] = (
    ProtocolSpec(
        key="simulator",
        family=FAMILY_BARRIER,
        label="Simulator (no hardware)",
        description=(
            "Software-only barrier for commissioning, demos and testing. "
            "Accepts every command and tracks an in-memory position."
        ),
        fields=(_PULSE,),
        hardware_verified=True,  # the one driver the test suite fully exercises
    ),
    ProtocolSpec(
        key="hikvision",
        family=FAMILY_BARRIER,
        label="Hikvision access controller (ISAPI)",
        description="Dedicated Hikvision controller reached over its ISAPI HTTP interface.",
        fields=(_HOST, _port(80), _USERNAME, _PASSWORD, _PULSE),
    ),
    ProtocolSpec(
        key="dahua",
        family=FAMILY_BARRIER,
        label="Dahua access controller (HTTP CGI)",
        description="Dedicated Dahua controller reached over its HTTP CGI interface.",
        fields=(_HOST, _port(80), _USERNAME, _PASSWORD, _PULSE),
        # Several Dahua barrier models expose no remote close — the driver
        # reports unsupported() rather than pretending. Surfaced here so the
        # admin knows before wiring the site, not after.
        supports_close=False,
    ),
    ProtocolSpec(
        key="hikvision_camera_io",
        family=FAMILY_BARRIER,
        label="Hikvision ANPR camera relay output",
        description=(
            "The ANPR camera's own relay output drives the boom directly — no "
            "separate controller. This is how most gate installations are wired."
        ),
        fields=(
            _HOST, _port(80), _USERNAME, _PASSWORD,
            _relay_channel(4, "Which alarm output on the camera is wired to the boom."),
            _PULSE,
        ),
    ),
    ProtocolSpec(
        key="dahua_camera_io",
        family=FAMILY_BARRIER,
        label="Dahua ANPR camera relay output",
        description="As above, for Dahua ANPR cameras driving the boom from their own output.",
        fields=(
            _HOST, _port(80), _USERNAME, _PASSWORD,
            _relay_channel(4, "Which alarm output on the camera is wired to the boom."),
            _PULSE,
        ),
    ),
    ProtocolSpec(
        key="relay",
        family=FAMILY_BARRIER,
        label="Network relay board",
        description="Standalone LAN relay board switched by an HTTP call.",
        fields=(
            _HOST, _port(80),
            _relay_channel(16, "Board channel wired to the boom's open input."),
            _PULSE,
            # Boards in this category are all slightly different, which is
            # exactly the case the old fixed-column schema could not express.
            ProtocolField(
                name="open_path", label="Open URL path", type="string",
                default="/relay/{channel}/on",
                help="Path called to energise the relay. {channel} is substituted.",
            ),
            ProtocolField(
                name="close_path", label="Close URL path", type="string",
                default="/relay/{channel}/off",
                help="Leave blank if the board only supports a momentary pulse.",
            ),
            ProtocolField(
                name="auth_scheme", label="Authentication", type="select",
                default="none", options=("none", "basic", "digest"),
            ),
            _USERNAME, _PASSWORD,
        ),
    ),
)

_ALARM_PANEL_PROTOCOLS: tuple[ProtocolSpec, ...] = (
    ProtocolSpec(
        key="generic_tcp",
        family=FAMILY_ALARM_PANEL,
        label="Generic TCP panel",
        description="Alarm panel reachable over a raw TCP receiver port.",
        fields=(_HOST, _port(9000)),
    ),
)

_ALL: tuple[ProtocolSpec, ...] = _BARRIER_PROTOCOLS + _ALARM_PANEL_PROTOCOLS

PROTOCOLS: dict[tuple[str, str], ProtocolSpec] = {(p.family, p.key): p for p in _ALL}


def get_protocol(family: str, key: str) -> ProtocolSpec | None:
    return PROTOCOLS.get((family, key))


def protocols_for_family(family: str) -> list[ProtocolSpec]:
    return [p for p in _ALL if p.family == family]


def known_keys(family: str) -> tuple[str, ...]:
    return tuple(p.key for p in _ALL if p.family == family)
