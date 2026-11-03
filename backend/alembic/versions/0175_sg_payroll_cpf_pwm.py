"""Singapore payroll: CPF rates by date, PR graduated rates, Additional Wages, the official PWM floors.

Design: docs/plans/2026-11-03-known-limits-programme-and-sg-payroll-design.md, agreed with the owner on
3 November 2026 (Phase 1 of closing the feature reference's known limits).

* cpf_rate_tables, cpf_wage_ceilings: CPF Board's published contribution rates and wage ceilings, by the date they
  take effect. Platform-owned like pwm_wage_floors (no tenant_id, read-only to the application): the law is the
  same for every organisation. Loaded from CPF's own documents, which are named in `source`:
    - CPF Contribution Rate Table from 1 January to 31 December 2025 (jan2025_contributionandallocationrates.pdf);
    - CPF Contribution Rate Table from 1 January 2026 (CPFcontributionratesfrom1Jan2026.pdf).
  Each published table is one `scheme`: `full` (Table 1: citizens, PRs from the 3rd year, and PRs in years 1-2 on
  full rates after a joint application), `graduated_1`/`graduated_2` (Tables 2 and 3, G/G) and `fg_1`/`fg_2`
  (Tables 4 and 5, F/G). Every row of every table is an employer rate and an employee rate for an age band; the
  wage-band formulas follow from them (services/cpf.py), and the tests hold them to CPF's "Max." figures.
  2027 is NOT loaded: CPF has announced new totals for ages 55-65 but not yet the employer/employee split.
* pwm_wage_floors gets MOM's outsourced full-time monthly basic floors, 2024-2028. That table was made for exactly
  these figures and left empty on purpose until they could be loaded with their source (migration 0115).
* pwm_wage_schedules: the rest of MOM's schedule - outsourced part-time hourly basic, and in-house full-time monthly
  gross. In-house part-time is pro-rated from the full-time figure on a 44-hour week, as MOM says (services/pwm.py).
* users: pr_since, cpf_rate_type (graduated | fg | full), employment_arrangement (outsourced | in_house).
* payroll_additional_wages: bonus-type pay (CPF's Additional Wages) as lines on a draft run.
* payslips: what the CPF was worked out on and from.

Revision ID: 0175
Revises: 0174
"""
from alembic import op

revision = "0175"
down_revision = "0174"
branch_labels = None
depends_on = None

CPF_2025 = "CPF Board, CPF Contribution Rate Table from 1 January to 31 December 2025 (jan2025_contributionandallocationrates.pdf)"
CPF_2026 = "CPF Board, CPF Contribution Rate Table from 1 January 2026 (CPFcontributionratesfrom1Jan2026.pdf)"
AW_SOURCE = "CPF Board, What is the Additional Wage (AW) ceiling? - $102,000 less the total Ordinary Wages subject to CPF for the year"
MOM_PWM = ("MOM, Progressive Wage Model for the security sector "
           "(mom.gov.sg/employment-practices/progressive-wage-model/security-sector), read 3 November 2026")

# (employer, employee) per age band, as published. Age band = the age the employee is ABOVE (0 = 55 and below).
# The graduated (G/G) tables have four bands: "Above 65" is one.
CPF_RATES = {
    "2025-01-01": {
        "full":        {0: (17, 20), 55: (15.5, 17), 60: (12, 11.5), 65: (9, 7.5), 70: (7.5, 5)},
        "graduated_1": {0: (4, 5), 55: (4, 5), 60: (3.5, 5), 65: (3.5, 5)},
        "graduated_2": {0: (9, 15), 55: (6, 12.5), 60: (3.5, 7.5), 65: (3.5, 5)},
        "fg_1":        {0: (17, 5), 55: (15.5, 5), 60: (12, 5), 65: (9, 5), 70: (7.5, 5)},
        "fg_2":        {0: (17, 15), 55: (15.5, 12.5), 60: (12, 7.5), 65: (9, 5), 70: (7.5, 5)},
    },
    "2026-01-01": {
        "full":        {0: (17, 20), 55: (16, 18), 60: (12.5, 12.5), 65: (9, 7.5), 70: (7.5, 5)},
        "graduated_1": {0: (4, 5), 55: (4, 5), 60: (3.5, 5), 65: (3.5, 5)},
        "graduated_2": {0: (9, 15), 55: (6, 12.5), 60: (3.5, 7.5), 65: (3.5, 5)},
        "fg_1":        {0: (17, 5), 55: (16, 5), 60: (12.5, 5), 65: (9, 5), 70: (7.5, 5)},
        "fg_2":        {0: (17, 15), 55: (16, 12.5), 60: (12.5, 7.5), 65: (9, 5), 70: (7.5, 5)},
    },
}
CPF_CEILINGS = {"2025-01-01": (7400, CPF_2025), "2026-01-01": (8000, CPF_2026)}
SCHEMES = ("full", "graduated_1", "graduated_2", "fg_1", "fg_2")

