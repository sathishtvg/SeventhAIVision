"""One phone push per person after an hourly AI pass raises warnings they may decide.

Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md, section 3.

AFTER THE PASS'S COMMIT, ON ITS OWN SESSION. services/ai_runner.run_all calls this
once an organisation's pass is committed, with the pass's own clock: each warning
the pass raised carries that time as created_at (the column defaults to the
transaction's now()), so there is no second clock to drift. Nothing here writes;
a push that fails is logged and changes nothing the pass decided.

WHO IS TOLD: the people who may decide each warning, by the rules GET /ai/insights
and GET /ai/waiting use: active, their role holding `ai:insights:decide` and among
the roles the admin chose for the warning's feature (licence and switch open),
and the warning's site theirs (dependencies/sites.get_allowed_site_ids: an admin
every site; anyone else their assigned sites, or every site with none assigned;
a warning without a site only to those with every site).

ONCE PER WARNING. Only warnings raised in this pass are counted. One that expired
and came back keeps its first time and is not pushed again; "Learn now" on AI
Insights pushes nothing. The app's minute check still shows every open warning.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import _CLIENT_ROLE, _UNRESTRICTED_ROLES, is_site_allowed
from app.services import ai_module

logger = logging.getLogger(__name__)

T = "current_setting('app.current_tenant')::uuid"
TITLE = "AI needs a decision"
#: What the phone is told the push is about; the app opens on its own minute check.
DATA = {"type": "ai_waiting"}


def words(n: int) -> str:
    return f"{n} new AI warning{'' if n == 1 else 's'} to decide. Open the app to accept or dismiss."


def _allowed_sites(role_id: int, assigned: list[str]) -> list[str] | None:
    """get_allowed_site_ids' rule for someone who is not signed in right now."""
    if role_id in _UNRESTRICTED_ROLES:
        return None
    if assigned:
        return assigned
    return [] if role_id == _CLIENT_ROLE else None


async def deciders(db: AsyncSession, since: datetime) -> dict[str, int]:
    """Person -> how many of the warnings raised since `since` they may decide.

    Inside the caller's transaction, with the organisation's scope already set."""
    new = (await db.execute(text(f"""
        SELECT feature_key, site_id FROM ai_suggestions
         WHERE tenant_id = {T} AND status = 'OPEN' AND created_at >= :since
    """), {"since": since})).mappings().all()
    if not new:
        return {}
    licensed = ai_module.licensed_features(await ai_module.load_licence(db))
    settings = await ai_module.load_settings(db)
    people = (await db.execute(text(f"""
        SELECT u.id, u.role_id,
               COALESCE(array_agg(us.site_id::text) FILTER (WHERE us.site_id IS NOT NULL), '{{}}') AS sites
          FROM users u
          LEFT JOIN user_sites us ON us.user_id = u.id
         WHERE u.tenant_id = {T} AND u.is_active IS TRUE
           AND u.role_id IN (SELECT rp.role_id FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                              WHERE p.code = 'ai:insights:decide')
         GROUP BY u.id, u.role_id
    """))).mappings().all()
    out: dict[str, int] = {}
    for p in people:
        allowed = _allowed_sites(p["role_id"], list(p["sites"] or []))
        n = sum(1 for w in new
                if w["feature_key"] in ai_module.FEATURES
                and ai_module.usable_by(ai_module.FEATURES[w["feature_key"]], settings.get(w["feature_key"]) or {},
                                        licensed, p["role_id"])
                and is_site_allowed(allowed, w["site_id"]))
        if n:
            out[str(p["id"])] = n
    return out


async def after_pass(session_factory, tenant_id: str, *, since: datetime, redis=None, send=None) -> dict[str, int]:
    """Tell each person their count. Returns person -> count for those with a registered phone. Never raises."""
    try:
        async with session_factory() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})
            counts = await deciders(db, since)
            await db.rollback()  # read only
        if not counts:
            return {}
        from app.services.response_notify import tokens_of  # noqa: PLC0415
        if send is None:
            from app.realtime.redis_listener import _send_expo_push as send  # noqa: PLC0415
        own = redis is None
        if own:
            from redis.asyncio import Redis  # noqa: PLC0415

            from app.core.config import settings  # noqa: PLC0415
            redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        sent: dict[str, int] = {}
        try:
            for user_id, n in counts.items():
                tokens = await tokens_of(redis, str(tenant_id), [user_id])
                if tokens:
                    await send(tokens, TITLE, words(n), dict(DATA))
                    sent[user_id] = n
        finally:
            if own:
                await redis.aclose()
        return sent
    except Exception:  # noqa: BLE001 - a push that fails changes nothing the pass decided
        logger.exception("ai module: the decision push failed for organisation %s", tenant_id)
        return {}
