"""Lost and found — what was handed in, where it is, and who took it away.

Kept on paper today at almost every site, which makes two things impossible:
finding out whether the wallet somebody is asking about was ever handed in,
and proving later that it was released to its owner rather than to whoever
described it best.

PERSONAL DATA. A claimant's name, contact and identity document are collected
for exactly one purpose — establishing that the right person took the item —
and only the last four digits of the document are ever stored. Checking a NRIC
against the card in somebody's hand is the verification; keeping a copy of it
in a table nobody prunes is a liability with no matching benefit.

RETENTION. Items are held for a period and then disposed of. The register
reports what is past that period, because an item held forever is a PDPA
problem quietly accruing and a paper book never surfaces it.

Permissions:
  lostfound:read   — see the register (all ops roles + viewer)
  lostfound:log    — book items in, and release them to a claimant
  lostfound:manage — dispose of unclaimed property (supervisors and up)
Site scoping (Gap 81) applies to every read path.
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.uploads import MAX_IMAGE_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/lost-found", tags=["guard-ops"])

VALID_CATEGORIES = {"wallet", "phone", "keys", "bag", "clothing",
                    "jewellery", "documents", "electronics", "other"}
VALID_STATUSES = {"held", "claimed", "disposed", "handed_to_police"}

# How long an unclaimed item is held before it is due for disposal. Ninety
# days is the common practice at Singapore malls and condominiums; a tenant
# whose client contract says otherwise overrides it.
RETENTION_DAYS_KEY = "lostfound.retention_days"
RETENTION_DAYS_DEFAULT = 90

_ALLOWED_PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}


async def _retention_days(db: AsyncSession) -> int:
    row = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": RETENTION_DAYS_KEY},
    )).first()
    # bool is an int subclass, so a JSONB `true` would otherwise become 1 day.
    if row is not None and isinstance(row[0], int) and not isinstance(row[0], bool) and row[0] > 0:
        return row[0]
    return RETENTION_DAYS_DEFAULT


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1)
    category: str = "other"
    site_id: str | None = None
    found_location: str | None = None
    found_at: datetime | None = None
    storage_location: str | None = Field(default=None, max_length=160)
    notes: str | None = None


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, min_length=1)
    category: str | None = None
    found_location: str | None = None
    storage_location: str | None = Field(default=None, max_length=160)
    notes: str | None = None


class ItemRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claimed_by_name: str = Field(min_length=1, max_length=160)
    claimed_by_contact: str | None = Field(default=None, max_length=60)
    claimed_id_type: str | None = Field(default=None, max_length=40)
    # Last four characters only. Never the whole document.
    claimed_id_last4: str | None = Field(default=None, max_length=8)
    notes: str | None = None


class ItemDispose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposal_method: str = Field(min_length=1, max_length=80)
    # A police handover is a disposal with a different name on it, and the
    # distinction matters when somebody comes back asking where their
    # passport went.
    handed_to_police: bool = False
    notes: str | None = None


_ITEM_SELECT = """
    SELECT i.id, i.site_id, i.description, i.category, i.found_location,
           i.found_at, i.storage_location, i.status, i.notes,
           (i.photo_path IS NOT NULL) AS has_photo,
           i.claimed_by_name, i.claimed_by_contact,
           i.claimed_id_type, i.claimed_id_last4, i.released_at,
           i.disposed_at, i.disposal_method,
           i.created_at, i.updated_at,
           s.name AS site_name,
           fu.full_name AS found_by_name,
           ru.full_name AS released_by_name,
           EXTRACT(EPOCH FROM (now() - i.found_at)) / 86400.0 AS days_held
      FROM lost_found_items i
 LEFT JOIN sites s ON s.id = i.site_id
 LEFT JOIN users fu ON fu.id = i.found_by_user_id
 LEFT JOIN users ru ON ru.id = i.released_by_user_id
