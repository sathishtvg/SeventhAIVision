"""IP allowlist management for per-tenant access control.

If a tenant has ≥1 active rule, every request whose client IP doesn't match
any of the listed CIDRs is rejected with 403. No active rules = open access.
"""

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/ip-allowlist", tags=["ip-allowlist"])


def _validate_cidr(value: str) -> str:
    """Raise ValueError if value is not a valid IPv4/IPv6 CIDR or host address."""
    try:
        # Treat bare IPs as /32 or /128
        if "/" not in value:
            ipaddress.ip_address(value)
            value = f"{value}/32" if ":" not in value else f"{value}/128"
        else:
            ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid CIDR: {value!r}") from exc
    return value


class IpRuleCreate(BaseModel):
    cidr: str
    description: str | None = None

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        return _validate_cidr(v)


@router.get("", dependencies=[Depends(require_permission("iplist:manage"))])
async def list_rules(db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (await db.execute(
        text("""
            SELECT ip.id, ip.cidr, ip.description, ip.is_active, ip.created_at,
                   u.email AS created_by_email
            FROM ip_allowlist ip
            LEFT JOIN users u ON u.id = ip.created_by_user_id
            ORDER BY ip.created_at DESC
        """)
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("", dependencies=[Depends(require_permission("iplist:manage"))])
async def add_rule(
    body: IpRuleCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    try:
        row = (await db.execute(
            text("""
                INSERT INTO ip_allowlist (tenant_id, cidr, description, created_by_user_id)
                VALUES (
                    current_setting('app.current_tenant')::uuid,
                    :cidr, :description, :created_by
                )
                RETURNING id, cidr, description, is_active, created_at
            """),
            {"cidr": body.cidr, "description": body.description, "created_by": token.user_id},
        )).first()
    except Exception as exc:
        if "uq_ip_allowlist_tenant_cidr" in str(exc):
            raise HTTPException(status.HTTP_409_CONFLICT, f"CIDR {body.cidr!r} already exists for this tenant")
        raise
    await db.commit()
    return dict(row._mapping)


@router.delete("/{rule_id}", dependencies=[Depends(require_permission("iplist:manage"))])
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM ip_allowlist WHERE id = :id RETURNING id"),
        {"id": rule_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IP rule not found")
    return {"id": rule_id, "deleted": True}


@router.patch("/{rule_id}/toggle", dependencies=[Depends(require_permission("iplist:manage"))])
async def toggle_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("""
            UPDATE ip_allowlist
            SET is_active = NOT is_active
            WHERE id = :id
            RETURNING id, is_active
        """),
        {"id": rule_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IP rule not found")
    return {"id": rule_id, "is_active": row.is_active}
