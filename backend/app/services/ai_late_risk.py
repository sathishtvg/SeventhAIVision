"""Late and no-show warnings (feature `late_risk`): shifts in the next 36 hours likely to start late or be missed.

WHAT IT LEARNS FROM. Every finished shift of the last 180 days in this
organisation: whether it started late (`shifts.is_late`) or was missed (a
`no_show` violation, or a shift whose time passed with nobody checking in).

THE FACTORS, each worked out from what was known BEFORE the shift, so the model
never learns from the answer:
  guard_late_rate    how often this guard was late or absent in their last 10 shifts
  guard_experience   how many shifts this guard has on record (more history, steadier)
  site_late_rate     how often this site's shifts started late in the 60 days before
  night              a shift starting from 19:00 to 05:59, where the site is
  early              a shift starting from 05:00 to 07:59
  weekend            Saturday or Sunday
  hours_last_7d      hours rostered for the guard in the 7 days before
  rest_hours         hours since the guard's previous shift ended (48 at most)

A RATE WITH LITTLE HISTORY IS PULLED TOWARDS THE ORGANISATION'S OWN. A guard with
one shift who was late for it is not "late 100% of the time"; with two shifts'
weight of the organisation's rate added, they are a little above average.

IT WARNS; IT CHANGES NOTHING. A warning is a row in ai_suggestions. The shift,
the roster and the standby list are untouched; a supervisor decides.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store

KEY = "late_risk"
FACTORS = ["guard_late_rate", "guard_experience", "site_late_rate", "night", "early", "weekend",
           "hours_last_7d", "rest_hours"]
HISTORY = timedelta(days=180)
AHEAD = timedelta(hours=36)
GUARD_WINDOW = 10
SITE_WINDOW = timedelta(days=60)
PRIOR_WEIGHT = 2.0
#: Enough history to learn from: finished shifts, and late or missed ones among them.
MIN_ROWS, MIN_LATE, MIN_ON_TIME = 30, 5, 5

#: Each factor's name for "what it has learned matters", on the AI Insights page.
FACTOR_TITLES = {
    "guard_late_rate": "The guard's own recent lateness",
    "guard_experience": "How many shifts the guard has worked",
    "site_late_rate": "How often the site's shifts start late",
    "night": "Night shifts",
    "early": "Early starts",
    "weekend": "Weekends",
    "hours_last_7d": "Hours worked in the week before",
    "rest_hours": "Rest since the previous shift",
}

_WORDS = {
    "guard_late_rate": lambda e: (f"late or absent for {e['guard_late']} of their last {e['guard_n']} shifts"
                                  if e["guard_n"] else "no shifts on record yet"),
    "guard_experience": lambda e: f"{e['guard_total']} shifts on record",
    "site_late_rate": lambda e: f"shifts at this site started late {round(e['site_rate'] * 100)}% of the time lately",
    "night": lambda e: "a night shift",
    "early": lambda e: "an early start",
    "weekend": lambda e: "a weekend shift",
    "hours_last_7d": lambda e: f"{round(e['hours_7d'])} hours rostered in the 7 days before",
    "rest_hours": lambda e: (f"{round(e['rest'])} hours' rest since the previous shift"
                             if e["rest"] < 48 else "rested for two days or more"),
}


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


async def load_shifts(db: AsyncSession, now: datetime, ahead: timedelta = AHEAD) -> list[dict]:
    """The organisation's shifts from 180 days ago to 36 hours ahead, oldest first, with what happened."""
    rows = (await db.execute(text("""
        SELECT s.id, s.guard_user_id, s.site_id, s.scheduled_start, s.scheduled_end, s.actual_start,
               s.status, s.is_late, u.full_name AS guard_name, si.name AS site_name,
               EXISTS (SELECT 1 FROM violations v
                        WHERE v.shift_id = s.id AND v.violation_type = 'no_show') AS no_show
          FROM shifts s
          JOIN users u ON u.id = s.guard_user_id
          LEFT JOIN sites si ON si.id = s.site_id
         WHERE s.tenant_id = current_setting('app.current_tenant')::uuid
           AND s.scheduled_start >= :since AND s.scheduled_start < :until
           AND s.status IN ('scheduled', 'active', 'completed')
         ORDER BY s.scheduled_start, s.id
    """), {"since": now - HISTORY, "until": now + ahead})).mappings().all()
    return [dict(r) for r in rows]


