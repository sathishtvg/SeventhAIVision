"""Per-protocol hardware configuration (admin-configurable devices).

The point of this feature is that a site's hardware is configured by an
administrator rather than by a migration. Two things therefore have to hold:

  - a protocol's declared fields are ENFORCED, so a typo doesn't become a
    silently-stored value that makes a gate look configured when it isn't;
  - the declaration the admin UI renders forms from is the same declaration
    the API validates against, so the form can never offer a field the server
    will reject.

Sections:
  A — Registry integrity (4 tests)
  B — Validation and coercion (8 tests)
  C — Catalogue shape (3 tests)
"""
from __future__ import annotations

import pytest

from app.services.device_protocols import (
    ConfigError,
    describe_family,
    resolve_spec,
    validate_extra_config,
)
from shared.device_protocols import (
    FAMILIES,
    FAMILY_BARRIER,
    PROTOCOLS,
    get_protocol,
    known_keys,
    protocols_for_family,
)


# ─── A. Registry integrity ───────────────────────────────────────────────────


def test_dp_every_protocol_belongs_to_a_known_family():
    for (family, _key), spec in PROTOCOLS.items():
        assert family in FAMILIES
        assert spec.family == family


def test_dp_field_names_unique_within_a_protocol():
    """A duplicate name would make one of the two silently unreachable."""
    for spec in PROTOCOLS.values():
        names = [f.name for f in spec.fields]
        assert len(names) == len(set(names)), f"{spec.key} has duplicate field names"


def test_dp_select_fields_declare_options():
    """A select with no options renders an empty dropdown — unusable."""
    for spec in PROTOCOLS.values():
        for f in spec.fields:
            if f.type == "select":
                assert f.options, f"{spec.key}.{f.name} is a select with no options"
                if f.default is not None:
                    assert f.default in f.options


def test_dp_secret_fields_are_never_stored_in_jsonb():
    """A secret must map to a column so it goes through encrypt_secret. Landing
    in the config blob would put a device password in plaintext JSONB."""
    for spec in PROTOCOLS.values():
        for f in spec.fields:
            if f.is_secret:
                assert f.column, f"{spec.key}.{f.name} is secret but not column-backed"


# ─── B. Validation and coercion ──────────────────────────────────────────────


def test_dp_unknown_protocol_rejected():
    with pytest.raises(ConfigError, match="unknown protocol"):
        resolve_spec(FAMILY_BARRIER, "not_a_real_protocol")


def test_dp_unknown_family_rejected():
    with pytest.raises(ConfigError, match="unknown device family"):
        resolve_spec("teleporter", "simulator")


def test_dp_defaults_materialise_when_omitted():
    out = validate_extra_config(FAMILY_BARRIER, "relay", None)
    assert out["open_path"] == "/relay/{channel}/on"
    assert out["auth_scheme"] == "none"


def test_dp_unknown_field_rejected_not_ignored():
    """Silently dropping a field an admin believed they set is how a gate ends
    up misconfigured behind a UI that looks correct."""
    with pytest.raises(ConfigError, match="baud_rate"):
        validate_extra_config(FAMILY_BARRIER, "relay", {"baud_rate": 9600})


def test_dp_field_from_another_protocol_rejected():
    with pytest.raises(ConfigError, match="open_path"):
        validate_extra_config(FAMILY_BARRIER, "simulator", {"open_path": "/x"})


def test_dp_select_value_must_be_an_option():
    with pytest.raises(ConfigError, match="one of"):
        validate_extra_config(FAMILY_BARRIER, "relay", {"auth_scheme": "kerberos"})


def test_dp_blank_allowed_on_optional_string():
    """close_path's own help text says to leave it blank when the board only
    supports a momentary pulse — the validator must agree with the help text."""
    out = validate_extra_config(FAMILY_BARRIER, "relay", {"close_path": ""})
    assert out["close_path"] == ""


def test_dp_column_backed_fields_are_not_returned_as_config():
    """host/port live in real columns. If they leaked into the JSONB too the
    row would carry two copies that could disagree."""
    out = validate_extra_config(FAMILY_BARRIER, "relay", {})
    assert "host" not in out
    assert "port" not in out
    assert "password" not in out


# ─── C. Catalogue shape ──────────────────────────────────────────────────────


def test_dp_catalogue_covers_every_registered_protocol():
    described = {p["key"] for p in describe_family(FAMILY_BARRIER)["protocols"]}
    assert described == set(known_keys(FAMILY_BARRIER))


def test_dp_catalogue_is_json_safe():
    """It is served straight to the admin UI, so every value must survive
    serialisation — no tuples, no dataclasses."""
    import json

    for family in FAMILIES:
        json.dumps(describe_family(family))


def test_dp_dahua_reports_it_cannot_close():
    """Several Dahua barrier models expose no remote close. The UI must be able
    to hide the button rather than offer an action that always fails."""
    spec = get_protocol(FAMILY_BARRIER, "dahua")
    assert spec is not None
    assert spec.supports_close is False
    assert all(
        p.supports_close for p in protocols_for_family(FAMILY_BARRIER) if p.key != "dahua"
    )
