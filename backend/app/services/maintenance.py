"""Work orders: what may follow what, and what the platform puts forward for a person to accept.

  MOVES            which state a work order may go to from which
  settings()       whether an organisation has asked for suggestions from health
  suggest_from_health()     a device read as down for long → a SUGGESTED order
  suggest_from_schedules()  a schedule falling due → a SUGGESTED order
  after_closing()  what finishing, cancelling or dismissing an order does to
                   the schedule it came from
  run()            the scheduler's pass over every organisation

THE PLATFORM SUGGESTS; A PERSON RAISES THE WORK. A suggestion is a row in a
list. It assigns nobody, tells nobody, and becomes work only when somebody who
manages maintenance accepts it — or it is dismissed, with why. Each is made
once: the same outage, and the same due date of a schedule, are not put forward
twice, whatever became of the first.

SUGGESTIONS FROM HEALTH ARE OFF UNTIL AN ORGANISATION ASKS FOR THEM
(`maintenance.suggest_from_health`). A schedule is itself the asking, so what a
schedule puts forward needs no switch.

A SUGGESTION SAYS WHAT IT WAS MADE OF: the state that was read, since when, and
why — the same words the health screen showed. It does not say what is wrong
with the device, which nothing here knows.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import device_health

logger = logging.getLogger(__name__)

SUGGEST_KEY = "maintenance.suggest_from_health"
AFTER_KEY = "maintenance.suggest_after_hours"
DEFAULT_AFTER_HOURS = 4
STATES = ("SUGGESTED", "OPEN", "IN_PROGRESS", "DONE", "CANCELLED", "DISMISSED")
OVER = ("DONE", "CANCELLED", "DISMISSED")
MOVES = {"SUGGESTED": ("OPEN", "DISMISSED"), "OPEN": ("IN_PROGRESS", "CANCELLED"),
         "IN_PROGRESS": ("DONE", "CANCELLED"), "DONE": (), "CANCELLED": (), "DISMISSED": ()}
KINDS = ("CORRECTIVE", "PREVENTIVE", "INSPECTION")
PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
ORIGINS = ("PERSON", "DEFECT", "HEALTH", "SCHEDULE")
SUGGESTION_NOTE = ("Put forward by the platform from what it read. It is not work until somebody who manages "
                   "maintenance accepts it, and it assigns and tells nobody.")


async def settings(db: AsyncSession) -> dict:
    """What the calling organisation has asked for."""
    rows = await db.execute(text(
        "SELECT setting_key, setting_value FROM tenant_settings WHERE setting_key = ANY(:keys)"),
        {"keys": [SUGGEST_KEY, AFTER_KEY]})
    found = {}
    for row in rows:
        value = row.setting_value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        found[row.setting_key] = value
    hours = found.get(AFTER_KEY)
    return {"suggest_from_health": found.get(SUGGEST_KEY) is True,
            "suggest_after_hours": hours if isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 168
            else DEFAULT_AFTER_HOURS}


async def next_number(db: AsyncSession) -> str:
    """The next work order number of the calling organisation. One at a time."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('workorder:' || current_setting('app.current_tenant')))"))
    number = (await db.execute(text(
        "SELECT COALESCE(max(CAST(substring(number FROM 4) AS integer)), 0) + 1 FROM maintenance_work_orders"))).scalar()
    return f"WO-{number:04d}"


async def _timezone(db: AsyncSession) -> ZoneInfo:
    name = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    at = moment.astimezone(zone)
    return f"{at.day} {at.strftime('%b %H:%M')}"


def health_key(reading: Mapping) -> str:
    """What one outage is known by: the device and the moment it began."""
    return f"health:{reading['kind']}:{reading['device_id']}:{reading['since'].astimezone(timezone.utc).isoformat()}"


