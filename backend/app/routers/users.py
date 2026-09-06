import datetime
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import InvalidTokenError, decode_access_token, hash_password
from app.core.uploads import MAX_DOCUMENT_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.db.session import AsyncSessionLocal
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/users", tags=["users"])

WorkPassType = Literal["citizen", "pr", "ep", "sp", "wp"]
EmploymentType = Literal["full_time", "part_time", "contract"]

# Employee profile fields shared by UserCreate/UserUpdate and the SELECT
# column lists below (ShiftSecure Phase 1 — see plan). Nullable, filled in
# incrementally via PUT.
_EMPLOYEE_FIELDS = (
    "nric_fin", "date_of_birth", "nationality", "phone", "address",
    "work_pass_type", "work_pass_expiry", "employment_type", "designation",
    "department", "date_joined", "bank_name", "bank_account_number",
    "emergency_contact_name", "emergency_contact_phone",
    "hourly_rate", "daily_rate", "monthly_salary",
    "profile_photo_path",
)


class UserCreate(BaseModel):
    email: str
    password: str
    role_id: int
    full_name: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role_id: int | None = None
    is_active: bool | None = None
    new_password: str | None = None
    locale: str | None = None
    nric_fin: str | None = None
    date_of_birth: datetime.date | None = None
    nationality: str | None = None
    phone: str | None = None
    address: str | None = None
    work_pass_type: WorkPassType | None = None
    work_pass_expiry: datetime.date | None = None
    employment_type: EmploymentType | None = None
    designation: str | None = None
    department: str | None = None
    date_joined: datetime.date | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    hourly_rate: float | None = None
    daily_rate: float | None = None
    monthly_salary: float | None = None


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    d.pop("hashed_password", None)
    return d


_USER_SELECT_COLUMNS = (
    "id, tenant_id, role_id, email, full_name, is_active, locale, last_login_at, created_at, updated_at, "
    + ", ".join(_EMPLOYEE_FIELDS)
)


PLATFORM_ADMIN_ROLE_ID = 1

# Role ids are NOT a seniority ranking, however much 1..7 looks like one — 8 is
# Manager, which the roster service treats as senior to Supervisor (3). So the
# guard below compares against one specific role rather than doing arithmetic on
# role_id, which would silently get Managers wrong.


def _is_platform_admin(token: TokenPayload) -> bool:
    return token.role_id == PLATFORM_ADMIN_ROLE_ID


async def _assert_may_touch(db: AsyncSession, token: TokenPayload, user_id) -> None:
    """Refuse to let anyone but a platform admin act on a platform admin.

    RLS scopes users to a tenant and require_permission asks only "does your
    role hold this code". Neither compares the caller's role to the TARGET's,
    so a tenant Admin holding user:update could act on the super admin sharing
    its tenant — including PUT {"new_password": "..."}, after which it simply
    logs in as them. _assert_assignable_role blocks *becoming* role 1; this
    blocks the shorter route to the same place.

    404 rather than 403: a tenant Admin has no business learning that a
    platform admin exists in their tenant, and 403 confirms it.
    """
    if _is_platform_admin(token):
        return
    target_role = (
        await db.execute(
            text("SELECT role_id FROM users WHERE id = :id"), {"id": user_id}
        )
    ).scalar_one_or_none()
    if target_role == PLATFORM_ADMIN_ROLE_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")


