"""The facility defect log — what your officers found, and what happened about it.

A guard finds a blown stairwell light or a leak in the car park, writes it in
the occurrence book, tells the building's FM desk, and there it ends. Nothing
tracks whether it was fixed. At contract review the client asks for exactly
this list, and the answer today is a stack of photocopied pages.

THE STATUS LADDER STOPS AT THE BOUNDARY OF WHAT A SECURITY COMPANY CONTROLS.
A guard does not repair a lift. They report it, chase it, and see when it is
done — so the row carries who it was referred to and the building's own
reference number, which is the thing you quote when chasing.

SEVERITY IS NOT STATUS. A safety hazard that is already fixed needs no
attention; a low-priority defect open for six weeks does. The list sorts on
what is open first and severity within that, so neither dimension hides the
other.

Permissions:
  defect:read   — see the log (all ops roles, viewer, and the client)
  defect:log    — report a defect (guards, because they are the ones walking)
  defect:manage — refer, resolve, close (supervisors and up)
Site scoping (Gap 81) applies to every path.
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

router = APIRouter(prefix="/api/v1/defects", tags=["guard-ops"])

VALID_CATEGORIES = {"lighting", "plumbing", "electrical", "lift", "door_access",
                    "cctv", "fire_safety", "structural", "cleanliness",
                    "landscaping", "other"}
VALID_SEVERITIES = {"low", "medium", "high", "safety_hazard"}
VALID_STATUSES = {"open", "reported", "in_progress", "resolved", "closed"}

# Anything in these three still wants somebody to do something.
ACTIVE_STATUSES = ("open", "reported", "in_progress")

_ALLOWED_PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}


class DefectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_id: str
    description: str = Field(min_length=1)
    category: str = "other"
    location: str | None = None
    severity: str = "medium"
    shift_id: str | None = None
    reported_at: datetime | None = None


class DefectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, min_length=1)
    category: str | None = None
    location: str | None = None
    severity: str | None = None


class DefectRefer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    referred_to: str = Field(min_length=1, max_length=160)
    reference_no: str | None = Field(default=None, max_length=80)
    # A building that has accepted the job is further along than one that has
    # merely been told, and a chasing list wants to tell them apart.
    in_progress: bool = False


class DefectResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_notes: str | None = None
    # Closing without a fix — a duplicate, or not the client's scope. Recorded
    # as its own thing so "resolved" keeps meaning "actually fixed".
    close_without_fix: bool = False


_DEFECT_SELECT = """
    SELECT d.id, d.site_id, d.shift_id, d.category, d.location, d.description,
           d.severity, d.status, d.reported_at, d.referred_to, d.referred_at,
           d.reference_no, d.resolved_at, d.resolution_notes,
           d.created_at, d.updated_at,
           (d.photo_path IS NOT NULL) AS has_photo,
           s.name AS site_name,
           ru.full_name AS reported_by_name,
           fu.full_name AS resolved_by_name,
           EXTRACT(EPOCH FROM (COALESCE(d.resolved_at, now()) - d.reported_at)) / 86400.0
               AS days_open
      FROM facility_defects d
      JOIN sites s ON s.id = d.site_id
 LEFT JOIN users ru ON ru.id = d.reported_by_user_id
 LEFT JOIN users fu ON fu.id = d.resolved_by_user_id
