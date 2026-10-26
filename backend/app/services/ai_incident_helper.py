"""Incident helper (feature `incident_helper`): similar past incidents, what was done, and a suggested severity.

WHAT IT LEARNS FROM. The organisation's own incidents of the last 365 days: the
words of their titles and descriptions, the severity they were given, and the
last note written on them (what was done).

HOW. Word weights (TF-IDF: a word common to every incident counts for little, a
rare one for a lot) learned from those incidents; a new incident is compared
with each past one by those weights (cosine similarity). The most similar past
incidents are shown with what was done, and their severities, weighted by how
similar they are, give a suggested severity and how sure it is.

WHEN IT SPEAKS. Hourly, for incidents opened in the last 24 hours whose recorded
severity differs from the one similar past incidents had, when it is sure enough
(60% by default). Also on request, for any words (`suggest()`), for the incident
form and the assistant.

IT CHANGES NO INCIDENT. The severity stays as recorded until a person changes it.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_learning, ai_store

KEY = "incident_helper"
HISTORY = timedelta(days=365)
OPEN_WINDOW = timedelta(hours=24)
MIN_INCIDENTS = 20
NEIGHBOURS = 7
SIMILAR_AT = 0.20
VOCABULARY = 5000
BASES = {"HIGH": 0.50, "MEDIUM": 0.60, "LOW": 0.75}
T = "current_setting('app.current_tenant')::uuid"
_STOP = set("the and for was were with that this from have has had are not but you your our their there then than "
            "into onto been being also near very just some any all who what when where which while about after "
            "before over under again more most other such only same can could would should will may might did does "
            "out off per via its it's his her him she they them".split())


def words(*texts: str | None) -> list[str]:
    out = []
    for t in texts:
        for w in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if len(w) >= 3 and w not in _STOP:
                out.append(w)
    return out


def fit_idf(documents: list[list[str]]) -> dict[str, float]:
    df: Counter = Counter()
    for d in documents:
        df.update(set(d))
    n = len(documents)
    idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}
    keep = sorted(idf, key=lambda w: (-df[w], w))[:VOCABULARY]
    return {w: round(idf[w], 5) for w in keep}


def vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(t for t in tokens if t in idf)
    v = {w: c * idf[w] for w, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


async def load_incidents(db: AsyncSession, now: datetime) -> list[dict]:
    rows = (await db.execute(text(f"""
        SELECT i.id, i.title, i.description, i.severity, i.status, i.created_at, i.camera_id,
               c.site_id, si.name AS site_name,
               (SELECT n.note FROM incident_notes n WHERE n.incident_id = i.id
                 ORDER BY n.created_at DESC LIMIT 1) AS last_note
          FROM incidents i
          LEFT JOIN cameras c ON c.id = i.camera_id
          LEFT JOIN sites si ON si.id = c.site_id
         WHERE i.tenant_id = {T} AND i.created_at >= :since AND i.created_at <= :now
         ORDER BY i.created_at, i.id
    """), {"since": now - HISTORY, "now": now})).mappings().all()
    return [dict(r) for r in rows]


def suggest_from(tokens: list[str], past: list[dict], idf: dict[str, float]) -> dict:
    """The most similar past incidents and the severity they suggest, from words alone."""
    v = vector(tokens, idf)
    scored = sorted(((cosine(v, p["vector"]), p) for p in past), key=lambda x: x[0], reverse=True)
    near = [(s, p) for s, p in scored[:NEIGHBOURS] if s >= SIMILAR_AT]
    votes: dict = defaultdict(float)
    for s, p in near:
        votes[(p["severity"] or "medium").lower()] += s
    total = sum(votes.values())
    best = max(votes, key=votes.get) if votes else None
    return {
        "severity": best, "sure": round(votes[best] / total, 4) if best else 0.0,
        "similar": [{"id": str(p["id"]), "title": p["title"], "severity": p["severity"], "status": p["status"],
                     "at": p["created_at"].isoformat(), "similarity": round(s, 3),
                     "what_was_done": (p["last_note"] or "")[:300] or None} for s, p in near[:3]],
    }


async def train(db: AsyncSession, *, sensitivity: str, now: datetime) -> dict:
    incidents = [i for i in await load_incidents(db, now) if words(i["title"], i["description"])]
    threshold, why = ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, KEY),
                                               bases=BASES, cap=0.95)
    if len(incidents) < MIN_INCIDENTS:
        return await ai_store.save_model(
            db, KEY, status="LEARNING", rows_used=len(incidents), positives=0, fitted=None,
            metrics={"rows": len(incidents)}, threshold=threshold, threshold_reason=why,
            note=f"Still learning: it needs {MIN_INCIDENTS - len(incidents)} more incidents with a title or description.")
    idf = fit_idf([words(i["title"], i["description"]) for i in incidents])
    return await ai_store.save_model(
        db, KEY, status="READY", rows_used=len(incidents), positives=len(idf), fitted={"idf": idf},
        metrics={"rows": len(incidents), "words": len(idf)}, threshold=threshold, threshold_reason=why,
        note=f"Learned the words of {len(incidents)} incidents ({len(idf)} words).")


def _past(incidents: list[dict], idf: dict, before: datetime | None = None) -> list[dict]:
    return [{**i, "vector": vector(words(i["title"], i["description"]), idf)}
            for i in incidents if before is None or i["created_at"] < before]


async def suggest(db: AsyncSession, model: dict | None, title: str, description: str | None,
                  now: datetime) -> dict | None:
    """For the incident form and the assistant: None while the feature is still learning."""
    if not model or model["status"] != "READY":
        return None
    idf = model["fitted"]["idf"]
    return suggest_from(words(title, description), _past(await load_incidents(db, now), idf), idf)


async def score(db: AsyncSession, *, model: dict | None, threshold: float, site_ids: list | None,
                now: datetime) -> dict:
    counts = {"new": 0, "refreshed": 0, "decided": 0, "expired": 0, "outcomes": 0}
    incidents = await load_incidents(db, now)
    counts["outcomes"] = await record_outcomes(db, incidents)
    counts["expired"] += await ai_store.expire_past(db, KEY, now)
    if not model or model["status"] != "READY":
        return counts
    idf = model["fitted"]["idf"]
    allowed = None if site_ids is None else {str(s) for s in site_ids}
    every = _past(incidents, idf)
    fresh, raised = [], set()
    for i in incidents:
        if i["status"] in ("resolved", "closed") or i["created_at"] < now - OPEN_WINDOW:
            continue
        if allowed is not None and str(i["site_id"]) not in allowed:
            continue
        fresh.append(i["id"])
        before = [p for p in every if p["created_at"] < i["created_at"]]
        s = suggest_from(words(i["title"], i["description"]), before, idf)
        recorded = (i["severity"] or "medium").lower()
        if not s["severity"] or s["severity"] == recorded or s["sure"] < threshold:
            continue
        what = await ai_store.upsert_open(
            db, KEY, subject_type="incident", subject=i["id"], site_id=i["site_id"], person_user_id=None,
            title=f"\"{i['title']}\" looks like past {s['severity']} incidents (recorded as {recorded})",
            detail="A suggestion only: the severity is not changed. Similar past incidents and what was done are below.",
            score=s["sure"], threshold=threshold,
            reasons=[{"factor": "similar", "push": x["similarity"],
                      "words": (f"{x['title']} ({x['severity']}, {x['at'][:10]})"
                                + (f": {x['what_was_done']}" if x["what_was_done"] else ""))} for x in s["similar"]],
            evidence={"suggested": s["severity"], "recorded": recorded, "similar": s["similar"]},
            model_id=model["id"], valid_until=i["created_at"] + OPEN_WINDOW)
        counts[what] += 1
        raised.add(i["id"])
    counts["expired"] += await ai_store.expire_open_except(
        db, KEY, "incident", raised, fresh, "It no longer differs enough from similar incidents.")
    return counts


async def record_outcomes(db: AsyncSession, incidents: list[dict]) -> int:
    """HAPPENED once a person gave the incident the suggested severity; DID_NOT_HAPPEN once it was resolved
    without it."""
    by_id = {i["id"]: i for i in incidents}
    rows = (await db.execute(text(f"""
        SELECT id, subject_id, evidence FROM ai_suggestions
         WHERE tenant_id = {T} AND feature_key = :k AND outcome IS NULL
    """), {"k": KEY})).mappings().all()
    done = 0
    for r in rows:
        i = by_id.get(r["subject_id"])
        if i is None:
            continue
        if (i["severity"] or "").lower() == (r["evidence"] or {}).get("suggested"):
            outcome = "HAPPENED"
        elif i["status"] in ("resolved", "closed"):
            outcome = "DID_NOT_HAPPEN"
        else:
            continue
        await db.execute(text("UPDATE ai_suggestions SET outcome = :o, outcome_at = now(), updated_at = now() "
                              "WHERE id = :id"), {"o": outcome, "id": r["id"]})
        done += 1
    return done