@router.get("", dependencies=[Depends(require_permission("user:read"))])
async def list_users(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    # Site names and document-expiry counts are list-only additions (Guards
    # grid round) — kept out of _USER_SELECT_COLUMNS so get_user/create_user
    # stay simple, unaliased single-table queries. Both LEFT JOIN subqueries
    # rely on the existing RLS policies on user_sites/employee_documents
    # (same pattern as roles.py's role_permissions/permissions joins) — no
    # manual tenant_id filter needed since app.current_tenant is already set.
    result = await db.execute(
        text(f"""
            SELECT {_USER_SELECT_COLUMNS},
                   COALESCE(sn.site_names, '{{}}') AS site_names,
                   COALESCE(dc.total_count, 0)::int AS documents_total_count,
                   COALESCE(dc.expiring_count, 0)::int AS documents_expiring_count,
                   COALESCE(dc.expired_count, 0)::int AS documents_expired_count
            FROM users
            LEFT JOIN (
                SELECT us.user_id, ARRAY_AGG(s.name ORDER BY s.name) AS site_names
                FROM user_sites us JOIN sites s ON s.id = us.site_id
                GROUP BY us.user_id
            ) sn ON sn.user_id = users.id
            LEFT JOIN (
                SELECT ed.user_id,
                       COUNT(*) AS total_count,
                       COUNT(*) FILTER (WHERE ed.expiry_date IS NOT NULL AND ed.expiry_date < CURRENT_DATE) AS expired_count,
                       COUNT(*) FILTER (WHERE ed.expiry_date IS NOT NULL
                                         AND ed.expiry_date >= CURRENT_DATE
                                         AND ed.expiry_date <= CURRENT_DATE + INTERVAL '30 days') AS expiring_count
                FROM employee_documents ed
                GROUP BY ed.user_id
            ) dc ON dc.user_id = users.id
            WHERE (:show_platform_admins OR users.role_id <> :platform_role)
            ORDER BY users.created_at DESC
        """),
        {
            "show_platform_admins": _is_platform_admin(token),
            "platform_role": PLATFORM_ADMIN_ROLE_ID,
        },
    )
    return [dict(row._mapping) for row in result]


async def _assert_assignable_role(db: AsyncSession, role_id: int) -> None:
    """A user may be assigned a built-in role or one of this tenant's own
    custom roles (Gap 91) — never another tenant's custom role, and never
    the platform super_admin role (id 1), which is a cross-tenant role with
    no tenant_id and would otherwise slip through the "built-in role"
    branch below. Without this exclusion, any tenant Admin holding
    user:create/user:update could self-assign role_id=1 and, via
    tenants.py's get_raw_db (which bypasses RLS entirely), read/modify
    every tenant on the platform."""
    if role_id == 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Invalid role_id for this tenant")
    row = (await db.execute(
        text("SELECT 1 FROM roles WHERE id = :id AND "
             "(tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant')::uuid)"),
        {"id": role_id},
    )).first()
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Invalid role_id for this tenant")


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("user:create"))])
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    await _assert_assignable_role(db, body.role_id)
    new_id = uuid.uuid4()
    try:
        result = await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, role_id, email, hashed_password, full_name) "
                "VALUES (:id, current_setting('app.current_tenant')::uuid, :rid, :email, :pw, :name) "
                f"RETURNING {_USER_SELECT_COLUMNS}"
            ),
            {
                "id": new_id,
                "rid": body.role_id,
                "email": body.email,
                "pw": hash_password(body.password),
                "name": body.full_name,
            },
        )
        row = result.first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists in this tenant") from exc
        raise
    return dict(row._mapping)


