"""The SOP library: procedures in versions, approved before they are in force, and found by their own words.

Additive: four new tables and three permissions. No existing table, policy or
permission is altered; post orders stay as they are
(LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 6).

  a PROCEDURE (sop_documents)
    └── its VERSIONS (sop_versions)   DRAFT ─► SUBMITTED ─► APPROVED
                                                       └──► REJECTED
          └── an approved version's PASSAGES (sop_passages), cut from its text
              when it is approved, and searched by their own words

  which kinds of incident a procedure is for (sop_incident_types)

A VERSION IS IN FORCE ONLY IF IT WAS APPROVED. Of a procedure's approved
versions whose date has come, the latest version is the one in force — unless
it has run out, in which case nothing is: an expired procedure does not fall
back to the version it replaced.

AN APPROVED VERSION IS NOT CHANGED. A trigger refuses every change to a version
that has been approved or rejected. A procedure is changed by drafting the next
version and having that approved.

A PASSAGE IS THE PROCEDURE'S OWN WORDS. Passages are written once, when the
version is approved, and the application's role cannot update or delete them.
The search finds passages and returns them as written; nothing composes an
answer (owner decision E2).

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0148
Revises: 0147
"""
from alembic import op

revision = "0148"
down_revision = "0147"
branch_labels = None
depends_on = None

CATEGORIES = ("'general','emergency','fire','medical','evacuation','access','visitor','patrol','incident_response',"
              "'equipment','contacts'")
STATES = "'DRAFT','SUBMITTED','APPROVED','REJECTED'"

PERMISSIONS = [
    ("sop:read", "Read the SOP library and ask it for the procedure on something", "sop"),
    ("sop:write", "Draft procedures and submit them for approval", "sop"),
    ("sop:approve", "Approve or reject a procedure, and retire one", "sop"),
]

