"""The daily briefing emailed when it is published, and the operations reports emailed on a schedule.

Phase 4 of closing the feature reference's known limits (4 November 2026; docs/plans/2026-11-04-ops-reports-and-
briefing-email-design.md).

ONLY THE ORGANISATION'S OWN PEOPLE, EACH WHAT THEY COULD READ ON SCREEN. A briefing goes to whoever is on its site's
list, or the list for every briefing, and holds briefing:read and is shown its site. A report goes to whoever its
schedule names who holds opsreport:export and the permission its records are read under, and is made for each of
them as they would take it out: their own sites only. Whoever may not is LEFT_OUT, with why, and the record says so.
Nobody outside the organisation is ever written to.

NOTHING IS SENT WHERE THE WORK HAPPENS. Publishing a briefing queues its emails in the same transaction; a schedule
falling due queues its emails. The scheduler's pass then claims each row in a committed transaction, makes the
document - at send time, from the records; nothing of it is stored - sends it, and records SENT, or FAILED with why
and a later attempt, as Virtual Patrolling's and Drone Patrol's emails are (their backoff and error wording are
imported, not rewritten). Who may receive it is asked again when it is sent.

A SCHEDULE THAT FELL DUE WHILE THE PLATFORM WAS DOWN SENDS ONCE when it is back, for the period that was due, and
moves on to its next time: not one email for every time it missed.

EVERY EMAIL SENT IS AUDITED, as taking a report out is: which, for whom, how many rows.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import daily_briefing, ops_documents, ops_reports
from app.services.drone_report_delivery import delivery_error, normalise_email
from app.services.vpatrol_email import MAX_ATTEMPTS, next_attempt_at

logger = logging.getLogger(__name__)

FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY")
#: A row PROCESSING this long was claimed by a pass that died; take it again.
STALE_CLAIM = timedelta(minutes=30)
BATCH = 25
SMTP_TIMEOUT_S = 30
#: Admin sees every site; a client sees none.
UNRESTRICTED_ROLES = (1, 2)
CLIENT_ROLE = 7

Attachment = tuple[str, bytes, str, str]  # (filename, content, maintype, subtype)
Deliver = Callable[[list[str], str, str, list[Attachment]], Awaitable[None]]


# ─── Who ─────────────────────────────────────────────────────────────────────

def _permissions() -> tuple[str, ...]:
    from app.routers.operations_board import SOURCE_PERMISSIONS

    return tuple(dict.fromkeys((*SOURCE_PERMISSIONS, "opsreport:export", "briefing:read",
                                *(c for r in ops_reports.REPORTS for c in r.needs))))


async def people(db: AsyncSession, user_ids: Sequence[Any], role_ids: Sequence[int]) -> list[dict]:
    """Those named, whatever their standing, and the active people of the roles named."""
    rows = await db.execute(text("""
        SELECT u.id, u.full_name, u.email, u.role_id, u.is_active FROM users u
         WHERE u.id = ANY(CAST(:ids AS uuid[])) OR (u.is_active AND u.role_id = ANY(CAST(:roles AS int[])))
         ORDER BY u.full_name, u.id
    """), {"ids": [str(u) for u in user_ids], "roles": list(role_ids)})
    return [dict(r) for r in rows.mappings()]


async def held(db: AsyncSession, role_id: int) -> frozenset[str]:
    rows = await db.execute(text("""
        SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = :role AND p.code = ANY(CAST(:codes AS text[]))
    """), {"role": role_id, "codes": list(_permissions())})
    return frozenset(r.code for r in rows)


async def allowed_sites(db: AsyncSession, person: Mapping) -> list[str] | None:
    """The sites a person is shown, as the API works it out for them (dependencies/sites.py): None for every site."""
    if person["role_id"] in UNRESTRICTED_ROLES:
        return None
    sites = [str(r[0]) for r in await db.execute(
        text("SELECT site_id FROM user_sites WHERE user_id = CAST(:u AS uuid)"), {"u": str(person["id"])})]
    if sites:
        return sites
    return [] if person["role_id"] == CLIENT_ROLE else None


def sees(allowed: list[str] | None, site_id: Any) -> bool:
    if site_id is None:
        return allowed is None
    return allowed is None or str(site_id) in allowed


async def why_not_briefing(db: AsyncSession, person: Mapping, site_id: Any, site_name: str | None) -> str | None:
    if not person["is_active"]:
        return "No longer in use"
    if "briefing:read" not in await held(db, person["role_id"]):
        return "Does not hold briefing:read"
    if not sees(await allowed_sites(db, person), site_id):
        return f"Not shown {site_name}" if site_id else "Held to particular sites, and this briefing is for every site"
    if not normalise_email(person["email"] or ""):
        return "No email address mail can be sent to"
    return None


async def why_not_report(db: AsyncSession, person: Mapping, report: ops_reports.Report, site_id: Any,
                         site_name: str | None) -> str | None:
    if not person["is_active"]:
        return "No longer in use"
    codes = await held(db, person["role_id"])
    if "opsreport:export" not in codes:
        return "Does not hold opsreport:export"
    missing = [c for c in report.needs if c not in codes]
    if missing:
        return f"Does not hold {' and '.join(missing)}, which the report's records are read under"
    if site_id is not None and not sees(await allowed_sites(db, person), site_id):
        return f"Not shown {site_name}"
    if not normalise_email(person["email"] or ""):
        return "No email address mail can be sent to"
    return None


async def _site_name(db: AsyncSession, site_id: Any) -> str | None:
    if site_id is None:
        return None
    return (await db.execute(text("SELECT name FROM sites WHERE id = CAST(:s AS uuid)"), {"s": str(site_id)})).scalar()


async def zone_of(db: AsyncSession) -> str:
    name = (await db.execute(text(
        "SELECT timezone FROM tenants WHERE id = current_setting('app.current_tenant')::uuid"))).scalar()
    try:
        ZoneInfo(name or "Asia/Singapore")
        return name or "Asia/Singapore"
    except Exception:
        return "Asia/Singapore"


async def _queue(db: AsyncSession, *, kind: str, person: Mapping, why: str | None, subject: str,
                 briefing_id: Any = None, schedule_id: Any = None, start: datetime | None = None,
                 end: datetime | None = None, revised: bool = False, at: datetime | None = None) -> bool:
    made = (await db.execute(text("""
        INSERT INTO ops_emails (tenant_id, kind, briefing_id, schedule_id, user_id, email, period_start, period_end,
                                revised, subject, status, last_error, scheduled_at)
        VALUES (current_setting('app.current_tenant')::uuid, :kind, CAST(:b AS uuid), CAST(:s AS uuid),
                CAST(:u AS uuid), :email, :start, :end, :revised, :subject, :status, :why,
                COALESCE(CAST(:at AS timestamptz), now()))
        ON CONFLICT DO NOTHING RETURNING id
    """), {"kind": kind, "b": str(briefing_id) if briefing_id else None, "s": str(schedule_id) if schedule_id else None,
           "u": str(person["id"]), "email": normalise_email(person["email"] or ""), "start": start, "end": end,
           "revised": revised, "subject": subject, "status": "LEFT_OUT" if why else "PENDING", "why": why,
           "at": at})).scalar()
    return made is not None


# ─── A briefing, when published ──────────────────────────────────────────────

async def queue_briefing(db: AsyncSession, briefing: Mapping) -> dict:
    """Queue a published briefing's emails, in the caller's transaction: the people on its site's list and the list
    for every briefing. `briefing` has id, site_id, briefing_date and revision."""
    lists = (await db.execute(text("""
        SELECT user_ids, role_ids FROM briefing_recipient_lists
         WHERE site_id IS NULL OR site_id = CAST(:s AS uuid)
    """), {"s": str(briefing["site_id"]) if briefing["site_id"] else None})).mappings().all()
    user_ids = {str(u) for row in lists for u in row["user_ids"]}
    role_ids = sorted({int(r) for row in lists for r in row["role_ids"]})
    if not user_ids and not role_ids:
        return {"queued": 0, "left_out": 0}
    site_name = await _site_name(db, briefing["site_id"])
    revised = briefing["revision"] > 1
    subject = ops_documents.briefing_title(site_name, briefing["briefing_date"], revised)
    counts = {"queued": 0, "left_out": 0}
    for person in await people(db, sorted(user_ids), role_ids):
        why = await why_not_briefing(db, person, briefing["site_id"], site_name)
        if await _queue(db, kind="BRIEFING", person=person, why=why, subject=subject, briefing_id=briefing["id"],
                        revised=revised):
            counts["left_out" if why else "queued"] += 1
    return counts


# ─── A report, on its schedule ───────────────────────────────────────────────

def next_after(schedule: Mapping, after: datetime, zone: str) -> datetime:
    """A schedule's next time after `after`, at its hour where the organisation is."""
    tz = ZoneInfo(zone)
    local = after.astimezone(tz)
    at = local.replace(hour=schedule["hour"], minute=0, second=0, microsecond=0)
    if schedule["frequency"] == "DAILY":
        if at <= local:
            at += timedelta(days=1)
    elif schedule["frequency"] == "WEEKLY":
        at += timedelta(days=(schedule["weekday"] - local.weekday()) % 7)
        if at <= local:
            at += timedelta(days=7)
    else:
        at = at.replace(day=schedule["month_day"])
        if at <= local:
            year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
            at = at.replace(year=year, month=month)
    return at.astimezone(timezone.utc)


