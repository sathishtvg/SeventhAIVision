"""The AI module: what it offers, whether an organisation has it, and who may use each part.

Design: docs/plans/2026-10-26-ai-module-design.md. Reference: AI_MODULE.md.

THREE GATES, ALL OF WHICH MUST BE OPEN for a feature to do anything:
  1. the Super Admin's licence (ai_module_licenses): on, not expired, and the
     feature among those bought;
  2. the company admin's switch (ai_feature_settings.enabled);
  3. the person's role among the roles the admin chose.
The first is the platform owner's, the second and third the customer's. Neither
can open the other's.

A FEATURE THAT IS NOT BUILT YET can be licensed (the Super Admin may sell ahead)
but not switched on: `ready` is False until its phase ships, and the admin's
switch is refused until then rather than turning on something that does nothing.

THE AI SUGGESTS, A PERSON DECIDES. Nothing in this module acts on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEARNING, ASSISTANT = "learning", "assistant"
PROVIDERS = ("LOCAL", "HOSTED")
SENSITIVITIES = ("LOW", "MEDIUM", "HIGH")

#: Roles that may be given a learning feature: the warnings are about other
#: people's work, so a guard (5) and a viewer (6) are not offered them.
INSIGHT_ROLES = (2, 8, 3, 4)
#: Roles that may be given the assistant. Not the client (7), an outside party,
#: and not the platform owner (1), who sees no customer's data.
ASSISTANT_ROLES = (2, 8, 3, 4, 6, 5)


@dataclass(frozen=True)
class Feature:
    key: str
    title: str
    group: str
    description: str
    allowed_roles: tuple[int, ...]
    default_roles: tuple[int, ...]
    phase: int
    ready: bool = False


FEATURES: dict[str, Feature] = {f.key: f for f in (
    Feature("late_risk", "Late and no-show warnings", LEARNING,
            "Shifts in the next 36 hours likely to start late or be missed, so a standby can be lined up.",
            INSIGHT_ROLES, (2, 8, 3), 2),
    Feature("overtime_fatigue", "Overtime and fatigue forecast", LEARNING,
            "Guards heading over their monthly overtime or working too long without rest.",
            INSIGHT_ROLES, (2, 8, 3), 2),
    Feature("false_alarm", "False-alarm learning", LEARNING,
            "Alerts likely to be false alarms, and cameras and hours worth a sensitivity review.",
            INSIGHT_ROLES, (2, 8, 3, 4), 3),
    Feature("device_failure", "Device failure warnings", LEARNING,
            "Cameras and devices likely to go down soon, as a suggested maintenance job.",
            INSIGHT_ROLES, (2, 8, 3), 3),
    Feature("roster_fit", "Roster suggestions", LEARNING,
            "A fit score for each guard, site and shift, shown beside the auto-schedule.",
            INSIGHT_ROLES, (2, 8, 3), 4),
    Feature("patrol_timing", "Best patrol times", LEARNING,
            "When problems happen at each site, and patrol times to match.",
            INSIGHT_ROLES, (2, 8, 3), 4),
    Feature("patrol_miss_risk", "Missed-patrol warnings", LEARNING,
            "Patrols likely to be missed or to fail.",
            INSIGHT_ROLES, (2, 8, 3), 4),
    Feature("guard_trends", "Guard work trends", LEARNING,
            "Each guard's punctuality, patrol completion and response times over time.",
            INSIGHT_ROLES, (2, 8, 3), 4),
    Feature("incident_helper", "Incident helper", LEARNING,
            "A suggested category and severity for a new incident, and similar past incidents.",
            INSIGHT_ROLES, (2, 8, 3, 4), 5),
    Feature("payroll_check", "Payroll check", LEARNING,
            "Pay lines far from a guard's own normal, before payroll is finalised.",
            (2, 8), (2, 8), 5),
    Feature("assistant", "AI assistant", ASSISTANT,
            "Ask anything about the app and its data; it prepares changes for you to confirm.",
            ASSISTANT_ROLES, (2, 8, 3), 6),
    Feature("ic_reading", "IC reading", ASSISTANT,
            "Reads IC front and back photos to fill in a new staff member's details.",
            (2, 8), (2, 8), 8),
)}


# ── The licence ──────────────────────────────────────────────────────────────

async def load_licence(db: AsyncSession) -> dict | None:
    """The calling organisation's licence row, or None. RLS scopes the read and
    tenant_id is unique, so there is at most one."""
    row = (await db.execute(text(
        "SELECT * FROM ai_module_licenses "
        " WHERE tenant_id = current_setting('app.current_tenant')::uuid"
    ))).mappings().first()
    return dict(row) if row else None


def licence_problem(lic: dict | None, now: datetime | None = None) -> str | None:
    """Why the module is unavailable, in words an administrator can act on, or None."""
    now = now or datetime.now(timezone.utc)
    if lic is None or not lic["is_enabled"]:
        return ("The AI module is not licensed for this organisation. "
                "Ask your platform administrator to enable it.")
    expires = lic.get("expires_at")
    if expires is not None and expires <= now:
        return (f"The AI module licence expired on {expires:%d %b %Y}. "
                "Ask your platform administrator to renew it.")
    return None


def licensed_features(lic: dict | None, now: datetime | None = None) -> set[str]:
    """The features the organisation may switch on now: none at all when the licence is off."""
    if licence_problem(lic, now) is not None:
        return set()
    return {k for k in (lic or {}).get("features") or () if k in FEATURES}


def unknown_features(keys: list[str]) -> list[str]:
    return sorted(k for k in set(keys) if k not in FEATURES)


# ── The company's controls ───────────────────────────────────────────────────

async def load_settings(db: AsyncSession) -> dict[str, dict]:
    """feature_key -> the admin's row, for the calling organisation."""
    rows = (await db.execute(text(
        "SELECT feature_key, enabled, role_ids, site_ids, sensitivity, learning_enabled, "
        "       updated_by_user_id, updated_at "
        "  FROM ai_feature_settings"
    ))).mappings().all()
    return {r["feature_key"]: dict(r) for r in rows}


