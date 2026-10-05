"""Audit entries for the intelligence layer, in the platform's existing log.

One helper so that every entry carries the same things: who, in what role, on
which site, by what means, under which request, and how it ended. They go into
the tenant's hash-chained audit log like every other entry; the extra fields
ride in `detail`, which the chain's hash covers.

Used by the API only. A request made by a person is what an audit entry is for.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload
from app.dependencies.tenant import _client_ip
from app.services.audit import write_audit_log


async def record(db: AsyncSession, request: Request, token: TokenPayload, action: str,
                 resource_type: str, resource_id: Any, *, site_id: Any = None, result: str = "ok",
                 detail: dict | None = None) -> None:
    """Write one entry. The caller commits, so the entry and the change it
    describes are saved together or not at all."""
    await write_audit_log(
        db,
        tenant_id=token.tenant_id,
        user_id=token.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=_client_ip(request),
        detail={
            "actor_role": token.role_id,
            "site_id": str(site_id) if site_id is not None else None,
            "source": "api_key" if token.via_api_key else "user",
            "request_id": getattr(request.state, "request_id", None),
            "result": result,
            **(detail or {}),
        },
    )
