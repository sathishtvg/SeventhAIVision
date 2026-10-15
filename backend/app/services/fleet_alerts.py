"""Fleet alerts: into the platform's one alert queue, and to the people a rule names.

Design: docs/plans/2026-10-14-gps-fleet-tracking-design.md, section 5.

AN ALERT IS A ROW IN `alerts` (module "gps"), the queue every other module uses: it is acknowledged, assigned,
noted, made an incident or dismissed there, and the AI layer reads it as it reads any GPS alert. The codes the
older position endpoint raises are kept (gps.entry, gps.exit, gps.speed_violation); these are added:

    gps.dwell_inside / gps.dwell_outside   inside / outside a geofence longer than its rule's minutes
    gps.stale / gps.offline                a tracked vehicle in service stopped reporting

TELLING PEOPLE. Raising the alert puts `alert_created` on the organisation's channel after the transaction is
committed, as every module does: the screens show it, the organisation's notification rules (email, SMS,
webhook) apply to it, and a high or critical one is pushed to the phones as every high alert is. A rule's own
recipients - chosen people, or everybody holding chosen roles - are pushed to as well when the alert is below
high (above it they are reached already). Only somebody who may read vehicles (gps:read) and whose sites
include the vehicle's is told; a name on a rule is not a way round a site limit.

OFFLINE AND STALE ALERTS are raised when the minute job records the change, so once per episode, for vehicles
with tracking on and in service. Offline is on by default, stale off. When the vehicle reports again, a note
saying so is added to the alerts of that episode; they are not closed for anybody.

A fleet alert never opens a barrier, admits anybody or confirms who is driving: a person looks at it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SEVERITIES = ("low", "medium", "high", "critical")
#: Pushed to every registered phone by the platform already (app/realtime/redis_listener.py).
PUSHED_ANYWAY = ("high", "critical")
OFFLINE_KEY, STALE_KEY = "fleet.alerts.offline", "fleet.alerts.stale"
DEFAULTS = {OFFLINE_KEY: {"on": True, "severity": "medium"}, STALE_KEY: {"on": False, "severity": "low"}}


@dataclass
class Raised:
    """An alert made in the open transaction, to be announced once it is committed."""
    alert_id: str
    code: str
    severity: str
    title: str
    recipients: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def vehicle_words(v: Mapping[str, Any]) -> str:
    plate = v.get("plate_number")
    return f"{v['name']} ({plate})" if plate else str(v["name"])


async def raise_alert(db: AsyncSession, tenant_id: str, v: Mapping[str, Any], *, code: str, severity: str,
                      title: str, params: Mapping[str, Any], recipients: Sequence[str] = ()) -> Raised:
    row = (await db.execute(text("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity, alert_code, message_params,
                            title, message, status)
        VALUES (CAST(:t AS uuid), NULL, CAST(:site AS uuid), 'gps', :sev, :code, CAST(:params AS jsonb),
                :title, :message, 'open')
        RETURNING id
    """), {"t": tenant_id, "site": str(v["site_id"]) if v.get("site_id") else None, "sev": severity, "code": code,
           "params": json.dumps({"vehicle_id": str(v["id"]), "vehicle": v["name"], "plate": v.get("plate_number"),
                                 **params}, default=str),
           "title": title[:255], "message": title})).first()
    return Raised(str(row.id), code, severity, title[:255], list(recipients),
                  {"alert_id": str(row.id), "vehicle_id": str(v["id"])})


async def recipients(db: AsyncSession, v: Mapping[str, Any], user_ids: Sequence[Any], role_ids: Sequence[int]) -> list[str]:
    """The people a rule names, and everybody holding the roles it names - only those who may read vehicles and
    whose sites include this vehicle's."""
    if not user_ids and not role_ids:
        return []
    rows = await db.execute(text("""
        SELECT DISTINCT u.id
          FROM users u
          JOIN role_permissions rp ON rp.role_id = u.role_id
          JOIN permissions p ON p.id = rp.permission_id AND p.code = 'gps:read'
         WHERE u.is_active
           AND (u.id = ANY(CAST(:users AS uuid[])) OR u.role_id = ANY(CAST(:roles AS int[])))
           AND (u.role_id = 2
                OR NOT EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id)
                OR EXISTS (SELECT 1 FROM user_sites us WHERE us.user_id = u.id AND us.site_id = CAST(:site AS uuid)))
    """), {"users": [str(u) for u in user_ids], "roles": [int(r) for r in role_ids],
           "site": str(v["site_id"]) if v.get("site_id") else None})
    return sorted(str(r[0]) for r in rows)


