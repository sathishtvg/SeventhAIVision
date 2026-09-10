"""Gap 62 — Watchlist Router (LPR plates + face watchlist)

Isolated-tenant tests for backend/app/routers/watchlist.py (266 lines).
All endpoints require 'watchlist:manage' permission.

Endpoints (all at /api/v1/watchlist/...):
  GET    /plates                  — list watchlist_entries
  POST   /plates                  — 201; {id}; soft-create (no duplicate check here)
  DELETE /plates/{id}             — soft-deactivate (is_active=FALSE); {id, is_active: False}
  GET    /faces                   — list face_watchlist_entries (embedding excluded)
  POST   /faces                   — 201; takes pre-computed embedding list[float]; {id}
  DELETE /faces/{id}              — soft-deactivate; {id, is_active: False}
  POST   /plates/bulk-import      — multipart CSV; ON CONFLICT DO NOTHING; {total,imported,duplicates,errors}
  POST   /faces/enroll            — multipart photo; runs insightface; 415 wrong type; 503 model fail

Sections:
  A — Plate Watchlist (6 tests)
  B — Face Watchlist (4 tests)
  C — Bulk Import (5 tests)
  D — Face Enroll content-type guard (1 test)
  E — Permissions + RLS (3 tests)
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_app_db_url = os.environ.get("DATABASE_URL", "")
_m = re.search(r"@([^:/]+):", _app_db_url)
_db_host = _m.group(1) if _m else "localhost"

ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:change_me_dev_only@{_db_host}:5432/seventh_ai_vision_test",
)

# 512-dimensional dummy embedding (zero vector — valid pgvector input)
_DUMMY_EMBEDDING: list[float] = [0.0] * 512


def _admin_engine():
    return create_async_engine(ADMIN_DATABASE_URL)


def _app():
    from app.main import app
    return app


async def _seed_tenant_and_token(role_id: int = 2):
    """Create isolated tenant + user; return (tenant_id, user_id, token)."""
    from app.core.security import create_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    slug = f"wl-{tenant_id.hex[:10]}"
    engine = _admin_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Watchlist Test {slug}", "slug": slug},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, "
                "                   full_name, totp_enabled) "
                "VALUES (:id, :tid, CAST(:role AS smallint), :email, 'hashed', 'WL Tester', CAST(:role AS smallint) = 1)"
            ),
            {"id": user_id, "tid": tenant_id, "role": role_id,
             "email": f"wl-{user_id.hex[:8]}@test.local"},
        )
        await s.commit()
    await engine.dispose()
    token = create_access_token(str(user_id), str(tenant_id), role_id)
    return tenant_id, user_id, token


async def _authed(token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(_app()), base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def _create_plate(c: AsyncClient, plate: str = "SGX1234A",
                         list_type: str = "block") -> str:
    r = await c.post("/api/v1/watchlist/plates", json={
        "plate_number": plate, "list_type": list_type, "reason": "Test entry",
    })
    assert r.status_code == 201, f"create_plate failed: {r.text}"
    return r.json()["id"]


# ─── A. Plate Watchlist ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plates_empty_fresh_tenant():
    """GET /watchlist/plates on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/watchlist/plates")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_plate_block_returns_201():
    """POST /watchlist/plates with list_type=block returns 201 + {id}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "SGX1234A", "list_type": "block", "reason": "Stolen vehicle",
        })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_plate_appears_in_list():
    """After POST /watchlist/plates, the entry is visible via GET."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        entry_id = await _create_plate(c, plate="SGB9999Z")
        r = await c.get("/api/v1/watchlist/plates")
    items = r.json()
    ids = [item["id"] for item in items]
    assert entry_id in ids
    # Response contains expected fields
    match = next(item for item in items if item["id"] == entry_id)
    assert match["plate_number"] == "SGB9999Z"
    assert match["list_type"] == "block"
    assert match["is_active"] is True


@pytest.mark.asyncio
async def test_create_plate_allow_type_returns_201():
    """POST /watchlist/plates with list_type=allow returns 201."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "VIP0001A", "list_type": "allow",
        })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_deactivate_plate_sets_is_active_false():
    """DELETE /watchlist/plates/{id} soft-deactivates the entry."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        entry_id = await _create_plate(c, plate="DEL1111B")
        r = await c.delete(f"/api/v1/watchlist/plates/{entry_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == entry_id
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_list_plates_shows_both_list_types():
    """GET /watchlist/plates returns both allow and block entries."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        allow_id = (await c.post("/api/v1/watchlist/plates",
                                  json={"plate_number": "ALLOW001", "list_type": "allow"})).json()["id"]
        block_id = (await c.post("/api/v1/watchlist/plates",
                                  json={"plate_number": "BLOCK001", "list_type": "block"})).json()["id"]
        r = await c.get("/api/v1/watchlist/plates")
    ids = [item["id"] for item in r.json()]
    assert allow_id in ids
    assert block_id in ids


# ─── B. Face Watchlist ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_faces_empty_fresh_tenant():
    """GET /watchlist/faces on a fresh tenant returns 200 + empty list."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.get("/api/v1/watchlist/faces")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_face_with_embedding_returns_201():
    """POST /watchlist/faces with a pre-computed 512-dim embedding returns 201 + {id}."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/watchlist/faces", json={
            "person_name": "John Doe", "list_type": "block",
            "embedding": _DUMMY_EMBEDDING,
        })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_create_face_appears_in_list():
    """After POST /watchlist/faces, the entry is visible via GET (without embedding field)."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post("/api/v1/watchlist/faces", json={
            "person_name": "Jane Smith", "list_type": "allow",
            "embedding": _DUMMY_EMBEDDING,
        })
        entry_id = r.json()["id"]
        r2 = await c.get("/api/v1/watchlist/faces")
    items = r2.json()
    ids = [item["id"] for item in items]
    assert entry_id in ids
    # Embedding should NOT be in the list response (SELECT omits it)
    match = next(item for item in items if item["id"] == entry_id)
    assert "embedding" not in match
    assert "embedding_v" not in match
    assert match["person_name"] == "Jane Smith"


