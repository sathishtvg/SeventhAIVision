"""Overtime and fatigue forecast (feature `overtime_fatigue`).

OVERTIME. The platform already projects each guard's month from the roster as it
stands (services/overtime_projection.py, used by payroll; not changed here). What
that projection cannot know is how each guard actually works: some routinely stay
past the end of a shift. This feature LEARNS that, per guard, from the last 90
days of completed shifts (worked hours beyond the rostered ones), and adds it for
every shift still to come this month. It warns when the expected total nears the
Employment Act's 72-hour monthly limit (payroll.MAX_OT_HOURS_PER_MONTH).

A guard with few shifts on record is pulled towards the organisation's own
overrun, so one long night does not label them.

FATIGUE. In the next 7 days, by the organisation's own roster rules
(`roster.min_rest_hours`, default 11; `roster.max_consecutive_days`, default 6):
shifts with too little rest before them, and runs of working days too long.
These are rules, not learning, and each warning says so.

IT WARNS; IT CHANGES NOTHING. The roster stays as it is until a person changes it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store, overtime_projection
from app.services.payroll import MAX_OT_HOURS_PER_MONTH

KEY = "overtime_fatigue"
HISTORY = timedelta(days=90)
PRIOR_SHIFTS = 5.0
MIN_SHIFTS = 20
#: Share of the monthly limit the expected total must reach, by sensitivity.
BASES = {"HIGH": 0.80, "MEDIUM": 0.90, "LOW": 1.00}
ROSTER_DEFAULTS = {"roster.min_rest_hours": 11, "roster.max_consecutive_days": 6}


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


def shrunk(n: int, mean: float, org_mean: float, prior: float = PRIOR_SHIFTS) -> float:
    """A guard's usual overrun, pulled towards the organisation's when there is little history."""
    return (n * mean + prior * org_mean) / (n + prior)


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    rows = (await db.execute(text("""
        SELECT guard_user_id,
               GREATEST(0, EXTRACT(EPOCH FROM (actual_end - actual_start))
                         - EXTRACT(EPOCH FROM (scheduled_end - scheduled_start))) / 3600.0 AS over
          FROM shifts
         WHERE tenant_id = current_setting('app.current_tenant')::uuid
           AND status = 'completed' AND actual_start IS NOT NULL AND actual_end IS NOT NULL
           AND actual_start >= :since
    """), {"since": now - HISTORY})).mappings().all()
    threshold, why = ai_learning.threshold_for(
        sensitivity, await ai_store.recent_decisions(db, KEY), bases=BASES, cap=1.2)
    if len(rows) < MIN_SHIFTS:
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(rows), positives=0, fitted=None,
            metrics={"rows": len(rows)}, threshold=threshold, threshold_reason=why,
            note=f"Still learning: it needs {MIN_SHIFTS - len(rows)} more completed shifts from the last 90 days.")
    per: dict = defaultdict(list)
    for r in rows:
        per[str(r["guard_user_id"])].append(float(r["over"] or 0))
    org_mean = sum(float(r["over"] or 0) for r in rows) / len(rows)
    guards = {g: {"n": len(v), "mean": round(sum(v) / len(v), 4)} for g, v in per.items()}
    stayed = sum(1 for r in rows if float(r["over"] or 0) > 0.25)
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(rows), positives=stayed,
        fitted={"org_mean_overrun_hours": round(org_mean, 4), "prior_shifts": PRIOR_SHIFTS, "guards": guards},
        metrics={"rows": len(rows), "guards": len(guards), "org_mean_overrun_hours": round(org_mean, 2)},
        threshold=threshold, threshold_reason=why,
        note=(f"Learned from {len(rows)} completed shifts of {len(guards)} guards: on average a shift ran "
              f"{round(org_mean * 60)} minutes past its rostered end."))


async def _roster_rules(db: AsyncSession) -> dict:
    rows = (await db.execute(text(
        "SELECT setting_key, setting_value FROM tenant_settings "
        "WHERE setting_key = ANY(:k) AND tenant_id = current_setting('app.current_tenant')::uuid"),
        {"k": list(ROSTER_DEFAULTS)})).all()
    out = dict(ROSTER_DEFAULTS)
    for k, v in rows:
        try:
            v = json.loads(v) if isinstance(v, str) else v
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
        except ValueError:
            pass
    return out


def fatigue_issues(shifts: list[dict], *, min_rest: float, max_days: int, tz: ZoneInfo) -> list[str]:
    """In words, what in these shifts (one guard's, oldest first) breaks the rest rules."""
    issues = []
    for prev, nxt in zip(shifts, shifts[1:]):
        rest = (nxt["scheduled_start"] - prev["scheduled_end"]).total_seconds() / 3600
        if rest < min_rest:
            issues.append(f"only {rest:.1f} hours' rest before the shift on "
                          f"{nxt['scheduled_start'].astimezone(tz):%a %d %b %H:%M} (the rule is {min_rest:g})")
    days = sorted({s["scheduled_start"].astimezone(tz).date() for s in shifts})
    run, longest, end = 1, 1, days[0] if days else None
    for a, b in zip(days, days[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        if run > longest:
            longest, end = run, b
    if days and longest > max_days:
        issues.append(f"{longest} working days in a row up to {end:%a %d %b} (the rule is {max_days})")
    return issues


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0}
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    if not model or model["status"] != "READY":
        return counts
    tz = await _zone(db)
    learned = model["fitted"]
    org_mean = float(learned["org_mean_overrun_hours"])
    cap = float(MAX_OT_HOURS_PER_MONTH)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    today = now.astimezone(tz).date()
    first = today.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    month_end_utc = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    # Where each guard works, for the site filter and so a supervisor sees only their own sites.
    site_of = {str(g): s for g, s in (await db.execute(text("""
        SELECT DISTINCT ON (guard_user_id) guard_user_id, site_id FROM shifts
         WHERE tenant_id = current_setting('app.current_tenant')::uuid AND scheduled_start >= :a ORDER BY guard_user_id, scheduled_start
    """), {"a": now - timedelta(days=1)})).all()}

    month_subjects, raised = [], set()
    for p in await overtime_projection.project_month(db, month_start=first, month_end=last):
        site = site_of.get(p.guard_user_id)
        if allowed is not None and str(site) not in allowed:
            continue
        sid = ai_store.subject_id(p.guard_user_id, f"{first:%Y-%m}")
        month_subjects.append(sid)
        g = learned["guards"].get(p.guard_user_id)
        per_shift = shrunk(g["n"], g["mean"], org_mean) if g else org_mean
        extra = per_shift * p.scheduled_shifts_remaining
        expected = float(p.total_ot_hours) + extra
        share = expected / cap if cap else 0.0
        if share < threshold:
            continue
        reasons = [
            {"factor": "accrued", "words": f"{float(p.accrued_ot_hours):.1f} overtime hours worked so far this month",
             "push": float(p.accrued_ot_hours)},
            {"factor": "roster", "words": f"the roster adds {float(p.projected_ot_hours):.1f} more "
                                          f"over {p.scheduled_shifts_remaining} shifts", "push": float(p.projected_ot_hours)},
            {"factor": "learned_overrun", "words": (f"usually stays {round(per_shift * 60)} minutes past a shift's end, "
                                                    f"about {extra:.1f} hours more"), "push": round(extra, 2)},
        ]
        what = await ai_store.upsert_open(
            db, KEY, subject_type="guard_month", subject=sid, site_id=site, person_user_id=p.guard_user_id,
            title=f"{p.guard_name or 'A guard'} is heading for {expected:.0f} overtime hours in {first:%B} (limit {cap:.0f})",
            detail="Move or reassign a shift before the limit is reached; going over needs an MOM exemption.",
            score=share, threshold=threshold, reasons=reasons,
            evidence={"accrued": float(p.accrued_ot_hours), "roster": float(p.projected_ot_hours),
                      "learned_extra": round(extra, 2), "expected": round(expected, 2), "limit": cap},
            model_id=model["id"], valid_until=month_end_utc)
        counts[what] += 1
        raised.add(sid)
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "guard_month", raised, month_subjects, "The forecast fell below the bar.")

    # Fatigue: the next 7 days by the roster rules, with the shift before them for the first gap.
    rules = await _roster_rules(db)
    rows = (await db.execute(text("""
        SELECT s.guard_user_id, s.site_id, s.scheduled_start, s.scheduled_end, u.full_name
          FROM shifts s JOIN users u ON u.id = s.guard_user_id
         WHERE s.tenant_id = current_setting('app.current_tenant')::uuid AND s.status IN ('scheduled', 'active', 'completed')
           AND s.scheduled_start >= :a AND s.scheduled_start < :b
         ORDER BY s.guard_user_id, s.scheduled_start
    """), {"a": now - timedelta(days=1), "b": now + timedelta(days=7)})).mappings().all()
    by_guard: dict = defaultdict(list)
    for r in rows:
        by_guard[str(r["guard_user_id"])].append(dict(r))
    week = today - timedelta(days=today.weekday())
    week_subjects, tired = [], set()
    for gid, shifts in by_guard.items():
        site = shifts[-1]["site_id"]
        if allowed is not None and str(site) not in allowed:
            continue
        sid = ai_store.subject_id(gid, f"week-{week.isoformat()}")
        week_subjects.append(sid)
        issues = fatigue_issues(shifts, min_rest=float(rules["roster.min_rest_hours"]),
                                max_days=int(rules["roster.max_consecutive_days"]), tz=tz)
        if not issues:
            continue
        what = await ai_store.upsert_open(
            db, KEY, subject_type="guard_week", subject=sid, site_id=site, person_user_id=gid,
            title=f"{shifts[0]['full_name'] or 'A guard'} may be over-tired in the next 7 days",
            detail="By your roster rules (a rule, not a prediction). Consider moving a shift.",
            score=1.0, threshold=None,
            reasons=[{"factor": "rule", "words": w, "push": 1.0} for w in issues[:3]],
            evidence={"issues": issues, "min_rest_hours": rules["roster.min_rest_hours"],
                      "max_consecutive_days": rules["roster.max_consecutive_days"]},
            model_id=model["id"], valid_until=now + timedelta(days=7))
        counts[what] += 1
        tired.add(sid)
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "guard_week", tired, week_subjects, "The roster no longer breaks the rest rules.")
    return counts


def month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
