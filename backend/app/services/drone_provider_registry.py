"""Which drone providers exist, and what each one's configuration looks like.

This is the catalogue, not the adapters. It says what a provider is called and
what settings it takes, so the fleet API can refuse a configuration the adapter
could never use. The adapters that actually talk to a drone (and the simulator)
arrive in Phase 4 and register against these same keys.

ONLY THE SIMULATOR, FOR NOW. No physical drone or manufacturer SDK has been
chosen, and listing one here would promise an integration that does not exist.
Adding a real provider is a new entry here plus its adapter — no migration,
because drone_provider_configs.provider_key is validated here rather than by a
CHECK constraint.

SECRETS NEVER TRAVEL WITH CONFIG. A field marked secret is split out, encrypted
with core.crypto, and stored in drone_provider_configs.secret_encrypted; it is
never returned by the API — only whether one is set. The same split
device_protocols makes for hardware passwords.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderField:
    key: str
    label: str
    kind: str = "string"          # string | integer | number | boolean
    required: bool = False
    secret: bool = False
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    name: str
    description: str
    simulated: bool
    fields: tuple[ProviderField, ...] = field(default_factory=tuple)


PROVIDERS: dict[str, ProviderSpec] = {
    "simulator": ProviderSpec(
        key="simulator",
        name="Simulator (no hardware)",
        description=("A simulated drone for development, training and testing. "
                     "It never commands a real aircraft."),
        simulated=True,
        fields=(
            ProviderField("speed_factor", "Time speed-up", kind="number",
                          help="1 = real time; 10 flies a 10-minute route in 1 minute.",
                          minimum=0.1, maximum=100),
            ProviderField("failure_rate", "Injected failure rate", kind="number",
                          help="Fraction of flights that fail on purpose, for testing (0–1).",
                          minimum=0, maximum=1),
        ),
    ),
}


class ProviderConfigError(ValueError):
    """A configuration the provider cannot use. Shown to an administrator."""


def get_provider(key: str) -> ProviderSpec | None:
    return PROVIDERS.get(key)


def catalogue() -> list[dict]:
    """The registry as the API returns it — field definitions, never values."""
    return [
        {
            "key": p.key, "name": p.name, "description": p.description,
            "simulated": p.simulated,
            "fields": [
                {"key": f.key, "label": f.label, "kind": f.kind, "required": f.required,
                 "secret": f.secret, "help": f.help, "minimum": f.minimum, "maximum": f.maximum}
                for f in p.fields
            ],
        }
        for p in PROVIDERS.values()
    ]


def _coerce(f: ProviderField, raw: Any) -> Any:
    try:
        if f.kind == "integer":
            value: Any = int(raw)
        elif f.kind == "number":
            value = float(raw)
        elif f.kind == "boolean":
            if isinstance(raw, bool):
                value = raw
            elif str(raw).lower() in ("true", "1", "yes"):
                value = True
            elif str(raw).lower() in ("false", "0", "no"):
                value = False
            else:
                raise ValueError
        else:
            value = str(raw)
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(f"{f.label} must be a {f.kind}.") from exc
    if f.minimum is not None and isinstance(value, (int, float)) and value < f.minimum:
        raise ProviderConfigError(f"{f.label} must be at least {f.minimum}.")
    if f.maximum is not None and isinstance(value, (int, float)) and value > f.maximum:
        raise ProviderConfigError(f"{f.label} must be at most {f.maximum}.")
    return value


def split_config(provider_key: str, submitted: dict[str, Any] | None,
                 *, has_existing_secret: bool = False) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a submitted configuration and split it into (config, secrets).

    Unknown keys are refused rather than stored: a typo in a key name would
    otherwise sit silently in JSONB while the adapter uses its default.
    """
    spec = get_provider(provider_key)
    if spec is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderConfigError(f"Unknown provider {provider_key!r}. Available: {known}.")
    submitted = submitted or {}
    known_keys = {f.key for f in spec.fields}
    unknown = sorted(set(submitted) - known_keys)
    if unknown:
        raise ProviderConfigError(f"Unknown setting(s) for {spec.name}: {', '.join(unknown)}.")

    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for f in spec.fields:
        raw = submitted.get(f.key)
        if raw is None or raw == "":
            if f.required and not (f.secret and has_existing_secret):
                raise ProviderConfigError(f"{f.label} is required for {spec.name}.")
            continue
        value = _coerce(f, raw)
        if f.secret:
            secrets[f.key] = str(value)
        else:
            config[f.key] = value
    return config, secrets
