#!/bin/sh
# Creates the dedicated test database (plan §13/§14: `seventh_ai_vision_test`) with
# the same svc_app grants as the dev database, so the pytest suite runs against a
# real Postgres with real RLS instead of a mock. Role creation already happened in
# 10-init-rls.sh (roles are cluster-wide, not per-database); this script repeats
# only the per-database grant setup against the new database.
set -e

# The Helm chart runs this same image as a cluster's database, where there is no test suite: it sets
# CREATE_TEST_DB=0. docker-compose sets nothing, so the dev box keeps its test database as before.
if [ "${CREATE_TEST_DB:-1}" = "0" ]; then
    echo "create-test-db: CREATE_TEST_DB=0, no test database"
    exit 0
fi

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
