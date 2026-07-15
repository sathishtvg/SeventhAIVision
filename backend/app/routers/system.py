"""System info endpoints — version, health status, and backup management.

Health and version require no auth. Backup endpoints require settings:write (admin+).
"""

import asyncio
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.backup import list_backups
from app.core.backup import run_database_backup as _run_backup
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.dependencies.permissions import require_permission

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/version")
async def get_version() -> dict[str, str]:
    return {
        "version": settings.APP_VERSION,
        "git_sha": settings.GIT_SHA,
        "build_date": settings.BUILD_DATE,
    }


@router.get("/health")
async def get_health(request: Request) -> dict:
    """Per-service health check for the dashboard system health strip."""
    results: dict[str, str] = {}

    # API itself is responding (trivially healthy if we reach here)
    results["api"] = "healthy"

    # PostgreSQL
    try:
        t0 = time.monotonic()
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        results["postgres"] = "healthy"
        results["postgres_latency_ms"] = str(round((time.monotonic() - t0) * 1000))
    except Exception as exc:
        results["postgres"] = f"unhealthy: {type(exc).__name__}"

    # Redis
    try:
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            t0 = time.monotonic()
            await redis.ping()
            results["redis"] = "healthy"
            results["redis_latency_ms"] = str(round((time.monotonic() - t0) * 1000))
        else:
            results["redis"] = "unknown"
    except Exception as exc:
        results["redis"] = f"unhealthy: {type(exc).__name__}"

    # AI workers — checked via Redis stream consumer-group lag
    AI_MODULES = [
        "lpr", "face", "intrusion",
        "ppe", "crowd", "fire_smoke", "weapon", "behavior",
        "tampering", "abandoned", "fall",
    ]
    CONSUMER_GROUPS = {
        "lpr": "lpr_workers",
        "face": "face_workers",
        "intrusion": "intrusion_workers",
        "ppe": "ppe_workers",
        "crowd": "crowd_workers",
        "fire_smoke": "fire_smoke_workers",
        "weapon": "weapon_workers",
        "behavior": "behavior_workers",
        "tampering": "tampering_workers",
        "abandoned": "abandoned_workers",
        "fall": "fall_workers",
    }
    try:
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            info = await redis.xinfo_groups("frame_jobs")
            group_map = {g["name"]: g for g in info}
            for module in AI_MODULES:
                grp = CONSUMER_GROUPS[module]
                if grp in group_map:
                    lag = group_map[grp].get("lag", 0)
                    consumers = group_map[grp].get("consumers", 0)
                    if consumers == 0:
                        results[f"worker_{module}"] = "offline"
                    elif lag > 1000:
                        results[f"worker_{module}"] = f"lagging ({lag})"
                    else:
                        results[f"worker_{module}"] = "healthy"
                else:
                    results[f"worker_{module}"] = "unknown"
        else:
            for module in AI_MODULES:
                results[f"worker_{module}"] = "unknown"
    except Exception:
        for module in AI_MODULES:
            results.setdefault(f"worker_{module}", "unknown")

    overall = "healthy" if all(v == "healthy" for k, v in results.items() if not k.endswith("_latency_ms")) else "degraded"
    return {"status": overall, "services": results}


# ── Backup endpoints (admin-only) ─────────────────────────────────────────────

@router.get("/backups", dependencies=[Depends(require_permission("settings:write"))])
async def get_backups() -> dict:
    """List all available daily database backup files."""
    return {"backups": list_backups()}


@router.post("/backup", dependencies=[Depends(require_permission("settings:write"))])
async def trigger_backup(background_tasks: BackgroundTasks) -> dict:
    """Trigger an on-demand database backup (runs asynchronously in background).

    Returns immediately with `{"status": "started"}`. Poll GET /api/v1/system/backups
    to see the new file once the dump completes (typically < 30 s).
    """
    async def _do_backup() -> None:
        try:
            await _run_backup()
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).error("on-demand backup failed: %s", exc)

    background_tasks.add_task(_do_backup)
    return {"status": "started", "message": "Backup running in background. Check GET /api/v1/system/backups shortly."}

