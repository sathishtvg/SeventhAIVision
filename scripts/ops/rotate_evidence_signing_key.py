"""Replace the key the API signs evidence with (Phase 7 of the known limits; EVIDENCE_CHAIN_OF_CUSTODY.md).

    # see what would happen, touch nothing
    docker cp scripts/ops/rotate_evidence_signing_key.py docker-api-1:/tmp/
    docker exec -it docker-api-1 python /tmp/rotate_evidence_signing_key.py

    # do it
    docker exec -it docker-api-1 python /tmp/rotate_evidence_signing_key.py --commit

What it does, only when the key is the API's own file (EVIDENCE_SIGNING_KEY not given): renames the key file to
`evidence_ed25519.retired-<UTC time>.pem` beside it - kept, readable by its owner alone, never deleted - and makes a
new one. The API reads the new key at its next signing, records its public half, and marks the old one retired from
then: exports signed with the old key still check, because its public half stays on record.

When the key is given as a secret, this refuses: replace the secret, then restart the API.

It never deletes a private key. Removing a retired one is a person's decision, made by hand.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")


def main(argv: list[str]) -> int:
    from app.services import evidence_signature_check as checker
    from app.services import evidence_signing as signing

    commit = "--commit" in argv
    if os.environ.get(signing.ENV, "").strip():
        print(f"The key is given as a secret ({signing.ENV}). Replace the secret and restart the API instead.")
        return 2
    path = Path(signing.KEY_FILE)
    if not path.exists():
        print(f"There is no key at {path} yet. The API makes one the first time it signs; nothing to replace.")
        return 0
    signer, why = signing.current(path)
    if signer is None:
        print(f"The key at {path} cannot be read ({why}). Look at it before replacing it; nothing was changed.")
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kept = path.with_name(f"{path.stem}.retired-{stamp}{path.suffix}")
    print(f"In use:  {checker.spaced(signer.fingerprint)}")
    print(f"It would be kept as {kept} and a new key made at {path}.")
    if not commit:
        print("Nothing changed. Run again with --commit to do it.")
        return 0
    os.replace(path, kept)
    os.chmod(kept, 0o600)
    signing.make_key_file(path)
    signing.forget()
    made, _ = signing.current(path)
    print(f"New key: {checker.spaced(made.fingerprint)}")
    print("The API records it and retires the old one at its next signing. Give the new fingerprint to whoever "
          "checks your exports.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
