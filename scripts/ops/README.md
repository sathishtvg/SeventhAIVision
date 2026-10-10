# Operator scripts

Run inside the API container, which already has the app's modules, bcrypt and a
database URL:

    docker exec -it docker-api-1 python /app/backend/../scripts/ops/reset_password.py <email>

If the repo is not mounted into the container, copy it in first:

    docker cp scripts/ops/reset_password.py docker-api-1:/tmp/reset_password.py
    docker exec -it docker-api-1 python /tmp/reset_password.py <email>

## reset_password.py

Sets any account's password. Prompts twice (silently), enforces the same
`app.services.password_policy` the API enforces, writes the hash with a bound
parameter, and verifies the stored hash by round-trip **before** committing --
so a bad write rolls back instead of locking the account out.

### Why this exists rather than a psql one-liner

A bcrypt hash starts `$2b$12$`. Pasted into a **double-quoted** shell string --
`psql -c "UPDATE users SET hashed_password='$2b$12$...'"` -- bash expands `$2b`
and `$12` as positional parameters, both empty. The prefix is eaten before psql
sees it and a truncated remnant is stored. That happened on 2026-09-10 and
locked the platform owner out of the product.

It is worse than it sounds: `bcrypt.checkpw` raises `ValueError` on a malformed
hash, which used to surface as a 500 that the login page rendered as "Invalid
credentials" -- so a corrupt row looked exactly like a typo. (`verify_password`
now returns False and logs it; see `backend/app/core/security.py`.)

Never move a hash through a shell argument. This script never does.

### Detecting the corruption

    SELECT email FROM users
     WHERE NOT (length(hashed_password) = 60
                AND strpos(hashed_password, '$2') = 1);

A bcrypt hash is always exactly 60 characters. Anything else cannot authenticate.

## demo_showcase.py

Loads a demonstration's worth of sample data into the `demo` organisation: one
connected story at Jurong Logistics Hub across the response desk, the security
map, the occurrence book, investigations, evidence packages, the SOP library
and visitor authorisations. `docs/DEMO-SCRIPT.md` is the presenter's route
through it.

It works through the running application, signed in as the demo accounts, so
what it makes is numbered and audited as a person's work would be. It only
adds, and it can be run again at any time. It is read from standard input, so
nothing is copied into the container:

    docker exec -i -e DEMO_STEP=today docker-api-1 python - < scripts/ops/demo_showcase.py

`DEMO_STEP` is `setup` (what lasts), `today` (what goes stale: run it an hour
before a demonstration) or `live` (an unanswered incident and a situation
awaiting a decision: run it five minutes before). With none it runs `setup`,
then `today`.

The demo accounts' password is read from `backend/scripts/seed_demo.py` and is
never printed. Do not run `seed_demo.py` itself: it deletes the organisation.
