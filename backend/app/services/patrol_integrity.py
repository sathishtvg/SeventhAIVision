"""Checking that patrol evidence is still the evidence that was recorded.

A CHECKSUM NOTHING EVER COMPARES IS JUST A NUMBER. Every patrol snapshot on this
system carries a SHA-256, written at capture. Not one of them has ever been read
back and compared against the file. If a snapshot were truncated by a full
volume, corrupted on disk, or replaced, the database would still hold a hash
that says otherwise and no part of the application would notice -- the hash is
computed, stored, returned in API responses, and never used to decide anything.

That is what this module is for. It re-reads the file and compares.

WHAT IT PROVES, AND WHAT IT DOES NOT. This detects a file that has changed since
it was written: truncation, corruption, a replaced JPEG, a report regenerated
over the top. It does NOT detect somebody with database access who changes the
row and the file together -- for that the hash has to be chained, the way
audit_logs chains prev_hash, or anchored outside the system. Saying "verified"
about the first and letting it be heard as the second would be worse than saying
nothing, so the result names which question it answered.

MISSING IS NOT THE SAME AS FAILED, AND NEITHER IS THE SAME AS PASSED. A report
generated before 0121 has no checksum and is unverifiable; that is a gap in
coverage, not evidence of tampering, and collapsing the two would either cry
wolf or hide a real mismatch in a crowd of old rows.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

PASSED = "PASSED"          # file read, hash matches what was recorded
MISMATCH = "MISMATCH"      # file read, hash differs -- the loud one
FILE_MISSING = "FILE_MISSING"     # the row says there is a file; there is not
NOT_RECORDED = "NOT_RECORDED"     # no checksum was ever stored for this row

#: Read in chunks rather than whole. A month of patrol PDFs is hundreds of
#: megabytes and a verification sweep that loads each one entirely is a sweep
#: that gets switched off.
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Check:
    kind: str          # "snapshot" | "report"
    reference: str     # the row id
    label: str         # camera name or report format, for a human
    status: str
    expected: str | None = None
    actual: str | None = None

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "reference": self.reference,
             "label": self.label, "status": self.status}
        # The hashes are only interesting when they disagree; printing them on
        # every passing row buries the one that matters.
        if self.status == MISMATCH:
            d["expected_sha256"] = self.expected
            d["actual_sha256"] = self.actual
        return d


def sha256_of(path: Path) -> str | None:
    """The file's hash, or None when it is not there to be read."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        # An unreadable file is not a mismatch -- a permissions problem or a
        # disconnected mount is an operational fault, and reporting it as
        # tampering would send somebody looking for the wrong thing.
        logger.exception("integrity: could not read %s", path)
        return None


def classify(expected: str | None, actual: str | None) -> str:
    if not expected:
        return NOT_RECORDED
    if actual is None:
        return FILE_MISSING
    return PASSED if actual == expected else MISMATCH


async def verify_session(db: AsyncSession, session_id: str) -> dict:
    """Every snapshot and report belonging to one patrol.

    Tenant scope comes from RLS, so this only ever reads the caller's own.
    """
    root = Path(settings.EVIDENCE_ROOT)
    checks: list[Check] = []

    cameras = (await db.execute(text("""
        SELECT id, camera_name, snapshot_path, snapshot_checksum
          FROM virtual_patrol_session_cameras
         WHERE session_id = CAST(:s AS uuid) AND snapshot_path IS NOT NULL
         ORDER BY sequence_no
    """), {"s": session_id})).mappings().all()
    for c in cameras:
        actual = sha256_of(root / c["snapshot_path"])
        checks.append(Check(
            kind="snapshot", reference=str(c["id"]),
            label=c["camera_name"] or "camera",
            status=classify(c["snapshot_checksum"], actual),
            expected=c["snapshot_checksum"], actual=actual))

    reports = (await db.execute(text("""
        SELECT id, report_format, storage_path, checksum_sha256
          FROM virtual_patrol_reports
         WHERE session_id = CAST(:s AS uuid)
         ORDER BY report_format
    """), {"s": session_id})).mappings().all()
    for r in reports:
        actual = sha256_of(root / r["storage_path"])
        checks.append(Check(
            kind="report", reference=str(r["id"]),
            label=r["report_format"],
            status=classify(r["checksum_sha256"], actual),
            expected=r["checksum_sha256"], actual=actual))

    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1

    return {
        "session_id": session_id,
        "checked": len(checks),
        "by_status": counts,
        # One bad file makes the patrol's evidence questionable, so the overall
        # verdict is the worst individual result rather than a majority.
        "verdict": (MISMATCH if counts.get(MISMATCH) else
                    FILE_MISSING if counts.get(FILE_MISSING) else
                    PASSED if counts.get(PASSED) else NOT_RECORDED),
        "scope": ("Detects a file changed since it was written. Does not "
                  "detect a change made to the database and the file together."),
        "checks": [c.as_dict() for c in checks],
    }


async def sweep_recent(db: AsyncSession, *, days: int = 7,
                       limit: int = 200) -> dict:
    """Verify recent patrol evidence across every tenant.

    Reads per tenant with the GUC set AND tenant_id in the WHERE clause -- the
    policy alone is not enough, since a connection that bypasses RLS would
    otherwise process every tenant's rows once per tenant.
    """
    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    checked, mismatched, missing = 0, 0, 0
    offenders: list[dict] = []

    for tenant_id in tenants:
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(tenant_id)})
            sessions = (await db.execute(text("""
                SELECT id FROM virtual_patrol_sessions
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND completed_at > now() - make_interval(days => :d)
                 ORDER BY completed_at DESC
                 LIMIT :lim
            """), {"t": str(tenant_id), "d": days, "lim": limit})).scalars().all()

            for sid in sessions:
                result = await verify_session(db, str(sid))
                checked += result["checked"]
                mismatched += result["by_status"].get(MISMATCH, 0)
                missing += result["by_status"].get(FILE_MISSING, 0)
                if result["verdict"] in (MISMATCH, FILE_MISSING):
                    offenders.append({"session_id": str(sid),
                                      "verdict": result["verdict"]})
            await db.rollback()
        except Exception:
            await db.rollback()
            logger.exception("integrity sweep failed for tenant %s", tenant_id)

    if mismatched:
        # Loud, and not merely counted. A snapshot that no longer matches its
        # hash is either corruption or interference, and both want a person.
        logger.error("patrol integrity: %d file(s) no longer match their "
                     "recorded checksum", mismatched)

    return {"checked": checked, "mismatched": mismatched,
            "files_missing": missing, "sessions_affected": offenders[:50]}
