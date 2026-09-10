"""What a customer is charged, and why.

Licensing already exists and works: tenant_module_licenses says whether a
module is on, up to how many cameras, until when, and the AI workers enforce
it. What it never said is what any of that COSTS. So the vendor could switch
LPR on for a customer and had nowhere to record that LPR is a hundred a month.
§12's point exactly: extend licensing into a commercial system rather than
leave it a technical one.

THE CATALOGUE. billing_modules is the price list — one row per sellable module,
with how it is charged. The code column matches tenant_module_licenses.
module_type where the module is an AI one, so the thing being licensed and the
thing being billed are the same thing rather than two lists that drift apart.
Not every sellable module is an AI module (mobile access, the Windows command
centre, API access) which is why this table exists rather than a price column
bolted onto the licence.

HOW A MODULE IS CHARGED matters as much as what it costs:

    included     part of the plan, billed at zero
    flat         a fixed sum per cycle, whatever the customer's size
    per_camera   scales with what they actually run
    per_site
    per_user

§13 wants those combinable, so the plan carries unit rates too, and a bill is

    base + per-unit overage + module charges

with the allowances the plan includes subtracted before anything is charged per
unit. A customer on a plan including 50 cameras running 60 pays for 10.

PRICES ARE NUMERIC(10,2), never floats. Money in binary floating point is how
an invoice ends up a cent out and a customer stops trusting the whole bill.

WHAT IS NOT DECIDED HERE. Nothing is billed by this migration; it is the price
list and the plan shape. Producing an invoice from it is the next one, so this
can be reviewed on its own terms.

Revision ID: 0109
Revises: 0108
"""
from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None

#: The modules Seventh AI sells, from §12. Codes match
#: tenant_module_licenses.module_type where one exists, so the licence and the
#: price refer to the same thing.
SEED_MODULES = [
    # (code, name, billing_type, unit_price, sort)
    ("vms",             "Video Management",       "included",   0,   10),
    ("lpr",             "AI LPR",                 "per_camera", 5,   20),
    ("face",            "Face Recognition",       "per_camera", 8,   30),
    ("intrusion",       "Intrusion Detection",    "per_camera", 4,   40),
    ("fire_smoke",      "Fire / Smoke Detection", "per_camera", 6,   50),
    ("ppe",             "PPE Detection",          "per_camera", 4,   60),
    ("crowd",           "Crowd Detection",        "per_camera", 4,   70),
    ("weapon",          "Weapon Detection",       "per_camera", 9,   80),
    ("behavior",        "Behaviour Analytics",    "per_camera", 6,   90),
    ("guard",           "Guard Management",       "per_user",   3,  100),
    ("patrol",          "Patrol Management",      "per_site",  15,  110),
    ("visitor",         "Visitor Management",     "per_site",  12,  120),
    ("access",          "Access Control",         "per_site",  20,  130),
    ("incident",        "Incident Management",    "included",   0,  140),
    ("reports",         "Reports",                "included",   0,  150),
    ("mobile",          "Mobile Application",     "per_user",   2,  160),
    ("windows",         "Windows Command Centre", "flat",     150,  170),
    ("api",             "API Access",             "flat",     100,  180),
    ("analytics",       "Advanced Analytics",     "flat",     200,  190),
]


