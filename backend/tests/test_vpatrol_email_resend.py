"""Recovering a report or digest that failed past its retry budget.

WHY THIS EXISTS. Retries span about five hours across five attempts and then the
row rests at FAILED. For a digest that is terminal: uq_vpeq_digest_period allows
one digest per (schedule, frequency, period), so no replacement can ever be
queued for that window. A mail outage over a weekend would silently cost a
client their weekly summary, and the only trace would be a FAILED row nobody
looks at. The constraint that prevents duplicates is precisely what makes the
loss permanent, so recovery has to be an explicit action.

Sections:
  A — A failed email goes back in the queue (3 tests)
  B — What resend refuses, and why (3 tests)
  C — The recovered digest actually sends (1 test)
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app
from app.core.security import create_access_token
from app.services import vpatrol_email as mail

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

ADMIN_ROLE = 2
QUEUE = "/api/v1/virtual-patrol/email-queue"


async def _sql(stmt: str, params: dict | None = None):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app), base_url="http://test")


async def _world(*, status: str = "FAILED", attempts: int = 5,
                 frequency: str = "DAILY"):
    """A schedule with one queue row in the given state."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "sched", "queued", "admin")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Resend Co',:s)",
               {"t": i["tenant"], "s": f"vprs-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql(
        "INSERT INTO virtual_patrol_schedules "
        "  (id, tenant_id, site_id, name, schedule_type, start_date, patrol_time, "
        "   timezone, email_frequency) "
        "VALUES (:i,:t,:s,'Morning Patrol','DAILY',:d,:pt,'UTC',:f)",
        {"i": i["sched"], "t": i["tenant"], "s": i["site"],
         "d": date(2026, 1, 1), "pt": time(7, 0), "f": frequency})

    period = date(2026, 9, 11)
    is_digest = frequency != "IMMEDIATE"
    await _sql(
        "INSERT INTO virtual_patrol_email_queue "
        "  (id, tenant_id, schedule_id, session_id, frequency, recipients, subject, "
        "   status, attempts, last_error, period_start, period_end) "
        "VALUES (:i,:t,:sc,NULL,:f,'ops@example.test','Summary',:st,:a,"
        "        'smtp: connection refused',:p,:p)"
        if is_digest else
        "INSERT INTO virtual_patrol_email_queue "
        "  (id, tenant_id, schedule_id, session_id, frequency, recipients, subject, "
        "   status, attempts, last_error) "
        "VALUES (:i,:t,:sc,NULL,:f,'ops@example.test','Report',:st,:a,"
        "        'smtp: connection refused')",
        {"i": i["queued"], "t": i["tenant"], "sc": i["sched"], "f": frequency,
         "st": status, "a": attempts, **({"p": period} if is_digest else {})})

    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,CAST(:r AS smallint),:e,'x','Resend Admin')",
        {"i": i["admin"], "t": i["tenant"], "r": ADMIN_ROLE,
         "e": f"resend-{i['admin'].hex[:8]}@rs.test"})
    i["headers"] = {"Authorization":
                    f"Bearer {create_access_token(str(i['admin']), str(i['tenant']), ADMIN_ROLE)}"}
    i["period"] = period
    return i


# ─── A. A failed email goes back in the queue ────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_digest_can_be_resent():
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{QUEUE}/{w['queued']}/resend", headers=w["headers"])
    assert r.status_code == 200, r.text

    row = (await _sql("SELECT status, attempts, last_error, scheduled_at "
                      "  FROM virtual_patrol_email_queue WHERE id = :i",
                      {"i": w["queued"]}))[0]
    assert row["status"] == "PENDING"
    assert row["attempts"] == 0, "the retry budget was not restored"
    assert row["last_error"] is None, "the stale error was left on the row"


@pytest.mark.asyncio
async def test_the_attempt_count_is_reset_not_merely_decremented():
    """The row gets its full budget again. One last try against a mail server
    that may still be recovering is not a recovery."""
    w = await _world(attempts=mail.MAX_ATTEMPTS)
    async with _client() as c:
        await c.post(f"{QUEUE}/{w['queued']}/resend", headers=w["headers"])
    row = (await _sql("SELECT attempts FROM virtual_patrol_email_queue WHERE id = :i",
                      {"i": w["queued"]}))[0]
    assert row["attempts"] == 0


@pytest.mark.asyncio
async def test_the_queue_can_be_listed_and_filtered_to_failures():
    """Without a listing the id is unobtainable, and a resend endpoint nobody
    can address is not a recovery path."""
    w = await _world()
    async with _client() as c:
        r = await c.get(QUEUE, headers=w["headers"], params={"status": "FAILED"})
    assert r.status_code == 200, r.text
    mine = [q for q in r.json() if q["id"] == str(w["queued"])]
    assert mine, "the failed row is not in the listing"
    assert mine[0]["status"] == "FAILED"
    assert "smtp" in (mine[0]["last_error"] or ""), "the reason is not surfaced"
    assert mine[0]["period_start"] == str(w["period"]), mine[0]["period_start"]


# ─── B. What resend refuses, and why ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_resending_an_already_sent_email_is_refused():
    """A second copy of an email that DID arrive is a different decision, and
    should not be a side effect of a button labelled "resend"."""
    w = await _world(status="SENT")
    async with _client() as c:
        r = await c.post(f"{QUEUE}/{w['queued']}/resend", headers=w["headers"])
    assert r.status_code == 409, r.text
    assert "SENT" in r.text

    row = (await _sql("SELECT status FROM virtual_patrol_email_queue WHERE id = :i",
                      {"i": w["queued"]}))[0]
    assert row["status"] == "SENT", "a sent email was moved back to PENDING"


@pytest.mark.asyncio
async def test_resending_a_pending_email_is_refused():
    """It is already going to be tried. Resetting it would just hide how long
    it has been waiting."""
    w = await _world(status="PENDING", attempts=0)
    async with _client() as c:
        r = await c.post(f"{QUEUE}/{w['queued']}/resend", headers=w["headers"])
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_an_unknown_id_is_a_404_not_a_409():
    """The two are different problems: one is a typo, the other is a state the
    operator can reason about."""
    w = await _world()
    async with _client() as c:
        r = await c.post(f"{QUEUE}/{uuid.uuid4()}/resend", headers=w["headers"])
    assert r.status_code == 404, r.text


# ─── C. The recovered digest actually sends ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_resent_digest_is_picked_up_and_sent():
    """The whole point. Requeueing is worthless if the worker skips the row --
    process_queue selects on status and attempts < MAX, so both had to be reset
    for it to be eligible again.
    """
    w = await _world(attempts=mail.MAX_ATTEMPTS)
    async with _client() as c:
        r = await c.post(f"{QUEUE}/{w['queued']}/resend", headers=w["headers"])
    assert r.status_code == 200, r.text

    seen = []

    async def fake_send(db, *, session_id, recipients, subject, **kw):
        seen.append({"subject": subject, "frequency": kw.get("frequency"),
                     "period": kw.get("period")})

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await mail.process_queue(s, send=fake_send)
    finally:
        await engine.dispose()

    mine = [x for x in seen if x["subject"] == "Summary"]
    assert mine, f"the resent digest was not picked up (saw {len(seen)} sends)"
    assert mine[0]["frequency"] == "DAILY"
    assert mine[0]["period"][0] == w["period"], mine[0]["period"]

    row = (await _sql("SELECT status FROM virtual_patrol_email_queue WHERE id = :i",
                      {"i": w["queued"]}))[0]
    assert row["status"] == "SENT"