@pytest.mark.asyncio
async def test_deactivate_face_sets_is_active_false():
    """DELETE /watchlist/faces/{id} soft-deactivates the face entry."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        create_r = await c.post("/api/v1/watchlist/faces", json={
            "person_name": "To Deactivate", "list_type": "block",
            "embedding": _DUMMY_EMBEDDING,
        })
        entry_id = create_r.json()["id"]
        r = await c.delete(f"/api/v1/watchlist/faces/{entry_id}")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


# ─── C. Bulk Import ───────────────────────────────────────────────────────────

def _csv(*rows: tuple) -> bytes:
    """Build a UTF-8 CSV with header plate_number,list_type,reason."""
    lines = ["plate_number,list_type,reason"] + [f"{p},{lt},{r}" for p, lt, r in rows]
    return "\n".join(lines).encode()


@pytest.mark.asyncio
async def test_bulk_import_valid_csv_returns_imported_count():
    """POST /watchlist/plates/bulk-import with 2 valid rows returns imported=2."""
    _, _, token = await _seed_tenant_and_token()
    csv_bytes = _csv(("SGA0001A", "block", "Stolen"), ("SGA0002B", "allow", "VIP"))
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/plates/bulk-import",
            files={"file": ("import.csv", csv_bytes, "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["imported"] == 2
    assert body["duplicates"] == 0
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_bulk_import_duplicate_counts_as_duplicate():
    """Importing the same plate+list_type twice counts the second as a duplicate."""
    _, _, token = await _seed_tenant_and_token()
    csv_bytes = _csv(("DUP9999A", "block", "First"), ("DUP9999A", "block", "Second"))
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/plates/bulk-import",
            files={"file": ("import.csv", csv_bytes, "text/csv")},
        )
    body = r.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 1


@pytest.mark.asyncio
async def test_bulk_import_invalid_list_type_returns_error_row():
    """A row with an invalid list_type goes into the errors list."""
    _, _, token = await _seed_tenant_and_token()
    csv_bytes = _csv(("ERR1111A", "suspicious", ""))
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/plates/bulk-import",
            files={"file": ("import.csv", csv_bytes, "text/csv")},
        )
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1
    assert "list_type" in body["errors"][0]["error"]


@pytest.mark.asyncio
async def test_bulk_import_missing_columns_returns_400():
    """CSV without plate_number column returns 400."""
    _, _, token = await _seed_tenant_and_token()
    bad_csv = b"vehicle,type\nSGA0001,block\n"
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/plates/bulk-import",
            files={"file": ("bad.csv", bad_csv, "text/csv")},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_import_empty_file_returns_400():
    """An empty CSV file (no headers) returns 400."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/plates/bulk-import",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
    assert r.status_code == 400


# ─── D. Face Enroll content-type guard ───────────────────────────────────────

@pytest.mark.asyncio
async def test_enroll_face_wrong_content_type_returns_415():
    """POST /watchlist/faces/enroll with a non-image file returns 415."""
    _, _, token = await _seed_tenant_and_token()
    async with await _authed(token) as c:
        r = await c.post(
            "/api/v1/watchlist/faces/enroll",
            data={"person_name": "Test Person", "list_type": "allow"},
            files={"file": ("document.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert r.status_code == 415


# ─── E. Permissions + RLS ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_viewer_cannot_list_plates_403():
    """Viewer (role 6) cannot list plate watchlist — watchlist:manage not granted."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.get("/api/v1/watchlist/plates")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_create_plate_403():
    """Viewer (role 6) cannot add watchlist entries — watchlist:manage not granted."""
    _, _, token = await _seed_tenant_and_token(role_id=6)
    async with await _authed(token) as c:
        r = await c.post("/api/v1/watchlist/plates", json={
            "plate_number": "FRB0001", "list_type": "block",
        })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_plates_rls_isolation():
    """Tenant B cannot see Tenant A's watchlist entries."""
    _, _, tok_a = await _seed_tenant_and_token()
    _, _, tok_b = await _seed_tenant_and_token()
    async with await _authed(tok_a) as c:
        entry_id = await _create_plate(c, plate="RSLTEST1")
    async with await _authed(tok_b) as c:
        r = await c.get("/api/v1/watchlist/plates")
    ids = [item["id"] for item in r.json()]
    assert entry_id not in ids
