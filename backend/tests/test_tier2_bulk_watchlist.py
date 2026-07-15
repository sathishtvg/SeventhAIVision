"""Tests for Tier 2 Feature 6: Bulk watchlist import (CSV upload).

Covers:
- Valid CSV imports (happy path, mixed list types)
- Duplicate handling (ON CONFLICT DO NOTHING)
- Invalid row data (missing plate, bad list_type, invalid expires_at)
- Permission gate (operator cannot import)
- Empty CSV / missing required columns
- Row limit enforcement (>1000 rows)
"""

import io
import uuid

import pytest

from app.core.security import create_access_token
from tests.test_rbac import _seed_user_with_role


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID, role_id: int) -> str:
    return create_access_token(str(user_id), str(tenant_id), role_id=role_id)


def _csv_bytes(*rows: str, header: str = "plate_number,list_type,reason,expires_at") -> bytes:
    lines = [header] + list(rows)
    return "\n".join(lines).encode()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def _admin(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=2)
    return _jwt(tenant_id, user_id, 2)


@pytest.fixture
async def _operator(admin_session):
    tenant_id, user_id = await _seed_user_with_role(admin_session, role_id=4)
    return _jwt(tenant_id, user_id, 4)


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_import_valid_csv(_admin, app_client):
    jwt = _admin
    csv_data = _csv_bytes(
        "SGA1234,block,stolen car,",
        "SGB5678,allow,company van,",
        "SGC9999,block,suspect,,",  # extra trailing comma
    )
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["total"] == 3
    assert data["imported"] == 3
    assert data["duplicates"] == 0
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_bulk_import_duplicate_handling(_admin, app_client):
    """Importing the same plate+list_type twice: second counted as duplicate."""
    jwt = _admin
    csv_once = _csv_bytes("DUPPLATE1,block,,")
    resp1 = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_once, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp1.json()["imported"] == 1

    resp2 = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_once, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    data = resp2.json()
    assert data["imported"] == 0
    assert data["duplicates"] == 1


@pytest.mark.asyncio
async def test_bulk_import_invalid_list_type(_admin, app_client):
    jwt = _admin
    csv_data = _csv_bytes(
        "GOOD1,block,,",
        "BAD1,unknown_type,,",
        "GOOD2,allow,,",
    )
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert len(data["errors"]) == 1
    assert data["errors"][0]["row"] == 3
    assert "list_type" in data["errors"][0]["error"]


@pytest.mark.asyncio
async def test_bulk_import_missing_plate(_admin, app_client):
    jwt = _admin
    csv_data = _csv_bytes(
        ",block,,",
        "REALPLATE,allow,,",
    )
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 1
    assert len(data["errors"]) == 1
    assert "plate_number" in data["errors"][0]["error"]


@pytest.mark.asyncio
async def test_bulk_import_invalid_expires_at(_admin, app_client):
    jwt = _admin
    csv_data = _csv_bytes("SOMEPLATE,block,,not-a-date")
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 0
    assert len(data["errors"]) == 1
    assert "expires_at" in data["errors"][0]["error"]


@pytest.mark.asyncio
async def test_bulk_import_valid_expires_at(_admin, app_client):
    jwt = _admin
    csv_data = _csv_bytes("EXPPLATE,block,stolen,2030-12-31")
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1


@pytest.mark.asyncio
async def test_bulk_import_requires_permission(_operator, app_client):
    jwt = _operator
    csv_data = _csv_bytes("ANYPLATE,block,,")
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bulk_import_missing_required_columns(_admin, app_client):
    jwt = _admin
    csv_data = b"plate_number,reason\nABC123,stolen\n"
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 400
    assert "list_type" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_import_row_limit(_admin, app_client):
    jwt = _admin
    rows = [f"PLATE{i:04d},block,," for i in range(1001)]
    csv_data = _csv_bytes(*rows)
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 400
    assert "1000" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bulk_import_empty_file(_admin, app_client):
    jwt = _admin
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", b"", "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bulk_import_uppercase_normalization(_admin, app_client):
    """plate_number values are normalized to uppercase."""
    jwt = _admin
    csv_data = _csv_bytes("abc123,block,,")
    resp = await app_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("import.csv", csv_data, "text/csv")},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1

    list_resp = await app_client.get(
        "/api/v1/watchlist/plates",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    plates = [e["plate_number"] for e in list_resp.json()]
    assert "ABC123" in plates
