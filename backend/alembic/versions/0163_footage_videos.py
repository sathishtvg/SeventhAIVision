"""Footage Review: uploaded videos, who may use them, and a hold that keeps one past its 30 days.

Phase 1 of Footage Review (docs/plans/2026-10-20-footage-review-design.md,
sections 1 and 2). A video is uploaded in parts, kept for 30 days from the
moment its upload began, and then removed with everything worked out from it -
unless an evidence hold of kind FOOTAGE is in force on it.

A VIDEO IS ITS ORGANISATION'S. `footage_videos` is under the tenant policy like
every other table. Its file lives under `footage/<tenant>/<video>/` in the
storage the installation uses (local disk or S3); the row says where.

THE APPLICATION MAY DELETE A VIDEO, because a person holding footage:delete may
remove one before its 30 days are up. The daily removal of expired videos runs
on the scheduler's superuser session (services/footage_retention.py).

AN EVIDENCE HOLD MAY NOW BE ON FOOTAGE. The allowed kinds of `evidence_holds`
and of `evidence_custody_events` gain 'FOOTAGE' (an approved change to existing
schema): the constraint is dropped and made again with one more value, and
every row that satisfied the old one satisfies the new. The kinds of an evidence
package's items are unchanged.

    footage:upload   Admin, Supervisor, Operator, Manager
    footage:view     Admin, Supervisor, Operator, Viewer, Manager
    footage:review   Admin, Supervisor, Operator, Manager
    footage:delete   Admin

Revision ID: 0163
Revises: 0162
"""
from alembic import op

revision = "0163"
down_revision = "0162"
branch_labels = None
depends_on = None

SOURCES = ("UPLOAD",)
STATUSES = ("UPLOADING", "QUEUED", "PROCESSING", "READY", "FAILED")
OLD_KINDS = "'SNAPSHOT','CLIP','RECORDING','DRONE_MEDIA'"
NEW_KINDS = OLD_KINDS + ",'FOOTAGE'"

PERMISSIONS = [
    ("footage:upload", "Upload a video to Footage Review", "footage"),
    ("footage:view", "Watch and search the videos in Footage Review", "footage"),
    ("footage:review", "Mark what Footage Review found as confirmed or not", "footage"),
    ("footage:delete", "Delete a video from Footage Review before its 30 days are up", "footage"),
]
ROLES = [(2, "footage:upload"), (3, "footage:upload"), (4, "footage:upload"), (8, "footage:upload"),
         (2, "footage:view"), (3, "footage:view"), (4, "footage:view"), (6, "footage:view"), (8, "footage:view"),
         (2, "footage:review"), (3, "footage:review"), (4, "footage:review"), (8, "footage:review"),
         (2, "footage:delete")]


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE footage_videos (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source              VARCHAR(12) NOT NULL DEFAULT 'UPLOAD',
            title               VARCHAR(200) NOT NULL,
            original_filename   VARCHAR(255) NOT NULL,
            content_type        VARCHAR(100),
            size_bytes          BIGINT NOT NULL,
            received_bytes      BIGINT NOT NULL DEFAULT 0,
            part_size           INTEGER NOT NULL,
            storage_path        VARCHAR(500) NOT NULL,
            -- S3 only: the multipart upload and the parts it has been given.
            s3_upload_id        VARCHAR(255),
            s3_parts            JSONB NOT NULL DEFAULT '[]'::jsonb,
            sha256              VARCHAR(64),
            site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
            place               VARCHAR(200),
            filmed_at           TIMESTAMPTZ,
            duration_seconds    NUMERIC(10, 3),
            width               INTEGER,
            height              INTEGER,
            frame_rate          NUMERIC(8, 3),
            status              VARCHAR(12) NOT NULL DEFAULT 'UPLOADING',
            progress            SMALLINT NOT NULL DEFAULT 0,
            error               TEXT,
            attempts            SMALLINT NOT NULL DEFAULT 0,
            uploaded_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            upload_completed_at TIMESTAMPTZ,
            processed_at        TIMESTAMPTZ,
            expires_at          TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_footage_source   CHECK (source IN ({_in(SOURCES)})),
            CONSTRAINT ck_footage_status   CHECK (status IN ({_in(STATUSES)})),
            CONSTRAINT ck_footage_title    CHECK (btrim(title) <> ''),
            CONSTRAINT ck_footage_size     CHECK (size_bytes > 0 AND part_size > 0),
            CONSTRAINT ck_footage_received CHECK (received_bytes >= 0 AND received_bytes <= size_bytes),
            CONSTRAINT ck_footage_progress CHECK (progress BETWEEN 0 AND 100),
            CONSTRAINT ck_footage_failed   CHECK (status <> 'FAILED' OR (error IS NOT NULL AND btrim(error) <> '')),
            CONSTRAINT ck_footage_expiry   CHECK (expires_at > created_at)
        )
    """)
    op.execute("CREATE INDEX idx_footage_videos_list ON footage_videos (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX idx_footage_videos_expiry ON footage_videos (expires_at)")
    op.execute("CREATE INDEX idx_footage_videos_waiting ON footage_videos (status, updated_at) "
               "WHERE status IN ('UPLOADING', 'QUEUED', 'PROCESSING')")

    op.execute("ALTER TABLE footage_videos ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE footage_videos FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_footage_videos ON footage_videos
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON footage_videos FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON footage_videos TO svc_app")

    # A hold may be on footage. Every existing row satisfies the wider list.
    for table, name, check in (
        ("evidence_holds", "ck_evhold_kind", "kind IN ({kinds})"),
        ("evidence_custody_events", "ck_evcust_kind", "kind IS NULL OR kind IN ({kinds})"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({check.format(kinds=NEW_KINDS)})")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    values = ", ".join(f"({role}, '{code}')" for role, code in ROLES)
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES {values}) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    codes = ", ".join(f"'{c}'" for c, _, _ in PERMISSIONS)
    op.execute(f"DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ({codes}))")
    op.execute(f"DELETE FROM permissions WHERE code IN ({codes})")
    # A hold or a custody step about footage cannot stay once footage is not a kind.
    op.execute("DELETE FROM evidence_custody_events WHERE kind = 'FOOTAGE'")
    op.execute("DELETE FROM evidence_holds WHERE kind = 'FOOTAGE'")
    for table, name, check in (
        ("evidence_holds", "ck_evhold_kind", "kind IN ({kinds})"),
        ("evidence_custody_events", "ck_evcust_kind", "kind IS NULL OR kind IN ({kinds})"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({check.format(kinds=OLD_KINDS)})")
    op.execute("DROP TABLE footage_videos")
