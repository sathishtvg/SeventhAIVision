"""The daily briefing: what was counted for a day, what a person wrote of it, and that they published it.

Additive: one new table and three permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 10).

  the day's records ──► counted, by fixed rules ──► a DRAFT: sections of counted lines
                                                          │  a person reads it, leaves sections
                                                          │  out, adds a note in their own words
                                                          ▼
                                                     PUBLISHED ── and from then not changed
                                                          │
                              a correction is a new revision for the same day; both stay

THE OPERATIONS BOARD STORES NOTHING. It is counted when somebody asks, from
rows the platform already keeps. The only thing this migration keeps is a
briefing: the lines as they were counted, the period they were counted for,
what the reviewer left out and wrote, and who published it when.

THE PLATFORM DRAFTS; A PERSON PUBLISHES. A draft is read only by whoever
manages briefings. Nothing publishes one but a person's own request, and the
database refuses a published briefing with no time of publishing.

THE COUNTED LINES AND THE PERSON'S WORDS ARE KEPT APART. `content` is what was
counted; `note` is what the reviewer wrote. Neither is put into the other.

A PUBLISHED BRIEFING IS NOT CHANGED. A trigger refuses it. A correction is a
new revision for the same day, and the earlier one stays readable.

THE TABLE IS NOT NAMED `security_…`: that prefix is the intelligence layer's.

SUPER ADMIN (1), GUARD (5) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0152
Revises: 0151
"""
from alembic import op

revision = "0152"
down_revision = "0151"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("board:read", "Read the operations board: what each part of the operation counts, by site and customer", "operations"),
    ("briefing:read", "Read the daily briefings that have been published", "operations"),
    ("briefing:manage", "Draft a daily briefing, review it and publish it", "operations"),
]

#: What the application may change of a briefing, and only while it is a draft (the trigger holds the rest).
BRIEFING_CHANGES = ("content", "period_end", "drafted_at", "drafted_by_user_id", "note", "left_out", "state",
                    "published_by_user_id", "published_at", "discarded_by_user_id", "discarded_at", "updated_at")

NO_SITE = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE daily_briefings (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- One site, or NULL for every site of the organisation together.
            site_id              UUID REFERENCES sites(id) ON DELETE CASCADE,
            -- The calendar day it is for, where the site is.
            briefing_date        DATE NOT NULL,
            revision             SMALLINT NOT NULL DEFAULT 1,
            state                VARCHAR(10) NOT NULL DEFAULT 'DRAFT',
            -- What was counted, and from when to when.
            period_start         TIMESTAMPTZ NOT NULL,
            period_end           TIMESTAMPTZ NOT NULL,
            timezone             VARCHAR(64) NOT NULL,
            content              JSONB NOT NULL,
            drafted_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            drafted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- What the reviewer did with it: the sections left out, and their own words.
            left_out             TEXT[] NOT NULL DEFAULT '{}',
            note                 TEXT,
            published_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            published_at         TIMESTAMPTZ,
            discarded_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            discarded_at         TIMESTAMPTZ,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_brief_state     CHECK (state IN ('DRAFT', 'PUBLISHED', 'DISCARDED')),
            CONSTRAINT ck_brief_revision  CHECK (revision >= 1),
            CONSTRAINT ck_brief_period    CHECK (period_end > period_start),
            -- Published by a person, at a time.
            CONSTRAINT ck_brief_published CHECK (state <> 'PUBLISHED' OR published_at IS NOT NULL),
            CONSTRAINT ck_brief_discarded CHECK (state <> 'DISCARDED' OR discarded_at IS NOT NULL),
            CONSTRAINT ck_brief_note      CHECK (note IS NULL OR btrim(note) <> '')
        )
    """)
    # A day's revisions are numbered; there is one draft of a day at a time.
    op.execute(f"""
        CREATE UNIQUE INDEX uq_brief_revision ON daily_briefings
            (tenant_id, COALESCE(site_id, {NO_SITE}), briefing_date, revision)
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX uq_brief_one_draft ON daily_briefings
            (tenant_id, COALESCE(site_id, {NO_SITE}), briefing_date) WHERE state = 'DRAFT'
    """)
    op.execute("CREATE INDEX idx_brief_day ON daily_briefings (tenant_id, briefing_date DESC, published_at DESC)")

    # A briefing that is published, or a draft that was discarded, is not changed.
    op.execute("""
        CREATE FUNCTION daily_briefing_settled() RETURNS trigger AS $$
        BEGIN
            IF OLD.state IN ('PUBLISHED', 'DISCARDED') AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'a briefing that is published or discarded is not changed';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER daily_briefing_settled BEFORE UPDATE ON daily_briefings "
               "FOR EACH ROW EXECUTE FUNCTION daily_briefing_settled()")

    op.execute("ALTER TABLE daily_briefings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE daily_briefings FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_daily_briefings ON daily_briefings
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    # The REVOKE is what does it: default privileges hand the application
    # UPDATE and DELETE on every new table.
    op.execute("REVOKE ALL ON daily_briefings FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON daily_briefings TO svc_app")
    op.execute(f"GRANT UPDATE ({', '.join(BRIEFING_CHANGES)}) ON daily_briefings TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Read by whoever watches a site from a desk (2, 3, 4, 6, 8). Drafted and
    # published by Admin, Manager and Supervisor.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'board:read'), (2, 'briefing:read'), (2, 'briefing:manage'),
                  (8, 'board:read'), (8, 'briefing:read'), (8, 'briefing:manage'),
                  (3, 'board:read'), (3, 'briefing:read'), (3, 'briefing:manage'),
                  (4, 'board:read'), (4, 'briefing:read'),
                  (6, 'board:read'), (6, 'briefing:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('board:read', 'briefing:read', 'briefing:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('board:read', 'briefing:read', 'briefing:manage')")
    op.execute("DROP TABLE IF EXISTS daily_briefings CASCADE")
    op.execute("DROP FUNCTION IF EXISTS daily_briefing_settled()")
