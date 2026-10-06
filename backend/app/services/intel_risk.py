"""Normality and risk: how unusual a situation is, and how much it matters.

  situation + its events + the context of its most serious event + this
  camera's own past  ─►  normality  ─►  risk  ─►  an assessment, written once

RULES, NOT A MODEL. Every point of risk is a factor with a sentence beside it,
the way the drone module's engine does it. Nothing here was trained and nothing
learns. The same situation, context and history always give the same score, and
an officer can read exactly why it is what it is — and disagree with a weight,
which is a number in this file or a tenant's setting, not a mystery.

RISK IS NOT THE EVENT'S SEVERITY AND NOT THE MODEL'S CONFIDENCE. Severity is
fixed by the rule that raised the alert. Confidence is the model's certainty
about what it saw. Risk is this engine's judgement of how much the situation
matters *here, now*: the same person at the main entrance at ten in the morning
and in the fuel store at two at night are the same detection and very different
risks. Each is stored in its own place and never folded into one number.

WHAT IS NOT KNOWN ADDS NOTHING. A site with no hours set gets no "after hours"
points. A person nobody identified gets no points for being unidentified. Each
unknown instead lowers `risk_confidence`, which says how complete the picture
was — so an officer sees "HIGH, but four things were not known" rather than a
score that quietly assumed the worst or the best.

NORMALITY IS THIS PLACE'S OWN HABIT. How often this camera has raised this kind
of alert in this hour of the week, over the past weeks. With too little history
there is no normality score at all, and risk takes nothing from it.

THE WORDS ARE CHOSEN. Unusual, suspicious, requires review. An assessment never
says a person is an intruder or is unauthorised, and never states intent.

AN ASSESSMENT IS APPENDED, NEVER EDITED. `assess_tenant()` writes a new row only
when the answer has changed; the application's database role can neither change
nor remove one.

READ-ONLY ON EVERYTHING BUT ITS OWN TABLES, and it takes no action.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import intel_context as ctx

ENGINE_VERSION = "rules-3"
LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
#: What the corroboration factor says when one kind of source saw the matter
#: from more than one camera. It is corroboration, and it is not "more than one
#: kind of source": the recommender reads this to tell the two apart.
SEEN_BY_CAMERAS = "Seen by more than one camera."
#: Score at or above which each level starts — the drone engine's own bands, so
#: a "HIGH" means the same thing wherever an officer reads it.
THRESHOLDS = {"LOW": 15, "MEDIUM": 35, "HIGH": 55, "CRITICAL": 80}

#: What the most severe event in the situation is worth before anything else is known.
SEVERITY_POINTS = {"info": 5, "low": 15, "medium": 30, "high": 45, "critical": 60}
CRITICALITY_POINTS = {"critical": 15, "high": 10, "medium": 0, "low": -5}
ZONE_POINTS = {"critical": 20, "high": 20, "medium": 15, "low": 10}
#: A drone security zone's kind, as the drone module scores it.
DRONE_ZONE_POINTS = {"CRITICAL": 25, "NO_ENTRY": 20, "RESTRICTED": 15, "PERSON_RESTRICTED": 15,
                     "VEHICLE_RESTRICTED": 15, "SPECIAL_INSPECTION": 5}
#: What a second look by a drone is worth, either way: added when it saw more of
#: the same thing, taken off when it saw nothing more.
LOOK_POINTS = 5
#: The name each factor is weighted by in a tenant's `intel.risk_weights`.
FACTORS = ("SEVERITY", "ZONE", "CRITICALITY", "TIME", "IDENTITY", "ACCESS", "CORROBORATION", "PERSISTENCE",
           "HISTORY", "EXPECTED", "ANOMALY", "DRONE", "GUARD", "CONFIDENCE")

#: How many past weeks a camera's habit is read from, and how few are too few.
BASELINE_WEEKS = 8
BASELINE_FLOOR = 4
BATCH = int(os.environ.get("INTEL_ASSESS_BATCH", "50"))

CAMERA_SOURCES = frozenset({"CCTV_AI", "LPR", "FACE_RECOGNITION"})

#: What a situation appears to be, as a code. The label is the same thing in
#: words; later stages and screens branch on the code, never on the sentence.
KINDS = ("GUARD_EMERGENCY", "WEAPON", "FIRE_SMOKE", "DOOR_FORCED", "ACCESS_REFUSED", "ALARM", "FALL",
         "BLOCK_LISTED", "RESTRICTED_ZONE", "PATROL_FINDING", "CAMERA_OFFLINE", "ACTIVITY")


def level_of(score: int) -> str:
    level = "INFO"
    for name in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        if score >= THRESHOLDS[name]:
            level = name
    return level


def validate_weights(value: Any) -> None:
    """A tenant's `intel.risk_weights`: an object of factor name to a multiplier
    between 0 and 3. A factor left out keeps its shipped weight of 1."""
    if not isinstance(value, dict):
        raise ValueError("must be an object of {factor: multiplier}")
    for name, weight in value.items():
        if name not in FACTORS:
            raise ValueError(f"'{name}' is not a risk factor; use {', '.join(FACTORS)}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 3:
            raise ValueError(f"'{name}' must be a number between 0 and 3")


# ─── Normality ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Baseline:
    """A camera's own past for one kind of alert in one hour of the week:
    how many weeks of history there are, in how many of them it happened, and
    how often altogether. None of it is a guess; all of it is counted."""

    weeks_observed: int = 0
    weeks_with_alerts: int = 0
    alerts: int = 0


def normality(context: Mapping, baseline: Baseline | None) -> dict:
    """{normality_score, anomaly_score, factors, basis}. Scores are None when
    there is too little history to say what is usual."""
    if baseline is None or baseline.weeks_observed < BASELINE_FLOOR:
        weeks = 0 if baseline is None else baseline.weeks_observed
        return {"normality_score": None, "anomaly_score": None, "factors": [],
                "basis": f"Insufficient history: {weeks} week(s) of alerts from this camera, "
                         f"{BASELINE_FLOOR} needed to say what is usual."}
    factors: list[dict] = []

    def add(factor: str, points: int, detail: str) -> None:
        factors.append({"factor": factor, "points": points, "detail": detail})

    share = baseline.weeks_with_alerts / baseline.weeks_observed
    add("HABIT", round(share * 100),
        f"This camera raised this kind of alert in this hour of the week in {baseline.weeks_with_alerts} of the "
        f"last {baseline.weeks_observed} weeks ({baseline.alerts} alert(s)).")
    # Within hours is not scored here: "the site was open" is already one of the
    # things `expected` lists, and counting it twice would call a busy hour
    # more usual than the camera's own history says it is.
    if (context.get("time") or {}).get("after_hours") is True:
        add("HOURS", -20, "Outside business hours.")
    if any(z.get("in_force") for z in (context.get("place") or {}).get("zones", [])):
        add("ZONE", -15, "A restricted zone was in force.")
    for reason in context.get("expected") or []:
        add("EXPECTED", 10, f"Could be ordinary: {reason}.")
    score = max(0, min(100, sum(f["points"] for f in factors)))
    return {"normality_score": score, "anomaly_score": 100 - score, "factors": factors,
            "basis": f"{baseline.weeks_observed} weeks of this camera's own alerts."}


# ─── Risk ────────────────────────────────────────────────────────────────────

@dataclass
class Assessment:
    kind: str
    label: str
    summary: str
    risk_score: int
    risk_level: str
    risk_factors: list[dict]
    normality_score: int | None
    anomaly_score: int | None
    normality_factors: list[dict]
    detection_confidence: float | None
    correlation_confidence: float | None
    risk_confidence: float
    context: dict = field(default_factory=dict)

    def same_answer_as(self, previous: Mapping | None) -> bool:
        """Whether this says what the last assessment said — in which case no
        new row is written. A repeat alert that changes nothing is not news."""
        if previous is None:
            return False
        before = previous["risk_factors"]
        before = json.loads(before) if isinstance(before, str) else before
        return (previous["risk_score"] == self.risk_score and previous["label"] == self.label
                and [(f["factor"], f["points"]) for f in before]
                == [(f["factor"], f["points"]) for f in self.risk_factors])


def _attrs(event: Mapping) -> dict:
    a = event.get("attributes")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


def classify(situation: Mapping, events: list[Mapping], context: Mapping) -> tuple[str, str]:
    """What the situation appears to be: (kind, label). The label is in the
    layer's chosen words. The first that applies."""
    sources = {e.get("source_type") for e in events}
    types = {str(e.get("event_type") or "") for e in events}
    modules = {_attrs(e).get("module_type") for e in events}
    seen = bool(sources & (CAMERA_SOURCES | {"DRONE_PATROL"}))
    zone = any(z.get("in_force") for z in (context.get("place") or {}).get("zones", []))
    after = (context.get("time") or {}).get("after_hours") is True
    if "GUARD" in sources:
        return "GUARD_EMERGENCY", "Guard emergency"
    if "weapon" in modules:
        return "WEAPON", "Possible weapon — requires review"
    if "fire_smoke" in modules:
        return "FIRE_SMOKE", "Possible fire or smoke — requires review"
    if "access.forced" in types:
        return "DOOR_FORCED", "Door forced" + (" — with activity seen nearby" if seen else "")
    if "access.denied" in types and seen:
        return "ACCESS_REFUSED", "Access refused, with activity seen nearby"
    if "access.denied" in types:
        return "ACCESS_REFUSED", "Access refused"
    if "ALARM" in sources:
        return "ALARM", "Alarm" + (" — with activity seen nearby" if seen else "")
    if "fall" in modules:
        return "FALL", "Possible fall — requires review"
    if any(e.get("subject_verdict") == "BLOCK" for e in events):
        vehicle = any(e.get("subject_kind") == "VEHICLE" and e.get("subject_verdict") == "BLOCK" for e in events)
        return "BLOCK_LISTED", "Block-listed " + ("vehicle" if vehicle else "person")
    if zone:
        return "RESTRICTED_ZONE", ("Suspicious activity in a restricted zone, out of hours" if after
                                   else "Activity in a restricted zone")
    # An intrusion alert is activity whatever else reported beside it.
    if "intrusion" not in modules:
        if "VIRTUAL_PATROL" in sources:
            return "PATROL_FINDING", "Virtual patrol finding"
        if sources == {"SYSTEM"}:
            return "CAMERA_OFFLINE", "Camera stopped sending"
    return "ACTIVITY", "Unusual activity out of hours" if after else "Activity that requires review"