def period_for(schedule: Mapping, due_at: datetime, zone: str) -> tuple[datetime, datetime]:
    """The period a run at `due_at` reports: the day, the week or the month just ended, where the organisation is."""
    tz = ZoneInfo(zone)
    midnight = due_at.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    if schedule["frequency"] == "DAILY":
        return (midnight - timedelta(days=1)).astimezone(timezone.utc), midnight.astimezone(timezone.utc)
    if schedule["frequency"] == "WEEKLY":
        return (midnight - timedelta(days=7)).astimezone(timezone.utc), midnight.astimezone(timezone.utc)
    first = midnight.replace(day=1)
    year, month = (first.year - 1, 12) if first.month == 1 else (first.year, first.month - 1)
    start = first.replace(year=year, month=month, day=1)
    return start.astimezone(timezone.utc), first.astimezone(timezone.utc)


def report_subject(report: ops_reports.Report, site_name: str | None, frequency: str, start, end, zone: str) -> str:
    when = ops_documents.period_words(frequency, start, end, zone) if report.periodic else "as it is now"
    return f"{report.title}: {site_name or 'every site you are shown'}, {when}"


async def queue_due_reports(db: AsyncSession, now: datetime) -> int:
    """Queue the emails of every schedule of the organisation in scope that has fallen due, and move each on to its
    next time. One run for a time missed, for the period that was due."""
    zone = await zone_of(db)
    due = (await db.execute(text("""
        SELECT id, report_key, site_id, frequency, hour, weekday, month_day, user_ids, role_ids, next_run_at
          FROM ops_report_schedules
         WHERE is_active AND removed_at IS NULL AND next_run_at <= :now
         ORDER BY next_run_at FOR UPDATE SKIP LOCKED
    """), {"now": now})).mappings().all()
    queued = 0
    for s in (dict(r) for r in due):
        report = ops_reports.BY_KEY[s["report_key"]]
        site_name = await _site_name(db, s["site_id"])
        if report.periodic:
            start, end = period_for(s, s["next_run_at"], zone)
        else:
            start, end = s["next_run_at"], None
        subject = report_subject(report, site_name, s["frequency"], start, end, zone)
        for person in await people(db, s["user_ids"], s["role_ids"]):
            why = await why_not_report(db, person, report, s["site_id"], site_name)
            queued += await _queue(db, kind="REPORT", person=person, why=why, subject=subject, schedule_id=s["id"],
                                   start=start, end=end, at=now)
        await db.execute(text("""
            UPDATE ops_report_schedules SET last_run_at = :now, next_run_at = :next WHERE id = :id
        """), {"now": now, "next": next_after(s, now, zone), "id": s["id"]})
    return queued