@router.get("/{user_id}", dependencies=[Depends(require_permission("user:read"))])
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    result = await db.execute(
        text(f"SELECT {_USER_SELECT_COLUMNS} FROM users WHERE id = :id"),
        {"id": user_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return dict(row._mapping)


@router.put("/{user_id}", dependencies=[Depends(require_permission("user:update"))])
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    # Before the role being ASSIGNED is checked, check the user being TOUCHED.
    # Without this, new_password on a platform admin is a complete takeover.
    await _assert_may_touch(db, token, user_id)
    if body.role_id is not None:
        await _assert_assignable_role(db, body.role_id)
    updates = {k: v for k, v in body.model_dump(exclude={"new_password"}).items() if v is not None}
    if body.new_password:
        updates["hashed_password"] = hash_password(body.new_password)
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = user_id
    result = await db.execute(
        text(f"UPDATE users SET {set_clause}, updated_at = now() WHERE id = :id RETURNING id, role_id, full_name, is_active"),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/{user_id}", dependencies=[Depends(require_permission("user:delete"))])
async def deactivate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    result = await db.execute(
        text("UPDATE users SET is_active = FALSE, updated_at = now() WHERE id = :id RETURNING id, is_active"),
        {"id": user_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.commit()
    return dict(row._mapping)


# ── Site assignments (Gap 81 — site-scoped access) ───────────────────────────

class UserSitesBody(BaseModel):
    site_ids: list[uuid.UUID]


@router.get("/{user_id}/sites", dependencies=[Depends(require_permission("user:read"))])
async def get_user_sites(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """List the sites a user is restricted to. Empty list = unrestricted
    (for internal roles) or no visibility (for the client role)."""
    await _assert_may_touch(db, token, user_id)
    user_check = await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})
    if user_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    result = await db.execute(
        text(
            "SELECT us.site_id, s.name AS site_name "
            "FROM user_sites us JOIN sites s ON s.id = us.site_id "
            "WHERE us.user_id = :uid ORDER BY s.name"
        ),
        {"uid": user_id},
    )
    return [dict(r._mapping) for r in result]


@router.put("/{user_id}/sites", dependencies=[Depends(require_permission("user:update"))])
async def set_user_sites(
    user_id: uuid.UUID,
    body: UserSitesBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Replace a user's site assignments. An empty list clears all
    assignments (internal roles become unrestricted; clients lose access)."""
    await _assert_may_touch(db, token, user_id)
    user_check = await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})
    if user_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    if body.site_ids:
        found = await db.execute(
            text("SELECT COUNT(*) FROM sites WHERE id = ANY(:ids)"),
            {"ids": body.site_ids},
        )
        if (found.scalar() or 0) != len(set(body.site_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more sites not found")

    await db.execute(text("DELETE FROM user_sites WHERE user_id = :uid"), {"uid": user_id})
    for sid in set(body.site_ids):
        await db.execute(
            text(
                "INSERT INTO user_sites (user_id, site_id, tenant_id) "
                "VALUES (:uid, :sid, current_setting('app.current_tenant')::uuid)"
            ),
            {"uid": user_id, "sid": sid},
        )
    await db.commit()
    return {"user_id": str(user_id), "site_ids": [str(s) for s in set(body.site_ids)]}


# ── Employee Documents (ShiftSecure Phase 1 — passport/work pass/certs) ──────

_DOCUMENT_TYPES = {"passport", "work_pass", "certification", "other"}


class EmployeeDocumentUpdate(BaseModel):
    document_number: str | None = None
    issuing_body: str | None = None
    issue_date: datetime.date | None = None
    expiry_date: datetime.date | None = None
    notes: str | None = None


@router.get("/{user_id}/documents", dependencies=[Depends(require_permission("user:read"))])
async def get_employee_documents(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    user_check = await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})
    if user_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    result = await db.execute(
        text(
            "SELECT id, user_id, document_type, document_number, issuing_body, issue_date, "
            "expiry_date, storage_path, notes, created_at, updated_at "
            "FROM employee_documents WHERE user_id = :uid ORDER BY created_at DESC"
        ),
        {"uid": user_id},
    )
    return [dict(r._mapping) for r in result]


@router.post(
    "/{user_id}/documents",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("user:update"))],
)
async def upload_employee_document(
    user_id: uuid.UUID,
    document_type: str = Form(...),
    document_number: str | None = Form(None),
    issuing_body: str | None = Form(None),
    issue_date: datetime.date | None = Form(None),
    expiry_date: datetime.date | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if document_type not in _DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"document_type must be one of {sorted(_DOCUMENT_TYPES)}")
    await _assert_may_touch(db, token, user_id)
    user_check = await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})
    if user_check.first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    new_id = uuid.uuid4()
    storage_path: str | None = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix or ".bin"
        relative_path = f"{token.tenant_id}/{user_id}/{new_id}{ext}"
        dest = Path(settings.EMPLOYEE_DOCS_ROOT) / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await read_upload_limited(file, MAX_DOCUMENT_UPLOAD_BYTES))
        storage_path = relative_path

    result = await db.execute(
        text(
            "INSERT INTO employee_documents "
            "(id, tenant_id, user_id, document_type, document_number, issuing_body, issue_date, expiry_date, storage_path, notes, created_by_user_id) "
            "VALUES (:id, current_setting('app.current_tenant')::uuid, :uid, :dtype, :dnum, :issuer, :issue_date, :expiry_date, :storage_path, :notes, :creator) "
            "RETURNING id, user_id, document_type, document_number, issuing_body, issue_date, expiry_date, storage_path, notes, created_at"
        ),
        {
            "id": new_id,
            "uid": user_id,
            "dtype": document_type,
            "dnum": document_number,
            "issuer": issuing_body,
            "issue_date": issue_date,
            "expiry_date": expiry_date,
            "storage_path": storage_path,
            "notes": notes,
            "creator": token.user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/{user_id}/documents/{doc_id}/file", dependencies=[Depends(require_permission("user:read"))])
async def download_employee_document(
    user_id: uuid.UUID, doc_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    row = (
        await db.execute(
            text("SELECT storage_path FROM employee_documents WHERE id = :id AND user_id = :uid"),
            {"id": doc_id, "uid": user_id},
        )
    ).first()
    if row is None or not row.storage_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document file not found")
    file_path = Path(settings.EMPLOYEE_DOCS_ROOT) / row.storage_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document file not found on disk")
    return FileResponse(str(file_path))


@router.put("/{user_id}/documents/{doc_id}", dependencies=[Depends(require_permission("user:update"))])
async def update_employee_document(
    user_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: EmployeeDocumentUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = doc_id
    updates["uid"] = user_id
    result = await db.execute(
        text(
            f"UPDATE employee_documents SET {set_clause}, updated_at = now() "
            "WHERE id = :id AND user_id = :uid RETURNING id, document_type, expiry_date"
        ),
        updates,
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.commit()
    return dict(row._mapping)


@router.delete("/{user_id}/documents/{doc_id}", dependencies=[Depends(require_permission("user:delete"))])
async def delete_employee_document(
    user_id: uuid.UUID, doc_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _assert_may_touch(db, token, user_id)
    result = await db.execute(
        text("DELETE FROM employee_documents WHERE id = :id AND user_id = :uid RETURNING storage_path"),
        {"id": doc_id, "uid": user_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.commit()
    if row.storage_path:
        file_path = Path(settings.EMPLOYEE_DOCS_ROOT) / row.storage_path
        file_path.unlink(missing_ok=True)
    return {"id": str(doc_id), "deleted": True}


# ── Push Notification Token ───────────────────────────────────────────────────

class PushTokenBody(BaseModel):
    token: str  # Expo push token, e.g. ExponentPushToken[xxx]


@router.post("/me/push-token")
async def register_push_token(
    body: PushTokenBody,
    request: Request,
    token: TokenPayload = Depends(get_token_payload),
):
    """Register an Expo push notification token for the current user.

    Stored in Redis twice (both expire after 30 days):
      push_tokens:{tenant_id}            — tenant-wide set (fallback fan-out)
      push_tokens:{tenant_id}:{user_id}  — per-user set for site+shift-aware
                                           routing (Gap 82)"""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push service unavailable")
    tenant_key = f"push_tokens:{token.tenant_id}"
    user_key = f"push_tokens:{token.tenant_id}:{token.user_id}"
    await redis.sadd(tenant_key, body.token)
    await redis.expire(tenant_key, 30 * 86400)  # 30 days
    await redis.sadd(user_key, body.token)
    await redis.expire(user_key, 30 * 86400)
    return {"registered": True, "token": body.token}


@router.delete("/me/push-token")
async def unregister_push_token(
    body: PushTokenBody,
    request: Request,
    token: TokenPayload = Depends(get_token_payload),
):
    """Remove a push token (called on logout)."""
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        await redis.srem(f"push_tokens:{token.tenant_id}", body.token)
        await redis.srem(f"push_tokens:{token.tenant_id}:{token.user_id}", body.token)
    return {"unregistered": True}


# ── Profile photo ────────────────────────────────────────────────────────────
#
# The command office needs a face for a guard *before* they turn up — the one
# you most need to identify is the one who has not arrived. The check-in selfie
# can only ever answer that after the fact, so a permanent photo lives here and
# the live selfie supersedes it once a shift starts.

_PROFILE_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.post("/{user_id}/photo", dependencies=[Depends(require_permission("user:update"))])
async def upload_profile_photo(
    user_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if file.content_type not in _PROFILE_PHOTO_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Photo must be one of {sorted(_PROFILE_PHOTO_TYPES)}",
        )
    await _assert_may_touch(db, token, user_id)
    if (await db.execute(text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id})).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    ext = Path(file.filename or "").suffix or ".jpg"
    # Fixed filename per user: a profile photo is replaced, not versioned, and
    # a stable path means no orphaned files accumulating on every re-upload.
    relative_path = f"{token.tenant_id}/{user_id}/profile{ext}"
    dest = Path(settings.EMPLOYEE_DOCS_ROOT) / relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await read_upload_limited(file, MAX_DOCUMENT_UPLOAD_BYTES))

    await db.execute(
        text("UPDATE users SET profile_photo_path = :p, updated_at = now() WHERE id = :id"),
        {"p": relative_path, "id": user_id},
    )
    await db.commit()
    return {"profile_photo_path": relative_path}


@router.get("/{user_id}/photo")
async def get_profile_photo(
    user_id: uuid.UUID,
    token: str = Query(..., description="JWT access token"),
):
    """Served for `<img src>`, which cannot set an Authorization header — same
    query-param-JWT pattern as the check-in photo and evidence endpoints.

    Gated on user:read rather than being public: a guard's face plus the site
    they work is exactly the pairing that should not leak from a URL alone.
    """
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": payload["tenant_id"]},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'user:read'"
            ),
            {"role_id": payload["role_id"]},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: user:read")
        row = (await session.execute(
            text("SELECT profile_photo_path, role_id FROM users WHERE id = :id"), {"id": user_id},
        )).first()

    # This endpoint authenticates from a query param and runs its own session,
    # so it cannot use _assert_may_touch — but it needs the same rule, or a
    # platform admin's face is readable from a URL by any tenant Admin.
    if (row is not None
            and row[1] == PLATFORM_ADMIN_ROLE_ID
            and payload["role_id"] != PLATFORM_ADMIN_ROLE_ID):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo")

    if row is None or not row[0]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo")
    file_path = Path(settings.EMPLOYEE_DOCS_ROOT) / row[0]
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile photo missing on disk")
    return FileResponse(str(file_path))