def default_setting(feature: Feature) -> dict:
    """What an organisation that has never touched a feature has: off, with the
    usual roles ready for when it is switched on."""
    return {"enabled": False, "role_ids": list(feature.default_roles), "site_ids": None,
            "sensitivity": "MEDIUM", "learning_enabled": True,
            "updated_by_user_id": None, "updated_at": None}


def setting_problem(feature: Feature, *, enabled: bool, role_ids: list[int],
                    sensitivity: str, licensed: set[str]) -> str | None:
    """Why the admin may not save this, or None. Switching OFF is always allowed:
    an organisation must be able to stop a feature whatever its licence says."""
    bad_roles = sorted(set(role_ids) - set(feature.allowed_roles))
    if bad_roles:
        return (f"{feature.title} cannot be given to role(s) {', '.join(map(str, bad_roles))}. "
                f"It may be given to roles {', '.join(map(str, feature.allowed_roles))}.")
    if sensitivity not in SENSITIVITIES:
        return f"Sensitivity must be one of {', '.join(SENSITIVITIES)}."
    if not enabled:
        return None
    if feature.key not in licensed:
        return (f"{feature.title} is not in this organisation's AI licence. "
                "Ask your platform administrator to add it.")
    if not feature.ready:
        return f"{feature.title} is not available yet. It arrives in a later release."
    if not role_ids:
        return f"Choose at least one role that uses {feature.title}."
    return None


def usable_by(feature: Feature, setting: dict, licensed: set[str], role_id: int) -> bool:
    """All three gates: licensed, switched on (and built), and this person's role chosen."""
    return (feature.key in licensed and feature.ready and bool(setting.get("enabled"))
            and role_id in (setting.get("role_ids") or ()))


async def status_for(db: AsyncSession, role_id: int) -> dict:
    """What the module is for the caller's organisation, and what of it the caller may use."""
    lic = await load_licence(db)
    problem = licence_problem(lic)
    licensed = licensed_features(lic)
    settings = await load_settings(db)
    out = []
    for f in FEATURES.values():
        s = settings.get(f.key) or default_setting(f)
        out.append({"key": f.key, "title": f.title, "group": f.group, "description": f.description,
                    "ready": f.ready, "licensed": f.key in licensed, "enabled": bool(s["enabled"]),
                    "mine": usable_by(f, s, licensed, role_id)})
    return {"licensed": problem is None, "reason": problem,
            "expires_at": lic["expires_at"] if lic else None,
            "assistant_provider": lic["assistant_provider"] if lic and problem is None else None,
            "features": out}