def assess(situation: Mapping, events: list[Mapping], context: Mapping, baseline: Baseline | None = None,
           weights: Mapping[str, float] | None = None) -> Assessment:
    """Score one situation. `events` are its events with their link
    (`method`, `is_duplicate`); `context` is that of its most serious event."""
    weights = weights or {}
    factors: list[dict] = []

    def add(factor: str, points: float, detail: str) -> None:
        scaled = round(points * float(weights.get(factor, 1)))
        factors.append({"factor": factor, "points": scaled, "detail": detail})

    place, time = context.get("place") or {}, context.get("time") or {}
    people, access = context.get("people") or {}, context.get("access") or {}
    history = context.get("history") or {}
    real = [e for e in events if not e.get("is_duplicate")]

    severity = str(situation.get("severity") or "low")
    add("SEVERITY", SEVERITY_POINTS.get(severity, 15),
        f"The most serious event is “{situation.get('title')}” ({severity}).")

    in_force = [z for z in place.get("zones", []) if z.get("in_force")]
    if in_force:
        z = max(in_force, key=lambda z: ZONE_POINTS.get(str(z.get("severity")), 0))
        add("ZONE", ZONE_POINTS.get(str(z.get("severity")), 15),
            f"Restricted zone “{z.get('name')}” ({z.get('severity')}) was in force.")
    elif place.get("restricted_area"):
        add("ZONE", 10, f"A restricted area{': ' + place['area'] if place.get('area') else ''}.")
    drone_zone = next((_attrs(e).get("zone_type") for e in events if _attrs(e).get("zone_type")), None)
    if drone_zone and DRONE_ZONE_POINTS.get(drone_zone) and not in_force:
        add("ZONE", DRONE_ZONE_POINTS[drone_zone],
            f"Over a drone security zone ({drone_zone.lower().replace('_', ' ')}).")

    if place.get("criticality") in CRITICALITY_POINTS and CRITICALITY_POINTS[place["criticality"]]:
        add("CRITICALITY", CRITICALITY_POINTS[place["criticality"]], f"Criticality of the place: {place['criticality']}.")

    if time.get("after_hours") is True:
        add("TIME", 15, "Outside business hours" + (f" — public holiday ({time['holiday']})." if time.get("holiday")
                                                    else "."))

    verdicts = {e.get("subject_verdict") for e in events}
    if "BLOCK" in verdicts:
        add("IDENTITY", 25, "A person or vehicle on a block list.")
    elif "ALLOW" in verdicts and "UNKNOWN" not in verdicts:
        add("IDENTITY", -25, "Identified on an allow list.")

    types = {str(e.get("event_type") or "") for e in events}
    if access.get("forced") or "access.forced" in types:
        add("ACCESS", 25, "A door was forced.")
    elif access.get("denied") or "access.denied" in types:
        reason = f" ({access['last_denial_reason']})" if access.get("last_denial_reason") else ""
        add("ACCESS", 15, f"Access was refused nearby in time{reason}.")
    elif access.get("granted"):
        add("ACCESS", -10, "Access was granted nearby in time: someone was let in.")

    kinds = sorted({str(e.get("source_type")) for e in real})
    if len(kinds) >= 3:
        add("CORROBORATION", 20, f"Reported by {len(kinds)} kinds of source: {', '.join(kinds)}.")
    elif len(kinds) == 2:
        add("CORROBORATION", 10, f"Reported by two kinds of source: {', '.join(kinds)}.")
    elif any(e.get("method") == "ADJACENT_CAMERA" for e in events):
        add("CORROBORATION", 5, SEEN_BY_CAMERAS)

    repeats = sum(1 for e in events if e.get("is_duplicate"))
    if repeats >= 10:
        add("PERSISTENCE", 10, f"The same alert has repeated {repeats} times.")
    elif repeats >= 3:
        add("PERSISTENCE", 5, f"The same alert has repeated {repeats} times.")

    if history.get("incidents_at_camera"):
        add("HISTORY", 5, f"{history['incidents_at_camera']} incident(s) at this camera in {history.get('days', 30)} days.")
    share = history.get("false_positive_share")
    if share is not None and share >= 0.8:
        add("HISTORY", -25, f"{round(share * 100)}% of decided alerts of this kind from this camera were false.")
    elif share is not None and share >= 0.6:
        add("HISTORY", -15, f"{round(share * 100)}% of decided alerts of this kind from this camera were false.")

    if people.get("contractor_permits_in_force"):
        add("EXPECTED", -10, "A contractor permit was in force at the site.")
    if people.get("visitors_on_site") and time.get("after_hours") is False:
        add("EXPECTED", -5, "Visitors were signed in, during business hours.")

    norm = normality(context, baseline)
    if norm["anomaly_score"] is not None:
        if norm["anomaly_score"] >= 80:
            add("ANOMALY", 10, f"Unusual for this place and hour (anomaly {norm['anomaly_score']}).")
        elif norm["anomaly_score"] <= 20:
            add("ANOMALY", -10, f"Usual for this place and hour (anomaly {norm['anomaly_score']}).")

    drone_levels = [(_attrs(e).get("drone_risk_level"), _attrs(e).get("drone_risk_score")) for e in events
                    if e.get("source_type") == "DRONE_PATROL" and _attrs(e).get("drone_risk_level")]
    if drone_levels:
        lvl, score = max(drone_levels, key=lambda d: LEVELS.index(d[0]) if d[0] in LEVELS else 0)
        pts = {"CRITICAL": 15, "HIGH": 10}.get(lvl, 0)
        if pts:
            add("DRONE", pts, f"The drone module assessed its own sighting as {lvl}"
                              + (f" ({round(score)})." if score is not None else "."))
    # A look a person asked for: the drone held and looked again. Seeing more
    # of the same thing is a confirmation; seeing nothing more is a small
    # reason for less concern, and no more than that — the latest look counts.
    looks = [e for e in events if e.get("event_type") == "drone.verification"]
    if looks:
        last = max(looks, key=lambda e: e["occurred_at"])
        added = int(_attrs(last).get("detections_added") or 0)
        hold = _attrs(last).get("hold_seconds")
        held = f"held for {hold} s and looked again" if hold else "held and looked again"
        if added > 0:
            add("DRONE", LOOK_POINTS, f"A drone {held}, as a person asked: {added} more detection(s) of the same thing.")
        else:
            add("DRONE", -LOOK_POINTS, f"A drone {held}, as a person asked: it saw nothing more.")

    if any(e.get("source_type") == "GUARD" for e in events):
        add("GUARD", 20, "A guard raised an SOS.")

    confidences = [float(e["confidence"]) for e in events if e.get("confidence") is not None]
    detection = max(confidences) if confidences else None
    if detection is not None and detection >= 0.9:
        add("CONFIDENCE", 5, f"The model was sure of what it saw ({detection:.2f}).")
    elif detection is not None and detection < 0.6:
        add("CONFIDENCE", -10, f"The model was not sure of what it saw ({detection:.2f}).")

    score = max(0, min(100, sum(f["points"] for f in factors)))
    level = level_of(score)
    unknowns = list(context.get("unknowns") or [])
    if norm["anomaly_score"] is None:
        unknowns.append(norm["basis"])
    risk_confidence = round(max(0.3, 1.0 - 0.12 * len(unknowns)), 2)
    correlation = situation.get("correlation_confidence")
    kind, label = classify(situation, events, context)
    up = sorted((f for f in factors if f["points"] > 0), key=lambda f: -f["points"])[:3]
    down = sorted((f for f in factors if f["points"] < 0), key=lambda f: f["points"])[:2]
    summary = f"{label}. Risk {level} ({score})."
    if up:
        summary += " Raised by: " + " ".join(f["detail"] for f in up)
    if down:
        summary += " Lowered by: " + " ".join(f["detail"] for f in down)
    if unknowns:
        summary += f" Not known: {len(unknowns)} thing(s)."
    return Assessment(
        kind=kind, label=label, summary=summary, risk_score=score, risk_level=level, risk_factors=factors,
        normality_score=norm["normality_score"], anomaly_score=norm["anomaly_score"],
        normality_factors=norm["factors"], detection_confidence=detection,
        correlation_confidence=float(correlation) if correlation is not None else None,
        risk_confidence=risk_confidence,
        context={**dict(context), "unknowns": unknowns, "normality_basis": norm["basis"]},
    )


