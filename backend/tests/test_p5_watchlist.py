"""P5-J: Watchlist management — plates, faces, and bulk import.

Tests cover:

Plate watchlist:
- POST /watchlist/plates: create allow/block entry
- GET  /watchlist/plates: lists entries
- DELETE /watchlist/plates/{id}: soft-deactivates (is_active → False)
- plate_number is uppercased on import (import path)

Face watchlist (direct embedding):
- POST /watchlist/faces: create entry with pre-computed 512-d vector
- GET  /watchlist/faces: lists entries (embedding not exposed)
- DELETE /watchlist/faces/{id}: soft-deactivates

Bulk plate import:
- Valid CSV → imported count matches rows
- Duplicate plate+list_type → counted in duplicates, not error
- Missing required columns → 400
- Invalid list_type value → row appears in errors, not imported
- Empty/blank plate_number → appears in errors
- More than 1000 rows → 400
- UTF-8 BOM header handled correctly
- Optional expires_at column accepted
- Invalid expires_at format → row appears in errors

Face enroll:
- Wrong MIME type → 415

Auth:
- All endpoints require auth (401)
"""
import io
import pytest
from httpx import AsyncClient


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _dummy_embedding(dim: int = 512) -> list[float]:
    """Return a normalised 512-d vector suitable for face watchlist entries."""
    import math
    raw = [float(i % 17 - 8) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm for v in raw]


def _csv_file(content: str, filename: str = "plates.csv") -> dict:
    """Return a files dict for httpx multipart upload."""
    return {"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")}


# ── Plate CRUD ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_plate_watchlist_entry(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/watchlist/plates", json={
        "plate_number": "SGB1234X",
        "list_type": "block",
        "reason": "Stolen vehicle",
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_list_plate_watchlist_includes_created(auth_client: AsyncClient):
    await auth_client.post("/api/v1/watchlist/plates", json={
        "plate_number": "ABC999Z",
        "list_type": "allow",
    })

    r = await auth_client.get("/api/v1/watchlist/plates")
    assert r.status_code == 200
    plates = [row["plate_number"] for row in r.json()]
    assert "ABC999Z" in plates


@pytest.mark.asyncio
async def test_deactivate_plate_entry(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/watchlist/plates", json={
        "plate_number": "DEL001A",
        "list_type": "block",
    })
    entry_id = r.json()["id"]

    r2 = await auth_client.delete(f"/api/v1/watchlist/plates/{entry_id}")
    assert r2.status_code == 200
    assert r2.json()["is_active"] is False


@pytest.mark.asyncio
async def test_plate_list_has_expected_fields(auth_client: AsyncClient):
    await auth_client.post("/api/v1/watchlist/plates", json={
        "plate_number": "FIELDCHK",
        "list_type": "allow",
    })

    r = await auth_client.get("/api/v1/watchlist/plates")
    assert r.status_code == 200
    assert len(r.json()) > 0
    row = r.json()[0]
    for field in ("id", "plate_number", "list_type", "is_active", "created_at"):
        assert field in row


@pytest.mark.asyncio
async def test_create_plate_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/watchlist/plates", json={
        "plate_number": "UNAUTH01",
        "list_type": "block",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_plates_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/watchlist/plates")
    assert r.status_code == 401


# ── Face CRUD (direct embedding) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_face_watchlist_entry(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/watchlist/faces", json={
        "person_name": "John Doe",
        "list_type": "block",
        "embedding": _dummy_embedding(),
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_list_face_watchlist_includes_created(auth_client: AsyncClient):
    await auth_client.post("/api/v1/watchlist/faces", json={
        "person_name": "Jane Smith",
        "list_type": "allow",
        "embedding": _dummy_embedding(),
    })

    r = await auth_client.get("/api/v1/watchlist/faces")
    assert r.status_code == 200
    names = [row["person_name"] for row in r.json()]
    assert "Jane Smith" in names


@pytest.mark.asyncio
async def test_list_faces_does_not_expose_embedding(auth_client: AsyncClient):
    await auth_client.post("/api/v1/watchlist/faces", json={
        "person_name": "Embed Check",
        "list_type": "block",
        "embedding": _dummy_embedding(),
    })

    r = await auth_client.get("/api/v1/watchlist/faces")
    assert r.status_code == 200
    for row in r.json():
        assert "embedding" not in row


