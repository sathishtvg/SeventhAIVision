"""Retention for modules 17 to 28: an evidence package removed by a person, with a record that stays.

Phase 2 of closing the feature reference's known limits (docs/plans/2026-11-03-retention-periods-and-evidence-
disposal-design.md). Seven kinds of history get optional periods in services/record_retention.py and need nothing
here. This migration is for evidence packages, which the owner decided a person removes, from a list of those due.

WHAT STAYS WHEN A PACKAGE GOES. `evidence_package_disposals`: the package's number, title, the fingerprint of what
was sealed, when it was sealed, how many items it held, what it was made for, who removed it, when, why and under
what period. Kept for good. The chain of custody stays too: each of its entries is linked to that record, and a
last entry, DISPOSED, is written.

THE APPLICATION ONLY ASKS. It inserts a row into `evidence_package_disposals` with the package, the person and the
reason. Two steps the database runs with that row, as the table's owner, do the rest:

  before   check the package is due (`evidence_package_due`), refuse otherwise, and fill the record from the
           package itself (what the application sent beyond the four facts it knows is overwritten);
  after    link the package's custody entries to the record, write DISPOSED, and delete the package. Its items go
           with it, by the existing rule.

The rule that a sealed package cannot be changed (0144, `evidence_package_sealed`) is not edited. It refuses a
statement and lets through what runs beneath another step - a foreign key's own action, and this. The application's
role still has no right to change or delete a package.

TWO CHANGES TO THE CUSTODY LOG, agreed with the owner on 3 November 2026: a `disposal_id` link, so an entry about a
package is not left pointing at nothing (an entry must be about a package, an item or now a disposal), and the
DISPOSED step.

A NEW RIGHT, `evidence:package:dispose`, for Admin and Manager: the roles that place and lift holds.

migration-safety: two CHECK constraints on evidence_custody_events are replaced by wider ones (every existing row
satisfies both); one nullable column is added. No row is changed.
"""
from alembic import op

revision = "0176"
down_revision = "0175"
branch_labels = None
depends_on = None

#: The steps 0144 allows, and DISPOSED.
OLD_STEPS = ("COLLECTED", "REMOVED", "VIEWED", "SEALED", "LOCKED", "UNLOCKED", "EXPORTED", "DOWNLOADED", "SHARED",
             "RELEASED")
