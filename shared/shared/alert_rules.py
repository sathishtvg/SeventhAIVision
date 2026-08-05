"""Canonical alert/incident decision table (Module 14).

WHY THIS FILE EXISTS
    "A person in a restricted zone is a `high` alert and opens an incident" is
    a *policy* decision, not a technical one. Until now it lived as Python
    constants inside eleven separate worker tasks — `ALERT_RULES` in lpr_task
    and face_task, `WEAPON_SEVERITIES`/`AUTO_INCIDENT_WEAPON_TYPES` in
    weapon_model, `BEHAVIOR_SEVERITIES` in behavior_model, bare `SEVERITY = "high"`
    module constants in tampering/abandoned/fall, and so on.

    That means changing "loitering should be low, not medium, at our sites"
    required a code change, a rebuild, and a redeploy of an AI worker — for a
    setting that differs legitimately between a shopping mall and a data
    centre.

    This module is the single source of truth for the DEFAULTS. It lives in
    `shared/` because both the API (which serves and validates rule overrides)
    and the AI workers (which apply them) must agree on what the baseline is;
    two copies would eventually disagree, and the disagreement would be about
    whether a weapon detection opens an incident.

WHAT IS AND ISN'T CONFIGURABLE
    Configurable: severity, whether an incident is auto-created, and the
    incident's severity.

    NOT configurable: `alert_code`. That is the stable machine key that
    `message_params` is rendered against for i18n (plan §16.3). Letting a
    tenant rename it would break the translation lookup and make historical
    alerts unreadable — it identifies *what happened*, not *how much we care*.

    Also not configurable here: detection thresholds. Those already live in
    `tenant_settings` (e.g. `lpr.confidence_threshold`) and answer a different
    question — "is this real?" rather than "how much does it matter?".

EVERY VALUE BELOW WAS READ OUT OF THE RUNNING CODE, NOT CHOSEN.
    This table must reproduce today's behaviour exactly, because it is the
    fallback used whenever a tenant has configured nothing — which is every
    tenant on the day this ships. A "tidier" default here would be a silent
    behaviour change to live alerting.
"""
from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = ("info", "low", "medium", "high", "critical")


@dataclass(frozen=True)
class AlertRule:
    """What a given (module, trigger) should produce.

    `incident_severity` is None exactly when `create_incident` is False.
    """

    severity: str
    create_incident: bool
    incident_severity: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.create_incident and self.incident_severity is None:
            raise ValueError("create_incident=True requires an incident_severity")
        if not self.create_incident and self.incident_severity is not None:
            raise ValueError("incident_severity set but create_incident=False")


def _alerting(severity: str) -> AlertRule:
    """Alert only, no incident — the common shape for lower tiers."""
    return AlertRule(severity=severity, create_incident=False)


def _escalating(severity: str) -> AlertRule:
    """Alert plus an auto-created incident at the same severity. Every module
    that escalates today mirrors the alert severity onto the incident; none
    diverges, so this helper encodes that rather than repeating it 12 times."""
    return AlertRule(severity=severity, create_incident=True, incident_severity=severity)


# Keyed (module_type, trigger_key). `trigger_key` is whatever discriminates
# outcomes within a module: a watchlist verdict for lpr/face, the zone's own
# severity for intrusion/crowd, the detected class for weapon/behavior/
# fire_smoke, and a single fixed key for modules with only one outcome.
DEFAULT_ALERT_RULES: dict[tuple[str, str], AlertRule] = {
    # lpr_task.ALERT_RULES. An unmatched plate produces no alert at all and so
    # has no rule — most plates aren't list-worthy and alerting on them would
    # bury the ones that are.
    ("lpr", "block"): _escalating("critical"),
    ("lpr", "allow"): _alerting("low"),

    # face_task.ALERT_RULES + UNRECOGNIZED_ALERT_CODE. Note the deliberate
    # asymmetry with lpr: an unrecognized *face* does alert (at info), because
    # an unknown person on a monitored site is itself the signal.
    ("face", "block"): _escalating("high"),
    ("face", "allow"): _alerting("low"),
    ("face", "unrecognized"): _alerting("info"),

    # intrusion_task / crowd_task: severity comes from the breached zone, and
    # AUTO_INCIDENT_SEVERITIES = {"high", "critical"} in both files.
    ("intrusion", "zone_low"): _alerting("low"),
    ("intrusion", "zone_medium"): _alerting("medium"),
    ("intrusion", "zone_high"): _escalating("high"),
    ("intrusion", "zone_critical"): _escalating("critical"),
    ("crowd", "zone_low"): _alerting("low"),
    ("crowd", "zone_medium"): _alerting("medium"),
    ("crowd", "zone_high"): _escalating("high"),
    ("crowd", "zone_critical"): _escalating("critical"),

    # weapon_model.WEAPON_SEVERITIES + AUTO_INCIDENT_WEAPON_TYPES={firearm,blade}
    ("weapon", "firearm"): _escalating("critical"),
    ("weapon", "blade"): _escalating("high"),
    ("weapon", "blunt"): _alerting("medium"),

    # behavior_model.BEHAVIOR_SEVERITIES + AUTO_INCIDENT_BEHAVIORS={aggression,tailgating}
    ("behavior", "loitering"): _alerting("medium"),
    ("behavior", "running"): _alerting("low"),
    ("behavior", "aggression"): _escalating("critical"),
    ("behavior", "tailgating"): _escalating("high"),

    # fire_smoke_task.DETECTION_SEVERITIES; both escalate unconditionally.
    ("fire_smoke", "fire"): _escalating("critical"),
    ("fire_smoke", "smoke"): _escalating("high"),

    # Single-outcome modules: one module-level SEVERITY constant each.
    ("ppe", "violation"): _escalating("high"),          # ppe_task.ALERT_SEVERITY
    ("tampering", "detected"): _escalating("high"),     # tampering_task.SEVERITY
    ("fall", "detected"): _escalating("high"),          # fall_task.SEVERITY
    # abandoned_task.SEVERITY = "medium" and creates no incident — an
    # unattended bag is worth a look, not an automatic incident record.
    ("abandoned", "detected"): _alerting("medium"),
}

# Trigger keys per module, in the order a UI should present them. Derived from
# the table above rather than re-listed, so the two can't drift.
MODULE_TRIGGERS: dict[str, tuple[str, ...]] = {}
for _module, _trigger in DEFAULT_ALERT_RULES:
    MODULE_TRIGGERS.setdefault(_module, ())
    MODULE_TRIGGERS[_module] += (_trigger,)
del _module, _trigger


def get_default_rule(module_type: str, trigger_key: str) -> AlertRule | None:
    """The shipped default, or None for a (module, trigger) that produces no
    alert at all (e.g. an unmatched licence plate)."""
    return DEFAULT_ALERT_RULES.get((module_type, trigger_key))
