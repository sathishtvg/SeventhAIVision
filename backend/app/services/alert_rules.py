"""Effective AI alert rules = shipped defaults + this tenant's overrides.

The API's job here is to never make the caller compute the merge itself. A UI
that reconstructed "default unless overridden" in TypeScript would eventually
disagree with what the workers actually apply, and the disagreement would be
about what severity pages the on-call rota.

So every read returns the FULL matrix — one row per (module, trigger) that the
platform knows about — each flagged with whether it is a default or an
override. There is no endpoint that returns overrides alone.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.alert_rules import (
    DEFAULT_ALERT_RULES,
    SEVERITIES,
    AlertRule,
    get_default_rule,
)

RULE_COLUMNS = (
    "id, tenant_id, module_type, trigger_key, severity, create_incident, "
    "incident_severity, is_enabled, updated_by_user_id, created_at, updated_at"
)


async def fetch_overrides(session: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    result = await session.execute(text(f"SELECT {RULE_COLUMNS} FROM alert_rules"))
    return {
        (r.module_type, r.trigger_key): dict(r._mapping) for r in result
    }


async def get_effective_rules(session: AsyncSession) -> list[dict[str, Any]]:
    """Every known (module, trigger), merged. Ordered module then trigger so
    the UI renders a stable matrix rather than reshuffling on each save."""
    overrides = await fetch_overrides(session)
    rows: list[dict[str, Any]] = []

    for (module_type, trigger_key), default in DEFAULT_ALERT_RULES.items():
        override = overrides.get((module_type, trigger_key))
        if override is None:
            rows.append({
                "module_type": module_type,
                "trigger_key": trigger_key,
                "severity": default.severity,
                "create_incident": default.create_incident,
                "incident_severity": default.incident_severity,
                "is_enabled": True,
                "is_overridden": False,
                "default_severity": default.severity,
                "default_create_incident": default.create_incident,
                "default_incident_severity": default.incident_severity,
            })
        else:
            rows.append({
                "module_type": module_type,
                "trigger_key": trigger_key,
                "severity": override["severity"],
                "create_incident": override["create_incident"],
                "incident_severity": override["incident_severity"],
                "is_enabled": override["is_enabled"],
                "is_overridden": True,
                "default_severity": default.severity,
                "default_create_incident": default.create_incident,
                "default_incident_severity": default.incident_severity,
                "updated_at": override["updated_at"],
            })

    # An override for a (module, trigger) the platform no longer ships — e.g.
    # a module removed in a later release. Surfaced rather than hidden so an
    # admin can see and delete it, instead of wondering why a rule they set
    # has no visible effect.
    for (module_type, trigger_key), override in overrides.items():
        if (module_type, trigger_key) in DEFAULT_ALERT_RULES:
            continue
        rows.append({
            "module_type": module_type,
            "trigger_key": trigger_key,
            "severity": override["severity"],
            "create_incident": override["create_incident"],
            "incident_severity": override["incident_severity"],
            "is_enabled": override["is_enabled"],
            "is_overridden": True,
            "is_orphaned": True,
            "default_severity": None,
            "default_create_incident": None,
            "default_incident_severity": None,
        })

    rows.sort(key=lambda r: (r["module_type"], r["trigger_key"]))
    return rows


def validate_rule(
    severity: str, create_incident: bool, incident_severity: str | None
) -> None:
    """Raise ValueError on an inconsistent rule.

    Reuses AlertRule's own __post_init__ so the API, the workers and the DB
    CHECK constraint cannot drift into three different opinions about what a
    valid rule is.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    if incident_severity is not None and incident_severity not in SEVERITIES:
        raise ValueError(f"incident_severity must be one of {', '.join(SEVERITIES)}")
    AlertRule(
        severity=severity,
        create_incident=create_incident,
        incident_severity=incident_severity,
    )


def is_known_trigger(module_type: str, trigger_key: str) -> bool:
    return get_default_rule(module_type, trigger_key) is not None