STEPS = (*OLD_STEPS, "DISPOSED")
#: The setting that gives evidence packages a period (core/config_keys.py, services/record_retention.py).
SETTING = "retention.evidence_packages_days"
LEAST_DAYS = 30


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ── The record that stays ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE evidence_package_disposals (
            id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- The package's own id, as a value: the package is gone.
            package_id           UUID         NOT NULL,
            package_number       VARCHAR(24),
            title                VARCHAR(200),
            site_id              UUID         REFERENCES sites(id) ON DELETE SET NULL,
            was_sealed           BOOLEAN,
            sealed_at            TIMESTAMPTZ,
            manifest_sha256      CHAR(64),
            items                INTEGER,
            investigation_id     UUID,
            investigation_number VARCHAR(24),
            incident_id          UUID,
            period_days          INTEGER,
            reason               TEXT         NOT NULL CHECK (btrim(reason) <> ''),
            removed_by_user_id   UUID         REFERENCES users(id) ON DELETE SET NULL,
            removed_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
            ip_address           VARCHAR(45),
            request_id           VARCHAR(64),
            CONSTRAINT uq_evdisposal_package UNIQUE (tenant_id, package_id)
        )
    """)
    op.execute("CREATE INDEX idx_evdisposal_recent ON evidence_package_disposals (tenant_id, removed_at DESC)")
    op.execute("ALTER TABLE evidence_package_disposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evidence_package_disposals FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_evidence_package_disposals ON evidence_package_disposals
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON evidence_package_disposals FROM svc_app")
    # Asked for and read: never changed or deleted by the application.
    op.execute("GRANT SELECT, INSERT ON evidence_package_disposals TO svc_app")

    # ── The custody log: the link, and the step ──────────────────────────────
    op.execute("ALTER TABLE evidence_custody_events ADD COLUMN disposal_id UUID "
               "REFERENCES evidence_package_disposals(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX idx_evcust_disposal ON evidence_custody_events (disposal_id) WHERE disposal_id IS NOT NULL")
    op.execute("ALTER TABLE evidence_custody_events DROP CONSTRAINT ck_evcust_what")
    op.execute("ALTER TABLE evidence_custody_events ADD CONSTRAINT ck_evcust_what "
               "CHECK (package_id IS NOT NULL OR ref_id IS NOT NULL OR disposal_id IS NOT NULL)")
    op.execute("ALTER TABLE evidence_custody_events DROP CONSTRAINT ck_evcust_step")
    op.execute(f"ALTER TABLE evidence_custody_events ADD CONSTRAINT ck_evcust_step CHECK (step IN ({_in(STEPS)}))")

    # ── When a package is due ────────────────────────────────────────────────
    # One definition, read by the due list (as the application, under row security) and by the step below.
    op.execute("""
        CREATE FUNCTION evidence_package_due(p_package uuid, p_cutoff timestamptz)
        RETURNS boolean LANGUAGE sql STABLE SET search_path = public AS $$
            SELECT EXISTS (
                SELECT 1 FROM evidence_packages p
                 WHERE p.id = p_package
                   -- Past the period: a sealed package from when it was sealed, a draft from its last change.
                   AND COALESCE(p.sealed_at, p.updated_at) < p_cutoff
                   -- Nothing in it on hold.
                   AND NOT EXISTS (SELECT 1 FROM evidence_holds h
                                    WHERE h.tenant_id = p.tenant_id AND h.released_at IS NULL
                                      AND (h.package_id = p.id
                                           OR EXISTS (SELECT 1 FROM evidence_package_items i
                                                       WHERE i.package_id = p.id AND i.kind = h.kind
                                                         AND i.ref_id = h.ref_id)))
                   -- What it was made for is over.
                   AND NOT EXISTS (SELECT 1 FROM investigations v
                                    WHERE v.id = p.investigation_id AND v.status <> 'CLOSED')
                   AND NOT EXISTS (SELECT 1 FROM incidents n
                                    WHERE n.id = p.incident_id AND n.status <> 'resolved')
                   -- No case that is not closed links it.
                   AND NOT EXISTS (SELECT 1 FROM case_links l JOIN case_files c ON c.id = l.case_id
                                    WHERE l.kind = 'EVIDENCE_PACKAGE' AND l.ref_id = p.id AND l.removed_at IS NULL
                                      AND c.status <> 'CLOSED')
            )
        $$
    """)
    op.execute("GRANT EXECUTE ON FUNCTION evidence_package_due(uuid, timestamptz) TO svc_app")

    # ── Before: is it due, and what the record says ──────────────────────────
    op.execute(f"""
        CREATE FUNCTION evidence_package_disposal_check() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            pkg    record;
            period integer;
        BEGIN
            SELECT p.*, v.investigation_number AS inv_number
              INTO pkg
              FROM evidence_packages p LEFT JOIN investigations v ON v.id = p.investigation_id
             WHERE p.id = NEW.package_id AND p.tenant_id = NEW.tenant_id
               FOR UPDATE OF p;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no such evidence package' USING ERRCODE = 'no_data_found';
            END IF;
            SELECT CASE WHEN jsonb_typeof(s.setting_value) = 'number' THEN (s.setting_value #>> '{{}}')::numeric END
              INTO period
              FROM tenant_settings s WHERE s.tenant_id = NEW.tenant_id AND s.setting_key = '{SETTING}';
            IF period IS NULL OR period < {LEAST_DAYS} THEN
                RAISE EXCEPTION 'the organisation has set no period for evidence packages'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NOT evidence_package_due(NEW.package_id, now() - make_interval(days => period)) THEN
                RAISE EXCEPTION 'evidence package % is not due for removal', pkg.package_number
                    USING ERRCODE = 'check_violation';
            END IF;
            -- The record is the package's own account of itself, whatever was sent.
            NEW.package_number := pkg.package_number;
            NEW.title := pkg.title;
            NEW.site_id := pkg.site_id;
            NEW.was_sealed := pkg.status = 'SEALED';
            NEW.sealed_at := pkg.sealed_at;
            NEW.manifest_sha256 := pkg.manifest_sha256;
            NEW.items := (SELECT count(*) FROM evidence_package_items i WHERE i.package_id = pkg.id);
            NEW.investigation_id := pkg.investigation_id;
            NEW.investigation_number := pkg.inv_number;
            NEW.incident_id := pkg.incident_id;
            NEW.period_days := period;
            NEW.removed_at := now();
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER evidence_package_disposal_check BEFORE INSERT ON evidence_package_disposals
            FOR EACH ROW EXECUTE FUNCTION evidence_package_disposal_check()
    """)

    # ── After: the chain, the last step, and the package removed ─────────────
    op.execute("""
        CREATE FUNCTION evidence_package_disposal_carry_out() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            UPDATE evidence_custody_events SET disposal_id = NEW.id
             WHERE tenant_id = NEW.tenant_id AND package_id = NEW.package_id;
            INSERT INTO evidence_custody_events
                   (tenant_id, disposal_id, step, actor_user_id, actor_role, reason, detail, ip_address, request_id)
            VALUES (NEW.tenant_id, NEW.id, 'DISPOSED', NEW.removed_by_user_id,
                    (SELECT u.role_id FROM users u WHERE u.id = NEW.removed_by_user_id), NEW.reason,
                    jsonb_build_object('package_number', NEW.package_number, 'items', NEW.items,
                                       'period_days', NEW.period_days, 'manifest_sha256', NEW.manifest_sha256),
                    NEW.ip_address, NEW.request_id);
            DELETE FROM evidence_packages WHERE id = NEW.package_id AND tenant_id = NEW.tenant_id;
            RETURN NULL;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER evidence_package_disposal_carry_out AFTER INSERT ON evidence_package_disposals
            FOR EACH ROW EXECUTE FUNCTION evidence_package_disposal_carry_out()
    """)
    for fn in ("evidence_package_disposal_check()", "evidence_package_disposal_carry_out()"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")

    # ── Who may ──────────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('evidence:package:dispose', 'Remove an evidence package that is due for removal, with a reason',
                'evidence')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
         WHERE r.id IN (2, 8) AND p.code = 'evidence:package:dispose'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
            (SELECT id FROM permissions WHERE code = 'evidence:package:dispose')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'evidence:package:dispose'")
    op.execute("DROP TRIGGER IF EXISTS evidence_package_disposal_carry_out ON evidence_package_disposals")
    op.execute("DROP TRIGGER IF EXISTS evidence_package_disposal_check ON evidence_package_disposals")
    op.execute("DROP FUNCTION IF EXISTS evidence_package_disposal_carry_out()")
    op.execute("DROP FUNCTION IF EXISTS evidence_package_disposal_check()")
    op.execute("DROP FUNCTION IF EXISTS evidence_package_due(uuid, timestamptz)")
    # The DISPOSED entries name no package or item once their record goes: they go with it, as the step does.
    op.execute("DELETE FROM evidence_custody_events WHERE step = 'DISPOSED'")
    op.execute("ALTER TABLE evidence_custody_events DROP CONSTRAINT ck_evcust_step")
    op.execute(f"ALTER TABLE evidence_custody_events ADD CONSTRAINT ck_evcust_step CHECK (step IN ({_in(OLD_STEPS)}))")
    # An entry about a package that was removed points at nothing once the link goes: it goes too.
    op.execute("DELETE FROM evidence_custody_events WHERE package_id IS NULL AND ref_id IS NULL")
    op.execute("ALTER TABLE evidence_custody_events DROP CONSTRAINT ck_evcust_what")
    op.execute("ALTER TABLE evidence_custody_events ADD CONSTRAINT ck_evcust_what "
               "CHECK (package_id IS NOT NULL OR ref_id IS NOT NULL)")
    op.execute("DROP INDEX IF EXISTS idx_evcust_disposal")
    op.execute("ALTER TABLE evidence_custody_events DROP COLUMN disposal_id")
    op.execute("DROP TABLE evidence_package_disposals")
