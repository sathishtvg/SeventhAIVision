"""Queueing patrol reports, and retrying the ones that fail.

NOTHING IS SENT FROM AN HTTP REQUEST. Completing a patrol enqueues a row and
returns. An officer standing at the last camera should not be waiting on an SMTP
handshake, and a mail server that is down must not make their patrol look
broken. The worker does the sending.

A FAILED SEND IS RETRIED WITH BACKOFF, NOT FORGOTTEN AND NOT HAMMERED. Attempts
and the last error are recorded, the next try is pushed further out each time,
and after MAX_ATTEMPTS it stays FAILED with the reason still attached — visible,
rather than quietly dropped. An email nobody can prove was never sent is worse
than one that is plainly marked failed.

CLAIMED BEFORE SENT. A row is moved to PROCESSING in its own committed
transaction before the send begins, so two workers cannot both pick it up and a
crash mid-send leaves it PROCESSING rather than PENDING — one email possibly
sent twice is recoverable; the same email sent by two workers every minute
forever is not.

The digests (DAILY/WEEKLY/MONTHLY) are not built here yet: this phase covers the
queue and the immediate report, which is the one an agency actually acts on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
#: Minutes before the next attempt, indexed by attempts already made. A mail
#: server that is down is usually down for minutes, not seconds.
BACKOFF_MINUTES = [1, 5, 15, 60, 240]
BATCH = 20


def next_attempt_at(*, attempts: int, now: datetime) -> datetime:
    idx = min(attempts, len(BACKOFF_MINUTES) - 1)
    return now + timedelta(minutes=BACKOFF_MINUTES[idx])


async def enqueue_completed_patrol(db: AsyncSession, session_id: str) -> str | None:
    """Queue the immediate report for a finished patrol, if anyone wants it.

    Returns the queue row id, or None when the schedule has no recipients or is
    not set to IMMEDIATE — in which case there is nothing to send and a queued
    row addressed to nobody would just fail five times.
    """
    row = (await db.execute(text("""
        SELECT s.id, s.tenant_id, s.schedule_id, s.patrol_number, s.schedule_name,
               sc.email_frequency,
               (SELECT string_agg(r.email, ',' ORDER BY r.email)
                  FROM virtual_patrol_email_recipients r
                 WHERE r.schedule_id = s.schedule_id) AS recipients
          FROM virtual_patrol_sessions s
          LEFT JOIN virtual_patrol_schedules sc ON sc.id = s.schedule_id
         WHERE s.id = CAST(:id AS uuid)
    """), {"id": session_id})).mappings().first()

    if row is None or not row["recipients"]:
        return None
    if (row["email_frequency"] or "IMMEDIATE") != "IMMEDIATE":
        return None

    queued = (await db.execute(text("""
        INSERT INTO virtual_patrol_email_queue
            (tenant_id, schedule_id, session_id, frequency, recipients, subject)
        VALUES (:tid, :sid, CAST(:sess AS uuid), 'IMMEDIATE', :to, :subject)
        RETURNING id
    """), {
        "tid": row["tenant_id"], "sid": row["schedule_id"], "sess": session_id,
        "to": row["recipients"],
        "subject": f"Virtual patrol report: {row['schedule_name']} "
                   f"({row['patrol_number']})",
    })).scalar()
    return str(queued)


async def _claim(db: AsyncSession, queue_id) -> bool:
    """Take ownership of one row, committed, before any sending happens."""
    claimed = (await db.execute(text("""
        UPDATE virtual_patrol_email_queue
           SET status = 'PROCESSING', attempts = attempts + 1
         WHERE id = :id AND status IN ('PENDING', 'FAILED')
        RETURNING id
    """), {"id": queue_id})).first()
    await db.commit()
    return claimed is not None


async def process_queue(db: AsyncSession, *, send=None, now: datetime | None = None) -> dict:
    """Send what is due. `send` is injectable so tests never touch SMTP."""
    now = now or datetime.now(timezone.utc)
    send = send or _send_patrol_email

    # The queue is RLS-protected and this worker spans tenants, so the rows are
    # gathered one tenant at a time with the GUC set. An unscoped read does not
    # quietly return nothing — the policy casts current_setting(...) to uuid and
    # the empty string fails the cast, taking the whole run down.
    #
    # The tenant is in the WHERE clause too. Leaving the filtering to RLS alone
    # means any BYPASSRLS connection reads every row on every iteration, and the
    # loop then sends the same report once per tenant in the system.
    tenants = (await db.execute(text(
        "SELECT id FROM tenants WHERE is_active"))).scalars().all()

    due = []
    for tenant_id in tenants:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                         {"t": str(tenant_id)})
        rows = (await db.execute(text("""
            SELECT id, tenant_id, session_id, recipients, subject, attempts
              FROM virtual_patrol_email_queue
             WHERE status IN ('PENDING', 'FAILED')
               AND tenant_id = CAST(:t AS uuid)
               AND attempts < :max
               AND scheduled_at <= :now
             ORDER BY scheduled_at
             LIMIT :batch
        """), {"max": MAX_ATTEMPTS, "now": now, "batch": BATCH,
               "t": str(tenant_id)})).mappings().all()
        due.extend(dict(r) for r in rows)
    await db.rollback()

    sent, failed = 0, 0
    for row in due:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                         {"t": str(row["tenant_id"])})
        if not await _claim(db, row["id"]):
            continue  # another worker got there first

        try:
            # _claim COMMITS, and set_config(..., true) is SET LOCAL, so the
            # tenant is gone the moment the claim lands. Everything after this
            # point -- building the report, and marking the row SENT -- needs it
            # set again. Without this the send succeeds and the UPDATE throws,
            # the row is marked FAILED, and the same report goes out five times.
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(row["tenant_id"])})
            await send(db, session_id=str(row["session_id"]),
                       recipients=[e.strip() for e in row["recipients"].split(",") if e.strip()],
                       subject=row["subject"])
            await db.execute(text("""
                UPDATE virtual_patrol_email_queue
                   SET status = 'SENT', sent_at = now(), last_error = NULL
                 WHERE id = :id
            """), {"id": row["id"]})
            await db.commit()
            sent += 1
        except Exception as exc:
            await db.rollback()
            attempts = (row["attempts"] or 0) + 1
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                             {"t": str(row["tenant_id"])})
            await db.execute(text("""
                UPDATE virtual_patrol_email_queue
                   SET status = 'FAILED', last_error = :err, scheduled_at = :next
                 WHERE id = :id
            """), {"id": row["id"], "err": str(exc)[:500],
                   "next": next_attempt_at(attempts=attempts, now=now)})
            await db.commit()
            failed += 1
            logger.warning("virtual patrol email %s failed (attempt %d): %s",
                           row["id"], attempts, exc)

    return {"sent": sent, "failed": failed, "considered": len(due)}


async def _send_patrol_email(db: AsyncSession, *, session_id: str,
                             recipients: list[str], subject: str) -> None:
    """Build the report and hand it to SMTP.

    The PDF is generated at SEND time rather than stored on the queue row. The
    evidence it renders is already immutable, so the document is identical
    whenever it is built — and keeping megabytes of attachment in a queue table
    that retries five times is how a database fills up.
    """
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    import aiosmtplib

    from app.core.config import settings
    from app.services import vpatrol_reports

    pdf = await vpatrol_reports.build_patrol_pdf(db, session_id)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(
        "The attached report covers a completed virtual patrol, including the "
        "snapshot captured at each camera and the answers recorded against it.",
        "plain"))
    attachment = MIMEApplication(pdf, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment",
                          filename=f"{subject}.pdf")
    msg.attach(attachment)

    await aiosmtplib.send(
        msg, hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=settings.SMTP_PORT == 587,
    )
