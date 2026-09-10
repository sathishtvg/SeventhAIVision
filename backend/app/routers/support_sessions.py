"""Support sessions — the deliberate way a platform operator enters a tenant.

Super Admin holds four permissions and none of them read a customer's rosters,
payroll or key register (migration 0102). That is correct for the ninety-nine
percent of platform work that never needs them, and useless for the one percent
where a customer raises a ticket and somebody has to look.

So access to a tenant became an event instead of a condition. Opening a session
requires saying which tenant and why, lasts an hour at the outside, and writes
an audit entry into the CUSTOMER's own log as well as the platform's — the
people whose data it is can see that the vendor came in and what reason was
given. Ending it revokes the token immediately, because every tenant-scoped
request re-checks that the session is still live.

What this is not: a way to be someone else. The token carries the platform
user's own id, so every action during a session is attributed to the human who
opened it rather than to a borrowed admin account.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SUPPORT_TOKEN_MAX_MINUTES, create_support_token
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/support-sessions", tags=["support"])


class SupportSessionCreate(BaseModel):
    tenant_id: str
    # Required, and required to be meaningful: an access record nobody has to
    # justify is a log, not a control. The table enforces non-blank as well.
    reason: str = Field(min_length=10, max_length=500)
    minutes: int = Field(default=30, ge=5, le=SUPPORT_TOKEN_MAX_MINUTES)
    #: Read-only unless somebody deliberately says otherwise. Most support is
    #: looking, and an operator who only needs to read should not be one
    #: mis-click from editing a customer's roster.
    access_level: str = Field(default="read_only", pattern="^(read_only|elevated)$")


async def _set_tenant(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id}
    )


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("support:manage"))])
async def open_support_session(
    body: SupportSessionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Open a session into one tenant and return a token scoped to it."""
    if token.support_session_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You are already inside a support session. End it before opening another.",
        )

    # tenants is a global catalogue with no RLS, so this reads across tenants by
    # design — it is the one table a platform operator is meant to see whole.
    target = (await db.execute(
        text("SELECT id, name FROM tenants "
             " WHERE id = CAST(:tid AS uuid) AND is_active = TRUE"),
        {"tid": body.tenant_id},
    )).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found or inactive")

    live = (await db.execute(
        text("SELECT id FROM tenant_support_sessions "
             " WHERE platform_user_id = CAST(:uid AS uuid) AND ended_at IS NULL"),
        {"uid": token.user_id},
    )).first()
    if live is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You already have a live support session. End it first — two open "
            "sessions means two tenants' tokens in the same hands.",
        )

    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
    await db.execute(
        text(
            "INSERT INTO tenant_support_sessions "
            "  (id, platform_user_id, platform_tenant_id, tenant_id, reason, "
            "   expires_at, access_level) "
            "VALUES (CAST(:id AS uuid), CAST(:uid AS uuid), CAST(:ptid AS uuid), "
            "        CAST(:tid AS uuid), :reason, :exp, :access)"
        ),
        {"id": session_id, "uid": token.user_id, "ptid": token.tenant_id,
         "tid": body.tenant_id, "reason": body.reason.strip(), "exp": expires_at,
         "access": body.access_level},
    )

    # access_level is in the audit detail because a customer reading their own
    # log should be able to tell "they looked" from "they could have changed
    # anything", which is the whole point of having two levels.
    detail = {"session_id": session_id, "tenant": target.name,
              "reason": body.reason.strip(), "minutes": body.minutes,
              "access_level": body.access_level}
    ip = request.client.host if request.client else None

    # The platform's own record...
    await write_audit_log(
        db, tenant_id=token.tenant_id, user_id=token.user_id,
        action="support_session.opened", resource_type="tenant",
        resource_id=body.tenant_id, ip_address=ip, detail=detail,
    )
    # ...and the customer's, which is the half that matters. Their auditors
    # should not have to ask us whether we came in.
    #
    # Unless they are the same log. A platform operator's account can live
    # inside a customer tenant, and since Super Admin now has no operational
    # access anywhere it needs a session even for the tenant it sits in — which
    # would otherwise write the same event into the same log twice.
    if body.tenant_id != token.tenant_id:
        await _set_tenant(db, body.tenant_id)
        await write_audit_log(
            db, tenant_id=body.tenant_id, user_id=None,
            action="support_session.opened", resource_type="tenant",
            resource_id=body.tenant_id, ip_address=ip,
            detail={**detail, "by_platform_user": token.user_id},
        )

    await db.commit()
    # Nothing is read after this point: the commit drops the transaction-local
    # tenant GUC, and every RLS policy would then cast '' to uuid.
    return {
        "id": session_id,
        "tenant_id": body.tenant_id,
        "tenant_name": target.name,
        "expires_at": expires_at.isoformat(),
        "access_level": body.access_level,
        "access_token": create_support_token(
            token.user_id, body.tenant_id, session_id, body.minutes,
        ),
    }


@router.get("", dependencies=[Depends(require_permission("support:manage"))])
async def list_support_sessions(db: AsyncSession = Depends(get_db_with_tenant)):
    """Every session, live and past.

    Not filtered to the caller: "who entered which customer, and why" is
    exactly the question this table exists to answer, and a record only its
    author can read is not an audit trail.
    """
    rows = (await db.execute(text(
        "SELECT s.id, s.tenant_id, t.name AS tenant_name, s.reason, "
        "       s.access_level, "
        "       s.started_at, s.expires_at, s.ended_at, "
        "       s.platform_user_id, u.email AS platform_user_email, "
        "       (s.ended_at IS NULL AND s.expires_at > now()) AS is_live "
        "  FROM tenant_support_sessions s "
        "  JOIN tenants t ON t.id = s.tenant_id "
        "  LEFT JOIN users u ON u.id = s.platform_user_id "
        " ORDER BY s.started_at DESC "
        " LIMIT 200"
    ))).mappings().all()
    return [dict(r) for r in rows]


@router.post("/{session_id}/end",
             dependencies=[Depends(require_permission("support:manage"))])
async def end_support_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """End a session now. The token dies with it, on its next request."""
    row = (await db.execute(
        text(
            "UPDATE tenant_support_sessions "
            "   SET ended_at = now() "
            " WHERE id = CAST(:sid AS uuid) AND ended_at IS NULL "
            " RETURNING tenant_id, platform_tenant_id, platform_user_id"
        ),
        {"sid": session_id},
    )).first()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No live support session with that id"
        )

    ip = request.client.host if request.client else None
    detail = {"session_id": session_id, "ended_by": token.user_id}

    # Written where the session was opened from, not where the caller happens
    # to be standing — a session ended from inside the customer's tenant still
    # belongs in the platform's log.
    await _set_tenant(db, str(row.platform_tenant_id))
    await write_audit_log(
        db, tenant_id=str(row.platform_tenant_id), user_id=token.user_id,
        action="support_session.ended", resource_type="tenant",
        resource_id=str(row.tenant_id), ip_address=ip, detail=detail,
    )
    if str(row.tenant_id) != str(row.platform_tenant_id):
        await _set_tenant(db, str(row.tenant_id))
        await write_audit_log(
            db, tenant_id=str(row.tenant_id), user_id=None,
            action="support_session.ended", resource_type="tenant",
            resource_id=str(row.tenant_id), ip_address=ip, detail=detail,
        )
    await db.commit()
    return {"id": session_id, "ended": True}
