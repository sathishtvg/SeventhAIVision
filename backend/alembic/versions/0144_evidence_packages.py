"""Evidence packages: what was kept about a matter, sealed, held and accounted for.

Additive: four new tables, two functions with their triggers, four permissions.
No existing table, policy or permission is altered
(LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 2).

  an investigation ─► a package ─► SEALED ─► exported, shared, released
  or an incident       (draft)      │
                                    ├─ a manifest of every item and its
                                    │  checksum, and the manifest's own hash
                                    └─ a hold on every item, so that the
                                       retention purge leaves it alone

A PACKAGE HOLDS REFERENCES. The frames, clips, recordings and drone media stay
where the platform already keeps them; an item is which one, when it was
captured and the checksum the platform had recorded for it when it was added.

SEALED MEANS SEALED, AND THE DATABASE HOLDS IT. Once a package is sealed a
trigger refuses any statement that changes it or its items — from the
application, from a script, from anybody who has not first dropped the
trigger. A wrong package is superseded by another; it is not corrected.
The database's own referential actions are let through, so that removing a
user, a site or a whole organisation still works: those arrive from inside
another trigger, which is how they are told apart.

A HOLD IS WHAT STOPS A PURGE. The three retention jobs each leave alone
anything with a hold that has not been released (they are changed for that, and
for nothing else). Sealing a package places a hold on each of its items. A hold
is released by a person, with a reason, and is never deleted.

CUSTODY IS ADDED TO AND NEVER REWRITTEN. `evidence_custody_events` takes
SELECT and INSERT from the application and nothing else. Opening an individual
frame or clip continues to be written to the existing `evidence_access_log`;
the two are read together as one chain.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE, and nor does a guard (5).

Revision ID: 0144
Revises: 0143
"""
from alembic import op

revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None

#: What a package can hold, by where the platform keeps it.
KINDS = "'SNAPSHOT','CLIP','RECORDING','DRONE_MEDIA'"
#: The steps of custody this table records. CAPTURED is not among them: when a
#: thing was captured is on the thing. ACCESSED is not either: the opening of a
#: frame or a clip is in the existing `evidence_access_log`.
STEPS = "'COLLECTED','REMOVED','VIEWED','SEALED','LOCKED','UNLOCKED','EXPORTED','DOWNLOADED','SHARED','RELEASED'"

PERMISSIONS = [
    ("evidence:package:read", "View evidence packages and what is in them", "evidence"),
    ("evidence:package:manage", "Put together and seal evidence packages", "evidence"),
    ("evidence:package:export", "Export a sealed evidence package, and record its disclosure", "evidence"),
    ("evidence:hold:manage", "Place and release holds that stop evidence being purged", "evidence"),
]

