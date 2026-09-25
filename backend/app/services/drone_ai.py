"""The drone context and risk engine — pure rules, no database.

A worker detection says "a person, 0.87". This module decides what that means on
this site, here, now: which zone the drone was over, whether the zone is in
force at this hour, whether the person or vehicle is authorised, whether it was
seen once or kept being seen, what has happened here before — and from that a
security risk with every reason written down.

AI CONFIDENCE IS NOT SECURITY RISK. Confidence is the model's certainty about
what it saw (0–1, from the worker, never altered here). Risk is this engine's
judgement of how much it matters (0–100, INFO…CRITICAL). They are separate
columns and are never shown as one number.

ONE FRAME IS NOT AN INCIDENT. An event is VERIFIED only once it has been seen
repeatedly or for long enough (the profile's verify_min_seconds); until then it
is OBSERVING and raises nothing. The exception is a high-confidence weapon or
fire: those are verified on first sight, because waiting is the greater risk.

GROUPING IS NOT TRACKING. The workers do not track objects across a moving
camera's frames, so this engine groups detections — same flight, same module,
same zone, same plate or face, close in time — into one event. It is called
grouping everywhere, and it can merge two people seen together.

Weights and thresholds are constants here, documented in DRONE_PATROL_AI.md;
what a tenant configures is per profile (base severity per module, minimum
confidence, verification time) and per zone (type, severity, detection
threshold, alert policy, allowed people and vehicles, active hours).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from app.services.drone_geometry import zone_contains

# ── What each worker can do on a moving camera ───────────────────────────────
#
# Established by reading every worker (ai-worker/worker/tasks), not by flying:
# no drone video exists yet. Accuracy at altitude is unverified for all of them.

SUPPORTED, NEEDS_IMAGE_ZONE, UNRELIABLE = "SUPPORTED", "NEEDS_IMAGE_ZONE", "UNRELIABLE"
SUITABILITY: dict[str, tuple[str, str]] = {
    "lpr": (SUPPORTED, "Reads plates frame by frame. Plates are small from altitude; fly low or zoom."),
    "face": (SUPPORTED, "Matches faces frame by frame. Faces are tiny from altitude; useful only low and close."),
    "fire_smoke": (SUPPORTED, "Classifies fire and smoke frame by frame."),
    "weapon": (SUPPORTED, "Classifies weapons frame by frame. Small objects; confidence falls with altitude."),
    "ppe": (SUPPORTED, "Checks each person for required PPE frame by frame."),
    "fall": (SUPPORTED, "Classifies a fallen pose frame by frame."),
    "intrusion": (NEEDS_IMAGE_ZONE, "Reports people only inside a zone drawn on the camera's picture. Give the drone "
                                    "camera a full-frame restricted zone and it reports every person in view."),
    "crowd": (NEEDS_IMAGE_ZONE, "Counts people only inside crowd zones drawn on the camera's picture."),
    "behavior": (UNRELIABLE, "Loitering and tailgating need a fixed view; running and aggression measure movement "
                             "between frames, which the drone's own motion corrupts."),
    "abandoned": (UNRELIABLE, "Needs an object to stay still in the picture; on a moving camera nothing does, "
                              "and while hovering everything does."),
    "tampering": (UNRELIABLE, "Compares each frame with a reference view; a moving camera changes view constantly "
                              "and would raise a tampering alert and incident each time."),
}

# ── Weights ──────────────────────────────────────────────────────────────────

LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
#: Score at or above which each level starts.
THRESHOLDS = {"LOW": 15, "MEDIUM": 35, "HIGH": 55, "CRITICAL": 80}
SEVERITY_POINTS = {"info": 5, "low": 20, "medium": 40, "high": 60, "critical": 80}
#: A module's base severity when the flight's profile has no rule for it.
DEFAULT_BASE_SEVERITY = {
    "intrusion": "low", "crowd": "low", "face": "low", "lpr": "low", "ppe": "medium", "behavior": "medium",
    "abandoned": "medium", "fall": "high", "fire_smoke": "high", "weapon": "critical", "tampering": "info",
}
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_VERIFY_SECONDS = 3
#: Detections of the same thing further apart than this are separate events.
GROUPING_WINDOW = timedelta(seconds=30)
VERIFY_DETECTIONS = 3
IMMEDIATE_MODULES = {"weapon", "fire_smoke"}
IMMEDIATE_CONFIDENCE = 0.8
NIGHT_START, NIGHT_END = time(22, 0), time(6, 0)

PERSON_MODULES = {"intrusion", "crowd", "behavior", "fall", "ppe", "face"}
#: Modules that report someone BEING somewhere — where who they are matters.
#: A fall or a missing hard hat is a safety matter whoever it is.
PRESENCE_MODULES = {"intrusion", "crowd", "face"}
VEHICLE_MODULES = {"lpr"}
ZONE_POINTS = {"CRITICAL": 25, "NO_ENTRY": 20, "RESTRICTED": 15, "SPECIAL_INSPECTION": 5, "NORMAL": 0}
ZONE_SEVERITY_POINTS = {"critical": 10, "high": 5}
#: Level → alert severity. INFO and LOW never alert.
ALERT_SEVERITY = {"MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class Sighting:
    """One detection, as the worker (or a gateway) reported it."""
    module_type: str
    detected_at: datetime
    confidence: float | None
    label: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    #: The worker's own verdict where it has one: "allow", "block" or None
    #: (unmatched plate, unrecognised face).
    watchlist: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Situation:
    """Everything around the sighting that the engine weighs."""
    local_time: datetime
    zone: dict | None
    #: Guards on shift at the site right now: [{user_id, role_id}].
    on_duty: list[dict] = field(default_factory=list)
    detection_count: int = 1
    observed_seconds: float = 0.0
    max_confidence: float | None = None
    verified: bool = False
    #: Other open events on this flight in the last two minutes, by module.
    concurrent_modules: set[str] = field(default_factory=set)
    #: Drone events in this zone (or at this site) in the last 30 days.
    history_events: int = 0
    #: Incidents raised at this site in the last 30 days.
    history_incidents: int = 0
    #: Fixed cameras whose own detection agrees with the drone's (phase 7).
    cctv: list[str] = field(default_factory=list)


@dataclass
class Risk:
    score: int
    level: str
    factors: list[dict]
    authorisation: str


# ── Rules and zones ──────────────────────────────────────────────────────────

def rule_for(rules: list[dict] | None, profile: dict | None, module: str) -> dict | None:
    """The rule in force for a module, or None when the flight is not looking
    for it. A flight with no profile looks for everything, at defaults."""
    if profile is None or not rules:
        return {"module_type": module, "is_enabled": True, "min_confidence": None,
                "base_severity": DEFAULT_BASE_SEVERITY.get(module, "low"), "incident_risk_level": None}
    for r in rules:
        if r.get("module_type") == module:
            return r if r.get("is_enabled", True) else None
    return None


def zone_active(zone: dict, local: datetime) -> bool:
    """A zone's active hours, in site-local time; one ending before it starts
    runs overnight. Weekdays are 0 = Monday."""
    days = zone.get("active_weekdays")
    if days and local.weekday() not in days:
        return False
    start, end = _t(zone.get("active_from")), _t(zone.get("active_to"))
    if start is None or end is None or start == end:
        return True
    now = local.time()
    return start <= now < end if start < end else (now >= start or now < end)


def _t(v) -> time | None:
    if v is None or isinstance(v, time):
        return v
    return time.fromisoformat(str(v))


def zone_at(zones: list[dict], lat: float | None, lng: float | None, local: datetime) -> dict | None:
    """The most serious active zone the drone was over."""
    if lat is None or lng is None:
        return None
    rank = {"CRITICAL": 6, "NO_ENTRY": 5, "RESTRICTED": 4, "PERSON_RESTRICTED": 3, "VEHICLE_RESTRICTED": 3,
            "SPECIAL_INSPECTION": 2, "NORMAL": 1}
    hits = [z for z in zones if z.get("is_active", True) and zone_active(z, local) and zone_contains(z, lat, lng)]
    return max(hits, key=lambda z: rank.get(z.get("zone_type"), 0), default=None)


def min_confidence(rule: dict | None, profile: dict | None, zone: dict | None) -> float:
    """The strictest threshold that applies: profile, module rule, zone."""
    vals = [float(v) for v in ((profile or {}).get("min_confidence"), (rule or {}).get("min_confidence"),
                               (zone or {}).get("detection_threshold")) if v is not None]
    return max(vals) if vals else DEFAULT_MIN_CONFIDENCE


def ignore_reason(s: Sighting, rule: dict | None, profile: dict | None, zone: dict | None) -> str | None:
    """Why a sighting does not become (part of) an event at all, or None."""
    if SUITABILITY.get(s.module_type, (UNRELIABLE,))[0] == UNRELIABLE:
        return f"{s.module_type} is not reliable on a moving camera"
    if rule is None:
        return f"the flight's security profile does not look for {s.module_type}"
    need = min_confidence(rule, profile, zone)
    if s.confidence is not None and s.confidence < need:
        return f"confidence {s.confidence:.2f} is below the {need:.2f} this flight requires"
    return None


def zone_applies(zone: dict | None, module: str) -> bool:
    """A person-restricted zone says nothing about a vehicle, and the reverse."""
    if zone is None:
        return False
    zt = zone.get("zone_type")
    if zt == "PERSON_RESTRICTED":
        return module in PERSON_MODULES
    if zt == "VEHICLE_RESTRICTED":
        return module in VEHICLE_MODULES
    return True


# ── Authorisation ────────────────────────────────────────────────────────────

def _plate(v: str | None) -> str:
    return "".join((v or "").split()).upper()


def authorisation(s: Sighting, zone: dict | None, on_duty: list[dict]) -> tuple[str, str]:
    """(verdict, why). Verdicts:
      BLOCKLISTED   on a watchlist as a threat
      AUTHORISED    on an allow list, or a plate the zone allows
      ON_DUTY       someone allowed here is on shift — the person may be them
      UNAUTHORISED  in a zone that restricts who may be there, and no one
                    allowed is on shift
      UNKNOWN       nothing to go on
    """
    if s.watchlist == "block":
        return "BLOCKLISTED", "on the blocklist"
    if s.watchlist == "allow":
        return "AUTHORISED", "on the allow list"
    applies = zone_applies(zone, s.module_type)
    zt = (zone or {}).get("zone_type")
    if s.module_type in VEHICLE_MODULES:
        allowed = {_plate(p) for p in (zone or {}).get("allowed_vehicle_plates") or []}
        if s.label and applies and _plate(s.label) in allowed:
            return "AUTHORISED", f"plate {s.label} is allowed in {zone.get('name')}"
        if applies and zt not in (None, "NORMAL"):
            return "UNAUTHORISED", f"vehicle not allowed in {zone.get('name')}"
        return "UNKNOWN", "unlisted vehicle"
    if s.module_type in PRESENCE_MODULES:
        users = {str(u) for u in (zone or {}).get("allowed_user_ids") or []}
        roles = {int(r) for r in (zone or {}).get("allowed_role_ids") or []}
        if applies and zt not in (None, "NORMAL"):
            if not users and not roles:
                return "UNAUTHORISED", f"no one is allowed in {zone.get('name')}"
            ok = [g for g in on_duty if str(g.get("user_id")) in users or g.get("role_id") in roles]
            if ok:
                return "ON_DUTY", f"{len(ok)} person(s) allowed in {zone.get('name')} on shift"
            return "UNAUTHORISED", f"no one allowed in {zone.get('name')} is on shift"
        if on_duty:
            return "ON_DUTY", f"{len(on_duty)} guard(s) on shift at the site"
        return "UNKNOWN", "no guard on shift at the site"
    return "UNKNOWN", ""


# ── Verification ─────────────────────────────────────────────────────────────

def is_verified(module: str, detection_count: int, observed_seconds: float, max_confidence: float | None,
                profile: dict | None, corroborated: bool = False) -> bool:
    """Seen repeatedly, or for long enough — or a weapon or fire seen clearly —
    or seen by a fixed camera too: a second, independent sensor agreeing."""
    if corroborated:
        return True
    if module in IMMEDIATE_MODULES and (max_confidence or 0) >= IMMEDIATE_CONFIDENCE:
        return True
    need = (profile or {}).get("verify_min_seconds")
    need = DEFAULT_VERIFY_SECONDS if need is None else int(need)
    return detection_count >= VERIFY_DETECTIONS or observed_seconds >= need


# ── Risk ─────────────────────────────────────────────────────────────────────

def level_of(score: int) -> str:
    level = "INFO"
    for name in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        if score >= THRESHOLDS[name]:
            level = name
    return level


def assess(s: Sighting, rule: dict, situation: Situation) -> Risk:
    """Score a sighting in its situation. Every point is a written factor."""
    factors: list[dict] = []

    def add(factor: str, points: int, detail: str) -> None:
        factors.append({"factor": factor, "points": points, "detail": detail})

    base = (rule.get("base_severity") or DEFAULT_BASE_SEVERITY.get(s.module_type, "low")).lower()
    add("DETECTION", SEVERITY_POINTS.get(base, 20), f"{s.module_type}{' ' + s.label if s.label else ''} "
        f"(profile severity {base})")

    zone = situation.zone
    if zone is not None and zone_applies(zone, s.module_type):
        zt = zone.get("zone_type")
        pts = ZONE_POINTS.get(zt, 15 if zt in ("PERSON_RESTRICTED", "VEHICLE_RESTRICTED") else 0)
        pts += ZONE_SEVERITY_POINTS.get((zone.get("severity") or "").lower(), 0)
        if pts:
            add("ZONE", pts, f"in {zt.lower().replace('_', ' ')} zone {zone.get('name')}")
    elif zone is None:
        add("ZONE", 0, "not over any active security zone")

    verdict, why = authorisation(s, zone, situation.on_duty)
    auth_points = {"BLOCKLISTED": 25, "AUTHORISED": -30, "UNAUTHORISED": 15,
                   "ON_DUTY": -5 if zone_applies(zone, s.module_type) and (zone or {}).get("zone_type")
                   not in (None, "NORMAL") else -10}.get(verdict, 0)
    if zone is not None and (zone.get("zone_type") == "CRITICAL") and verdict == "ON_DUTY":
        auth_points = 0       # in a critical zone, "someone allowed is on shift" proves nothing
    if verdict != "UNKNOWN" or why:
        add("AUTHORISATION", auth_points, why or verdict.lower())

    if s.module_type in PRESENCE_MODULES | VEHICLE_MODULES:
        t = situation.local_time.time()
        if t >= NIGHT_START or t < NIGHT_END:
            add("TIME", 10, f"at night ({situation.local_time:%H:%M} site time)")

    conf = situation.max_confidence if situation.max_confidence is not None else s.confidence
    if conf is not None:
        if conf >= 0.9:
            add("CONFIDENCE", 5, f"AI confidence {conf:.2f}")
        elif conf < 0.6:
            add("CONFIDENCE", -10, f"low AI confidence {conf:.2f}")

    if situation.verified:
        add("VERIFICATION", 10, f"seen {situation.detection_count} time(s) over {situation.observed_seconds:.0f} s")
    elif situation.detection_count <= 1:
        add("VERIFICATION", -10, "seen once, not yet confirmed")
    if situation.detection_count >= 5:
        add("REPEATED", 5, f"{situation.detection_count} detections")

    others = sorted(situation.concurrent_modules - {s.module_type})
    if others:
        add("CONCURRENT", 10, "also seen on this flight just now: " + ", ".join(others))

    if situation.history_events >= 5:
        add("HISTORY", 10, f"{situation.history_events} drone events here in 30 days")
    elif situation.history_events >= 3:
        add("HISTORY", 5, f"{situation.history_events} drone events here in 30 days")
    if situation.history_incidents:
        add("HISTORY", 5, f"{situation.history_incidents} incident(s) at this site in 30 days")

    if situation.cctv:
        add("CCTV", 10, "also detected by fixed camera " + ", ".join(situation.cctv))

    score = max(0, min(100, sum(f["points"] for f in factors)))
    return Risk(score=score, level=level_of(score), factors=factors, authorisation=verdict)


def alert_severity(level: str, verified: bool, zone: dict | None) -> str | None:
    """The alert this event should carry, or None. Nothing unverified, nothing
    below MEDIUM, nothing in a zone whose policy is NONE."""
    if not verified or level not in ALERT_SEVERITY:
        return None
    if zone is not None and zone.get("alert_policy") == "NONE":
        return None
    return ALERT_SEVERITY[level]


LEVEL_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
#: The incident level when a flight has no security profile — the same default
#: every profile rule gets (drone_profile_rules.incident_risk_level), so "not
#: configured" means one thing everywhere.
DEFAULT_INCIDENT_LEVEL = "HIGH"
INCIDENT_SEVERITY = {"INFO": "low", "LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}


def incident_due(level: str, verified: bool, zone: dict | None, rule: dict | None) -> bool:
    """Whether a drone event becomes an incident. Only verified events; never
    in a zone whose policy is NONE. A zone whose policy is INCIDENT makes every
    alertable event (MEDIUM and above) an incident; otherwise the profile
    rule's incident_risk_level decides (HIGH by default, as for every rule)."""
    if not verified or (zone is not None and zone.get("alert_policy") == "NONE"):
        return False
    if zone is not None and zone.get("alert_policy") == "INCIDENT" and LEVEL_RANK[level] >= LEVEL_RANK["MEDIUM"]:
        return True
    threshold = ((rule or {}).get("incident_risk_level") or DEFAULT_INCIDENT_LEVEL).upper()
    return LEVEL_RANK[level] >= LEVEL_RANK.get(threshold, LEVEL_RANK[DEFAULT_INCIDENT_LEVEL])


def local(dt: datetime, tz) -> datetime:
    return dt.astimezone(tz)
