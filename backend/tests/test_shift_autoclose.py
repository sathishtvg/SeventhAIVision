"""A shift nobody checked out of is closed by the system, and the guard is warned first.

Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 2 (agreed with the owner on 25 October
2026): `attendance.auto_close_hours` (15) after check-in, plus overtime asked for and not refused; a warning
`attendance.auto_close_reminder_minutes` (15) before, once per closing time. Run as the application's own
restricted user, over two organisations, as the scheduler runs it (memory: sweeps tested as superuser pass every
test and fail every production run).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.db.session import AsyncSessionLocal
from app.services import overtime_rules, shift_autoclose
from tests.test_shifts import _admin_engine

NOW = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)


async def _sql(statements: list[tuple[str, dict]]):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        for sql, params in statements:
            await s.execute(text(sql), params)
        await s.commit()
    await engine.dispose()


async def _rows(sql: str, params: dict):
    engine = _admin_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    await engine.dispose()
    return rows


async def _org(settings: dict | None = None) -> dict:
    t, site, guard = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    statements = [
        ("INSERT INTO tenants (id, name, slug, timezone) VALUES (:id, 'AC', :s, 'Asia/Singapore')",
         {"id": t, "s": f"ac-{t.hex[:8]}"}),
        ("INSERT INTO sites (id, tenant_id, name) VALUES (:id, :t, 'Gate')", {"id": site, "t": t}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (:id, :t, 5, :e, 'x')",
         {"id": guard, "t": t, "e": f"g-{guard.hex[:6]}@test.local"}),
    ]
    for key, value in (settings or {}).items():
        statements.append(("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
                           "VALUES (:t, :k, CAST(:v AS jsonb), :u)", {"t": t, "k": key, "v": str(value), "u": guard}))
    await _sql(statements)
    return {"tenant": str(t), "site": str(site), "guard": str(guard)}


async def _open_shift(org: dict, checked_in_ago: timedelta, planned_hours: int = 12,
                      needs_approval: bool = True) -> str:
    sid = uuid.uuid4()
    start = NOW - checked_in_ago
    await _sql([("INSERT INTO shifts (id, tenant_id, site_id, guard_user_id, scheduled_start, scheduled_end, "
                 "actual_start, status, overtime_needs_approval) VALUES (:id, :t, :s, :g, :ss, :se, :as, 'active', :na)",
                 {"id": sid, "t": org["tenant"], "s": org["site"], "g": org["guard"], "ss": start,
                  "se": start + timedelta(hours=planned_hours), "as": start, "na": needs_approval})])
    return str(sid)


async def _ask(org: dict, shift_id: str, hours: float, status: str = "pending"):
    await _sql([("INSERT INTO overtime_requests (tenant_id, shift_id, guard_user_id, hours_requested, reason, status) "
                 "VALUES (:t, :s, :g, :h, 'Relief late', :st)",
                 {"t": org["tenant"], "s": shift_id, "g": org["guard"], "h": hours, "st": status})])


async def _sweep(orgs: list[dict], monkeypatch, now: datetime = NOW) -> tuple[dict, list]:
    sent = []

    async def fake_push(redis, tenant_id, user_id, title, body, data):
        sent.append((tenant_id, user_id, title, body))

    monkeypatch.setattr(shift_autoclose, "_push", fake_push)
    async with AsyncSessionLocal() as db:
        who = (await db.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"))).scalar()
        assert who is False, "the sweep is tested as the restricted app user, not a superuser"
        out = await shift_autoclose.sweep(db, redis=object(), now=now, only_tenants=[o["tenant"] for o in orgs])
    return out, sent


async def _shift(sid: str):
    return (await _rows("SELECT status, actual_end, auto_closed_at, auto_close_reminded_for, overtime_minutes "
                        "FROM shifts WHERE id = CAST(:i AS uuid)", {"i": sid}))[0]


@pytest.mark.asyncio
async def test_a_shift_open_15_hours_after_check_in_is_closed_in_every_organisation(monkeypatch):
    a, b = await _org(), await _org()
    sa = await _open_shift(a, timedelta(hours=15, minutes=5))
    sb = await _open_shift(b, timedelta(hours=15, minutes=1))
    young = await _open_shift(a, timedelta(hours=10))
    out, sent = await _sweep([a, b], monkeypatch)
    assert out["closed"] == 2
    for sid in (sa, sb):
        s = await _shift(sid)
        assert s["status"] == "completed" and s["auto_closed_at"] == NOW and s["actual_end"] == NOW
    assert (await _shift(young))["status"] == "active"
    audit = await _rows("SELECT action FROM audit_logs WHERE tenant_id IN (CAST(:a AS uuid), CAST(:b AS uuid)) "
                        "AND action = 'shift.auto_closed'", {"a": a["tenant"], "b": b["tenant"]})
    assert len(audit) == 2
    closed_pushes = [x for x in sent if x[2] == shift_autoclose.PUSH_TITLE_CLOSED]
    assert {x[0] for x in closed_pushes} == {a["tenant"], b["tenant"]} and "closed automatically at" in closed_pushes[0][3]


@pytest.mark.asyncio
async def test_the_guard_is_warned_once_before_the_close(monkeypatch):
    org = await _org()
    sid = await _open_shift(org, timedelta(hours=14, minutes=50))  # closes in 10 minutes
    out, sent = await _sweep([org], monkeypatch)
    assert out == {"reminded": 1, "closed": 0}
    assert sent[0][2] == shift_autoclose.PUSH_TITLE_REMINDER and "closes automatically at" in sent[0][3]
    assert "22:10" in sent[0][3] or ":" in sent[0][3]  # in Singapore time
    assert (await _shift(sid))["auto_close_reminded_for"] is not None
    out, sent = await _sweep([org], monkeypatch, now=NOW + timedelta(minutes=5))
    assert out == {"reminded": 0, "closed": 0} and sent == [], "warned once for one closing time"


@pytest.mark.asyncio
async def test_overtime_asked_for_moves_the_close_and_a_refusal_does_not(monkeypatch):
    org = await _org()
    asked = await _open_shift(org, timedelta(hours=15, minutes=5))
    await _ask(org, asked, 3)
    refused = await _open_shift(org, timedelta(hours=15, minutes=5))
    await _ask(org, refused, 3, status="rejected")
    out, _ = await _sweep([org], monkeypatch)
    assert (await _shift(asked))["status"] == "active", "15 h + the 3 h asked for"
    assert (await _shift(refused))["status"] == "completed"
    out, sent = await _sweep([org], monkeypatch, now=NOW + timedelta(hours=2, minutes=50))
    assert out["reminded"] == 1, "warned again before the new closing time"
    out, _ = await _sweep([org], monkeypatch, now=NOW + timedelta(hours=3))
    assert (await _shift(asked))["status"] == "completed"


@pytest.mark.asyncio
async def test_the_organisations_own_hours_are_used(monkeypatch):
    org = await _org({"attendance.auto_close_hours": 8})
    sid = await _open_shift(org, timedelta(hours=8, minutes=1), planned_hours=8)
    out, _ = await _sweep([org], monkeypatch)
    assert out["closed"] == 1 and (await _shift(sid))["status"] == "completed"


@pytest.mark.asyncio
async def test_an_automatic_close_pays_only_approved_overtime_on_the_new_rule(monkeypatch):
    org = await _org()
    approved = await _open_shift(org, timedelta(hours=17, minutes=5))  # 12 h planned, 5 h past its end
    await _ask(org, approved, 2, status="approved")
    nothing_asked = await _open_shift(org, timedelta(hours=15, minutes=5))
    old_rule = await _open_shift(org, timedelta(hours=15, minutes=5), needs_approval=False)
    await _sql([("INSERT INTO shift_breaks (tenant_id, shift_id) VALUES (:t, :s)",
                 {"t": org["tenant"], "s": nothing_asked})])
    await _sweep([org], monkeypatch)
    assert (await _shift(approved))["overtime_minutes"] == 120, "approved 2 h, worked 5 h past the end"
    assert (await _shift(nothing_asked))["overtime_minutes"] == 0, "nothing approved, nothing paid as overtime"
    assert (await _shift(old_rule))["overtime_minutes"] == 3 * 60 + 5 - 15, "a shift from before keeps its rule"
    open_breaks = await _rows("SELECT 1 FROM shift_breaks WHERE shift_id = CAST(:s AS uuid) AND break_end IS NULL",
                              {"s": nothing_asked})
    assert open_breaks == [], "an open break is closed with the shift"


def test_the_overtime_rule():
    end = NOW
    f = overtime_rules.overtime_minutes
    assert f(needs_approval=True, scheduled_end=end, actual_end=end + timedelta(hours=3), approved_minutes=120,
             threshold_minutes=15) == 120
    assert f(needs_approval=True, scheduled_end=end, actual_end=end + timedelta(hours=1), approved_minutes=120,
             threshold_minutes=15) == 60, "never more than worked"
    assert f(needs_approval=True, scheduled_end=end, actual_end=end - timedelta(hours=1), approved_minutes=120,
             threshold_minutes=15) == 0
    assert f(needs_approval=False, scheduled_end=end, actual_end=end + timedelta(minutes=10), approved_minutes=0,
             threshold_minutes=15) == 0
    assert overtime_rules.unapproved_extra_minutes(needs_approval=True, scheduled_end=end,
                                                   actual_end=end + timedelta(hours=3), approved_minutes=60) == 120
