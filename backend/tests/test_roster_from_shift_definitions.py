"""Auto-Schedule plans a site with no roster pattern from the shift definitions and the site's headcounts.

Found 25 October 2026 (docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 1): the demo
organisation had Day and Night shift definitions and four sites that each need guards, but no roster pattern, and
Auto-Schedule saved an empty draft. Also fixed here: times are the organisation's own (they were UTC, so a 07:00
start became 15:00 in Singapore), past days are not planned, and nothing to plan is refused with the reason.
Through the real endpoint, as the restricted app user.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app  # noqa: F401 - the import every backend test makes first
from tests.test_shifts import _admin_engine

SGT = ZoneInfo("Asia/Singapore")


async def _sql(statements: list[tuple[str, dict]]):
    engine = _admin_engine()
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        for sql, params in statements:
            await s.execute(text(sql), params)
        await s.commit()
    await engine.dispose()


async def _org(*, guards: int = 3, day_needed: int = 2, night_needed: int = 1, definitions: bool = True,
               pattern_at: str | None = None) -> dict:
    """An organisation (Singapore time) with one site, its admin, `guards` guards, the Day and Night shift
    definitions, and optionally one roster pattern at the site."""
    from app.core.security import create_access_token, hash_password

    t, site, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    guard_ids = [uuid.uuid4() for _ in range(guards)]
    statements = [
        ("INSERT INTO tenants (id, name, slug, timezone) VALUES (:id, 'Roster', :s, 'Asia/Singapore')",
         {"id": t, "s": f"rost-{t.hex[:8]}"}),
        ("INSERT INTO sites (id, tenant_id, name, day_guards_required, night_guards_required) "
         "VALUES (:id, :t, 'Site One', :d, :n)", {"id": site, "t": t, "d": day_needed, "n": night_needed}),
        ("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (:id, :t, 2, :e, :pw)",
         {"id": admin, "t": t, "e": f"admin-{admin.hex[:6]}@test.local", "pw": hash_password("x")}),
    ]
    for g in guard_ids:
        statements.append(("INSERT INTO users (id, tenant_id, role_id, email, hashed_password) "
                           "VALUES (:id, :t, 5, :e, :pw)",
                           {"id": g, "t": t, "e": f"g-{g.hex[:6]}@test.local", "pw": hash_password("x")}))
    if definitions:
        statements += [
            ("INSERT INTO shift_definitions (tenant_id, name, shift_type, start_time, duration_minutes) "
             "VALUES (:t, 'Day Shift', 'day', '07:00', 720)", {"t": t}),
            ("INSERT INTO shift_definitions (tenant_id, name, shift_type, start_time, duration_minutes) "
             "VALUES (:t, 'Night Shift', 'night', '19:00', 720)", {"t": t}),
        ]
    if pattern_at:
        statements.append(("INSERT INTO shift_patterns (tenant_id, site_id, guard_user_id, label, days_of_week, "
                           "start_time, duration_minutes) VALUES (:t, :s, :g, 'Pattern', :dow, :st, 480)",
                           {"t": t, "s": site, "g": guard_ids[0], "dow": [0, 1, 2, 3, 4, 5, 6],
                            "st": time.fromisoformat(pattern_at)}))
    await _sql(statements)
    return {"token": create_access_token(str(admin), str(t), 2), "site": str(site)}


async def _auto(org: dict, start: date, end: date):
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {org['token']}"
        return await c.post("/api/v1/roster/auto-schedule",
                            json={"period_start": start.isoformat(), "period_end": end.isoformat()})


def _today() -> date:
    return datetime.now(SGT).date()


@pytest.mark.asyncio
async def test_a_site_with_no_pattern_is_planned_from_the_shift_definitions_in_its_own_time():
    org = await _org(guards=4, day_needed=2, night_needed=1)
    start = _today() + timedelta(days=1)
    r = await _auto(org, start, start + timedelta(days=2))
    assert r.status_code == 201, r.text
    batch = r.json()
    shifts = batch["draft_shifts"]
    assert len(shifts) == 3 * 3, "two day posts and one night post, for each of three days"
    for s in shifts:
        local = datetime.fromisoformat(s["scheduled_start"]).astimezone(SGT)
        expected = time(7, 0) if s["shift_type"] == "day" else time(19, 0)
        assert local.time() == expected, (s["shift_type"], local)
        assert (datetime.fromisoformat(s["scheduled_end"]) - datetime.fromisoformat(s["scheduled_start"])) == timedelta(hours=12)
    assert sum(s["guard_user_id"] is not None for s in shifts) >= 6, "filled under the usual rules"
    summary = batch["rules_summary"]
    assert summary["sites_from_shift_definitions"] == 1 and summary["sites_from_patterns"] == 0
    assert summary["time_zone"] == "Asia/Singapore"


@pytest.mark.asyncio
async def test_a_site_with_a_pattern_keeps_its_pattern_and_its_time_is_the_organisations():
    org = await _org(guards=3, day_needed=1, night_needed=1, pattern_at="08:00")
    start = _today() + timedelta(days=1)
    r = await _auto(org, start, start)
    assert r.status_code == 201, r.text
    shifts = r.json()["draft_shifts"]
    starts = {datetime.fromisoformat(s["scheduled_start"]).astimezone(SGT).time() for s in shifts}
    assert starts == {time(8, 0)}, "the pattern's 08:00, in Singapore time; the shift definitions are not used here"
    assert r.json()["rules_summary"]["sites_from_patterns"] == 1


@pytest.mark.asyncio
async def test_days_already_gone_are_not_planned():
    org = await _org()
    today = _today()
    r = await _auto(org, today - timedelta(days=10), today + timedelta(days=1))
    assert r.status_code == 201, r.text
    batch = r.json()
    assert batch["period_start"] == today.isoformat()
    assert batch["rules_summary"]["planned_from"] == today.isoformat()
    assert batch["rules_summary"]["requested_from"] == (today - timedelta(days=10)).isoformat()
    assert min(datetime.fromisoformat(s["scheduled_start"]).astimezone(SGT).date() for s in batch["draft_shifts"]) >= today
    r = await _auto(org, today - timedelta(days=10), today - timedelta(days=1))
    assert r.status_code == 422 and "in the past" in r.json()["detail"]


@pytest.mark.asyncio
async def test_nothing_to_plan_is_refused_with_the_reason_and_no_empty_draft_is_saved():
    start = _today() + timedelta(days=1)
    no_definitions = await _org(definitions=False)
    r = await _auto(no_definitions, start, start)
    assert r.status_code == 422 and "no active shift definitions" in r.json()["detail"]
    no_guards = await _org(guards=0)
    r = await _auto(no_guards, start, start)
    assert r.status_code == 422 and "no active guards" in r.json()["detail"]
    nobody_needed = await _org(day_needed=0, night_needed=0)
    r = await _auto(nobody_needed, start, start)
    assert r.status_code == 422 and "needs 0 guards" in r.json()["detail"]
    # None of them left a draft behind.
    engine = _admin_engine()
    async with engine.connect() as conn:
        for org in (no_definitions, no_guards, nobody_needed):
            n = (await conn.execute(text("SELECT count(*) FROM roster_batches b JOIN sites s ON s.tenant_id = b.tenant_id "
                                         "WHERE s.id = CAST(:s AS uuid)"), {"s": org["site"]})).scalar()
            assert n == 0
    await engine.dispose()
