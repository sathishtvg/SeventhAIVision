"""P5-M (part 2): Crowd Zone CRUD — capacity-bounded polygon zones for crowd density.

Crowd zones extend restricted_zones with a max_capacity field for density threshold
alerting. They use the same 'zone:manage' permission.

Tests cover:

POST /api/v1/crowd-zones:
- Create zone with valid polygon → 201 + id
- Invalid severity → 422
- max_capacity=0 → 422 (must be >= 1)
- max_capacity=1 (minimum valid) → 201

GET /api/v1/crowd-zones:
- Returns list including newly created zone
- Each row has all expected fields (id, camera_id, name, polygon, max_capacity, severity, is_active)
- Polygon stored and returned as list of {x, y} objects

PUT /api/v1/crowd-zones/{zone_id}:
- Update name → reflected in response
- Update max_capacity → reflected in response
- Update severity → reflected in response
- Set is_active=False → reflected in response
- Empty body (no fields) → 422
- Invalid severity in update → 422
- max_capacity=0 in update → 422

DELETE /api/v1/crowd-zones/{zone_id}:
- Soft-deactivates (is_active=False), returns {id, is_active: false}
- Deactivated zone still appears in list (soft delete, not removed)

Auth:
- All endpoints require auth (401)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "CrowdZone Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "Hall A"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


_POLYGON = [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}, {"x": 0.1, "y": 0.5}]


async def _create_zone(
    client: AsyncClient,
    camera_id: str,
    *,
    name: str = "Queue Area",
    max_capacity: int = 20,
    severity: str = "medium",
) -> dict:
    r = await client.post("/api/v1/crowd-zones", json={
        "camera_id": camera_id,
        "name": name,
        "polygon": _POLYGON,
        "max_capacity": max_capacity,
        "severity": severity,
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── Create ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_crowd_zone_201(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "CreateZoneCam")
    r = await auth_client.post("/api/v1/crowd-zones", json={
        "camera_id": cam,
        "name": "Entry Queue",
        "polygon": _POLYGON,
        "max_capacity": 15,
        "severity": "medium",
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_returns_id(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "IdCheckCam")
    result = await _create_zone(auth_client, cam, name="Zone ID Check")
    assert "id" in result
    assert isinstance(result["id"], str)
    assert len(result["id"]) == 36   # UUID


@pytest.mark.asyncio
async def test_create_invalid_severity_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "BadSevCam")
    r = await auth_client.post("/api/v1/crowd-zones", json={
        "camera_id": cam,
        "name": "Bad Zone",
        "polygon": _POLYGON,
        "max_capacity": 10,
        "severity": "extreme",   # not a valid severity
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_max_capacity_zero_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ZeroCapCam")
    r = await auth_client.post("/api/v1/crowd-zones", json={
        "camera_id": cam,
        "name": "Zero Cap Zone",
        "polygon": _POLYGON,
        "max_capacity": 0,   # must be >= 1
        "severity": "medium",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_max_capacity_one_is_valid(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "MinCapCam")
    r = await auth_client.post("/api/v1/crowd-zones", json={
        "camera_id": cam,
        "name": "Min Cap Zone",
        "polygon": _POLYGON,
        "max_capacity": 1,   # minimum valid
        "severity": "low",
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_all_valid_severities(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "SevVariantCam")
    for sev in ("low", "medium", "high", "critical"):
        r = await auth_client.post("/api/v1/crowd-zones", json={
            "camera_id": cam,
            "name": f"Zone-{sev}",
            "polygon": _POLYGON,
            "max_capacity": 10,
            "severity": sev,
        })
        assert r.status_code == 201, f"Failed for severity={sev}: {r.text}"


@pytest.mark.asyncio
async def test_create_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/crowd-zones", json={
        "camera_id": "00000000-0000-0000-0000-000000000001",
        "name": "Unauth Zone",
        "polygon": _POLYGON,
        "max_capacity": 10,
        "severity": "medium",
    })
    assert r.status_code == 401


# ── List ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_includes_created_zone(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ListZoneCam")
    zone = await _create_zone(auth_client, cam, name="List Check Zone")

    r = await auth_client.get("/api/v1/crowd-zones")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert zone["id"] in ids


@pytest.mark.asyncio
async def test_list_zone_has_expected_fields(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "FieldCheckCam")
    await _create_zone(auth_client, cam, name="Field Zone")

    r = await auth_client.get("/api/v1/crowd-zones")
    assert r.status_code == 200
    assert len(r.json()) > 0
    row = r.json()[0]
    for field in ("id", "camera_id", "name", "polygon", "max_capacity", "severity", "is_active", "created_at"):
        assert field in row, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_list_polygon_returned_as_list(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "PolygonCheckCam")
    await _create_zone(auth_client, cam, name="Polygon Zone")

    r = await auth_client.get("/api/v1/crowd-zones")
    assert r.status_code == 200
    zone_rows = [row for row in r.json() if row["name"] == "Polygon Zone"]
    assert len(zone_rows) >= 1
    poly = zone_rows[0]["polygon"]
    assert isinstance(poly, list)
    assert len(poly) == 4
    for point in poly:
        assert "x" in point
        assert "y" in point


@pytest.mark.asyncio
async def test_list_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/crowd-zones")
    assert r.status_code == 401


# ── Update ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_zone_name(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateNameCam")
    zone = await _create_zone(auth_client, cam, name="Old Name")

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"name": "New Name"})
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_zone_max_capacity(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateCapCam")
    zone = await _create_zone(auth_client, cam, max_capacity=20)

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"max_capacity": 50})
    assert r.status_code == 200
    assert r.json()["max_capacity"] == 50


@pytest.mark.asyncio
async def test_update_zone_severity(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateSevCam")
    zone = await _create_zone(auth_client, cam, severity="low")

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"severity": "critical"})
    assert r.status_code == 200
    assert r.json()["severity"] == "critical"


@pytest.mark.asyncio
async def test_update_zone_deactivate(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateDeactCam")
    zone = await _create_zone(auth_client, cam, name="Deactivate Via Update")

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False


@pytest.mark.asyncio
async def test_update_empty_body_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "EmptyBodyCam")
    zone = await _create_zone(auth_client, cam)

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_invalid_severity_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateBadSevCam")
    zone = await _create_zone(auth_client, cam)

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"severity": "catastrophic"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_max_capacity_zero_422(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "UpdateZeroCapCam")
    zone = await _create_zone(auth_client, cam)

    r = await auth_client.put(f"/api/v1/crowd-zones/{zone['id']}", json={"max_capacity": 0})
    assert r.status_code == 422


# ── Delete (soft deactivate) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_crowd_zone_returns_inactive(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "DeleteZoneCam")
    zone = await _create_zone(auth_client, cam, name="Zone To Delete")

    r = await auth_client.delete(f"/api/v1/crowd-zones/{zone['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == zone["id"]
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_deleted_zone_still_in_list(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "StillInListCam")
    zone = await _create_zone(auth_client, cam, name="Soft Deleted Zone")

    await auth_client.delete(f"/api/v1/crowd-zones/{zone['id']}")

    r = await auth_client.get("/api/v1/crowd-zones")
    ids = [row["id"] for row in r.json()]
    assert zone["id"] in ids   # soft delete: row remains in list

    deactivated = next(row for row in r.json() if row["id"] == zone["id"])
    assert deactivated["is_active"] is False


@pytest.mark.asyncio
async def test_delete_requires_auth(client: AsyncClient):
    r = await client.delete("/api/v1/crowd-zones/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 401
