"""P5-I (part 2): IP allowlist management.

If a tenant has ≥1 active allowlist rule, requests from IPs that don't match
any listed CIDR are rejected 403. No active rules = open access.

Tests cover:
- POST /ip-allowlist: add valid CIDR, add bare IPv4 host (→ /32), add bare IPv6 (→ /128)
- POST /ip-allowlist: invalid CIDR → 422
- POST /ip-allowlist: duplicate CIDR for same tenant → 409
- GET  /ip-allowlist: lists rules, includes created_by_email
- DELETE /ip-allowlist/{id}: removes rule, subsequent list excludes it
- DELETE /ip-allowlist/{id}: non-existent → 404
- PATCH /ip-allowlist/{id}/toggle: flips is_active; idempotent second toggle restores
- Auth required (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _add_rule(
    client: AsyncClient,
    cidr: str = "10.0.0.0/24",
    description: str | None = None,
) -> dict:
    body: dict = {"cidr": cidr}
    if description:
        body["description"] = description
    r = await client.post("/api/v1/ip-allowlist", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── Create ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_valid_cidr(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={"cidr": "192.168.1.0/24"})
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["cidr"] == "192.168.1.0/24"
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_add_bare_ipv4_normalised_to_slash32(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={"cidr": "10.20.30.40"})
    assert r.status_code == 200
    assert r.json()["cidr"] == "10.20.30.40/32"


@pytest.mark.asyncio
async def test_add_bare_ipv6_normalised_to_slash128(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={
        "cidr": "2001:db8::1"
    })
    assert r.status_code == 200
    assert r.json()["cidr"] == "2001:db8::1/128"


@pytest.mark.asyncio
async def test_add_invalid_cidr_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={"cidr": "not-an-ip"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_out_of_range_cidr_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={"cidr": "999.0.0.1/32"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_duplicate_cidr_409(auth_client: AsyncClient):
    await _add_rule(auth_client, cidr="172.16.0.0/12")
    r = await auth_client.post("/api/v1/ip-allowlist", json={"cidr": "172.16.0.0/12"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_add_rule_with_description(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/ip-allowlist", json={
        "cidr": "10.99.0.0/16",
        "description": "Office VPN range",
    })
    assert r.status_code == 200
    assert r.json()["description"] == "Office VPN range"


# ── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rules_includes_added_rule(auth_client: AsyncClient):
    rule = await _add_rule(auth_client, "203.0.113.0/24")

    r = await auth_client.get("/api/v1/ip-allowlist")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert rule["id"] in ids


@pytest.mark.asyncio
async def test_list_rules_has_created_by_email(auth_client: AsyncClient):
    await _add_rule(auth_client, "198.51.100.0/24")

    r = await auth_client.get("/api/v1/ip-allowlist")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0
    assert "created_by_email" in rows[0]


# ── Delete ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_rule(auth_client: AsyncClient):
    rule = await _add_rule(auth_client, "10.0.10.0/24")
    r = await auth_client.delete(f"/api/v1/ip-allowlist/{rule['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_deleted_rule_not_in_list(auth_client: AsyncClient):
    rule = await _add_rule(auth_client, "10.0.11.0/24")
    await auth_client.delete(f"/api/v1/ip-allowlist/{rule['id']}")

    r = await auth_client.get("/api/v1/ip-allowlist")
    ids = [row["id"] for row in r.json()]
    assert rule["id"] not in ids


@pytest.mark.asyncio
async def test_delete_nonexistent_rule_404(auth_client: AsyncClient):
    r = await auth_client.delete("/api/v1/ip-allowlist/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


# ── Toggle ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_disables_active_rule(auth_client: AsyncClient):
    rule = await _add_rule(auth_client, "10.0.20.0/24")
    assert rule["is_active"] is True

    r = await auth_client.patch(f"/api/v1/ip-allowlist/{rule['id']}/toggle")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_toggle_twice_restores_active(auth_client: AsyncClient):
    rule = await _add_rule(auth_client, "10.0.21.0/24")

    await auth_client.patch(f"/api/v1/ip-allowlist/{rule['id']}/toggle")
    r = await auth_client.patch(f"/api/v1/ip-allowlist/{rule['id']}/toggle")
    assert r.status_code == 200
    assert r.json()["is_active"] is True


@pytest.mark.asyncio
async def test_toggle_nonexistent_404(auth_client: AsyncClient):
    r = await auth_client.patch(
        "/api/v1/ip-allowlist/00000000-0000-0000-0000-000000000099/toggle"
    )
    assert r.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rules_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/ip-allowlist")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_add_rule_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/ip-allowlist", json={"cidr": "10.0.0.0/8"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_rule_requires_auth(client: AsyncClient):
    r = await client.delete("/api/v1/ip-allowlist/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_toggle_rule_requires_auth(client: AsyncClient):
    r = await client.patch(
        "/api/v1/ip-allowlist/00000000-0000-0000-0000-000000000001/toggle"
    )
    assert r.status_code == 401
