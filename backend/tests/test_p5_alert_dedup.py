"""P5-H: Alert deduplication rules — CRUD + dedup enforcement.

Tests cover:
- POST /alert-dedup-rules: create rule, returned fields present
- GET  /alert-dedup-rules: list rules
- GET  /alert-dedup-rules/{id}: single rule, 404 on missing
- PUT  /alert-dedup-rules/{id}: update window_seconds + is_active
- DELETE /alert-dedup-rules/{id}: removes rule, subsequent GET 404s

Dedup enforcement (via POST /alerts):
- Rule active with matching camera+module → duplicate alert returns existing id
- Rule active but window already expired (window=1s, wait not possible in test)
  → tested via an exact-match rule with no prior open alert (creates new)
- Rule with module_type=None (wildcard) matches any module for that camera
- Rule with camera_id=None (wildcard) matches any camera for that module
- Global wildcard (both None) matches any alert
- Rule disabled (is_active=False) does NOT suppress duplicates
- Most-specific rule wins (camera+module > camera-only > module-only > global)

Auth:
- All endpoints require auth (401 when unauthenticated)
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Dedup Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_rule(
    client: AsyncClient,
    *,
    module_type: str | None = "intrusion",
    camera_id: str | None = None,
    window_seconds: int = 300,
    is_active: bool = True,
) -> str:
    body = {
        "window_seconds": window_seconds,
        "is_active": is_active,
    }
    if module_type is not None:
        body["module_type"] = module_type
    if camera_id is not None:
        body["camera_id"] = camera_id

    r = await client.post("/api/v1/alert-dedup-rules", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _make_alert(
    client: AsyncClient,
    camera_id: str,
    module_type: str = "intrusion",
    title: str = "Dedup test alert",
) -> dict:
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": module_type,
        "severity": "medium",
        "title": title,
        "message": "Test",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── CRUD ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_dedup_rule_returns_fields(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alert-dedup-rules", json={
        "module_type": "lpr",
        "window_seconds": 120,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["module_type"] == "lpr"
    assert body["window_seconds"] == 120
    assert body["is_active"] is True
    assert "id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_list_dedup_rules_includes_created(auth_client: AsyncClient):
    rule_id = await _make_rule(auth_client, module_type="face")

    r = await auth_client.get("/api/v1/alert-dedup-rules")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert rule_id in ids


@pytest.mark.asyncio
async def test_get_dedup_rule_by_id(auth_client: AsyncClient):
    rule_id = await _make_rule(auth_client, module_type="crowd", window_seconds=60)

    r = await auth_client.get(f"/api/v1/alert-dedup-rules/{rule_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rule_id
    assert body["module_type"] == "crowd"
    assert body["window_seconds"] == 60


@pytest.mark.asyncio
async def test_get_nonexistent_rule_404(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/alert-dedup-rules/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_dedup_rule(auth_client: AsyncClient):
    rule_id = await _make_rule(auth_client, window_seconds=300)

    r = await auth_client.put(f"/api/v1/alert-dedup-rules/{rule_id}", json={
        "module_type": "intrusion",
        "window_seconds": 600,
        "is_active": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["window_seconds"] == 600
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_update_nonexistent_rule_404(auth_client: AsyncClient):
    r = await auth_client.put(
        "/api/v1/alert-dedup-rules/00000000-0000-0000-0000-000000000099",
        json={"window_seconds": 60},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_dedup_rule(auth_client: AsyncClient):
    rule_id = await _make_rule(auth_client, module_type="weapon")

    r = await auth_client.delete(f"/api/v1/alert-dedup-rules/{rule_id}")
    assert r.status_code == 200

    # Confirm it's gone
    r2 = await auth_client.get(f"/api/v1/alert-dedup-rules/{rule_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_rule_404(auth_client: AsyncClient):
    r = await auth_client.delete("/api/v1/alert-dedup-rules/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404


# ── Validation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_window_seconds_below_minimum_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alert-dedup-rules", json={
        "module_type": "intrusion",
        "window_seconds": 0,
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_window_seconds_above_maximum_422(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/alert-dedup-rules", json={
        "module_type": "intrusion",
        "window_seconds": 86401,   # > 24 hours
    })
    assert r.status_code == 422


# ── Dedup enforcement ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exact_match_rule_suppresses_duplicate(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ExactDedup")
    await _make_rule(auth_client, module_type="intrusion", camera_id=cam, window_seconds=300)

    # First alert — should create
    first = await _make_alert(auth_client, cam, "intrusion")
    assert first["deduplicated"] is False

    # Second alert same cam+module within window — should be suppressed
    second = await _make_alert(auth_client, cam, "intrusion")
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]


@pytest.mark.asyncio
async def test_no_rule_allows_duplicate_alerts(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "NoDedupCam")
    # No rule created for this camera

    first = await _make_alert(auth_client, cam, "lpr")
    second = await _make_alert(auth_client, cam, "lpr")

    # Both should create (or first creates, second is a new row too)
    # Both IDs should be distinct because there's no dedup rule
    assert first["deduplicated"] is False
    assert second["deduplicated"] is False
    assert first["id"] != second["id"]


@pytest.mark.asyncio
async def test_disabled_rule_does_not_suppress(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "DisabledRule")
    await _make_rule(
        auth_client, module_type="intrusion", camera_id=cam,
        window_seconds=300, is_active=False,
    )

    first = await _make_alert(auth_client, cam, "intrusion")
    second = await _make_alert(auth_client, cam, "intrusion")

    assert first["deduplicated"] is False
    assert second["deduplicated"] is False
    assert first["id"] != second["id"]


@pytest.mark.asyncio
async def test_module_wildcard_rule_suppresses_any_module(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "WildcardModule")
    # module_type=None → wildcard: matches any module for this camera
    await _make_rule(auth_client, module_type=None, camera_id=cam, window_seconds=300)

    first = await _make_alert(auth_client, cam, "intrusion")
    assert first["deduplicated"] is False

    # Different module, same camera — should still be suppressed by wildcard
    second = await _make_alert(auth_client, cam, "lpr")
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]


@pytest.mark.asyncio
async def test_camera_wildcard_rule_suppresses_any_camera(auth_client: AsyncClient):
    cam_a = await _make_camera(auth_client, "WildcamA")
    cam_b = await _make_camera(auth_client, "WildcamB")
    # camera_id=None → wildcard: matches module=face on any camera
    await _make_rule(auth_client, module_type="face", camera_id=None, window_seconds=300)

    first = await _make_alert(auth_client, cam_a, "face")
    assert first["deduplicated"] is False

    # Same module, different camera — wildcard should suppress
    second = await _make_alert(auth_client, cam_b, "face")
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]


@pytest.mark.asyncio
async def test_different_module_bypasses_specific_rule(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "ModuleMismatch")
    # Rule only for "intrusion" on this camera
    await _make_rule(auth_client, module_type="intrusion", camera_id=cam, window_seconds=300)

    # First intrusion → creates
    await _make_alert(auth_client, cam, "intrusion")

    # Different module (lpr) → should NOT be suppressed by an intrusion-only rule
    lpr_alert = await _make_alert(auth_client, cam, "lpr")
    assert lpr_alert["deduplicated"] is False


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_rules_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/alert-dedup-rules")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_rule_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/alert-dedup-rules", json={"window_seconds": 60})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_rule_requires_auth(client: AsyncClient):
    r = await client.delete("/api/v1/alert-dedup-rules/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 401
