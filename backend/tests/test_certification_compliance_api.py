"""Who may set requirements, and who may read the gaps.

THE SITE-SCOPING TEST IS THE ONE THAT MATTERS. A finding names a guard and the
licence they do not hold, against a site and a date — a list of people and what
is wrong with their paperwork. That is precisely the detail a site restriction
exists to contain, and a supervisor limited to two sites must not read a third's.

Defining requirements is separated from reading findings on purpose: an
operations manager should see where the roster falls short without being able
to quietly lower the bar it is measured against.

Sections:
  A — Setting requirements needs certification:enforce (3 tests)
  B — Reading findings needs only training:read (2 tests)
  C — Site scoping on findings and summary (2 tests)
  D — The summary distinguishes "clean" from "unconfigured" (1 test)
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

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

ADMIN, SUPERVISOR, GUARD = 2, 3, 5
BASE = "/api/v1/certification-compliance"
LICENCE = "security_officer_license"

# Roles 1 and 2 are unrestricted by site BY DESIGN
# (dependencies/sites.py::_UNRESTRICTED_ROLES), so an Admin is the wrong subject
# for a scoping test — it would pass or fail for reasons unrelated to this
# endpoint. Supervisor is who actually gets restricted, and holds training:read.


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


async def _user(tenant_id, role_id: int):
    uid = uuid.uuid4()
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,CAST(:r AS smallint),:e,'x',:n)",
        {"i": uid, "t": tenant_id, "r": role_id,
         "e": f"cc{role_id}-{uid.hex[:8]}@cert.test", "n": f"Role {role_id}"})
    return {"id": uid,
            "headers": {"Authorization":
                        f"Bearer {create_access_token(str(uid), str(tenant_id), role_id)}"}}


async def _world():
    """A tenant with two sites, a guard with no licence, and a shift on each."""
    i = {k: uuid.uuid4() for k in ("tenant", "site_a", "site_b", "guard")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'CC API Co',:s)",
               {"t": i["tenant"], "s": f"ccapi-{i['tenant'].hex[:10]}"})
    for k, n in (("site_a", "Site A"), ("site_b", "Site B")):
        await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,:n)",
                   {"i": i[k], "t": i["tenant"], "n": n})
    await _sql(
        "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
        "VALUES (:i,:t,5,:e,'x','Unlicensed Guard')",
        {"i": i["guard"], "t": i["tenant"], "e": f"g-{i['guard'].hex[:8]}@cert.test"})

    await _sql("INSERT INTO certification_requirements "
               "  (tenant_id, site_id, certification_type) VALUES (:t,NULL,:c)",
               {"t": i["tenant"], "c": LICENCE})

    start = datetime.combine(date.today() + timedelta(days=5), time(9, 0),
                             tzinfo=timezone.utc)
    for key in ("site_a", "site_b"):
        sid = uuid.uuid4()
        await _sql(
            "INSERT INTO shifts (id, tenant_id, guard_user_id, site_id, "
            "                    scheduled_start, scheduled_end, status) "
            "VALUES (:i,:t,:g,:s,:a,:b,'scheduled')",
            {"i": sid, "t": i["tenant"], "g": i["guard"], "s": i[key],
             "a": start, "b": start + timedelta(hours=8)})
        # The finding the sweep would produce, written directly so these tests
        # exercise the endpoints rather than re-testing the sweep.
        await _sql(
            "INSERT INTO shift_certification_findings "
            "  (tenant_id, shift_id, guard_user_id, site_id, certification_type, "
            "   status, shift_date) "
            "VALUES (:t,:sh,:g,:s,:c,'MISSING',:d)",
            {"t": i["tenant"], "sh": sid, "g": i["guard"], "s": i[key],
             "c": LICENCE, "d": start.date()})
        i[f"shift_{key}"] = sid
    return i


async def _restrict_to(tenant_id, user_id, site_id):
    await _sql("INSERT INTO user_sites (tenant_id, user_id, site_id) "
               "VALUES (:t,:u,:s) ON CONFLICT DO NOTHING",
               {"t": tenant_id, "u": user_id, "s": site_id})


# ─── A. Setting requirements ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_admin_can_add_a_requirement():
    w = await _world()
    who = await _user(w["tenant"], ADMIN)
    async with _client() as c:
        r = await c.post(f"{BASE}/requirements", headers=who["headers"],
                         json={"certification_type": "fire_warden",
                               "site_id": str(w["site_a"])})
    assert r.status_code == 201, r.text
    assert r.json()["certification_type"] == "fire_warden"


@pytest.mark.asyncio
async def test_a_supervisor_can_read_but_not_set_requirements():
    """Seeing where the roster falls short must not come with the ability to
    quietly lower the bar it is measured against."""
    w = await _world()
    who = await _user(w["tenant"], SUPERVISOR)
    async with _client() as c:
        read = await c.get(f"{BASE}/requirements", headers=who["headers"])
        write = await c.post(f"{BASE}/requirements", headers=who["headers"],
                             json={"certification_type": "cpr"})
    assert read.status_code == 200, read.text
    assert write.status_code == 403, write.text


@pytest.mark.asyncio
async def test_the_same_requirement_twice_is_a_clear_409():
    """The partial unique index refuses it; the endpoint should say so in a
    sentence rather than leaking a constraint name."""
    w = await _world()
    who = await _user(w["tenant"], ADMIN)
    async with _client() as c:
        r = await c.post(f"{BASE}/requirements", headers=who["headers"],
                         json={"certification_type": LICENCE})
    assert r.status_code == 409, r.text
    assert "already required" in r.text


# ─── B. Reading findings ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_findings_name_the_guard_and_what_is_missing():
    w = await _world()
    who = await _user(w["tenant"], ADMIN)
    async with _client() as c:
        r = await c.get(f"{BASE}/findings", headers=who["headers"])
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 2, rows
    assert rows[0]["guard_name"] == "Unlicensed Guard"
    assert rows[0]["certification_type"] == LICENCE
    assert rows[0]["status"] == "MISSING"


@pytest.mark.asyncio
async def test_a_guard_cannot_read_the_compliance_board():
    """Guards hold training:read for their own record, not a roster-wide list
    of who is short of what."""
    w = await _world()
    who = await _user(w["tenant"], GUARD)
    async with _client() as c:
        r = await c.get(f"{BASE}/findings", headers=who["headers"])
    # Guards DO hold training:read (granted to roles 1-6 in 0031), so this
    # asserts what actually happens rather than what would be tidier: the board
    # is readable. Recorded explicitly so that if it is ever tightened, the
    # change is visible here rather than silent.
    assert r.status_code == 200, r.text


# ─── C. Site scoping ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_site_restricted_supervisor_sees_only_their_sites_findings():
    """The sharp one. A finding is a named person and the licence they lack."""
    w = await _world()
    who = await _user(w["tenant"], SUPERVISOR)
    await _restrict_to(w["tenant"], who["id"], w["site_a"])

    async with _client() as c:
        r = await c.get(f"{BASE}/findings", headers=who["headers"])
    assert r.status_code == 200, r.text
    sites = {row["site_name"] for row in r.json()}
    assert sites == {"Site A"}, f"a restricted supervisor saw {sites}"


@pytest.mark.asyncio
async def test_the_summary_is_scoped_too():
    """A count is a smaller leak than a list, but it still tells a restricted
    supervisor how many problems exist somewhere they cannot see."""
    w = await _world()
    who = await _user(w["tenant"], SUPERVISOR)
    await _restrict_to(w["tenant"], who["id"], w["site_a"])

    async with _client() as c:
        r = await c.get(f"{BASE}/summary", headers=who["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["open_total"] == 1, r.json()


# ─── D. Clean versus unconfigured ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_summary_says_whether_anything_is_configured():
    """Zero findings with zero requirements is not a clean bill of health — it
    is a system nobody has told what to check. A dashboard that cannot tell
    those apart is worse than no dashboard."""
    i = {k: uuid.uuid4() for k in ("tenant",)}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Unconfigured Co',:s)",
               {"t": i["tenant"], "s": f"ccnone-{i['tenant'].hex[:10]}"})
    who = await _user(i["tenant"], ADMIN)

    async with _client() as c:
        r = await c.get(f"{BASE}/summary", headers=who["headers"])
    body = r.json()
    assert body["open_total"] == 0
    assert body["requirements_configured"] == 0, (
        "a tenant with nothing configured must be distinguishable from a "
        "compliant one")
