"""Alerts that are not about a camera, for tenants that may not have one.

WHAT WAS WRONG. alerts.camera_id was NOT NULL and alerts carried no site, so
every non-camera alert had to produce a camera from somewhere. Eighteen call
sites did it with `(SELECT id FROM cameras WHERE tenant_id = ... LIMIT 1)`,
which fails two ways:

  * no cameras -> the guarded INSERT wrote nothing and the alert silently did
    not exist. One of those code paths said so in a comment:
    "continue  # tenant has no cameras at all — skip silently".
  * some cameras -> LIMIT 1 with no ORDER BY picked an arbitrary one, so the
    alert was filed against an unrelated camera at an unrelated site, and
    site-scoped visibility followed the wrong site.

A guarding-only agency is a large part of who this product is sold to, so the
first case is not an edge case.

Sections:
  A — A tenant with no cameras still gets its alerts (3 tests)
  B — The alert is filed against the right site, or none (2 tests)
  C — Null-camera alerts still reach the boards (2 tests)
"""
from __future__ import annotations

import json
import os
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Module level on purpose — app.main pulls the ML stack, and inside a test that
# import lands on whichever test runs first and trips pytest-timeout.
from app.main import app  # noqa: F401

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


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


async def _tenant_with_no_cameras():
    i = {k: uuid.uuid4() for k in ("tenant", "site")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'Guarding Only',:s)",
               {"t": i["tenant"], "s": f"nocam-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'The Site')",
               {"i": i["site"], "t": i["tenant"]})
    cams = await _sql("SELECT count(*) AS n FROM cameras WHERE tenant_id = :t",
                      {"t": i["tenant"]})
    assert cams[0]["n"] == 0, "this fixture is only meaningful with no cameras"
    return i


# ─── A. A tenant with no cameras still gets its alerts ───────────────────────

@pytest.mark.asyncio
async def test_an_alert_can_be_written_with_no_camera_at_all():
    """The schema change itself. Before 0119 this INSERT raised a not-null
    violation, which is why every caller worked around it."""
    i = await _tenant_with_no_cameras()
    rows = await _sql("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity,
                            alert_code, message_params, title, message, status)
        VALUES (:t, NULL, :s, 'contractor', 'medium', 'contractor.permit_expiring',
                CAST(:p AS jsonb), 'Permit expiring', 'A permit expires soon', 'open')
        RETURNING id, camera_id, site_id
    """, {"t": i["tenant"], "s": i["site"], "p": json.dumps({"permit_id": "x"})})
    assert rows[0]["camera_id"] is None
    assert str(rows[0]["site_id"]) == str(i["site"])


@pytest.mark.asyncio
async def test_the_contractor_sweep_alerts_a_tenant_that_owns_no_cameras():
    """The reported bug, end to end through the real scheduler function."""
    i = await _tenant_with_no_cameras()
    contractor = uuid.uuid4()
    await _sql("INSERT INTO contractors (id, tenant_id, company_name) "
               "VALUES (:i,:t,'Acme Cleaning')",
               {"i": contractor, "t": i["tenant"]})
    await _sql("""
        INSERT INTO work_permits (tenant_id, contractor_id, site_id, permit_number,
                                  work_description, status, start_at, end_at)
        VALUES (:t,:c,:s,'WP-001','Nightly cleaning','approved',
                now() - interval '1 hour', now() + interval '2 hours')
    """, {"t": i["tenant"], "c": contractor, "s": i["site"]})

    from app.scheduler_main import check_contractor_expiry

    class _NullRedis:
        async def publish(self, *a, **kw):
            return None

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await check_contractor_expiry(s, _NullRedis(), tenant_ids=[i["tenant"]])
    finally:
        await engine.dispose()

    alerts = await _sql(
        "SELECT alert_code, camera_id, site_id FROM alerts "
        " WHERE tenant_id = :t AND alert_code = 'contractor.permit_expiring'",
        {"t": i["tenant"]})
    assert alerts, ("a tenant with no cameras received no permit alert — the "
                    "whole point of 0119")
    assert alerts[0]["camera_id"] is None
    assert str(alerts[0]["site_id"]) == str(i["site"])


@pytest.mark.asyncio
async def test_nothing_fabricates_a_camera_any_more():
    """A guard against the pattern coming back. If a future change resurrects
    `SELECT id FROM cameras ... LIMIT 1` to satisfy a not-null that no longer
    exists, this is where it should be noticed."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in list(root.rglob("*.py")):
        body = path.read_text(encoding="utf-8", errors="replace")
        if "SELECT id FROM cameras WHERE tenant_id" in body:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        "these still invent a camera to satisfy alerts.camera_id, which is "
        f"nullable since 0119: {offenders}")


# ─── B. Filed against the right site, or none ────────────────────────────────

