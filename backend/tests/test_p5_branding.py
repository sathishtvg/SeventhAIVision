"""P5-L (part 1): Tenant branding management.

Tests cover:

GET /api/v1/branding:
- Returns name, branding (dict), timezone
- branding key is always a dict, never null
- Reflects updates made via PUT

PUT /api/v1/branding:
- Sets arbitrary string key/value pairs in the branding dict
- Returns the new branding dict
- Persists across subsequent GET requests
- Empty dict ({}) clears all branding keys
- Non-string value in branding dict → 422 (dict[str, str] enforced)

Auth:
- GET requires auth (401)
- PUT requires auth (401)
"""
import pytest
from httpx import AsyncClient


# ── GET /branding ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_branding_returns_expected_fields(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/branding")
    assert r.status_code == 200
    body = r.json()
    assert "name" in body
    assert "branding" in body
    assert "timezone" in body


@pytest.mark.asyncio
async def test_get_branding_branding_key_is_dict(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/branding")
    assert r.status_code == 200
    # branding is never null — defaults to {} when unset
    assert isinstance(r.json()["branding"], dict)


@pytest.mark.asyncio
async def test_get_branding_timezone_is_string(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/branding")
    assert r.status_code == 200
    assert isinstance(r.json()["timezone"], str)
    assert len(r.json()["timezone"]) > 0


# ── PUT /branding ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_branding_sets_keys(auth_client: AsyncClient):
    r = await auth_client.put("/api/v1/branding", json={
        "branding": {
            "logo_url": "https://example.com/logo.png",
            "primary_color": "#6C63FF",
        }
    })
    assert r.status_code == 200
    body = r.json()
    assert "branding" in body
    assert body["branding"]["logo_url"] == "https://example.com/logo.png"
    assert body["branding"]["primary_color"] == "#6C63FF"


@pytest.mark.asyncio
async def test_updated_branding_persisted_in_get(auth_client: AsyncClient):
    sentinel = "https://cdn.example.com/logo-sentinel.png"
    await auth_client.put("/api/v1/branding", json={
        "branding": {"logo_url": sentinel}
    })

    r = await auth_client.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json()["branding"]["logo_url"] == sentinel


@pytest.mark.asyncio
async def test_update_branding_empty_dict_clears_keys(auth_client: AsyncClient):
    # Populate first
    await auth_client.put("/api/v1/branding", json={
        "branding": {"theme": "dark", "accent": "#00D9C0"}
    })

    # Now clear
    r = await auth_client.put("/api/v1/branding", json={"branding": {}})
    assert r.status_code == 200
    assert r.json()["branding"] == {}


@pytest.mark.asyncio
async def test_update_branding_non_string_value_422(auth_client: AsyncClient):
    # dict[str, str] is enforced — integer value must be rejected
    r = await auth_client.put("/api/v1/branding", json={
        "branding": {"logo_size": 256}   # int, not str
    })
    assert r.status_code == 422


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_branding_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/branding")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_update_branding_requires_auth(client: AsyncClient):
    r = await client.put("/api/v1/branding", json={"branding": {"k": "v"}})
    assert r.status_code == 401
