"""Visitor and contractor authorisation: who approved a visit, for where, for how long, and with whom.

Additive: three new tables and three permissions. No existing table, policy or
permission is altered; visitors, work permits and their check-in are as they
were (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 7).

  a VISIT (an existing visitor)        an AUTHORISATION
  or a WORK PERMIT (existing)   ──►    asked for ─► approved by the host ─► valid ─► run out
                                                └─► declined, with why
                                       for which PLACES of the site, with which ESCORT,
                                       and whether somebody saw the visitor's ID

AN AUTHORISATION DOES NOT LET ANYBODY IN OR KEEP ANYBODY OUT. It is what the
guard at the gate reads before they decide. Checking a visitor in is the
existing endpoint's, unchanged, and nothing here refuses it.

A DOOR EVENT OUTSIDE THE PLACES A VISIT IS AUTHORISED FOR IS SOMETHING TO LOOK
AT, NOT A FINDING AGAINST ANYBODY. `visitor_movement_reviews` records what a
person made of one. Nothing is raised, and no visitor is accused, by a row here
or the lack of one.

WHAT WAS SEEN OF A VISITOR'S ID IS A KIND OF DOCUMENT AND WHO SAW IT. No number
is kept here.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0149
Revises: 0148
"""
from alembic import op

revision = "0149"
down_revision = "0148"
branch_labels = None
depends_on = None

STATES = "'REQUESTED','APPROVED','DECLINED','CANCELLED'"

PERMISSIONS = [
    ("visitorauth:read", "Read visitor and contractor authorisations, and decide the ones one is host of", "visitor"),
    ("visitorauth:write", "Ask for an authorisation, and record an escort and that an ID was seen", "visitor"),
    ("visitorauth:manage", "Decide, extend and cancel any authorisation, and review movements", "visitor"),
]

