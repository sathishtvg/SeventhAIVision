"""Smart investigation: a file of what somebody found, and why they were looking.

Additive: two new tables and two permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 1).

  search across sources ─► an investigation ─► the records put in it, in order
                            (who opened it,     (each a REFERENCE to a record
                             and for what)       that stays where it is)

AN ITEM IS A REFERENCE, NOT A COPY. It holds which kind of record, its id, when
that record happened and at which site. The record itself is read where it
lives each time the file is opened, under the reader's own permissions: what a
reader may not see elsewhere they do not see here, and a record that has since
been purged or erased is not kept alive by having been filed.

A FILE IS NOT REWRITTEN. The application's role may open an investigation and
add to it. It can change only what closing and reopening change, and on an item
only the three columns that say it was set aside, by whom and why. It can
delete neither, so what was once filed, and that it was later set aside, both
stay on the record.

WHY IS PART OF THE RECORD. An investigation cannot be opened without a reason,
closed without a note, or have an item set aside without one.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE. The platform owner is
not a customer's investigator, and a customer's own client does not search the
security company's records. A guard (5) does not investigate either.

Revision ID: 0143
Revises: 0142
"""
from alembic import op

revision = "0143"
down_revision = "0142"
branch_labels = None
depends_on = None

#: The kinds of record a search reads and an investigation can hold. NOTE is a
#: person's own remark in the file and refers to nothing.
KINDS = ("'ALERT','INCIDENT','PLATE_READ','FACE_MATCH','DETECTION','ACCESS','VISITOR','OCCURRENCE','DRONE',"
         "'ALARM','PATROL_SCAN','MAN_DOWN','SITUATION','SENSOR','NOTE'")

PERMISSIONS = [
    ("investigation:read", "Search security records across sources and read investigations", "investigation"),
    ("investigation:manage", "Open, add to and close investigations", "investigation"),
]

#: What closing and reopening an investigation change. Nothing else of a row can
#: be changed by the application: not its title, its reason or who opened it.
INVESTIGATION_CHANGES = "status, closed_at, closed_by_user_id, closing_note, updated_at"
#: An item is set aside, never removed.
ITEM_CHANGES = "set_aside_at, set_aside_by_user_id, set_aside_reason"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE investigations (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- NULL when it spans sites: then only someone not restricted to
            -- certain sites can see it, as with every other site-scoped list.
            site_id              UUID REFERENCES sites(id) ON DELETE SET NULL,
            investigation_number VARCHAR(24) NOT NULL,
            title                VARCHAR(200) NOT NULL,
            reason               TEXT NOT NULL,
            status               VARCHAR(8) NOT NULL DEFAULT 'OPEN',
            -- What it grew out of, if anything.
            incident_id          UUID REFERENCES incidents(id) ON DELETE SET NULL,
            situation_id         UUID REFERENCES security_situations(id) ON DELETE SET NULL,
            opened_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            opened_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            closed_at            TIMESTAMPTZ,
            closing_note         TEXT,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_investigation_number UNIQUE (tenant_id, investigation_number),
            CONSTRAINT ck_investigation_status CHECK (status IN ('OPEN','CLOSED')),
            CONSTRAINT ck_investigation_title  CHECK (btrim(title) <> ''),
            CONSTRAINT ck_investigation_reason CHECK (btrim(reason) <> ''),
            -- Closed means somebody said how it ended.
            CONSTRAINT ck_investigation_closed CHECK (
                (status = 'OPEN' AND closed_at IS NULL)
                OR (status = 'CLOSED' AND closed_at IS NOT NULL
                    AND closing_note IS NOT NULL AND btrim(closing_note) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_investigations_tenant_time ON investigations (tenant_id, opened_at DESC)")
    op.execute("CREATE INDEX idx_investigations_site ON investigations (site_id) WHERE site_id IS NOT NULL")
    op.execute("CREATE INDEX idx_investigations_incident ON investigations (incident_id) "
               "WHERE incident_id IS NOT NULL")

    op.execute(f"""
        CREATE TABLE investigation_items (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            investigation_id     UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            kind                 VARCHAR(16) NOT NULL,
            -- The record referred to. No foreign key: it may be in any of a
            -- dozen tables, some of them partitioned by time.
            ref_id               UUID,
            -- When the record happened, so that the file reads in order and a
            -- partitioned record can be found again.
            occurred_at          TIMESTAMPTZ NOT NULL,
            site_id              UUID REFERENCES sites(id) ON DELETE SET NULL,
            -- Why it matters to the investigation; for a NOTE, the note.
            note                 TEXT,
            added_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            added_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            set_aside_at         TIMESTAMPTZ,
            set_aside_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            set_aside_reason     TEXT,
            CONSTRAINT ck_invitem_kind CHECK (kind IN ({KINDS})),
            CONSTRAINT ck_invitem_ref  CHECK (
                (kind = 'NOTE' AND ref_id IS NULL AND note IS NOT NULL AND btrim(note) <> '')
                OR (kind <> 'NOTE' AND ref_id IS NOT NULL)),
            CONSTRAINT ck_invitem_set_aside CHECK (
                set_aside_at IS NULL
                OR (set_aside_reason IS NOT NULL AND btrim(set_aside_reason) <> ''))
        )
    """)
    # A record is in a file once. Notes are not records and may repeat.
    op.execute("CREATE UNIQUE INDEX uq_invitem_record ON investigation_items (investigation_id, kind, ref_id) "
               "WHERE ref_id IS NOT NULL")
    op.execute("CREATE INDEX idx_invitem_file_time ON investigation_items (investigation_id, occurred_at)")
    op.execute("CREATE INDEX idx_invitem_record ON investigation_items (tenant_id, kind, ref_id) "
               "WHERE ref_id IS NOT NULL")

    for table in ("investigations", "investigation_items"):
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
    op.execute(f"GRANT UPDATE ({INVESTIGATION_CHANGES}) ON investigations TO svc_app")
    op.execute(f"GRANT UPDATE ({ITEM_CHANGES}) ON investigation_items TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Admin (2), Manager (8), Supervisor (3) and Operator (4) investigate.
    # Viewer (6) may search and read, and files nothing.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'investigation:read'), (2, 'investigation:manage'),
                  (8, 'investigation:read'), (8, 'investigation:manage'),
                  (3, 'investigation:read'), (3, 'investigation:manage'),
                  (4, 'investigation:read'), (4, 'investigation:manage'),
                  (6, 'investigation:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'investigation:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'investigation:%'")
    op.execute("DROP TABLE IF EXISTS investigation_items CASCADE")
    op.execute("DROP TABLE IF EXISTS investigations CASCADE")
