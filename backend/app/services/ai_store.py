"""Where the AI module keeps what it learned and what it suggested (ai_models, ai_suggestions).

Every function runs inside the caller's transaction with the organisation's
tenant setting already in force; row-level security keeps each organisation's
rows its own. Nothing here commits.

A DECIDED SUGGESTION IS NEVER REOPENED OR RESCORED. Once a person has accepted or
dismissed a warning, a later run that scores the same subject leaves the row as
the person left it: the decision is the record, and it is what the feature
learns from.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_learning import Fitted

#: A stable id for a subject that is not one row (a guard's month, a guard's week).
SUBJECT_NAMESPACE = uuid.UUID("5d0c1e8a-6f3b-4c1e-9a1d-7e2f0b4a9c11")


def subject_id(*parts: object) -> uuid.UUID:
    return uuid.uuid5(SUBJECT_NAMESPACE, ":".join(str(p) for p in parts))


async def latest_model(db: AsyncSession, feature_key: str) -> dict | None:
    row = (await db.execute(text("""
        SELECT id, version, status, trained_at, rows_used, positives, fitted, metrics,
               threshold, threshold_reason, note
          FROM ai_models WHERE feature_key = :k AND tenant_id = current_setting('app.current_tenant')::uuid
         ORDER BY version DESC LIMIT 1
    """), {"k": feature_key})).mappings().first()
    return dict(row) if row else None


def fitted_of(model: dict | None) -> Fitted | None:
    if not model or model["status"] != "READY" or not model["fitted"]:
        return None
    return Fitted.from_json(model["fitted"])


async def save_model(db: AsyncSession, feature_key: str, *, status: str, rows_used: int, positives: int,
                     fitted: dict | None, metrics: dict, threshold: float | None,
                     threshold_reason: str | None, note: str | None) -> dict:
    """A new version. Old versions are kept: they are how a model's history is told."""
    row = (await db.execute(text("""
        INSERT INTO ai_models (tenant_id, feature_key, version, status, rows_used, positives,
                               fitted, metrics, threshold, threshold_reason, note)
        VALUES (current_setting('app.current_tenant')::uuid, CAST(:k AS varchar),
                COALESCE((SELECT max(version) FROM ai_models
                          WHERE feature_key = CAST(:k AS varchar) AND tenant_id = current_setting('app.current_tenant')::uuid), 0) + 1,
                :status, :rows, :pos, CAST(:fitted AS jsonb), CAST(:metrics AS jsonb),
                :thr, :why, :note)
        RETURNING id, version, status, trained_at, rows_used, positives, fitted, metrics,
                  threshold, threshold_reason, note
    """), {"k": feature_key, "status": status, "rows": rows_used, "pos": positives,
           "fitted": json.dumps(fitted) if fitted is not None else None, "metrics": json.dumps(metrics),
           "thr": threshold, "why": threshold_reason, "note": note})).mappings().first()
    return dict(row)


async def recent_decisions(db: AsyncSession, feature_key: str, limit: int = 20) -> list[dict]:
    rows = (await db.execute(text("""
        SELECT status, outcome FROM ai_suggestions
         WHERE feature_key = :k AND status IN ('ACCEPTED', 'DISMISSED') AND tenant_id = current_setting('app.current_tenant')::uuid
         ORDER BY decided_at DESC LIMIT :n
    """), {"k": feature_key, "n": limit})).mappings().all()
    return [dict(r) for r in rows]


async def upsert_open(db: AsyncSession, feature_key: str, *, subject_type: str, subject: uuid.UUID,
                      site_id, person_user_id, title: str, detail: str | None, score: float,
                      threshold: float | None, reasons: list[dict], evidence: dict, model_id,
                      valid_until: datetime | None) -> str:
    """Raise or refresh a warning. Returns 'new', 'refreshed' or 'decided' (left as the person left it)."""
    row = (await db.execute(text("""
        INSERT INTO ai_suggestions
            (tenant_id, feature_key, subject_type, subject_id, site_id, person_user_id, title, detail,
             score, threshold, reasons, evidence, model_id, valid_until)
        VALUES (current_setting('app.current_tenant')::uuid, :k, :st, :sid, :site, :person, :title, :detail,
                :score, :thr, CAST(:reasons AS jsonb), CAST(:evidence AS jsonb), :model, :until)
        ON CONFLICT (tenant_id, feature_key, subject_type, subject_id) DO UPDATE SET
            title = EXCLUDED.title, detail = EXCLUDED.detail, score = EXCLUDED.score,
            threshold = EXCLUDED.threshold, reasons = EXCLUDED.reasons, evidence = EXCLUDED.evidence,
            model_id = EXCLUDED.model_id, valid_until = EXCLUDED.valid_until,
            site_id = EXCLUDED.site_id, person_user_id = EXCLUDED.person_user_id,
            -- An expired warning whose risk is back is open again; a decided one is not touched.
            status = 'OPEN', updated_at = now()
         WHERE ai_suggestions.status IN ('OPEN', 'EXPIRED')
        RETURNING (xmax = 0) AS inserted
    """), {"k": feature_key, "st": subject_type, "sid": subject, "site": site_id, "person": person_user_id,
           "title": title, "detail": detail, "score": round(min(score, 9.9999), 4), "thr": threshold,
           "reasons": json.dumps(reasons), "evidence": json.dumps(evidence, default=str), "model": model_id,
           "until": valid_until})).first()
    if row is None:
        return "decided"
    return "new" if row[0] else "refreshed"


async def expire_open_except(db: AsyncSession, feature_key: str, subject_type: str,
                             keep: set[uuid.UUID], scope: list[uuid.UUID], reason: str) -> int:
    """Close the open warnings among `scope` that this run did not raise again."""
    gone = [s for s in scope if s not in keep]
    if not gone:
        return 0
    r = await db.execute(text("""
        UPDATE ai_suggestions SET status = 'EXPIRED', decision_note = :why, updated_at = now()
         WHERE feature_key = :k AND subject_type = :st AND status = 'OPEN' AND tenant_id = current_setting('app.current_tenant')::uuid
           AND subject_id = ANY(CAST(:ids AS uuid[]))
    """), {"k": feature_key, "st": subject_type, "ids": [str(s) for s in gone], "why": reason})
    return r.rowcount or 0


async def expire_past(db: AsyncSession, feature_key: str, now: datetime) -> int:
    r = await db.execute(text("""
        UPDATE ai_suggestions SET status = 'EXPIRED', updated_at = now(),
               decision_note = COALESCE(decision_note, 'Nobody decided before it was over.')
         WHERE feature_key = :k AND status = 'OPEN' AND valid_until IS NOT NULL AND valid_until < :now
           AND tenant_id = current_setting('app.current_tenant')::uuid
    """), {"k": feature_key, "now": now})
    return r.rowcount or 0
