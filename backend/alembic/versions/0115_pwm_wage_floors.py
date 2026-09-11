"""Give users.pwm_grade something to check pay against.

Migration 0094 added users.pwm_grade, constrained it to the seven real
Progressive Wage Model grades, and said why:

    users.pwm_grade   the Progressive Wage Model sets a minimum basic wage per
                      grade; without the grade there is nothing to check pay
                      against

The column has been referenced nowhere outside that migration since. It is a
stored field with no behaviour. This is the other half.

TWO TABLES, because the law and a company's own policy are different things.

pwm_wage_floors is PLATFORM-OWNED: no tenant_id, no RLS, like `permissions`.
PWM is national law and identical for every customer, so sharing it means a new
tenant is protected on day one with nothing to seed, and an annual MOM increase
is one INSERT that every tenant picks up at once with no per-customer drift. It
also means a tenant cannot lower its own legal floor. No SECURITY DEFINER
function is needed to read it — worth saying out loud in this codebase — because
it is not tenant data and there is nothing to scope.

tenant_pwm_floors is per-tenant and RLS-protected, because many agencies pay
above the statutory minimum under a collective agreement or their own policy and
want THAT enforced rather than the legal bare minimum. A tenant floor may only
ever be HIGHER. That is enforced by a trigger rather than only in the API: a
direct UPDATE must not be able to certify an illegal wage as compliant.

NO effective_to COLUMN. An end date is derivable from the next row's
effective_from, and storing both invites them to disagree — silently.

THE FLOOR RESOLVES AS OF THE PAYROLL PERIOD, NEVER AS OF TODAY. Restating March
in September must judge March's payslips against March's floor, or a lawful
March payslip becomes retroactively non-compliant the moment rates rise in
January.

NO RATES ARE SEEDED HERE, DELIBERATELY. The figures belong to the published
MOM/PLRD schedule and are recorded with a source citation when they are loaded.
A wrong floor is worse than no floor: it gives false assurance while still
underpaying. With no rate on file the resolver returns NULL, which callers must
render as "not assessed" — never as compliant.

Revision ID: 0115
Revises: 0114
"""
from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None

GRADES = (
    "security_officer",
    "senior_security_officer",
    "security_supervisor",
    "senior_security_supervisor",
    "security_site_supervisor",
    "senior_security_site_supervisor",
    "chief_security_officer",
)


