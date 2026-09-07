"""AI roster auto-scheduler (ShiftSecure Phase 2B) — greedy day-by-day,
post-by-post assignment against shift_patterns, scored by 9 rules.

Not a CSP/ILP solver. Rules 1 (min rest), 2 (max consecutive days), and 5
(respect leave) hard-exclude a candidate from a post. Rules 3 (balance
day/night), 4 (honor preferences), and 8 (off-day stagger) rank the
survivors via score_candidate. Rule 9 (site duty team) narrows the pool
before that ranking, where the site has a team. Rules 6 (site minimum
coverage) and 7 (supervisor present) can't be force-satisfied by
assignment alone, so they become warnings on the draft for a human to
resolve before publishing — the same "flag, don't block" approach already
used for geofence checks in Phase 2A.

A shift pattern is a POST, not a person: a site staffed for three day
officers produces three draft shifts from one pattern. That count comes
from sites.day_guards_required / night_guards_required, picked by the
pattern's inferred shift type.
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

# Rules a planner can set per run. Two of these used to live in tenant
# settings and the rest were fixed in this file, which meant the only way to
# schedule day-only cover for a week was to not use the scheduler.
#
# A None on a numeric rule means the rule is OFF, not zero — "no maximum" and
# "a maximum of nothing" are opposite instructions, and a planner switching a
# rule off should not silently get the strictest possible version of it.
DEFAULT_RULES: dict = {
    # rotation | day_only | night_only — which shift types to fill at all.
    "shift_pattern": "rotation",
    "fair_rotation": True,
    "min_rest_hours": None,            # None -> the tenant setting
    "max_consecutive_days": None,      # None -> the tenant setting
    "max_night_shifts_per_period": None,
    "max_off_days_per_period": None,
    # Overrides the site's own day/night strength. None keeps per-site
    # staffing, which is richer than one number for the whole estate.
    "min_headcount": None,
    "honour_preferences": True,
    "respect_leave": True,
    # Applied at PUBLISH, not here: the draft is a proposal, and clearing a
    # live roster when somebody generates a draft they might discard would be
    # destructive at the wrong moment.
    "overwrite_existing": False,
}


def infer_shift_type(start_time: time) -> str:
    """Pure: 05:00-16:59 start -> day, else night. 'split' is never inferred
    here — only ever set explicitly when two linked slots are created for
    one guard on one day."""
    return "day" if 5 <= start_time.hour < 17 else "night"


def posts_for_group(group_slots: list[dict], required: int) -> list[dict]:
    """Pure: deal `required` posts round-robin across one shift type's patterns.

    A site's strength is a figure for the SHIFT, not for each pattern — "three
    officers on day duty" is three posts spread over that site's day patterns,
    not three on every one of them. A site running an 08:00 and a 14:00 day
    pattern with three due gets two on the first and one on the second.

    Returns [] for a shift type the site does not staff (required 0), which is
    how a site with no night shift stops generating night shifts at all.
    """
    if required <= 0 or not group_slots:
        return []
    return [group_slots[i % len(group_slots)] for i in range(required)]


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
    rules: dict | None = None,
) -> float:
    """Pure: higher is better. Additive bonuses for rules 3, 4, 8 and 10."""
    rules = rules or DEFAULT_RULES
    score = 0.0
    h = history[guard_id]
    pref = preferences.get(guard_id, {})

    # Rule 3 — balance day/night: bonus proportional to how skewed this
    # guard already is toward the OPPOSITE of this slot's type.
    if rules.get("fair_rotation", True):
        total = h["day_count"] + h["night_count"]
        if total > 0:
            skew_source = h["day_count"] if slot_shift_type == "night" else h["night_count"]
            score += (skew_source / total) * 2.0

    # Rule 4 — honor preferences
    if rules.get("honour_preferences", True) and pref.get("preferred_shift_type") == slot_shift_type:
        score += 3.0

    # Rule 10 — somebody who has been idle past the allowed number of off days
    # is the one who most needs work. Weighted above every other bonus so it
    # actually decides, rather than being outvoted by a preference match.
    max_off = rules.get("max_off_days_per_period")
    if max_off is not None:
        worked = h["day_count"] + h["night_count"]
        off_so_far = (day_index + 1) - worked
        if off_so_far > max_off:
            score += 6.0 + (off_so_far - max_off)

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
    rules: dict | None = None,
) -> str:
    """Orchestrates the greedy assignment for the current tenant (GUC must
    already be set). Returns the new roster_batches.id.

    `rules` overrides DEFAULT_RULES for this run only. Omitted, the behaviour
    is exactly what it was before rules existed: the tenant's rest and
    consecutive-day settings, every rule on, both shift types filled.
    """
    rules = {**DEFAULT_RULES, **(rules or {})}

    # A per-run number wins over the tenant setting; the setting remains the
    # default so an unconfigured run still behaves like the tenant expects.
    max_consecutive = rules["max_consecutive_days"]
    if max_consecutive is None:
        max_consecutive = await _get_roster_setting(db, "roster.max_consecutive_days")
    min_rest_hours = rules["min_rest_hours"]
    if min_rest_hours is None:
        min_rest_hours = await _get_roster_setting(db, "roster.min_rest_hours")

    site_clause = "AND p.site_id = CAST(:site_id AS uuid)" if site_id else ""
    patterns = [dict(r) for r in (await db.execute(
        text(f"""
            SELECT p.site_id, p.days_of_week, p.start_time, p.duration_minutes,
                   s.day_guards_required, s.night_guards_required
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

    # Who is posted to each site, per shift type. A site with a team draws
    # from it; a site without one draws from every guard, which is what the
    # scheduler did for every site before duty teams existed.
    duty_rows = [dict(r) for r in (await db.execute(
        text("SELECT site_id, guard_user_id, shift_type FROM site_duty_assignments")
    )).mappings().all()]
    duty_teams: dict[tuple[str, str], set[str]] = {}
    for d in duty_rows:
        duty_teams.setdefault((str(d["site_id"]), d["shift_type"]), set()).add(str(d["guard_user_id"]))

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

        # Slots grouped into the thing a site is actually staffed for: a shift
        # TYPE, not an individual pattern. `day_guards_required` means "three
        # officers on day duty here", so three posts are filled across that
        # site's day patterns — not three per pattern, which would multiply a
        # site that already runs a morning and an afternoon pattern.
        groups: dict[tuple[str, str], list[dict]] = {}
        for slot in day_slots:
            key = (str(slot["site_id"]), infer_shift_type(slot["start_time"]))
            groups.setdefault(key, []).append(slot)

        for (site_id_str, shift_type), group_slots in groups.items():
            # Day-only and night-only runs exist for a reason: covering a
            # week of days while nights are handled by a standing roster is a
            # normal request, and doing it by generating everything and
            # deleting half is how a roster gets wrong.
            mode = rules["shift_pattern"]
            if mode == "day_only" and shift_type != "day":
                continue
            if mode == "night_only" and shift_type != "night":
                continue

            if rules["min_headcount"] is not None:
                required = rules["min_headcount"]
            else:
                required = (group_slots[0]["day_guards_required"] if shift_type == "day"
                            else group_slots[0]["night_guards_required"])
            required = max(int(required or 0), 0)

            team = duty_teams.get((site_id_str, shift_type), set())
            placed_in_group: set[str] = set()
            filled_here = 0

            # Posts dealt round-robin across this shift type's patterns, so a
            # site with a 08:00 and a 14:00 day pattern and three officers due
            # gets two on the first and one on the second rather than all
            # three stacked on whichever pattern was read first.
            for slot in posts_for_group(group_slots, required):
                slot_start = datetime.combine(current_date, slot["start_time"], tzinfo=timezone.utc)
                slot_end = slot_start + timedelta(minutes=slot["duration_minutes"])

                candidates = []
                for gid in guard_ids:
                    if gid in placed_in_group:
                        continue  # already holds a post of this type here today
                    h = history[gid]
                    if rules["respect_leave"] and is_guard_on_leave(gid, current_date, leave_blocks):
                        continue  # rule 5
                    if h["last_shift_end"] is not None:
                        rest_hours = (slot_start - h["last_shift_end"]).total_seconds() / 3600
                        if rest_hours < min_rest_hours:
                            continue  # rule 1
                    if h["last_day_index_worked"] == day_index - 1 and h["consecutive_days"] >= max_consecutive:
                        continue  # rule 2
                    # Rule 11 — night cap. A hard exclusion rather than a
                    # scoring penalty: "no more than fifteen nights" is a
                    # limit somebody agreed to, not a preference to weigh.
                    night_cap = rules["max_night_shifts_per_period"]
                    if (shift_type == "night" and night_cap is not None
                            and h["night_count"] >= night_cap):
                        continue
                    candidates.append(gid)

                # Rule 9 — the site's own team first. Falling back to the wider
                # pool rather than leaving the post empty is deliberate: an
                # unmanned post is a worse outcome than an unfamiliar guard,
                # and the warning says which happened, so a planner can widen
                # the team instead of discovering the gap at 07:00.
                warnings: list[str] = []
                on_team = [gid for gid in candidates if gid in team]
                if team and on_team:
                    pool = on_team
                else:
                    pool = candidates
                    if team:
                        warnings.append("outside_duty_team")

                chosen = None
                if pool:
                    chosen = max(pool, key=lambda gid: score_candidate(
                        gid, shift_type, preferences, history, day_index, rules))

                if chosen is None:
                    # Which pool it would have come from stops being the useful
                    # thing to say once the post is empty.
                    warnings = ["unfilled_slot"]
                else:
                    filled_here += 1
                    placed_in_group.add(chosen)
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
                    "min_coverage": required,
                    "date": current_date,
                })

            # Rule 6 — coverage, judged per shift type rather than per site-day.
            # A site can be fully staffed by day and short by night, and one
            # combined figure cannot say that.
            if required and filled_here < required:
                for r in draft_rows:
                    if (r["site_id"] == site_id_str and r["date"] == current_date
                            and r["shift_type"] == shift_type):
                        r["warnings"].append("below_min_coverage")

        # Rule 7 — supervisor and manager presence, still judged once per site
        # per day: the question is whether anyone senior was on site at all,
        # which does not divide by shift type.
        for site_id_str, assigned in assigned_today_by_site.items():
            site_day_rows = [r for r in draft_rows if r["site_id"] == site_id_str and r["date"] == current_date]
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
    # Posts filled by someone who is not on that site's team. Not a failure —
    # the roster is complete — but it is the number that tells a planner the
    # team is too small for the strength the site is configured for.
    off_team_fills = sum(1 for r in draft_rows if "outside_duty_team" in r["warnings"])

    rules_summary = {
        "unfilled_slots": unfilled,
        "coverage_shortfalls": coverage_shortfalls,
        "missing_supervisor_days": missing_supervisor_days,
        "missing_manager_days": missing_manager_days,
        "off_team_fills": off_team_fills,
        # The rules this batch was generated under, kept with the batch.
        # Two drafts of the same period can differ entirely because the rules
        # differed, and without this recorded there is no way to tell which
        # one produced the roster somebody is looking at — or to reproduce it.
        "rules": {**rules, "min_rest_hours": min_rest_hours,
                  "max_consecutive_days": max_consecutive},
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
