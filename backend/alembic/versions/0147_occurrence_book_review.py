"""The occurrence book: review, correction by a further entry, instructions, and a shift's summary.

Additive: five new tables and one permission. No existing table, policy or
permission is altered (LATEST_ENTERPRISE_FEATURE_GAP_ANALYSIS.md §5, phase 5).

  an ENTRY, as it has always      ─►  REVIEWED by a supervisor: noted, or to be followed up
  been written (unchanged)        ─►  CORRECTED by a further entry — never edited

  INSTRUCTIONS in force at a site ─►  carried into every shift's summary until closed

  a SHIFT'S SUMMARY: drafted from what was recorded, by fixed sentences —
  read and corrected by a person, then confirmed, and only then handed over

AN ENTRY IS STILL NEVER EDITED. A mistake is put right by writing another entry
and recording which entry it corrects and why. Both stay in the book.

A REVIEW AND A CORRECTION ARE ADDED TO AND NEVER REWRITTEN: the application's
role takes SELECT and INSERT on them and nothing else.

A SUMMARY IS DRAFTED BY THE PLATFORM AND CONFIRMED BY A PERSON. `drafted_text`
is what the platform wrote and is kept as written; `final_text` is what the
person made of it. Once confirmed it cannot be changed: a trigger refuses.
No language model is involved (`method` is `TEMPLATE`, and nothing else is
allowed): every sentence is a count or a quotation of something recorded.

SUPER ADMIN (1) AND CLIENT (7) GET NO PERMISSION HERE.

Revision ID: 0147
Revises: 0146
"""
from alembic import op

revision = "0147"
down_revision = "0146"
branch_labels = None
depends_on = None

OUTCOMES = "'NOTED','FOLLOW_UP','CLOSED'"
STATES = "'DRAFT','CONFIRMED','DISCARDED'"

PERMISSIONS = [
    ("dob:review", "Review occurrence book entries, and issue and close instructions for a site", "guard"),
]

#: What a person changes of a summary. Not the facts, not what the platform
#: drafted, and not which shift it is of.
SUMMARY_CHANGES = "final_text, state, confirmed_by_user_id, confirmed_at, updated_at"
INSTRUCTION_CHANGES = "closed_at, closed_by_user_id, close_note"

