"""Cross-language check: a key signed by the Node generator must verify
with the Python service the server actually uses.

Run after `node test/roundtrip.js`, which writes the fixture. This is the
one test that would catch an encoding disagreement between the two
implementations — the failure mode that would otherwise only appear once a
key reached a customer.
"""
import json
import sys
from datetime import date

sys.path.insert(0, "/app/backend")

from app.services.licensing import (  # noqa: E402
    LicenceError, check_binding, load_public_key, verify_licence,
)

fixture = json.load(open(sys.argv[1], encoding="utf-8"))
pub = load_public_key(fixture["public_key"])

lic = verify_licence(fixture["server_key"], pub)
assert lic.customer == fixture["expected_customer"], lic.customer
assert lic.type == "server", lic.type
assert lic.modules == ["lpr", "face", "intrusion"], lic.modules
assert lic.bind == "install-abc-123", lic.bind
print(f"  ok  node-signed key verifies in python  ({lic.licence_id})")
print(f"      customer={lic.customer!r} expires={lic.expires} "
      f"days_left={lic.days_remaining(date.today())}")

# Binding
check_binding(lic, "install-abc-123")
print("  ok  binding accepts the matching install id")
try:
    check_binding(lic, "some-other-install")
except LicenceError as exc:
    print(f"  ok  binding rejects a different install  ({str(exc)[:48]}…)")
else:
    raise AssertionError("binding check failed to reject a foreign install id")

# Tamper
prefix, body, sig = fixture["server_key"].split(".")
import base64  # noqa: E402
payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
payload["exp"] = "2099-12-31"
forged_body = base64.urlsafe_b64encode(
    json.dumps(payload).encode()).decode().rstrip("=")
try:
    verify_licence(f"{prefix}.{forged_body}.{sig}", pub)
except LicenceError:
    print("  ok  python rejects an extended-expiry forgery")
else:
    raise AssertionError("forged key verified — signature check is not working")

# A key from someone else's keypair
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402
other = ed25519.Ed25519PrivateKey.generate().public_key()
try:
    verify_licence(fixture["server_key"], other)
except LicenceError:
    print("  ok  python rejects a key signed by a different keypair")
else:
    raise AssertionError("key verified against the wrong public key")

print("\ncross-language check passed")
