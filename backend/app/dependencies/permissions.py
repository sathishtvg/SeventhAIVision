from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.tenant import get_db_with_tenant


def require_permission(code: str):
    """Checks the caller's role against role_permissions — the same table the
    Phase 2 web dashboard and mobile app will read permission codes from, so
    there's never a parallel authorization model to invent for a new client."""

    async def checker(
        db: AsyncSession = Depends(get_db_with_tenant),
        token: TokenPayload = Depends(get_token_payload),
    ) -> None:
        result = await db.execute(
            text(
                """
                SELECT 1 FROM role_permissions rp
                JOIN permissions p ON p.id = rp.permission_id
                WHERE rp.role_id = :role_id AND p.code = :code
                """
            ),
            {"role_id": token.role_id, "code": code},
        )
        if result.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {code}")

    return checker