def upgrade() -> None:
    # ── §12 The catalogue ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_modules (
            code         VARCHAR(50) PRIMARY KEY,
            name         VARCHAR(120)  NOT NULL,
            description  TEXT,
            billing_type VARCHAR(20)   NOT NULL DEFAULT 'flat',
            unit_price   NUMERIC(10,2) NOT NULL DEFAULT 0,
            is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
            sort_order   INTEGER       NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_billing_module_type CHECK (
                billing_type IN ('included', 'flat', 'per_camera', 'per_site', 'per_user')
            ),
            CONSTRAINT ck_billing_module_price CHECK (unit_price >= 0)
        )
    """)
    op.execute("""
        COMMENT ON TABLE billing_modules IS
            'The price list. code matches tenant_module_licenses.module_type '
            'for AI modules, so the thing licensed and the thing billed are '
            'one thing rather than two lists that drift.'
    """)

    for code, name, billing_type, price, sort in SEED_MODULES:
        op.execute(f"""
            INSERT INTO billing_modules (code, name, billing_type, unit_price, sort_order)
            VALUES ('{code}', '{name}', '{billing_type}', {price}, {sort})
            ON CONFLICT (code) DO NOTHING
        """)

    # ── §11, §13 The plan shape ──────────────────────────────────────────────
    #
    # billing_plans already carries name, price_monthly, price_yearly and the
    # max_* ceilings. What it could not express is a bill that scales: a plan
    # with an allowance and a rate for whatever exceeds it. These add that
    # without disturbing what Stripe already reads.
    op.execute("""
        ALTER TABLE billing_plans
            ADD COLUMN IF NOT EXISTS billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
            ADD COLUMN IF NOT EXISTS included_cameras INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS included_sites INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS included_users INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS included_storage_gb INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS price_per_camera NUMERIC(10,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS price_per_site NUMERIC(10,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS price_per_user NUMERIC(10,2) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS price_per_gb NUMERIC(10,4) NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS support_level VARCHAR(30) NOT NULL DEFAULT 'standard',
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE
    """)
    op.execute("""
        ALTER TABLE billing_plans
            ADD CONSTRAINT ck_billing_plan_cycle CHECK (
                billing_cycle IN ('monthly', 'quarterly', 'half_yearly', 'yearly', 'custom')
            )
    """)
    op.execute("""
        COMMENT ON COLUMN billing_plans.included_cameras IS
            'Allowance. Charged at price_per_camera only above this, so a '
            'customer on a 50-camera plan running 60 pays for 10.'
    """)

    # ── Which modules a plan carries, and at what price ──────────────────────
    #
    # An override rather than a duplicate: price_override NULL means "whatever
    # the catalogue says", so changing a module's list price moves every plan
    # that has not deliberately negotiated something else.
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_plan_modules (
            plan_id        UUID        NOT NULL REFERENCES billing_plans(id) ON DELETE CASCADE,
            module_code    VARCHAR(50) NOT NULL REFERENCES billing_modules(code) ON DELETE CASCADE,
            is_included    BOOLEAN     NOT NULL DEFAULT TRUE,
            price_override NUMERIC(10,2),
            PRIMARY KEY (plan_id, module_code),
            CONSTRAINT ck_plan_module_price CHECK (
                price_override IS NULL OR price_override >= 0
            )
        )
    """)
    op.execute("""
        COMMENT ON COLUMN billing_plan_modules.price_override IS
            'NULL means the catalogue price. Set only where a plan has '
            'deliberately negotiated something else, so a list-price change '
            'moves every plan that has not.'
    """)

    # ── What a tenant is actually charged for a module ───────────────────────
    #
    # tenant_module_licenses says a module is ON. This says what it costs THIS
    # customer, which is not always the list price — the difference between the
    # two is where discounting lives, and it belongs on a row somebody can
    # point at rather than in a spreadsheet.
    op.execute("""
        ALTER TABLE tenant_module_licenses
            ADD COLUMN IF NOT EXISTS price_override NUMERIC(10,2),
            ADD COLUMN IF NOT EXISTS billing_notes TEXT
    """)
    op.execute("""
        COMMENT ON COLUMN tenant_module_licenses.price_override IS
            'What this customer pays for this module, where it differs from '
            'the plan or catalogue. NULL means no special arrangement.'
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_billing_modules_active
            ON billing_modules (is_active, sort_order)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS billing_plan_modules")
    op.execute("DROP TABLE IF EXISTS billing_modules")
    op.execute("""
        ALTER TABLE tenant_module_licenses
            DROP COLUMN IF EXISTS price_override,
            DROP COLUMN IF EXISTS billing_notes
    """)
    op.execute("ALTER TABLE billing_plans DROP CONSTRAINT IF EXISTS ck_billing_plan_cycle")
    op.execute("""
        ALTER TABLE billing_plans
            DROP COLUMN IF EXISTS billing_cycle,
            DROP COLUMN IF EXISTS included_cameras,
            DROP COLUMN IF EXISTS included_sites,
            DROP COLUMN IF EXISTS included_users,
            DROP COLUMN IF EXISTS included_storage_gb,
            DROP COLUMN IF EXISTS price_per_camera,
            DROP COLUMN IF EXISTS price_per_site,
            DROP COLUMN IF EXISTS price_per_user,
            DROP COLUMN IF EXISTS price_per_gb,
            DROP COLUMN IF EXISTS support_level,
            DROP COLUMN IF EXISTS is_active
    """)
