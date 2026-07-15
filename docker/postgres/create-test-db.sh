#!/bin/sh
# Creates the dedicated test database (plan §13/§14: `seventh_ai_vision_test`) with
# the same svc_app grants as the dev database, so the pytest suite runs against a
# real Postgres with real RLS instead of a mock. Role creation already happened in
# 10-init-rls.sh (roles are cluster-wide, not per-database); this script repeats
# only the per-database grant setup against the new database.
set -e

TEST_DB="${POSTGRES_TEST_DB:-${POSTGRES_DB}_test}"

DB_EXISTS=$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -tAc "SELECT 1 FROM pg_database WHERE datname = '${TEST_DB}'")
if [ "$DB_EXISTS" != "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE DATABASE ${TEST_DB}"
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$TEST_DB" <<-EOSQL
    GRANT CONNECT ON DATABASE ${TEST_DB} TO ${POSTGRES_APP_USER};
    GRANT USAGE ON SCHEMA public TO ${POSTGRES_APP_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${POSTGRES_APP_USER};
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO ${POSTGRES_APP_USER};
    CREATE EXTENSION IF NOT EXISTS pg_partman;
EOSQL
