"""Rotate CREDENTIALS_ENCRYPTION_KEY and re-encrypt every stored secret.

    # see what would change, touch nothing
    docker exec -it docker-api-1 python /tmp/rotate_credential_key.py

    # do it
    docker exec -it docker-api-1 python /tmp/rotate_credential_key.py --commit

Fernet keys are not versioned in the ciphertext, so swapping the env var
without re-encrypting makes every stored secret undecryptable -- cameras and
barriers stop authenticating, and the failure looks like bad hardware. This
reads each secret with the CURRENT key, re-encrypts under the NEW one, and
verifies the round trip before committing.

The three places a secret is stored (see app/core/crypto.py callers):
    barriers.password_encrypted
    nvr_connections.password_enc
    streams.auth_config ->> 'password_enc'

ORDER MATTERS. Run this BEFORE changing the env var: it needs the old key to
read. Afterwards put the printed key in docker/.env and restart api,
ingestion and scheduler together -- between re-encryption and restart the
running processes still hold the old key and cannot read the new ciphertext.
"""
from __future__ import annotations

import asyncio, json, os, re, sys
from datetime import datetime, timezone
sys.path.insert(0, "/app/backend")

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import _get_fernet  # current key, however it is configured

COMMIT = "--commit" in sys.argv


async def main() -> int:
    old = _get_fernet()
    new_key = Fernet.generate_key()
    new = Fernet(new_key)

    # Persist the new key BEFORE any row is rewritten.
    #
    # The first version of this script printed the key once to stdout and kept
    # no copy. On 2026-09-10 a --commit run re-encrypted a live credential and
    # the only copy of the key was terminal output. A rotation tool whose
    # output you must catch by hand is a data-loss tool wearing a hat.
    #
    # /data/backups is a mounted volume, so this survives the container.
    key_path = None
    if COMMIT:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for candidate in ("/data/backups", "/tmp"):
            try:
                os.makedirs(candidate, exist_ok=True)
                key_path = os.path.join(candidate, "credential_key_%s.txt" % stamp)
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(new_key.decode() + chr(10))
                break
            except OSError:
                key_path = None
        if key_path is None:
            print("REFUSING TO ROTATE: could not write the new key anywhere.")
            print("Rotating without a durable copy destroys every stored")
            print("secret. Nothing was changed.")
            return 1

    raw = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ["DATABASE_URL"]
    engine = create_async_engine(re.sub(r"^postgresql\+\w+://", "postgresql+asyncpg://", raw))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def recrypt(token: str) -> str:
        plain = old.decrypt(token.encode())          # raises if the key is wrong
        out = new.encrypt(plain)
        assert new.decrypt(out) == plain, "round trip failed"
        return out.decode()

    moved = 0
    try:
        async with factory() as s:
            for row in (await s.execute(text(
                "SELECT id, password_encrypted FROM barriers "
                " WHERE password_encrypted IS NOT NULL"))).mappings().all():
                if COMMIT:
                    await s.execute(text(
                        "UPDATE barriers SET password_encrypted = :v WHERE id = :id"),
                        {"v": recrypt(row["password_encrypted"]), "id": row["id"]})
                else:
                    recrypt(row["password_encrypted"])
                moved += 1

            for row in (await s.execute(text(
                "SELECT id, password_enc FROM nvr_connections "
                " WHERE password_enc IS NOT NULL"))).mappings().all():
                if COMMIT:
                    await s.execute(text(
                        "UPDATE nvr_connections SET password_enc = :v WHERE id = :id"),
                        {"v": recrypt(row["password_enc"]), "id": row["id"]})
                else:
                    recrypt(row["password_enc"])
                moved += 1

            for row in (await s.execute(text(
                "SELECT id, auth_config FROM streams "
                " WHERE auth_config ? 'password_enc'"))).mappings().all():
                cfg = dict(row["auth_config"])
                cfg["password_enc"] = recrypt(cfg["password_enc"])
                if COMMIT:
                    await s.execute(text(
                        "UPDATE streams SET auth_config = CAST(:v AS jsonb) WHERE id = :id"),
                        {"v": json.dumps(cfg), "id": row["id"]})
                moved += 1

            if COMMIT:
                await s.commit()
    finally:
        await engine.dispose()

    if not COMMIT:
        print(f"DRY RUN. {moved} secret(s) decrypt cleanly and would be re-encrypted.")
        print("Nothing was changed. Re-run with --commit to rotate.")
        return 0

    print(f"Re-encrypted {moved} secret(s).")
    print("\nPut this in docker/.env as CREDENTIALS_ENCRYPTION_KEY, then restart")
    print("api, ingestion and scheduler. Until you do, those services hold the")
    print("OLD key and cannot read what was just written.\n")
    print("The new key is saved inside the container at:")
    print("    " + str(key_path))
    print("Read it with:  docker exec docker-api-1 cat " + str(key_path))
    print("Delete that file once the key is in docker/.env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