# ─── Sending ─────────────────────────────────────────────────────────────────

async def _scope(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})


async def _briefing_email(db: AsyncSession, row: Mapping, person: Mapping) -> tuple[str | None, str, list, dict]:
    b = (await db.execute(text("""
        SELECT b.id, b.site_id, s.name AS site_name, b.briefing_date, b.revision, b.state, b.content, b.left_out,
               b.note, b.published_at, u.full_name AS published_by_name
          FROM daily_briefings b LEFT JOIN sites s ON s.id = b.site_id LEFT JOIN users u ON u.id = b.published_by_user_id
         WHERE b.id = :b
    """), {"b": row["briefing_id"]})).mappings().first()
    if b is None or b["state"] != "PUBLISHED":
        return "The briefing is no longer published", "", [], {}
    why = await why_not_briefing(db, person, b["site_id"], b["site_name"])
    if why:
        return why, "", [], {}
    content = b["content"] if isinstance(b["content"], dict) else json.loads(b["content"])
    sections = daily_briefing.shown(content, b["left_out"] or [], whole=False)
    zone = await zone_of(db)
    body = ops_documents.briefing_text(b, sections, zone)
    pdf = ops_documents.briefing_pdf(b, sections, zone)
    name = f"briefing-{b['briefing_date']:%Y%m%d}" + (f"-revision-{b['revision']}" if b["revision"] > 1 else "")
    return None, body, [(f"{name}.pdf", pdf, "application", "pdf")], {
        "action": "briefing.email", "resource_type": "daily_briefing", "resource_id": str(b["id"]),
        "site_id": b["site_id"], "detail": {"recipient_user_id": str(person["id"]), "revision": b["revision"]}}


