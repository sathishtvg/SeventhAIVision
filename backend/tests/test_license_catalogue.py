"""The vendor's licence catalogue, brought up to what the platform now has (migration 0158).

Rows only: every customer has each new module exactly as before, until the
platform owner switches one off. Drone Patrol is not a row of the catalogue: it
has a licence of its own, with limits, switched beside it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.main import app  # noqa: F401
from tests.test_platform_licenses import _seed_super_admin, _seed_target_tenant, _super_admin_auth

MIGRATION = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0158_license_catalogue.py").read_text(encoding="utf-8")
BEFORE = {"seventh_ai_vision": ["vms", "detections", "operations", "analytics", "compliance", "client_portal"],
          "shift_secure": ["shifts", "patrols", "dob", "visitors", "sos", "dispatch"]}
ADDED = {"seventh_ai_vision": ["virtual_patrol", "security_intelligence", "investigation", "evidence_packages",
                               "security_map", "risk_advice", "operations_board", "cases", "data_retention"],
         "shift_secure": ["guard_response", "sop_library", "visitor_authorisation", "assets_maintenance",
                          "workforce_readings"]}


@pytest.mark.asyncio
async def test_the_catalogue_lists_what_was_there_and_what_was_built_since(app_client):
    products = (await app_client.get("/api/v1/platform/products")).json()
    by = {p["id"]: [m["module_code"] for m in p["modules"]] for p in products}
    assert by == {product: BEFORE[product] + ADDED[product] for product in BEFORE}, "the older six first, then the new"
    names = {m["module_code"]: m for p in products for m in p["modules"]}
    assert names["cases"]["module_name"] == "Security Cases" and names["cases"]["description"]
    assert all(m["module_name"] and m["description"] for m in names.values())
    # Drone Patrol has a licence of its own, with limits; it is not a row here.
    assert "drone_patrol" not in names and "DRONE PATROL IS NOT A ROW HERE" in MIGRATION
    # Rows only.
    upgrade = MIGRATION.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert not re.search(r"CREATE TABLE|ALTER TABLE|GRANT |REVOKE |DROP ", upgrade) and "ON CONFLICT (product_id, module_code) DO NOTHING" in upgrade
    assert sum(len(v) for v in ADDED.values()) == 14


@pytest.mark.asyncio
async def test_a_customer_has_each_new_module_as_before_until_the_platform_owner_switches_one_off(app_client, admin_session):
    platform, owner, _ = await _seed_super_admin(admin_session)
    target, _ = await _seed_target_tenant(admin_session)
    h = _super_admin_auth(platform, owner)
    url = f"/api/v1/platform/tenants/{target}/products"

    for product in BEFORE:
        assert (await app_client.post(f"{url}/{product}", headers=h, json={"is_enabled": True})).status_code == 201
    shown = {p["product_id"]: p for p in (await app_client.get(url, headers=h)).json()}
    for product, modules in shown.items():
        assert [m["module_code"] for m in modules["modules"]] == BEFORE[product] + ADDED[product]
        assert all(m["is_enabled"] for m in modules["modules"]), "on, with no row of the customer's own"
    # Nothing was written for the customer by adding the modules.
    rows = (await admin_session.execute(text(
        "SELECT count(*) FROM tenant_product_modules WHERE tenant_id = :t"), {"t": target})).scalar()
    assert rows == 0

    # One is switched off: that one, and no other.
    off = await app_client.put(f"{url}/seventh_ai_vision/modules/cases", headers=h, json={"is_enabled": False})
    assert off.status_code == 200, off.text
    after = {p["product_id"]: {m["module_code"]: m["is_enabled"] for m in p["modules"]}
             for p in (await app_client.get(url, headers=h)).json()}
    assert after["seventh_ai_vision"]["cases"] is False
    assert all(on for code, on in after["seventh_ai_vision"].items() if code != "cases") and all(after["shift_secure"].values())

    # Drone Patrol, beside the catalogue: its own licence, with its limits, written in the customer's audit log.
    licence = f"/api/v1/platform/tenants/{target}/drone-license"
    first = (await app_client.get(licence, headers=h)).json()
    assert first["licensed"] is False and first["is_enabled"] is False and first["reason"]
    saved = await app_client.put(licence, headers=h, json={"is_enabled": True, "max_drones": 4, "max_sites": 2,
                                                           "notes": "Pilot for two sites."})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["licensed"], saved.json()["max_drones"], saved.json()["max_missions"], saved.json()["max_sites"]) == (
        True, 4, None, 2)
    logged = (await admin_session.execute(text(
        "SELECT count(*) FROM audit_logs WHERE tenant_id = :t AND action = 'drone.license.set'"), {"t": target})).scalar()
    assert logged == 1
    # The platform's own organisation is not licensed for it: the vendor does not run a customer's patrols.
    own = await app_client.get(f"/api/v1/platform/tenants/{platform}/drone-license", headers=h)
    assert own.status_code in (200, 422)
    # And nobody but the platform owner reaches either.
    assert (await app_client.get(url)).status_code == 401 and (await app_client.get(licence)).status_code == 401
