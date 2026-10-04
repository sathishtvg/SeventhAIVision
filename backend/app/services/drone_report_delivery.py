"""Sending drone patrol reports: who gets them, when, and what happens if the
mail server is down.

NOTHING IS SENT WHERE THE WORK HAPPENS. A flight ending puts a row in the queue;
a period closing puts a row in the queue. The runner's report job — its own
cadence, never the flight loop and never an HTTP request — claims rows and
sends them. A mail server that is slow or down costs that job a tick and
nothing else.

THE SAME QUEUE DISCIPLINE AS VIRTUAL PATROLLING'S, and its backoff and period
arithmetic are imported rather than rewritten: claimed in a committed
transaction before sending, retried with a growing delay, and left FAILED with
its reason after the last attempt — visible, never silently dropped.

QUEUEING IS IDEMPOTENT BY CONSTRAINT. One immediate email per flight and one
summary per scope and period are unique indexes, so a restart, a retry, or two
runners at once cannot queue anything twice.

THE DOCUMENT IS BUILT AT SEND TIME, from the flight's own record, and never
stored on the queue row: megabytes of attachment in a table that retries five
times is how a database fills up.

A FLIGHT IS REPORTED A FEW MINUTES AFTER IT ENDS, not the instant it lands, so
that the fixed cameras' corroboration, an officer's first action and a late
upload from the site are in the report people actually receive.

Every function here expects a session already scoped to one tenant.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import drone_reports as reports
from app.services.drone_ai_pipeline import tenant_tz
from app.services.vpatrol_email import MAX_ATTEMPTS, digest_period, next_attempt_at

logger = logging.getLogger(__name__)

#: How long after landing a flight is reported.
SETTLE = timedelta(minutes=5)
#: Flights older than this are not reported automatically (they still render on
#: request); matches drone_report_tenants().
LOOKBACK = timedelta(days=7)
#: A row PROCESSING this long was claimed by a worker that died; take it again.
STALE_CLAIM = timedelta(minutes=30)
FLIGHT_BATCH = 10
EMAIL_BATCH = 20
MAX_RECIPIENTS_PER_EMAIL = 50
#: A mail server that does not answer is given this long, then the row fails
#: into backoff.
SMTP_TIMEOUT_S = 30

FREQUENCIES = ("IMMEDIATE", "DAILY", "WEEKLY", "MONTHLY")
_EMAIL = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")

#: (recipients, subject, body, filename, payload, mime subtype) -> None
Deliver = Callable[[list[str], str, str, str, bytes, str], Awaitable[None]]


def normalise_email(value: str) -> str | None:
    """A lower-cased address, or None if it is not one. Deliberately plain: it
    refuses what could break a header or name two recipients, and leaves the
    rest to the mail server."""
    v = (value or "").strip().lower()
    return v if len(v) <= 255 and _EMAIL.match(v) else None


def _one_line(s: str, limit: int = 200) -> str:
    """Safe for a header or a filename: no line breaks, bounded."""
    return re.sub(r"[\r\n]+", " ", s or "").strip()[:limit]


def scope_key(site_id, mission_id) -> str:
    if mission_id:
        return f"mission:{mission_id}"
    if site_id:
        return f"site:{site_id}"
    return "tenant"


# ═════════════════════════════════════════════════════════════════════════════
# A finished flight
# ═════════════════════════════════════════════════════════════════════════════

async def flights_to_report(db: AsyncSession, now: datetime) -> list[str]:
    rows = (await db.execute(text("""
        SELECT id FROM drone_patrol_sessions
         WHERE ended_at IS NOT NULL AND report_queued_at IS NULL
           AND ended_at <= :settled AND ended_at > :oldest
         ORDER BY ended_at
         LIMIT :n
    """), {"settled": now - SETTLE, "oldest": now - LOOKBACK, "n": FLIGHT_BATCH})).scalars().all()
    return [str(r) for r in rows]


async def immediate_recipients(db: AsyncSession, *, site_id, mission_id, ended_at: datetime) -> list[str]:
    """Who is told about this flight straight away: anyone subscribed to the
    whole organisation, to its site or to its mission — and who already was when
    it ended. Adding a recipient does not mail them the past week."""
    rows = (await db.execute(text("""
        SELECT DISTINCT email FROM drone_report_recipients
         WHERE is_active AND frequency = 'IMMEDIATE' AND created_at <= :ended
           AND ((site_id IS NULL AND mission_id IS NULL)
                OR site_id = CAST(:site AS uuid) OR mission_id = CAST(:mission AS uuid))
         ORDER BY email
         LIMIT :n
    """), {"ended": ended_at, "site": str(site_id) if site_id else None,
           "mission": str(mission_id) if mission_id else None, "n": MAX_RECIPIENTS_PER_EMAIL})).scalars().all()
    return list(rows)


async def report_finished_flight(db: AsyncSession, session_id: str, now: datetime) -> dict:
    """Store a finished flight's report and queue its email, once.

    The session row is locked for the duration, and skipped if another runner
    holds it, so two runners never store or queue the same flight together."""
    s = (await db.execute(text("""
        SELECT id, site_id, mission_id, session_number, mission_name, status, ended_at,
               (SELECT name FROM sites WHERE id = ps.site_id) AS site_name
          FROM drone_patrol_sessions ps
         WHERE id = CAST(:id AS uuid) AND report_queued_at IS NULL AND ended_at IS NOT NULL
           FOR UPDATE OF ps SKIP LOCKED
    """), {"id": session_id})).mappings().first()
    if s is None:
        return {"stored": 0, "queued": 0}
    stored = await reports.store_reports(db, session_id)
    to = await immediate_recipients(db, site_id=s["site_id"], mission_id=s["mission_id"], ended_at=s["ended_at"])
    queued = 0
    if to:
        subject = _one_line(f"Drone patrol report: {s['mission_name'] or 'Mission'}"
                            f"{' at ' + s['site_name'] if s['site_name'] else ''} — "
                            f"{s['status'].replace('_', ' ').lower()} ({s['session_number']})")
        row = (await db.execute(text("""
            INSERT INTO drone_report_email_queue
                (tenant_id, session_id, site_id, mission_id, frequency, recipients, subject, scheduled_at)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:s AS uuid), :site, :mission, 'IMMEDIATE',
                    :to, :subject, :now)
            ON CONFLICT (session_id) WHERE frequency = 'IMMEDIATE' DO NOTHING
            RETURNING id
        """), {"s": session_id, "site": s["site_id"], "mission": s["mission_id"], "to": ",".join(to),
               "subject": subject, "now": now})).first()
        queued = 1 if row else 0
    await db.execute(text("UPDATE drone_patrol_sessions SET report_queued_at = :now WHERE id = CAST(:id AS uuid)"),
                     {"now": now, "id": session_id})
    return {"stored": len(stored), "queued": queued}


# ═════════════════════════════════════════════════════════════════════════════
# Summaries: daily, weekly, monthly
# ═════════════════════════════════════════════════════════════════════════════

async def enqueue_due_digests(db: AsyncSession, now: datetime) -> dict:
    """Queue one summary per scope and frequency whose period has closed.

    Periods are computed in the organisation's own zone — "yesterday" in
    Singapore is not yesterday in UTC for eight hours of every day. Only closed
    periods are queued, and a period with no flights is not a summary: someone
    who receives "0 flights" every Monday stops opening Monday's email."""
    tz = await tenant_tz(db)
    today = now.astimezone(tz).date()
    groups = (await db.execute(text("""
        SELECT r.frequency, r.site_id, r.mission_id,
               string_agg(DISTINCT r.email, ',' ORDER BY r.email) AS recipients,
               (SELECT name FROM sites WHERE id = r.site_id) AS site_name,
               (SELECT name FROM drone_missions WHERE id = r.mission_id) AS mission_name,
               (SELECT site_id FROM drone_missions WHERE id = r.mission_id) AS mission_site_id
          FROM drone_report_recipients r
         WHERE r.is_active AND r.frequency <> 'IMMEDIATE'
         GROUP BY r.frequency, r.site_id, r.mission_id
    """))).mappings().all()

    counts = {"queued": 0, "already_queued": 0, "nothing_to_report": 0}
    for g in groups:
        window = digest_period(g["frequency"], today=today)
        if window is None:
            continue
        start, end = window
        since, until = reports.period_bounds(start, end, tz)
        flights = (await db.execute(text("""
            SELECT count(*) FROM drone_patrol_sessions ps
             WHERE ps.created_at >= :since AND ps.created_at < :until
               AND (CAST(:site AS uuid) IS NULL OR ps.site_id = CAST(:site AS uuid))
               AND (CAST(:mission AS uuid) IS NULL OR ps.mission_id = CAST(:mission AS uuid))
        """), {"since": since, "until": until, "site": str(g["site_id"]) if g["site_id"] else None,
               "mission": str(g["mission_id"]) if g["mission_id"] else None})).scalar()
        if not flights:
            counts["nothing_to_report"] += 1
            continue
        label = (f"mission {g['mission_name']}" if g["mission_id"]
                 else g["site_name"] if g["site_id"] else "all sites")
        subject = _one_line(f"Drone patrol {g['frequency'].lower()} summary: {label} "
                            f"({start:%d %b} – {end:%d %b %Y})")
        # A mission's summary carries its site too, so someone restricted to that
        # site can see the delivery in the log.
        row = (await db.execute(text("""
            INSERT INTO drone_report_email_queue
                (tenant_id, site_id, mission_id, scope_key, scope_label, frequency, period_start, period_end,
                 timezone, recipients, subject, scheduled_at)
            VALUES (current_setting('app.current_tenant')::uuid, :site, :mission, :key, :label, :f, :a, :b,
                    :tz, :to, :subject, :now)
            ON CONFLICT (tenant_id, frequency, period_start, scope_key) WHERE frequency <> 'IMMEDIATE' DO NOTHING
            RETURNING id
        """), {"site": g["site_id"] or g["mission_site_id"], "mission": g["mission_id"],
               "key": scope_key(g["site_id"], g["mission_id"]),
               "label": label[:200], "f": g["frequency"], "a": start, "b": end, "tz": tz.key,
               "to": g["recipients"], "subject": subject, "now": now})).first()
        counts["queued" if row else "already_queued"] += 1
    return counts


