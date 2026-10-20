"""Footage Review's 30 days: the daily removal of expired videos.

A video is removed, with its file and everything kept beside it, once its
`expires_at` has passed — RETENTION_DAYS after its upload began — unless an
evidence hold of kind FOOTAGE is in force on it (services/evidence_hold.py). An
upload that nobody has added to for ABANDONED_AFTER is removed too, whatever its
expiry: half a file is of no use to anybody and takes the room of a whole one.

Runs in the scheduler's daily cycle on the superuser session, as the other
retention jobs do, so every organisation is in one pass. Each statement names
the video's own organisation as well: nothing relies on a policy to scope it.
The file goes before the row, as the evidence purge does: a crash between the
two leaves a row whose file is missing, never a file nothing refers to.

Every removal is written to the organisation's audit log with why.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import footage
from app.services.audit import write_audit_log
from app.services.evidence_hold import FOOTAGE, not_held

logger = logging.getLogger(__name__)

REMOVED_BY = "The scheduler, once a day"
REASONS = {"EXPIRED": "Its 30 days were up", "ABANDONED": "Its upload was not finished"}


async def run(db: AsyncSession, now: datetime | None = None) -> dict:
    """Remove what is due, every organisation, one video at a time."""
    now = now or datetime.now(timezone.utc)
    due = (await db.execute(text(f"""
        SELECT v.id, v.tenant_id, v.title, v.storage_path, v.s3_upload_id, v.status, v.created_at, v.expires_at,
               CASE WHEN v.expires_at <= :now THEN 'EXPIRED' ELSE 'ABANDONED' END AS why
          FROM footage_videos v
         WHERE (v.expires_at <= :now OR (v.status = 'UPLOADING' AND v.updated_at <= :abandoned))
           AND {not_held(FOOTAGE, 'v.id')}
         ORDER BY v.expires_at, v.id
    """), {"now": now, "abandoned": now - footage.ABANDONED_AFTER})).mappings().all()
    await db.commit()
    out = {"removed": 0, "failed": 0}
    for v in due:
        try:
            await asyncio.to_thread(footage.remove_everything, footage.storage_dir(v["tenant_id"], v["id"]),
                                    v["storage_path"], v["s3_upload_id"])
            await db.execute(text("DELETE FROM footage_videos WHERE id = :id AND tenant_id = :t"),
                             {"id": v["id"], "t": v["tenant_id"]})
            await write_audit_log(db, tenant_id=str(v["tenant_id"]), user_id=None, action="footage.removed",
                                  resource_type="footage_video", resource_id=str(v["id"]),
                                  detail={"why": v["why"], "words": REASONS[v["why"]], "title": v["title"],
                                          "status": v["status"], "uploaded_at": v["created_at"].isoformat(),
                                          "expires_at": v["expires_at"].isoformat()})
            await db.commit()
        except Exception:  # noqa: BLE001 — one video's trouble is its own
            await db.rollback()
            logger.exception("footage retention failed for video %s of tenant %s", v["id"], v["tenant_id"])
            out["failed"] += 1
            continue
        out["removed"] += 1
    return out
