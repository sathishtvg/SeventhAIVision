import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair
from app.services import auth_service
from app.services.auth_service import _TwoFactorRequired

from sqlalchemy import text

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class TwoFAVerifyRequest(BaseModel):
    challenge_token: str
    totp_code: str


class LoginRequestExtended(LoginRequest):
    device_name: str | None = None


@router.get("/me/permissions")
async def my_permissions(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """The caller's effective permission codes. The web/mobile clients load
    this to drive UI gating — required for custom roles (Gap 91), whose
    permission sets the frontend cannot hardcode."""
    result = await db.execute(
        text("SELECT p.code FROM role_permissions rp "
             "JOIN permissions p ON p.id = rp.permission_id "
             "WHERE rp.role_id = :role_id ORDER BY p.code"),
        {"role_id": token.role_id},
    )
    return {"role_id": token.role_id, "permissions": [r.code for r in result]}


@router.get("/resolve-tenant/{subdomain}")
async def resolve_tenant_by_subdomain(subdomain: str):
    """Resolve a subdomain to tenant info (no auth required).
    Used by the Windows desktop app first-run wizard to validate company URL.
    Returns 404 if no active tenant matches the subdomain or slug.
    """
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                "SELECT id, name, slug, subdomain, timezone, branding "
                "FROM tenants "
                "WHERE (subdomain = :sub OR slug = :sub) AND is_active = TRUE"
            ),
            {"sub": subdomain},
        )
        row = result.mappings().first()
    if row is None:
        from fastapi import HTTPException, status as _status
        raise HTTPException(_status.HTTP_404_NOT_FOUND, "No tenant found for this subdomain")
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "subdomain": row["subdomain"],
        "timezone": row["timezone"],
        "branding": row["branding"] or {},
    }


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequestExtended):
    client_ip = request.client.host if request.client else None
    try:
        return await auth_service.authenticate_and_issue_tokens(
            body.tenant_slug, body.email, body.password,
            device_name=body.device_name, client_ip=client_ip,
        )
    except _TwoFactorRequired as exc:
        return {"requires_2fa": True, "challenge_token": exc.challenge_token}


@router.post("/2fa-verify", response_model=TokenPair)
@limiter.limit("5/minute")
async def verify_2fa(request: Request, body: TwoFAVerifyRequest) -> TokenPair:
    return await auth_service.verify_2fa_and_issue_tokens(body.challenge_token, body.totp_code)


@router.post("/refresh", response_model=TokenPair)
@limiter.limit("5/minute")
async def refresh(request: Request, body: RefreshRequest) -> TokenPair:
    client_ip = request.client.host if request.client else None
    return await auth_service.rotate_refresh_token(
        body.refresh_token, client_ip=client_ip
    )


@router.post("/users/{user_id}/unlock", dependencies=[Depends(require_permission("user:update"))])
async def unlock_account(
    user_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Admin/supervisor: clear account lockout for a user."""
    result = await db.execute(
        text(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL "
            "WHERE id = :uid RETURNING id, email"
        ),
        {"uid": user_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return {"id": str(row.id), "email": row.email, "locked": False}