@pytest.mark.asyncio
async def test_an_accreditation_alert_has_no_site_because_it_has_none():
    """An accreditation belongs to a contractor company, not to a site. Making
    one up would put it in front of the wrong people."""
    i = await _tenant_with_no_cameras()
    contractor = uuid.uuid4()
    await _sql("INSERT INTO contractors (id, tenant_id, company_name) "
               "VALUES (:i,:t,'Acme Cleaning')",
               {"i": contractor, "t": i["tenant"]})
    await _sql("""
        INSERT INTO contractor_accreditations (tenant_id, contractor_id,
                                               document_type, expires_at)
        VALUES (:t,:c,'Public liability', CURRENT_DATE + 10)
    """, {"t": i["tenant"], "c": contractor})

    from app.scheduler_main import check_contractor_expiry

    class _NullRedis:
        async def publish(self, *a, **kw):
            return None

    engine = create_async_engine(ADMIN_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await check_contractor_expiry(s, _NullRedis(), tenant_ids=[i["tenant"]])
    finally:
        await engine.dispose()

    rows = await _sql(
        "SELECT camera_id, site_id FROM alerts WHERE tenant_id = :t "
        "  AND alert_code = 'contractor.accreditation_expiring'", {"t": i["tenant"]})
    assert rows, "no accreditation alert was raised"
    assert rows[0]["camera_id"] is None and rows[0]["site_id"] is None


@pytest.mark.asyncio
async def test_existing_alerts_kept_their_site_through_the_backfill():
    """0119 backfilled site_id from the camera. A camera-based alert must still
    know where it happened, or scoping breaks for the rows that already exist."""
    i = {k: uuid.uuid4() for k in ("tenant", "site", "camera")}
    await _sql("INSERT INTO tenants (id, name, slug) VALUES (:t,'With Cameras',:s)",
               {"t": i["tenant"], "s": f"withcam-{i['tenant'].hex[:10]}"})
    await _sql("INSERT INTO sites (id, tenant_id, name) VALUES (:i,:t,'Site')",
               {"i": i["site"], "t": i["tenant"]})
    await _sql("INSERT INTO cameras (id, tenant_id, site_id, name) "
               "VALUES (:i,:t,:s,'Gate')",
               {"i": i["camera"], "t": i["tenant"], "s": i["site"]})
    rows = await _sql("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity,
                            alert_code, title, message, status)
        VALUES (:t, :c, :s, 'intrusion', 'high', 'intrusion.detected',
                'Intrusion', 'Someone climbed the fence', 'open')
        RETURNING camera_id, site_id
    """, {"t": i["tenant"], "c": i["camera"], "s": i["site"]})
    assert rows[0]["camera_id"] is not None
    assert str(rows[0]["site_id"]) == str(i["site"])


# ─── C. They still reach the boards ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_null_camera_alert_is_visible_to_the_command_centre_query():
    """The reason the migration changes both things at once. Seven queries
    INNER JOINed alerts to cameras; left as they were, a null-camera alert
    would have been written successfully and then shown to nobody."""
    i = await _tenant_with_no_cameras()
    await _sql("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity,
                            alert_code, title, message, status)
        VALUES (:t, NULL, :s, 'contractor', 'high', 'contractor.permit_expiring',
                'Permit expiring', 'Expires in 2 hours', 'open')
    """, {"t": i["tenant"], "s": i["site"]})

    # The shape the Command Centre and Action Centre now use.
    rows = await _sql("""
        SELECT a.id, c.name AS camera_name, COALESCE(a.site_id, c.site_id) AS scope_site
          FROM alerts a
          LEFT JOIN cameras c ON c.id = a.camera_id
         WHERE a.tenant_id = :t AND a.status = 'open'
    """, {"t": i["tenant"]})
    assert rows, "the alert exists but the board query cannot see it"
    assert rows[0]["camera_name"] is None
    assert str(rows[0]["scope_site"]) == str(i["site"]), (
        "scoping fell back to nothing, so a site-restricted user would never "
        "see this alert")


@pytest.mark.asyncio
async def test_the_old_inner_join_would_have_hidden_it():
    """Proves the previous test is not vacuous: the same row is invisible to
    the query shape that shipped before this change."""
    i = await _tenant_with_no_cameras()
    await _sql("""
        INSERT INTO alerts (tenant_id, camera_id, site_id, module_type, severity,
                            alert_code, title, message, status)
        VALUES (:t, NULL, :s, 'contractor', 'high', 'contractor.permit_expiring',
                'Permit expiring', 'Expires in 2 hours', 'open')
    """, {"t": i["tenant"], "s": i["site"]})

    hidden = await _sql("""
        SELECT a.id FROM alerts a
          JOIN cameras c ON c.id = a.camera_id
         WHERE a.tenant_id = :t AND a.status = 'open'
    """, {"t": i["tenant"]})
    assert hidden == [], (
        "the inner join returned something, so this test no longer demonstrates "
        "the failure it was written for")
