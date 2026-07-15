"""Sync RLS-correct session for the ai-worker side, mirroring backend's
get_db_with_tenant (plan §3) but for psycopg's synchronous API: the consumer
loop is synchronous, so there's no async session to reuse."""

import os
from contextlib import contextmanager
from uuid import UUID

import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://svc_app:change_me_dev_only_too@localhost:5432/seventh_ai_vision"
)


def _connect() -> psycopg.Connection:
    # psycopg3's connect() wants a plain "postgresql://" DSN; strip the
    # SQLAlchemy-style "+psycopg" driver marker so the same DATABASE_URL env var
    # convention used elsewhere still works here without a second env var.
    dsn = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(dsn)


@contextmanager
def get_tenant_session(tenant_id: UUID | str):
    """is_local=true (third set_config arg) scopes the GUC to the transaction
    that follows — callers must do all their writes then call conn.commit()
    within this same `with` block, exactly like the LPR/face/intrusion
    pipelines do (one transaction per processed frame, plan §6)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_tenant', %s, true)", (str(tenant_id),))
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