def upgrade() -> None:
    grade_list = ", ".join(f"'{g}'" for g in GRADES)

    # ── The law ──────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS pwm_wage_floors (
            grade               VARCHAR(40)  NOT NULL,
            effective_from      DATE         NOT NULL,
            monthly_basic_floor NUMERIC(10,2) NOT NULL
                CHECK (monthly_basic_floor > 0),
            source              TEXT         NOT NULL,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (grade, effective_from),
            CONSTRAINT ck_pwm_wage_floors_grade CHECK (grade IN ({grade_list}))
        )
    """)
    op.execute("""
        COMMENT ON TABLE pwm_wage_floors IS
            'Statutory Progressive Wage Model minimum basic wage per security '
            'grade. National law, shared by every tenant, deliberately not '
            'tenant-scoped: a new tenant is protected with nothing to seed, and '
            'no tenant can lower its own legal floor.'
    """)
    op.execute("""
        COMMENT ON COLUMN pwm_wage_floors.source IS
            'Which published MOM/PLRD schedule this figure came from. When '
            'somebody asks in two years why a guard was flagged, the answer '
            'names a document rather than "the system said so".'
    """)

    # ── A company's own, higher, bar ──────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS tenant_pwm_floors (
            tenant_id           UUID         NOT NULL
                                REFERENCES tenants(id) ON DELETE CASCADE,
            grade               VARCHAR(40)  NOT NULL,
            effective_from      DATE         NOT NULL,
            monthly_basic_floor NUMERIC(10,2) NOT NULL
                CHECK (monthly_basic_floor > 0),
            note                TEXT,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, grade, effective_from),
            CONSTRAINT ck_tenant_pwm_floors_grade CHECK (grade IN ({grade_list}))
        )
    """)
    op.execute("ALTER TABLE tenant_pwm_floors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_pwm_floors FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_tenant_pwm_floors ON tenant_pwm_floors
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # A tenant may raise its own bar. It may not lower the law.
    #
    # A CHECK constraint cannot reach another table, so this is a trigger. It
    # lives in the database rather than only in the API because the failure it
    # prevents — a floor quietly set below statutory — would make the system
    # certify an illegal wage as compliant, which is worse than not checking at
    # all.
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_tenant_pwm_floor_at_least_statutory()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            statutory NUMERIC(10,2);
        BEGIN
            SELECT monthly_basic_floor INTO statutory
              FROM pwm_wage_floors
             WHERE grade = NEW.grade
               AND effective_from <= NEW.effective_from
             ORDER BY effective_from DESC
             LIMIT 1;

            IF statutory IS NOT NULL AND NEW.monthly_basic_floor < statutory THEN
                RAISE EXCEPTION
                    'A tenant PWM floor may not be below the statutory floor: '
                    '% is less than % for grade % effective %',
                    NEW.monthly_basic_floor, statutory, NEW.grade,
                    NEW.effective_from;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_tenant_pwm_floor_at_least_statutory
            BEFORE INSERT OR UPDATE ON tenant_pwm_floors
            FOR EACH ROW EXECUTE FUNCTION enforce_tenant_pwm_floor_at_least_statutory()
    """)

    # ── Resolution ───────────────────────────────────────────────────────────
    #
    # Returns NULL when no statutory rate is on file for that grade and period.
    # NULL means "not assessable" and callers must render it that way. It must
    # never be coalesced to zero: a floor of zero makes every wage compliant,
    # which is the precise failure this feature exists to prevent.
    #
    # Not SECURITY DEFINER. tenant_pwm_floors is read under the caller's own
    # RLS, so a tenant can only ever see its own override.
    op.execute("""
        CREATE OR REPLACE FUNCTION pwm_effective_floor(
            p_grade VARCHAR, p_period_start DATE
        ) RETURNS NUMERIC
        LANGUAGE sql
        STABLE
        SET search_path = public, pg_temp
        AS $$
            WITH statutory AS (
                SELECT monthly_basic_floor AS amount
                  FROM pwm_wage_floors
                 WHERE grade = p_grade AND effective_from <= p_period_start
                 ORDER BY effective_from DESC
                 LIMIT 1
            ), override AS (
                SELECT monthly_basic_floor AS amount
                  FROM tenant_pwm_floors
                 WHERE grade = p_grade AND effective_from <= p_period_start
                 ORDER BY effective_from DESC
                 LIMIT 1
            )
            SELECT CASE
                WHEN (SELECT amount FROM statutory) IS NULL THEN NULL
                ELSE GREATEST(
                    (SELECT amount FROM statutory),
                    COALESCE((SELECT amount FROM override),
                             (SELECT amount FROM statutory))
                )
            END
        $$;
    """)

    # SELECT only, and deliberately so. pwm_wage_floors has no RLS, so a write
    # grant would mean any svc_app connection could edit the statutory floor and
    # the only thing standing between a customer and their own legal minimum
    # would be an API permission check. Statutory rates are loaded by migration
    # or by ops with a source citation. If the platform console is ever given a
    # rate editor, widening this grant is the deliberate decision that enables
    # it — not an incidental one.
    op.execute("GRANT SELECT ON pwm_wage_floors TO svc_app")
    op.execute("GRANT ALL ON tenant_pwm_floors TO svc_app")
    op.execute("GRANT EXECUTE ON FUNCTION pwm_effective_floor(VARCHAR, DATE) TO svc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS pwm_effective_floor(VARCHAR, DATE)")
    op.execute("DROP TRIGGER IF EXISTS trg_tenant_pwm_floor_at_least_statutory "
               "ON tenant_pwm_floors")
    op.execute("DROP FUNCTION IF EXISTS enforce_tenant_pwm_floor_at_least_statutory()")
    op.execute("DROP TABLE IF EXISTS tenant_pwm_floors")
    op.execute("DROP TABLE IF EXISTS pwm_wage_floors")
