import os
import uuid

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL_SYNC",
    "postgresql+psycopg://svc_app:change_me_dev_only@postgres:5432/seventh_ai_vision",
)

from worker.db_writer import get_tenant_session  # noqa: E402  (must follow the env var set above)

ADMIN_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL_SYNC",
    "postgresql://postgres:change_me_dev_only@postgres:5432/seventh_ai_vision",
)


def _seed_tenant_and_camera() -> tuple[uuid.UUID, uuid.UUID]:
    import psycopg

    tenant_id, camera_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (id, name, slug) VALUES (%s, 'T', %s)",
                (tenant_id, f"t-{tenant_id.hex[:8]}"),
            )
            cur.execute(
                "INSERT INTO cameras (id, tenant_id, name) VALUES (%s, %s, 'Cam')",
                (camera_id, tenant_id),
            )
        conn.commit()
    return tenant_id, camera_id


def _cleanup(tenant_id: uuid.UUID) -> None:
    import psycopg

    with psycopg.connect(ADMIN_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cameras WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()


def test_get_tenant_session_scopes_rls_correctly():
    tenant_id, camera_id = _seed_tenant_and_camera()
    try:
        with get_tenant_session(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM cameras")
                rows = cur.fetchall()
            assert rows == [("Cam",)]
            conn.commit()
    finally:
        _cleanup(tenant_id)


def test_get_tenant_session_does_not_leak_into_other_tenant():
    tenant_a, _ = _seed_tenant_and_camera()
    tenant_b, _ = _seed_tenant_and_camera()
    try:
        with get_tenant_session(tenant_b) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM cameras")
                rows = cur.fetchall()
            assert len(rows) == 1  # only tenant_b's own camera, not tenant_a's
            conn.commit()
    finally:
        _cleanup(tenant_a)
        _cleanup(tenant_b)
