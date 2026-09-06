import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_admin_url() -> str:
    """Alembic runs DDL (CREATE TABLE / CREATE POLICY / CREATE EXTENSION), which
    needs the bootstrap superuser, not the restricted svc_app role the app uses at
    runtime — see plan §2/§16 and backend/app/db/session.py's docstring. Sync
    psycopg, not asyncpg: Alembic's default migration runner is synchronous and
    Phase 1 has no need for async migrations."""
    url = os.environ.get("ALEMBIC_DATABASE_URL")
    if url:
        _assert_not_dev_default(url, "ALEMBIC_DATABASE_URL")
        return url
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "change_me_dev_only")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "seventh_ai_vision")
    url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
    _assert_not_dev_default(url, "POSTGRES_PASSWORD")
    return url


def _assert_not_dev_default(url: str, source: str) -> None:
    """Refuse to migrate production with the repo's public dev password.

    Mirrors app/core/config.py::assert_production_secrets_configured, but has
    to live here too: migrations run as a separate process *before* uvicorn
    imports that config at all, so its guard cannot cover this path.

    The failure this prevents is specifically quiet. The password above falls
    back to a literal that is correct in dev and wrong everywhere else, so a
    deployment either works by coincidence or dies with a bare "password
    authentication failed" that says nothing about which variable is missing.
    """
    if os.environ.get("ENVIRONMENT") != "production":
        return
    if "change_me_dev_only" not in url:
        return
    raise RuntimeError(
        f"Refusing to run migrations with ENVIRONMENT=production while the database "
        f"password is still the dev default 'change_me_dev_only' (via {source}). "
        f"Set a real POSTGRES_PASSWORD — note the api service passes the superuser "
        f"secret as PGPASSWORD for pg_dump, so ALEMBIC_DATABASE_URL must carry it "
        f"for Alembic."
    )


def run_migrations_offline() -> None:
    context.configure(
        url=get_admin_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_admin_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
