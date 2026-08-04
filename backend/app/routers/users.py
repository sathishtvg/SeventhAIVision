import datetime
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.core.uploads import MAX_DOCUMENT_UPLOAD_BYTES, read_upload_limited
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
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


@router.get("", dependencies=[Depends(require_permission("user:read"))])
async def list_users(db: AsyncSession = Depends(get_db_with_tenant)):
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
            ORDER BY users.created_at DESC
        """)
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
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(f"SELECT {_USER_SELECT_COLUMNS} FROM users WHERE id = :id"),
        {"id": user_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return dict(row._mapping)


@router.put("/{user_id}", dependencies=[Depends(require_permission("user:update"))])
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
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
async def deactivate_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
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
async def get_user_sites(user_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    """List the sites a user is restricted to. Empty list = unrestricted
    (for internal roles) or no visibility (for the client role)."""
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
):
    """Replace a user's site assignments. An empty list clears all
    assignments (internal roles become unrestricted; clients lose access)."""
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
async def get_employee_documents(user_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
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
    user_id: uuid.UUID, doc_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)
):
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
):
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
    user_id: uuid.UUID, doc_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)
):
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