def health_reason(reading: Mapping, now: datetime, zone: ZoneInfo) -> str:
    """Why an order is put forward for a device: what was read, since when, and why — and nothing about its cause."""
    hours = int((now - reading["since"]).total_seconds() // 3600)
    began = "has been read as down since at least" if reading.get("since_is_when_first_read") else "has been down since"
    return (f"{reading['kind_label']} “{reading['name']}” {began} {_clock(reading['since'], zone)} "
            f"({hours} hour{'' if hours == 1 else 's'}). {' '.join(reading['reasons'])}").strip()


async def _put_forward(db: AsyncSession, *, key: str, origin: str, kind: str, title: str, reason: str,
                       site_id: Any, asset_id: Any, schedule_id: Any = None, description: str | None = None,
                       due_at: datetime | None = None, priority: str = "NORMAL") -> bool:
    """Put one order forward, unless what it is made of has been put forward before."""
    if (await db.execute(text("SELECT 1 FROM maintenance_work_orders WHERE origin_key = :k"), {"k": key})).scalar():
        return False
    await db.execute(text("""
        INSERT INTO maintenance_work_orders
               (tenant_id, number, site_id, asset_id, schedule_id, title, description, kind, priority, state, origin,
                origin_key, suggestion_reason, due_at)
        VALUES (current_setting('app.current_tenant')::uuid, :number, :site, :asset, :schedule, :title, :description,
                :kind, :priority, 'SUGGESTED', :origin, :key, :reason, :due)
    """), {"number": await next_number(db), "site": site_id, "asset": asset_id, "schedule": schedule_id,
           "title": title[:200], "description": description, "kind": kind, "priority": priority, "origin": origin,
           "key": key, "reason": reason, "due": due_at})
    return True


async def suggest_from_health(db: AsyncSession, readings: Sequence[Mapping], now: datetime, after_hours: int) -> int:
    """Put a corrective order forward for each device that has been down for
    `after_hours` or more. Once for each outage."""
    zone = await _timezone(db)
    made = 0
    for r in readings:
        if r["state"] != "DOWN" or r["since"] is None or now - r["since"] < timedelta(hours=after_hours):
            continue
        made += await _put_forward(
            db, key=health_key(r), origin="HEALTH", kind="CORRECTIVE", title=f"{r['name']}: down",
            reason=health_reason(r, now, zone), site_id=r["site_id"], asset_id=r["asset_id"], priority="HIGH")
    return made


async def suggest_from_schedules(db: AsyncSession, now: datetime) -> int:
    """Put a preventive order forward for each schedule that falls due within
    its lead time. Once for each due date."""
    rows = (await db.execute(text("""
        SELECT m.id, m.title, m.instructions, m.every_days, m.next_due_on, m.asset_id,
               COALESCE(m.site_id, a.site_id) AS site_id, a.asset_code, a.name AS asset_name,
               ((m.next_due_on + 1)::timestamp AT TIME ZONE t.timezone) AS due_at
          FROM maintenance_schedules m
          JOIN tenants t ON t.id = m.tenant_id
          LEFT JOIN asset_register a ON a.id = m.asset_id
         WHERE m.is_active AND (a.id IS NULL OR a.status <> 'RETIRED')
           AND m.next_due_on - m.lead_days <= (CAST(:now AS timestamptz) AT TIME ZONE t.timezone)::date
    """), {"now": now})).mappings().all()
    made = 0
    for s in rows:
        of = f" of {s['asset_code']} {s['asset_name']}" if s["asset_code"] else ""
        made += await _put_forward(
            db, key=f"schedule:{s['id']}:{s['next_due_on'].isoformat()}", origin="SCHEDULE", kind="PREVENTIVE",
            title=s["title"], description=s["instructions"], schedule_id=s["id"], site_id=s["site_id"],
            asset_id=s["asset_id"], due_at=s["due_at"],
            reason=(f"“{s['title']}”{of} is due on {s['next_due_on'].day} {s['next_due_on'].strftime('%b %Y')}: "
                    f"it is scheduled every {s['every_days']} day{'' if s['every_days'] == 1 else 's'}."))
    return made


async def after_closing(db: AsyncSession, order: Mapping, state: str, now: datetime) -> None:
    """What an order's end does to the schedule it came from. Done: the
    schedule runs again from the day the work was done. Cancelled or
    dismissed: it moves on to its next date, and the order keeps why."""
    if order["schedule_id"] is None or state not in OVER:
        return
    if state == "DONE":
        await db.execute(text("""
            UPDATE maintenance_schedules m
               SET last_done_on = (CAST(:now AS timestamptz) AT TIME ZONE t.timezone)::date,
                   next_due_on = (CAST(:now AS timestamptz) AT TIME ZONE t.timezone)::date + m.every_days,
                   updated_at = now()
              FROM tenants t WHERE t.id = m.tenant_id AND m.id = :id
        """), {"now": now, "id": order["schedule_id"]})
    else:
        # Only if the schedule still stands at the date this order was for: it may have been moved by hand since.
        await db.execute(text("""
            UPDATE maintenance_schedules SET next_due_on = next_due_on + every_days, updated_at = now()
             WHERE id = :id AND :key = 'schedule:' || id::text || ':' || next_due_on::text
        """), {"id": order["schedule_id"], "key": order["origin_key"]})


async def run(session_factory, now: datetime | None = None) -> dict:
    """One pass over every organisation: read its devices, keep what changed,
    and put forward what is due. Each organisation is a transaction of its own."""
    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    at = now or datetime.now(timezone.utc)
    counts = {"changes": 0, "from_health": 0, "from_schedules": 0}
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
                items = await device_health.readings(db, at)
                changes = await device_health.record(db, items, at)
                asked = await settings(db)
                from_health = 0
                if asked["suggest_from_health"]:
                    # Read again: what was just kept is since when a device has been as it is.
                    items = await device_health.readings(db, at) if changes else items
                    from_health = await suggest_from_health(db, items, at, asked["suggest_after_hours"])
                from_schedules = await suggest_from_schedules(db, at)
                await db.commit()
        except Exception:
            logger.exception("maintenance: could not look at tenant %s", tenant_id)
            continue
        counts["changes"] += changes
        counts["from_health"] += from_health
        counts["from_schedules"] += from_schedules
    return counts