# MOM security PWM, 2024-2028.
YEARS = (2024, 2025, 2026, 2027, 2028)
OUTSOURCED_FULL_TIME = {   # monthly basic wage, 35-44 hours a week
    "senior_security_supervisor": (3550, 3770, 3990, 4210, 4430),
    "security_supervisor":        (3250, 3470, 3690, 3910, 4130),
    "senior_security_officer":    (2950, 3170, 3390, 3610, 3830),
    "security_officer":           (2650, 2870, 3090, 3310, 3530),
}
OUTSOURCED_PART_TIME = {   # hourly basic wage
    "senior_security_supervisor": ("13.55", "14.40", "15.25", "16.05", "16.90"),
    "security_supervisor":        ("12.40", "13.25", "14.10", "14.90", "15.75"),
    "senior_security_officer":    ("11.25", "12.10", "12.95", "13.80", "14.60"),
    "security_officer":           ("10.10", "10.95", "11.80", "12.65", "13.45"),
}
IN_HOUSE_FULL_TIME = {     # monthly gross wage without overtime; senior security supervisor is left to the market
    "security_supervisor":     (2605, 2745, 2905, 3065, 3225),
    "senior_security_officer": (2375, 2515, 2675, 2835, 2995),
    "security_officer":        (2175, 2315, 2475, 2635, 2795),
}
GRADES = (
    "security_officer", "senior_security_officer", "security_supervisor", "senior_security_supervisor",
    "security_site_supervisor", "senior_security_site_supervisor", "chief_security_officer",
)
AW_KINDS = ("bonus", "aws", "leave_encashment", "incentive", "other")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # ── CPF ──────────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE cpf_rate_tables (
            effective_from DATE         NOT NULL,
            scheme         VARCHAR(20)  NOT NULL CHECK (scheme IN ({_in(SCHEMES)})),
            age_above      SMALLINT     NOT NULL CHECK (age_above IN (0, 55, 60, 65, 70)),
            employer_rate  NUMERIC(6,4) NOT NULL CHECK (employer_rate >= 0 AND employer_rate < 1),
            employee_rate  NUMERIC(6,4) NOT NULL CHECK (employee_rate >= 0 AND employee_rate < 1),
            source         TEXT         NOT NULL,
            PRIMARY KEY (effective_from, scheme, age_above)
        )
    """)
    op.execute("""
        COMMENT ON TABLE cpf_rate_tables IS
            'CPF Board''s published contribution rates by the date they take effect. Platform-owned: the same law for '
            'every organisation. Each row is one age band of one published table; the wage bands follow from the two '
            'rates (services/cpf.py).'
    """)
    op.execute("""
        CREATE TABLE cpf_wage_ceilings (
            effective_from       DATE          PRIMARY KEY,
            ow_ceiling_monthly   NUMERIC(10,2) NOT NULL CHECK (ow_ceiling_monthly > 0),
            annual_wage_ceiling  NUMERIC(12,2) NOT NULL CHECK (annual_wage_ceiling > 0),
            source               TEXT          NOT NULL
        )
    """)
    rows = []
    for effective, schemes in CPF_RATES.items():
        source = CPF_2025 if effective.startswith("2025") else CPF_2026
        for scheme, bands in schemes.items():
            for age_above, (er, ee) in bands.items():
                rows.append(f"('{effective}', '{scheme}', {age_above}, {er / 100}, {ee / 100}, '{source}')")
    op.execute("INSERT INTO cpf_rate_tables (effective_from, scheme, age_above, employer_rate, employee_rate, source) "
               "VALUES " + ", ".join(rows))
    op.execute("INSERT INTO cpf_wage_ceilings (effective_from, ow_ceiling_monthly, annual_wage_ceiling, source) VALUES "
               + ", ".join(f"('{d}', {ow}, 102000, '{src}; {AW_SOURCE}')" for d, (ow, src) in CPF_CEILINGS.items()))
    for table in ("cpf_rate_tables", "cpf_wage_ceilings"):
        op.execute(f"REVOKE ALL ON {table} FROM svc_app")
        op.execute(f"GRANT SELECT ON {table} TO svc_app")

    # ── PWM ──────────────────────────────────────────────────────────────────
    values = []
    for grade, amounts in OUTSOURCED_FULL_TIME.items():
        for year, amount in zip(YEARS, amounts):
            values.append(f"('{grade}', '{year}-01-01', {amount}, "
                          f"'{MOM_PWM}: outsourced full-time, monthly basic wage, {year}')")
    op.execute("INSERT INTO pwm_wage_floors (grade, effective_from, monthly_basic_floor, source) VALUES "
               + ", ".join(values) + " ON CONFLICT (grade, effective_from) DO NOTHING")

    op.execute(f"""
        CREATE TABLE pwm_wage_schedules (
            grade          VARCHAR(40)   NOT NULL CHECK (grade IN ({_in(GRADES)})),
            arrangement    VARCHAR(12)   NOT NULL CHECK (arrangement IN ('outsourced', 'in_house')),
            hours          VARCHAR(10)   NOT NULL CHECK (hours IN ('full_time', 'part_time')),
            basis          VARCHAR(14)   NOT NULL CHECK (basis IN ('hourly_basic', 'monthly_gross')),
            effective_from DATE          NOT NULL,
            amount         NUMERIC(10,2) NOT NULL CHECK (amount > 0),
            source         TEXT          NOT NULL,
            PRIMARY KEY (grade, arrangement, hours, effective_from)
        )
    """)
    op.execute("""
        COMMENT ON TABLE pwm_wage_schedules IS
            'MOM''s PWM floors other than outsourced full-time monthly basic (which is pwm_wage_floors): outsourced '
            'part-time hourly basic, in-house full-time monthly gross. Platform-owned, read-only to the application.'
    """)
    values = []
    for grade, amounts in OUTSOURCED_PART_TIME.items():
        for year, amount in zip(YEARS, amounts):
            values.append(f"('{grade}', 'outsourced', 'part_time', 'hourly_basic', '{year}-01-01', {amount}, "
                          f"'{MOM_PWM}: outsourced part-time, hourly basic wage, {year}')")
    for grade, amounts in IN_HOUSE_FULL_TIME.items():
        for year, amount in zip(YEARS, amounts):
            values.append(f"('{grade}', 'in_house', 'full_time', 'monthly_gross', '{year}-01-01', {amount}, "
                          f"'{MOM_PWM}: in-house full-time, monthly gross wage excluding overtime, {year}')")
    op.execute("INSERT INTO pwm_wage_schedules (grade, arrangement, hours, basis, effective_from, amount, source) "
               "VALUES " + ", ".join(values))
    op.execute("REVOKE ALL ON pwm_wage_schedules FROM svc_app")
    op.execute("GRANT SELECT ON pwm_wage_schedules TO svc_app")

    # ── The employee ─────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE users
          ADD COLUMN pr_since DATE,
          ADD COLUMN cpf_rate_type VARCHAR(10) CHECK (cpf_rate_type IN ('graduated', 'fg', 'full')),
          ADD COLUMN employment_arrangement VARCHAR(12) NOT NULL DEFAULT 'outsourced'
              CHECK (employment_arrangement IN ('outsourced', 'in_house'))
    """)
    op.execute("COMMENT ON COLUMN users.pr_since IS 'The date this person became a Singapore PR: decides their CPF "
               "year (graduated rates in years 1 and 2).'")
    op.execute("COMMENT ON COLUMN users.cpf_rate_type IS 'For a PR in years 1-2: graduated (the default, also when "
               "empty), fg (full employer, graduated employee) or full - the last two only after CPF Board approves a "
               "joint application.'")

    # ── Additional Wages on a run ────────────────────────────────────────────
    op.execute(f"""
        CREATE TABLE payroll_additional_wages (
            id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            payroll_run_id     UUID          NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
            guard_user_id      UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind               VARCHAR(20)   NOT NULL CHECK (kind IN ({_in(AW_KINDS)})),
            amount             NUMERIC(12,2) NOT NULL CHECK (amount > 0),
            note               TEXT,
            created_by_user_id UUID          REFERENCES users(id) ON DELETE SET NULL,
            created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_payroll_additional_wages_run ON payroll_additional_wages (payroll_run_id, guard_user_id)")
    op.execute("ALTER TABLE payroll_additional_wages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_additional_wages FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_payroll_additional_wages ON payroll_additional_wages
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON payroll_additional_wages FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payroll_additional_wages TO svc_app")

    # ── What a payslip's CPF was worked out on ───────────────────────────────
    op.execute("""
        ALTER TABLE payslips
          ADD COLUMN ordinary_wage      NUMERIC(12,2),
          ADD COLUMN additional_wage    NUMERIC(12,2) NOT NULL DEFAULT 0,
          ADD COLUMN ow_subject_to_cpf  NUMERIC(12,2),
          ADD COLUMN aw_subject_to_cpf  NUMERIC(12,2),
          ADD COLUMN aw_ceiling_left    NUMERIC(12,2),
          ADD COLUMN cpf_scheme         VARCHAR(20),
          ADD COLUMN cpf_age_above      SMALLINT,
          ADD COLUMN cpf_rates_from     DATE,
          ADD COLUMN cpf_note           TEXT
    """)
    op.execute("COMMENT ON COLUMN payslips.cpf_note IS 'Why CPF was not worked out (no date of birth, no PR date, no "
               "rate table for the month), or NULL when it was.'")
    op.execute("ALTER TABLE payroll_runs ADD COLUMN recalculated_at TIMESTAMPTZ, "
               "ADD COLUMN additional_wages_changed_at TIMESTAMPTZ")
    op.execute("COMMENT ON COLUMN payroll_runs.additional_wages_changed_at IS 'When an Additional Wage line of this "
               "run was last added, changed or removed: later than the payslips means they need recalculating.'")


def downgrade() -> None:
    op.execute("ALTER TABLE payroll_runs DROP COLUMN recalculated_at, DROP COLUMN additional_wages_changed_at")
    op.execute("""
        ALTER TABLE payslips
          DROP COLUMN ordinary_wage, DROP COLUMN additional_wage, DROP COLUMN ow_subject_to_cpf,
          DROP COLUMN aw_subject_to_cpf, DROP COLUMN aw_ceiling_left, DROP COLUMN cpf_scheme,
          DROP COLUMN cpf_age_above, DROP COLUMN cpf_rates_from, DROP COLUMN cpf_note
    """)
    op.execute("DROP TABLE payroll_additional_wages")
    op.execute("ALTER TABLE users DROP COLUMN employment_arrangement, DROP COLUMN cpf_rate_type, DROP COLUMN pr_since")
    op.execute("DROP TABLE pwm_wage_schedules")
    op.execute(f"DELETE FROM pwm_wage_floors WHERE grade IN ({_in(OUTSOURCED_FULL_TIME)}) "
               f"AND effective_from IN ({_in(f'{y}-01-01' for y in YEARS)}) AND source LIKE 'MOM, Progressive Wage Model%'")
    op.execute("DROP TABLE cpf_wage_ceilings")
    op.execute("DROP TABLE cpf_rate_tables")
