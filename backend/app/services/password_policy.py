"""What counts as a password here.

There was no policy at all: hash_password accepted anything a caller passed,
including one character. §28 asks for a strong one, and the useful version of
that is not a longer list of character classes — it is length, plus refusing
the passwords that actually get guessed.

WHY LENGTH FIRST. Every hour of research since NIST SP 800-63B says the same
thing: length beats composition. "P@ssw0rd!" satisfies four character classes
and is on every cracking list; "the cat sat on the alarm panel" satisfies one
and is enormously stronger. So the floor is twelve characters, composition is
encouraged rather than demanded, and the obvious guesses are refused by name.

WHAT IS DELIBERATELY NOT HERE. Forced rotation, which the same guidance
recommends against — it produces Password1, Password2, Password3. And a
maximum length, which only ever exists because somebody stored the password
somewhere it had to fit; bcrypt takes 72 bytes and that is the only ceiling.

THE PLATFORM OWNER IS HELD HIGHER. That account can open a support session into
any customer, so its floor is longer. A single standard would either be too
weak for the vendor or unreasonable for a guard signing in on a phone in the
rain.
"""
from __future__ import annotations

import re

#: Everyone. Twelve is the shortest length that is not trivially brute-forced
#: offline, and short enough that a guard will accept it.
MIN_LENGTH = 12

#: The platform owner, who can reach every customer's tenant.
MIN_LENGTH_PLATFORM = 16

PLATFORM_ADMIN_ROLE_ID = 1

#: Refused outright. Not a serious blocklist — that belongs in a downloaded
#: corpus — but these are what people actually type when told to pick
#: something, and rejecting them by name teaches more than a rule does.
_COMMON = {
    "password", "passw0rd", "p@ssword", "p@ssw0rd", "letmein", "welcome",
    "admin", "administrator", "qwerty", "abc123", "iloveyou", "monkey",
    "dragon", "football", "sunshine", "princess", "changeme", "secret",
    "seventh", "seventhai", "vision", "security", "guard", "demo1234",
}

_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop")


def _looks_sequential(password: str) -> bool:
    """Four or more characters straight off a keyboard row or the alphabet."""
    lowered = password.lower()
    for seq in _SEQUENCES:
        for i in range(len(seq) - 3):
            if seq[i:i + 4] in lowered:
                return True
            if seq[i:i + 4][::-1] in lowered:
                return True
    return False


def problems(password: str, *, role_id: int | None = None,
             email: str | None = None) -> list[str]:
    """Everything wrong with it, so the user can fix it in one go.

    A form that reports one failure at a time makes somebody guess four times
    to satisfy four rules, and what they land on is usually worse than what
    they started with.
    """
    found: list[str] = []
    minimum = (MIN_LENGTH_PLATFORM if role_id == PLATFORM_ADMIN_ROLE_ID
               else MIN_LENGTH)

    if len(password) < minimum:
        found.append(
            f"Use at least {minimum} characters"
            + (" — this account can reach every tenant"
               if minimum == MIN_LENGTH_PLATFORM else "")
        )

    lowered = password.lower()
    if any(word in lowered for word in _COMMON):
        found.append("Avoid common words like 'password', 'admin' or the product name")

    if _looks_sequential(password):
        found.append("Avoid runs of keys like 'qwer' or '1234'")

    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 3 and local in lowered:
            found.append("Do not use your email address in your password")

    # Encouraged, not demanded: a passphrase of five ordinary words is stronger
    # than anything this rule would produce, and refusing it would be wrong.
    if len(password) < 20 and not re.search(r"[^A-Za-z]", password):
        found.append("Add a number, symbol or space — or make it longer")

    return found


def validate(password: str, *, role_id: int | None = None,
             email: str | None = None) -> None:
    """Raise ValueError listing everything wrong, or return quietly."""
    found = problems(password, role_id=role_id, email=email)
    if found:
        raise ValueError("; ".join(found))
