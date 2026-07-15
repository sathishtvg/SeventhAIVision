import os
import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import create_tenant, set_tenant

# Derive host from DATABASE_URL so this works both inside Docker ('postgres')
# and on the host machine ('localhost') without manual env-var overrides.
_m = re.search(r"@([^:/]+):", os.environ.get("DATABASE_URL", ""))
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)


@pytest.mark.asyncio
async def test_cross_tenant_camera_read_blocked(db_session):
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    await set_tenant(db_session, tenant_a)
    camera_a = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam A1')"),
        {"id": camera_a, "tid": tenant_a},
    )

    await set_tenant(db_session, tenant_b)
    rows = (await db_session.execute(text("SELECT name FROM cameras"))).fetchall()
    assert rows == []

    # Even knowing the exact ID, tenant B's context still can't read it.
    rows = (await db_session.execute(
        text("SELECT name FROM cameras WHERE id = :id"), {"id": camera_a}
    )).fetchall()
    assert rows == []

    await set_tenant(db_session, tenant_a)
    rows = (await db_session.execute(text("SELECT name FROM cameras"))).fetchall()
    assert [r.name for r in rows] == ["Cam A1"]


@pytest.mark.asyncio
async def test_cross_tenant_alert_incident_evidence_blocked(db_session):
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    await set_tenant(db_session, tenant_a)
    camera_a = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO cameras (id, tenant_id, name) VALUES (:id, :tid, 'Cam A1')"),
        {"id": camera_a, "tid": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO alerts (tenant_id, camera_id, module_type, severity, title) "
            "VALUES (:tid, :cid, 'lpr', 'critical', 'Blocklist hit')"
        ),
        {"tid": tenant_a, "cid": camera_a},
    )

    await set_tenant(db_session, tenant_b)
    rows = (await db_session.execute(text("SELECT title FROM alerts"))).fetchall()
    assert rows == []


@pytest.mark.asyncio
async def test_unscoped_session_sees_zero_rows():
    """Fail-closed: a connection that never ran the get_db_with_tenant dependency
    at all (e.g. a router bug) sees zero rows, not every tenant's rows. This is
    the property that makes RLS safer than 'remember to add WHERE tenant_id='
    app-level filtering (plan §2/§3).

    Needs a genuinely untouched connection: once a session has called
    set_config()/SET on a custom GUC, Postgres won't cleanly revert it to a true
    NULL (RESET produces an empty string, not NULL) — confirmed by hand against
    the real database, not assumed. So fixture data is seeded via a separate,
    committed admin connection rather than reusing db_session's svc_app
    transaction, to get a connection that has truly never touched
    app.current_tenant.
    """
    admin_engine = create_async_engine(ADMIN_DATABASE_URL)
    tenant_id = uuid.uuid4()
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Tenant A', :slug)"),
                {"id": tenant_id, "slug": f"tenant-a-{tenant_id.hex[:8]}"},
            )
            await conn.execute(
                text("INSERT INTO cameras (tenant_id, name) VALUES (:tid, 'Cam A1')"),
                {"tid": tenant_id},
            )

        svc_engine = create_async_engine(
            os.environ.get(
                "TEST_DATABASE_URL",
                f"postgresql+asyncpg://svc_app:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
            )
        )
        try:
            async with svc_engine.connect() as conn:
                rows = (await conn.execute(text("SELECT name FROM cameras"))).fetchall()
                assert rows == []
        finally:
            await svc_engine.dispose()
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM cameras WHERE tenant_id = :tid"), {"tid": tenant_id})
            await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_insert_mismatched_tenant_rejected(db_session):
    tenant_a = await create_tenant(db_session, "Tenant A")
    tenant_b = await create_tenant(db_session, "Tenant B")

    await set_tenant(db_session, tenant_a)
    with pytest.raises(Exception, match="row-level security"):
        await db_session.execute(
            text("INSERT INTO cameras (tenant_id, name) VALUES (:tid, 'Sneaky Cam')"),
            {"tid": tenant_b},
        )


@pytest.mark.asyncio
async def test_app_db_role_is_not_superuser(db_session):
    """Confirms FORCE ROW LEVEL SECURITY is actually load-bearing: a superuser or
    BYPASSRLS role ignores it regardless (plan §2's operational note)."""
    row = (
        await db_session.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False