# ═════════════════════════════════════════════════════════════════════════════
# The queue
# ═════════════════════════════════════════════════════════════════════════════

async def _scope(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)})


async def build_email(db: AsyncSession, row: dict) -> tuple[str, str, bytes, str]:
    """(body, filename, payload, mime subtype) for one queue row."""
    if row["frequency"] == "IMMEDIATE":
        data = await reports.load_report(db, row["session_id"])
        payload = await asyncio.to_thread(reports.render_pdf, data, reports.stored_file_reader())
        return (reports.flight_text(data), f"drone-patrol-{data['mission']['session_number']}.pdf", payload, "pdf")
    kind, _, ident = (row["scope_key"] or "tenant").partition(":")
    data = await reports.load_period(db, start=row["period_start"], end=row["period_end"],
                                     site_id=ident if kind == "site" else None,
                                     mission_id=ident if kind == "mission" else None)
    label = row["scope_label"] or "all sites"
    payload = await asyncio.to_thread(reports.render_period_xlsx, data, scope_label=label)
    name = (f"drone-patrol-{row['frequency'].lower()}-{row['period_start']:%Y%m%d}-"
            f"{row['period_end']:%Y%m%d}.xlsx")
    return (reports.period_text(data, scope_label=label), name, payload,
            "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


async def smtp_deliver(recipients: list[str], subject: str, body: str, filename: str, payload: bytes,
                       subtype: str) -> None:  # pragma: no cover - talks to a mail server
    """Hand one report to the platform's mail server (the same settings every
    other email in the product uses)."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    import aiosmtplib

    msg = MIMEMultipart()
    msg["Subject"] = _one_line(subject)
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    attachment = MIMEApplication(payload, _subtype=subtype)
    attachment.add_header("Content-Disposition", "attachment", filename=_one_line(filename, 120))
    msg.attach(attachment)
    await aiosmtplib.send(
        msg, hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None, password=settings.SMTP_PASSWORD or None,
        use_tls=(settings.SMTP_PORT == 465), start_tls=(settings.SMTP_PORT == 587),
        timeout=SMTP_TIMEOUT_S,
    )


async def process_queue(db: AsyncSession, tenant_id: str, now: datetime, deliver: Deliver | None = None) -> dict:
    """Send what is due for one tenant. `deliver` is injectable so tests build
    the real documents and never touch a mail server.

    Each row is claimed in its own committed transaction before anything is
    sent. That commit ends the tenant scope, which is set again before every
    statement that follows."""
    deliver = deliver or smtp_deliver
    await _scope(db, tenant_id)
    due = (await db.execute(text("""
        SELECT id, session_id, scope_key, scope_label, frequency, period_start, period_end, recipients,
               subject, attempts
          FROM drone_report_email_queue
         WHERE attempts < :max
           AND ((status IN ('PENDING','FAILED') AND scheduled_at <= :now)
                OR (status = 'PROCESSING' AND claimed_at < :stale))
         ORDER BY scheduled_at
         LIMIT :n
    """), {"max": MAX_ATTEMPTS, "now": now, "stale": now - STALE_CLAIM, "n": EMAIL_BATCH})).mappings().all()
    await db.rollback()

    sent = failed = 0
    for row in (dict(r) for r in due):
        await _scope(db, tenant_id)
        claimed = (await db.execute(text("""
            UPDATE drone_report_email_queue
               SET status = 'PROCESSING', attempts = attempts + 1, claimed_at = :now
             WHERE id = :id AND attempts < :max
               AND (status IN ('PENDING','FAILED') OR (status = 'PROCESSING' AND claimed_at < :stale))
            RETURNING id
        """), {"id": row["id"], "now": now, "max": MAX_ATTEMPTS, "stale": now - STALE_CLAIM})).first()
        await db.commit()
        if claimed is None:
            continue  # another runner got there first
        try:
            await _scope(db, tenant_id)
            body, filename, payload, subtype = await build_email(db, row)
            to = [e for e in (row["recipients"] or "").split(",") if e]
            await deliver(to, row["subject"], body, filename, payload, subtype)
            await db.execute(text("""
                UPDATE drone_report_email_queue SET status = 'SENT', sent_at = :now, last_error = NULL
                 WHERE id = :id
            """), {"id": row["id"], "now": now})
            await db.commit()
            sent += 1
        except Exception as exc:
            await db.rollback()
            attempts = (row["attempts"] or 0) + 1
            await _scope(db, tenant_id)
            await db.execute(text("""
                UPDATE drone_report_email_queue SET status = 'FAILED', last_error = :err, scheduled_at = :next
                 WHERE id = :id
            """), {"id": row["id"], "err": str(exc)[:500], "next": next_attempt_at(attempts=attempts, now=now)})
            await db.commit()
            failed += 1
            logger.warning("drone report email %s failed (attempt %d of %d): %s",
                           row["id"], attempts, MAX_ATTEMPTS, exc)
    return {"sent": sent, "failed": failed}