#: Tables the application adds to and never rewrites.
ADDED_TO_ONLY = ("occurrence_entry_reviews", "occurrence_entry_corrections", "site_instruction_reads")
TABLES = (*ADDED_TO_ONLY, "site_instructions", "shift_handover_summaries")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE occurrence_entry_reviews (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            entry_id         UUID NOT NULL REFERENCES occurrence_book_entries(id) ON DELETE CASCADE,
            site_id          UUID REFERENCES sites(id) ON DELETE SET NULL,
            reviewer_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            reviewer_role    SMALLINT,
            outcome          VARCHAR(12) NOT NULL,
            note             TEXT,
            reviewed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dobreview_outcome CHECK (outcome IN ({OUTCOMES})),
            -- Something to be followed up says what; closing it says what was done.
            CONSTRAINT ck_dobreview_note CHECK (outcome = 'NOTED' OR (note IS NOT NULL AND btrim(note) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_dobreview_entry ON occurrence_entry_reviews (entry_id, reviewed_at DESC)")

    op.execute("""
        CREATE TABLE occurrence_entry_corrections (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            -- The further entry, which says what is right.
            entry_id           UUID NOT NULL REFERENCES occurrence_book_entries(id) ON DELETE CASCADE,
            -- The entry it corrects, which stays in the book as written.
            corrects_entry_id  UUID NOT NULL REFERENCES occurrence_book_entries(id) ON DELETE CASCADE,
            reason             TEXT NOT NULL,
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dobcorrection_entry UNIQUE (entry_id),
            CONSTRAINT ck_dobcorrection_other CHECK (entry_id <> corrects_entry_id),
            CONSTRAINT ck_dobcorrection_reason CHECK (btrim(reason) <> '')
        )
    """)
    op.execute("CREATE INDEX idx_dobcorrection_corrects ON occurrence_entry_corrections (corrects_entry_id)")

    op.execute("""
        CREATE TABLE site_instructions (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            site_id           UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            body              TEXT NOT NULL,
            issued_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- NULL: in force until somebody closes it.
            expires_at        TIMESTAMPTZ,
            closed_at         TIMESTAMPTZ,
            closed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            close_note        TEXT,
            CONSTRAINT ck_instruction_body   CHECK (btrim(body) <> ''),
            CONSTRAINT ck_instruction_expiry CHECK (expires_at IS NULL OR expires_at > issued_at),
            -- Closing one says why.
            CONSTRAINT ck_instruction_closed CHECK (
                (closed_at IS NULL AND close_note IS NULL)
                OR (closed_at IS NOT NULL AND close_note IS NOT NULL AND btrim(close_note) <> ''))
        )
    """)
    op.execute("CREATE INDEX idx_instruction_in_force ON site_instructions (site_id, issued_at DESC) "
               "WHERE closed_at IS NULL")

    op.execute("""
        CREATE TABLE site_instruction_reads (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            instruction_id UUID NOT NULL REFERENCES site_instructions(id) ON DELETE CASCADE,
            user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            read_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_instruction_read UNIQUE (instruction_id, user_id)
        )
    """)

    op.execute(f"""
        CREATE TABLE shift_handover_summaries (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            shift_id             UUID NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            site_id              UUID REFERENCES sites(id) ON DELETE SET NULL,
            guard_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
            period_start         TIMESTAMPTZ NOT NULL,
            period_end           TIMESTAMPTZ NOT NULL,
            -- What was counted, as it was counted when the summary was drafted.
            facts                JSONB NOT NULL,
            -- What the platform wrote, kept as written.
            drafted_text         TEXT NOT NULL,
            -- What the person made of it. The same text until they change it.
            final_text           TEXT NOT NULL,
            method               VARCHAR(12) NOT NULL DEFAULT 'TEMPLATE',
            state                VARCHAR(10) NOT NULL DEFAULT 'DRAFT',
            drafted_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            drafted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            confirmed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            confirmed_at         TIMESTAMPTZ,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_shiftsummary_state  CHECK (state IN ({STATES})),
            -- Fixed sentences over recorded facts. No language model writes here.
            CONSTRAINT ck_shiftsummary_method CHECK (method = 'TEMPLATE'),
            CONSTRAINT ck_shiftsummary_period CHECK (period_end >= period_start),
            CONSTRAINT ck_shiftsummary_text   CHECK (btrim(final_text) <> ''),
            CONSTRAINT ck_shiftsummary_confirmed CHECK ((state = 'CONFIRMED') = (confirmed_at IS NOT NULL))
        )
    """)
    # One summary of a shift stands at a time; a draft is discarded to draft again.
    op.execute("CREATE UNIQUE INDEX uq_shiftsummary_live ON shift_handover_summaries (shift_id) "
               "WHERE state <> 'DISCARDED'")
    op.execute("CREATE INDEX idx_shiftsummary_site ON shift_handover_summaries (site_id, period_end DESC)")

    # A summary that has been confirmed, or discarded, is not changed. Depth 1
    # is the application's own statement: a foreign key clearing a person who
    # has left runs deeper and is let through.
    op.execute("""
        CREATE FUNCTION shift_summary_settled() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state IN ('CONFIRMED', 'DISCARDED') AND pg_trigger_depth() = 1 THEN
                RAISE EXCEPTION 'a shift summary that is % is not changed', lower(OLD.state)
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER shift_summary_settled BEFORE UPDATE ON shift_handover_summaries
            FOR EACH ROW EXECUTE FUNCTION shift_summary_settled()
    """)

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
    op.execute(f"GRANT UPDATE ({INSTRUCTION_CHANGES}) ON site_instructions TO svc_app")
    op.execute(f"GRANT UPDATE ({SUMMARY_CHANGES}) ON shift_handover_summaries TO svc_app")

    for code, description, category in PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (code, description, category)
            VALUES ('{code}', '{description}', '{category}')
            ON CONFLICT (code) DO NOTHING
        """)
    # Admin (2), Manager (8) and Supervisor (3): the people a book is answered to.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.role_id, p.id
          FROM permissions p
          JOIN (VALUES (2, 'dob:review'), (8, 'dob:review'), (3, 'dob:review')) AS r(role_id, code)
            ON r.code = p.code
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'dob:review')
    """)
    op.execute("DELETE FROM permissions WHERE code = 'dob:review'")
    for table in ("shift_handover_summaries", "site_instruction_reads", "site_instructions",
                  "occurrence_entry_corrections", "occurrence_entry_reviews"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS shift_summary_settled()")
