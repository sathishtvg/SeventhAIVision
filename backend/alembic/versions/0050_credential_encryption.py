"""0050 — Credential Encryption at Rest

Enable pgcrypto extension. Application-layer credential encryption is in effect
from this revision onward: nvr_connections.password_enc and streams.auth_config
now store Fernet-encrypted tokens (AES-128-CBC + HMAC-SHA256) instead of
plaintext. Existing dev rows should be recreated after applying this migration.

The pgcrypto extension is enabled here for potential future DB-layer encryption
and to serve as the formal audit record that the encryption scheme is active.
"""
from __future__ import annotations

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    # pgcrypto left in place — dropping it could affect gen_random_uuid() callers.
    pass
