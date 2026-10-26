"""False-alarm learning (feature `false_alarm`): open alerts likely to be false, and cameras worth a review.

WHAT IT LEARNS FROM. The verdicts operators already give: an alert marked
`false_positive` against one `resolved` or `acknowledged` (real). `dismissed` is
left out, because it says neither.

THE FACTORS, each from what was known before the alert:
  camera_false_rate   how often this camera's alerts were false in the 60 days before
  module_false_rate   how often this kind of alert (intrusion, face, PPE...) was false here
  night               raised from 19:00 to 05:59, where the site is
  severity            info 0 .. critical 4
  camera_burst        how many alerts the camera raised in the hour before (more is often noise)
  repeat              the same kind of alert on the same camera in the 10 minutes before

IT NEVER CLOSES, HIDES OR CHANGES AN ALERT. A warning says "likely a false alarm,
look at the picture before closing it"; the alert stays open in the alert queue
until an operator decides, as it always has. Its bar starts high (75%), because
a real intrusion called false is the worst mistake this feature can make.

CAMERAS WORTH A REVIEW. Once a month's verdicts show a camera raising mostly
false alarms (at least 10 decided, 70% or more false), it is put forward for a
person to review its zones or sensitivity. Nothing is changed on the camera.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store

KEY = "false_alarm"
FACTORS = ["camera_false_rate", "module_false_rate", "night", "severity", "camera_burst", "repeat"]
FACTOR_TITLES = {
    "camera_false_rate": "How often the camera's alerts were false",
    "module_false_rate": "How often that kind of alert was false",
    "night": "Night-time alerts",
    "severity": "The alert's severity",
    "camera_burst": "Many alerts from the camera in the hour before",
    "repeat": "The same alert again within 10 minutes",
}
HISTORY = timedelta(days=90)
CAMERA_WINDOW = timedelta(days=60)
OPEN_WINDOW = timedelta(hours=24)
PRIOR_WEIGHT = 3.0
MIN_ROWS, MIN_FALSE, MIN_REAL = 40, 8, 8
#: A chance of being false the warning must reach, by sensitivity: high, because calling a real alert false is
#: the costly mistake.
BASES = {"HIGH": 0.60, "MEDIUM": 0.75, "LOW": 0.90}
REVIEW_AT, REVIEW_MIN = 0.70, 10
SEVERITY = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
FALSE, REAL = ("false_positive",), ("resolved", "acknowledged")
T = "current_setting('app.current_tenant')::uuid"

_WORDS = {
    "camera_false_rate": lambda e: (f"{e['cam_false']} of this camera's last {e['cam_n']} decided alerts were false"
                                    if e["cam_n"] else "no decided alerts from this camera yet"),
    "module_false_rate": lambda e: f"{round(e['mod_rate'] * 100)}% of {e['module']} alerts here were false",
    "night": lambda e: "raised at night",
    "severity": lambda e: f"severity {e['severity']}",
    "camera_burst": lambda e: f"{e['burst']} alerts from this camera in the hour before",
    "repeat": lambda e: "the same alert again within 10 minutes",
}


async def _zone(db: AsyncSession) -> ZoneInfo:
    tz = (await db.execute(text(f"SELECT timezone FROM tenants WHERE id = {T}"))).scalar()
    try:
        return ZoneInfo(tz or "Asia/Singapore")
    except Exception:
        return ZoneInfo("Asia/Singapore")


async def load_alerts(db: AsyncSession, now: datetime) -> list[dict]:
    rows = (await db.execute(text(f"""
        SELECT a.id, a.camera_id, a.module_type, a.severity, a.status, a.created_at,
               COALESCE(a.site_id, c.site_id) AS site_id, c.name AS camera_name, si.name AS site_name
          FROM alerts a
          LEFT JOIN cameras c ON c.id = a.camera_id
          LEFT JOIN sites si ON si.id = COALESCE(a.site_id, c.site_id)
         WHERE a.tenant_id = {T} AND a.created_at >= :since AND a.created_at <= :now
         ORDER BY a.created_at, a.id
    """), {"since": now - HISTORY, "now": now})).mappings().all()
    return [dict(r) for r in rows]


def verdict(status: str) -> bool | None:
    if status in FALSE:
        return True
    if status in REAL:
        return False
    return None


def build_rows(alerts: list[dict], tz: ZoneInfo) -> list[dict]:
    """Each alert with its factors, from the alerts before it, and its verdict when one was given."""
    decided = [verdict(a["status"]) for a in alerts if verdict(a["status"]) is not None]
    base = (sum(decided) + 1) / (len(decided) + 2)
    cam_hist: dict = defaultdict(deque)   # (time, false) of decided alerts
    mod_hist: dict = defaultdict(lambda: [0, 0])  # [false, decided]
    recent: dict = defaultdict(deque)     # (time, module) of every alert, per camera
    out = []
    for a in alerts:
        t, cam, mod = a["created_at"], a["camera_id"], a["module_type"] or "other"
        ch = cam_hist[cam]
        while ch and ch[0][0] < t - CAMERA_WINDOW:
            ch.popleft()
        rc = recent[cam]
        while rc and rc[0][0] < t - timedelta(hours=1):
            rc.popleft()
        cam_n, cam_false = len(ch), sum(f for _, f in ch)
        mf, mn = mod_hist[mod]
        local = t.astimezone(tz)
        e = {"cam_n": cam_n, "cam_false": cam_false, "module": mod.replace("_", " "),
             "mod_rate": (mf + PRIOR_WEIGHT * base) / (mn + PRIOR_WEIGHT),
             "severity": a["severity"] or "medium", "burst": len(rc)}
        values = {
            "camera_false_rate": (cam_false + PRIOR_WEIGHT * base) / (cam_n + PRIOR_WEIGHT),
            "module_false_rate": e["mod_rate"],
            "night": 1.0 if local.hour >= 19 or local.hour < 6 else 0.0,
            "severity": float(SEVERITY.get((a["severity"] or "").lower(), 2)),
            "camera_burst": math.log1p(len(rc)),
            "repeat": 1.0 if any(m == mod and x >= t - timedelta(minutes=10) for x, m in rc) else 0.0,
        }
        v = verdict(a["status"])
        out.append({"alert": a, "values": values, "evidence": e, "outcome": v})
        rc.append((t, mod))
        if v is not None:
            ch.append((t, 1 if v else 0))
            mod_hist[mod][0] += 1 if v else 0
            mod_hist[mod][1] += 1
    return out


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    tz = await _zone(db)
    rows = [r for r in build_rows(await load_alerts(db, now), tz) if r["outcome"] is not None]
    y = np.array([1 if r["outcome"] else 0 for r in rows], dtype=float)
    n_false, n_real = int(y.sum()), int(len(y) - y.sum())
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY),
                                               bases=BASES, cap=0.95)
    if len(rows) < MIN_ROWS or n_false < MIN_FALSE or n_real < MIN_REAL:
        need = []
        if len(rows) < MIN_ROWS:
            need.append(f"{MIN_ROWS - len(rows)} more alerts with a verdict")
        if n_false < MIN_FALSE:
            need.append(f"{MIN_FALSE - n_false} more marked as false alarms")
        if n_real < MIN_REAL:
            need.append(f"{MIN_REAL - n_real} more resolved as real")
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(rows), positives=n_false, fitted=None,
            metrics={"rows": len(rows), "positives": n_false}, threshold=threshold, threshold_reason=why,
            note="Still learning: it needs " + ", ".join(need) + ".")
    X = np.array([[r["values"][f] for f in FACTORS] for r in rows], dtype=float)
    fitted = ai_learning.fit_logistic(FACTORS, X, y)
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(rows), positives=n_false, fitted=fitted.as_json(),
        metrics=ai_learning.evaluate(FACTORS, X, y), threshold=threshold, threshold_reason=why,
        note=f"Learned from {len(rows)} alerts with a verdict, {n_false} of them false alarms.")


def _reasons(parts: list[dict], evidence: dict) -> list[dict]:
    return [{"factor": p["factor"], "words": _WORDS[p["factor"]](evidence), "push": p["push"]}
            for p in parts if p["push"] > 0.05][:3]


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0, "outcomes": 0}
    counts["outcomes"] = await record_outcomes(db)
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return counts
    tz = await _zone(db)
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    alerts = await load_alerts(db, now)
    open_ids, raised = [], set()
    for r in build_rows(alerts, tz):
        a = r["alert"]
        if a["status"] != "open" or a["created_at"] < now - OPEN_WINDOW:
            continue
        if allowed is not None and str(a["site_id"]) not in allowed:
            continue
        open_ids.append(a["id"])
        chance, parts = ai_learning.score(fitted, r["values"])
        if chance < threshold:
            continue
        local = a["created_at"].astimezone(tz)
        what = await ai_store.upsert_open(
            db, KEY, subject_type="alert", subject=a["id"], site_id=a["site_id"], person_user_id=None,
            title=(f"This {r['evidence']['module']} alert from {a['camera_name'] or 'a camera'} at {local:%H:%M} "
                   f"is likely a false alarm"),
            detail="Look at the picture before closing it. The AI never closes, hides or changes an alert.",
            score=chance, threshold=threshold, reasons=_reasons(parts, r["evidence"]),
            evidence={**r["evidence"], "alert_at": a["created_at"].isoformat()},
            model_id=model["id"], valid_until=a["created_at"] + OPEN_WINDOW)
        counts[what] += 1
        raised.add(a["id"])
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "alert", raised, open_ids, "The chance fell below the bar.")
    counts["reviews"] = await _camera_reviews(db, alerts, allowed, now, tz, model["id"])
    return counts


async def _camera_reviews(db: AsyncSession, alerts: list[dict], allowed: set | None, now: datetime,
                          tz: ZoneInfo, model_id) -> int:
    """Cameras whose alerts this month were mostly false, put forward for a person to review."""
    month = now.astimezone(tz).date().replace(day=1)
    by_cam: dict = defaultdict(lambda: {"false": 0, "n": 0, "name": None, "site": None, "site_name": None,
                                        "modules": defaultdict(int)})
    for a in alerts:
        v = verdict(a["status"])
        if v is None or a["created_at"].astimezone(tz).date() < month or a["camera_id"] is None:
            continue
        c = by_cam[a["camera_id"]]
        c["n"] += 1
        c["false"] += 1 if v else 0
        c["name"], c["site"], c["site_name"] = a["camera_name"], a["site_id"], a["site_name"]
        if v:
            c["modules"][(a["module_type"] or "other").replace("_", " ")] += 1
    raised = 0
    for cam, c in by_cam.items():
        if c["n"] < REVIEW_MIN or c["false"] / c["n"] < REVIEW_AT:
            continue
        if allowed is not None and str(c["site"]) not in allowed:
            continue
        top = sorted(c["modules"].items(), key=lambda kv: kv[1], reverse=True)[:2]
        what = await ai_store.upsert_open(
            db, KEY, subject_type="camera_month", subject=ai_store.subject_id(cam, f"{month:%Y-%m}"),
            site_id=c["site"], person_user_id=None,
            title=(f"{c['name'] or 'A camera'} raised mostly false alarms this month "
                   f"({c['false']} of {c['n']} decided)"),
            detail="Consider reviewing its zones or sensitivity in Cameras. Nothing on the camera is changed.",
            score=c["false"] / c["n"], threshold=REVIEW_AT,
            reasons=[{"factor": "module", "words": f"{n} false {m} alerts", "push": float(n)} for m, n in top],
            evidence={"false": c["false"], "decided": c["n"], "month": f"{month:%Y-%m}"},
            model_id=model_id, valid_until=None)
        raised += what == "new"
    return raised


async def record_outcomes(db: AsyncSession) -> int:
    """An operator's verdict on the alert is what happened: false (HAPPENED) or real (DID_NOT_HAPPEN). A
    warning whose alert is no longer open is closed: the operator decided in the alert queue."""
    rows = (await db.execute(text(f"""
        SELECT s.id, s.status AS s_status, a.status
          FROM ai_suggestions s JOIN alerts a ON a.id = s.subject_id
         WHERE s.tenant_id = {T} AND s.feature_key = :k AND s.subject_type = 'alert' AND s.outcome IS NULL
           AND a.status <> 'open'
           AND (s.status = 'OPEN' OR a.status IN ('false_positive', 'resolved', 'acknowledged'))
    """), {"k": KEY})).mappings().all()
    done = 0
    for r in rows:
        v = verdict(r["status"])
        await db.execute(text("""
            UPDATE ai_suggestions
               SET outcome = :o, outcome_at = CASE WHEN CAST(:o AS varchar) IS NULL THEN NULL ELSE now() END,
                   status = CASE WHEN status = 'OPEN' THEN 'EXPIRED' ELSE status END,
                   decision_note = CASE WHEN status = 'OPEN' THEN 'The operator decided the alert.' ELSE decision_note END,
                   updated_at = now()
             WHERE id = :id
        """), {"o": None if v is None else ("HAPPENED" if v else "DID_NOT_HAPPEN"), "id": r["id"]})
        done += v is not None
    return done