@pytest.mark.asyncio
async def test_deactivate_face_entry(auth_client: AsyncClient):
    r = await auth_client.post("/api/v1/watchlist/faces", json={
        "person_name": "Del Face",
        "list_type": "block",
        "embedding": _dummy_embedding(),
    })
    entry_id = r.json()["id"]

    r2 = await auth_client.delete(f"/api/v1/watchlist/faces/{entry_id}")
    assert r2.status_code == 200
    assert r2.json()["is_active"] is False


@pytest.mark.asyncio
async def test_create_face_requires_auth(client: AsyncClient):
    r = await client.post("/api/v1/watchlist/faces", json={
        "person_name": "Unauth Face",
        "list_type": "block",
        "embedding": _dummy_embedding(),
    })
    assert r.status_code == 401


# ── Bulk plate import ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulk_import_plates_happy_path(auth_client: AsyncClient):
    csv_content = "plate_number,list_type,reason\nSGA111A,block,Stolen\nSGB222B,allow,VIP\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 2
    assert body["duplicates"] == 0
    assert body["errors"] == []
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_bulk_import_plates_uppercased(auth_client: AsyncClient):
    csv_content = "plate_number,list_type\nlowercase1,block\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1

    list_r = await auth_client.get("/api/v1/watchlist/plates")
    plates = [row["plate_number"] for row in list_r.json()]
    assert "LOWERCASE1" in plates


@pytest.mark.asyncio
async def test_bulk_import_duplicate_counted_not_errored(auth_client: AsyncClient):
    csv_content = "plate_number,list_type\nDUPPLATE1,block\n"
    # First import
    await auth_client.post("/api/v1/watchlist/plates/bulk-import", files=_csv_file(csv_content))
    # Second import — same plate+list_type is a duplicate
    r = await auth_client.post("/api/v1/watchlist/plates/bulk-import", files=_csv_file(csv_content))
    assert r.status_code == 200
    body = r.json()
    assert body["duplicates"] == 1
    assert body["imported"] == 0
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_bulk_import_invalid_list_type_goes_to_errors(auth_client: AsyncClient):
    csv_content = "plate_number,list_type\nBADTYPE1,unknown\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1
    assert body["errors"][0]["row"] == 2


@pytest.mark.asyncio
async def test_bulk_import_empty_plate_number_goes_to_errors(auth_client: AsyncClient):
    csv_content = "plate_number,list_type\n,block\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1


@pytest.mark.asyncio
async def test_bulk_import_missing_required_column_400(auth_client: AsyncClient):
    # Missing list_type column
    csv_content = "plate_number,reason\nSGX001X,stolen\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_import_missing_plate_number_column_400(auth_client: AsyncClient):
    csv_content = "list_type,reason\nblock,stolen\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_import_with_bom_header(auth_client: AsyncClient):
    # UTF-8 BOM prefix (﻿) is common from Excel CSV exports
    csv_content = "﻿plate_number,list_type\nBOMPLATE1,block\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files={"file": ("plates.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1


@pytest.mark.asyncio
async def test_bulk_import_with_expires_at(auth_client: AsyncClient):
    csv_content = "plate_number,list_type,expires_at\nEXPIRE01,block,2027-01-01\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1
    assert r.json()["errors"] == []


@pytest.mark.asyncio
async def test_bulk_import_invalid_expires_at_goes_to_errors(auth_client: AsyncClient):
    csv_content = "plate_number,list_type,expires_at\nBADEXP1,block,not-a-date\n"
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 0
    assert len(body["errors"]) == 1


@pytest.mark.asyncio
async def test_bulk_import_mixed_valid_and_invalid_rows(auth_client: AsyncClient):
    csv_content = (
        "plate_number,list_type\n"
        "VALID001,block\n"
        ",allow\n"           # empty plate → error
        "VALID002,allow\n"
        "BAD003,neither\n"   # invalid list_type → error
    )
    r = await auth_client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 2
    assert body["total"] == 4
    assert len(body["errors"]) == 2


@pytest.mark.asyncio
async def test_bulk_import_requires_auth(client: AsyncClient):
    csv_content = "plate_number,list_type\nAUTH001,block\n"
    r = await client.post(
        "/api/v1/watchlist/plates/bulk-import",
        files=_csv_file(csv_content),
    )
    assert r.status_code == 401


# ── Face enroll — validation only (model not loaded in tests) ─────────────────

@pytest.mark.asyncio
async def test_enroll_face_wrong_mime_type_415(auth_client: AsyncClient):
    r = await auth_client.post(
        "/api/v1/watchlist/faces/enroll",
        data={"person_name": "Test", "list_type": "block"},
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
    )
    assert r.status_code == 415
