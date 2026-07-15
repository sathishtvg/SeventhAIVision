"""P4-D: Pagination with total count on list endpoints.

Verifies that:
- Every upgraded endpoint returns { items, total, limit, offset, has_more }
- total is accurate
- limit/offset correctly slice the result set
- has_more is consistent with total
"""
import pytest
from httpx import AsyncClient


# ── helpers ──────────────────────────────────────────────────────────────────

def assert_paginated(body: dict, min_total: int = 0) -> None:
    assert "items"    in body, "missing 'items'"
    assert "total"    in body, "missing 'total'"
    assert "limit"    in body, "missing 'limit'"
    assert "offset"   in body, "missing 'offset'"
    assert "has_more" in body, "missing 'has_more'"
    assert isinstance(body["items"],    list)
    assert isinstance(body["total"],    int)
    assert isinstance(body["limit"],    int)
    assert isinstance(body["offset"],   int)
    assert isinstance(body["has_more"], bool)
    assert body["total"] >= min_total
    assert body["has_more"] == (body["offset"] + body["limit"] < body["total"])


# ── alerts ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/alerts")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_alerts_limit_respected(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/alerts", params={"limit": 1, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) <= 1
    assert body["limit"] == 1


@pytest.mark.asyncio
async def test_alerts_offset_advances_page(auth_client: AsyncClient):
    """Two consecutive pages should not share the same first item (if total > 1)."""
    p0 = (await auth_client.get("/api/v1/alerts", params={"limit": 1, "offset": 0})).json()
    p1 = (await auth_client.get("/api/v1/alerts", params={"limit": 1, "offset": 1})).json()
    if p0["total"] > 1:
        assert p0["items"] != p1["items"]


@pytest.mark.asyncio
async def test_alerts_total_stable_across_pages(auth_client: AsyncClient):
    """total must be the same regardless of which page is requested."""
    t0 = (await auth_client.get("/api/v1/alerts", params={"limit": 1, "offset": 0})).json()["total"]
    t1 = (await auth_client.get("/api/v1/alerts", params={"limit": 1, "offset": 5})).json()["total"]
    assert t0 == t1


# ── incidents ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incidents_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/incidents")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_incidents_limit_respected(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/incidents", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) <= 2
    assert body["limit"] == 2


# ── visitors ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_visitors_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/visitors")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_visitors_total_includes_all_statuses(auth_client: AsyncClient):
    """Creating a visitor and then querying should increment total."""
    before = (await auth_client.get("/api/v1/visitors")).json()["total"]

    await auth_client.post("/api/v1/visitors", json={"full_name": "Pagination Test User"})

    after = (await auth_client.get("/api/v1/visitors")).json()["total"]
    assert after >= before + 1


@pytest.mark.asyncio
async def test_visitors_offset_pagination(auth_client: AsyncClient):
    """Offset beyond total returns empty items but correct total."""
    total = (await auth_client.get("/api/v1/visitors")).json()["total"]
    r = await auth_client.get("/api/v1/visitors", params={"limit": 10, "offset": total + 1000})
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == total
    assert body["has_more"] is False


# ── audit ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/audit")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_audit_limit_respected(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/audit", params={"limit": 3, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) <= 3
    assert body["limit"] == 3


@pytest.mark.asyncio
async def test_audit_action_filter(auth_client: AsyncClient):
    """Filtering by action must reduce total to only matching rows."""
    all_total = (await auth_client.get("/api/v1/audit")).json()["total"]
    r = await auth_client.get("/api/v1/audit", params={"action": "nonexistent_action_xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    # total without filter must be >= total with non-matching filter
    assert all_total >= 0


# ── detections ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detections_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/detections")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_lpr_events_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/detections/lpr-events")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_face_events_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/detections/face-events")
    assert r.status_code == 200
    assert_paginated(r.json())


@pytest.mark.asyncio
async def test_intrusion_events_paginated_envelope(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/detections/intrusion-events")
    assert r.status_code == 200
    assert_paginated(r.json())


# ── max limit cap ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_limit_capped_at_200(auth_client: AsyncClient):
    """Requesting limit=9999 must be silently capped at 200."""
    r = await auth_client.get("/api/v1/alerts", params={"limit": 9999})
    assert r.status_code == 200
    assert r.json()["limit"] == 200


@pytest.mark.asyncio
async def test_negative_offset_clamped_to_zero(auth_client: AsyncClient):
    """offset=-5 must be treated as offset=0."""
    r = await auth_client.get("/api/v1/visitors", params={"offset": -5})
    assert r.status_code == 200
    assert r.json()["offset"] == 0
