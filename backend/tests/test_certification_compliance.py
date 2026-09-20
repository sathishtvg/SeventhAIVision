"""Whether a guard holds what their shift requires — and when we say so.

The date tests are the point of this file, and they are pure functions. The
question is never "is this guard certified today" but "will they be certified on
the day they stand at that post", and those differ for every future shift. A
sweep that asks the wrong one produces a clean board for a roster full of guards
whose licences lapse next week.

Sections:
  A — One requirement against what a guard holds (9 tests)
  B — A whole shift, worst finding first (4 tests)
  C — The sweep: what it raises, and what it closes (5 tests)
  D — Tenant isolation, as svc_app (2 tests)
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
from app.services import certification_compliance as cc

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


def _svc_app_test_url() -> str:
    """The app's own credentials against the test database.

    Derived from DATABASE_URL rather than by string-replacing the admin URL: the
    replace approach silently yields the ADMIN url when credentials differ, and
    an isolation test that quietly reconnects as a BYPASSRLS superuser is worse
    than no isolation test at all.
    """
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        return ADMIN_DATABASE_URL
    base, _, db = raw.rpartition("/")
    db = db.split("?")[0]
    if not db.endswith("_test"):
        db = f"{db}_test"
    return f"{base}/{db}"


APP_DATABASE_URL = _svc_app_test_url()
LICENCE = "security_officer_license"
FIRE = "fire_warden"
DAY = date(2026, 6, 15)


async def _sql(stmt: str, params: dict | None = None, *, url: str | None = None):
    engine = create_async_engine(url or ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            r = await s.execute(text(stmt), params or {})
            rows = r.mappings().all() if r.returns_rows else []
            await s.commit()
        return rows
    finally:
        await engine.dispose()


# ─── A. One requirement against what a guard holds ───────────────────────────

def _req(warn: int = 30) -> cc.Requirement:
    return cc.Requirement(LICENCE, warn_days_before=warn)


def test_nothing_on_file_is_missing():
    assert cc.assess_one(_req(), [], on=DAY) == cc.MISSING


def test_a_different_certification_does_not_satisfy_the_requirement():
    """Holding a first-aid certificate is not holding a licence. An unanchored
    match here would let any certification satisfy any requirement."""
    held = [cc.Held(FIRE, date(2027, 1, 1))]
    assert cc.assess_one(_req(), held, on=DAY) == cc.MISSING


def test_valid_well_into_the_future_is_compliant():
    held = [cc.Held(LICENCE, DAY + timedelta(days=200))]
    assert cc.assess_one(_req(), held, on=DAY) == cc.COMPLIANT


def test_expired_before_the_shift_is_expired():
    held = [cc.Held(LICENCE, DAY - timedelta(days=1))]
    assert cc.assess_one(_req(), held, on=DAY) == cc.EXPIRED


def test_expiring_on_the_day_itself_is_still_valid_that_day():
    """A licence valid THROUGH its expiry date is valid on that date. Treating
    it as expired would stand down a guard who is lawfully able to work."""
    held = [cc.Held(LICENCE, DAY)]
    assert cc.assess_one(_req(), held, on=DAY) == cc.EXPIRING


def test_inside_the_warning_window_is_expiring():
    held = [cc.Held(LICENCE, DAY + timedelta(days=10))]
    assert cc.assess_one(_req(warn=30), held, on=DAY) == cc.EXPIRING


def test_just_outside_the_warning_window_is_compliant():
    """The boundary. 31 days out with a 30-day window is not yet a warning;
    off by one here means either crying wolf or arriving too late."""
    held = [cc.Held(LICENCE, DAY + timedelta(days=31))]
    assert cc.assess_one(_req(warn=30), held, on=DAY) == cc.COMPLIANT


def test_no_expiry_recorded_does_not_lapse():
    """A one-off qualification entered without a date must not read as expired,
    or every such record becomes a permanent false alarm."""
    held = [cc.Held(LICENCE, None)]
    assert cc.assess_one(_req(), held, on=DAY) == cc.COMPLIANT


def test_a_renewal_alongside_the_old_certificate_counts_as_renewed():
    """Guards keep the superseded record. The best one decides — otherwise a
    properly renewed officer reads as lapsed because the old row is still
    there."""
    held = [cc.Held(LICENCE, DAY - timedelta(days=30)),      # last year's
            cc.Held(LICENCE, DAY + timedelta(days=300))]     # the renewal
    assert cc.assess_one(_req(), held, on=DAY) == cc.COMPLIANT


def test_a_revoked_certificate_is_not_merely_expired():
    """Revoked is a different fact from lapsed, and the more serious one."""
    held = [cc.Held(LICENCE, DAY + timedelta(days=300), is_valid=False)]
    assert cc.assess_one(_req(), held, on=DAY) == cc.REVOKED


# ─── B. A whole shift ────────────────────────────────────────────────────────

def test_no_requirements_is_not_assessed_rather_than_compliant():
    """The distinction this module exists for. An agency that configured
    nothing must not see a clean board — it has not passed, it has not been
    looked at."""
    got = cc.assess_shift([], [cc.Held(LICENCE, None)], on=DAY)
    assert got["status"] == cc.NOT_ASSESSED
    assert got["findings"] == []


def test_the_worst_finding_leads():
    reqs = [cc.Requirement(LICENCE), cc.Requirement(FIRE)]
    held = [cc.Held(LICENCE, DAY + timedelta(days=5)),   # EXPIRING
            ]                                            # FIRE missing entirely
    got = cc.assess_shift(reqs, held, on=DAY)
    assert got["status"] == cc.MISSING, got
    assert got["findings"][0]["certification_type"] == FIRE


def test_every_requirement_is_reported_not_just_the_worst():
    reqs = [cc.Requirement(LICENCE), cc.Requirement(FIRE)]
    got = cc.assess_shift(reqs, [], on=DAY)
    assert {f["certification_type"] for f in got["findings"]} == {LICENCE, FIRE}


def test_all_requirements_met_is_compliant():
    reqs = [cc.Requirement(LICENCE), cc.Requirement(FIRE)]
    held = [cc.Held(LICENCE, None), cc.Held(FIRE, DAY + timedelta(days=400))]
    assert cc.assess_shift(reqs, held, on=DAY)["status"] == cc.COMPLIANT


def test_only_actionable_statuses_are_worth_raising():
    assert cc.is_actionable(cc.MISSING) and cc.is_actionable(cc.EXPIRING)
    assert not cc.is_actionable(cc.COMPLIANT)
    assert not cc.is_actionable(cc.NOT_ASSESSED)


# ─── C. The sweep ────────────────────────────────────────────────────────────

async def _world(*, cert_expires: date | None, baseline: bool = True,
                 shift_in_days: int = 10, site_requires: str | None = None):
    """A tenant with one guard, one site and one upcoming shift."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "guard", "shift")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Cert Co',:s)",
               {"t": i["tenant"], "s": f"cert-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,5,:e,'x','A Guard')",
        {"i": i["guard"], "t": i["tenant"], "e": f"g-{i['guard'].hex[:8]}@cert.test"})

    if baseline:
        await _sql(
            "INSERT INTO certification_requirements "
            "  (tenant_id, site_id, certification_type) VALUES (:t,NULL,:c)",
            {"t": i["tenant"], "c": LICENCE})
    if site_requires:
        await _sql(
            "INSERT INTO certification_requirements "
            "  (tenant_id, site_id, certification_type) VALUES (:t,:s,:c)",
            {"t": i["tenant"], "s": i["site"], "c": site_requires})

    if cert_expires is not None:
        await _sql(
            "INSERT INTO guard_certifications "
            "  (tenant_id, user_id, certification_type, expires_at) "
            "VALUES (:t,:u,:c,:e)",
            {"t": i["tenant"], "u": i["guard"], "c": LICENCE, "e": cert_expires})

    start = datetime.combine(date.today() + timedelta(days=shift_in_days),
                             time(9, 0), tzinfo=timezone.utc)
    await _sql(
        "INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, "
        "                    scheduled_start, scheduled_end, status) "
        "VALUES (:i,:t,:g,:s,:a,:b,'scheduled')",
        {"i": i["shift"], "t": i["tenant"], "g": i["guard"], "s": i["site"],
         "a": start, "b": start + timedelta(hours=8)})
    return i


