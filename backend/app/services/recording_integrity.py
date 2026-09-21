"""Making recording_policies.verify_checksums mean what it says.

THE SETTING PROMISED SOMETHING NOTHING DELIVERED. verify_checksums defaults to
TRUE, is stored per site, and is shown to whoever configures that site. It
appeared in the application exactly once, in a SELECT column list. No code read
it, and `recordings` carried no checksum, so there was never anything to verify.
An operator saw integrity checking enabled on every site and none had ever
happened.

That is worse than the feature being absent. An absent control is one you know
to arrange yourself; a control that reports itself as active is one you rely on.

WHAT THIS DOES. A hash is taken when a recording is finalised and the file is
complete, and a sweep re-reads files and compares, for the sites whose policy
asks for it. A site that has turned verify_checksums off is skipped and says so,
rather than being quietly verified anyway -- a setting that is ignored in the
other direction is the same bug wearing a different hat.

WHAT IT DOES NOT DO, and the API says so too: it detects a file changed since it
was written -- truncation, corruption, replacement. It does not detect somebody
with database access changing the row and the file together. That needs a chain
like audit_logs, or an anchor outside the system.

HASHING NEVER COSTS A RECORDING. A segment that cannot be hashed keeps its row
and leaves the checksum NULL. Losing footage because the integrity feature
stumbled would be a worse outcome than the footage being unverifiable.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PASSED = "PASSED"
MISMATCH = "MISMATCH"
FILE_MISSING = "FILE_MISSING"

#: Video files are large; read them in chunks rather than whole. A sweep that
#: loads a segment into memory is a sweep somebody turns off.
_CHUNK = 4 * 1024 * 1024

#: Recordings root, which is NOT the evidence root -- clips live on their own
#: volume, mounted separately (see RECORDINGS_ROOT in ingestion_main). Hashing
#: an evidence path here would silently read the wrong file.
RECORDINGS_ROOT = os.environ.get("RECORDINGS_ROOT", "/data/recordings")


def _hash_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("recording integrity: cannot read %s", path)
        return None


async def hash_file(path: Path) -> str | None:
    """Hash off the event loop: this reads megabytes and the loop is shared
    with every camera's capture task."""
    return await asyncio.to_thread(_hash_file, path)


async def record_checksum(db: AsyncSession, recording_id: str,
                          file_path: str | Path) -> str | None:
    """Store the hash of a finished recording. Never raises.

    `file_path` is taken as given -- the capture task holds an absolute path
    and the sweep holds the relative one stored on the row, so resolving it
    here would be right for one caller and wrong for the other.

    Called at finalisation, when the file is complete. Deliberately tolerant:
    the recording is the valuable thing and an integrity feature that could
    lose one would not deserve to run.
    """
    try:
        digest = await hash_file(Path(file_path))
        if digest is None:
            logger.warning("recording integrity: no checksum stored for %s "
                           "(file unreadable at finalisation)", recording_id)
            return None
        await db.execute(text("""
            UPDATE recordings SET checksum_sha256 = :sum
             WHERE id = CAST(:id AS uuid)
        """), {"sum": digest, "id": str(recording_id)})
        return digest
    except Exception:
        logger.exception("recording integrity: could not record a checksum "
                         "for %s; the recording itself is unaffected",
                         recording_id)
        return None


async def verify_recent(db: AsyncSession, *, limit_per_tenant: int = 100) -> dict:
    """Re-read recordings and compare, for sites whose policy asks for it.

    Reads per tenant with the GUC set AND tenant_id in the WHERE clause. The
    policy alone is not enough: under a connection that bypasses RLS nothing
    filters, and this loop would verify every tenant's recordings once per
    tenant.
    """
    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    checked, passed, mismatched, missing, skipped = 0, 0, 0, 0, 0

    for tenant_id in tenants:
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(tenant_id)})

            # Least recently verified first, so a large estate is covered over
            # successive runs instead of re-checking the same newest rows.
            #
            # The policy join is what makes the setting real. A site with
            # verify_checksums FALSE is left alone -- honouring the flag in one
            # direction only would be the same bug in reverse.
            rows = (await db.execute(text("""
                SELECT r.id, r.file_path, r.checksum_sha256, r.site_id,
                       COALESCE(p.verify_checksums, TRUE) AS wanted
                  FROM recordings r
                  LEFT JOIN recording_policies p
                         ON p.site_id = r.site_id AND p.is_active
                 WHERE r.tenant_id = CAST(:t AS uuid)
                   AND r.status = 'completed'
                   AND r.checksum_sha256 IS NOT NULL
                 ORDER BY r.checksum_verified_at NULLS FIRST
                 LIMIT :lim
            """), {"t": str(tenant_id), "lim": limit_per_tenant})).mappings().all()

            for r in rows:
                if not r["wanted"]:
                    # The site said no. Counted so the sweep can say how much it
                    # deliberately left alone, rather than looking idle.
                    skipped += 1
                    continue

                actual = await hash_file(Path(RECORDINGS_ROOT) / r["file_path"])
                if actual is None:
                    status = FILE_MISSING
                    missing += 1
                elif actual == r["checksum_sha256"]:
                    status = PASSED
                    passed += 1
                else:
                    status = MISMATCH
                    mismatched += 1

                checked += 1
                await db.execute(text("""
                    UPDATE recordings
                       SET checksum_status = :st, checksum_verified_at = :at
                     WHERE id = :id
                """), {"st": status, "at": datetime.now(timezone.utc),
                       "id": r["id"]})

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("recording integrity sweep failed for tenant %s",
                             tenant_id)

    if mismatched:
        # ERROR, not a counter. Recorded footage that no longer matches its hash
        # is either corruption or interference, and both want a person tonight.
        logger.error("recording integrity: %d recording(s) no longer match "
                     "their checksum", mismatched)

    return {"checked": checked, "passed": passed, "mismatched": mismatched,
            "files_missing": missing, "skipped_by_policy": skipped}