#: What sealing changes. Nothing else of a package can be changed by the
#: application, and after sealing nothing can.
PACKAGE_CHANGES = "status, sealed_by_user_id, sealed_at, manifest, manifest_sha256, updated_at"
#: A hold is released, never removed.
HOLD_CHANGES = "released_by_user_id, released_at, release_reason"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE evidence_packages (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- NULL when it spans sites: then only someone not restricted to
            -- certain sites can see it.
            site_id            UUID REFERENCES sites(id) ON DELETE SET NULL,
            package_number     VARCHAR(24) NOT NULL,
            title              VARCHAR(200) NOT NULL,
            purpose            TEXT NOT NULL,
            investigation_id   UUID REFERENCES investigations(id) ON DELETE SET NULL,
            incident_id        UUID REFERENCES incidents(id) ON DELETE SET NULL,
            status             VARCHAR(8) NOT NULL DEFAULT 'DRAFT',
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            sealed_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            sealed_at          TIMESTAMPTZ,
            -- Everything in the package as it stood when it was sealed, and
            -- the SHA-256 of that, canonically written.
            manifest           JSONB,
            manifest_sha256    CHAR(64),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_evidence_package_number UNIQUE (tenant_id, package_number),
            CONSTRAINT ck_evpkg_status  CHECK (status IN ('DRAFT','SEALED')),
            CONSTRAINT ck_evpkg_title   CHECK (btrim(title) <> ''),
            CONSTRAINT ck_evpkg_purpose CHECK (btrim(purpose) <> ''),
            CONSTRAINT ck_evpkg_sealed  CHECK (
                (status = 'DRAFT' AND sealed_at IS NULL AND manifest IS NULL AND manifest_sha256 IS NULL)
                OR (status = 'SEALED' AND sealed_at IS NOT NULL AND manifest IS NOT NULL
                    AND manifest_sha256 ~ '^[0-9a-f]{64}$'))
        )
    """)
    op.execute("CREATE INDEX idx_evpkg_tenant_time ON evidence_packages (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX idx_evpkg_investigation ON evidence_packages (investigation_id) "
               "WHERE investigation_id IS NOT NULL")
    op.execute("CREATE INDEX idx_evpkg_incident ON evidence_packages (incident_id) WHERE incident_id IS NOT NULL")

    op.execute(f"""
        CREATE TABLE evidence_package_items (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            package_id       UUID NOT NULL REFERENCES evidence_packages(id) ON DELETE CASCADE,
            kind             VARCHAR(12) NOT NULL,
            -- The thing referred to. No foreign key: it is in one of three
            -- tables, one of them partitioned by time.
            ref_id           UUID NOT NULL,
            captured_at      TIMESTAMPTZ NOT NULL,
            site_id          UUID REFERENCES sites(id) ON DELETE SET NULL,
            camera_id        UUID REFERENCES cameras(id) ON DELETE SET NULL,
            -- As the platform had recorded it when the item was added. NULL
            -- when the platform had recorded none.
            checksum_sha256  VARCHAR(64),
            note             TEXT,
            added_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_evitem_kind CHECK (kind IN ({KINDS})),
            CONSTRAINT uq_evitem_once UNIQUE (package_id, kind, ref_id)
        )
    """)
    op.execute("CREATE INDEX idx_evitem_thing ON evidence_package_items (tenant_id, kind, ref_id)")

    op.execute(f"""
        CREATE TABLE evidence_holds (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind                VARCHAR(12) NOT NULL,
            ref_id              UUID NOT NULL,
            site_id             UUID REFERENCES sites(id) ON DELETE SET NULL,
            -- The package whose sealing placed it; NULL for one placed by hand.
            package_id          UUID REFERENCES evidence_packages(id) ON DELETE SET NULL,
            reason              TEXT NOT NULL,
            placed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            placed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            released_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            released_at         TIMESTAMPTZ,
            release_reason      TEXT,
            CONSTRAINT ck_evhold_kind    CHECK (kind IN ({KINDS})),
            CONSTRAINT ck_evhold_reason  CHECK (btrim(reason) <> ''),
            CONSTRAINT ck_evhold_release CHECK (
                released_at IS NULL OR (release_reason IS NOT NULL AND btrim(release_reason) <> ''))
        )
    """)
    # What the purges ask: is there a hold on this, still in force?
    op.execute("CREATE INDEX idx_evhold_in_force ON evidence_holds (kind, ref_id) WHERE released_at IS NULL")
    op.execute("CREATE INDEX idx_evhold_package ON evidence_holds (package_id) WHERE package_id IS NOT NULL")
    # A package holds a thing once.
    op.execute("CREATE UNIQUE INDEX uq_evhold_package_thing ON evidence_holds (package_id, kind, ref_id) "
               "WHERE package_id IS NOT NULL")

    op.execute(f"""
        CREATE TABLE evidence_custody_events (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            package_id    UUID REFERENCES evidence_packages(id) ON DELETE SET NULL,
            -- The one thing the step was about, when it was about one.
            kind          VARCHAR(12),
            ref_id        UUID,
            step          VARCHAR(12) NOT NULL,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_role    SMALLINT NOT NULL,
            reason        TEXT,
            detail        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ip_address    VARCHAR(45),
            request_id    VARCHAR(64),
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_evcust_step  CHECK (step IN ({STEPS})),
            CONSTRAINT ck_evcust_kind  CHECK (kind IS NULL OR kind IN ({KINDS})),
            CONSTRAINT ck_evcust_about CHECK ((kind IS NULL) = (ref_id IS NULL)),
            CONSTRAINT ck_evcust_what  CHECK (package_id IS NOT NULL OR ref_id IS NOT NULL),
            -- Handing evidence to somebody, and letting it go, say why.
            CONSTRAINT ck_evcust_why   CHECK (step NOT IN ('SHARED','RELEASED','UNLOCKED','LOCKED')
                                              OR (reason IS NOT NULL AND btrim(reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_evcust_package ON evidence_custody_events (package_id, occurred_at)")
    op.execute("CREATE INDEX idx_evcust_thing ON evidence_custody_events (tenant_id, kind, ref_id, occurred_at) "
               "WHERE ref_id IS NOT NULL")

    # ── Sealed means sealed ───────────────────────────────────────────────────
    op.execute("""
        CREATE FUNCTION evidence_package_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- Deeper than one: a foreign key's SET NULL or CASCADE, not a statement.
            IF OLD.status = 'SEALED' AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'evidence package % is sealed and cannot be changed', OLD.package_number
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END $$
    """)
    op.execute("""
        CREATE TRIGGER evidence_package_sealed BEFORE UPDATE OR DELETE ON evidence_packages
            FOR EACH ROW EXECUTE FUNCTION evidence_package_sealed()
    """)
    op.execute("""
        CREATE FUNCTION evidence_package_item_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            its_status text;
        BEGIN
            IF pg_trigger_depth() > 1 THEN
                RETURN COALESCE(NEW, OLD);
            END IF;
            SELECT status INTO its_status FROM evidence_packages
             WHERE id = COALESCE(NEW.package_id, OLD.package_id);
            IF its_status = 'SEALED' THEN
                RAISE EXCEPTION 'the items of a sealed evidence package cannot be changed'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END $$
    """)
    op.execute("""
        CREATE TRIGGER evidence_package_item_sealed BEFORE INSERT OR UPDATE OR DELETE ON evidence_package_items
            FOR EACH ROW EXECUTE FUNCTION evidence_package_item_sealed()
    """)

    for table in ("evidence_packages", "evidence_package_items", "evidence_holds", "evidence_custody_events"):
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
    op.execute(f"GRANT UPDATE ({PACKAGE_CHANGES}) ON evidence_packages TO svc_app")
    # A draft is put together by adding and taking away. The trigger above is
    # what stops either once it is sealed.
    op.execute("GRANT DELETE ON evidence_package_items TO svc_app")
    op.execute(f"GRANT UPDATE ({HOLD_CHANGES}) ON evidence_holds TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Admin (2) and Manager (8) do all of it. Supervisor (3) puts together,
    # seals and exports, and does not release a hold. Operator (4) puts
    # together and seals. Viewer (6) reads.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'evidence:package:read'), (2, 'evidence:package:manage'), (2, 'evidence:package:export'),
                  (2, 'evidence:hold:manage'),
                  (8, 'evidence:package:read'), (8, 'evidence:package:manage'), (8, 'evidence:package:export'),
                  (8, 'evidence:hold:manage'),
                  (3, 'evidence:package:read'), (3, 'evidence:package:manage'), (3, 'evidence:package:export'),
                  (4, 'evidence:package:read'), (4, 'evidence:package:manage'),
                  (6, 'evidence:package:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions
                                  WHERE code LIKE 'evidence:package:%' OR code = 'evidence:hold:manage')
    """)
    op.execute("DELETE FROM permissions WHERE code LIKE 'evidence:package:%' OR code = 'evidence:hold:manage'")
    for table in ("evidence_custody_events", "evidence_holds", "evidence_package_items", "evidence_packages"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS evidence_package_item_sealed()")
    op.execute("DROP FUNCTION IF EXISTS evidence_package_sealed()")
