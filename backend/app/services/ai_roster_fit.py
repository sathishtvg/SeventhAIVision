"""Roster suggestions (feature `roster_fit`): a better-fitting guard for a shift, put forward for a planner.

THE SAME LEARNING AS THE LATE AND NO-SHOW WARNINGS (services/ai_late_risk.py),
kept as a model of its own so each feature's bar and decisions stay its own. For
each shift in the next 7 days, it asks the history how likely the rostered guard
is to start late or miss it, and how likely each other guard who could take it.

WHO COULD TAKE IT: an active guard (roles 3, 4, 5) of the organisation who has
worked at that site in the last 180 days, has no shift within the organisation's
minimum rest (`roster.min_rest_hours`) of it, and is not on approved leave that day.

IT SUGGESTS WHEN the rostered guard's chance reaches the bar and the best other
guard's is at least 20 points lower. It names who, and why, in words.

THE ROSTER IS NOT CHANGED. Neither the auto-scheduler (services/roster_autoschedule.py)
nor any shift is touched; a planner who agrees moves the shift on the roster.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_late_risk, ai_learning, ai_overtime_fatigue, ai_store

KEY = "roster_fit"
FACTOR_TITLES = ai_late_risk.FACTOR_TITLES
AHEAD = timedelta(days=7)
#: How much lower the best other guard's chance must be, in points, to be worth putting forward.
MIN_GAIN = 0.20
GUARD_ROLES = (3, 4, 5)
T = "current_setting('app.current_tenant')::uuid"


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    return await ai_late_risk.train(db, sensitivity=sensitivity, now=now, key=KEY)


async def _guards(db: AsyncSession) -> dict:
    rows = (await db.execute(text(f"""
        SELECT id, full_name FROM users
         WHERE tenant_id = {T} AND is_active AND role_id IN {GUARD_ROLES}
    """))).all()
    return {r[0]: r[1] for r in rows}


async def _on_leave(db: AsyncSession, now: datetime) -> dict:
    rows = (await db.execute(text(f"""
        SELECT guard_user_id, start_date, end_date FROM leave_requests
         WHERE tenant_id = {T} AND status = 'approved' AND end_date >= :a AND start_date <= :b
    """), {"a": (now - timedelta(days=1)).date(), "b": (now + AHEAD + timedelta(days=1)).date()})).all()
    out: dict = defaultdict(list)
    for g, a, b in rows:
        out[g].append((a, b))
    return out


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0}
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return counts
    tz = await ai_late_risk._zone(db)
    rules = await ai_overtime_fatigue._roster_rules(db)
    min_rest = timedelta(hours=float(rules["roster.min_rest_hours"]))
    guards = await _guards(db)
    leave = await _on_leave(db, now)
    shifts = await ai_late_risk.load_shifts(db, now, ahead=AHEAD)
    allowed = None if site_ids is None else {str(s) for s in site_ids}

    worked_at: dict = defaultdict(lambda: defaultdict(int))  # site -> guard -> finished shifts there
    busy: dict = defaultdict(list)                           # guard -> [(start, end)]
    for s in shifts:
        busy[s["guard_user_id"]].append((s["scheduled_start"], s["scheduled_end"]))
        if s["scheduled_start"] < now and ai_late_risk.outcome_of(s, now) is not None:
            worked_at[s["site_id"]][s["guard_user_id"]] += 1

    def free(g, s) -> bool:
        if any(a < s["scheduled_end"] + min_rest and b > s["scheduled_start"] - min_rest for a, b in busy[g]):
            return False
        day = s["scheduled_start"].astimezone(tz).date()
        return not any(a <= day <= b for a, b in leave.get(g, []))

    found: list[dict] = []

    def consider(s: dict, history: ai_late_risk.History) -> None:
        if s["status"] != "scheduled" or not (now <= s["scheduled_start"] < now + AHEAD):
            return
        if allowed is not None and str(s["site_id"]) not in allowed:
            return
        mine, mine_e = history.values_for(s["guard_user_id"], s["site_id"], s["scheduled_start"])
        p_mine, parts = ai_learning.score(fitted, mine)
        best = None
        for g, n in worked_at[s["site_id"]].items():
            if g == s["guard_user_id"] or g not in guards or not free(g, s):
                continue
            values, e = history.values_for(g, s["site_id"], s["scheduled_start"])
            p, _ = ai_learning.score(fitted, values)
            if best is None or p < best[1]:
                best = (g, p, e, n)
        found.append({"shift": s, "p": p_mine, "parts": parts, "evidence": mine_e, "best": best})

    ai_late_risk.build_rows(shifts, tz, now, on_each=consider)

    upcoming, raised = [], set()
    for f in found:
        s, best = f["shift"], f["best"]
        upcoming.append(s["id"])
        if f["p"] < threshold or best is None or f["p"] - best[1] < MIN_GAIN:
            continue
        g, p_best, e_best, n_there = best
        local = s["scheduled_start"].astimezone(tz)
        name, other = s["guard_name"] or "The rostered guard", guards.get(g) or "Another guard"
        where = f" at {s['site_name']}" if s["site_name"] else ""
        what = await ai_store.upsert_open(
            db, KEY, subject_type="shift", subject=s["id"], site_id=s["site_id"], person_user_id=s["guard_user_id"],
            title=f"For the {local:%a %d %b %H:%M} shift{where}, {other} may be a better fit than {name}",
            detail="A suggestion for the planner. The roster is not changed; move the shift on the roster if you agree.",
            score=f["p"], threshold=threshold,
            reasons=[
                {"factor": "rostered", "words": f"{name}: {round(f['p'] * 100)}% chance of starting late or missing it",
                 "push": round(f["p"], 4)},
                {"factor": "alternative", "words": (f"{other}: {round(p_best * 100)}% chance, has worked{where} "
                                                    f"{n_there} times and is free then"), "push": round(p_best, 4)},
                *ai_late_risk.reasons_of(f["parts"], f["evidence"])[:1],
            ],
            evidence={"alternative_user_id": str(g), "alternative_chance": round(p_best, 4),
                      "rostered_chance": round(f["p"], 4), "shift_start": s["scheduled_start"].isoformat()},
            model_id=model["id"], valid_until=s["scheduled_start"])
        counts[what] += 1
        raised.add(s["id"])
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "shift", raised, upcoming, "No better fit than the rostered guard any more.")
    return counts
