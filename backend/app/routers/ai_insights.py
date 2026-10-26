"""The AI module's warnings, the decisions on them, and what each feature has learned.

  GET  /api/v1/ai/insights                     ai:insights:read    the warnings the caller may see
  POST /api/v1/ai/insights/{id}/decide         ai:insights:decide  accept or dismiss one
  GET  /api/v1/ai/learning                     ai:insights:read    what each feature has learned, and its bar
  POST /api/v1/ai/learning/{feature}/run       ai:configure        learn now and score now

WHO SEES A WARNING. All three gates (services/ai_module.usable_by): the licence,
the admin's switch, and the caller's role among the feature's roles. A
supervisor limited to some sites sees only the warnings of those sites, and none
without a site (fail-closed, as everywhere else).

A DECISION IS A RECORD, NOT AN ACTION. Accepting a warning does not move a shift
or call a guard: it says a person agreed and will act. Only an OPEN warning can be
decided, once; the decision is written to the audit log and is what the feature
learns from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.services import ai_late_risk, ai_module, ai_runner, ai_store
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
_READ = Depends(require_permission("ai:insights:read"))
_DECIDE = Depends(require_permission("ai:insights:decide"))
_CONFIGURE = Depends(require_permission("ai:configure"))


class DecisionBody(BaseModel):
    decision: Literal["ACCEPT", "DISMISS"]
    note: str | None = Field(None, max_length=1000)


async def _my_features(db: AsyncSession, role_id: int) -> list[str]:
    """The learning features this person may see warnings of: every gate open."""
    licensed = ai_module.licensed_features(await ai_module.load_licence(db))
    settings = await ai_module.load_settings(db)
    return [k for k, f in ai_module.FEATURES.items()
            if f.group == ai_module.LEARNING and k in ai_runner.FEATURES
            and ai_module.usable_by(f, settings.get(k) or {}, licensed, role_id)]


def _out(r: dict) -> dict:
    f = ai_module.FEATURES.get(r["feature_key"])
    return {
        "id": str(r["id"]), "feature_key": r["feature_key"], "feature_title": f.title if f else r["feature_key"],
        "subject_type": r["subject_type"], "title": r["title"], "detail": r["detail"],
        "score": float(r["score"]), "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
        "reasons": r["reasons"], "evidence": r["evidence"],
        "site_id": str(r["site_id"]) if r["site_id"] else None, "site_name": r["site_name"],
        "person_user_id": str(r["person_user_id"]) if r["person_user_id"] else None, "person_name": r["person_name"],
        "status": r["status"], "decided_by_name": r["decided_by_name"], "decided_at": r["decided_at"],
        "decision_note": r["decision_note"], "outcome": r["outcome"], "valid_until": r["valid_until"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


_SELECT = """
    SELECT a.*, si.name AS site_name, p.full_name AS person_name, d.full_name AS decided_by_name
      FROM ai_suggestions a
      LEFT JOIN sites si ON si.id = a.site_id
      LEFT JOIN users p ON p.id = a.person_user_id
      LEFT JOIN users d ON d.id = a.decided_by_user_id
