-- Runs once, at first container creation. Only registers the extension — the
-- actual partman.create_parent(...) calls that register specific tables for
-- automatic partition maintenance live in the Alembic migration instead, because
-- they reference tables (detections, evidence, audit_logs, ...) that don't exist
-- yet at Postgres init time. See backend/alembic/versions/0001_initial_schema.py.
CREATE EXTENSION IF NOT EXISTS pg_partman;
