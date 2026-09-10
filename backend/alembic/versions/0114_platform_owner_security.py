"""The account that can reach every customer needs more than a password.

2FA already exists and works — TOTP enrolment, a challenge at login, the lot.
What it has never been is REQUIRED. So the one account that can open a support
session into any tenant, change every customer's price and read the whole
ledger has been protected by a password alone, at the vendor's option, which
is the same as saying at nobody's.

IT ALSO GRANTS 2fa:manage TO SUPER ADMIN, which it did not have. Without that
GET /2fa/setup answers 403 and the gate below is a hard lockout — the owner is
told to enrol and handed a door they cannot open. The grant and the gate are in
one migration so they can never be applied apart.

WHAT THIS DOES NOT DO IS LOCK ANYBODY OUT. Refusing the login of a Super Admin
without TOTP would be correct and unusable: they cannot enrol without signing
in. So the login still succeeds and the PERMISSIONS are withheld — an
unenrolled platform owner can reach their own account and the 2FA setup pages
and nothing else. The way out is visible from where they are standing.

    security.require_mfa_for_platform_owner   default true

Off is available for a first-run installation where nobody has enrolled yet,
and turning it off is a decision with a name attached: platform_settings
records updated_by.

NEW-LOCATION DETECTION, not "impossible travel". refresh_tokens already records
last_ip per session, so "this platform owner has never signed in from this
address before" is a query rather than a new subsystem. It raises a
notification rather than blocking, because a vendor travelling is far more
likely than a vendor compromised, and a security control that stops the owner
working gets switched off within a week.

PASSWORD POLICY lives in code (app/services/password_policy.py) rather than
here, because it is logic rather than data — but the floor for the platform
owner is longer than for everybody else, and that asymmetry is the same
reasoning as above.

Revision ID: 0114
Revises: 0113
"""
from alembic import op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None

SECURITY_SETTINGS = [
    ("security.require_mfa_for_platform_owner", "true",
     "Withhold platform permissions from a Super Admin who has not enrolled in "
     "2FA. They can still sign in and enrol — this does not lock anybody out."),
    ("security.alert_on_new_platform_login_ip", "true",
     "Raise a notification when a platform owner signs in from an address they "
     "have not used before"),
]


def upgrade() -> None:
    # ── The way out has to exist ─────────────────────────────────────────────
    #
    # Withholding the platform powers until the owner enrols is only safe if
    # they CAN enrol. Migration 0102 cut Super Admin to seven permissions and
    # 2fa:manage was not among them, so GET /2fa/setup answered 403 — the gate
    # below would have been a hard lockout with no way through from inside the
    # product.
    #
    # Granted first, in the same migration, so the two can never be applied
    # apart. 2fa:policy comes with it: deciding whether the vendor's own staff
    # must use 2FA is a platform decision, not a customer's.
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT 1, id FROM permissions WHERE code IN ('2fa:manage', '2fa:policy')
        ON CONFLICT DO NOTHING
    """)

    for key, value, description in SECURITY_SETTINGS:
        escaped = description.replace("'", "''")
        op.execute(f"""
            INSERT INTO platform_settings (key, value, description)
            VALUES ('{key}', '{value}', '{escaped}')
            ON CONFLICT (key) DO NOTHING
        """)

    # Every address a platform owner has signed in from. Small, and the
    # question it answers — "have they been here before" — is asked on every
    # login, so it is a table rather than a scan of refresh_tokens.
    op.execute("""
        CREATE TABLE IF NOT EXISTS platform_login_locations (
            user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ip_address VARCHAR(45) NOT NULL,
            first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
            logins     INTEGER     NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, ip_address)
        )
    """)
    op.execute("""
        COMMENT ON TABLE platform_login_locations IS
            'Addresses a platform owner has signed in from. A new one raises a '
            'notification rather than blocking: a vendor travelling is far more '
            'likely than a vendor compromised, and a control that stops the '
            'owner working gets switched off within a week.'
    """)

    # Seeded from the sessions that already exist, so enabling this does not
    # announce every address the owner has been using for months as new.
    op.execute("""
        INSERT INTO platform_login_locations (user_id, ip_address, first_seen, last_seen)
        SELECT r.user_id, r.last_ip, min(r.created_at), max(r.last_seen_at)
          FROM refresh_tokens r
          JOIN users u ON u.id = r.user_id AND u.role_id = 1
         WHERE r.last_ip IS NOT NULL
      GROUP BY r.user_id, r.last_ip
        ON CONFLICT (user_id, ip_address) DO NOTHING
    """)

    # Reading users across tenants for the MFA check: the platform owner sits
    # in its own tenant and the permission check runs before any tenant scope
    # is settled. Sixth time.
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_user_mfa_state(p_user UUID)
        RETURNS TABLE (role_id SMALLINT, totp_enabled BOOLEAN)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT role_id, COALESCE(totp_enabled, FALSE)
              FROM users WHERE id = p_user
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_record_login_location(
            p_user UUID, p_ip VARCHAR
        ) RETURNS BOOLEAN
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            is_new BOOLEAN;
        BEGIN
            -- Returns whether this address is new, and records it either way,
            -- in one statement: doing it as a SELECT then an INSERT would let
            -- two simultaneous logins both report "new".
            INSERT INTO platform_login_locations (user_id, ip_address)
            VALUES (p_user, p_ip)
            ON CONFLICT (user_id, ip_address) DO UPDATE
               SET last_seen = now(),
                   logins    = platform_login_locations.logins + 1
            RETURNING (logins = 1) INTO is_new;
            RETURN COALESCE(is_new, FALSE);
        END;
        $$;
    """)

    for fn in ("platform_user_mfa_state(UUID)",
               "platform_record_login_location(UUID, VARCHAR)"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO svc_app")


def downgrade() -> None:
    op.execute("""
        DELETE FROM role_permissions
         WHERE role_id = 1
           AND permission_id IN (SELECT id FROM permissions
                                  WHERE code IN ('2fa:manage', '2fa:policy'))
    """)
    op.execute("DROP FUNCTION IF EXISTS platform_record_login_location(UUID, VARCHAR)")
    op.execute("DROP FUNCTION IF EXISTS platform_user_mfa_state(UUID)")
    op.execute("DROP TABLE IF EXISTS platform_login_locations")
    op.execute("""
        DELETE FROM platform_settings
         WHERE key IN ('security.require_mfa_for_platform_owner',
                       'security.alert_on_new_platform_login_ip')
    """)