async def _report_email(db: AsyncSession, row: Mapping, person: Mapping, now: datetime) -> tuple[str | None, str, list, dict]:
    from app.routers.operations_board import sites_in
    from app.services import intel_insight

    s = (await db.execute(text("""
        SELECT id, report_key, site_id, frequency, removed_at, is_active FROM ops_report_schedules WHERE id = :s
    """), {"s": row["schedule_id"]})).mappings().first()
    if s is None or s["removed_at"] is not None:
        return "The schedule was taken away", "", [], {}
    report = ops_reports.BY_KEY[s["report_key"]]
    site_name = await _site_name(db, s["site_id"])
    why = await why_not_report(db, person, report, s["site_id"], site_name)
    if why:
        return why, "", [], {}
    codes = await held(db, person["role_id"])
    allowed = await allowed_sites(db, person)
    sites, site, _ = await sites_in(db, allowed, s["site_id"])
    every = site is None and allowed is None
    zone = await intel_insight.zone_for(db, site["id"] if site else None)
    start = row["period_start"] if report.periodic else now - timedelta(days=1)
    end = row["period_end"] if report.periodic else now
    found, cut = await ops_reports.rows(db, report, codes, sites, None if every else [x["id"] for x in sites], zone,
                                        start, end, now, site["id"] if site else None)
    scope = site_name or ("Every site" if every else "Every site you are shown")
    period = ops_documents.period_words(s["frequency"], row["period_start"], row["period_end"], zone) \
        if report.periodic else "as it is now"
    body = ops_documents.report_text(report.title, scope, period, len(found), cut, zone)
    csv_bytes = ops_reports.as_csv(report, found, zone, cut).encode("utf-8-sig")
    pdf = ops_documents.report_pdf(report, found, scope=scope, period=period, zone=zone, made_at=now, cut=cut)
    stamp = (row["period_start"] or now).astimezone(ZoneInfo(zone)).strftime("%Y%m%d")
    return None, body, [(f"operations-{report.key}-{stamp}.csv", csv_bytes, "text", "csv"),
                        (f"operations-{report.key}-{stamp}.pdf", pdf, "application", "pdf")], {
        "action": "report.email", "resource_type": "operations_report", "resource_id": None,
        "site_id": site["id"] if site else None, "rows": len(found),
        "detail": {"report": report.key, "rows": len(found), "cut": cut, "schedule_id": str(s["id"]),
                   "recipient_user_id": str(person["id"]), "every_site": every,
                   "period_start": row["period_start"].isoformat() if row["period_start"] else None,
                   "period_end": row["period_end"].isoformat() if row["period_end"] else None}}


