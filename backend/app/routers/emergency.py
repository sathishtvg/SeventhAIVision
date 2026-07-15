"""Emergency Mass Notification router — /api/v1/emergency"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/emergency", tags=["emergency"])

VALID_SEVERITIES = {"info", "warning", "critical", "drill"}
VALID_TYPES = {"all", "role"}

SEVERITY_COLOR = {
    "info":     "#2196F3",
    "warning":  "#FF9800",
    "critical": "#FF4560",
    "drill":    "#6C63FF",
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class BroadcastCreate(BaseModel):
    title: str
    message: str
    severity: str = "warning"
    broadcast_type: str = "all"
    target_role_ids: list[int] | None = None


# ── Background delivery ───────────────────────────────────────────────────────

async def _send_broadcast_emails(
    recipients: list[dict],
    broadcast: dict,
) -> None:
    """Fire-and-forget: send email to each recipient."""
    from app.core.config import settings
    try:
        import aiosmtplib
        from email.mime.text import MIMEText
    except ImportError:
        logger.warning("aiosmtplib not available — skipping broadcast email delivery")
        return

    if not settings.SMTP_HOST:
        return

    sev = broadcast["severity"].upper()
    prefix = "[EMERGENCY]" if broadcast["severity"] == "critical" else "[Seventh AI]"
    subject = f"{prefix} {broadcast['title']}"

    for r in recipients:
        email = r.get("email")
        if not email:
            continue
        body = (
            f"Severity: {sev}\n"
            f"From: {broadcast.get('sender_name', 'Operations')}\n"
            f"Sent: {broadcast.get('sent_at', '')}\n\n"
            f"{broadcast['message']}\n\n"
            "──────────────────────────────────────\n"
            "This is an automated emergency broadcast from Seventh AI Vision.\n"
            "Please acknowledge receipt in the platform.\n"
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = email
        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                use_tls=(settings.SMTP_PORT == 465),
                start_tls=(settings.SMTP_PORT == 587),
            )
        except Exception as exc:
            logger.warning("broadcast email failed to=%s: %s", email, exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/broadcasts", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("broadcast:send"))])
async def create_broadcast(
    body: BroadcastCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.severity not in VALID_SEVERITIES:
        raise HTTPException(422, f"severity must be one of {sorted(VALID_SEVERITIES)}")
    if body.broadcast_type not in VALID_TYPES:
        raise HTTPException(422, f"broadcast_type must be one of {sorted(VALID_TYPES)}")
    if body.broadcast_type == "role" and not body.target_role_ids:
        raise HTTPException(422, "target_role_ids required when broadcast_type='role'")

    # ── Create broadcast row ──────────────────────────────────────────────────
    broadcast_row = (await db.execute(text("""
        INSERT INTO emergency_broadcasts
            (tenant_id, title, message, severity, broadcast_type, target_role_ids,
             created_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid,
                :title, :msg, :sev, :btype,
                CAST(:role_ids AS jsonb), CAST(:uid AS uuid))
        RETURNING id, sent_at
    """), {
        "title": body.title, "msg": body.message, "sev": body.severity,
        "btype": body.broadcast_type,
        "role_ids": json.dumps(body.target_role_ids) if body.target_role_ids else None,
        "uid": token.user_id,
    })).first()
    broadcast_id = str(broadcast_row.id)

    # ── Resolve recipients ────────────────────────────────────────────────────
    if body.broadcast_type == "role":
        users_rows = await db.execute(
            text("SELECT id, email, full_name FROM users WHERE is_active = TRUE AND role_id = ANY(:rids)"),
            {"rids": body.target_role_ids}
        )
    else:
        users_rows = await db.execute(text(
            "SELECT id, email, full_name FROM users WHERE is_active = TRUE"
        ))
    recipients = [{"id": str(r.id), "email": r.email, "full_name": r.full_name}
                  for r in users_rows]

    # ── Insert recipient rows ─────────────────────────────────────────────────
    if recipients:
        for r in recipients:
            await db.execute(text("""
                INSERT INTO broadcast_recipients (tenant_id, broadcast_id, user_id)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:bid AS uuid), CAST(:uid AS uuid))
                ON CONFLICT (broadcast_id, user_id) DO NOTHING
            """), {"bid": broadcast_id, "uid": r["id"]})

        await db.execute(text(
            "UPDATE emergency_broadcasts SET recipient_count = :cnt WHERE id = CAST(:bid AS uuid)"
        ), {"cnt": len(recipients), "bid": broadcast_id})

    # ── Get sender name for email ─────────────────────────────────────────────
    sender = (await db.execute(
        text("SELECT full_name FROM users WHERE id = CAST(:uid AS uuid)"),
        {"uid": token.user_id}
    )).first()
    sender_name = sender.full_name if sender else "Operations"

    await db.commit()

    # ── Publish WebSocket event ───────────────────────────────────────────────
    _tid = str(token.tenant_id)
    _redis = getattr(request.app.state, "redis", None)
    if _redis:
        try:
            await _redis.publish(f"tenant_events:{_tid}", json.dumps({
                "event_type": "broadcast_created",
                "tenant_id": _tid,
                "payload": {
                    "broadcast_id": broadcast_id,
                    "title": body.title,
                    "message": body.message,
                    "severity": body.severity,
                    "sender_name": sender_name,
                    "recipient_count": len(recipients),
                },
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }))
        except Exception as exc:
            logger.warning("broadcast redis publish failed: %s", exc)

    # ── Fire-and-forget email delivery ────────────────────────────────────────
    asyncio.create_task(_send_broadcast_emails(recipients, {
        "title": body.title, "message": body.message, "severity": body.severity,
        "sender_name": sender_name,
        "sent_at": broadcast_row.sent_at.isoformat() if broadcast_row.sent_at else "",
    }))

    return {
        "id": broadcast_id,
        "recipient_count": len(recipients),
        "sent_at": broadcast_row.sent_at.isoformat() if broadcast_row.sent_at else None,
    }


@router.get("/broadcasts", dependencies=[Depends(require_permission("broadcast:read"))])
async def list_broadcasts(
    db: AsyncSession = Depends(get_db_with_tenant),
    limit: int = 50,
    offset: int = 0,
):
    rows = await db.execute(text("""
        SELECT b.id, b.title, b.message, b.severity, b.broadcast_type,
               b.target_role_ids, b.recipient_count, b.acknowledged_count,
               b.sent_at, b.status, b.created_at,
               u.full_name AS sender_name, u.email AS sender_email
        FROM emergency_broadcasts b
        LEFT JOIN users u ON u.id = b.created_by_user_id
        ORDER BY b.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {"limit": min(limit, 200), "offset": max(offset, 0)})
    return [dict(r._mapping) for r in rows]


