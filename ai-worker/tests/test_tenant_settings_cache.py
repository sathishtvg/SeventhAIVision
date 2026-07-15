import os
import time
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from worker.common.tenant_settings_cache import clear_cache, get_tenant_setting

ADMIN_URL = os.environ.get("ADMIN_TEST_DATABASE_URL_SYNC", "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision")


@pytest.fixture
def tenant_and_conn():
    clear_cache()
    tenant_id = uuid.uuid4()
    admin_conn = psycopg.connect(ADMIN_URL)
    with admin_conn.cursor() as cur:
        cur.execute("INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)", (tenant_id, f"t-{tenant_id.hex[:8]}"))
        cur.execute(
            "INSERT INTO users (id, tenant_id, role_id, email, hashed_password) VALUES (%s, %s, 1, %s, 'x')",
            (uuid.uuid4(), tenant_id, f"{tenant_id}@example.com"),
        )
    admin_conn.commit()

    conn = psycopg.connect(ADMIN_URL)  # admin conn bypasses RLS; fine for this read-path test
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant', %s, true)", (str(tenant_id),))

    yield tenant_id, conn

    conn.close()
    with admin_conn.cursor() as cur:
        cur.execute("DELETE FROM tenant_settings WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant_id,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
    admin_conn.commit()
    admin_conn.close()


def test_missing_row_falls_back_to_env_default(tenant_and_conn, monkeypatch):
    tenant_id, conn = tenant_and_conn
    monkeypatch.setenv("LPR_CONFIDENCE_THRESHOLD", "0.55")
    value = get_tenant_setting(conn, tenant_id, "lpr.confidence_threshold")
    assert value == 0.55


def test_tenant_setting_overrides_env_default(tenant_and_conn):
    tenant_id, conn = tenant_and_conn
    with psycopg.connect(ADMIN_URL) as admin_conn:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM users WHERE tenant_id = %s", (tenant_id,)
            )
            user_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id) "
                "VALUES (%s, 'lpr.confidence_threshold', %s, %s)",
                (tenant_id, Jsonb(0.91), user_id),
            )
        admin_conn.commit()

    value = get_tenant_setting(conn, tenant_id, "lpr.confidence_threshold")
    assert value == 0.91


def test_cache_hit_avoids_db_round_trip(tenant_and_conn):
    tenant_id, conn = tenant_and_conn
    first = get_tenant_setting(conn, tenant_id, "face.match_threshold")
    conn.close()  # if the cache is actually used, a second call must NOT need this connection
    second = get_tenant_setting(conn, tenant_id, "face.match_threshold")
    assert first == second
