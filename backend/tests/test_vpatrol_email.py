"""The report email queue: what gets queued, what retries, what gives up.

SMTP is never touched — the sender is injected, so these tests exercise the
queue's behaviour rather than a mail server's mood.

Sections:
  A — What gets queued (3 tests)
  B — Sending, and claiming before sending (2 tests)
  C — Failure, backoff, and giving up visibly (4 tests)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401
from app.services import vpatrol_email as mail

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _factory():
    engine = create_async_engine(ADMIN_DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _sql(stmt: str, params: dict | None = None):
    engine, factory = _factory()
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


async def _patrol(*, recipients: list[str], frequency: str = "IMMEDIATE"):
    """A completed session whose schedule has the given recipients."""
    ids = {k: uuid.uuid4() for k in ("tenant", "site", "sched", "sess")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Mail Co',:s)",
               {"t": ids["tenant"], "s": f"vpmail-{ids['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": ids["site"], "t": ids["tenant"]})
    await _sql(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time, "
        "   email_frequency) "
        "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt,:f)",
        {"i": ids["sched"], "t": ids["tenant"], "s": ids["site"],
         "d": date(2026, 1, 1), "pt": time(7, 0), "f": frequency})
    for email in recipients:
        await _sql(
            "INSERT INTO virtual_patrol_email_recipients (tenant_id, schedule_id, email) "
            "VALUES (:t,:s,:e)",
            {"t": ids["tenant"], "s": ids["sched"], "e": email})
    await _sql(
        "INSERT INTO virtual_patrol_sessions "
        "  (id, tenant_id, site_id, schedule_id, patrol_number, schedule_name, "
        "   scheduled_for, status) "
        "VALUES (:i,:t,:s,:sc,'VP-MAIL','Morning Patrol',:w,'COMPLETED')",
        {"i": ids["sess"], "t": ids["tenant"], "s": ids["site"], "sc": ids["sched"],
         "w": datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)})
    return ids


async def _enqueue(ids):
    engine, factory = _factory()
    try:
        async with factory() as s:
            queued = await mail.enqueue_completed_patrol(s, str(ids["sess"]))
            await s.commit()
        return queued
    finally:
        await engine.dispose()


async def _drain(send, now=None):
    engine, factory = _factory()
    try:
        async with factory() as s:
            return await mail.process_queue(s, send=send, now=now)
    finally:
        await engine.dispose()


# ─── A. What gets queued ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_completed_patrol_queues_a_report():
    ids = await _patrol(recipients=["ops@example.com"])
    assert await _enqueue(ids) is not None
    rows = await _sql("SELECT status, recipients FROM virtual_patrol_email_queue "
                      " WHERE session_id = :s", {"s": ids["sess"]})
    assert rows[0][0] == "PENDING"
    assert rows[0][1] == "ops@example.com"


@pytest.mark.asyncio
async def test_a_patrol_with_no_recipients_queues_nothing():
    """A row addressed to nobody would just fail five times and sit there
    looking like a problem."""
    ids = await _patrol(recipients=[])
    assert await _enqueue(ids) is None
    rows = await _sql("SELECT count(*) FROM virtual_patrol_email_queue "
                      " WHERE session_id = :s", {"s": ids["sess"]})
    assert rows[0][0] == 0


@pytest.mark.asyncio
async def test_a_digest_schedule_does_not_queue_an_immediate_report():
    """Somebody who asked for a weekly digest has said, in as many words, that
    they do not want an email per patrol."""
    ids = await _patrol(recipients=["ops@example.com"], frequency="WEEKLY")
    assert await _enqueue(ids) is None


# ─── B. Sending ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_queued_report_is_sent_and_marked():
    ids = await _patrol(recipients=["ops@example.com", "boss@example.com"])
    await _enqueue(ids)
    seen = {}

    async def fake_send(db, *, session_id, recipients, subject):
        seen["recipients"] = recipients
        seen["subject"] = subject

    result = await _drain(fake_send)
    assert result["sent"] >= 1
    assert seen["recipients"] == ["boss@example.com", "ops@example.com"]
    rows = await _sql("SELECT status, sent_at IS NOT NULL, attempts "
                      "  FROM virtual_patrol_email_queue WHERE session_id = :s",
                      {"s": ids["sess"]})
    assert rows[0][0] == "SENT"
    assert rows[0][1] is True
    assert rows[0][2] == 1


@pytest.mark.asyncio
async def test_a_sent_report_is_not_sent_again_on_the_next_run():
    """The queue is drained every scheduler tick. Re-sending a completed patrol
    every minute is the kind of bug that gets a sender domain blocked."""
    ids = await _patrol(recipients=["ops@example.com"])
    await _enqueue(ids)
    calls = []

    async def fake_send(db, *, session_id, recipients, subject):
        calls.append(session_id)

    await _drain(fake_send)
    await _drain(fake_send)
    assert len(calls) == 1, f"sent {len(calls)} times"


# ─── C. Failure, backoff, giving up visibly ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_send_records_the_reason_and_retries_later():
    ids = await _patrol(recipients=["ops@example.com"])
    await _enqueue(ids)

    async def boom(db, *, session_id, recipients, subject):
        raise RuntimeError("smtp: connection refused")

    now = datetime.now(timezone.utc)
    result = await _drain(boom, now=now)
    assert result["failed"] == 1
    rows = await _sql("SELECT status, attempts, last_error, scheduled_at "
                      "  FROM virtual_patrol_email_queue WHERE session_id = :s",
                      {"s": ids["sess"]})
    assert rows[0][0] == "FAILED"
    assert rows[0][1] == 1
    assert "connection refused" in rows[0][2]
    assert rows[0][3] > now, "the retry was not pushed into the future"


@pytest.mark.asyncio
async def test_a_failed_send_is_not_retried_before_its_backoff():
    """Otherwise a dead mail server is hammered once per scheduler tick."""
    ids = await _patrol(recipients=["ops@example.com"])
    await _enqueue(ids)
    calls = []

    async def boom(db, *, session_id, recipients, subject):
        calls.append(1)
        raise RuntimeError("smtp down")

    now = datetime.now(timezone.utc)
    await _drain(boom, now=now)
    await _drain(boom, now=now)          # same instant: still inside the backoff
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_it_gives_up_after_the_attempt_limit_but_stays_visible():
    """FAILED with the reason attached, never deleted. An email nobody can prove
    was never sent is worse than one plainly marked failed."""
    ids = await _patrol(recipients=["ops@example.com"])
    await _enqueue(ids)

    async def boom(db, *, session_id, recipients, subject):
        raise RuntimeError("smtp down")

    now = datetime.now(timezone.utc)
    for _ in range(mail.MAX_ATTEMPTS + 2):
        now += timedelta(hours=6)        # past any backoff
        await _drain(boom, now=now)

    rows = await _sql("SELECT status, attempts, last_error "
                      "  FROM virtual_patrol_email_queue WHERE session_id = :s",
                      {"s": ids["sess"]})
    assert rows[0][0] == "FAILED"
    assert rows[0][1] == mail.MAX_ATTEMPTS, "attempts ran past the limit"
    assert rows[0][2], "the reason was lost"


def test_backoff_grows_rather_than_hammering():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    gaps = [(mail.next_attempt_at(attempts=n, now=now) - now).total_seconds()
            for n in range(mail.MAX_ATTEMPTS)]
    assert gaps == sorted(gaps), gaps
    assert gaps[-1] > gaps[0]
