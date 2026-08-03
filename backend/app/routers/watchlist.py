import asyncio
import csv
import io
from datetime import date, datetime

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


# Vehicle registry categories (migration 0076). `list_type` is no longer set
# by callers: a database trigger derives it from `category` so the LPR
# worker's coarse allow/block lookup can never contradict the category an
# admin actually chose. See the migration docstring for why the split exists.
VEHICLE_CATEGORIES = (
    "whitelist", "blacklist", "watchlist", "vip", "staff",
    "visitor", "contractor", "emergency", "government", "unknown",
)

_REGISTRY_COLUMNS = (
    "id, plate_number, list_type, category, owner_name, company, vehicle_type, "
    "vehicle_color, valid_from, valid_to, remarks, reason, is_active, expires_at, created_at"
)


class PlateWatchlistCreate(BaseModel):
    plate_number: str
    category: str = "watchlist"
    reason: str | None = None
    owner_name: str | None = None
    company: str | None = None
    vehicle_type: str | None = None
    vehicle_color: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    remarks: str | None = None
    # Accepted for backward compatibility with any existing caller that still
    # sends the old binary field. Ignored — the trigger owns list_type now.
    list_type: str | None = None


class PlateWatchlistUpdate(BaseModel):
    plate_number: str | None = None
    category: str | None = None
    reason: str | None = None
    owner_name: str | None = None
    company: str | None = None
    vehicle_type: str | None = None
    vehicle_color: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    remarks: str | None = None
    is_active: bool | None = None


def _validate_category(category: str) -> None:
    if category not in VEHICLE_CATEGORIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown category '{category}'; expected one of: {', '.join(VEHICLE_CATEGORIES)}",
        )


class FaceWatchlistCreate(BaseModel):
    person_name: str
    list_type: str
    embedding: list[float]  # pre-computed elsewhere (e.g. an enrollment flow) — Phase 1 takes the vector directly


@router.get("/api/v1/watchlist/plates", dependencies=[Depends(require_permission("watchlist:manage"))])
async def list_plate_watchlist(
    category: str | None = None,
    search: str | None = None,
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    clauses, params = [], {}
    if category:
        _validate_category(category)
        clauses.append("category = :category")
        params["category"] = category
    if is_active is not None:
        clauses.append("is_active = :is_active")
        params["is_active"] = is_active
    if search:
        # Matches the fields an operator actually searches by at a gate: the
        # plate itself, who owns it, or which company it belongs to.
        clauses.append(
            "(plate_number ILIKE :q OR owner_name ILIKE :q OR company ILIKE :q)"
        )
        params["q"] = f"%{search}%"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    result = await db.execute(
        text(f"SELECT {_REGISTRY_COLUMNS} FROM watchlist_entries {where} ORDER BY created_at DESC"),
        params,
    )
    return [dict(row._mapping) for row in result]


@router.post("/api/v1/watchlist/plates", status_code=201, dependencies=[Depends(require_permission("watchlist:manage"))])
async def create_plate_watchlist_entry(
    body: PlateWatchlistCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _validate_category(body.category)
    if body.valid_from and body.valid_to and body.valid_to < body.valid_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "valid_to cannot be earlier than valid_from"
        )
    # list_type is intentionally absent from this INSERT — the trigger added in
    # migration 0076 derives it from category. Setting it here would just be
    # overwritten, and pretending otherwise would mislead the next reader.
    result = await db.execute(
        text(
            f"""
            INSERT INTO watchlist_entries (
                tenant_id, plate_number, list_type, category, reason, owner_name, company,
                vehicle_type, vehicle_color, valid_from, valid_to, remarks, added_by_user_id
            ) VALUES (
                current_setting('app.current_tenant')::uuid,
                :plate, 'allow', :category, :reason, :owner_name, :company,
                :vehicle_type, :vehicle_color, :valid_from, :valid_to, :remarks, :uid
            )
            RETURNING {_REGISTRY_COLUMNS}
            """
        ),
        {
            "plate": body.plate_number.strip().upper(),
            "category": body.category,
            "reason": body.reason,
            "owner_name": body.owner_name,
            "company": body.company,
            "vehicle_type": body.vehicle_type,
            "vehicle_color": body.vehicle_color,
            "valid_from": body.valid_from,
            "valid_to": body.valid_to,
            "remarks": body.remarks,
            "uid": token.user_id,
        },
    )
    row = result.mappings().first()
    await db.commit()
    return dict(row)


@router.put("/api/v1/watchlist/plates/{entry_id}", dependencies=[Depends(require_permission("watchlist:manage"))])
async def update_plate_watchlist_entry(
    entry_id: str,
    body: PlateWatchlistUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Editing a registry entry — previously impossible, so correcting an
    owner's name or extending a pass meant deleting and re-adding the vehicle
    and losing its history."""
    fields = body.model_dump(exclude_unset=True)
    if "category" in fields and fields["category"] is not None:
        _validate_category(fields["category"])
    if "plate_number" in fields and fields["plate_number"]:
        fields["plate_number"] = fields["plate_number"].strip().upper()
    if not fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no fields to update")

    sets = ", ".join(f"{k} = :{k}" for k in fields)
    result = await db.execute(
        text(
            f"""
            UPDATE watchlist_entries SET {sets}, updated_at = now()
            WHERE id = CAST(:entry_id AS uuid)
            RETURNING {_REGISTRY_COLUMNS}
            """
        ),
        {**fields, "entry_id": entry_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registry entry not found")
    await db.commit()
    return dict(row)


@router.delete("/api/v1/watchlist/plates/{entry_id}", dependencies=[Depends(require_permission("watchlist:manage"))])
async def deactivate_plate_watchlist_entry(entry_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(
        text("UPDATE watchlist_entries SET is_active = FALSE WHERE id = CAST(:id AS uuid)"),
        {"id": entry_id},
    )
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
