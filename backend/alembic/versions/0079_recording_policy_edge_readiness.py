"""Per-site recording policy + evidence sync state (Phase X-A).

WHY THIS EXISTS
    The platform records and retains on one tenant-wide setting
    (`recording.retention_days`, read by services/continuous_recording.py).
    That is a centralized-VMS assumption: every site behaves identically and
    everything lands in one place.

    Real security operators do not work that way. One customer's site keeps
    365 days on-premise and ships nothing; another keeps 7 days locally and
    only incidents centrally; a third records nothing continuous at all and
    stores only AI/alarm clips. Retention is a contractual term that varies
    per client, not a platform constant.

    This migration makes the DATA MODEL support that, without yet building an
    edge gateway. Two independent wins:

      1. Per-site retention and record mode work immediately, centrally. That
         is useful on its own — it is the difference between "we keep 7 days
         for everyone" and "we keep what each contract says".

      2. The columns an edge gateway will need (where a recording physically
         lives, whether evidence has synced, how much bandwidth a site may
         use) exist from the start. Adding them later, to partitioned
         evidence tables holding real data, is a far more expensive change.

    Nothing here changes behaviour on its own: every site without an explicit
    policy row falls back to today's tenant setting. Deliberate — a migration
    that silently altered how long footage is kept would be a compliance
    incident, not a feature.

Revision ID: 0079
Revises: 0078
"""
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Per-site recording policy ─────────────────────────────────────────
    #
    # record_mode  — what gets captured at all
    # sync_mode    — what of that reaches the central server
    #
    # Kept as two orthogonal columns rather than one combined enum: "record
    # continuously but only ship incidents" and "record only incidents and
    # ship all of them" are both real configurations, and collapsing them
    # into a single mode makes half the matrix unreachable.
    op.execute("""
        CREATE TABLE IF NOT EXISTS recording_policies (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,

            record_mode           VARCHAR(20) NOT NULL DEFAULT 'continuous'
                                  CHECK (record_mode IN ('continuous','motion','ai_event','scheduled','off')),
            sync_mode             VARCHAR(20) NOT NULL DEFAULT 'central'
                                  CHECK (sync_mode IN ('central','local_only','incident_only','scheduled','manual')),

            -- Retention is split because the two stores answer to different
            -- constraints: local disk is finite and cheap, central storage is
            -- elastic and expensive. NULL means "inherit the tenant setting",
            -- which is what every existing site does until configured.
            local_retention_days   INTEGER CHECK (local_retention_days   IS NULL OR local_retention_days   >= 0),
            central_retention_days INTEGER CHECK (central_retention_days IS NULL OR central_retention_days >= 0),

            -- Scheduled sync: quiet-hours upload window, site-local time.
            sync_window_start     TIME,
            sync_window_end       TIME,
            bandwidth_limit_kbps  INTEGER CHECK (bandwidth_limit_kbps IS NULL OR bandwidth_limit_kbps > 0),

            -- Event clip bounds. Defaults chosen to match the existing
            -- pre/post event buffer so clip length does not silently change
            -- for sites that adopt a policy.
            clip_pre_seconds      INTEGER NOT NULL DEFAULT 20 CHECK (clip_pre_seconds  >= 0),
            clip_post_seconds     INTEGER NOT NULL DEFAULT 60 CHECK (clip_post_seconds >= 0),

            compression           VARCHAR(20) NOT NULL DEFAULT 'none'
                                  CHECK (compression IN ('none','h264','h265')),
            encrypt_archives      BOOLEAN NOT NULL DEFAULT FALSE,
            verify_checksums      BOOLEAN NOT NULL DEFAULT TRUE,

            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- One active policy per site: two contradictory policies would
            -- make retention non-deterministic, and retention is evidence.
            CONSTRAINT uq_recording_policy_site UNIQUE (site_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recording_policies_tenant
            ON recording_policies(tenant_id) WHERE is_active = TRUE
    """)

    op.execute("ALTER TABLE recording_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE recording_policies FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_recording_policies ON recording_policies
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ── Where a recording physically lives ────────────────────────────────
    #
    # Today every recording is central, so the default is honest for existing
    # rows. Once a gateway exists, 'local' rows are playable only via that
    # site and the UI must say so rather than serving a 404.
    op.execute("""
        ALTER TABLE recordings
          ADD COLUMN IF NOT EXISTS storage_location VARCHAR(10) NOT NULL DEFAULT 'central',
          ADD COLUMN IF NOT EXISTS synced_at        TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS sync_state       VARCHAR(20) NOT NULL DEFAULT 'not_required'
    """)
    op.execute("""
        ALTER TABLE recordings
          ADD CONSTRAINT ck_recordings_storage_location
          CHECK (storage_location IN ('central','local','both'))
    """)
    op.execute("""
        ALTER TABLE recordings
          ADD CONSTRAINT ck_recordings_sync_state
          CHECK (sync_state IN ('not_required','pending','uploading','synced','failed'))
    """)
    # The sync worker's hot path: "what is still owed to HQ, oldest first".
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recordings_pending_sync
            ON recordings(site_id, started_at)
            WHERE sync_state IN ('pending','failed')
    """)

    # ── Evidence sync state ───────────────────────────────────────────────
    #
    # evidence is RANGE-partitioned on captured_at, so these columns are added
    # to the parent and inherited by every partition. Doing it now, while the
    # table is small, avoids rewriting years of partitions later.
    op.execute("""
        ALTER TABLE evidence
          ADD COLUMN IF NOT EXISTS storage_location VARCHAR(10) NOT NULL DEFAULT 'central',
          ADD COLUMN IF NOT EXISTS synced_at        TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS sync_state       VARCHAR(20) NOT NULL DEFAULT 'synced',
          ADD COLUMN IF NOT EXISTS site_id          UUID
    """)
    # Existing evidence is already on the central server, so 'synced' is the
    # truthful default here — unlike recordings, where 'not_required' is.
    op.execute("""
        ALTER TABLE evidence
          ADD CONSTRAINT ck_evidence_storage_location
          CHECK (storage_location IN ('central','local','both'))
    """)
    op.execute("""
        ALTER TABLE evidence
          ADD CONSTRAINT ck_evidence_sync_state
          CHECK (sync_state IN ('not_required','pending','uploading','synced','failed'))
    """)

    # ── Permissions ───────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category) VALUES
          ('recording_policy:read',   'View per-site recording and retention policy', 'recording'),
          ('recording_policy:manage', 'Set per-site recording, retention and sync policy', 'recording')
        ON CONFLICT (code) DO NOTHING
    """)
    # Read for anyone who runs a site (incl. Manager=8 and Supervisor=3);
    # manage restricted to super_admin/admin/manager. Retention length is a
    # contractual and evidentiary commitment — a supervisor should see what
    # the policy is without being able to shorten it.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,3,8) AND p.code = 'recording_policy:read'
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.id IN (1,2,8) AND p.code = 'recording_policy:manage'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
          (SELECT id FROM permissions WHERE code IN ('recording_policy:read','recording_policy:manage'))
    """)
    op.execute("""
        DELETE FROM permissions WHERE code IN ('recording_policy:read','recording_policy:manage')
    """)
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_sync_state")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_storage_location")
    op.execute("""
        ALTER TABLE evidence
          DROP COLUMN IF EXISTS site_id,
          DROP COLUMN IF EXISTS sync_state,
          DROP COLUMN IF EXISTS synced_at,
          DROP COLUMN IF EXISTS storage_location
    """)
    op.execute("DROP INDEX IF EXISTS idx_recordings_pending_sync")
    op.execute("ALTER TABLE recordings DROP CONSTRAINT IF EXISTS ck_recordings_sync_state")
    op.execute("ALTER TABLE recordings DROP CONSTRAINT IF EXISTS ck_recordings_storage_location")
    op.execute("""
        ALTER TABLE recordings
          DROP COLUMN IF EXISTS sync_state,
          DROP COLUMN IF EXISTS synced_at,
          DROP COLUMN IF EXISTS storage_location
    """)
    op.execute("DROP POLICY IF EXISTS tenant_isolation_recording_policies ON recording_policies")
    op.execute("DROP TABLE IF EXISTS recording_policies")
