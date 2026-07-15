#!/bin/sh
# Runs once, at first container creation, against an empty database — before any
# application table exists. docker-entrypoint-initdb.d only substitutes env vars
# into executable shell scripts, not raw .sql files, which is why this is a .sh
# (psql -v) rather than a .sql file.
#
# Creates the RESTRICTED application role every service connects as at runtime
# (api, ingestion, scheduler, all ai-worker-*). This must NOT be the same role as
# POSTGRES_USER: the official postgres image makes POSTGRES_USER a superuser, and
# a superuser (or any BYPASSRLS role) silently bypasses RLS regardless of
# FORCE ROW LEVEL SECURITY. POSTGRES_USER is reserved for running Alembic
# migrations (CREATE TABLE / CREATE POLICY needs elevated privileges); svc_app is
# what every running service's DATABASE_URL actually points at.
#
# Table DDL, RLS policies, and seed data (roles/permissions/role_permissions) live
# in backend/alembic/versions/0001_initial_schema.py instead: this script runs too
# early to reference tables that don't exist yet.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_APP_USER}') THEN
            CREATE ROLE ${POSTGRES_APP_USER} WITH LOGIN PASSWORD '${POSTGRES_APP_PASSWORD}'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF;
    END
    \$\$;

    -- Defense in depth: strip the two attributes that would make
    -- FORCE ROW LEVEL SECURITY moot, even if the role already existed.
    ALTER ROLE ${POSTGRES_APP_USER} NOSUPERUSER NOBYPASSRLS;

    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_APP_USER};
    GRANT USAGE ON SCHEMA public TO ${POSTGRES_APP_USER};

    -- svc_app gets row-level access only through RLS policies on top of these
    -- table-level grants — granting privileges does not bypass RLS, it's a
    -- separate, additional filter. Default privileges so tables Alembic creates
    -- LATER (as POSTGRES_USER) are usable by svc_app without a manual GRANT per table.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${POSTGRES_APP_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ${POSTGRES_APP_USER};
EOSQL
