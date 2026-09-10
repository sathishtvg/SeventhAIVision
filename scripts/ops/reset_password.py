"""Set any Seventh AI Vision account's password, safely.

    docker exec -it docker-api-1 python /tmp/reset_password.py <email>

The earlier attempt pasted a bcrypt hash into a double-quoted `psql -c` string.
Bash expanded $2b and $12 as positional parameters, ate the prefix, and stored a
32-character remnant; bcrypt.checkpw then raised ValueError on every login,
which the UI rendered as "Invalid credentials".

Here the password is typed straight into this process, hashed here, and written
with a bound parameter -- no shell, no quoting, nothing echoed, nothing in
history. The stored hash is verified by round-trip BEFORE the transaction
commits, so a bad write rolls back instead of locking the account out again.
"""
from __future__ import annotations

import asyncio, getpass, os, re, sys
import asyncpg, bcrypt

sys.path.insert(0, "/app/backend")
from app.services import password_policy


async def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    email = sys.argv[1].strip().lower()

    raw = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(re.sub(r"^postgresql\+\w+://", "postgresql://", raw))
    try:
        row = await conn.fetchrow(
            "SELECT u.id, u.role_id, u.tenant_id, u.totp_enabled, t.slug "
            "  FROM users u JOIN tenants t ON t.id = u.tenant_id "
            " WHERE lower(u.email) = $1", email)
        if row is None:
            print(f"No account with email {email}")
            return 1
        print(f"Account: {email} | tenant slug: {row['slug']} | role_id: {row['role_id']}"
              f" | 2FA enrolled: {row['totp_enabled']}")

        first = getpass.getpass("New password: ")
        if first != getpass.getpass("Confirm:      "):
            print("They do not match. Nothing was changed.")
            return 1

        # The same policy the API enforces, so this back door cannot set a
        # password the front door would have refused.
        problems = password_policy.problems(first, email=email, role_id=row["role_id"])
        if problems:
            print("That password does not meet the policy for this role:")
            for p in problems:
                print(f"  - {p}")
            print("Nothing was changed.")
            return 1

        hashed = bcrypt.hashpw(first.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        async with conn.transaction():
            # set_config(..., true) is SET LOCAL -- scoped to this transaction.
            await conn.execute("SELECT set_config('app.current_tenant', $1, true)",
                               str(row["tenant_id"]))
            res = await conn.execute(
                "UPDATE users SET hashed_password = $1, failed_login_count = 0, "
                "       locked_until = NULL WHERE id = $2", hashed, row["id"])
            if res.split()[-1] != "1":
                raise SystemExit(f"Expected to update exactly 1 row, got: {res}")
            stored = await conn.fetchval(
                "SELECT hashed_password FROM users WHERE id = $1", row["id"])
            if len(stored) != 60 or not bcrypt.checkpw(first.encode(), stored.encode()):
                raise SystemExit("Stored hash failed verification -- rolled back.")

        print(f"Done. Password set and verified (60-char hash). Lockout cleared.")
        if row["totp_enabled"]:
            print("NOTE: this account has 2FA enrolled -- you will also be asked "
                  "for a 6-digit authenticator code at login.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
