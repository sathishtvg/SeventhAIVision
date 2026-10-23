"""The nightly removal of a deactivated person's registered face.

Design: docs/plans/2026-10-23-staff-face-registration-design.md, section 3. A face
is biometric data kept only to confirm a person at check-in; once their account
is deactivated it has no use, so the daily cycle removes the photo and the face
code, and writes each removal to the organisation's audit log. The consent record
(pdpa_consents) is kept: it is the record that consent was given.

Runs on the superuser session, as the other retention jobs do, so every
organisation is in one pass; each statement names the face's own organisation.
The photo goes after the row is committed: a crash between the two leaves a file
nothing refers to, which the next registration's folder clean-up does not need.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import staff_faces
from app.services.audit import write_audit_log

logger = logging.getLogger(__name__)

WHY = "The account was deactivated"
REMOVED_BY = "The scheduler, once a day"


async def run(db: AsyncSession) -> dict:
    due = (await db.execute(text("""
        SELECT f.id, f.tenant_id, f.user_id, f.photo_path, f.source, u.full_name, u.email
          FROM staff_faces f
          JOIN users u ON u.id = f.user_id
         WHERE u.is_active = FALSE
         ORDER BY f.tenant_id, f.id
    """))).mappings().all()
    await db.commit()
    out = {"removed": 0, "failed": 0}
    for f in due:
        try:
            await db.execute(text("DELETE FROM staff_faces WHERE id = :id AND tenant_id = :t"),
                             {"id": f["id"], "t": f["tenant_id"]})
            await write_audit_log(db, tenant_id=str(f["tenant_id"]), user_id=None, action="face.removed",
                                  resource_type="staff_face", resource_id=str(f["user_id"]),
                                  detail={"why": WHY, "person": f["full_name"] or f["email"], "source": f["source"]})
            await db.commit()
        except Exception:  # noqa: BLE001 - one person's trouble is their own
            await db.rollback()
            logger.exception("face removal failed for user %s of tenant %s", f["user_id"], f["tenant_id"])
            out["failed"] += 1
            continue
        staff_faces.remove_photo(f["photo_path"])
        out["removed"] += 1
    return out
