"""Payroll check (feature `payroll_check`): pay lines far from a guard's own normal, before payroll is finalised.

WHAT IT LEARNS FROM. Each guard's own payslips in finalised payroll runs (the
last 12): their usual gross pay, regular hours, overtime hours and pay per hour
worked. "Usual" is the median, and "far" is measured against how much that
guard's pay normally varies (the median absolute deviation), so one guard's
normal swing is not another's alarm.

WHEN IT SPEAKS. For payslips in DRAFT runs only, when a figure is far enough
from the guard's own normal: 2.5, 3.5 or 4.5 deviations by sensitivity (High,
Medium, Low), with at least 4 finalised payslips of that guard behind it.

IT CHANGES NO PAYROLL. Nothing in the run or the payslip is changed, and the
run is finalised (or not) by a person, as always. Finalising payroll is one of
the actions the assistant is never offered.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store

KEY = "payroll_check"
HISTORY_SLIPS = 12
MIN_HISTORY = 4
#: The deviations a figure must reach, by sensitivity, carried on the bar's scale (divided by 10).
BASES = {"HIGH": 0.25, "MEDIUM": 0.35, "LOW": 0.45}
METRICS = {"gross_pay": "gross pay", "regular_hours": "regular hours", "overtime_hours": "overtime hours",
           "pay_per_hour": "pay per hour worked"}
MONEY = {"gross_pay", "pay_per_hour"}
T = "current_setting('app.current_tenant')::uuid"


def figures(slip: dict) -> dict:
    hours = float(slip["regular_hours"] or 0) + float(slip["overtime_hours"] or 0)
    gross = float(slip["gross_pay"] or 0)
    return {"gross_pay": gross, "regular_hours": float(slip["regular_hours"] or 0),
            "overtime_hours": float(slip["overtime_hours"] or 0),
            "pay_per_hour": gross / hours if hours > 0 else None}


def deviations(value: float, history: list[float]) -> float:
    """How far a value is from the median of the history, in robust standard deviations."""
    med = statistics.median(history)
    mad = statistics.median([abs(h - med) for h in history]) * 1.4826
    # A guard paid the same every month has no spread: a floor of 2% of the usual (or 1) keeps a cent from
    # becoming an alarm.
    spread = max(mad, abs(med) * 0.02, 1.0)
    return abs(value - med) / spread


async def load_slips(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(text(f"""
        SELECT p.id, p.guard_user_id, p.payroll_run_id, p.regular_hours, p.overtime_hours, p.gross_pay,
               r.status AS run_status, r.period_start, r.period_end, u.full_name
          FROM payslips p
          JOIN payroll_runs r ON r.id = p.payroll_run_id
          LEFT JOIN users u ON u.id = p.guard_user_id
         WHERE p.tenant_id = {T}
         ORDER BY r.period_start, p.created_at
    """))).mappings().all()
    return [dict(r) for r in rows]


def histories(slips: list[dict]) -> dict:
    out: dict = defaultdict(list)
    for s in slips:
        if s["run_status"] == "finalized":
            out[s["guard_user_id"]].append(figures(s))
    return {g: h[-HISTORY_SLIPS:] for g, h in out.items()}


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    hist = histories(await load_slips(db))
    enough = sum(1 for h in hist.values() if len(h) >= MIN_HISTORY)
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY),
                                               bases=BASES, cap=0.8)
    if not enough:
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=sum(len(h) for h in hist.values()), positives=0, fitted=None,
            metrics={"guards": 0}, threshold=threshold, threshold_reason=why,
            note=f"Still learning: no guard has {MIN_HISTORY} payslips in finalised payroll runs yet.")
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=sum(len(h) for h in hist.values()), positives=enough,
        fitted={"guards_with_history": enough}, metrics={"guards": enough}, threshold=threshold,
        threshold_reason=why, note=f"Knows the usual pay of {enough} guards from their finalised payslips.")


def _say(metric: str, value: float) -> str:
    return f"S${value:,.2f}" if metric in MONEY else f"{value:,.1f} h"


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0}
    slips = await load_slips(db)
    # A payslip whose run was finalised, or which is gone, is no longer for checking.
    drafts = [s for s in slips if s["run_status"] == "draft"]
    counts["expired"] += await _expire_finalised(db, {s["id"] for s in drafts})
    if not model or model["status"] != "READY":
        return counts
    hist = histories(slips)
    raised = set()
    for s in drafts:
        h = hist.get(s["guard_user_id"], [])
        if len(h) < MIN_HISTORY:
            continue
        now_figs = figures(s)
        far = []
        for metric, words in METRICS.items():
            past = [x[metric] for x in h if x[metric] is not None]
            if now_figs[metric] is None or len(past) < MIN_HISTORY:
                continue
            d = deviations(now_figs[metric], past)
            if d / 10 >= threshold:
                far.append((d, metric, words, now_figs[metric], statistics.median(past)))
        if not far:
            continue
        far.sort(reverse=True)
        d, metric, words, value, usual = far[0]
        period = f"{s['period_start']:%d %b} - {s['period_end']:%d %b %Y}"
        what = await ai_store.upsert_open(
            db, KEY, subject_type="payslip", subject=s["id"], site_id=None, person_user_id=s["guard_user_id"],
            title=(f"Check {s['full_name'] or 'a guard'}'s pay for {period}: {words} {_say(metric, value)} "
                   f"against a usual {_say(metric, usual)}"),
            detail="Before finalising. Nothing in the payroll run or the payslip is changed.",
            score=min(9.9, d / 10), threshold=threshold,
            reasons=[{"factor": m, "push": round(x, 2),
                      "words": f"{w}: {_say(m, v)} this period, usually {_say(m, u)} ({x:.1f} deviations)"}
                     for x, m, w, v, u in far],
            evidence={m: {"value": round(v, 2), "usual": round(u, 2), "deviations": round(x, 2)} for x, m, _, v, u in far},
            model_id=model["id"], valid_until=None)
        counts[what] += 1
        raised.add(s["id"])
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "payslip", raised, [s["id"] for s in drafts], "The figures are back within the usual range.")
    return counts


async def _expire_finalised(db: AsyncSession, draft_ids: set) -> int:
    r = await db.execute(text(f"""
        UPDATE ai_suggestions SET status = 'EXPIRED', updated_at = now(),
               decision_note = 'The payroll run was finalised, or the payslip is gone.'
         WHERE tenant_id = {T} AND feature_key = :k AND status = 'OPEN'
           AND NOT (subject_id = ANY(CAST(:ids AS uuid[])))
    """), {"k": KEY, "ids": [str(i) for i in draft_ids]})
    return r.rowcount or 0
