"""Active session management — list and revoke refresh-token sessions.

A "session" is a non-revoked, non-expired refresh_token row.  Listing them
lets a user see which devices are logged in; revoking one forces a logout
of that device without touching other sessions.

Endpoints:
  GET  /api/v1/sessions/me          — current user's active sessions
  DELETE /api/v1/sessions/me/{id}   — revoke one of my sessions
  DELETE /api/v1/sessions/me        — revoke ALL my sessions (logout everywhere)
  GET  /api/v1/sessions/users/{uid} — admin: list another user's sessions
  DELETE /api/v1/sessions/users/{uid} — admin: revoke all sessions for a user
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
_SESSION_COLS = "id, device_name, last_ip, last_seen_at, created_at, expires_at"
_ACTIVE_FILTER = "revoked_at IS NULL AND expires_at > now()"


@router.get("/me")
async def list_my_sessions(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """List active sessions for the currently logged-in user."""
    result = await db.execute(
        text(
            f"SELECT {_SESSION_COLS} FROM refresh_tokens "
            f"WHERE user_id = :uid AND {_ACTIVE_FILTER} "
            "ORDER BY last_seen_at DESC NULLS LAST"
        ),
        {"uid": token.user_id},
    )
    return [dict(r._mapping) for r in result]


@router.delete("/me/{session_id}", status_code=status.HTTP_200_OK)
async def revoke_my_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Revoke a specific session (logout that device)."""
    result = await db.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = now() "
            "WHERE id = :sid AND user_id = :uid AND revoked_at IS NULL "
            "RETURNING id"
        ),
        {"sid": session_id, "uid": token.user_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return {"id": session_id, "revoked": True}


@router.delete("/me", status_code=status.HTTP_200_OK)
async def revoke_all_my_sessions(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Revoke all active sessions for the current user (logout everywhere)."""
    result = await db.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = now() "
            "WHERE user_id = :uid AND revoked_at IS NULL "
            "RETURNING id"
        ),
        {"uid": token.user_id},
    )
    count = len(result.fetchall())
    await db.commit()
    return {"revoked_count": count}


@router.get("/users/{user_id}", dependencies=[Depends(require_permission("session:manage"))])
async def list_user_sessions(
    user_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Admin: list active sessions for any user in the tenant."""
    result = await db.execute(
        text(
            f"SELECT {_SESSION_COLS} FROM refresh_tokens "
            f"WHERE user_id = :uid AND {_ACTIVE_FILTER} "
            "ORDER BY last_seen_at DESC NULLS LAST"
        ),
        {"uid": user_id},
    )
    return [dict(r._mapping) for r in result]


@router.delete("/users/{user_id}", dependencies=[Depends(require_permission("session:manage"))])
async def revoke_all_user_sessions(
    user_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Admin: force-logout a user by revoking all their active sessions."""
    result = await db.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = now() "
            "WHERE user_id = :uid AND revoked_at IS NULL "
            "RETURNING id"
        ),
        {"uid": user_id},
    )
    count = len(result.fetchall())
    await db.commit()
    return {"user_id": user_id, "revoked_count": count}
