"""Security cases: a case, the people on it, its tasks, what is linked to it, who is named in it, and its history.

Additive: six new tables and three permissions. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 12).

  an incident · an investigation · an evidence package ──► LINKED, by reference, to a CASE
                                                                   │
        a lead and investigators · tasks · notes · the people and vehicles named in it
                                                                   │
        OPEN ──► the lead asks to close it, with what was found ──► AWAITING_APPROVAL
                                                                   │
                          somebody else approves ──► CLOSED     or declines, with why ──► OPEN

A CASE HOLDS NOTHING BUT ITS OWN RECORD. An incident, an investigation and an
evidence package are linked to it by reference and stay where they are, under
their own permissions. Nothing of theirs is copied here, and removing a case's
link removes nothing of theirs.

CLOSING A CASE TAKES TWO PEOPLE. Whoever asks for it to be closed says what was
found; somebody else approves. The database refuses a case closed by the person
who asked.

A CLOSED CASE IS NOT CHANGED. A trigger refuses any change to it but reopening
it, and refuses anything added to it. Its history (`case_entries`) is added to
and never rewritten: the application's role may read and add, and nothing else.

NOTHING IS REMOVED. An investigator, a link and a named person or vehicle are
taken off with who did it and — for a link or a name — why; the row stays.

BEING NAMED IN A CASE IS NOT AN ACCUSATION. A person or a vehicle is recorded
with how it is connected — reported it, saw it, was affected, is named in it —
by a person, in their own words. Nothing here names anybody by itself.

THE TABLES ARE NOT NAMED `security_…`: that prefix is the intelligence layer's.

SUPER ADMIN (1), GUARD (5) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0155
Revises: 0154
"""
from alembic import op

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None

PERMISSIONS = [
    ("case:read", "Read security cases", "cases"),
    ("case:work", "Open a case and work on one: notes, tasks, links, the people and vehicles named in it", "cases"),
    ("case:manage", "Assign a case, approve or decline its closing, and reopen it", "cases"),
]
TABLES = ("case_files", "case_investigators", "case_tasks", "case_entries", "case_links", "case_parties")