async def _sweep(**kw):
    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            return await cc.sweep_upcoming_shifts(s, **kw)
    finally:
        await engine.dispose()


async def _findings(shift_id):
    return await _sql("SELECT certification_type, status, resolved_at "
                      "  FROM shift_certification_findings WHERE shift_id = :s",
                      {"s": shift_id})


@pytest.mark.asyncio
async def test_a_shift_whose_guard_has_no_licence_is_raised():
    w = await _world(cert_expires=None)
    await _sweep()
    rows = await _findings(w["shift"])
    assert len(rows) == 1, rows
    assert rows[0]["status"] == cc.MISSING
    assert rows[0]["resolved_at"] is None


@pytest.mark.asyncio
async def test_a_licence_expiring_before_the_shift_is_raised_as_expired():
    """The heart of it. The licence is valid TODAY and invalid on the day the
    guard actually works — a check against today's date would call this fine."""
    w = await _world(cert_expires=date.today() + timedelta(days=3),
                     shift_in_days=10)
    await _sweep()
    rows = await _findings(w["shift"])
    assert rows and rows[0]["status"] == cc.EXPIRED, rows


@pytest.mark.asyncio
async def test_a_tenant_with_no_requirements_raises_nothing():
    w = await _world(cert_expires=None, baseline=False)
    await _sweep()
    assert await _findings(w["shift"]) == []


