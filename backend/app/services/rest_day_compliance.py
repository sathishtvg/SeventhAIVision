"""Whether the roster as it stands gives guards the rest the rules require.

THE RULES ALREADY EXIST AND ONLY ONE CODE PATH HONOURS THEM.
roster.max_consecutive_days (default 6) and roster.min_rest_hours (default 11)
are tenant settings, and roster_autoschedule enforces both -- when IT chooses
who to place. Every other way a shift comes into being ignores them: manual
creation, the roster router's three paths, the service's own generator, and the
two reassignment paths. The auto-scheduler declines to break the rule with its
own choices and has nothing to say about a roster somebody else built.

The demo database shows what that permits: one guard on 19 consecutive days,
then 14, with no rest day in either run.

So this reads the roster as it IS, not as the scheduler would have built it.

THE NUMBERS ARE THE TENANT'S OWN, read from tenant_settings through the same
defaults roster_autoschedule uses. A second copy of 6 and 11 living here would
drift from the planner's, and an agency that raised its limit would be told off
by one half of the system for a roster the other half had just approved.

IT REPORTS. It does not block, for the same reason certification compliance does
not: an ops manager covering a 2am no-show must not be stopped by the roster
tool, or they will stop using the roster tool and the record goes with them.

WHAT IT DOES NOT CLAIM. The Employment Act defines a rest day precisely -- a
whole day, or a continuous period of 30 hours for shift workers -- and this does
not encode that definition. It counts consecutive worked days against the
tenant's own configured maximum, which is what an ops manager can actually act
on, and it says so rather than implying a legal verdict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The planner's own defaults, imported rather than restated. If a later change
# moves these, both halves move together.
from app.services.roster_autoschedule import _ROSTER_DEFAULTS, _get_roster_setting

logger = logging.getLogger(__name__)

OVER_RUN = "OVER_RUN"        # more consecutive days than the tenant allows
SHORT_REST = "SHORT_REST"    # too little time between two shifts
OVERLAP = "OVERLAP"          # rostered in two places at once


@dataclass(frozen=True)
class Run:
    """An unbroken stretch of working days for one guard."""
    guard_user_id: str
    guard_name: str
    start: date
    end: date
    days: int

    def as_dict(self) -> dict:
        return {"guard_user_id": self.guard_user_id, "guard_name": self.guard_name,
                "from_day": self.start.isoformat(), "to_day": self.end.isoformat(),
                "consecutive_days": self.days, "status": OVER_RUN}


def consecutive_runs(days: list[date]) -> list[tuple[date, date, int]]:
    """Group sorted, de-duplicated dates into unbroken runs.

    Pure, because this is where an off-by-one hides silently: a run reported as
    six when it is seven is a breach nobody is told about, and nobody
    recalculates it by hand.
    """
    if not days:
        return []
    ordered = sorted(set(days))
    runs: list[tuple[date, date, int]] = []
    start = prev = ordered[0]
    for d in ordered[1:]:
        if d == prev + timedelta(days=1):
            prev = d
            continue
        runs.append((start, prev, (prev - start).days + 1))
        start = prev = d
    runs.append((start, prev, (prev - start).days + 1))
    return runs


def runs_over(days: list[date], max_consecutive: int) -> list[tuple[date, date, int]]:
    """Only the runs that exceed what is allowed.

    Strictly greater than: a guard who works exactly the maximum has not broken
    the rule, and flagging them teaches people to ignore the warning.
    """
    return [r for r in consecutive_runs(days) if r[2] > max_consecutive]


def rest_gaps(starts_ends: list[tuple[datetime, datetime]],
              min_rest_hours: int) -> tuple[list, list]:
    """(short_rests, overlaps) between consecutive shifts.

    TWO LISTS, NOT ONE, and this was got wrong first time round. A negative gap
    is not a short rest -- it means the guard is rostered in two places at once,
    which is a different and worse fact. Reporting them together looked
    reasonable until it ran against a real roster and returned sixty-seven
    "short rests", nearly all of them double-bookings, which would have buried
    every genuine short-rest case underneath them.

    Compares each shift's start against the previous shift's end, ordered by
    start.
    """
    if len(starts_ends) < 2:
        return [], []
    ordered = sorted(starts_ends, key=lambda p: p[0])
    short, overlapping = [], []
    for (_, prev_end), (next_start, _) in zip(ordered, ordered[1:]):
        gap = (next_start - prev_end).total_seconds() / 3600.0
        if gap < 0:
            overlapping.append((prev_end, next_start, round(gap, 1)))
        elif gap < min_rest_hours:
            short.append((prev_end, next_start, round(gap, 1)))
    return short, overlapping


async def limits_for_tenant(db: AsyncSession) -> tuple[int, int]:
    """(max_consecutive_days, min_rest_hours) as this tenant has them set."""
    try:
        return (await _get_roster_setting(db, "roster.max_consecutive_days"),
                await _get_roster_setting(db, "roster.min_rest_hours"))
    except Exception:
        # A missing or malformed setting must not stop the check; the planner's
        # defaults are the same ones it would have used.
        logger.exception("rest-day: could not read roster settings, using defaults")
        return (_ROSTER_DEFAULTS["roster.max_consecutive_days"],
                _ROSTER_DEFAULTS["roster.min_rest_hours"])


async def assess(db: AsyncSession, *, window_start: date, window_end: date) -> dict:
    """Runs and short rests across the roster in this window.

    Looks at shifts however they were created -- the whole point, since the
    auto-scheduler already declines to break these rules with its own choices
    and says nothing about anybody else's.
    """
    max_consecutive, min_rest = await limits_for_tenant(db)

    rows = (await db.execute(text("""
        SELECT s.guard_user_id, u.full_name,
               COALESCE(s.actual_start, s.scheduled_start) AS starts_at,
               COALESCE(s.actual_end, s.scheduled_end) AS ends_at
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id
         WHERE s.status IN ('completed', 'scheduled', 'active')
           AND COALESCE(s.actual_start, s.scheduled_start)::date
               BETWEEN :a AND :b
         ORDER BY s.guard_user_id, starts_at
    """), {"a": window_start, "b": window_end})).mappings().all()

    by_guard: dict[str, dict] = {}
    for r in rows:
        gid = str(r["guard_user_id"])
        entry = by_guard.setdefault(gid, {"name": r["full_name"], "days": [],
                                          "spans": []})
        entry["days"].append(r["starts_at"].date())
        if r["ends_at"] is not None:
            entry["spans"].append((r["starts_at"], r["ends_at"]))

    over_runs: list[dict] = []
    rest_breaches: list[dict] = []
    overlaps: list[dict] = []

    def _pair(gid, name, prev_end, next_start, gap, status):
        return {"guard_user_id": gid, "guard_name": name,
                "previous_shift_ended": prev_end.isoformat(),
                "next_shift_starts": next_start.isoformat(),
                "gap_hours": gap, "status": status}

    for gid, entry in by_guard.items():
        for start, end, days in runs_over(entry["days"], max_consecutive):
            over_runs.append(Run(gid, entry["name"], start, end, days).as_dict())
        short, overlapping = rest_gaps(entry["spans"], min_rest)
        for prev_end, next_start, gap in short:
            rest_breaches.append(_pair(gid, entry["name"], prev_end, next_start,
                                       gap, SHORT_REST))
        for prev_end, next_start, gap in overlapping:
            overlaps.append(_pair(gid, entry["name"], prev_end, next_start,
                                  gap, OVERLAP))

    over_runs.sort(key=lambda r: r["consecutive_days"], reverse=True)
    rest_breaches.sort(key=lambda r: r["gap_hours"])
    # Worst overlap first: the biggest double-booking is the most wrong.
    overlaps.sort(key=lambda r: r["gap_hours"])

    return {
        "window": {"from": window_start.isoformat(), "to": window_end.isoformat()},
        "max_consecutive_days": max_consecutive,
        "min_rest_hours": min_rest,
        "guards_assessed": len(by_guard),
        "over_run": over_runs,
        "short_rest": rest_breaches,
        "overlap": overlaps,
        "basis": ("Consecutive worked days against this tenant's configured "
                  "maximum. Not the statutory definition of a rest day, which "
                  "is a whole day or a continuous 30 hours for shift workers."),
    }


async def sweep(db: AsyncSession, *, today: date | None = None,
                lookahead_days: int = 30) -> dict:
    """Raise an alert per guard whose roster breaks the consecutive-day limit.

    Reads per tenant with the GUC set AND tenant_id in the WHERE clause -- the
    policy alone is not enough, since a BYPASSRLS connection would otherwise
    process every tenant's roster once per tenant.
    """
    import json

    today = today or date.today()
    window_end = today + timedelta(days=lookahead_days)

    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    raised, assessed = 0, 0
    for tenant_id in tenants:
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(tenant_id)})
            result = await assess(db, window_start=today, window_end=window_end)
            assessed += result["guards_assessed"]

            for run in result["over_run"]:
                # One alert per guard per run. The same stretch re-raised hourly
                # would bury the board it is meant to draw attention to.
                inserted = (await db.execute(text("""
                    INSERT INTO alerts
                        (tenant_id, camera_id, site_id, module_type, severity,
                         alert_code, message_params, title, message, status)
                    SELECT CAST(:t AS uuid), NULL, NULL, 'roster', 'medium',
                           'roster.rest_day_breach', CAST(:params AS jsonb),
                           :title, :msg, 'open'
                     WHERE NOT EXISTS (
                         SELECT 1 FROM alerts a
                          WHERE a.tenant_id = CAST(:t AS uuid)
                            AND a.alert_code = 'roster.rest_day_breach'
                            AND a.message_params->>'guard_user_id' = :gid
                            AND a.message_params->>'from_day' = :from_day
                     )
                    RETURNING id
                """), {
                    "t": str(tenant_id), "gid": run["guard_user_id"],
                    "from_day": run["from_day"],
                    "params": json.dumps(run),
                    "title": f"No rest day: {run['guard_name']}",
                    "msg": (f"{run['guard_name']} is rostered for "
                            f"{run['consecutive_days']} consecutive days "
                            f"({run['from_day']} to {run['to_day']}), past the "
                            f"{result['max_consecutive_days']}-day maximum."),
                })).first()
                if inserted:
                    raised += 1

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("rest-day sweep failed for tenant %s", tenant_id)

    return {"guards_assessed": assessed, "alerts_raised": raised}
