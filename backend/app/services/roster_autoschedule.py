"""AI roster auto-scheduler (ShiftSecure Phase 2B) — greedy day-by-day,
slot-by-slot assignment against shift_patterns, scored by 8 rules.

Not a CSP/ILP solver. Rules 1 (min rest), 2 (max consecutive days), and 5
(respect leave) hard-exclude a candidate from a slot. Rules 3 (balance
day/night), 4 (honor preferences), and 8 (off-day stagger) rank the
survivors via score_candidate. Rules 6 (site minimum coverage) and 7
(supervisor present) can't be force-satisfied by assignment alone, so they
become warnings on the draft for a human to resolve before publishing —
the same "flag, don't block" approach already used for geofence checks in
Phase 2A.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SUPERVISOR_ROLE_IDS = (1, 2, 3, 8)  # Manager (8) is senior to Supervisor, also satisfies "supervisor present"
MANAGER_ROLE_IDS = (1, 2, 8)
GUARD_ROLE_IDS = (3, 4, 5)  # supervisors can also be scheduled onto shifts

_ROSTER_DEFAULTS = {
    "roster.max_consecutive_days": 6,
    "roster.min_rest_hours": 11,
}


def infer_shift_type(start_time: time) -> str:
    """Pure: 05:00-16:59 start -> day, else night. 'split' is never inferred
    here — only ever set explicitly when two linked slots are created for
    one guard on one day."""
    return "day" if 5 <= start_time.hour < 17 else "night"


def is_guard_on_leave(guard_id: str, on_date: date, leave_blocks: list[dict]) -> bool:
    """Pure: leave_blocks is a pre-fetched list of
    {guard_user_id, start_date, end_date} dicts."""
    return any(
        str(lb["guard_user_id"]) == str(guard_id) and lb["start_date"] <= on_date <= lb["end_date"]
        for lb in leave_blocks
    )


def score_candidate(
    guard_id: str,
    slot_shift_type: str,
    preferences: dict[str, dict],
    history: dict[str, dict],
    day_index: int,
) -> float:
    """Pure: higher is better. Additive bonuses for rules 3, 4, 8."""
    score = 0.0
    h = history[guard_id]
    pref = preferences.get(guard_id, {})

    # Rule 3 — balance day/night: bonus proportional to how skewed this
    # guard already is toward the OPPOSITE of this slot's type.
    total = h["day_count"] + h["night_count"]
    if total > 0:
        skew_source = h["day_count"] if slot_shift_type == "night" else h["night_count"]
        score += (skew_source / total) * 2.0

    # Rule 4 — honor preferences
    if pref.get("preferred_shift_type") == slot_shift_type:
        score += 3.0

    # Rule 8 — off-day stagger: bonus for guards who've gone longest since
    # their last assigned day within this batch (spreads rest days evenly).
    last_day = h["last_assigned_day_index"]
    if last_day is not None:
        score += min(day_index - last_day, 5) * 0.5

    return score


async def _get_roster_setting(db: AsyncSession, key: str) -> int:
    row = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"), {"k": key}
    )).first()
    if row is not None and isinstance(row[0], int):
        return row[0]
    return _ROSTER_DEFAULTS[key]


async def generate_draft(
    db: AsyncSession,
    user_id: str,
    site_id: str | None,
    period_start: date,
    period_end: date,
) -> str:
    """Orchestrates the greedy assignment for the current tenant (GUC must
    already be set). Returns the new roster_batches.id."""
    max_consecutive = await _get_roster_setting(db, "roster.max_consecutive_days")
    min_rest_hours = await _get_roster_setting(db, "roster.min_rest_hours")

    site_clause = "AND p.site_id = CAST(:site_id AS uuid)" if site_id else ""
    patterns = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT p.site_id, p.days_of_week, p.start_time, p.duration_minutes,
                   s.min_guards_per_shift
            FROM shift_patterns p JOIN sites s ON s.id = p.site_id
            WHERE p.is_active = TRUE {site_clause}
        """),
        {"site_id": site_id} if site_id else {},
    )).mappings().all()]

    guard_rows = [dict(r) for r in (await db.execute(
        text("SELECT id, role_id FROM users WHERE role_id = ANY(:roles) AND is_active = TRUE"),
        {"roles": list(GUARD_ROLE_IDS)},
    )).mappings().all()]
    guard_ids = [str(g["id"]) for g in guard_rows]
    supervisor_ids = {str(g["id"]) for g in guard_rows if g["role_id"] in SUPERVISOR_ROLE_IDS}
    manager_ids = {str(g["id"]) for g in guard_rows if g["role_id"] in MANAGER_ROLE_IDS}

    leave_blocks = [dict(r) for r in (await db.execute(
        text("SELECT guard_user_id, start_date, end_date FROM guard_leave_blocks "
             "WHERE start_date <= :end AND end_date >= :start"),
        {"start": period_start, "end": period_end},
    )).mappings().all()]

    pref_rows = [dict(r) for r in (await db.execute(
        text("SELECT guard_user_id, preferred_shift_type, preferred_off_days FROM guard_shift_preferences")
    )).mappings().all()]
    preferences = {str(p["guard_user_id"]): p for p in pref_rows}

    history: dict[str, dict] = {
        gid: {
            "day_count": 0, "night_count": 0,
            "last_assigned_day_index": None, "last_shift_end": None,
            "consecutive_days": 0, "last_day_index_worked": None,
        }
        for gid in guard_ids
    }

    draft_rows: list[dict] = []
    total_days = (period_end - period_start).days + 1

    for day_index in range(total_days):
        current_date = period_start + timedelta(days=day_index)
        weekday = current_date.weekday()  # 0=Mon..6=Sun, matches shift_patterns.days_of_week

        day_slots = [p for p in patterns if weekday in (p["days_of_week"] or [])]
        assigned_today_by_site: dict[str, list[str]] = {}

        for slot in day_slots:
            site_id_str = str(slot["site_id"])
            shift_type = infer_shift_type(slot["start_time"])
            slot_start = datetime.combine(current_date, slot["start_time"], tzinfo=timezone.utc)
            slot_end = slot_start + timedelta(minutes=slot["duration_minutes"])

            candidates = []
            for gid in guard_ids:
                h = history[gid]
                if is_guard_on_leave(gid, current_date, leave_blocks):
                    continue  # rule 5
                if h["last_shift_end"] is not None:
                    rest_hours = (slot_start - h["last_shift_end"]).total_seconds() / 3600
                    if rest_hours < min_rest_hours:
                        continue  # rule 1
                if h["last_day_index_worked"] == day_index - 1 and h["consecutive_days"] >= max_consecutive:
                    continue  # rule 2
                candidates.append(gid)

            chosen = None
            if candidates:
                chosen = max(candidates, key=lambda gid: score_candidate(gid, shift_type, preferences, history, day_index))

            warnings: list[str] = []
            if chosen is None:
                warnings.append("unfilled_slot")
            else:
                h = history[chosen]
                h["day_count" if shift_type == "day" else "night_count"] += 1
                h["last_shift_end"] = slot_end
                h["consecutive_days"] = (h["consecutive_days"] + 1) if h["last_day_index_worked"] == day_index - 1 else 1
                h["last_day_index_worked"] = day_index
                h["last_assigned_day_index"] = day_index
                assigned_today_by_site.setdefault(site_id_str, []).append(chosen)

            draft_rows.append({
                "guard_user_id": chosen, "site_id": site_id_str,
                "scheduled_start": slot_start, "scheduled_end": slot_end,
                "shift_type": shift_type, "warnings": warnings,
                "min_coverage": slot["min_guards_per_shift"],
                "date": current_date,
            })

        # Rules 6 (coverage) + 7 (supervisor present) — evaluated once per
        # site after all of that day's slots are assigned.
        for site_id_str, assigned in assigned_today_by_site.items():
            site_day_rows = [r for r in draft_rows if r["site_id"] == site_id_str and r["date"] == current_date]
            min_cov = next((r["min_coverage"] for r in site_day_rows if r["min_coverage"]), None)
            if min_cov and len(assigned) < min_cov:
                for r in site_day_rows:
                    r["warnings"].append("below_min_coverage")
            if not any(gid in supervisor_ids for gid in assigned):
                for r in site_day_rows:
                    r["warnings"].append("no_supervisor_present")
            if not any(gid in manager_ids for gid in assigned):
                for r in site_day_rows:
                    r["warnings"].append("no_manager_present")

    unfilled = sum(1 for r in draft_rows if r["guard_user_id"] is None)
    coverage_shortfalls = sum(1 for r in draft_rows if "below_min_coverage" in r["warnings"])
    missing_supervisor_days = len({
        (r["site_id"], r["date"]) for r in draft_rows if "no_supervisor_present" in r["warnings"]
    })
    missing_manager_days = len({
        (r["site_id"], r["date"]) for r in draft_rows if "no_manager_present" in r["warnings"]
    })
    rules_summary = {
        "unfilled_slots": unfilled,
        "coverage_shortfalls": coverage_shortfalls,
        "missing_supervisor_days": missing_supervisor_days,
        "missing_manager_days": missing_manager_days,
    }

    batch_row = (await db.execute(
        text("""
            INSERT INTO roster_batches
                (tenant_id, site_id, period_start, period_end, generated_by_user_id, rules_summary)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site_id AS uuid), :ps, :pe, CAST(:uid AS uuid), CAST(:summary AS jsonb))
            RETURNING id
        """),
        {"site_id": site_id, "ps": period_start, "pe": period_end, "uid": user_id, "summary": json.dumps(rules_summary)},
    )).first()
    batch_id = str(batch_row.id)

    for r in draft_rows:
        await db.execute(
            text("""
                INSERT INTO roster_draft_shifts
                    (tenant_id, batch_id, guard_user_id, site_id, scheduled_start, scheduled_end, shift_type, warnings)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:bid AS uuid), CAST(:gid AS uuid),
                        CAST(:sid AS uuid), :ss, :se, :stype, CAST(:warn AS jsonb))
            """),
            {
                "bid": batch_id, "gid": r["guard_user_id"], "sid": r["site_id"],
                "ss": r["scheduled_start"], "se": r["scheduled_end"], "stype": r["shift_type"],
                "warn": json.dumps(r["warnings"]),
            },
        )

    # commit() ends the transaction that `is_local=true` scoped
    # app.current_tenant to (see get_db_with_tenant) — restore it immediately
    # so the caller's subsequent reads on this same session stay RLS-correct.
    # Same pattern as services/roster.py::generate_roster_shifts.
    tid = (await db.execute(text("SELECT current_setting('app.current_tenant', true)"))).scalar()
    await db.commit()
    if tid:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})

    return batch_id
