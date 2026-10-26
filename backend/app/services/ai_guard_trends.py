"""Guard work trends (feature `guard_trends`): a guard whose own work has clearly slipped, for a supervisor to talk to.

EACH GUARD IS COMPARED WITH THEMSELVES, NEVER RANKED AGAINST OTHERS. For every
guard, the last 4 weeks against the 8 weeks before:
  punctuality          the share of finished shifts started on time
  patrol completion    the share of checkpoints scanned on the guard's tours

IT SPEAKS ONLY OF A CLEAR DROP: at least 15 points, with at least 8 of each kind
in both periods, and unlikely to be chance by a two-proportion test (z of at
least 1.64, 1.96 or 2.58 by sensitivity: High, Medium, Low). A small or noisy
change is not raised.

IT IS NOT A SCORE AND IT ACTS ON NOTHING. No rating is kept against the guard,
nothing about their roster, pay or record changes, and the guard is not told by
the platform. It is a prompt for a supervisor's conversation.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_late_risk, ai_learning, ai_store

KEY = "guard_trends"
RECENT, BEFORE = timedelta(days=28), timedelta(days=56)
MIN_EACH, MIN_DROP = 8, 0.15
#: The test statistic a drop must reach, by sensitivity, carried on the bar's scale (divided by 10).
BASES = {"HIGH": 0.164, "MEDIUM": 0.196, "LOW": 0.258}
T = "current_setting('app.current_tenant')::uuid"


def z_drop(before_ok: int, before_n: int, recent_ok: int, recent_n: int) -> float:
    """How far the recent share fell below the earlier one, in standard errors (0 when it did not fall)."""
    if not before_n or not recent_n:
        return 0.0
    p1, p2 = before_ok / before_n, recent_ok / recent_n
    pooled = (before_ok + recent_ok) / (before_n + recent_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / before_n + 1 / recent_n))
    if se == 0:
        return 0.0
    return max(0.0, (p1 - p2) / se)


async def _measures(db: AsyncSession, now: datetime) -> dict:
    """guard -> measure -> {"recent": [ok, n], "before": [ok, n]}."""
    out: dict = defaultdict(lambda: defaultdict(lambda: {"recent": [0, 0], "before": [0, 0]}))
    for s in await ai_late_risk.load_shifts(db, now, ahead=timedelta(0)):
        k = ai_late_risk.outcome_of(s, now)
        if k is None or s["scheduled_start"] < now - RECENT - BEFORE:
            continue
        period = "recent" if s["scheduled_start"] >= now - RECENT else "before"
        cell = out[s["guard_user_id"]]["punctuality"][period]
        cell[0] += 0 if k else 1
        cell[1] += 1
    tours = (await db.execute(text(f"""
        SELECT guard_user_id, started_at, scanned_checkpoints, total_checkpoints FROM patrol_sessions
         WHERE tenant_id = {T} AND started_at >= :since AND started_at <= :now AND total_checkpoints > 0
           AND status = 'completed'
    """), {"since": now - RECENT - BEFORE, "now": now})).mappings().all()
    for t in tours:
        period = "recent" if t["started_at"] >= now - RECENT else "before"
        cell = out[t["guard_user_id"]]["patrol completion"][period]
        cell[0] += int(t["scanned_checkpoints"] or 0)
        cell[1] += int(t["total_checkpoints"])
    return out


async def _names(db: AsyncSession) -> dict:
    return {r[0]: r[1] for r in (await db.execute(text(f"SELECT id, full_name FROM users WHERE tenant_id = {T}"))).all()}


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    measures = await _measures(db, now)
    enough = sum(1 for m in measures.values() for c in m.values()
                 if c["recent"][1] >= MIN_EACH and c["before"][1] >= MIN_EACH)
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY),
                                               bases=BASES, cap=0.4)
    if not enough:
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(measures), positives=0, fitted=None, metrics={"guards": 0},
            threshold=threshold, threshold_reason=why,
            note=f"Still learning: no guard has {MIN_EACH} shifts or checkpoints in both of the last 4 weeks "
                 f"and the 8 weeks before yet.")
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(measures), positives=enough,
        # Only how much could be compared: nothing per guard is kept in the model.
        fitted={"comparable": enough},
        metrics={"guards": len(measures), "comparable": enough}, threshold=threshold, threshold_reason=why,
        note=f"Each guard's last 4 weeks set against their 8 weeks before: {enough} comparable measures.")


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0}
    if not model or model["status"] != "READY":
        return counts
    measures, names = await _measures(db, now), await _names(db)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    site_of = {str(g): s for g, s in (await db.execute(text(f"""
        SELECT DISTINCT ON (guard_user_id) guard_user_id, site_id FROM shifts
         WHERE tenant_id = {T} AND scheduled_start <= :now ORDER BY guard_user_id, scheduled_start DESC
    """), {"now": now})).all()}
    month = now.date().replace(day=1)
    subjects, raised = [], set()
    for g, m in measures.items():
        site = site_of.get(str(g))
        if allowed is not None and str(site) not in allowed:
            continue
        sid = ai_store.subject_id(g, f"trend-{month:%Y-%m}")
        subjects.append(sid)
        drops = []
        for measure, c in m.items():
            (bo, bn), (ro, rn) = c["before"], c["recent"]
            if bn < MIN_EACH or rn < MIN_EACH:
                continue
            fell = bo / bn - ro / rn
            z = z_drop(bo, bn, ro, rn)
            if fell >= MIN_DROP and z / 10 >= threshold:
                drops.append((measure, bo / bn, ro / rn, z, rn))
        if not drops:
            continue
        drops.sort(key=lambda d: d[3], reverse=True)
        top = drops[0]
        what = await ai_store.upsert_open(
            db, KEY, subject_type="guard_trend", subject=sid, site_id=site, person_user_id=g,
            title=(f"{names.get(g) or 'A guard'}'s {top[0]} fell from {round(top[1] * 100)}% to "
                   f"{round(top[2] * 100)}% in the last 4 weeks"),
            detail="A prompt for a conversation. Nothing about the guard is changed, scored or kept against them.",
            score=min(9.9, top[3] / 10), threshold=threshold,
            reasons=[{"factor": d[0], "words": (f"{d[0]}: {round(d[1] * 100)}% in the 8 weeks before, "
                                                f"{round(d[2] * 100)}% in the last 4 ({d[4]} counted)"),
                      "push": round(d[3], 2)} for d in drops],
            evidence={d[0]: {"before": round(d[1], 4), "recent": round(d[2], 4), "z": round(d[3], 2)} for d in drops},
            model_id=model["id"], valid_until=None)
        counts[what] += 1
        raised.add(sid)
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "guard_trend", raised, subjects, "The drop is no longer clear.")
    return counts
