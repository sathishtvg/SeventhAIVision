#!/bin/sh
# Traccar's own database, in the platform's Postgres: a role and a database both named traccar.
# Run once by the traccar-db service (docker compose --profile fleet); running it again changes nothing.
# Traccar creates its own tables in it when it first starts. Nothing of Seventh AI's is in it.
set -eu
export PGPASSWORD="$POSTGRES_PASSWORD"
until pg_isready -h postgres -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 2; done
role=$(psql -h postgres -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'traccar'")
if [ "$role" != "1" ]; then
  # On standard input, where psql quotes :'pw' itself (it does not in a -c string).
  echo "CREATE ROLE traccar LOGIN PASSWORD :'pw';" |
    psql -h postgres -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -v pw="$TRACCAR_DB_PASSWORD"
fi
db=$(psql -h postgres -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'traccar'")
if [ "$db" != "1" ]; then
  psql -h postgres -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE traccar OWNER traccar"
fi
echo "traccar database ready"