# ─── Against the database ────────────────────────────────────────────────────

async def _scope(db: AsyncSession, tenant_id) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def weights(db: AsyncSession) -> dict:
    """The tenant's own factor multipliers, or none."""
    value = (await db.execute(text(
        "SELECT setting_value FROM tenant_settings WHERE setting_key = 'intel.risk_weights'"))).scalar_one_or_none()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def baseline(db: AsyncSession, event: Mapping) -> Baseline | None:
    """This camera's past for this kind of alert in this hour of the week, read
    from the alerts table — which holds the history from before this layer was
    switched on. None when the event has no camera or is not an alert kind."""
    module = _attrs(event).get("module_type")
    if event.get("camera_id") is None or not module:
        return None
    at: datetime = event["occurred_at"]
    # For each of the past weeks: how many such alerts within half an hour of
    # this same time of the week. And how many whole weeks this camera has been
    # raising alerts of any kind at all, which is how much history there is.
    # The COALESCE matters: LEAST() ignores a null, so a camera with no alerts at
    # all would otherwise be credited with the full eight weeks of history.
    row = (await db.execute(text("""
        SELECT (SELECT LEAST(CAST(:weeks AS integer), COALESCE(
                             floor(EXTRACT(EPOCH FROM (CAST(:at AS timestamptz) - min(a2.created_at))) / 604800)::int,
                             0))
                  FROM alerts a2 WHERE a2.camera_id = :c AND a2.created_at < CAST(:at AS timestamptz))
                   AS weeks_observed,
               count(*) FILTER (WHERE w.n > 0) AS weeks_with_alerts,
               COALESCE(sum(w.n), 0) AS alerts
          FROM (SELECT (SELECT count(*) FROM alerts a
                         WHERE a.camera_id = :c AND a.module_type = :m
                           AND a.created_at BETWEEN CAST(:at AS timestamptz) - make_interval(weeks => k)
                                                        - interval '30 minutes'
                                                AND CAST(:at AS timestamptz) - make_interval(weeks => k)
                                                        + interval '30 minutes') AS n
                  FROM generate_series(1, CAST(:weeks AS integer)) AS k) w
    """), {"c": event["camera_id"], "m": module, "at": at, "weeks": BASELINE_WEEKS})).first()
    return Baseline(weeks_observed=int(row.weeks_observed or 0), weeks_with_alerts=int(row.weeks_with_alerts or 0),
                    alerts=int(row.alerts or 0))