async def announce(redis, tenant_id: str, raised: Sequence[Raised]) -> None:
    """After the commit: `alert_created` for each, and a push to a rule's own recipients when the platform will
    not push it anyway. A failure is logged; the alert is in the queue whatever happens here."""
    if redis is None or not raised:
        return
    from app.services import response_notify

    for r in raised:
        await response_notify.announce(redis, tenant_id, "alert_created", {
            "alert_id": r.alert_id, "alert_code": r.code, "severity": r.severity, "module_type": "gps",
            "title": r.title,
        })
        if r.recipients and r.severity not in PUSHED_ANYWAY:
            await response_notify.push(redis, tenant_id, r.recipients, r.title, r.data)


# ── a vehicle stops reporting ────────────────────────────────────────────────

def check_setting(value: Any, default: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("Each is {on, severity}")
    on, severity = value.get("on", default["on"]), value.get("severity", default["severity"])
    if not isinstance(on, bool):
        raise ValueError("on is true or false")
    if severity not in SEVERITIES:
        raise ValueError(f"severity is one of {', '.join(SEVERITIES)}")
    return {"on": on, "severity": severity}


async def settings_of(db: AsyncSession) -> dict:
    """{"offline": {on, severity}, "stale": {...}} - the defaults where the organisation has set nothing usable."""
    rows = await db.execute(text("SELECT setting_key, setting_value FROM tenant_settings WHERE setting_key = ANY(:k)"),
                            {"k": [OFFLINE_KEY, STALE_KEY]})
    found = {}
    for row in rows:
        value = row.setting_value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        found[row.setting_key] = value
    out = {}
    for key, name in ((OFFLINE_KEY, "offline"), (STALE_KEY, "stale")):
        try:
            out[name] = check_setting(found.get(key, DEFAULTS[key]), DEFAULTS[key])
        except ValueError:
            out[name] = dict(DEFAULTS[key])
    return out


async def status_changed(db: AsyncSession, tenant_id: str, v: Mapping[str, Any], from_status: str, to_status: str,
                         now: datetime, chosen: Mapping[str, Mapping[str, Any]] | None = None) -> list[Raised]:
    """Called when a status change has just been recorded. Raises the offline or stale alert of the episode, or
    notes on the episode's alerts that the vehicle reports again."""
    if to_status == "online" and from_status in ("stale", "offline"):
        await _note_recovery(db, tenant_id, v, now)
        return []
    if to_status not in ("stale", "offline") or not v.get("tracking_enabled") or v.get("service_status") != "in_service":
        return []
    chosen = chosen or await settings_of(db)
    if not chosen[to_status]["on"]:
        return []
    last = v.get("last_fix_at")
    since = f" since {last.astimezone(timezone.utc):%H:%M} UTC" if isinstance(last, datetime) else ""
    words = "has stopped reporting" if to_status == "offline" else "has not reported for a while"
    return [await raise_alert(db, tenant_id, v, code=f"gps.{to_status}", severity=chosen[to_status]["severity"],
                              title=f"{vehicle_words(v)} {words}{since}",
                              params={"status": to_status, "last_fix_at": last})]


async def _note_recovery(db: AsyncSession, tenant_id: str, v: Mapping[str, Any], now: datetime) -> None:
    await db.execute(text("""
        INSERT INTO alert_notes (tenant_id, alert_id, author_user_id, note)
        SELECT CAST(:t AS uuid), a.id, NULL, :note
          FROM alerts a
         WHERE a.module_type = 'gps' AND a.alert_code IN ('gps.offline', 'gps.stale')
           AND a.message_params->>'vehicle_id' = :v
           AND a.created_at > COALESCE((SELECT max(c.changed_at) FROM fleet_status_changes c
                                         WHERE c.vehicle_id = CAST(:v AS uuid) AND c.to_status = 'online'
                                           AND c.changed_at < :now), '-infinity')
    """), {"t": tenant_id, "v": str(v["id"]), "now": now,
           "note": f"The vehicle is reporting again ({now.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC)."})
