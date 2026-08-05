"""Validate and partition submitted hardware configuration.

The registry in shared/shared/device_protocols.py says what each protocol
needs. This module enforces it on write and splits a submitted payload into
the two places it is stored:

    columns  — the common fields that are real table columns
    config   — everything protocol-specific, destined for the JSONB column

Secrets are returned separately and never placed in `config`, so a password
cannot end up sitting in plaintext inside a JSONB blob. The caller encrypts
them through app.core.crypto before they touch the database — the same path
barriers.password_encrypted already used.
"""
from __future__ import annotations

from typing import Any

from shared.device_protocols import (
    FAMILIES,
    ProtocolField,
    ProtocolSpec,
    get_protocol,
    known_keys,
    protocols_for_family,
)


class ConfigError(ValueError):
    """Invalid device configuration. Message is safe to show an admin."""


def _coerce(f: ProtocolField, raw: Any) -> Any:
    if f.type == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{f.label}: must be a whole number") from None
        if f.min is not None and value < f.min:
            raise ConfigError(f"{f.label}: must be at least {f.min}")
        if f.max is not None and value > f.max:
            raise ConfigError(f"{f.label}: must be at most {f.max}")
        return value

    if f.type == "bool":
        if isinstance(raw, bool):
            return raw
        raise ConfigError(f"{f.label}: must be true or false")

    if f.type == "select":
        value = str(raw)
        if value not in f.options:
            raise ConfigError(f"{f.label}: must be one of {', '.join(f.options)}")
        return value

    value = str(raw)
    # Blank is only an error on a REQUIRED field. For an optional one it is a
    # deliberate "this device doesn't have that" — the relay driver's
    # close_path is exactly this: boards that only support a momentary pulse
    # have no close URL, and the field's own help text says to leave it empty.
    if f.required and not value.strip():
        raise ConfigError(f"{f.label}: cannot be blank")
    return value


def resolve_spec(family: str, protocol_key: str) -> ProtocolSpec:
    if family not in FAMILIES:
        raise ConfigError(f"unknown device family '{family}'")
    spec = get_protocol(family, protocol_key)
    if spec is None:
        raise ConfigError(
            f"unknown protocol '{protocol_key}' for {family}; "
            f"supported: {', '.join(known_keys(family))}"
        )
    return spec


def split_config(
    family: str,
    protocol_key: str,
    submitted: dict[str, Any],
    *,
    partial: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Returns (column_values, jsonb_config, secrets).

    `partial=True` skips the required-field check, for PATCH-style updates
    where the caller is changing one value and the rest already exists. It
    still validates and coerces whatever WAS submitted — a partial update is
    not an excuse to accept a bad port number.

    Unknown keys are rejected rather than ignored. Silently dropping a field
    an admin believed they set is how a gate ends up misconfigured with a UI
    that looks correct.
    """
    spec = resolve_spec(family, protocol_key)
    by_name = {f.name: f for f in spec.fields}

    unknown = set(submitted) - set(by_name)
    if unknown:
        raise ConfigError(
            f"{spec.label}: unsupported field(s) {', '.join(sorted(unknown))}; "
            f"this protocol accepts {', '.join(sorted(by_name))}"
        )

    columns: dict[str, Any] = {}
    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}

    for name, f in by_name.items():
        present = name in submitted and submitted[name] is not None
        if not present:
            if f.required and not partial:
                raise ConfigError(f"{spec.label}: {f.label} is required")
            # A default only materialises on create; on a partial update the
            # stored value stays untouched.
            if f.default is not None and not partial:
                if f.column:
                    columns[f.column] = f.default
                else:
                    config[name] = f.default
            continue

        value = _coerce(f, submitted[name])
        if f.is_secret:
            secrets[f.column or name] = value
        elif f.column:
            columns[f.column] = value
        else:
            config[name] = value

    return columns, config, secrets


def validate_extra_config(
    family: str, protocol_key: str, config: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate only the protocol-specific fields — the ones stored in JSONB.

    Used by routers whose common fields (host, port, …) are already typed
    Pydantic attributes mapping to real columns. Those keep their existing
    validation; this covers the tail that previously had nowhere to live, so
    the change is additive rather than a rewrite of the request model.

    Missing fields fall back to their declared defaults, so a protocol that
    gains a field later doesn't leave existing rows with a hole in them.
    """
    spec = resolve_spec(family, protocol_key)
    by_name = {f.name: f for f in spec.fields if not f.column}
    submitted = config or {}

    unknown = set(submitted) - set(by_name)
    if unknown:
        accepted = ", ".join(sorted(by_name)) or "no protocol-specific fields"
        raise ConfigError(
            f"{spec.label}: unsupported config field(s) {', '.join(sorted(unknown))}; "
            f"accepts {accepted}"
        )

    out: dict[str, Any] = {}
    for name, f in by_name.items():
        if name in submitted and submitted[name] is not None:
            out[name] = _coerce(f, submitted[name])
        elif f.default is not None:
            out[name] = f.default
        elif f.required:
            raise ConfigError(f"{spec.label}: {f.label} is required")
    return out


def describe_family(family: str) -> dict[str, Any]:
    """The catalogue an admin UI renders its forms from.

    Served rather than hardcoded in the frontend for the same reason as the
    recording-policy and alert-rule catalogues: adding a protocol should be a
    backend change only.
    """
    if family not in FAMILIES:
        raise ConfigError(f"unknown device family '{family}'")
    return {
        "family": family,
        "protocols": [
            {
                "key": p.key,
                "label": p.label,
                "description": p.description,
                "supports_close": p.supports_close,
                "hardware_verified": p.hardware_verified,
                "fields": [
                    {
                        "name": f.name,
                        "label": f.label,
                        "type": f.type,
                        "required": f.required,
                        "default": f.default,
                        "help": f.help,
                        "options": list(f.options),
                        "min": f.min,
                        "max": f.max,
                        "secret": f.is_secret,
                    }
                    for f in p.fields
                ],
            }
            for p in protocols_for_family(family)
        ],
    }