#: What may change of a procedure itself. Its code is what it is cited by and does not change.
DOCUMENT_CHANGES = "title, category, site_id, is_retired, retired_at, retired_by_user_id, updated_at"
#: What may change of a version — while it is a draft or submitted. The trigger
#: holds it still once it is decided.
VERSION_CHANGES = ("body, change_note, state, submitted_at, decided_by_user_id, decided_at, decision_note, "
                   "effective_from, effective_until, attachment_path, attachment_name, attachment_sha256, updated_at")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE sop_documents (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- What a procedure is cited by: SOP-0001. Never reused, never changed.
            code               VARCHAR(20) NOT NULL,
            title              VARCHAR(200) NOT NULL,
            category           VARCHAR(30) NOT NULL DEFAULT 'general',
            -- NULL: for every site.
            site_id            UUID REFERENCES sites(id) ON DELETE CASCADE,
            is_retired         BOOLEAN NOT NULL DEFAULT FALSE,
            retired_at         TIMESTAMPTZ,
            retired_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_sop_code     UNIQUE (tenant_id, code),
            CONSTRAINT ck_sop_title    CHECK (btrim(title) <> ''),
            CONSTRAINT ck_sop_category CHECK (category IN ({CATEGORIES})),
            CONSTRAINT ck_sop_retired  CHECK (is_retired = (retired_at IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX idx_sop_site ON sop_documents (tenant_id, site_id) WHERE NOT is_retired")

    op.execute(f"""
        CREATE TABLE sop_versions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            document_id        UUID NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
            version_no         INTEGER NOT NULL,
            body               TEXT NOT NULL,
            -- What is different from the version before. Asked of every version after the first.
            change_note        TEXT,
            state              VARCHAR(10) NOT NULL DEFAULT 'DRAFT',
            drafted_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            drafted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            submitted_at       TIMESTAMPTZ,
            decided_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at         TIMESTAMPTZ,
            decision_note      TEXT,
            effective_from     TIMESTAMPTZ,
            -- NULL: in force until the next version is.
            effective_until    TIMESTAMPTZ,
            -- The document as it was issued, if one was attached. The platform
            -- does not read it: what is searched and quoted is `body`.
            attachment_path    TEXT,
            attachment_name    VARCHAR(255),
            attachment_sha256  CHAR(64),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_sop_version      UNIQUE (document_id, version_no),
            CONSTRAINT ck_sopv_no          CHECK (version_no >= 1),
            CONSTRAINT ck_sopv_body        CHECK (btrim(body) <> ''),
            CONSTRAINT ck_sopv_state       CHECK (state IN ({STATES})),
            -- Approved: by whom, when, and from when it is in force.
            CONSTRAINT ck_sopv_approved    CHECK (state <> 'APPROVED' OR (
                decided_at IS NOT NULL AND effective_from IS NOT NULL)),
            -- Rejected: says why.
            CONSTRAINT ck_sopv_rejected    CHECK (state <> 'REJECTED' OR (
                decided_at IS NOT NULL AND decision_note IS NOT NULL AND btrim(decision_note) <> '')),
            CONSTRAINT ck_sopv_period      CHECK (effective_until IS NULL OR effective_until > effective_from),
            CONSTRAINT ck_sopv_attachment  CHECK ((attachment_path IS NULL) = (attachment_sha256 IS NULL))
        )
    """)
    # One version of a procedure is being written or awaiting a decision at a time.
    op.execute("CREATE UNIQUE INDEX uq_sop_version_open ON sop_versions (document_id) "
               "WHERE state IN ('DRAFT','SUBMITTED')")
    op.execute("CREATE INDEX idx_sopv_in_force ON sop_versions (document_id, version_no DESC) "
               "WHERE state = 'APPROVED'")

    op.execute("""
        CREATE FUNCTION sop_version_decided() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- Depth 1 is the application's own statement: a foreign key clearing
            -- a person who has left runs deeper and is let through.
            IF OLD.state IN ('APPROVED', 'REJECTED') AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'a procedure version that has been % is not changed', lower(OLD.state)
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER sop_version_decided BEFORE UPDATE ON sop_versions
            FOR EACH ROW EXECUTE FUNCTION sop_version_decided()
    """)

    op.execute("""
        CREATE TABLE sop_passages (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            version_id  UUID NOT NULL REFERENCES sop_versions(id) ON DELETE CASCADE,
            document_id UUID NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
            ordinal     INTEGER NOT NULL,
            -- The heading it stands under in the procedure, if it stands under one.
            heading     TEXT,
            body        TEXT NOT NULL,
            -- What it is found by: its own words and its heading's.
            tsv         tsvector GENERATED ALWAYS AS (
                            to_tsvector('english', coalesce(heading, '') || ' ' || body)) STORED,
            CONSTRAINT uq_sop_passage UNIQUE (version_id, ordinal),
            CONSTRAINT ck_sop_passage CHECK (btrim(body) <> '')
        )
    """)
    op.execute("CREATE INDEX idx_sop_passage_words ON sop_passages USING gin (tsv)")

    op.execute("""
        CREATE TABLE sop_incident_types (
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            document_id   UUID NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
            -- The first part of an incident's alert code: intrusion, fire_smoke, weapon ...
            incident_type VARCHAR(40) NOT NULL,
            PRIMARY KEY (document_id, incident_type),
            CONSTRAINT ck_sop_type CHECK (incident_type ~ '^[a-z][a-z0-9_]{1,39}$')
        )
    """)
    op.execute("CREATE INDEX idx_sop_type ON sop_incident_types (tenant_id, incident_type)")

    for table in ("sop_documents", "sop_versions", "sop_passages", "sop_incident_types"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)
        # The REVOKE is what does it: default privileges hand the application
        # UPDATE and DELETE on every new table.
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO svc_app")
    op.execute(f"GRANT UPDATE ({DOCUMENT_CHANGES}) ON sop_documents TO svc_app")
    op.execute(f"GRANT UPDATE ({VERSION_CHANGES}) ON sop_versions TO svc_app")
    # Which kinds of incident a procedure is for is a list that is set afresh.
    op.execute("GRANT DELETE ON sop_incident_types TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Everybody who works reads the procedures: Admin (2), Manager (8),
    # Supervisor (3), Operator (4), Guard (5), Viewer (6). They are written by
    # Admin, Manager and Supervisor, and approved by Admin and Manager.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'sop:read'), (2, 'sop:write'), (2, 'sop:approve'),
                  (8, 'sop:read'), (8, 'sop:write'), (8, 'sop:approve'),
                  (3, 'sop:read'), (3, 'sop:write'),
                  (4, 'sop:read'),
                  (5, 'sop:read'),
                  (6, 'sop:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'sop:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'sop:%'")
    for table in ("sop_incident_types", "sop_passages", "sop_versions", "sop_documents"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS sop_version_decided()")
