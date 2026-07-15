"""Tier 2 Feature 3: Audit log tamper detection.

Adds row_hash and prev_hash columns to audit_logs.
Each new row's HMAC-SHA256 covers all immutable fields + the previous row's
hash (chain). A broken chain means a row was modified or deleted.
Pre-existing rows get NULL (backward-compatible — they are skipped by verify).

Revision ID: 0017
Revises:     0016
Create Date: 2026-06-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alter the parent partitioned table — Postgres cascades to all partitions.
    op.add_column("audit_logs", sa.Column("row_hash", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64), nullable=True))

    # Grant on the new permission code
    op.execute("""
        INSERT INTO permissions (code, description, category)
        VALUES ('audit:verify', 'Verify audit log tamper-detection chain', 'audit')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.id IN (1, 2) AND p.code = 'audit:verify'
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN "
               "(SELECT id FROM permissions WHERE code = 'audit:verify')")
    op.execute("DELETE FROM permissions WHERE code = 'audit:verify'")
    op.drop_column("audit_logs", "prev_hash")
    op.drop_column("audit_logs", "row_hash")