#: What the application may change of each. A case's number, its site, who opened it and when are not among them;
#: nor is what a task is, or anything of a link or a name but its being taken off.
CHANGES = {
    "case_files": ("title", "summary", "category", "priority", "status", "lead_user_id", "outcome",
                   "close_requested_by_user_id", "close_requested_at", "closed_by_user_id", "closed_at", "updated_at"),
    "case_investigators": ("removed_at", "removed_by_user_id"),
    "case_tasks": ("state", "done_at", "done_by_user_id", "done_note", "dropped_reason", "updated_at"),
    "case_links": ("removed_at", "removed_by_user_id", "remove_reason"),
    "case_parties": ("removed_at", "removed_by_user_id", "remove_reason"),
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE case_files (
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id                    UUID REFERENCES sites(id) ON DELETE SET NULL,
            case_number                VARCHAR(20) NOT NULL,
            title                      VARCHAR(200) NOT NULL,
            -- What the case is about, and why it was opened.
            summary                    TEXT NOT NULL,
            category                   VARCHAR(20) NOT NULL DEFAULT 'OTHER',
            priority                   VARCHAR(10) NOT NULL DEFAULT 'NORMAL',
            status                     VARCHAR(20) NOT NULL DEFAULT 'OPEN',
            lead_user_id               UUID REFERENCES users(id) ON DELETE SET NULL,
            opened_by_user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
            opened_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- What was found and what was done, written by whoever asks for it to be closed.
            outcome                    TEXT,
            close_requested_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            close_requested_at         TIMESTAMPTZ,
            closed_by_user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
            closed_at                  TIMESTAMPTZ,
            updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_case_status   CHECK (status IN ('OPEN', 'AWAITING_APPROVAL', 'CLOSED')),
            CONSTRAINT ck_case_category CHECK (category IN ('THEFT', 'TRESPASS', 'DAMAGE', 'SAFETY', 'ACCESS', 'OTHER')),
            CONSTRAINT ck_case_priority CHECK (priority IN ('LOW', 'NORMAL', 'HIGH')),
            CONSTRAINT ck_case_title    CHECK (btrim(title) <> ''),
            CONSTRAINT ck_case_summary  CHECK (btrim(summary) <> ''),
            -- A case waiting for approval, or closed, says what was found.
            CONSTRAINT ck_case_asked    CHECK (status = 'OPEN' OR (
                outcome IS NOT NULL AND btrim(outcome) <> '' AND close_requested_at IS NOT NULL)),
            CONSTRAINT ck_case_closed   CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL)),
            -- Closing takes two people: whoever approved is not whoever asked.
            CONSTRAINT ck_case_two      CHECK (closed_by_user_id IS NULL OR close_requested_by_user_id IS NULL
                                               OR closed_by_user_id <> close_requested_by_user_id)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_case_number ON case_files (tenant_id, case_number)")
    op.execute("CREATE INDEX idx_case_status ON case_files (tenant_id, status, opened_at DESC)")

    op.execute("""
        CREATE TABLE case_investigators (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id            UUID NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            added_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            added_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at         TIMESTAMPTZ,
            removed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_caseinv_on ON case_investigators (case_id, user_id) WHERE removed_at IS NULL")

    op.execute("""
        CREATE TABLE case_tasks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id             UUID NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            title               VARCHAR(200) NOT NULL,
            detail              TEXT,
            assigned_to_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            due_at              TIMESTAMPTZ,
            state               VARCHAR(10) NOT NULL DEFAULT 'OPEN',
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            done_at             TIMESTAMPTZ,
            done_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
            done_note           TEXT,
            dropped_reason      TEXT,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_casetask_state   CHECK (state IN ('OPEN', 'DONE', 'DROPPED')),
            CONSTRAINT ck_casetask_title   CHECK (btrim(title) <> ''),
            CONSTRAINT ck_casetask_done    CHECK ((state = 'DONE') = (done_at IS NOT NULL)),
            -- A task that is dropped says why.
            CONSTRAINT ck_casetask_dropped CHECK (state <> 'DROPPED' OR (
                dropped_reason IS NOT NULL AND btrim(dropped_reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_casetask_case ON case_tasks (case_id, state)")

    op.execute("""
        CREATE TABLE case_entries (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id       UUID NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            kind          VARCHAR(24) NOT NULL,
            -- A note in somebody's own words, or the reason given for a step.
            body          TEXT,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_caseentry_kind CHECK (kind IN (
                'OPENED', 'NOTE', 'LEAD_SET', 'INVESTIGATOR_ADDED', 'INVESTIGATOR_REMOVED', 'CLOSE_REQUESTED',
                'CLOSE_APPROVED', 'CLOSE_DECLINED', 'REOPENED')),
            -- A note, a declining and a reopening are words: they are not empty.
            CONSTRAINT ck_caseentry_body CHECK (kind NOT IN ('NOTE', 'CLOSE_DECLINED', 'REOPENED') OR (
                body IS NOT NULL AND btrim(body) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_caseentry_case ON case_entries (case_id, occurred_at)")

    op.execute("""
        CREATE TABLE case_links (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id            UUID NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            kind               VARCHAR(20) NOT NULL,
            -- By reference: the record stays where it is, under its own permission.
            ref_id             UUID NOT NULL,
            note               TEXT,
            linked_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            linked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at         TIMESTAMPTZ,
            removed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            remove_reason      TEXT,
            CONSTRAINT ck_caselink_kind    CHECK (kind IN ('INCIDENT', 'INVESTIGATION', 'EVIDENCE_PACKAGE')),
            CONSTRAINT ck_caselink_removed CHECK (removed_at IS NULL OR (
                remove_reason IS NOT NULL AND btrim(remove_reason) <> ''))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_caselink_on ON case_links (case_id, kind, ref_id) WHERE removed_at IS NULL")
    op.execute("CREATE INDEX idx_caselink_ref ON case_links (tenant_id, kind, ref_id)")

    op.execute("""
        CREATE TABLE case_parties (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id            UUID NOT NULL REFERENCES case_files(id) ON DELETE CASCADE,
            kind               VARCHAR(10) NOT NULL,
            -- A person's name, or a vehicle's plate, as whoever added it wrote it.
            label              VARCHAR(200) NOT NULL,
            connection         VARCHAR(20) NOT NULL,
            note               TEXT,
            added_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            added_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at         TIMESTAMPTZ,
            removed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            remove_reason      TEXT,
            CONSTRAINT ck_caseparty_kind       CHECK (kind IN ('PERSON', 'VEHICLE')),
            CONSTRAINT ck_caseparty_connection CHECK (connection IN ('REPORTED_IT', 'WITNESS', 'AFFECTED', 'NAMED', 'OTHER')),
            CONSTRAINT ck_caseparty_label      CHECK (btrim(label) <> ''),
            CONSTRAINT ck_caseparty_removed    CHECK (removed_at IS NULL OR (
                remove_reason IS NOT NULL AND btrim(remove_reason) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_caseparty_case ON case_parties (case_id)")

    # A closed case is not changed, but for being reopened.
    op.execute("""
        CREATE FUNCTION case_file_closed() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'CLOSED' AND NEW.status <> 'OPEN' AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'a closed case is not changed; reopen it first';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER case_file_closed BEFORE UPDATE ON case_files "
               "FOR EACH ROW EXECUTE FUNCTION case_file_closed()")
    # Nothing is added to a closed case, and nothing of it is changed.
    op.execute("""
        CREATE FUNCTION case_part_of_closed() RETURNS trigger AS $$
        BEGIN
            IF pg_trigger_depth() = 1
               AND EXISTS (SELECT 1 FROM case_files c WHERE c.id = NEW.case_id AND c.status = 'CLOSED') THEN
                RAISE EXCEPTION 'the case is closed; reopen it to add to it';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    for table in ("case_investigators", "case_tasks", "case_links", "case_parties"):
        op.execute(f"CREATE TRIGGER {table}_closed BEFORE INSERT OR UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION case_part_of_closed()")
    # Its history takes the steps of closing and reopening, which are written with the case; a note is not one.
    op.execute("""
        CREATE FUNCTION case_note_of_closed() RETURNS trigger AS $$
        BEGIN
            IF pg_trigger_depth() = 1 AND NEW.kind = 'NOTE'
               AND EXISTS (SELECT 1 FROM case_files c WHERE c.id = NEW.case_id AND c.status = 'CLOSED') THEN
                RAISE EXCEPTION 'the case is closed; reopen it to add to it';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER case_entries_closed BEFORE INSERT ON case_entries "
               "FOR EACH ROW EXECUTE FUNCTION case_note_of_closed()")

    for table in TABLES:
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
        if table in CHANGES:
            op.execute(f"GRANT UPDATE ({', '.join(CHANGES[table])}) ON {table} TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Read by whoever reads investigations (2, 3, 4, 6, 8). Worked by the people who would be put on one.
    # Assigned and closed by Admin, Manager and Supervisor.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES
                  (2, 'case:read'), (2, 'case:work'), (2, 'case:manage'),
                  (8, 'case:read'), (8, 'case:work'), (8, 'case:manage'),
                  (3, 'case:read'), (3, 'case:work'), (3, 'case:manage'),
                  (4, 'case:read'), (4, 'case:work'),
                  (6, 'case:read')
               ) AS r(role_id, code) ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('case:read', 'case:work', 'case:manage'))
    """)
    op.execute("DELETE FROM permissions WHERE code IN ('case:read', 'case:work', 'case:manage')")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS case_part_of_closed()")
    op.execute("DROP FUNCTION IF EXISTS case_note_of_closed()")
    op.execute("DROP FUNCTION IF EXISTS case_file_closed()")