async def assess_situation(db: AsyncSession, situation: Mapping, now: datetime) -> tuple[Assessment, bool]:
    """Assess one situation now. Returns (the assessment, whether a new row was
    written). The caller commits."""
    events = [dict(r) for r in (await db.execute(text("""
        SELECT e.*, l.method, l.is_duplicate FROM security_situation_events l
          JOIN security_events e ON e.id = l.event_id
         WHERE l.situation_id = :s ORDER BY e.occurred_at, e.id
    """), {"s": situation["id"]})).mappings().all()]
    rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    head = min(events, key=lambda e: (-rank.get(str(e.get("severity")), 0), e["occurred_at"]))
    context = await ctx.context_for(db, head)
    result = assess(situation, events, context, await baseline(db, head), await weights(db))

    previous = (await db.execute(text(
        "SELECT sequence, risk_score, label, risk_factors FROM security_assessments "
        " WHERE situation_id = :s ORDER BY sequence DESC LIMIT 1"), {"s": situation["id"]})).mappings().first()
    if result.same_answer_as(previous):
        await db.execute(text(
            "UPDATE security_situations SET assessed_at = GREATEST(CAST(:n AS timestamptz), updated_at) "
            " WHERE id = :s"), {"n": now, "s": situation["id"]})
        return result, False
    assessment_id = (await db.execute(text("""
        INSERT INTO security_assessments
               (tenant_id, situation_id, sequence, assessed_at, kind, label, summary, risk_score, risk_level,
                risk_factors, normality_score, anomaly_score, normality_factors, detection_confidence,
                correlation_confidence, risk_confidence, context, event_count, engine_version)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :seq, :n, :kind, :label, :summary, :score, :level,
                CAST(:factors AS jsonb), :norm, :anom, CAST(:norm_factors AS jsonb), :det, :corr, :conf,
                CAST(:context AS jsonb), :count, :engine)
        RETURNING id
    """), {"s": situation["id"], "seq": (previous["sequence"] + 1) if previous else 1, "n": now,
           "kind": result.kind, "label": result.label, "summary": result.summary, "score": result.risk_score,
           "level": result.risk_level, "factors": json.dumps(result.risk_factors),
           "norm": result.normality_score, "anom": result.anomaly_score,
           "norm_factors": json.dumps(result.normality_factors), "det": result.detection_confidence,
           "corr": result.correlation_confidence, "conf": result.risk_confidence,
           "context": json.dumps(result.context, default=str), "count": len(events),
           "engine": ENGINE_VERSION})).scalar_one()
    # updated_at is left alone: it says when the situation's events last changed,
    # and assessing it again is not such a change.
    await db.execute(text(
        "UPDATE security_situations SET risk_score = :score, risk_level = :level, "
        "       assessed_at = GREATEST(CAST(:n AS timestamptz), updated_at), assessment_id = :a WHERE id = :s"),
        {"score": result.risk_score, "level": result.risk_level, "n": now, "a": assessment_id,
         "s": situation["id"]})
    return result, True


