"""0060 — Employee profile fields + employee_documents (ShiftSecure Phase 1)

Adds the Singapore-compliance employee fields ShiftSecure's spec requires
(NRIC/FIN, work pass, employment details, bank details) directly onto
`users` — nullable, filled in incrementally via PUT, same pattern as every
other optional profile field already on this table. Also adds
employee_documents (passport/work-pass/certification uploads with expiry)
as a new tenant-scoped child table.

Not built here (later phases): CPF-eligibility logic derived from
work_pass_type, and check-in blocking on expired documents — this
migration only makes the data model + storage queryable.

Revision ID: 0060
Revises: 0059
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
          ADD COLUMN nric_fin VARCHAR(20),
          ADD COLUMN date_of_birth DATE,
          ADD COLUMN nationality VARCHAR(100),
          ADD COLUMN phone VARCHAR(30),
          ADD COLUMN address TEXT,
          ADD COLUMN work_pass_type VARCHAR(20)
              CHECK (work_pass_type IN ('citizen','pr','ep','sp','wp')),
          ADD COLUMN work_pass_expiry DATE,
          ADD COLUMN employment_type VARCHAR(20)
              CHECK (employment_type IN ('full_time','part_time','contract')),
          ADD COLUMN designation VARCHAR(100),
          ADD COLUMN department VARCHAR(100),
          ADD COLUMN date_joined DATE,
          ADD COLUMN bank_name VARCHAR(100),
          ADD COLUMN bank_account_number VARCHAR(50),
          ADD COLUMN emergency_contact_name VARCHAR(255),
          ADD COLUMN emergency_contact_phone VARCHAR(30)
    """)

    op.execute("""
        CREATE TABLE employee_documents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            document_type       VARCHAR(30) NOT NULL
                CHECK (document_type IN ('passport','work_pass','certification','other')),
            document_number     VARCHAR(100),
            issuing_body        VARCHAR(255),
            issue_date          DATE,
            expiry_date         DATE,
            storage_path        VARCHAR(500),
            notes               TEXT,
            created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_employee_documents_tenant_user ON employee_documents(tenant_id, user_id)")
    op.execute("CREATE INDEX idx_employee_documents_expiry ON employee_documents(expiry_date) WHERE expiry_date IS NOT NULL")

    # Standard RLS triplet — same pattern as every tenant-scoped table.
    op.execute("ALTER TABLE employee_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE employee_documents FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_employee_documents ON employee_documents
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS employee_documents")
    op.execute("""
        ALTER TABLE users
          DROP COLUMN IF EXISTS nric_fin,
          DROP COLUMN IF EXISTS date_of_birth,
          DROP COLUMN IF EXISTS nationality,
          DROP COLUMN IF EXISTS phone,
          DROP COLUMN IF EXISTS address,
          DROP COLUMN IF EXISTS work_pass_type,
          DROP COLUMN IF EXISTS work_pass_expiry,
          DROP COLUMN IF EXISTS employment_type,
          DROP COLUMN IF EXISTS designation,
          DROP COLUMN IF EXISTS department,
          DROP COLUMN IF EXISTS date_joined,
          DROP COLUMN IF EXISTS bank_name,
          DROP COLUMN IF EXISTS bank_account_number,
          DROP COLUMN IF EXISTS emergency_contact_name,
          DROP COLUMN IF EXISTS emergency_contact_phone
    """)
