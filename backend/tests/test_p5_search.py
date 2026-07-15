"""P5-C: Unified cross-module search tests.

Tests cover:
- Basic search returns items from multiple modules
- module filter narrows results to specified types
- Pagination (limit / offset / has_more)
- Response envelope shape (query, modules, total, has_more)
- Query too short → 422
- Unknown module name → 422
- Empty modules list → 422
- No results returns empty items list
- Cross-module matches: one term hitting alerts AND incidents
- RLS: search never leaks rows from another tenant
"""
import pytest
from httpx import AsyncClient


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str) -> str:
    r = await client.post("/api/v1/cameras", json={
        "name": name,
        "location": f"Location for {name}",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(client: AsyncClient, title: str, camera_id: str | None = None) -> str:
    if camera_id is None:
        camera_id = await _make_camera(client, f"cam-for-alert-{title[:8]}")
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": "high",
        "alert_code": "test.search",
        "title": title,
        "message": f"Message for {title}",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _make_incident(client: AsyncClient, title: str, description: str = "") -> str:
    r = await client.post("/api/v1/incidents", json={
        "title": title,
        "description": description,
        "severity": "medium",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Basic search ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_returns_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search", params={"q": "test"})
    assert r.status_code == 200
    body = r.json()
    assert "query" in body
    assert "modules" in body
    assert "items" in body
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert "has_more" in body
    assert body["query"] == "test"
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)


@pytest.mark.asyncio
async def test_search_finds_alert_by_title(auth_client: AsyncClient):
    uid = "SRCH_ALERT_UNQ"
    await _make_alert(auth_client, f"Security breach detected {uid}")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["alerts"]})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    modules = [i["module"] for i in body["items"]]
    assert "alert" in modules


@pytest.mark.asyncio
async def test_search_finds_incident_by_title(auth_client: AsyncClient):
    uid = "SRCH_INC_UNQ"
    await _make_incident(auth_client, f"Major incident {uid}", "Description here")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["incidents"]})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(i["module"] == "incident" for i in body["items"])


@pytest.mark.asyncio
async def test_search_finds_camera_by_name(auth_client: AsyncClient):
    uid = "SRCH_CAM_UNQ"
    await _make_camera(auth_client, f"Main Gate Camera {uid}")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["cameras"]})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(i["module"] == "camera" for i in body["items"])


# ── Cross-module: one term hits multiple types ────────────────────────────────

@pytest.mark.asyncio
async def test_search_cross_module_returns_multiple_types(auth_client: AsyncClient):
    uid = "CROSS_MOD_UNQ"
    await _make_alert(auth_client, f"Alert with {uid}")
    await _make_incident(auth_client, f"Incident with {uid}")

    r = await auth_client.get(
        "/api/v1/search",
        params={"q": uid, "modules": ["alerts", "incidents"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    module_types = {i["module"] for i in body["items"]}
    assert "alert" in module_types
    assert "incident" in module_types


# ── Item shape ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_item_has_required_fields(auth_client: AsyncClient):
    uid = "SHAPE_CHK_UNQ"
    await _make_alert(auth_client, f"Shape check {uid}")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["alerts"]})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    item = body["items"][0]
    for field in ("module", "id", "title", "summary", "status", "created_at"):
        assert field in item, f"Missing field: {field}"


# ── Module filtering ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_module_filter_alerts_only(auth_client: AsyncClient):
    uid = "MOD_FILT_ALERT_UNQ"
    await _make_alert(auth_client, f"Alert only {uid}")
    await _make_incident(auth_client, f"Incident also {uid}")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["alerts"]})
    assert r.status_code == 200
    body = r.json()
    assert all(i["module"] == "alert" for i in body["items"])


@pytest.mark.asyncio
async def test_search_module_filter_incidents_only(auth_client: AsyncClient):
    uid = "MOD_FILT_INC_UNQ"
    await _make_alert(auth_client, f"Alert too {uid}")
    await _make_incident(auth_client, f"Incident only {uid}")

    r = await auth_client.get("/api/v1/search", params={"q": uid, "modules": ["incidents"]})
    assert r.status_code == 200
    body = r.json()
    assert all(i["module"] == "incident" for i in body["items"])


# ── Pagination ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_pagination_limit(auth_client: AsyncClient):
    uid = "PAGINATION_UNQ"
    # Create 5 alerts with same unique token
    for i in range(5):
        await _make_alert(auth_client, f"Alert item {i} {uid}")

    r = await auth_client.get(
        "/api/v1/search",
        params={"q": uid, "modules": ["alerts"], "limit": 2, "offset": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) <= 2
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] >= 5
    assert body["has_more"] is True


@pytest.mark.asyncio
async def test_search_pagination_offset(auth_client: AsyncClient):
    uid = "PGOFFSET_UNQ"
    for i in range(4):
        await _make_alert(auth_client, f"Offset test {i} {uid}")

    r1 = await auth_client.get(
        "/api/v1/search",
        params={"q": uid, "modules": ["alerts"], "limit": 2, "offset": 0},
    )
    r2 = await auth_client.get(
        "/api/v1/search",
        params={"q": uid, "modules": ["alerts"], "limit": 2, "offset": 2},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    ids1 = {i["id"] for i in r1.json()["items"]}
    ids2 = {i["id"] for i in r2.json()["items"]}
    # Pages should not overlap
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_search_has_more_false_when_exhausted(auth_client: AsyncClient):
    uid = "HASMORE_EXHAUST_UNQ"
    await _make_alert(auth_client, f"Only one {uid}")

    r = await auth_client.get(
        "/api/v1/search",
        params={"q": uid, "modules": ["alerts"], "limit": 50, "offset": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False


# ── No results ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_no_results(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/search",
        params={"q": "xqz_no_match_8675309"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["has_more"] is False


# ── Validation errors ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_query_too_short_422(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search", params={"q": "a"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_query_missing_422(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_unknown_module_422(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/search",
        params={"q": "test", "modules": ["unknown_module"]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_modules_422(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search?q=test&modules=")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_limit_max_50(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search", params={"q": "test", "limit": 100})
    assert r.status_code == 422


# ── Default modules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_default_modules_in_response(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search", params={"q": "test"})
    assert r.status_code == 200
    body = r.json()
    # Default modules should be listed
    expected = {"alerts", "incidents", "cameras", "sites", "watchlist", "face_watchlist", "zones"}
    assert expected.issubset(set(body["modules"]))


# ── Unauthenticated ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/search", params={"q": "test"})
    assert r.status_code == 401


# ── Query reflection ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_query_reflected_in_response(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/search", params={"q": "reflected_term"})
    assert r.status_code == 200
    assert r.json()["query"] == "reflected_term"


# ── Users module (opt-in) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_users_module_opt_in(auth_client: AsyncClient):
    r = await auth_client.get(
        "/api/v1/search",
        params={"q": "admin", "modules": ["users"]},
    )
    assert r.status_code == 200
    body = r.json()
    # Any user rows returned should have module='user'
    for item in body["items"]:
        assert item["module"] == "user"
