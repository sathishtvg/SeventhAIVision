"""Feedback: what was suggested, what a person decided, and what it turned out to be.

  AI recommendation ─► human decision ─► actual outcome
       dispatch a guard      monitor        an authorised maintenance worker

THE THREE ARE ALREADY RECORDED, EACH IN ITS OWN PLACE: the suggestion in
`security_recommendations`, the decision in `security_decisions`, how the
matter ended in the closing decision and its reason. This module puts them on
one row for each closed situation, and adds a fourth thing a person may say
afterwards: a REVIEW — what it turned out to be, whether the assessment was
about right, whether what was suggested was useful (`security_feedback`).

A CONTROLLED DATASET, FOR PEOPLE. `dataset()` is what a security manager
exports to see how the layer is doing at their sites, and what an engineer
would need to argue for changing a threshold or a rule. **Nothing in the
platform learns from it by itself.** No model is trained on it, no weight moves
because of it, and no code but this module and its API reads the reviews. Any
change to a rule or a weight is made by a person, in the open: a setting an
administrator changes, or code that is reviewed and released.

NO NAMES, NO NOTES. A row carries roles, codes, numbers and times. It carries
no user's name or id, and none of the free text people wrote — a note can hold
anything, and a dataset travels.

`analyse()` is pure. `dataset()` and `for_situation()` only read; `record()`
writes one review, to the layer's own table.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OUTCOMES = {
    "REAL_INCIDENT": "A real security incident",
    "AUTHORISED_ACTIVITY": "Authorised activity",
    "FALSE_DETECTION": "False detection",
    "EQUIPMENT_FAULT": "Equipment fault",
    "UNDETERMINED": "Could not be determined",
}
ASSESSMENT_VERDICTS = {
    "ABOUT_RIGHT": "About right", "TOO_HIGH": "Assessed too high", "TOO_LOW": "Assessed too low",
    "WRONG_KIND": "Wrong about what it was",
}
RECOMMENDATION_VERDICTS = {
    "USEFUL": "Useful", "NOT_USEFUL": "Not useful", "MISSED_A_STEP": "Missed a step that was needed",
    "NOTHING_SUGGESTED": "Nothing was suggested",
}
#: The columns of the dataset, in the order they are exported.
COLUMNS = (
    "situation_number", "site_id", "started_at", "closed_at", "kind", "event_count", "source_types",
    "first_risk_level", "first_risk_score", "last_risk_level", "last_risk_score", "assessments", "engine_version",
    "suggested_action", "suggestion_confidence",
    "first_decision", "first_decision_basis", "first_decision_reason", "first_decision_role",
    "seconds_to_first_decision", "decisions", "overrides",
    "closed_as", "closing_reason",
    "review_outcome", "review_assessment", "review_recommendation", "reviews",
)
#: What must never be a column: who somebody is, or what they wrote.
NEVER = ("name", "email", "user_id", "note", "full_name")
MAX_ROWS = 5000
USE = ("For people to read. Nothing in the platform is trained on this dataset or changes because of it; a rule or a "
       "weight is changed only by a person, as a setting or as released code.")


def validate(outcome: str, assessment_verdict: str | None, recommendation_verdict: str | None,
             note: str | None) -> None:
    """A review that makes sense, or ValueError saying what does not."""
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome '{outcome}'. One of: {', '.join(OUTCOMES)}.")
    if assessment_verdict is not None and assessment_verdict not in ASSESSMENT_VERDICTS:
        raise ValueError(f"Unknown assessment verdict '{assessment_verdict}'. One of: "
                         f"{', '.join(ASSESSMENT_VERDICTS)}.")
    if recommendation_verdict is not None and recommendation_verdict not in RECOMMENDATION_VERDICTS:
        raise ValueError(f"Unknown recommendation verdict '{recommendation_verdict}'. One of: "
                         f"{', '.join(RECOMMENDATION_VERDICTS)}.")
    if outcome == "UNDETERMINED" and not (note or "").strip():
        raise ValueError("An outcome that could not be determined needs a note saying what is still not known.")


# ─── Pure: what the dataset says ─────────────────────────────────────────────

def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def analyse(rows: list[Mapping]) -> dict:
    """How the suggestions fared, counted from dataset rows. Pure.

    A rate is given only where there is something to divide by; where there is
    not it is None, which a screen shows as "not enough to say"."""
    suggested = [r for r in rows if r.get("suggested_action")]
    decided = [r for r in suggested if r.get("first_decision")]
    followed = [r for r in decided if r.get("first_decision_basis") == "FOLLOWED"]
    overridden = [r for r in decided if r.get("first_decision_basis") == "OVERRIDE"]

    by_action: dict[str, dict] = {}
    for r in suggested:
        a = by_action.setdefault(r["suggested_action"], {"suggested": 0, "followed": 0, "overridden": 0,
                                                         "closed_false": 0})
        a["suggested"] += 1
        a["followed"] += r.get("first_decision_basis") == "FOLLOWED"
        a["overridden"] += r.get("first_decision_basis") == "OVERRIDE"
        a["closed_false"] += r.get("closed_as") == "FALSE_POSITIVE"

    by_kind: dict[str, dict] = {}
    for r in rows:
        k = by_kind.setdefault(r.get("kind") or "NOT_ASSESSED", {"situations": 0, "closed_false": 0,
                                                                  "reviewed_real": 0, "assessed_too_high": 0,
                                                                  "assessed_too_low": 0})
        k["situations"] += 1
        k["closed_false"] += r.get("closed_as") == "FALSE_POSITIVE"
        k["reviewed_real"] += r.get("review_outcome") == "REAL_INCIDENT"
        k["assessed_too_high"] += r.get("review_assessment") == "TOO_HIGH"
        k["assessed_too_low"] += r.get("review_assessment") == "TOO_LOW"

    def tally(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            if r.get(key):
                out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    reasons: dict[str, int] = {}
    for r in overridden:
        code = r.get("first_decision_reason") or "NOT_GIVEN"
        reasons[code] = reasons.get(code, 0) + 1
    reviewed = [r for r in rows if r.get("review_outcome")]
    return {
        "situations": len(rows),
        "with_a_suggestion": len(suggested),
        "decided": len(decided),
        "followed": len(followed),
        "overridden": len(overridden),
        "acceptance_rate": _share(len(followed), len(followed) + len(overridden)),
        "closed_false": sum(1 for r in rows if r.get("closed_as") == "FALSE_POSITIVE"),
        "false_positive_rate": _share(sum(1 for r in rows if r.get("closed_as") == "FALSE_POSITIVE"), len(rows)),
        "override_reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_suggested_action": dict(sorted(by_action.items(), key=lambda kv: (-kv[1]["suggested"], kv[0]))),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1]["situations"], kv[0]))),
        "reviewed": len(reviewed),
        "review_outcomes": tally("review_outcome"),
        "review_of_assessment": tally("review_assessment"),
        "review_of_recommendation": tally("review_recommendation"),
        "use": USE,
    }


def to_csv(rows: list[Mapping]) -> str:
    """The dataset as CSV: a header, then one line a situation."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        line = []
        for column in COLUMNS:
            value = r.get(column)
            if isinstance(value, (list, tuple)):
                value = " ".join(str(v) for v in value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            line.append("" if value is None else value)
        writer.writerow(line)
    return out.getvalue()


# ─── Reading and writing ─────────────────────────────────────────────────────

async def record(db: AsyncSession, *, situation: Mapping, outcome: str, assessment_verdict: str | None,
                 recommendation_verdict: str | None, note: str | None, user_id, role_id: int,
                 request_id: str | None) -> dict:
    """One person's review of one closed situation. The caller commits."""
    row = (await db.execute(text("""
        INSERT INTO security_feedback
               (tenant_id, situation_id, assessment_id, outcome, assessment_verdict, recommendation_verdict, note,
                reviewer_user_id, reviewer_role, request_id)
        VALUES (current_setting('app.current_tenant')::uuid, :s, :a, :outcome, :av, :rv, :note, CAST(:u AS uuid),
                :role, :rid)
        RETURNING id, reviewed_at
    """), {"s": situation["id"], "a": situation.get("assessment_id"), "outcome": outcome, "av": assessment_verdict,
           "rv": recommendation_verdict, "note": (note or "").strip() or None, "u": str(user_id), "role": role_id,
           "rid": request_id})).mappings().one()
    return dict(row)


async def for_situation(db: AsyncSession, situation_id) -> list[dict]:
    """The reviews of one situation, oldest first, each with who made it."""
    rows = (await db.execute(text("""
        SELECT f.id, f.outcome, f.assessment_verdict, f.recommendation_verdict, f.note, f.reviewed_at,
               f.reviewer_user_id AS user_id, u.full_name AS name, f.reviewer_role AS role_id
          FROM security_feedback f LEFT JOIN users u ON u.id = f.reviewer_user_id
         WHERE f.situation_id = :s ORDER BY f.reviewed_at, f.id
    """), {"s": situation_id})).mappings().all()
    return [{**dict(r), "outcome_words": OUTCOMES.get(r["outcome"], r["outcome"])} for r in rows]


async def dataset(db: AsyncSession, allowed: list[str] | None, *, since: datetime, until: datetime,
                  site_id: Any = None, limit: int = MAX_ROWS) -> list[dict]:
    """One row for each situation closed in the period that the caller may
    see: what was suggested, what was decided first, how it was closed, and
    the latest review if there is one. Roles, codes, numbers and times only."""
    params: dict = {"since": since, "until": until, "limit": limit}
    where = ["s.closed_at >= :since", "s.closed_at <= :until"]
    if allowed is not None:
        where.append("s.site_id = ANY(CAST(:sites AS uuid[]))")
        params["sites"] = [str(x) for x in allowed]
    if site_id is not None:
        where.append("s.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"""
        SELECT s.situation_number, s.site_id, s.started_at, s.closed_at, s.event_count, s.source_types,
               s.decision_status AS closed_as,
               la.kind, la.risk_level AS last_risk_level, la.risk_score AS last_risk_score,
               la.engine_version, la.sequence AS assessments,
               fa.risk_level AS first_risk_level, fa.risk_score AS first_risk_score,
               rec.action AS suggested_action, rec.confidence AS suggestion_confidence,
               fd.action AS first_decision, fd.basis AS first_decision_basis,
               fd.reason_code AS first_decision_reason, fd.actor_role AS first_decision_role,
               EXTRACT(EPOCH FROM (fd.decided_at - s.started_at))::int AS seconds_to_first_decision,
               (SELECT count(*) FROM security_decisions d WHERE d.situation_id = s.id) AS decisions,
               (SELECT count(*) FROM security_decisions d WHERE d.situation_id = s.id AND d.basis = 'OVERRIDE')
                   AS overrides,
               cd.reason_code AS closing_reason,
               fb.outcome AS review_outcome, fb.assessment_verdict AS review_assessment,
               fb.recommendation_verdict AS review_recommendation,
               (SELECT count(*) FROM security_feedback f WHERE f.situation_id = s.id) AS reviews
          FROM security_situations s
          LEFT JOIN security_assessments la ON la.id = s.assessment_id
          LEFT JOIN LATERAL (SELECT a.id, a.risk_level, a.risk_score FROM security_assessments a
                              WHERE a.situation_id = s.id ORDER BY a.sequence LIMIT 1) fa ON TRUE
          -- What was put first, among the steps that could be taken, for the first assessment.
          LEFT JOIN LATERAL (SELECT r.action, r.confidence FROM security_recommendations r
                              WHERE r.assessment_id = fa.id AND r.available ORDER BY r.rank LIMIT 1) rec ON TRUE
          LEFT JOIN LATERAL (SELECT d.action, d.basis, d.reason_code, d.actor_role, d.decided_at
                               FROM security_decisions d WHERE d.situation_id = s.id
                              ORDER BY d.decided_at, d.id LIMIT 1) fd ON TRUE
          LEFT JOIN security_decisions cd ON cd.id = s.last_decision_id
          LEFT JOIN LATERAL (SELECT f.outcome, f.assessment_verdict, f.recommendation_verdict
                               FROM security_feedback f WHERE f.situation_id = s.id
                              ORDER BY f.reviewed_at DESC, f.id DESC LIMIT 1) fb ON TRUE
         WHERE {' AND '.join(where)}
         ORDER BY s.closed_at, s.id
         LIMIT :limit
    """), params)).mappings().all()
    out = []
    for r in rows:
        row = {column: r[column] for column in COLUMNS}
        row["suggestion_confidence"] = (float(row["suggestion_confidence"])
                                        if row["suggestion_confidence"] is not None else None)
        row["source_types"] = list(row["source_types"] or [])
        out.append(row)
    return out
