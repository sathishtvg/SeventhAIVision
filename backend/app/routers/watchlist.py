import asyncio
import csv
import io
from datetime import datetime

from pydantic import BaseModel
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uploads import MAX_CSV_UPLOAD_BYTES, MAX_IMAGE_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.face import extract_embedding_sync as _extract_embedding_sync

router = APIRouter(tags=["watchlist"])


class PlateWatchlistCreate(BaseModel):
    plate_number: str
    list_type: str  # 'allow' | 'block'
    reason: str | None = None


class FaceWatchlistCreate(BaseModel):
    person_name: str
    list_type: str
    embedding: list[float]  # pre-computed elsewhere (e.g. an enrollment flow) — Phase 1 takes the vector directly


@router.get("/api/v1/watchlist/plates", dependencies=[Depends(require_permission("watchlist:manage"))])
async def list_plate_watchlist(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("SELECT id, plate_number, list_type, reason, is_active, created_at FROM watchlist_entries ORDER BY created_at DESC")
    )
    return [dict(row._mapping) for row in result]


@router.post("/api/v1/watchlist/plates", status_code=201, dependencies=[Depends(require_permission("watchlist:manage"))])
async def create_plate_watchlist_entry(
    body: PlateWatchlistCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            "INSERT INTO watchlist_entries (tenant_id, plate_number, list_type, reason, added_by_user_id) "
            "VALUES (current_setting('app.current_tenant')::uuid, :plate, :list_type, :reason, :uid) "
            "RETURNING id"
        ),
        {"plate": body.plate_number, "list_type": body.list_type, "reason": body.reason, "uid": token.user_id},
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id}


@router.delete("/api/v1/watchlist/plates/{entry_id}", dependencies=[Depends(require_permission("watchlist:manage"))])
async def deactivate_plate_watchlist_entry(entry_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE watchlist_entries SET is_active = FALSE WHERE id = :id"), {"id": entry_id})
    await db.commit()
    return {"id": entry_id, "is_active": False}


@router.get("/api/v1/watchlist/faces", dependencies=[Depends(require_permission("watchlist:manage"))])
async def list_face_watchlist(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("SELECT id, person_name, list_type, is_active, created_at FROM face_watchlist_entries ORDER BY created_at DESC")
    )
    return [dict(row._mapping) for row in result]


@router.post("/api/v1/watchlist/faces", status_code=201, dependencies=[Depends(require_permission("watchlist:manage"))])
async def create_face_watchlist_entry(body: FaceWatchlistCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    embedding_vec = "[" + ",".join(str(v) for v in body.embedding) + "]"
    result = await db.execute(
        text(
            "INSERT INTO face_watchlist_entries (tenant_id, person_name, embedding_v, list_type) "
            "VALUES (current_setting('app.current_tenant')::uuid, :name, CAST(:embedding_v AS vector), :list_type) "
            "RETURNING id"
        ),
        {"name": body.person_name, "embedding_v": embedding_vec, "list_type": body.list_type},
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id}


@router.delete("/api/v1/watchlist/faces/{entry_id}", dependencies=[Depends(require_permission("watchlist:manage"))])
async def deactivate_face_watchlist_entry(entry_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("UPDATE face_watchlist_entries SET is_active = FALSE WHERE id = :id"), {"id": entry_id})
    await db.commit()
    return {"id": entry_id, "is_active": False}


_BULK_MAX_ROWS = 1000


@router.post(
    "/api/v1/watchlist/plates/bulk-import",
    dependencies=[Depends(require_permission("watchlist:manage"))],
    summary="Bulk import plate watchlist entries from a CSV file",
)
async def bulk_import_plates(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Upload a CSV with columns: plate_number, list_type, reason (opt), expires_at (opt ISO date).
    Returns { total, imported, duplicates, errors }.
    """
    content = await read_upload_limited(file, MAX_CSV_UPLOAD_BYTES)
    try:
        text_content = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text_content))
    if reader.fieldnames is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty or invalid CSV file")

    headers = {h.strip().lower() for h in reader.fieldnames}
    missing = {"plate_number", "list_type"} - headers
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Missing required columns: {sorted(missing)}")

    rows = list(reader)
    if len(rows) > _BULK_MAX_ROWS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Maximum {_BULK_MAX_ROWS} rows per import")

    valid_rows: list[dict] = []
    errors: list[dict] = []

    for i, row in enumerate(rows, start=2):
        plate = (row.get("plate_number") or "").strip().upper()
        list_type = (row.get("list_type") or "").strip().lower()
        reason = (row.get("reason") or "").strip() or None
        expires_raw = (row.get("expires_at") or "").strip() or None

        if not plate:
            errors.append({"row": i, "error": "plate_number is required"})
            continue
        if list_type not in ("allow", "block"):
            errors.append({"row": i, "error": f"list_type must be 'allow' or 'block', got '{list_type}'"})
            continue

        expires_at = None
        if expires_raw:
            try:
                expires_at = datetime.fromisoformat(expires_raw)
            except ValueError:
                errors.append({"row": i, "error": f"Invalid expires_at: '{expires_raw}' (use ISO format)"})
                continue

        valid_rows.append({"plate": plate, "list_type": list_type, "reason": reason, "expires_at": expires_at})

    imported = 0
    duplicates = 0
    for vrow in valid_rows:
        result = await db.execute(
            text("""
                INSERT INTO watchlist_entries
                    (tenant_id, plate_number, list_type, reason, added_by_user_id, expires_at)
                VALUES (current_setting('app.current_tenant')::uuid,
                        :plate, :list_type, :reason, CAST(:uid AS uuid), :expires_at)
                ON CONFLICT (tenant_id, plate_number, list_type) DO NOTHING
                RETURNING id
            """),
            {
                "plate": vrow["plate"],
                "list_type": vrow["list_type"],
                "reason": vrow["reason"],
                "uid": token.user_id,
                "expires_at": vrow["expires_at"],
            },
        )
        if result.first() is None:
            duplicates += 1
        else:
            imported += 1

    await db.commit()
    return {"total": len(rows), "imported": imported, "duplicates": duplicates, "errors": errors}


@router.post(
    "/api/v1/watchlist/faces/enroll",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("watchlist:manage"))],
    summary="Enroll a face from an uploaded photo",
    description=(
        "Accepts a JPEG/PNG photo, extracts the ArcFace embedding automatically "
        "using the same buffalo_l model the AI workers use for matching, and saves "
        "the entry to the face watchlist. The first call downloads the model (~330 MB) "
        "— subsequent calls are fast."
    ),
)
async def enroll_face(
    person_name: str = Form(...),
    list_type: str = Form(..., pattern="^(allow|block)$"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if file.content_type not in ("image/jpeg", "image/jpg", "image/png"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only JPEG/PNG images are accepted")
    image_bytes = await read_upload_limited(file, MAX_IMAGE_UPLOAD_BYTES)
    try:
        embedding = await asyncio.to_thread(_extract_embedding_sync, image_bytes)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    embedding_vec = "[" + ",".join(str(v) for v in embedding) + "]"
    result = await db.execute(
        text(
            "INSERT INTO face_watchlist_entries (tenant_id, person_name, embedding_v, list_type) "
            "VALUES (current_setting('app.current_tenant')::uuid, :name, CAST(:embedding_v AS vector), :list_type) "
            "RETURNING id"
        ),
        {"name": person_name, "embedding_v": embedding_vec, "list_type": list_type},
    )
    new_id = result.scalar_one()
    await db.commit()
    return {"id": new_id, "person_name": person_name, "list_type": list_type}