def announcement(situation: Mapping, a: Assessment) -> dict:
    """What goes on the tenant's live channel when an assessment is ready."""
    return {
        "situation_id": str(situation["id"]),
        "situation_number": situation["situation_number"],
        "kind": a.kind,
        "label": a.label,
        "risk_score": a.risk_score,
        "risk_level": a.risk_level,
        "risk_confidence": a.risk_confidence,
        "detection_confidence": a.detection_confidence,
        "correlation_confidence": a.correlation_confidence,
        "anomaly_score": a.anomaly_score,
        "top_factors": [f["detail"] for f in sorted(a.risk_factors, key=lambda f: -f["points"])[:3]
                        if f["points"] > 0],
    }


async def assess_tenant(factory, tenant_id, now: datetime, batch: int = BATCH) -> dict:
    """Assess every situation whose events have changed since it was last
    assessed. Returns {"assessed", "changed", "failed", "announce"}."""
    out: dict = {"assessed": 0, "changed": 0, "failed": 0, "announce": []}
    async with factory() as db:
        await _scope(db, tenant_id)
        ids = (await db.execute(text(
            "SELECT id FROM security_situations WHERE assessed_at IS NULL OR assessed_at < updated_at "
            " ORDER BY updated_at LIMIT :n"), {"n": batch})).scalars().all()
        await db.rollback()
    for situation_id in ids:
        try:
            async with factory() as db:
                await _scope(db, tenant_id)
                situation = (await db.execute(text(
                    "SELECT * FROM security_situations WHERE id = :s FOR UPDATE SKIP LOCKED"),
                    {"s": situation_id})).mappings().first()
                if situation is None:
                    continue
                result, written = await assess_situation(db, dict(situation), now)
                await db.commit()
        except Exception:  # noqa: BLE001 — one situation's failure is not the batch's
            out["failed"] += 1
            continue
        out["assessed"] += 1
        if written:
            out["changed"] += 1
            out["announce"].append(("intel_assessment_ready", announcement(situation, result)))
    return out
