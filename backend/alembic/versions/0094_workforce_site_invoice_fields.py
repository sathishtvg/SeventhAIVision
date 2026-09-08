"""The columns the remaining gaps all need before they can be built.

Individually small, and every one of them was blocking something specific:

  users.plrd_licence_no/grade   a Security Officer licence is currently a
                                free-text certification_type among first-aid
                                and CPR, with nothing able to ask "is this
                                person licensed" or "which grade"
  users.pwm_grade               the Progressive Wage Model sets a minimum
                                basic wage per grade; without the grade there
                                is nothing to check pay against
  users.gender                  some sites contractually require a female
                                officer on duty for searches, which cannot be
                                rostered for today
  users.reports_to_user_id      there is no supervisor chain, so escalation
                                and approval routing have nothing to follow
  users.date_left/exit_reason   date_joined exists but there is no leaver
                                concept, so an ex-guard is indistinguishable
                                from a current one
  users.employee_code           the reference an agency actually uses on a
                                payslip and a deployment sheet
  sites.*                       who to call at 3am, where the muster point
                                is, and a postal code, because Singapore
                                operations route by postal code
  invoices.*                    currency, PO number and payment terms — no
                                corporate client pays an invoice without a PO,
                                and an ageing report needs terms

EMPLOYEE CODES ARE BACKFILLED, NOT GENERATED ON WRITE. Numbering by created_at
gives the existing staff stable codes in joining order, which is what somebody
reading an old roster expects. New rows get one from the application, because a
database default cannot know a tenant's numbering scheme and a trigger doing it
invisibly is worse than an explicit assignment.

Nothing here is NOT NULL. Every column describes something an agency may or may
not record, and forcing a value would mean inventing one for every row that
already exists.

Revision ID: 0094
Revises: 0093
"""
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── People ───────────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS employee_code        VARCHAR(20),
            ADD COLUMN IF NOT EXISTS gender               VARCHAR(20),
            ADD COLUMN IF NOT EXISTS plrd_licence_no      VARCHAR(50),
            ADD COLUMN IF NOT EXISTS plrd_licence_expiry  DATE,
            ADD COLUMN IF NOT EXISTS pwm_grade            VARCHAR(40),
            ADD COLUMN IF NOT EXISTS reports_to_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS date_left            DATE,
            ADD COLUMN IF NOT EXISTS exit_reason          VARCHAR(120),
            ADD COLUMN IF NOT EXISTS driving_licence_class VARCHAR(20),
            ADD COLUMN IF NOT EXISTS uniform_size         VARCHAR(20)
    """)

    # Free text would drift into "M", "male", "Male" and "MALE" within a month,
    # and the roster rule this exists for — a site requiring a female officer
    # on duty for searches — cannot match four spellings.
    op.execute("""
        ALTER TABLE users ADD CONSTRAINT ck_users_gender
            CHECK (gender IS NULL OR gender IN ('male', 'female', 'other'))
    """)

    # The PWM grade ladder for the security industry, in seniority order. A
    # CHECK rather than a lookup table: these are set by regulation, not by the
    # tenant, and a tenant-editable list would let somebody invent a grade with
    # no wage floor behind it.
    op.execute("""
        ALTER TABLE users ADD CONSTRAINT ck_users_pwm_grade
            CHECK (pwm_grade IS NULL OR pwm_grade IN (
                'security_officer',
                'senior_security_officer',
                'security_supervisor',
                'senior_security_supervisor',
                'security_site_supervisor',
                'senior_security_site_supervisor',
                'chief_security_officer'
            ))
    """)

    # Numbered per tenant in joining order, so existing staff get the codes
    # somebody reading an old roster would expect.
    op.execute("""
        WITH numbered AS (
            SELECT id, tenant_id,
                   ROW_NUMBER() OVER (PARTITION BY tenant_id
                                      ORDER BY created_at, id) AS n
              FROM users
        )
        UPDATE users u
           SET employee_code = 'EMP' || LPAD(numbered.n::text, 4, '0')
          FROM numbered
         WHERE numbered.id = u.id AND u.employee_code IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_users_employee_code
            ON users (tenant_id, employee_code)
         WHERE employee_code IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX idx_users_reports_to
            ON users (tenant_id, reports_to_user_id)
         WHERE reports_to_user_id IS NOT NULL
    """)
    # Partial: the only question ever asked of it is "who is still here".
    op.execute("""
        CREATE INDEX idx_users_current_staff
            ON users (tenant_id) WHERE date_left IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_users_plrd_expiry
            ON users (tenant_id, plrd_licence_expiry)
         WHERE plrd_licence_expiry IS NOT NULL
    """)

    # ── Sites ────────────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE sites
            ADD COLUMN IF NOT EXISTS postal_code         VARCHAR(20),
            ADD COLUMN IF NOT EXISTS site_contact_name   VARCHAR(255),
            ADD COLUMN IF NOT EXISTS site_contact_phone  VARCHAR(30),
            ADD COLUMN IF NOT EXISTS operating_hours     VARCHAR(120),
            ADD COLUMN IF NOT EXISTS muster_point        TEXT,
            ADD COLUMN IF NOT EXISTS access_instructions TEXT
    """)

    # ── Invoices ─────────────────────────────────────────────────────────────
    #
    # Currency defaults to SGD rather than being left null: every existing
    # invoice was raised in Singapore dollars, and a null would be read as
    # "unknown" by anything summing them.
    op.execute("""
        ALTER TABLE invoices
            ADD COLUMN IF NOT EXISTS currency       VARCHAR(3) NOT NULL DEFAULT 'SGD',
            ADD COLUMN IF NOT EXISTS po_number      VARCHAR(60),
            ADD COLUMN IF NOT EXISTS payment_terms  VARCHAR(40)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS payment_terms")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS po_number")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS currency")

    for col in ("access_instructions", "muster_point", "operating_hours",
                "site_contact_phone", "site_contact_name", "postal_code"):
        op.execute(f"ALTER TABLE sites DROP COLUMN IF EXISTS {col}")

    op.execute("DROP INDEX IF EXISTS idx_users_plrd_expiry")
    op.execute("DROP INDEX IF EXISTS idx_users_current_staff")
    op.execute("DROP INDEX IF EXISTS idx_users_reports_to")
    op.execute("DROP INDEX IF EXISTS uq_users_employee_code")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_pwm_grade")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_gender")
    for col in ("uniform_size", "driving_licence_class", "exit_reason", "date_left",
                "reports_to_user_id", "pwm_grade", "plrd_licence_expiry",
                "plrd_licence_no", "gender", "employee_code"):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {col}")