def outcome_of(s: dict, now: datetime) -> bool | None:
    """True when the shift started late or was missed, False when on time, None while not yet known."""
    if s["no_show"]:
        return True
    if s["actual_start"] is not None:
        return bool(s["is_late"])
    # Its time has passed and nobody checked in: missed.
    if s["status"] == "scheduled" and s["scheduled_end"] < now:
        return True
    return None


class History:
    """What is known of every guard and site at a moment, built shift by shift, oldest first.

    values_for() gives the factors of a shift starting then for any guard, the
    one rostered or another (roster suggestions ask that); add() records a shift
    once it is behind the moment being looked at.
    """

    def __init__(self, base: float, tz: ZoneInfo):
        self.base, self.tz = base, tz
        self.by_guard: dict = defaultdict(lambda: deque(maxlen=GUARD_WINDOW))
        self.guard_total: dict = defaultdict(int)
        self.guard_last_end: dict = {}
        self.guard_hours: dict = defaultdict(deque)  # (start, hours) of recent shifts
        self.site_hist: dict = defaultdict(deque)    # (start, late) of finished shifts

    def values_for(self, g, site, start: datetime) -> tuple[dict, dict]:
        hist = self.by_guard[g]
        n, late = len(hist), sum(hist)
        hrs = self.guard_hours[g]
        while hrs and hrs[0][0] < start - timedelta(days=7):
            hrs.popleft()
        sh = self.site_hist[site]
        while sh and sh[0][0] < start - SITE_WINDOW:
            sh.popleft()
        site_n, site_late = len(sh), sum(x for _, x in sh)
        local = start.astimezone(self.tz)
        last_end = self.guard_last_end.get(g)
        rest = (start - last_end).total_seconds() / 3600 if last_end is not None else 48.0
        e = {"guard_n": n, "guard_late": late, "guard_total": self.guard_total[g],
             "site_rate": (site_late + PRIOR_WEIGHT * self.base) / (site_n + PRIOR_WEIGHT),
             "hours_7d": sum(h for _, h in hrs), "rest": max(0.0, min(48.0, rest))}
        values = {
            "guard_late_rate": (late + PRIOR_WEIGHT * self.base) / (n + PRIOR_WEIGHT),
            "guard_experience": math.log1p(self.guard_total[g]),
            "site_late_rate": e["site_rate"],
            "night": 1.0 if local.hour >= 19 or local.hour < 6 else 0.0,
            "early": 1.0 if 5 <= local.hour < 8 else 0.0,
            "weekend": 1.0 if local.weekday() >= 5 else 0.0,
            "hours_last_7d": e["hours_7d"],
            "rest_hours": e["rest"],
        }
        return values, e

    def add(self, s: dict, finished_outcome: bool | None) -> None:
        """What a shift adds to the history of the ones after it. Its outcome only once it is finished."""
        g, start = s["guard_user_id"], s["scheduled_start"]
        self.guard_hours[g].append((start, (s["scheduled_end"] - start).total_seconds() / 3600))
        self.guard_last_end[g] = max(self.guard_last_end.get(g, s["scheduled_end"]), s["scheduled_end"])
        if finished_outcome is not None:
            self.by_guard[g].append(1 if finished_outcome else 0)
            self.guard_total[g] += 1
            self.site_hist[s["site_id"]].append((start, 1 if finished_outcome else 0))


def base_rate(shifts: list[dict], now: datetime) -> float:
    """The organisation's own rate of late or missed shifts, never 0 or 1."""
    finished = [k for k in (outcome_of(s, now) for s in shifts if s["scheduled_start"] < now) if k is not None]
    return (sum(finished) + 1) / (len(finished) + 2)


def build_rows(shifts: list[dict], tz: ZoneInfo, now: datetime, *, on_each=None) -> list[dict]:
    """Each shift with its factors (from what came before it) and, when known, what happened.

    `on_each(shift, history)` is called for each shift before it is added, so a
    caller can ask the history about other guards at that moment.
    """
    history = History(base_rate(shifts, now), tz)
    out = []
    for s in shifts:
        k = outcome_of(s, now)
        finished = k is not None and s["scheduled_start"] < now
        values, e = history.values_for(s["guard_user_id"], s["site_id"], s["scheduled_start"])
        if on_each is not None:
            on_each(s, history)
        out.append({"shift": s, "values": values, "evidence": e, "outcome": k, "finished": finished})
        history.add(s, k if finished else None)
    return out


def reasons_of(parts: list[dict], evidence: dict) -> list[dict]:
    """The factors that raised the chance, in words, largest first (three at most)."""
    return [{"factor": p["factor"], "words": _WORDS[p["factor"]](evidence), "push": p["push"]}
            for p in parts if p["push"] > 0.05][:3]