"""


def _check_enum(value: str, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field} must be one of {sorted(allowed)}")


async def _load(db: AsyncSession, defect_id: str, allowed_sites: list[str] | None):
    row = (await db.execute(
        text("SELECT id, site_id, status, photo_path FROM facility_defects "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": defect_id},
    )).first()
    if row is None or not is_site_allowed(allowed_sites, row.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Defect not found")
    return row


@router.get("", dependencies=[Depends(require_permission("defect:read"))])
async def list_defects(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    status_filter: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    active_only: bool = False,
    limit: int = 200,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Open first, worst first, oldest first. Somebody working this list is
    working the top of it."""
    limit = max(1, min(limit, 500))
    where, params = [], {"lim": limit}

    if status_filter:
        _check_enum(status_filter, VALID_STATUSES, "status")
        where.append("d.status = :st"); params["st"] = status_filter
    if active_only:
        where.append("d.status = ANY(:active)"); params["active"] = list(ACTIVE_STATUSES)
    if category:
        _check_enum(category, VALID_CATEGORIES, "category")
        where.append("d.category = :cat"); params["cat"] = category
    if severity:
        _check_enum(severity, VALID_SEVERITIES, "severity")
        where.append("d.severity = :sev"); params["sev"] = severity
    if site_id:
        where.append("d.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "d.site_id", params)
    if scope:
        where.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    result = await db.execute(
        text(f"""
            {_DEFECT_SELECT}
            {clause}
            ORDER BY (d.status = ANY(ARRAY['open','reported','in_progress'])) DESC,
                     CASE d.severity WHEN 'safety_hazard' THEN 0 WHEN 'high' THEN 1
                                     WHEN 'medium' THEN 2 ELSE 3 END,
                     d.reported_at
            LIMIT :lim
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


@router.get("/summary", dependencies=[Depends(require_permission("defect:read"))])
async def defect_summary(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Counts for the page header, and the two numbers a supervisor is judged
    on: safety hazards still open, and anything open longer than a fortnight.

    Declared before /{defect_id} — FastAPI matches in declaration order.
    """
    where, params = [], {}
    if site_id:
        where.append("d.site_id = CAST(:site_id AS uuid)"); params["site_id"] = site_id
    scope = site_scope_clause(allowed_sites, "d.site_id", params)
    if scope:
        where.append(scope)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    row = (await db.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE d.status = 'open')::int        AS open,
                   COUNT(*) FILTER (WHERE d.status = 'reported')::int    AS reported,
                   COUNT(*) FILTER (WHERE d.status = 'in_progress')::int AS in_progress,
                   COUNT(*) FILTER (WHERE d.status = 'resolved')::int    AS resolved,
                   COUNT(*) FILTER (WHERE d.status = 'closed')::int      AS closed,
                   COUNT(*) FILTER (
                       WHERE d.severity = 'safety_hazard'
                         AND d.status = ANY(ARRAY['open','reported','in_progress'])
                   )::int AS open_safety_hazards,
                   COUNT(*) FILTER (
                       WHERE d.status = ANY(ARRAY['open','reported','in_progress'])
                         AND d.reported_at < now() - interval '14 days'
                   )::int AS ageing
              FROM facility_defects d
            {clause}
        """),
        params,
    )).mappings().first()
    return dict(row)


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("defect:log"))])
async def report_defect(
    body: DefectCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    _check_enum(body.category, VALID_CATEGORIES, "category")
    _check_enum(body.severity, VALID_SEVERITIES, "severity")
    if not is_site_allowed(allowed_sites, body.site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:id AS uuid)"),
                         {"id": body.site_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")
    if body.reported_at is not None:
        found = body.reported_at
        if found.tzinfo is None:
            found = found.replace(tzinfo=timezone.utc)
        if found > datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "reported_at is in the future")

    result = await db.execute(
        text("""
            INSERT INTO facility_defects
                (tenant_id, site_id, shift_id, category, location, description,
                 severity, reported_by_user_id, reported_at)
            VALUES (current_setting('app.current_tenant')::uuid,
                    CAST(:site AS uuid), CAST(:shift AS uuid), :cat, :loc, :descr,
                    :sev, CAST(:uid AS uuid), COALESCE(:at, now()))
            RETURNING id, site_id, shift_id, category, location, description,
                      severity, status, reported_at, created_at
        """),
        {"site": body.site_id, "shift": body.shift_id, "cat": body.category,
         "loc": body.location, "descr": body.description, "sev": body.severity,
         "uid": token.user_id, "at": body.reported_at},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.get("/{defect_id}", dependencies=[Depends(require_permission("defect:read"))])
async def get_defect(
    defect_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    row = (await db.execute(
        text(f"{_DEFECT_SELECT} WHERE d.id = CAST(:id AS uuid)"),
        {"id": defect_id},
    )).mappings().first()
    if row is None or not is_site_allowed(allowed_sites, row["site_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Defect not found")
    return dict(row)


@router.put("/{defect_id}", dependencies=[Depends(require_permission("defect:log"))])
async def update_defect(
    defect_id: str,
    body: DefectUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Correct the description or raise the severity while it is still live.

    A closed record is left alone: it is what was reported to the building, and
    editing it afterwards would make the reference number point at something
    else.
    """
    existing = await _load(db, defect_id, allowed_sites)
    if existing.status in ("resolved", "closed"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This defect is {existing.status} — its record is closed")
    if body.category is not None:
        _check_enum(body.category, VALID_CATEGORIES, "category")
    if body.severity is not None:
        _check_enum(body.severity, VALID_SEVERITIES, "severity")

    sets, params = [], {"id": defect_id}
    if body.description is not None:
        sets.append("description = :descr"); params["descr"] = body.description
    if body.category is not None:
        sets.append("category = :cat"); params["cat"] = body.category
    if body.location is not None:
        sets.append("location = :loc"); params["loc"] = body.location
    if body.severity is not None:
        sets.append("severity = :sev"); params["sev"] = body.severity
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")
    sets.append("updated_at = now()")

    result = await db.execute(
        text(f"""
            UPDATE facility_defects SET {', '.join(sets)}
             WHERE id = CAST(:id AS uuid)
            RETURNING id, site_id, category, location, description, severity,
                      status, updated_at
        """),
        params,
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{defect_id}/refer", dependencies=[Depends(require_permission("defect:manage"))])
async def refer_defect(
    defect_id: str,
    body: DefectRefer,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """Hand it to whoever owns the building, and record their ticket number.

    Re-referring an already-referred defect is allowed on purpose: the building
    reassigns it to a contractor, and the reference number changes with it.
    """
    existing = await _load(db, defect_id, allowed_sites)
    if existing.status in ("resolved", "closed"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This defect is already {existing.status}")

    result = await db.execute(
        text("""
            UPDATE facility_defects
               SET status = :st,
                   referred_to = :to,
                   reference_no = COALESCE(:ref, reference_no),
                   referred_at = COALESCE(referred_at, now()),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, status, referred_to, referred_at, reference_no
        """),
        {"id": defect_id, "to": body.referred_to, "ref": body.reference_no,
         "st": "in_progress" if body.in_progress else "reported"},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{defect_id}/resolve", dependencies=[Depends(require_permission("defect:manage"))])
async def resolve_defect(
    defect_id: str,
    body: DefectResolve,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = await _load(db, defect_id, allowed_sites)
    if existing.status in ("resolved", "closed"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This defect is already {existing.status}")
    if body.close_without_fix and not (body.resolution_notes or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Say why this is being closed without a fix — the client will ask",
        )

    result = await db.execute(
        text("""
            UPDATE facility_defects
               SET status = :st,
                   resolved_at = now(),
                   resolved_by_user_id = CAST(:uid AS uuid),
                   resolution_notes = COALESCE(:notes, resolution_notes),
                   updated_at = now()
             WHERE id = CAST(:id AS uuid)
            RETURNING id, status, resolved_at, resolution_notes,
                      EXTRACT(EPOCH FROM (resolved_at - reported_at)) / 86400.0 AS days_open
        """),
        {"id": defect_id, "uid": token.user_id, "notes": body.resolution_notes,
         "st": "closed" if body.close_without_fix else "resolved"},
    )
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.post("/{defect_id}/photo", dependencies=[Depends(require_permission("defect:log"))])
async def upload_defect_photo(
    defect_id: str,
    photo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    """A photo of the defect. It is the difference between "the light is out"
    and a building manager knowing which light."""
    existing = await _load(db, defect_id, allowed_sites)
    suffix = _ALLOWED_PHOTO_TYPES.get(photo.content_type or "")
    if suffix is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Photo must be a JPEG or PNG")
    image_bytes = await read_upload_limited(photo, MAX_IMAGE_UPLOAD_BYTES)

    relative_path = f"{token.tenant_id}/defects/{defect_id}{suffix}"
    dest = Path(settings.GUARDHOUSE_PHOTOS_ROOT) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image_bytes)

    if existing.photo_path and existing.photo_path != relative_path:
        (Path(settings.GUARDHOUSE_PHOTOS_ROOT) / existing.photo_path).unlink(missing_ok=True)

    await db.execute(
        text("UPDATE facility_defects SET photo_path = :p, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"p": relative_path, "id": defect_id},
    )
    await db.commit()
    return {"id": defect_id, "has_photo": True}


@router.get("/{defect_id}/photo", dependencies=[Depends(require_permission("defect:read"))])
async def get_defect_photo(
    defect_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    existing = await _load(db, defect_id, allowed_sites)
    if not existing.photo_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No photo for this defect")
    file_path = Path(settings.GUARDHOUSE_PHOTOS_ROOT) / existing.photo_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo file not found on disk")
    media = "image/png" if file_path.suffix == ".png" else "image/jpeg"
    return FileResponse(str(file_path), media_type=media)