#: What changes of an authorisation after it is asked for. Not whose visit it
#: is, which site, who asked or when.
CHANGES = ("state, decided_by_user_id, decided_at, decision_note, valid_until, extended_by_user_id, extended_at, "
           "extend_reason, cancelled_by_user_id, cancelled_at, cancel_reason, escort_required, escort_user_id, "
           "escort_note, id_document_kind, id_checked_by_user_id, id_checked_at, updated_at")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE visitor_authorizations (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id               UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            -- One of the two: a visit, or a contractor's work permit.
            visitor_id            UUID REFERENCES visitors(id) ON DELETE CASCADE,
            work_permit_id        UUID REFERENCES work_permits(id) ON DELETE CASCADE,
            purpose               TEXT,
            -- Whose visitor it is: the person who says yes or no.
            host_user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
            state                 VARCHAR(10) NOT NULL DEFAULT 'REQUESTED',
            valid_from            TIMESTAMPTZ NOT NULL,
            valid_until           TIMESTAMPTZ NOT NULL,
            escort_required       BOOLEAN NOT NULL DEFAULT FALSE,
            escort_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
            escort_note           TEXT,
            -- The kind of document somebody saw. Never its number.
            id_document_kind      VARCHAR(30),
            id_checked_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            id_checked_at         TIMESTAMPTZ,
            requested_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            requested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at            TIMESTAMPTZ,
            decision_note         TEXT,
            extended_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            extended_at           TIMESTAMPTZ,
            extend_reason         TEXT,
            cancelled_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            cancelled_at          TIMESTAMPTZ,
            cancel_reason         TEXT,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_visauth_subject   CHECK ((visitor_id IS NULL) <> (work_permit_id IS NULL)),
            CONSTRAINT ck_visauth_state     CHECK (state IN ({STATES})),
            CONSTRAINT ck_visauth_period    CHECK (valid_until > valid_from),
            -- Said yes or no: when. Said no: why.
            CONSTRAINT ck_visauth_decided   CHECK (state NOT IN ('APPROVED','DECLINED') OR decided_at IS NOT NULL),
            CONSTRAINT ck_visauth_declined  CHECK (state <> 'DECLINED' OR (
                decision_note IS NOT NULL AND btrim(decision_note) <> '')),
            CONSTRAINT ck_visauth_cancelled CHECK (state <> 'CANCELLED' OR (
                cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL AND btrim(cancel_reason) <> '')),
            CONSTRAINT ck_visauth_id_seen   CHECK ((id_document_kind IS NULL) = (id_checked_at IS NULL))
        )
    """)
    # One request of a visit, or of a permit, awaits an answer at a time. One
    # that was approved and has run out stays as it was; whether another may be
    # asked for meanwhile depends on the hour, which the router judges.
    op.execute("CREATE UNIQUE INDEX uq_visauth_visitor ON visitor_authorizations (visitor_id) "
               "WHERE visitor_id IS NOT NULL AND state = 'REQUESTED'")
    op.execute("CREATE UNIQUE INDEX uq_visauth_permit ON visitor_authorizations (work_permit_id) "
               "WHERE work_permit_id IS NOT NULL AND state = 'REQUESTED'")
    op.execute("CREATE INDEX idx_visauth_visit ON visitor_authorizations (visitor_id, requested_at DESC) "
               "WHERE visitor_id IS NOT NULL")
    op.execute("CREATE INDEX idx_visauth_work ON visitor_authorizations (work_permit_id, requested_at DESC) "
               "WHERE work_permit_id IS NOT NULL")
    op.execute("CREATE INDEX idx_visauth_site ON visitor_authorizations (tenant_id, site_id, valid_until DESC)")
    op.execute("CREATE INDEX idx_visauth_host ON visitor_authorizations (host_user_id) WHERE state = 'REQUESTED'")

    op.execute("""
        CREATE TABLE visitor_authorization_places (
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            authorization_id UUID NOT NULL REFERENCES visitor_authorizations(id) ON DELETE CASCADE,
            place_id         UUID NOT NULL REFERENCES site_places(id) ON DELETE CASCADE,
            PRIMARY KEY (authorization_id, place_id)
        )
    """)

    op.execute("""
        CREATE TABLE visitor_movement_reviews (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            authorization_id    UUID NOT NULL REFERENCES visitor_authorizations(id) ON DELETE CASCADE,
            access_event_id     UUID NOT NULL REFERENCES access_events(id) ON DELETE CASCADE,
            outcome             VARCHAR(12) NOT NULL,
            note                TEXT,
            reviewed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_vismove_once    UNIQUE (authorization_id, access_event_id),
            CONSTRAINT ck_vismove_outcome CHECK (outcome IN ('IN_ORDER','FOLLOWED_UP')),
            -- Something that was followed up says what was done.
            CONSTRAINT ck_vismove_note    CHECK (outcome = 'IN_ORDER' OR (note IS NOT NULL AND btrim(note) <> ''))
        )
    """)

    for table in ("visitor_authorizations", "visitor_authorization_places", "visitor_movement_reviews"):
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
    op.execute(f"GRANT UPDATE ({CHANGES}) ON visitor_authorizations TO svc_app")
    # The places of an authorisation are a list that is set afresh while it is being asked for.
    op.execute("GRANT DELETE ON visitor_authorization_places TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Read by everybody who works (2, 3, 4, 5, 6, 8) - a host is any of them.
    # Asked for by whoever registers visitors (2, 3, 4, 5, 8). Managed by Admin,
    # Manager and Supervisor.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'visitorauth:read'), (2, 'visitorauth:write'), (2, 'visitorauth:manage'),
                  (8, 'visitorauth:read'), (8, 'visitorauth:write'), (8, 'visitorauth:manage'),
                  (3, 'visitorauth:read'), (3, 'visitorauth:write'), (3, 'visitorauth:manage'),
                  (4, 'visitorauth:read'), (4, 'visitorauth:write'),
                  (5, 'visitorauth:read'), (5, 'visitorauth:write'),
                  (6, 'visitorauth:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'visitorauth:%')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'visitorauth:%'")
    for table in ("visitor_movement_reviews", "visitor_authorization_places", "visitor_authorizations"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