"""


@router.get("/insights", dependencies=[_READ])
async def list_insights(
    feature: str | None = Query(None, max_length=40),
    status: Literal["OPEN", "DECIDED", "ALL"] = "OPEN",
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    mine = await _my_features(db, token.role_id)
    keys = [k for k in mine if feature in (None, k)]
    if not keys:
        return {"features": mine, "items": []}
    params: dict = {"keys": keys, "n": limit}
    where = ["a.feature_key = ANY(:keys)", "a.tenant_id = current_setting('app.current_tenant')::uuid"]
    if status == "OPEN":
        where.append("a.status = 'OPEN'")
    elif status == "DECIDED":
        where.append("a.status IN ('ACCEPTED', 'DISMISSED')")
    scope = site_scope_clause(allowed_sites, "a.site_id", params)
    if scope:
        where.append(scope)
    rows = (await db.execute(text(
        _SELECT + " WHERE " + " AND ".join(where) + " ORDER BY a.score DESC, a.created_at DESC LIMIT :n"),
        params)).mappings().all()
    return {"features": mine, "items": [_out(dict(r)) for r in rows]}


@router.post("/insights/{suggestion_id}/decide", dependencies=[_DECIDE])
async def decide_insight(
    suggestion_id: uuid.UUID, body: DecisionBody, request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(text(_SELECT + " WHERE a.id = :id"), {"id": suggestion_id})).mappings().first()
    # Not found and not yours read the same: a warning of a site or feature the
    # caller cannot see is not confirmed to exist.
    if row is None or row["feature_key"] not in await _my_features(db, token.role_id):
        raise HTTPException(404, "Warning not found")
    if allowed_sites is not None and (row["site_id"] is None or str(row["site_id"]) not in allowed_sites):
        raise HTTPException(404, "Warning not found")
    if row["status"] != "OPEN":
        raise HTTPException(409, f"This warning is already {row['status'].lower()}.")
    status = "ACCEPTED" if body.decision == "ACCEPT" else "DISMISSED"
    await db.execute(text("""
        UPDATE ai_suggestions
           SET status = :s, decided_by_user_id = CAST(:by AS uuid), decided_at = now(),
               decision_note = :note, updated_at = now()
         WHERE id = :id AND status = 'OPEN'
    """), {"s": status, "by": token.user_id, "note": (body.note or "").strip() or None, "id": suggestion_id})
    await write_audit_log(
        db, tenant_id=token.tenant_id, user_id=token.user_id,
        action="ai.insight.decide", resource_type="ai_suggestion", resource_id=str(suggestion_id),
        ip_address=_client_ip(request),
        detail={"feature": row["feature_key"], "decision": status, "score": float(row["score"])},
    )
    out = (await db.execute(text(_SELECT + " WHERE a.id = :id"), {"id": suggestion_id})).mappings().first()
    await db.commit()
    return _out(dict(out))


def _learned(key: str, model: dict | None) -> list[dict]:
    """What the model has learned matters most, in words, largest first."""
    if key != ai_late_risk.KEY:
        return []
    fitted = ai_store.fitted_of(model)
    if fitted is None:
        return []
    out = [{"factor": f, "title": ai_late_risk.FACTOR_TITLES.get(f, f), "weight": round(w, 3),
            "raises": w > 0} for f, w in zip(fitted.factors, fitted.weights)]
    out.sort(key=lambda x: abs(x["weight"]), reverse=True)
    return out


@router.get("/learning", dependencies=[_READ])
async def learning(db: AsyncSession = Depends(get_db_with_tenant),
                   token: TokenPayload = Depends(get_token_payload)):
    settings = await ai_module.load_settings(db)
    out = []
    for key in await _my_features(db, token.role_id):
        f, s = ai_module.FEATURES[key], settings[key]
        model = await ai_store.latest_model(db, key)
        threshold, why = await ai_runner.current_threshold(db, key, s["sensitivity"])
        counts = (await db.execute(text("""
            SELECT count(*) FILTER (WHERE status = 'OPEN') AS open,
                   count(*) FILTER (WHERE status = 'ACCEPTED') AS accepted,
                   count(*) FILTER (WHERE status = 'DISMISSED') AS dismissed,
                   count(*) FILTER (WHERE outcome = 'HAPPENED') AS came_true,
                   count(*) FILTER (WHERE outcome = 'DID_NOT_HAPPEN') AS did_not
              FROM ai_suggestions WHERE feature_key = :k AND created_at >= :since AND tenant_id = current_setting('app.current_tenant')::uuid
        """), {"k": key, "since": datetime.now(timezone.utc) - timedelta(days=30)})).mappings().first()
        out.append({
            "key": key, "title": f.title, "description": f.description,
            "sensitivity": s["sensitivity"], "learning_enabled": bool(s["learning_enabled"]),
            "threshold": threshold, "threshold_reason": why,
            "model": None if model is None else {
                "status": model["status"], "version": model["version"], "trained_at": model["trained_at"],
                "rows_used": model["rows_used"], "positives": model["positives"], "metrics": model["metrics"],
                "note": model["note"]},
            "learned": _learned(key, model),
            "last_30_days": dict(counts),
        })
    return {"features": out}


@router.post("/learning/{feature_key}/run", dependencies=[_CONFIGURE])
async def learn_now(feature_key: str, request: Request,
                    db: AsyncSession = Depends(get_db_with_tenant),
                    token: TokenPayload = Depends(get_token_payload)):
    """Refit now and score now, for this organisation and this feature: the hourly pass, at once."""
    if feature_key not in ai_runner.FEATURES:
        raise HTTPException(404, "No such learning feature")
    result = await ai_runner.run_tenant(db, token.tenant_id, force_train=True, only=feature_key)
    if feature_key not in result:
        raise HTTPException(422, "Switch the feature on in AI Settings first (it must also be in your licence).")
    await write_audit_log(db, tenant_id=token.tenant_id, user_id=token.user_id, action="ai.learning.run",
                          resource_type="ai_model", ip_address=_client_ip(request),
                          detail={"feature": feature_key, **result[feature_key]})
    await db.commit()
    return result[feature_key]
