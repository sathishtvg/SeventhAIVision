"""Turn a failure into a row somebody will actually read.

The pipeline §19 asks for, in one place:

    application -> collect -> classify -> group -> store -> console

GROUPING is the part that makes the difference between a log and something
useful. A bad deploy produces the same failure thousands of times; grouped by
fingerprint it is one row with a count, and the console answers "how many
distinct things are broken" instead of "how much scrolling is there".

THE FINGERPRINT is a hash of the service, the exception type, and the route
with its variable parts removed. /users/6f3a.../documents and
/users/91bc.../documents are the same bug seen twice; a fingerprint containing
the id would call them two bugs and hide the frequency that makes one worth
fixing first.

NOTHING HERE MAY RAISE. It runs on the error path, which is the worst possible
place to introduce a second error. Losing an error record is a nuisance; losing
the response the client was owed is a bug.
"""
from __future__ import annotations

import hashlib
import logging
import re
import traceback
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Stack traces are the useful part and the unbounded part. Enough to find the
#: frame that failed, not enough for one bad deploy to fill a disk.
MAX_STACK_CHARS = 8000
MAX_MESSAGE_CHARS = 2000

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_NUMBER_RE = re.compile(r"/\d+(?=/|$)")

#: Exception types that mean the platform itself is down rather than one
#: request being unlucky. §18's "critical" band.
_CRITICAL_TYPES = {
    "OperationalError", "InterfaceError", "DBAPIError", "ConnectionRefusedError",
    "ConnectionError", "RedisError", "ConnectionResetError", "TimeoutError",
    "PoolTimeout", "DisconnectionError",
}


def path_pattern(path: str) -> str:
    """The route with its variable parts removed, so occurrences group."""
    return _NUMBER_RE.sub("/{id}", _UUID_RE.sub("{id}", path))


def fingerprint_of(service: str, error_type: str, pattern: str) -> str:
    raw = f"{service}|{error_type}|{pattern}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def classify(error_type: str, status_code: int) -> str:
    """Which of §18's three bands this belongs in.

    Assigned at collection rather than worked out later by whoever is reading:
    the person on call at 2am should not have to judge whether a stack trace
    means the database is gone.
    """
    if error_type in _CRITICAL_TYPES:
        return "critical"
    if status_code >= 500:
        return "critical"
    if status_code >= 400:
        return "warning"
    return "info"


async def record(
    session_factory,
    *,
    service: str = "api",
    error_type: str,
    message: str | None,
    method: str | None = None,
    path: str | None = None,
    status_code: int = 500,
    tenant_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    exc: BaseException | None = None,
) -> None:
    """Group the failure, count it, and keep the occurrence. Never raises."""
    try:
        pattern = path_pattern(path or "")
        fp = fingerprint_of(service, error_type, pattern)
        severity = classify(error_type, status_code)
        stack = (
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            if exc is not None else None
        )

        async with session_factory() as session:
            # Upsert the group. A fingerprint already resolved goes back to
            # open: a fix that did not hold is worse news than a new bug, and
            # silently re-resolving it would hide that.
            error_id = (await session.execute(
                text("""
                    INSERT INTO platform_errors
                        (id, fingerprint, service, severity, error_type, message,
                         method, path_pattern)
                    VALUES (CAST(:id AS uuid), :fp, :service, :severity, :etype,
                            :msg, :method, :pattern)
                    ON CONFLICT (fingerprint) DO UPDATE
                       SET occurrence_count = platform_errors.occurrence_count + 1,
                           last_seen_at     = now(),
                           message          = EXCLUDED.message,
                           severity         = EXCLUDED.severity,
                           status           = CASE
                                                WHEN platform_errors.status = 'resolved'
                                                THEN 'open'
                                                ELSE platform_errors.status
                                              END,
                           resolved_at      = CASE
                                                WHEN platform_errors.status = 'resolved'
                                                THEN NULL
                                                ELSE platform_errors.resolved_at
                                              END
                    RETURNING id
                """),
                {"id": str(uuid.uuid4()), "fp": fp, "service": service,
                 "severity": severity, "etype": error_type[:200],
                 "msg": (message or "")[:MAX_MESSAGE_CHARS],
                 "method": method, "pattern": pattern},
            )).scalar()

            await session.execute(
                text("""
                    INSERT INTO platform_error_events
                        (id, error_id, tenant_id, user_id, status_code,
                         request_id, path, stack)
                    VALUES (CAST(:id AS uuid), CAST(:eid AS uuid),
                            CASE WHEN :tid = '' THEN NULL ELSE CAST(:tid AS uuid) END,
                            CASE WHEN :uid = '' THEN NULL ELSE CAST(:uid AS uuid) END,
                            :status, :rid, :path, :stack)
                """),
                {"id": str(uuid.uuid4()), "eid": str(error_id),
                 "tid": tenant_id or "", "uid": user_id or "",
                 "status": status_code, "rid": request_id, "path": path,
                 "stack": stack[-MAX_STACK_CHARS:] if stack else None},
            )
            # Neither table has RLS — they span tenants by definition — so no
            # tenant GUC is set on this connection, deliberately.
            await session.commit()
    except Exception:
        # Swallowed on purpose. The client is owed its response far more than
        # the vendor is owed this row, and raising here would replace a useful
        # 500 with a confusing one.
        logger.warning("platform error capture failed", exc_info=True)
