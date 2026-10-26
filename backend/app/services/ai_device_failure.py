"""Device failure warnings (feature `device_failure`): devices likely to go down in the next 7 days.

WHAT IT LEARNS FROM. The state changes the platform already keeps for every
device (device_health_changes: OK, DEGRADED, DOWN, NOT_KNOWN, OFF) and, for
cameras, their stream events (camera_health_events). For each device and each
day of the last 120, it asks: given the device's history up to that morning, did
it go DOWN in the 7 days that followed?

THE FACTORS, from the history before that day:
  downs_7d, downs_30d     times it went DOWN in the last 7 and 30 days
  degraded_7d             times it went DEGRADED in the last 7 days
  days_since_down         days since it last went DOWN (60 at most)
  degraded_now            it is DEGRADED as the day starts
  drops_7d                for a camera, stream disconnections in the last 7 days
A device already DOWN or switched OFF is not predicted: it is not about to fail.

IT SUGGESTS A CHECK; IT RAISES NO WORK ORDER. The maintenance module is not
touched: a person who agrees raises the order there.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store, device_health

KEY = "device_failure"
FACTORS = ["downs_7d", "downs_30d", "degraded_7d", "days_since_down", "degraded_now", "drops_7d"]
FACTOR_TITLES = {
    "downs_7d": "Going down in the last week",
    "downs_30d": "Going down in the last month",
    "degraded_7d": "Running degraded in the last week",
    "days_since_down": "Days since it last went down",
    "degraded_now": "Degraded right now",
    "drops_7d": "Stream drops in the last week",
}
HISTORY = timedelta(days=120)
AHEAD = timedelta(days=7)
MIN_ROWS, MIN_DOWN, MIN_UP = 60, 5, 5
T = "current_setting('app.current_tenant')::uuid"

_WORDS = {
    "downs_7d": lambda e: f"went down {e['downs_7d']} times in the last 7 days",
    "downs_30d": lambda e: f"went down {e['downs_30d']} times in the last 30 days",
    "degraded_7d": lambda e: f"ran degraded {e['degraded_7d']} times in the last 7 days",
    "days_since_down": lambda e: (f"last went down {e['days_since_down']} days ago" if e["days_since_down"] < 60
                                  else "has not gone down for two months or more"),
    "degraded_now": lambda e: "is degraded now",
    "drops_7d": lambda e: f"its stream dropped {e['drops_7d']} times in the last 7 days",
}


async def load_history(db: AsyncSession, now: datetime) -> tuple[dict, dict]:
    """(device -> [(time, state)] oldest first, camera -> [time of each stream drop])."""
    changes = (await db.execute(text(f"""
        SELECT device_kind, device_id, state, observed_at FROM device_health_changes
         WHERE tenant_id = {T} AND observed_at >= :since AND observed_at <= :now
         ORDER BY device_kind, device_id, observed_at
    """), {"since": now - HISTORY - timedelta(days=30), "now": now})).mappings().all()
    states: dict = defaultdict(list)
    for c in changes:
        states[(c["device_kind"], str(c["device_id"]))].append((c["observed_at"], c["state"]))
    drops = (await db.execute(text(f"""
        SELECT camera_id, occurred_at FROM camera_health_events
         WHERE tenant_id = {T} AND event_type = 'stream_disconnected'
           AND occurred_at >= :since AND occurred_at <= :now ORDER BY occurred_at
    """), {"since": now - HISTORY - timedelta(days=7), "now": now})).mappings().all()
    by_cam: dict = defaultdict(list)
    for d in drops:
        by_cam[str(d["camera_id"])].append(d["occurred_at"])
    return states, by_cam


def features_at(changes: list[tuple], drops: list, at: datetime) -> tuple[dict, dict, str | None]:
    """The factors for one device at one moment, the evidence in numbers, and its state then."""
    times = [t for t, _ in changes]
    i = bisect.bisect_right(times, at)
    before = changes[:i]
    state = before[-1][1] if before else None
    downs = [t for t, s in before if s == "DOWN"]
    e = {"downs_7d": sum(1 for t in downs if t >= at - timedelta(days=7)),
         "downs_30d": sum(1 for t in downs if t >= at - timedelta(days=30)),
         "degraded_7d": sum(1 for t, s in before if s == "DEGRADED" and t >= at - timedelta(days=7)),
         "days_since_down": min(60, (at - downs[-1]).days) if downs else 60,
         "degraded_now": 1 if state == "DEGRADED" else 0,
         "drops_7d": sum(1 for t in drops if at - timedelta(days=7) <= t < at)}
    return {k: float(v) for k, v in e.items()}, e, state


def went_down(changes: list[tuple], start: datetime, end: datetime) -> bool:
    return any(start <= t < end and s == "DOWN" for t, s in changes)


def build_rows(states: dict, drops: dict, now: datetime) -> list[dict]:
    """One row per device per day of the history whose next 7 days are over."""
    rows = []
    first_day = (now - HISTORY).replace(hour=0, minute=0, second=0, microsecond=0)
    for (kind, dev), changes in states.items():
        cam_drops = drops.get(dev, []) if kind == "CAMERA" else []
        day = max(first_day, changes[0][0].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        while day + AHEAD <= now:
            values, e, state = features_at(changes, cam_drops, day)
            if state not in ("DOWN", "OFF"):
                rows.append({"values": values, "evidence": e, "outcome": went_down(changes, day, day + AHEAD)})
            day += timedelta(days=1)
    return rows


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    states, drops = await load_history(db, now)
    rows = build_rows(states, drops, now)
    y = np.array([1 if r["outcome"] else 0 for r in rows], dtype=float)
    down, up = int(y.sum()), int(len(y) - y.sum())
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY))
    if len(rows) < MIN_ROWS or down < MIN_DOWN or up < MIN_UP:
        need = []
        if len(rows) < MIN_ROWS:
            need.append(f"{MIN_ROWS - len(rows)} more device-days of history")
        if down < MIN_DOWN:
            need.append(f"{MIN_DOWN - down} more weeks in which a device went down")
        if up < MIN_UP:
            need.append(f"{MIN_UP - up} more weeks in which a device stayed up")
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(rows), positives=down, fitted=None,
            metrics={"rows": len(rows), "positives": down}, threshold=threshold, threshold_reason=why,
            note="Still learning: it needs " + ", ".join(need) + ".")
    X = np.array([[r["values"][f] for f in FACTORS] for r in rows], dtype=float)
    fitted = ai_learning.fit_logistic(FACTORS, X, y)
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(rows), positives=down, fitted=fitted.as_json(),
        metrics=ai_learning.evaluate(FACTORS, X, y), threshold=threshold, threshold_reason=why,
        note=(f"Learned from {len(rows)} device-days across {len(states)} devices; "
              f"in {down} of them the device went down within a week."))


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0, "outcomes": 0}
    states, drops = await load_history(db, now)
    counts["outcomes"] = await record_outcomes(db, states, now)
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return counts
    allowed = None if site_ids is None else [str(s) for s in site_ids]
    week = now.date() - timedelta(days=now.date().weekday())
    subjects, raised = [], set()
    for dev in await device_health.readings(db, now, allowed=allowed):
        if dev["state"] in ("DOWN", "OFF"):
            continue
        key = (dev["kind"], str(dev["device_id"]))
        values, e, _ = features_at(states.get(key, []), drops.get(key[1], []) if dev["kind"] == "CAMERA" else [], now)
        sid = ai_store.subject_id(key[0], key[1], f"week-{week.isoformat()}")
        subjects.append(sid)
        chance, parts = ai_learning.score(fitted, values)
        if chance < threshold:
            continue
        what = await ai_store.upsert_open(
            db, KEY, subject_type="device_week", subject=sid, site_id=dev["site_id"], person_user_id=None,
            title=f"{dev['kind_label']} {dev['name'] or ''} may go down in the next 7 days".replace("  ", " "),
            detail="Consider a maintenance check. Nothing is raised by itself: a work order is yours to create in Maintenance.",
            score=chance, threshold=threshold,
            reasons=[{"factor": p["factor"], "words": _WORDS[p["factor"]](e), "push": p["push"]}
                     for p in parts if p["push"] > 0.05][:3],
            evidence={**e, "kind": dev["kind"], "device_id": key[1], "at": now.isoformat()},
            model_id=model["id"], valid_until=now + AHEAD)
        counts[what] += 1
        raised.add(sid)
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "device_week", raised, subjects, "The chance fell below the bar.")
    return counts


async def record_outcomes(db: AsyncSession, states: dict, now: datetime) -> int:
    """Did the device go down in the 7 days after the warning? Written once the 7 days are known."""
    rows = (await db.execute(text(f"""
        SELECT id, created_at, evidence FROM ai_suggestions
         WHERE tenant_id = {T} AND feature_key = :k AND subject_type = 'device_week' AND outcome IS NULL
    """), {"k": KEY})).mappings().all()
    done = 0
    for r in rows:
        ev = r["evidence"] or {}
        changes = states.get((ev.get("kind"), ev.get("device_id")), [])
        start = datetime.fromisoformat(ev["at"]) if ev.get("at") else r["created_at"]
        if went_down(changes, start, start + AHEAD):
            outcome = "HAPPENED"
        elif now >= start + AHEAD:
            outcome = "DID_NOT_HAPPEN"
        else:
            continue
        await db.execute(text("UPDATE ai_suggestions SET outcome = :o, outcome_at = now(), updated_at = now() "
                              "WHERE id = :id"), {"o": outcome, "id": r["id"]})
        done += 1
    return done