async def process_queue(db: AsyncSession, tenant_id: str, now: datetime, deliver: Deliver | None = None) -> dict:
    """Send what is due for one organisation. Each row is claimed in its own committed transaction first; that commit
    ends the organisation's scope, which is set again before every statement after it."""
    from app.services.audit import write_audit_log

    deliver = deliver or smtp_send
    await _scope(db, tenant_id)
    due = (await db.execute(text("""
        SELECT id, kind, briefing_id, schedule_id, user_id, subject, period_start, period_end, attempts
          FROM ops_emails
         WHERE attempts < :max
           AND ((status IN ('PENDING', 'FAILED') AND scheduled_at <= :now)
                OR (status = 'PROCESSING' AND claimed_at < :stale))
         ORDER BY scheduled_at LIMIT :n
    """), {"max": MAX_ATTEMPTS, "now": now, "stale": now - STALE_CLAIM, "n": BATCH})).mappings().all()
    await db.rollback()
    counts = {"sent": 0, "failed": 0, "left_out": 0}
    for row in (dict(r) for r in due):
        await _scope(db, tenant_id)
        claimed = (await db.execute(text("""
            UPDATE ops_emails SET status = 'PROCESSING', attempts = attempts + 1, claimed_at = :now
             WHERE id = :id AND attempts < :max
               AND (status IN ('PENDING', 'FAILED') OR (status = 'PROCESSING' AND claimed_at < :stale))
            RETURNING id
        """), {"id": row["id"], "now": now, "max": MAX_ATTEMPTS, "stale": now - STALE_CLAIM})).first()
        await db.commit()
        if claimed is None:
            continue
        try:
            await _scope(db, tenant_id)
            person = (await people(db, [row["user_id"]], []))[0]
            if row["kind"] == "BRIEFING":
                why, body, attachments, audit = await _briefing_email(db, row, person)
            else:
                why, body, attachments, audit = await _report_email(db, row, person, now)
            if why:
                await db.execute(text("UPDATE ops_emails SET status = 'LEFT_OUT', last_error = :why WHERE id = :id"),
                                 {"why": why, "id": row["id"]})
                await db.commit()
                counts["left_out"] += 1
                continue
            await deliver([normalise_email(person["email"])], row["subject"], body, attachments)
            await db.execute(text("""
                UPDATE ops_emails SET status = 'SENT', sent_at = :now, last_error = NULL, rows_sent = :rows,
                       email = :email WHERE id = :id
            """), {"now": now, "rows": audit.get("rows"), "email": normalise_email(person["email"]), "id": row["id"]})
            await write_audit_log(db, tenant_id=tenant_id, user_id=None, action=audit["action"],
                                  resource_type=audit["resource_type"], resource_id=audit["resource_id"],
                                  detail=audit["detail"])
            await db.commit()
            counts["sent"] += 1
        except Exception as exc:
            await db.rollback()
            attempts = (row["attempts"] or 0) + 1
            await _scope(db, tenant_id)
            await db.execute(text("""
                UPDATE ops_emails SET status = 'FAILED', last_error = :err, scheduled_at = :next WHERE id = :id
            """), {"id": row["id"], "err": delivery_error(exc), "next": next_attempt_at(attempts=attempts, now=now)})
            await db.commit()
            counts["failed"] += 1
            logger.warning("operations email %s failed (attempt %d of %d): %s", row["id"], attempts, MAX_ATTEMPTS, exc)
    return counts


async def run(session_factory, now: datetime | None = None, deliver: Deliver | None = None) -> dict:
    """One pass over every organisation: queue what fell due, then send. One organisation's trouble is its own."""
    now = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        tenants = [str(r[0]) for r in await db.execute(text("SELECT id FROM tenants WHERE is_active = TRUE"))]
    out = {"queued": 0, "sent": 0, "failed": 0, "left_out": 0, "errors": 0}
    for tenant_id in tenants:
        try:
            async with session_factory() as db:
                await _scope(db, tenant_id)
                out["queued"] += await queue_due_reports(db, now)
                await db.commit()
                for k, v in (await process_queue(db, tenant_id, now, deliver)).items():
                    out[k] += v
        except Exception:
            logger.exception("operations emails: tenant %s", tenant_id)
            out["errors"] += 1
    return out


async def smtp_send(to: list[str], subject: str, body: str, attachments: list[Attachment]) -> None:  # pragma: no cover
    """Hand one email to the platform's mail server, with the settings every other email of the product uses."""
    from email.message import EmailMessage

    import aiosmtplib

    msg = EmailMessage()
    msg["Subject"] = " ".join(subject.split())[:200]
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    for filename, content, maintype, subtype in attachments:
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    await aiosmtplib.send(msg, hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
                          username=settings.SMTP_USER or None, password=settings.SMTP_PASSWORD or None,
                          use_tls=(settings.SMTP_PORT == 465), start_tls=(settings.SMTP_PORT == 587),
                          timeout=SMTP_TIMEOUT_S)
