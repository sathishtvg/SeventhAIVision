"""Pre-flight: may this mission launch now, and if not, exactly why not.

Every check the brief lists, each reported whether it passed or not — an
operator looking at a blocked mission needs the full picture, not the first
failure. A check is either BLOCKING (the mission does not fly) or a WARNING
(it flies, and the operator is told).

PURE. The facts are gathered elsewhere (drone_sessions.gather_facts) and passed
in with the moment they describe, so a manual run, a scheduled launch and a test
all apply the same rules to the same inputs.

CONSERVATIVE BY DESIGN. An aircraft whose health has not been heard recently,
whose maintenance is overdue, or whose battery would not bring it home with the
reserve to spare, does not launch. The cost of a patrol not flying is a gap in
coverage; the cost of the opposite is a drone on the ground somewhere it should
not be.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.drone_ai import SUITABILITY, UNRELIABLE
from app.services.drone_flight_plan import RESERVE_PCT, Estimate

BLOCK, WARN = "BLOCK", "WARN"
#: Statuses a drone may launch from. CHARGING is allowed: what matters is the
#: charge it has, and the battery check measures that.
LAUNCHABLE = ("READY", "STANDBY", "CHARGING")


@dataclass
class PreflightFacts:
    now: datetime
    licence_problem: str | None
    mission: dict
    site: dict | None
    drone: dict | None
    provider: dict | None
    provider_capabilities: frozenset = frozenset()
    gateway: dict | None = None
    route: dict | None = None
    waypoint_count: int = 0
    outside_site: list[int] = field(default_factory=list)
    profile: dict | None = None
    drone_in_flight: bool = False
    estimate: Estimate | None = None
    #: The AI checks run only when these were loaded (a pure caller may skip them).
    ai_checked: bool = False
    #: The drone's camera row: {name, ai_modules_enabled, is_active}.
    camera: dict | None = None
    #: Modules the flight's profile looks for (None: no profile — all, at defaults).
    profile_modules: list[str] | None = None
    #: Zones drawn on the camera's picture: {"restricted": n, "crowd": n}.
    camera_image_zones: dict = field(default_factory=dict)


@dataclass
class PreflightResult:
    passed: bool
    checks: list[dict]

    @property
    def blocking(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"] and c["severity"] == BLOCK]

    @property
    def warnings(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"] and c["severity"] == WARN]

    def reason(self) -> str | None:
        """One sentence per blocking check — what goes on the session and the alert."""
        return " ".join(c["detail"] for c in self.blocking) or None

    def as_json(self) -> dict:
        return {"passed": self.passed, "checks": self.checks,
                "blocking": [c["code"] for c in self.blocking],
                "warnings": [c["code"] for c in self.warnings]}


def evaluate(f: PreflightFacts) -> PreflightResult:
    checks: list[dict] = []

    def check(code: str, label: str, ok: bool, detail_fail: str, detail_ok: str = "",
              severity: str = BLOCK) -> None:
        checks.append({"code": code, "label": label, "passed": bool(ok), "severity": severity,
                       "detail": detail_ok if ok else detail_fail})

    d, m = f.drone, f.mission

    check("LICENCE", "Licence valid", f.licence_problem is None, f.licence_problem or "",
          "Drone Patrol is licensed.")
    check("MISSION_ENABLED", "Mission enabled", bool(m.get("enabled")),
          "The mission is disabled.", "The mission is enabled.")
    check("SITE", "Site operational", bool(f.site and f.site.get("is_active")),
          "The mission's site is not active.", "The site is active.")

    # ── The aircraft ─────────────────────────────────────────────────────────
    check("DRONE_ASSIGNED", "Drone assigned", d is not None,
          "No drone is assigned to this mission.", f"{(d or {}).get('name')} is assigned.")
    if d is not None:
        status = d.get("status")
        check("DRONE_ENABLED", "Drone enabled", status not in ("DISABLED", "MAINTENANCE"),
              f"{d['name']} is {str(status).lower()}.", f"{d['name']} is in service.")
        check("DRONE_AVAILABLE", "Drone available", status in LAUNCHABLE and not f.drone_in_flight,
              (f"{d['name']} is already on a mission." if f.drone_in_flight
               else f"{d['name']} is {str(status).lower().replace('_', ' ')}; it must be ready, "
                    "on standby or charging."),
              f"{d['name']} is {str(status).lower()}.")

        hb, timeout = d.get("last_heartbeat_at"), int(d.get("heartbeat_timeout_seconds") or 30)
        heard = hb is not None and (f.now - hb) <= timedelta(seconds=timeout)
        check("COMMUNICATION", "Communication available",
              heard and d.get("communication_status") == "OK",
              ("Nothing has been heard from the drone yet." if hb is None else
               f"The drone was last heard {int((f.now - hb).total_seconds())}s ago "
               f"(limit {timeout}s)." if not heard else
               f"The drone reports its link as {str(d.get('communication_status')).lower()}."),
              "The drone is reporting.")

        battery = d.get("battery_level")
        need = f.estimate.battery_needed_pct if f.estimate else 0.0
        required = max(float(m.get("min_battery_pct") or 0), need + RESERVE_PCT)
        check("BATTERY", "Battery sufficient", battery is not None and float(battery) >= required,
              ("The drone has not reported its battery." if battery is None else
               f"Battery {float(battery):.0f}% is below the {required:.0f}% this flight needs "
               f"({need:.0f}% estimated + {RESERVE_PCT:.0f}% reserve, mission minimum "
               f"{m.get('min_battery_pct')}%)."),
              f"Battery {float(battery or 0):.0f}%, needs {required:.0f}%.")

        for code, label, col in (("GPS", "GPS available", "gps_status"),
                                 ("CAMERA", "Camera available", "camera_status")):
            check(code, label, d.get(col) == "OK",
                  f"{label.split()[0]} reports {str(d.get(col)).lower()}.", f"{label.split()[0]} OK.")
        check("STORAGE", "Storage available", d.get("storage_status") != "FAULT",
              "On-board storage reports a fault.",
              "Storage OK." if d.get("storage_status") == "OK" else "Storage status is not confirmed.")
        if d.get("storage_status") == "WARNING":
            check("STORAGE_LOW", "Storage space", False, "On-board storage is running low.", severity=WARN)

        due = d.get("next_maintenance_at")
        check("MAINTENANCE", "Maintenance current", due is None or due > f.now,
              f"Maintenance was due on {due:%d %b %Y}." if due else "", "Maintenance is current.")

    # ── How it is flown ──────────────────────────────────────────────────────
    p = f.provider
    check("PROVIDER", "Provider configured", bool(p and p.get("is_active")),
          "The drone has no active provider configuration." if not p else
          "The drone's provider configuration is disabled.",
          f"Provider: {(p or {}).get('provider_key')}.")
    if p and p.get("is_active"):
        check("PROVIDER_MISSIONS", "Provider can fly missions", "MISSION" in f.provider_capabilities,
              f"The {p.get('provider_key')} provider cannot fly missions.", "The provider flies missions.")
    if d is not None and d.get("edge_gateway_id"):
        g = f.gateway or {}
        check("EDGE_GATEWAY", "Edge gateway available",
              bool(g.get("is_active")) and g.get("status") != "OFFLINE",
              "The site's edge gateway is disabled." if not g.get("is_active") else
              "The site's edge gateway is offline.", "Edge gateway available.")

    # ── Where it goes ────────────────────────────────────────────────────────
    r = f.route
    check("ROUTE", "Route valid", bool(r and r.get("is_active") and f.waypoint_count > 0),
          ("No route is assigned." if not r else "The route is disabled." if not r.get("is_active")
           else "The route has no waypoints."),
          f"{f.waypoint_count} waypoint(s).")
    if f.outside_site:
        check("ROUTE_GEOFENCE", "Route inside site fence", False,
              f"Waypoint(s) {', '.join(map(str, f.outside_site))} lie outside the site's geofence.",
              severity=WARN)
    if f.estimate and m.get("max_duration_minutes"):
        limit = int(m["max_duration_minutes"]) * 60
        check("DURATION", "Within maximum duration", f.estimate.duration_s <= limit,
              f"The flight is estimated at {f.estimate.duration_s / 60:.1f} min, over the "
              f"{m['max_duration_minutes']} min limit.",
              f"Estimated {f.estimate.duration_s / 60:.1f} min.")

    if m.get("security_profile_id"):
        prof = f.profile
        check("SECURITY_PROFILE", "Security profile valid", bool(prof and prof.get("is_active")),
              "The mission's security profile is missing or disabled.", f"Profile: {(prof or {}).get('name')}.")
    else:
        check("SECURITY_PROFILE", "Security profile", False,
              "No security profile: detections are assessed with the default rules for every module.",
              severity=WARN)

    # ── What the AI can see ──────────────────────────────────────────────────
    if f.ai_checked and d is not None:
        cam = f.camera
        check("AI_CAMERA", "Camera linked for AI", cam is not None,
              f"No camera is linked to {d['name']}: no AI runs on this flight.",
              f"{(cam or {}).get('name')} carries the AI.", severity=WARN)
        if cam is not None:
            on = set(cam.get("ai_modules_enabled") or [])
            check("AI_TAMPERING", "No tampering detection on a moving camera", "tampering" not in on,
                  f"{cam['name']} has tampering detection enabled. On a moving camera it raises a tampering "
                  "alert and incident at every change of view — turn it off for this camera.",
                  "Tampering detection is off.")
            shaky = sorted(m for m in on if SUITABILITY.get(m, ("",))[0] == UNRELIABLE and m != "tampering")
            check("AI_UNRELIABLE", "AI modules suited to a moving camera", not shaky,
                  f"{', '.join(shaky)} on {cam['name']} are not reliable on a moving camera; their "
                  "detections are ignored.", "Every module on the camera works on a moving camera.",
                  severity=WARN)
            wanted = (set(f.profile_modules) if f.profile_modules is not None
                      else {m for m, (k, _) in SUITABILITY.items() if k != UNRELIABLE})
            wanted = {m for m in wanted if SUITABILITY.get(m, ("",))[0] != UNRELIABLE}
            if f.profile_modules is not None:
                missing = sorted(wanted - on)
                check("AI_MODULES", "Camera runs the profile's AI", not missing,
                      f"{cam['name']} does not run {', '.join(missing)}, which the security profile looks for. "
                      "Enable them on the camera or this flight cannot see them.",
                      "The camera runs every module the profile looks for.", severity=WARN)
            blind = []
            if "intrusion" in on and "intrusion" in wanted and not f.camera_image_zones.get("restricted"):
                blind.append("intrusion needs a restricted zone drawn on the camera's picture "
                             "(a full-frame zone makes it report every person in view)")
            if "crowd" in on and "crowd" in wanted and not f.camera_image_zones.get("crowd"):
                blind.append("crowd needs a crowd zone drawn on the camera's picture")
            check("AI_IMAGE_ZONES", "Image zones for zone-based AI", not blind,
                  f"On {cam['name']}: " + "; ".join(blind) + ". Without one, it reports nothing.",
                  "Zone-based modules have zones on the camera.", severity=WARN)

    passed = not any((not c["passed"]) and c["severity"] == BLOCK for c in checks)
    return PreflightResult(passed=passed, checks=checks)