@router.get("/broadcasts/my", dependencies=[Depends(require_permission("broadcast:read"))])
async def my_broadcasts(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    limit: int = 50,
):
    rows = await db.execute(text("""
        SELECT b.id, b.title, b.message, b.severity, b.broadcast_type,
               b.recipient_count, b.acknowledged_count,
               b.sent_at, b.status, b.created_at,
               u.full_name AS sender_name,
               br.delivered_at, br.acknowledged_at
        FROM broadcast_recipients br
        JOIN emergency_broadcasts b ON b.id = br.broadcast_id
        LEFT JOIN users u ON u.id = b.created_by_user_id
        WHERE br.user_id = CAST(:uid AS uuid)
        ORDER BY b.created_at DESC
        LIMIT :limit
    """), {"uid": token.user_id, "limit": min(limit, 200)})
    return [dict(r._mapping) for r in rows]


@router.get("/broadcasts/{broadcast_id}",
            dependencies=[Depends(require_permission("broadcast:read"))])
async def get_broadcast(
    broadcast_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    b = (await db.execute(text("""
        SELECT b.id, b.title, b.message, b.severity, b.broadcast_type,
               b.target_role_ids, b.recipient_count, b.acknowledged_count,
               b.sent_at, b.status, b.created_at,
               u.full_name AS sender_name, u.email AS sender_email
        FROM emergency_broadcasts b
        LEFT JOIN users u ON u.id = b.created_by_user_id
        WHERE b.id = CAST(:bid AS uuid)
    """), {"bid": broadcast_id})).first()
    if b is None:
        raise HTTPException(404, "Broadcast not found")

    recipients = await db.execute(text("""
        SELECT br.user_id, br.delivered_at, br.acknowledged_at, br.created_at,
               u.full_name, u.email, r.name AS role_name
        FROM broadcast_recipients br
        JOIN users u ON u.id = br.user_id
        JOIN roles r ON r.id = u.role_id
        WHERE br.broadcast_id = CAST(:bid AS uuid)
        ORDER BY br.acknowledged_at NULLS LAST, u.full_name
    """), {"bid": broadcast_id})

    return {
        **dict(b._mapping),
        "recipients": [dict(r._mapping) for r in recipients],
    }


@router.post("/broadcasts/{broadcast_id}/acknowledge",
             dependencies=[Depends(require_permission("broadcast:acknowledge"))])
async def acknowledge_broadcast(
    broadcast_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(text("""
        UPDATE broadcast_recipients
        SET acknowledged_at = now()
        WHERE broadcast_id = CAST(:bid AS uuid)
          AND user_id = CAST(:uid AS uuid)
          AND acknowledged_at IS NULL
        RETURNING id
    """), {"bid": broadcast_id, "uid": token.user_id})

    if result.first() is not None:
        await db.execute(text("""
            UPDATE emergency_broadcasts
            SET acknowledged_count = acknowledged_count + 1
            WHERE id = CAST(:bid AS uuid)
        """), {"bid": broadcast_id})
        await db.commit()

    return {"ok": True}
