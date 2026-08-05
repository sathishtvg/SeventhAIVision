"""Tenant alert-rule overrides, read side (Module 14).

Same shape and reasoning as tenant_settings_cache: a 30s in-process TTL cache
refreshed lazily on read, so a tenant with no frames in flight costs zero DB
work. 30s of staleness is acceptable here for the same reason it is there —
this is alert *tuning*, and a rule change that takes half a minute to reach
every worker is not a correctness problem.

One difference worth noting: this caches ALL of a tenant's rules in a single
entry, not one entry per rule. A worker processing a frame typically needs one
rule, but a tenant has at most a couple of dozen, they are wanted together
whenever any is, and fetching them per-key would turn one query per 30s into
one query per (rule, 30s).

Fail-open by design: any DB error resolves to the shipped default rather than
raising. A detection that fires at its default severity is a far better outcome
than a detection lost inside an exception because a config table was briefly
unreachable.
"""
from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any

from shared.alert_rules import AlertRule, get_default_rule

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
# tenant_id -> ({(module, trigger): AlertRule | None}, fetched_at). A None value
# means "explicitly disabled by this tenant", which is distinct from the key
# being absent ("no override, use the default").
_cache: dict[str, tuple[dict[tuple[str, str], AlertRule | None], float]] = {}
_lock = Lock()


def _fetch_rules(conn, tenant_id: Any) -> dict[tuple[str, str], AlertRule | None]:
    overrides: dict[tuple[str, str], AlertRule | None] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT module_type, trigger_key, severity, create_incident, "
            "       incident_severity, is_enabled "
            "FROM alert_rules WHERE tenant_id = %s",
            (str(tenant_id),),
        )
        for module_type, trigger_key, severity, create_incident, incident_severity, is_enabled in cur.fetchall():
            key = (module_type, trigger_key)
            if not is_enabled:
                overrides[key] = None
                continue
            try:
                overrides[key] = AlertRule(
                    severity=severity,
                    create_incident=create_incident,
                    incident_severity=incident_severity,
                )
            except ValueError:
                # The DB CHECK constraint should make this unreachable. If it
                # somehow happens, drop the bad override rather than the
                # detection — omitting the key falls through to the default.
                logger.warning(
                    "alert_rules: ignoring invalid row tenant=%s module=%s trigger=%s",
                    tenant_id, module_type, trigger_key,
                )
    return overrides


def _get_overrides(conn, tenant_id: Any) -> dict[tuple[str, str], AlertRule | None]:
    key = str(tenant_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
    try:
        overrides = _fetch_rules(conn, tenant_id)
    except Exception as exc:  # fail open — see module docstring
        logger.warning("alert_rules: lookup failed for tenant %s (%s); using defaults", tenant_id, exc)
        return {}
    with _lock:
        _cache[key] = (overrides, time.monotonic())
    return overrides


def resolve_rule(conn, tenant_id: Any, module_type: str, trigger_key: str) -> AlertRule | None:
    """The rule in force for this tenant, or None if no alert should fire.

    None has two causes that are deliberately indistinguishable to the caller,
    because the caller's action is the same in both: the tenant disabled this
    trigger, or the trigger has no default (e.g. an unmatched licence plate).

    `conn` must already have its RLS tenant context set, same contract as
    tenant_settings_cache.get_tenant_setting — this function does not open a
    transaction of its own.
    """
    overrides = _get_overrides(conn, tenant_id)
    key = (module_type, trigger_key)
    if key in overrides:
        return overrides[key]
    return get_default_rule(module_type, trigger_key)


def clear_cache() -> None:
    """Test hook, mirrors tenant_settings_cache.clear_cache."""
    with _lock:
        _cache.clear()