@pytest.mark.asyncio
async def test_a_renewed_licence_closes_the_finding():
    """A board that only grows is a board people stop reading."""
    w = await _world(cert_expires=None)
    await _sweep()
    assert (await _findings(w["shift"]))[0]["resolved_at"] is None

    await _sql("INSERT INTO guard_certifications "
               "  (tenant_id, user_id, certification_type, expires_at) "
               "VALUES (:t,:u,:c,:e)",
               {"t": w["tenant"], "u": w["guard"], "c": LICENCE,
                "e": date.today() + timedelta(days=400)})
    await _sweep()

    rows = await _findings(w["shift"])
    assert rows[0]["resolved_at"] is not None, "the fixed finding was left open"


@pytest.mark.asyncio
async def test_running_the_sweep_twice_does_not_duplicate_findings():
    w = await _world(cert_expires=None)
    await _sweep()
    await _sweep()
    assert len(await _findings(w["shift"])) == 1


@pytest.mark.asyncio
async def test_a_site_requirement_applies_on_top_of_the_baseline():
    w = await _world(cert_expires=date.today() + timedelta(days=400),
                     site_requires=FIRE)
    await _sweep()
    rows = await _findings(w["shift"])
    # The licence is fine; the site's fire-warden requirement is not held.
    assert [r["certification_type"] for r in rows] == [FIRE], rows


# ─── D. Tenant isolation, as the role the app actually uses ──────────────────

@pytest.mark.asyncio
async def test_the_new_tables_isolate_tenants():
    """postgres has BYPASSRLS, so this connects as svc_app and asserts that
    before drawing any conclusion."""
    a = await _world(cert_expires=None)
    b = await _world(cert_expires=None)
    await _sweep()

    engine = create_async_engine(APP_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            who = (await s.execute(text(
                "SELECT current_user, (SELECT rolbypassrls FROM pg_roles "
                " WHERE rolname = current_user)"))).first()
            assert who[1] is False, f"connected as {who[0]!r}, which bypasses RLS"

            await s.execute(text("SELECT set_config('app.current_tenant', :t, true)"),
                            {"t": str(a["tenant"])})
            reqs = (await s.execute(text(
                "SELECT count(*) FROM certification_requirements"))).scalar()
            finds = (await s.execute(text(
                "SELECT count(*) FROM shift_certification_findings "
                " WHERE shift_id = CAST(:s AS uuid)"), {"s": str(b["shift"])})).scalar()
    finally:
        await engine.dispose()

    assert reqs >= 1, "tenant A cannot see its own requirement"
    assert finds == 0, "tenant A read tenant B's certification finding"


@pytest.mark.asyncio
async def test_a_tenant_baseline_cannot_be_recorded_twice():
    """NULL <> NULL in SQL, so a plain unique constraint would accept the same
    tenant-wide requirement endlessly. The partial index is what stops it."""
    w = await _world(cert_expires=None)
    with pytest.raises(Exception) as exc:
        await _sql("INSERT INTO certification_requirements "
                   "  (tenant_id, site_id, certification_type) VALUES (:t,NULL,:c)",
                   {"t": w["tenant"], "c": LICENCE})
    assert "uq_certreq_tenant_baseline" in str(exc.value) or \
           "duplicate key" in str(exc.value).lower(), str(exc.value)[:300]
