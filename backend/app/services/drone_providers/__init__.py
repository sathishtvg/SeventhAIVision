"""Drone provider adapters, by catalogue key.

The catalogue (services/drone_provider_registry.py) says what providers exist and
what settings they take; this says which class talks to each. A key in one and
not the other is a configuration an administrator could save and nothing could
fly, so the two are checked against each other in the tests.
"""
from __future__ import annotations

from app.services.drone_providers.base import (  # noqa: F401  (re-exported)
    Capability, CapabilityNotSupported, DroneHealth, DroneProvider, DroneRef, FlightContext,
    FlightEvent, FlightUpdate, ProviderError, ProviderUnavailable, TelemetrySample,
)
from app.services.drone_providers.simulator import SimulatorProvider

ADAPTERS: dict[str, type[DroneProvider]] = {
    SimulatorProvider.key: SimulatorProvider,
}


def get_adapter(provider_key: str, config: dict | None = None,
                secrets: dict | None = None) -> DroneProvider:
    cls = ADAPTERS.get(provider_key)
    if cls is None:
        raise ProviderUnavailable(f"No adapter is installed for provider {provider_key!r}.")
    return cls(config, secrets)


def capabilities_of(provider_key: str | None) -> frozenset[Capability]:
    """What a provider can do, without instantiating it — for the API to refuse a
    command at request time."""
    cls = ADAPTERS.get(provider_key or "")
    return cls.capabilities if cls else frozenset()
