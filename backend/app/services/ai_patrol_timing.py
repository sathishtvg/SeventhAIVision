"""Best patrol times (feature `patrol_timing`): where problems gather in the day, and whether patrols follow them.

WHAT IT LEARNS FROM, PER SITE, over the last 90 days:
  problems  incidents, and doors refused, forced or tampered with, read through
            services/risk_patterns.events (the same rows the Risk & Advice page
            counts; that module is not changed);
  patrols   when virtual patrols and guard tours actually started.

Each is spread over the 24 hours of the day where the site is, smoothed a
little towards an even spread so a handful of records does not make a peak. It
finds the four-hour window holding the largest share of the site's problems and
compares it with the share of its patrols in that window.

IT SUGGESTS WHEN the gap is large enough: by sensitivity, 15, 25 or 35 points
between the problems' share and the patrols' (High, Medium, Low), with at least
10 problems behind it. Each statement is a count over the stated period with
the counts under it; it is not a forecast.

IT CHANGES NO SCHEDULE. A supervisor who agrees moves a patrol.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store, risk_patterns

KEY = "patrol_timing"
HISTORY = timedelta(days=90)
BAND = 4
SMOOTH = 2.0
MIN_PROBLEMS = 10
BASES = {"HIGH": 0.15, "MEDIUM": 0.25, "LOW": 0.35}
T = "current_setting('app.current_tenant')::uuid"


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(f"SELECT timezone FROM tenants WHERE id = {T}"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


def spread(times: list[datetime], tz: ZoneInfo) -> list[int]:
    hours = [0] * 24
    for t in times:
        hours[t.astimezone(tz).hour] += 1
    return hours


def shares(hours: list[int]) -> list[float]:
    """Each hour's share, smoothed towards an even spread."""
    total = sum(hours)
    return [(h + SMOOTH / 24) / (total + SMOOTH) for h in hours]


def busiest_window(problem_hours: list[int]) -> int:
    """The first hour of the four-hour window (wrapping past midnight) with the most problems."""
    s = shares(problem_hours)
    return max(range(24), key=lambda h: (sum(s[(h + i) % 24] for i in range(BAND)), -h))


def window_share(hours: list[int], first: int) -> float:
    total = sum(hours)
    return sum(hours[(first + i) % 24] for i in range(BAND)) / total if total else 0.0


async def _problems(db: AsyncSession, now: datetime) -> dict:
    out: dict = {}
    for source in ("INCIDENT", "ACCESS"):
        rows, _ = await risk_patterns.events(db, source, now - HISTORY, now, None)
        for r in rows:
            if r["site_id"] is not None:
                out.setdefault(str(r["site_id"]), []).append(r["at"])
    return out


async def _patrols(db: AsyncSession, now: datetime) -> dict:
    rows = (await db.execute(text(f"""
        SELECT v.site_id, v.started_at AS at FROM virtual_patrol_sessions v
         WHERE v.tenant_id = {T} AND v.started_at >= :since AND v.started_at <= :now
        UNION ALL
        SELECT r.site_id, p.started_at FROM patrol_sessions p JOIN patrol_routes r ON r.id = p.route_id
         WHERE p.tenant_id = {T} AND p.started_at >= :since AND p.started_at <= :now
    """), {"since": now - HISTORY, "now": now})).all()
    out: dict = {}
    for site, at in rows:
        if site is not None and at is not None:
            out.setdefault(str(site), []).append(at)
    return out


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    tz = await _zone(db)
    problems, patrols = await _problems(db, now), await _patrols(db, now)
    sites = {}
    for site, times in problems.items():
        if len(times) < MIN_PROBLEMS:
            continue
        ph, ah = spread(times, tz), spread(patrols.get(site, []), tz)
        first = busiest_window(ph)
        sites[site] = {"problem_hours": ph, "patrol_hours": ah, "window": first,
                       "problem_share": round(window_share(ph, first), 4),
                       "patrol_share": round(window_share(ah, first), 4)}
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY),
                                               bases=BASES, cap=0.6)
    if not sites:
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=sum(len(v) for v in problems.values()), positives=0, fitted=None,
            metrics={"sites": 0}, threshold=threshold, threshold_reason=why,
            note=f"Still learning: no site has {MIN_PROBLEMS} incidents or door events in the last 90 days yet.")
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=sum(len(problems[s]) for s in sites), positives=len(sites),
        fitted={"sites": sites}, metrics={"sites": len(sites)}, threshold=threshold, threshold_reason=why,
        note=f"Learned the hours of {sum(len(problems[s]) for s in sites)} problems at {len(sites)} sites.")


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0}
    if not model or model["status"] != "READY":
        return counts
    tz = await _zone(db)
    month = now.astimezone(tz).date().replace(day=1)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    names = {str(i): n for i, n in (await db.execute(text(f"SELECT id, name FROM sites WHERE tenant_id = {T}"))).all()}
    subjects, raised = [], set()
    for site, m in model["fitted"]["sites"].items():
        if allowed is not None and site not in allowed:
            continue
        sid = ai_store.subject_id(site, f"timing-{month:%Y-%m}")
        subjects.append(sid)
        gap = m["problem_share"] - m["patrol_share"]
        if gap < threshold:
            continue
        a, b = m["window"], (m["window"] + BAND) % 24
        n_prob, n_pat = sum(m["problem_hours"]), sum(m["patrol_hours"])
        what = await ai_store.upsert_open(
            db, KEY, subject_type="site_month", subject=sid, site_id=site, person_user_id=None,
            title=(f"At {names.get(site, 'a site')}, {round(m['problem_share'] * 100)}% of problems happen "
                   f"{a:02d}:00-{b:02d}:00, but {round(m['patrol_share'] * 100)}% of patrols do"),
            detail="Consider moving a patrol into that window. No schedule is changed.",
            score=gap, threshold=threshold,
            reasons=[{"factor": "problems", "words": (f"{round(m['problem_share'] * n_prob)} of {n_prob} incidents and "
                                                      f"door events in 90 days were in that window"), "push": gap},
                     {"factor": "patrols", "words": (f"{round(m['patrol_share'] * n_pat)} of {n_pat} patrols started "
                                                     f"in it" if n_pat else "no patrol started there in 90 days"),
                      "push": 0.0}],
            evidence={"window_start": a, "problems": n_prob, "patrols": n_pat,
                      "problem_share": m["problem_share"], "patrol_share": m["patrol_share"]},
            model_id=model["id"], valid_until=None)
        counts[what] += 1
        raised.add(sid)
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "site_month", raised, subjects, "The gap closed.")
    return counts
