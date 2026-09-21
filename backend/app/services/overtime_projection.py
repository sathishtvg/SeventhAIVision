"""Whether the roster is about to put a guard past the statutory overtime cap.

THE CAP IS ALREADY IN THIS CODEBASE AND IT IS ONLY EVER CHECKED TOO LATE.
payroll.MAX_OT_HOURS_PER_MONTH is 72, the Employment Act limit, and
calculate_pay reports ot_hours_over_statutory_cap when a month is totalled. That
comment is right about its own job -- refusing to pay hours somebody has already
worked would be the wrong correction -- but it means the only moment anyone
learns the cap was breached is after the month has closed, when the guard has
worked the hours and the agency is already in breach.

Nothing looks forward. This does: it adds what a guard has ALREADY accrued to
what the remaining roster will add, and says so while shifts can still be moved.

A PROJECTION IS NOT AN ACTUAL, AND IS NEVER PRESENTED AS ONE. Accrued hours come
from completed shifts via shifts.overtime_minutes -- the exact basis payroll
uses, so the two can never disagree about the past. Scheduled hours are an
estimate of a shift nobody has worked yet: the guard may go home early, the
shift may be reassigned, the roster may change tomorrow. Every number this
produces is labelled with which half it came from.

THE FIGURES ARE THE ONES ALREADY IN THE CODEBASE, NOT NEW ONES. 72 hours from
payroll, 8 hours a day from pwm.HOURS_PER_DAY. Inventing a second set of
statutory numbers that disagreed with payroll's would be worse than having none,
because both would look authoritative.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll import MAX_OT_HOURS_PER_MONTH
from app.services.pwm import HOURS_PER_DAY

logger = logging.getLogger(__name__)

OVER_CAP = "OVER_CAP"        # the roster as it stands breaches the limit
APPROACHING = "APPROACHING"  # inside the warning margin, still fixable
WITHIN = "WITHIN"

#: How close to the cap is close enough to say something. Eight hours is one
#: shift: the point of warning at all is that there is still a shift to move.
DEFAULT_WARN_MARGIN_HOURS = Decimal("8")


@dataclass(frozen=True)
class Projection:
    guard_user_id: str
    guard_name: str
    accrued_ot_hours: Decimal
    projected_ot_hours: Decimal
    total_ot_hours: Decimal
    cap: Decimal
    status: str
    scheduled_shifts_remaining: int

    def as_dict(self) -> dict:
        return {
            "guard_user_id": self.guard_user_id,
            "guard_name": self.guard_name,
            # Split deliberately: one of these is a fact and the other is a
            # forecast, and an operator deciding whether to move a shift needs
            # to know which is which.
            "accrued_ot_hours": float(self.accrued_ot_hours),
            "projected_ot_hours": float(self.projected_ot_hours),
            "total_ot_hours": float(self.total_ot_hours),
            "cap_hours": float(self.cap),
            "over_by_hours": float(max(self.total_ot_hours - self.cap, Decimal("0"))),
            "status": self.status,
            "scheduled_shifts_remaining": self.scheduled_shifts_remaining,
        }


def overtime_in(shift_hours: Decimal,
                normal_hours_per_day: Decimal = HOURS_PER_DAY) -> Decimal:
    """The overtime portion of one scheduled shift.

    Anything beyond a normal day. A shift shorter than that contributes no
    negative overtime -- an eight-hour cap is not a quota to be made up
    elsewhere, and letting short shifts offset long ones would quietly hide a
    guard who works six twelves and one four.
    """
    return max(shift_hours - normal_hours_per_day, Decimal("0"))


def classify(total_ot: Decimal, *, cap: Decimal = MAX_OT_HOURS_PER_MONTH,
             margin: Decimal = DEFAULT_WARN_MARGIN_HOURS) -> str:
    """Where this month stands against the limit."""
    if total_ot > cap:
        return OVER_CAP
    if total_ot >= cap - margin:
        return APPROACHING
    return WITHIN


def project(*, accrued_ot_hours: Decimal, scheduled_shift_hours: list[Decimal],
            normal_hours_per_day: Decimal = HOURS_PER_DAY,
            cap: Decimal = MAX_OT_HOURS_PER_MONTH,
            margin: Decimal = DEFAULT_WARN_MARGIN_HOURS) -> tuple[Decimal, Decimal, str]:
    """(projected_from_roster, total, status) for one guard in one month."""
    projected = sum((overtime_in(h, normal_hours_per_day)
                     for h in scheduled_shift_hours), Decimal("0"))
    total = accrued_ot_hours + projected
    return projected, total, classify(total, cap=cap, margin=margin)


# ── Reading it out of the database ───────────────────────────────────────────

async def project_month(db: AsyncSession, *, month_start: date, month_end: date,
                        normal_hours_per_day: Decimal = HOURS_PER_DAY,
                        cap: Decimal = MAX_OT_HOURS_PER_MONTH,
                        ) -> list[Projection]:
    """Every guard with hours in this month, worst first.

    Tenant scope comes from RLS, so this is only ever the caller's own guards.
    """
    # ACCRUED: completed shifts only, read exactly the way payroll reads them,
    # so the past never has two different answers.
    accrued_rows = (await db.execute(text("""
        SELECT s.guard_user_id, u.full_name,
               COALESCE(SUM(s.overtime_minutes), 0) / 60.0 AS ot_hours
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id
         WHERE s.status = 'completed' AND s.actual_end IS NOT NULL
           AND s.actual_start::date BETWEEN :a AND :b
         GROUP BY s.guard_user_id, u.full_name
    """), {"a": month_start, "b": month_end})).mappings().all()

    # SCHEDULED: what is still to come, by planned duration. Deliberately not
    # joined to the above -- a guard may have only future shifts, or only past
    # ones, and an inner join would drop whichever half is empty.
    scheduled_rows = (await db.execute(text("""
        SELECT s.guard_user_id, u.full_name,
               EXTRACT(EPOCH FROM (s.scheduled_end - s.scheduled_start)) / 3600.0 AS hours
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id
         WHERE s.status IN ('scheduled', 'active')
           AND s.scheduled_start::date BETWEEN :a AND :b
    """), {"a": month_start, "b": month_end})).mappings().all()

    names: dict[str, str] = {}
    accrued: dict[str, Decimal] = {}
    for r in accrued_rows:
        gid = str(r["guard_user_id"])
        names[gid] = r["full_name"]
        accrued[gid] = Decimal(str(r["ot_hours"]))

    scheduled: dict[str, list[Decimal]] = {}
    for r in scheduled_rows:
        gid = str(r["guard_user_id"])
        names.setdefault(gid, r["full_name"])
        scheduled.setdefault(gid, []).append(Decimal(str(r["hours"] or 0)))

    out: list[Projection] = []
    for gid, name in names.items():
        acc = accrued.get(gid, Decimal("0"))
        sched = scheduled.get(gid, [])
        projected, total, status = project(
            accrued_ot_hours=acc, scheduled_shift_hours=sched,
            normal_hours_per_day=normal_hours_per_day, cap=cap)
        out.append(Projection(
            guard_user_id=gid, guard_name=name, accrued_ot_hours=acc,
            projected_ot_hours=projected, total_ot_hours=total, cap=cap,
            status=status, scheduled_shifts_remaining=len(sched)))

    # Worst first: the guard already over the cap is the one to act on.
    out.sort(key=lambda p: p.total_ot_hours, reverse=True)
    return out


# ── The sweep ────────────────────────────────────────────────────────────────

async def sweep_overtime_risk(db: AsyncSession, *, today: date | None = None) -> dict:
    """Raise an alert for any guard the current month's roster puts over the cap.

    ONLY OVER_CAP, NOT APPROACHING. A warning that fires for everyone close to
    a limit becomes background noise, and the report at
    GET /api/v1/payroll/overtime-projection is where somebody looking for the
    near-misses should go. An alert is for the case that needs a decision now.

    THE ALERT HAS NO CAMERA, WHICH IT COULD NOT HAVE HAD BEFORE 0119. Overtime
    is about a person and a month; there is no camera and no site to attach it
    to. Until alerts.camera_id was made nullable, a job like this had to invent
    one -- and for a guarding-only agency, which is exactly the customer who
    rosters this heavily, the alert would have been dropped entirely.

    Reads per tenant with the GUC set AND tenant_id in the WHERE clause. The
    policy alone is not enough: under a connection that bypasses RLS nothing
    filters and the loop would alert every tenant once per tenant.
    """
    import json
    from calendar import monthrange

    today = today or date.today()
    first = today.replace(day=1)
    last = date(first.year, first.month, monthrange(first.year, first.month)[1])

    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    raised, assessed = 0, 0
    for tenant_id in tenants:
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(tenant_id)})
            projections = await project_month(db, month_start=first, month_end=last)
            assessed += len(projections)

            for p in projections:
                if p.status != OVER_CAP:
                    continue
                # One alert per guard per month. Re-raising every hour for the
                # same month would bury the board it is trying to draw
                # attention to.
                inserted = (await db.execute(text("""
                    INSERT INTO alerts
                        (tenant_id, camera_id, site_id, module_type, severity,
                         alert_code, message_params, title, message, status)
                    SELECT CAST(:t AS uuid), NULL, NULL, 'payroll', 'high',
                           'overtime.projected_over_cap', CAST(:params AS jsonb),
                           :title, :msg, 'open'
                     WHERE NOT EXISTS (
                         SELECT 1 FROM alerts a
                          WHERE a.tenant_id = CAST(:t AS uuid)
                            AND a.alert_code = 'overtime.projected_over_cap'
                            AND a.message_params->>'guard_user_id' = :gid
                            AND a.message_params->>'month' = :month
                     )
                    RETURNING id
                """), {
                    "t": str(tenant_id), "gid": p.guard_user_id,
                    "month": f"{first:%Y-%m}",
                    "params": json.dumps({
                        "guard_user_id": p.guard_user_id,
                        "guard_name": p.guard_name,
                        "month": f"{first:%Y-%m}",
                        "accrued_ot_hours": float(p.accrued_ot_hours),
                        "projected_ot_hours": float(p.projected_ot_hours),
                        "total_ot_hours": float(p.total_ot_hours),
                        "cap_hours": float(p.cap),
                    }),
                    "title": f"Overtime cap: {p.guard_name}",
                    "msg": (f"{p.guard_name} is rostered to reach "
                            f"{p.total_ot_hours:.1f}h overtime this month, past "
                            f"the {p.cap:.0f}h limit. "
                            f"{p.accrued_ot_hours:.1f}h already worked, "
                            f"{p.projected_ot_hours:.1f}h still rostered."),
                })).first()
                if inserted:
                    raised += 1

            await db.commit()
        except Exception:
            await db.rollback()
            # One tenant's bad data must not stop the sweep for everybody else.
            logger.exception("overtime sweep failed for tenant %s", tenant_id)

    return {"assessed": assessed, "raised": raised}