async def train(db: AsyncSession, *, sensitivity: str, now: datetime, key: str = KEY) -> dict:
    tz = await _zone(db)
    rows = [r for r in build_rows(await load_shifts(db, now), tz, now) if r["finished"]]
    y = np.array([1 if r["outcome"] else 0 for r in rows], dtype=float)
    late, on_time = int(y.sum()), int(len(y) - y.sum())
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, key))
    if len(rows) < MIN_ROWS or late < MIN_LATE or on_time < MIN_ON_TIME:
        need = []
        if len(rows) < MIN_ROWS:
            need.append(f"{MIN_ROWS - len(rows)} more finished shifts")
        if late < MIN_LATE:
            need.append(f"{MIN_LATE - late} more late or missed shifts")
        if on_time < MIN_ON_TIME:
            need.append(f"{MIN_ON_TIME - on_time} more shifts that started on time")
        return await ai_store.save_model(
            db, key, status="LEARNING", rows_used=len(rows), positives=late, fitted=None,
            metrics={"rows": len(rows), "positives": late}, threshold=threshold, threshold_reason=why,
            note="Still learning: it needs " + ", ".join(need) + ".")
    X = np.array([[r["values"][f] for f in FACTORS] for r in rows], dtype=float)
    fitted = ai_learning.fit_logistic(FACTORS, X, y)
    metrics = ai_learning.evaluate(FACTORS, X, y)
    return await ai_store.save_model(
        db, key, status="READY", rows_used=len(rows), positives=late, fitted=fitted.as_json(),
        metrics=metrics, threshold=threshold, threshold_reason=why,
        note=f"Learned from {len(rows)} finished shifts, {late} of them late or missed.")


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    """Warn about the shifts in the next 36 hours whose chance reaches the bar; close the rest."""
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0, "outcomes": 0}
    counts["outcomes"] = await record_outcomes(db, now)
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return counts
    tz = await _zone(db)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    upcoming, raised = [], set()
    for r in build_rows(await load_shifts(db, now), tz, now):
        s = r["shift"]
        if s["status"] != "scheduled" or not (now <= s["scheduled_start"] < now + AHEAD):
            continue
        if allowed is not None and str(s["site_id"]) not in allowed:
            continue
        upcoming.append(s["id"])
        chance, parts = ai_learning.score(fitted, r["values"])
        if chance < threshold:
            continue
        local = s["scheduled_start"].astimezone(tz)
        what = await ai_store.upsert_open(
            db, KEY, subject_type="shift", subject=s["id"], site_id=s["site_id"], person_user_id=s["guard_user_id"],
            title=(f"{s['guard_name'] or 'A guard'} may be late or absent for the {local:%H:%M} shift on "
                   f"{local:%a %d %b}" + (f" at {s['site_name']}" if s["site_name"] else "")),
            detail="Consider lining up a standby or calling the guard ahead of the shift.",
            score=chance, threshold=threshold, reasons=reasons_of(parts, r["evidence"]),
            evidence={**r["evidence"], "shift_start": s["scheduled_start"].isoformat()},
            model_id=model["id"], valid_until=s["scheduled_start"])
        counts[what] += 1
        raised.add(s["id"])
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "shift", raised, upcoming, "The chance fell below the bar.")
    return counts


async def record_outcomes(db: AsyncSession, now: datetime) -> int:
    """Write what happened onto the warnings whose shift has started: the second loop's evidence."""
    rows = (await db.execute(text("""
        SELECT a.id, s.is_late, s.actual_start, s.status, s.scheduled_end,
               EXISTS (SELECT 1 FROM violations v WHERE v.shift_id = s.id AND v.violation_type = 'no_show') AS no_show
          FROM ai_suggestions a
          JOIN shifts s ON s.id = a.subject_id
         WHERE a.tenant_id = current_setting('app.current_tenant')::uuid AND a.feature_key = :k AND a.subject_type = 'shift' AND a.outcome IS NULL
           AND s.scheduled_start < :now
    """), {"k": KEY, "now": now})).mappings().all()
    done = 0
    for r in rows:
        happened = outcome_of({**r, "scheduled_start": None}, now)
        if happened is None:
            continue
        await db.execute(text("""
            UPDATE ai_suggestions SET outcome = :o, outcome_at = now(), updated_at = now() WHERE id = :id
        """), {"o": "HAPPENED" if happened else "DID_NOT_HAPPEN", "id": r["id"]})
        done += 1
    return done
