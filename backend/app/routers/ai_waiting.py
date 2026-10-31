"""What the AI is waiting on this person to decide: the corner card on every screen.

  GET /api/v1/ai/waiting   signed in   this person's Confirm cards left waiting, and the warnings they may decide

Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md, agreed
with the owner on 31 October 2026.

NEVER REFUSED. The web and the phone ask once a minute, on every screen. A person
the AI module is not for gets `assistant: false` and empty lists, not a 403 the
app would have to keep showing. Each list has its own gates:

  - `actions`: the assistant is the person's (the three gates, as
    routers/ai_assistant._gate), and only their own cards: PENDING, not expired,
    and with the reply that made them stored (the rule _decidable applies, so a
    card is never offered before it can be confirmed). Soonest to expire first.
  - `warnings`: the person may decide warnings (`ai:insights:decide`), only for
    the features and sites GET /ai/insights shows them, open, and not about
    something already over. Most important first; the rest are a count.

DECIDING IS NOT HERE. The card calls the endpoints the chat and AI Insights call,
so permissions, the re-checks at Confirm and the audit log are theirs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.routers.ai_insights import _SELECT, _my_features, _out
from app.services import ai_actions, ai_module

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

T = "current_setting('app.current_tenant')::uuid"
#: The card shows one at a time; the others are counted. AI Insights lists every one.
WARNINGS_SHOWN = 20


async def may_decide(db: AsyncSession, role_id: int) -> bool:
    """Whether the role may accept or dismiss warnings (custom roles included)."""
    return (await db.execute(text(
        "SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        "WHERE rp.role_id = :r AND p.code = 'ai:insights:decide'"), {"r": role_id})).first() is not None


@router.get("/waiting")
async def waiting(db: AsyncSession = Depends(get_db_with_tenant),
                  token: TokenPayload = Depends(get_token_payload),
                  allowed_sites: list[str] | None = Depends(get_allowed_site_ids)):
    out: dict = {"assistant": False, "actions": [], "warnings": [], "counts": {"actions": 0, "warnings": 0}}
    if token.via_api_key:
        return out  # a key is not a person: there is nobody to ask
    licensed = ai_module.licensed_features(await ai_module.load_licence(db))
    if not licensed:
        return out
    settings = await ai_module.load_settings(db)
    out["assistant"] = ai_module.usable_by(ai_module.FEATURES["assistant"], settings.get("assistant") or {},
                                           licensed, token.role_id)
    if out["assistant"]:
        rows = (await db.execute(text(f"""
            SELECT a.* FROM ai_pending_actions a
             WHERE a.tenant_id = {T} AND a.user_id = CAST(:u AS uuid)
               AND a.status = 'PENDING' AND a.expires_at > now()
               AND EXISTS (SELECT 1 FROM ai_messages m
                            WHERE m.conversation_id = a.conversation_id AND m.role = 'assistant'
                              AND m.created_at >= a.created_at)
             ORDER BY a.expires_at, a.created_at
        """), {"u": token.user_id})).mappings().all()
        out["actions"] = [ai_actions.card(dict(r)) for r in rows]
        out["counts"]["actions"] = len(out["actions"])

    keys = await _my_features(db, token.role_id) if await may_decide(db, token.role_id) else []
    if keys:
        params: dict = {"keys": keys, "n": WARNINGS_SHOWN}
        where = ["a.feature_key = ANY(:keys)", f"a.tenant_id = {T}", "a.status = 'OPEN'",
                 "(a.valid_until IS NULL OR a.valid_until > now())"]
        scope = site_scope_clause(allowed_sites, "a.site_id", params)
        if scope:
            where.append(scope)
        clause = " AND ".join(where)
        rows = (await db.execute(text(
            _SELECT + " WHERE " + clause + " ORDER BY a.score DESC, a.created_at DESC LIMIT :n"),
            params)).mappings().all()
        total = (await db.execute(text("SELECT count(*) FROM ai_suggestions a WHERE " + clause), params)).scalar()
        out["warnings"] = [_out(dict(r)) for r in rows]
        out["counts"]["warnings"] = int(total or 0)
    return out
