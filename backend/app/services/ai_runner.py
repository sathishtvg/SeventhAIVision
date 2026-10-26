"""The AI module's hourly pass: train what is due, then score what is coming, for each organisation.

Called from the scheduler (scheduler_main.py), and by "Learn now" on the AI
Insights page for one organisation.

ONLY WHERE ALL THE GATES ARE OPEN: the licence includes the feature, the
feature is built, and the organisation's admin has switched it on.

ONE SESSION PER ORGANISATION. The tenant setting is transaction-local, so each
organisation gets its own session, its own transaction and its own commit; one
organisation's failure is logged and rolled back without touching the others.

RETRAINING. A model is refitted when it is more than 20 hours old. With the
admin's "keeps learning" switched off, the last model is kept and only scored
with; an organisation that has never had one gets one first model.

THE BAR IS WORKED OUT EVERY PASS, from the admin's current sensitivity and the
recent decisions, so a changed setting takes effect within the hour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_late_risk, ai_learning, ai_module, ai_overtime_fatigue, ai_store

logger = logging.getLogger(__name__)

#: The feature key -> (its module, the bars for its scale or None for chance).
FEATURES = {
    ai_late_risk.KEY: (ai_late_risk, None, 0.9),
    ai_overtime_fatigue.KEY: (ai_overtime_fatigue, ai_overtime_fatigue.BASES, 1.2),
}
RETRAIN_AFTER = timedelta(hours=20)


async def current_threshold(db: AsyncSession, key: str, sensitivity: str) -> tuple[float, str]:
    _, bases, cap = FEATURES[key]
    return ai_learning.threshold_for(sensitivity, await ai_store.recent_decisions(db, key), bases=bases, cap=cap)


async def run_tenant(db: AsyncSession, tenant_id: str, *, now: datetime | None = None,
                     force_train: bool = False, only: str | None = None) -> dict:
    """One organisation's pass, inside the caller's transaction (the caller commits)."""
    now = now or datetime.now(timezone.utc)
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})
    licensed = ai_module.licensed_features(await ai_module.load_licence(db), now)
    settings = await ai_module.load_settings(db)
    out: dict = {}
    for key, (module, _, _) in FEATURES.items():
        if only and key != only:
            continue
        feature, s = ai_module.FEATURES[key], settings.get(key)
        if key not in licensed or not feature.ready or not s or not s["enabled"]:
            continue
        model = await ai_store.latest_model(db, key)
        stale = model is None or model["trained_at"] < now - RETRAIN_AFTER
        if model is None or (s["learning_enabled"] and (force_train or stale)):
            model = await module.train(db, sensitivity=s["sensitivity"], now=now)
        threshold, _ = await current_threshold(db, key, s["sensitivity"])
        counts = await module.score(db, model=model, threshold=threshold, site_ids=s["site_ids"], now=now)
        out[key] = {"model": model["status"], "version": model["version"], **counts}
    return out


async def run_all(session_factory, *, now: datetime | None = None) -> dict:
    """Every active organisation with the module. Returns what was done, by organisation."""
    async with session_factory() as db:
        tenants = (await db.execute(text("SELECT id FROM tenants WHERE is_active"))).scalars().all()
    done: dict = {}
    for tid in tenants:
        async with session_factory() as db:
            try:
                result = await run_tenant(db, str(tid), now=now)
                await db.commit()
                if result:
                    done[str(tid)] = result
            except Exception:
                await db.rollback()
                logger.exception("ai module: the pass failed for organisation %s", tid)
    return done
