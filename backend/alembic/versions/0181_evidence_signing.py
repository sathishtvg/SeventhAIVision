"""Evidence digitally signed: the installation's signing keys, and each package's seal and export signatures.

Phase 7 of closing the feature reference's known limits (docs/plans/2026-11-05-evidence-watermark-and-signing-design.md).
Nothing here changes a package, its items, its holds or its chain of custody.

`evidence_signing_keys` - the public half of each key this installation has signed evidence with: its fingerprint
(the SHA-256 of the raw 32-byte Ed25519 public key), the key itself, where it came from, when it was first used, and
when another took its place. The installation's, not an organisation's: one key signs every organisation's evidence,
and anyone checking an export is told which key it was. The private half is never here.

`evidence_package_signatures` - a package's seal signature (one, made when it is sealed) and each export's: the exact
statement that was signed, the signature, and the key - or, when the key could not be read, why it is not signed. It
goes with its package.
"""
from alembic import op

revision = "0181"
down_revision = "0180"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE evidence_signing_keys (
            fingerprint    VARCHAR(64)  PRIMARY KEY CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            public_key_pem TEXT         NOT NULL,
            source         VARCHAR(8)   NOT NULL CHECK (source IN ('SECRET', 'FILE')),
            first_used_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            retired_at     TIMESTAMPTZ
        )
    """)
    # The installation's: no organisation's rows to keep apart. The application records a key and retires the one
    # before it; it never removes one.
    op.execute("REVOKE ALL ON evidence_signing_keys FROM svc_app")
    op.execute("GRANT SELECT, INSERT, UPDATE (retired_at) ON evidence_signing_keys TO svc_app")

    op.execute("""
        CREATE TABLE evidence_package_signatures (
            id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            package_id         UUID         NOT NULL REFERENCES evidence_packages(id) ON DELETE CASCADE,
            kind               VARCHAR(8)   NOT NULL CHECK (kind IN ('SEAL', 'EXPORT')),
            -- Exactly the bytes that were signed, as UTF-8 text.
            statement          TEXT         NOT NULL,
            signature          TEXT,
            key_fingerprint    VARCHAR(64)  REFERENCES evidence_signing_keys(fingerprint),
            not_signed_because TEXT,
            signed_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_evsig_signed_or_why CHECK (
                (signature IS NOT NULL AND key_fingerprint IS NOT NULL AND not_signed_because IS NULL)
                OR (signature IS NULL AND key_fingerprint IS NULL AND not_signed_because IS NOT NULL))
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_evsig_one_seal ON evidence_package_signatures (package_id) WHERE kind = 'SEAL'")
    op.execute("CREATE INDEX idx_evsig_package ON evidence_package_signatures (package_id, signed_at DESC)")
    op.execute("ALTER TABLE evidence_package_signatures ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evidence_package_signatures FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_evidence_package_signatures ON evidence_package_signatures
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
    op.execute("REVOKE ALL ON evidence_package_signatures FROM svc_app")
    op.execute("GRANT SELECT, INSERT ON evidence_package_signatures TO svc_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_package_signatures")
    op.execute("DROP TABLE IF EXISTS evidence_signing_keys")
