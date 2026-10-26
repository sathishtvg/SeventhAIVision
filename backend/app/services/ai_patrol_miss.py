"""Missed-patrol warnings (feature `patrol_miss_risk`): patrols in the next 24 hours likely to be missed or to fail.

WHAT IT LEARNS FROM. Two kinds of patrol the platform already schedules:
  virtual patrols (virtual_patrol_sessions): MISSED against COMPLETED or PARTIALLY_COMPLETED;
  guard tours (tour_occurrences): missed or incomplete against completed or late.
The last 120 days of both, oldest first.

THE FACTORS, each from what was known before the patrol:
  schedule_miss_rate   how often this schedule's last 10 patrols were missed or failed
  person_miss_rate     the same for the officer or guard it is assigned to
  missed_last          the schedule's previous patrol was missed
  night                due from 19:00 to 05:59, where the site is
  weekend              Saturday or Sunday
  is_tour              a guard's tour rather than a virtual patrol

IT WARNS; IT SCHEDULES NOTHING. The patrol stays as it is; a supervisor decides
whether to remind, reassign or cover it.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store

KEY = "patrol_miss_risk"
FACTORS = ["schedule_miss_rate", "person_miss_rate", "missed_last", "night", "weekend", "is_tour"]
FACTOR_TITLES = {
    "schedule_miss_rate": "How often the schedule's patrols were missed",
    "person_miss_rate": "How often the assigned person's patrols were missed",
    "missed_last": "The previous patrol of the schedule was missed",
    "night": "Night-time patrols",
    "weekend": "Weekends",
    "is_tour": "Guard tours (rather than virtual patrols)",
}
HISTORY = timedelta(days=120)
AHEAD = timedelta(hours=24)
WINDOW = 10
PRIOR_WEIGHT = 2.0
MIN_ROWS, MIN_MISSED, MIN_DONE = 30, 5, 5
T = "current_setting('app.current_tenant')::uuid"
FAILED = {"virtual": ("MISSED",), "tour": ("missed", "incomplete")}
DONE = {"virtual": ("COMPLETED", "PARTIALLY_COMPLETED"), "tour": ("completed", "late")}
UPCOMING = {"virtual": "SCHEDULED", "tour": "pending"}

_WORDS = {
    "schedule_miss_rate": lambda e: (f"{e['sched_missed']} of this schedule's last {e['sched_n']} patrols were missed"
                                     if e["sched_n"] else "no patrols of this schedule on record yet"),
    "person_miss_rate": lambda e: (f"{e['person_missed']} of the assigned person's last {e['person_n']} patrols were missed"
                                   if e["person_n"] else "nobody assigned, or no patrols on record"),
    "missed_last": lambda e: "the schedule's previous patrol was missed",
    "night": lambda e: "due at night",
    "weekend": lambda e: "due at the weekend",
    "is_tour": lambda e: "a guard's tour",
}


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(f"SELECT timezone FROM tenants WHERE id = {T}"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


async def load_patrols(db: AsyncSession, now: datetime) -> list[dict]:
    rows = (await db.execute(text(f"""
        SELECT 'virtual' AS kind, v.id, v.schedule_id, v.officer_user_id AS person_id, v.status, v.site_id,
               v.scheduled_for AS due, v.schedule_name AS name, si.name AS site_name
          FROM virtual_patrol_sessions v LEFT JOIN sites si ON si.id = v.site_id
         WHERE v.tenant_id = {T} AND v.scheduled_for >= :since AND v.scheduled_for < :until
        UNION ALL
        SELECT 'tour', o.id, o.schedule_id, t.assigned_guard_user_id, o.status, r.site_id,
               o.scheduled_at, t.name, si.name
          FROM tour_occurrences o JOIN tour_schedules t ON t.id = o.schedule_id
          LEFT JOIN patrol_routes r ON r.id = t.route_id LEFT JOIN sites si ON si.id = r.site_id
         WHERE o.tenant_id = {T} AND o.scheduled_at >= :since AND o.scheduled_at < :until
         ORDER BY due, id
    """), {"since": now - HISTORY, "until": now + AHEAD})).mappings().all()
    return [dict(r) for r in rows]


def outcome_of(p: dict) -> bool | None:
    if p["status"] in FAILED[p["kind"]]:
        return True
    if p["status"] in DONE[p["kind"]]:
        return False
    return None


def build_rows(patrols: list[dict], tz: ZoneInfo, now: datetime) -> list[dict]:
    known = [outcome_of(p) for p in patrols if p["due"] < now]
    finished = [k for k in known if k is not None]
    base = (sum(finished) + 1) / (len(finished) + 2)
    by_sched: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    by_person: dict = defaultdict(lambda: deque(maxlen=WINDOW))
    out = []
    for p in patrols:
        sh, ph = by_sched[(p["kind"], p["schedule_id"])], by_person[p["person_id"]] if p["person_id"] else deque()
        local = p["due"].astimezone(tz)
        e = {"sched_n": len(sh), "sched_missed": sum(sh), "person_n": len(ph), "person_missed": sum(ph)}
        values = {
            "schedule_miss_rate": (sum(sh) + PRIOR_WEIGHT * base) / (len(sh) + PRIOR_WEIGHT),
            "person_miss_rate": (sum(ph) + PRIOR_WEIGHT * base) / (len(ph) + PRIOR_WEIGHT),
            "missed_last": 1.0 if sh and sh[-1] == 1 else 0.0,
            "night": 1.0 if local.hour >= 19 or local.hour < 6 else 0.0,
            "weekend": 1.0 if local.weekday() >= 5 else 0.0,
            "is_tour": 1.0 if p["kind"] == "tour" else 0.0,
        }
        k = outcome_of(p)
        done = k is not None and p["due"] < now
        out.append({"patrol": p, "values": values, "evidence": e, "outcome": k, "finished": done})
        if done:
            sh.append(1 if k else 0)
            if p["person_id"]:
                by_person[p["person_id"]].append(1 if k else 0)
    return out


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    tz = await _zone(db)
    rows = [r for r in build_rows(await load_patrols(db, now), tz, now) if r["finished"]]
    y = np.array([1 if r["outcome"] else 0 for r in rows], dtype=float)
    missed, done = int(y.sum()), int(len(y) - y.sum())
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY))
    if len(rows) < MIN_ROWS or missed < MIN_MISSED or done < MIN_DONE:
        need = []
        if len(rows) < MIN_ROWS:
            need.append(f"{MIN_ROWS - len(rows)} more finished patrols")
        if missed < MIN_MISSED:
            need.append(f"{MIN_MISSED - missed} more missed or failed patrols")
        if done < MIN_DONE:
            need.append(f"{MIN_DONE - done} more completed patrols")
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(rows), positives=missed, fitted=None,
            metrics={"rows": len(rows), "positives": missed}, threshold=threshold, threshold_reason=why,
            note="Still learning: it needs " + ", ".join(need) + ".")
    X = np.array([[r["values"][f] for f in FACTORS] for r in rows], dtype=float)
    fitted = ai_learning.fit_logistic(FACTORS, X, y)
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(rows), positives=missed, fitted=fitted.as_json(),
        metrics=ai_learning.evaluate(FACTORS, X, y), threshold=threshold, threshold_reason=why,
        note=f"Learned from {len(rows)} finished patrols, {missed} of them missed or failed.")


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0, "outcomes": 0}
    patrols = await load_patrols(db, now)
    counts["outcomes"] = await record_outcomes(db, patrols)
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return counts
    tz = await _zone(db)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    upcoming, raised = [], set()
    for r in build_rows(patrols, tz, now):
        p = r["patrol"]
        if p["status"] != UPCOMING[p["kind"]] or not (now <= p["due"] < now + AHEAD):
            continue
        if allowed is not None and str(p["site_id"]) not in allowed:
            continue
        upcoming.append(p["id"])
        chance, parts = ai_learning.score(fitted, r["values"])
        if chance < threshold:
            continue
        local = p["due"].astimezone(tz)
        kind = "guard tour" if p["kind"] == "tour" else "virtual patrol"
        what = await ai_store.upsert_open(
            db, KEY, subject_type=p["kind"] + "_patrol", subject=p["id"], site_id=p["site_id"],
            person_user_id=p["person_id"],
            title=(f"The {p['name'] or kind} ({kind}) due at {local:%H:%M} on {local:%a %d %b} may be missed"
                   + (f" at {p['site_name']}" if p["site_name"] else "")),
            detail="Consider a reminder, or lining up someone to cover it. The patrol itself is not changed.",
            score=chance, threshold=threshold,
            reasons=[{"factor": x["factor"], "words": _WORDS[x["factor"]](r["evidence"]), "push": x["push"]}
                     for x in parts if x["push"] > 0.05][:3],
            evidence={**r["evidence"], "kind": p["kind"], "due": p["due"].isoformat()},
            model_id=model["id"], valid_until=p["due"] + timedelta(hours=2))
        counts[what] += 1
        raised.add(p["id"])
    for kind in ("virtual", "tour"):
        counts["expired"] += await ai_store.expire_open_except(
            db, KEY, kind + "_patrol", raised, upcoming, "The chance fell below the bar.")
    return counts


async def record_outcomes(db: AsyncSession, patrols: list[dict]) -> int:
    final = {p["id"]: outcome_of(p) for p in patrols if outcome_of(p) is not None}
    if not final:
        return 0
    rows = (await db.execute(text(f"""
        SELECT id, subject_id FROM ai_suggestions
         WHERE tenant_id = {T} AND feature_key = :k AND outcome IS NULL AND subject_id = ANY(:ids)
    """), {"k": KEY, "ids": list(final)})).mappings().all()
    for r in rows:
        await db.execute(text("UPDATE ai_suggestions SET outcome = :o, outcome_at = now(), updated_at = now() "
                              "WHERE id = :id"),
                         {"o": "HAPPENED" if final[r["subject_id"]] else "DID_NOT_HAPPEN", "id": r["id"]})
    return len(rows)