"""


def _check_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"category must be one of {sorted(VALID_CATEGORIES)}")


async def _load_item(db: AsyncSession, item_id: str, allowed_sites: list[str] | None):
    row = (await db.execute(
        text("SELECT id, site_id, status, photo_path FROM lost_found_items "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": item_id},
    )).first()
    # A restricted user may still see items logged with no site — nothing
    # about them is site-specific, and hiding them would lose the item.
    if row is None or (row.site_id is not None
                       and not is_site_allowed(allowed_sites, row.site_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    return row


@router.get("", dependencies=[Depends(require_permission("lostfound:read"))])
async def list_items(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    status_filter: str | None = None,
    category: str | None = None,
    search: str | None = None,
    overdue_only: bool = False,
    limit: int = 200,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """The register. Held items first and oldest first within that, because
    the oldest held item is the one somebody has to make a decision about."""
    limit = max(1, min(limit, 500))
    days = await _retention_days(db)
    where, params = [], {"lim": limit, "days": days}

    if status_filter:
        if status_filter not in VALID_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"status must be one of {sorted(VALID_STATUSES)}")
        where.append("i.status = :st"); params["st"] = status_filter
    if category:
        _check_category(category)
        where.append("i.category = :cat"); params["cat"] = category
    if site_id:
        where.append("i.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if search:
        where.append("(i.description ILIKE :q OR i.found_location ILIKE :q "
                     "OR i.storage_location ILIKE :q)")
        params["q"] = f"%{search}%"
    if overdue_only:
        where.append("i.status = 'held' AND i.found_at < now() - make_interval(days => :days)")
    scope = site_scope_clause(allowed_sites, "i.site_id", params)
    if scope:
        # Items with no site stay visible; see _load_item.
        where.append(f"(i.site_id IS NULL OR {scope})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_ITEM_SELECT}
            {clause}
            ORDER BY (i.status = 'held') DESC, i.found_at
            LIMIT :lim
        """),
        params,
    )
    rows = []
    for r in result.mappings():
        row = dict(r)
        row["due_for_disposal"] = (row["status"] == "held"
                                   and (row["days_held"] or 0) >= days)
        rows.append(row)
    return rows


