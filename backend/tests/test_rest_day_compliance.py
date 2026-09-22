"""Rest days the roster does not give, whoever built it.

roster.max_consecutive_days (6) and roster.min_rest_hours (11) have existed as
tenant settings all along, and roster_autoschedule honours both — when IT
chooses who to place. Nothing looked at a roster somebody else built, and the
demo database holds a guard on 19 consecutive days to prove it.

The run-grouping tests are pure functions. An off-by-one there is silent: a
seven-day stretch reported as six is a breach nobody is told about, and nobody
recomputes it by hand.

Sections:
  A — Grouping days into runs (6 tests)
  B — Which runs break the limit (3 tests)
  C — Rest between shifts (4 tests)
  D — Against the database (3 tests)
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
from app.services import rest_day_compliance as rd

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

D = date


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


# ─── A. Grouping days into runs ──────────────────────────────────────────────

def test_no_days_is_no_runs():
    assert rd.consecutive_runs([]) == []


def test_a_single_day_is_a_run_of_one():
    assert rd.consecutive_runs([D(2026, 9, 1)]) == [(D(2026, 9, 1), D(2026, 9, 1), 1)]


def test_consecutive_days_form_one_run():
    days = [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)]
    assert rd.consecutive_runs(days) == [(D(2026, 9, 1), D(2026, 9, 3), 3)]


def test_a_gap_starts_a_new_run():
    """The rest day is the gap. Missing it would merge two lawful stretches
    into one apparent breach."""
    days = [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 4), D(2026, 9, 5)]
    assert rd.consecutive_runs(days) == [
        (D(2026, 9, 1), D(2026, 9, 2), 2),
        (D(2026, 9, 4), D(2026, 9, 5), 2),
    ]


def test_two_shifts_on_one_day_are_still_one_day():
    """A double shift is hard on a guard but it is not two days without rest,
    and counting it as two would invent a breach."""
    days = [D(2026, 9, 1), D(2026, 9, 1), D(2026, 9, 2)]
    assert rd.consecutive_runs(days) == [(D(2026, 9, 1), D(2026, 9, 2), 2)]


def test_unsorted_input_is_handled():
    """Rows arrive ordered by start time, but a guard with shifts at two sites
    can interleave. Sorting here means the caller cannot get it wrong."""
    days = [D(2026, 9, 3), D(2026, 9, 1), D(2026, 9, 2)]
    assert rd.consecutive_runs(days) == [(D(2026, 9, 1), D(2026, 9, 3), 3)]


# ─── B. Which runs break the limit ───────────────────────────────────────────

def test_exactly_the_maximum_is_not_a_breach():
    """Six days with a maximum of six is lawful. Flagging it teaches people to
    ignore the warning, and then the seven-day one goes unread too."""
    days = [D(2026, 9, 1) + timedelta(days=i) for i in range(6)]
    assert rd.runs_over(days, 6) == []


def test_one_day_past_the_maximum_is_a_breach():
    days = [D(2026, 9, 1) + timedelta(days=i) for i in range(7)]
    assert rd.runs_over(days, 6) == [(D(2026, 9, 1), D(2026, 9, 7), 7)]


def test_only_the_offending_run_is_returned():
    days = ([D(2026, 9, 1) + timedelta(days=i) for i in range(8)]
            + [D(2026, 9, 20), D(2026, 9, 21)])
    over = rd.runs_over(days, 6)
    assert len(over) == 1 and over[0][2] == 8


# ─── C. Rest between shifts ──────────────────────────────────────────────────

def _dt(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def test_a_full_night_between_shifts_is_fine():
    spans = [(_dt(1, 8), _dt(1, 16)), (_dt(2, 8), _dt(2, 16))]
    assert rd.rest_gaps(spans, 11) == ([], [])


def test_too_little_rest_is_reported_with_the_gap():
    """Off at 22:00, back at 06:00 is eight hours. With an eleven-hour minimum
    that is the classic back-to-back the rule exists to stop."""
    spans = [(_dt(1, 14), _dt(1, 22)), (_dt(2, 6), _dt(2, 14))]
    short, overlaps = rd.rest_gaps(spans, 11)
    assert len(short) == 1 and short[0][2] == 8.0
    assert overlaps == [], "a short rest was miscounted as a double-booking"


def test_a_single_shift_cannot_breach_a_rest_rule():
    assert rd.rest_gaps([(_dt(1, 8), _dt(1, 16))], 11) == ([], [])


def test_an_overlap_is_reported_separately_from_a_short_rest():
    """THE ONE REAL DATA FORCED. A guard rostered in two places at once is a
    different and worse fact than a short rest, and the first version returned
    both in one list. Against the live roster that produced sixty-seven "short
    rests", nearly all double-bookings, which would have buried every genuine
    short-rest case underneath them.
    """
    spans = [(_dt(1, 8), _dt(1, 18)), (_dt(1, 16), _dt(2, 0))]
    short, overlaps = rd.rest_gaps(spans, 11)
    assert overlaps and overlaps[0][2] < 0
    assert short == [], "the overlap was also counted as a short rest"


def test_an_overlap_and_a_short_rest_land_in_their_own_lists():
    """Both present at once: the pair must be separated, not merged."""
    spans = [
        (_dt(1, 8), _dt(1, 18)), (_dt(1, 16), _dt(1, 22)),   # overlap
        (_dt(2, 6), _dt(2, 14)),                              # 8h after 22:00
    ]
    short, overlaps = rd.rest_gaps(spans, 11)
    assert len(overlaps) == 1 and len(short) == 1


# ─── D. Against the database ─────────────────────────────────────────────────

async def _world(*, consecutive_days: int):
    i = {k: uuid.uuid4() for k in ("tenant", "site", "guard")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Rest Co',:s)",
               {"t": i["tenant"], "s": f"rest-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,5,:e,'x','Tired Guard')",
        {"i": i["guard"], "t": i["tenant"], "e": f"g-{i['guard'].hex[:8]}@rest.test"})

    first = date.today()
    for n in range(consecutive_days):
        start = datetime.combine(first + timedelta(days=n), time(8, 0),
                                 tzinfo=timezone.utc)
        await _sql(
            "INSERT INTO shifts (tenant_id, guard_user_id, site_id, "
            "                    scheduled_start, scheduled_end, status) "
            "VALUES (:t,:g,:s,:a,:b,'scheduled')",
            {"t": i["tenant"], "g": i["guard"], "s": i["site"],
             "a": start, "b": start + timedelta(hours=8)})
    return i


async def _assess(tenant_id):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(tenant_id)})
            return await rd.assess(s, window_start=date.today(),
                                   window_end=date.today() + timedelta(days=60))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_lawful_roster_raises_nothing():
    w = await _world(consecutive_days=6)
    r = await _assess(w["tenant"])
    mine = [x for x in r["over_run"] if x["guard_user_id"] == str(w["guard"])]
    assert mine == [], mine


@pytest.mark.asyncio
async def test_a_guard_rostered_past_the_limit_is_reported():
    w = await _world(consecutive_days=9)
    r = await _assess(w["tenant"])
    mine = [x for x in r["over_run"] if x["guard_user_id"] == str(w["guard"])]
    assert mine, r["over_run"]
    assert mine[0]["consecutive_days"] == 9
    assert mine[0]["guard_name"] == "Tired Guard"


@pytest.mark.asyncio
async def test_the_limit_comes_from_the_tenant_setting_not_a_constant():
    """The planner reads roster.max_consecutive_days; so must this. A second
    copy would drift, and an agency that raised its limit would be told off by
    one half of the system for a roster the other half approved.
    """
    w = await _world(consecutive_days=9)
    await _sql(
        "INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, "
        "                             updated_by_user_id) "
        "VALUES (:t,'roster.max_consecutive_days','10'::jsonb,:u) "
        "ON CONFLICT (tenant_id, setting_key) DO UPDATE "
        "  SET setting_value = EXCLUDED.setting_value",
        {"t": w["tenant"], "u": w["guard"]})

    r = await _assess(w["tenant"])
    assert r["max_consecutive_days"] == 10, r["max_consecutive_days"]
    mine = [x for x in r["over_run"] if x["guard_user_id"] == str(w["guard"])]
    assert mine == [], "nine days was reported despite the tenant allowing ten"
