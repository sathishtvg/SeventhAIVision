"""P5-I (part 1): API key management — machine-to-machine auth.

Tests cover:
- POST /api-keys: creates a key, returns the full key once only
- Full key uses the sav1_ prefix
- Key hash is stored (list never exposes raw key)
- GET  /api-keys: lists keys with display prefix, no raw key in response
- DELETE /api-keys/{id}: revokes (is_active → False)
- DELETE /api-keys/{id}: non-existent → 404
- DELETE /api-keys/{id}: already-revoked → 404
- expires_at accepted and persisted; null = perpetual
- Auth required (401)
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_api_key_returns_full_key(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "SIEM Integration"})
    assert r.status_code == 200
    body = r.json()
    assert "key" in body
    assert body["key_shown_once"] is True
    assert body["name"] == "SIEM Integration"
    assert "id" in body


@pytest.mark.asyncio
async def test_api_key_has_sav1_prefix(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "Prefix Check"})
    assert r.status_code == 200
    assert r.json()["key"].startswith("sav1_")


@pytest.mark.asyncio
async def test_api_key_full_length(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "Length Check"})
    assert r.status_code == 200
    key = r.json()["key"]
    # sav1_ (5) + 64 hex chars = 69 characters total
    assert len(key) == 69


@pytest.mark.asyncio
async def test_list_keys_does_not_expose_raw_key(auth_client: AsyncClient):
    await auth_client.post("/api/v1/api-keys", json={"name": "No Leak Key"})

    r = await auth_client.get("/api/v1/api-keys")
    assert r.status_code == 200
    for item in r.json():
        assert "key" not in item
        assert "key_hash" not in item
        # display prefix (8 hex chars) should be present
        assert "key_prefix" in item
        assert len(item["key_prefix"]) == 8


@pytest.mark.asyncio
async def test_list_keys_includes_created_key(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "List Me"})
    key_id = r.json()["id"]

    r2 = await auth_client.get("/api/v1/api-keys")
    ids = [k["id"] for k in r2.json()]
    assert key_id in ids


@pytest.mark.asyncio
async def test_revoke_api_key(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "To Revoke"})
    key_id = r.json()["id"]

    r2 = await auth_client.delete(f"/api/v1/api-keys/{key_id}")
    assert r2.status_code == 200
    assert r2.json()["revoked"] is True


@pytest.mark.asyncio
async def test_revoke_nonexistent_key_404(auth_client: AsyncClient):
    r = await auth_client.delete("/api/v1/api-keys/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_revoke_already_revoked_key_404(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "Revoke Twice"})
    key_id = r.json()["id"]

    await auth_client.delete(f"/api/v1/api-keys/{key_id}")
    # Second revoke — key is no longer is_active=TRUE so WHERE clause finds nothing
    r2 = await auth_client.delete(f"/api/v1/api-keys/{key_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_create_key_with_expiry(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={
        "name": "Expiring Key",
        "expires_at": "2027-12-31T23:59:59Z",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["expires_at"] is not None
    assert "2027" in body["expires_at"]


@pytest.mark.asyncio
async def test_create_key_without_expiry_is_perpetual(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/api-keys", json={"name": "Perpetual Key"})
    assert r.status_code == 200
    assert r.json()["expires_at"] is None


@pytest.mark.asyncio
async def test_create_key_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/api-keys", json={"name": "Unauth"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_keys_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/api-keys")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_revoke_key_requires_auth(client: AsyncClient):
    r = await client.delete("/api/v1/api-keys/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 401