@router.get("/summary", dependencies=[Depends(require_permission("lostfound:read"))])
async def register_summary(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Counts for the page header, and the number nobody wants to see grow:
    items held past the retention period.

    Declared before /{item_id} — FastAPI matches in declaration order.
    """
    days = await _retention_days(db)
    where, params = [], {"days": days}
    if site_id:
        where.append("i.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "i.site_id", params)
    if scope:
        where.append(f"(i.site_id IS NULL OR {scope})")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    row = (await db.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE i.status = 'held')::int   AS held,
                   COUNT(*) FILTER (WHERE i.status = 'claimed')::int AS claimed,
                   COUNT(*) FILTER (WHERE i.status = 'disposed')::int AS disposed,
                   COUNT(*) FILTER (WHERE i.status = 'handed_to_police')::int
                       AS handed_to_police,
                   COUNT(*) FILTER (
                       WHERE i.status = 'held'
                         AND i.found_at < now() - make_interval(days => :days)
                   )::int AS due_for_disposal
              FROM lost_found_items i
            {clause}
        """),
        params,
    )).mappings().first()
    return {**dict(row), "retention_days": days}


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("lostfound:log"))])
async def log_item(
    body: ItemCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    _check_category(body.category)
    if body.site_id is not None:
        if not is_site_allowed(allowed_sites, body.site_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
        if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                             {"id": body.site_id})).first() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    if body.found_at is not None:
        found = body.found_at
        if found.tzinfo is None:
            found = found.replace(tzinfo=timezone.utc)
        if found > datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "found_at is in the future")

    result = await db.execute(
        text("""
            INSERT INTO lost_found_items
                (tenant_id, site_id, description, category, found_location,
                 found_at, found_by_user_id, storage_location, notes)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:site AS uuid), :descr, :cat, :loc,
                    COALESCE(:found_at, now()), CAST(:uid AS uuid), :storage, :notes)
            RETURNING id, site_id, description, category, found_location, found_at,
                      storage_location, status, notes, created_at
        """),
        {"site": body.site_id, "descr": body.description, "cat": body.category,
         "loc": body.found_location, "found_at": body.found_at,
         "uid": token.user_id, "storage": body.storage_location, "notes": body.notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.get("/{item_id}", dependencies=[Depends(require_permission("lostfound:read"))])
async def get_item(
    item_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(
        text(f"{_ITEM_SELECT} WHERE i.id = CAST(:id AS uuid)"),
        {"id": item_id},
    )).mappings().first()
    if row is None or (row["site_id"] is not None
                       and not is_site_allowed(allowed_sites, row["site_id"])):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    days = await _retention_days(db)
    item = dict(row)
    item["due_for_disposal"] = item["status"] == "held" and (item["days_held"] or 0) >= days
    item["retention_days"] = days
    return item


@router.put("/{item_id}", dependencies=[Depends(require_permission("lostfound:log"))])
async def update_item(
    item_id: str,
    body: ItemUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Correct the description or move the item to a different shelf.

    Only while it is held: editing what an item was after releasing it would
    change the record of what somebody signed for.
    """
    existing = await _load_item(db, item_id, allowed_sites)
    if existing.status != "held":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This item is {existing.status.replace('_', ' ')} "
                            "— its record is closed")
    if body.category is not None:
        _check_category(body.category)

    sets, params = [], {"id": item_id}
    if body.description is not None:
        sets.append("description = :descr"); params["descr"] = body.description
    if body.category is not None:
        sets.append("category = :cat"); params["cat"] = body.category
    if body.found_location is not None:
        sets.append("found_location = :loc"); params["loc"] = body.found_location
    if body.storage_location is not None:
        sets.append("storage_location = :storage"); params["storage"] = body.storage_location
    if body.notes is not None:
        sets.append("notes = :notes"); params["notes"] = body.notes
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    sets.append("updated_at = now()")

    result = await db.execute(
        text(f"""
            UPDATE lost_found_items SET {', '.join(sets)}
             WHERE id = CAST(:id AS uuid)
            RETURNING id, site_id, description, category, found_location,
                      storage_location, status, notes, updated_at
        """),
        params,
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{item_id}/release", dependencies=[Depends(require_permission("lostfound:log"))])
async def release_item(
    item_id: str,
    body: ItemRelease,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Hand the item to whoever claimed it, and record who that was."""
    existing = await _load_item(db, item_id, allowed_sites)
    if existing.status != "held":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This item is already {existing.status.replace('_', ' ')}",
        )
    # Guard against a whole NRIC arriving in the field meant for four digits.
    if body.claimed_id_last4 and len(body.claimed_id_last4.strip()) > 8:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Record only the last few characters of the document")

    result = await db.execute(
        text("""
            UPDATE lost_found_items
               SET status = 'claimed',
                   claimed_by_name = :name,
                   claimed_by_contact = :contact,
                   claimed_id_type = :id_type,
                   claimed_id_last4 = :id_last4,
                   released_by_user_id = CAST(:uid AS uuid),
                   released_at = now(),
                   notes = COALESCE(:notes, notes),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, description, status, claimed_by_name, claimed_by_contact,
                      claimed_id_type, claimed_id_last4, released_at
        """),
        {"id": item_id, "name": body.claimed_by_name, "contact": body.claimed_by_contact,
         "id_type": body.claimed_id_type, "id_last4": body.claimed_id_last4,
         "uid": token.user_id, "notes": body.notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{item_id}/dispose", dependencies=[Depends(require_permission("lostfound:manage"))])
async def dispose_item(
    item_id: str,
    body: ItemDispose,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Close out an item nobody claimed.

    Requires a method, because "disposed" on its own is not an answer to
    "where is my passport" and this is the row that has to answer it.
    """
    existing = await _load_item(db, item_id, allowed_sites)
    if existing.status != "held":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This item is already {existing.status.replace('_', ' ')}",
        )
    new_status = "handed_to_police" if body.handed_to_police else "disposed"

    result = await db.execute(
        text("""
            UPDATE lost_found_items
               SET status = :st,
                   disposed_at = now(),
                   disposal_method = :method,
                   notes = COALESCE(:notes, notes),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, description, status, disposed_at, disposal_method
        """),
        {"id": item_id, "st": new_status, "method": body.disposal_method,
         "notes": body.notes},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{item_id}/photo", dependencies=[Depends(require_permission("lostfound:log"))])
async def upload_photo(
    item_id: str,
    photo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """A photo of the item as handed in. This is what settles a disputed
    claim, so it is taken at the counter and not reconstructed later."""
    existing = await _load_item(db, item_id, allowed_sites)
    suffix = _ALLOWED_PHOTO_TYPES.get(photo.content_type or "")
    if suffix is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Photo must be a JPEG or PNG")
    image_bytes = await read_upload_limited(photo, MAX_IMAGE_UPLOAD_BYTES)

    relative_path = f"{token.tenant_id}/{item_id}{suffix}"
    dest = Path(settings.LOST_FOUND_PHOTOS_ROOT) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image_bytes)

    # Replacing a JPEG with a PNG leaves the old file behind; remove it so the
    # only image on disk is the one the row points at.
    if existing.photo_path and existing.photo_path != relative_path:
        old = Path(settings.LOST_FOUND_PHOTOS_ROOT) / existing.photo_path
        old.unlink(missing_ok=True)

    await db.execute(
        text("UPDATE lost_found_items SET photo_path = :p, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"p": relative_path, "id": item_id},
    )
    await db.commit()
    return {"id": item_id, "has_photo": True}


@router.get("/{item_id}/photo", dependencies=[Depends(require_permission("lostfound:read"))])
async def get_photo(
    item_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = await _load_item(db, item_id, allowed_sites)
    if not existing.photo_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No photo for this item")
    file_path = Path(settings.LOST_FOUND_PHOTOS_ROOT) / existing.photo_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo file not found on disk")
    media = "image/png" if file_path.suffix == ".png" else "image/jpeg"
    return FileResponse(str(file_path), media_type=media)
