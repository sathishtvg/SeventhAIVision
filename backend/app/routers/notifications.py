"""Notification channels, rules, and logs API.

All endpoints require the 'notification:manage' permission (admin/super_admin only),
except GET /logs which is read-only and requires the same permission.
"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.notifications.dispatch import dispatch_notifications_for_alert

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

_MANAGE = Depends(require_permission("notification:manage"))

VALID_CHANNEL_TYPES = {"email", "sms", "webhook"}
VALID_SEVERITIES = ("info", "low", "medium", "high", "critical")


# ──────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────

class ChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict = {}
    is_active: bool = True

    @field_validator("channel_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_CHANNEL_TYPES:
            raise ValueError(f"channel_type must be one of {VALID_CHANNEL_TYPES}")
        return v


class ChannelUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class RuleCreate(BaseModel):
    channel_id: str
    min_severity: str = "medium"
    module_types: list[str] = []
    alert_codes: list[str] = []
    trigger_events: list[str] = []
    site_ids: list[str] = []   # Gap 82 — empty = all sites
    is_active: bool = True

    @field_validator("min_severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"min_severity must be one of {VALID_SEVERITIES}")
        return v


class RuleUpdate(BaseModel):
    min_severity: str | None = None
    module_types: list[str] | None = None
    alert_codes: list[str] | None = None
    trigger_events: list[str] | None = None
    site_ids: list[str] | None = None   # Gap 82
    is_active: bool | None = None


# ──────────────────────────────────────────────────────────
# Channels
# ──────────────────────────────────────────────────────────

@router.get("/channels", dependencies=[_MANAGE])
async def list_channels(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text(
        "SELECT id, name, channel_type, config, is_active, created_at, updated_at "
        "FROM notification_channels ORDER BY created_at DESC"
    ))
    return [dict(r._mapping) for r in result]


@router.post("/channels", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_channel(body: ChannelCreate, db: AsyncSession = Depends(get_db_with_tenant),
                          token: TokenPayload = Depends(get_token_payload)):
    import json as _json
    new_id = str(_uuid.uuid4())
    result = await db.execute(text(
        """
        INSERT INTO notification_channels (id, tenant_id, name, channel_type, config, is_active)
        VALUES (:id, :tenant_id, :name, :channel_type, CAST(:config AS jsonb), :is_active)
        RETURNING id, name, channel_type, config, is_active, created_at
        """
    ), {
        "id": new_id,
        "tenant_id": str(token.tenant_id),
        "name": body.name,
        "channel_type": body.channel_type,
        "config": _json.dumps(body.config),
        "is_active": body.is_active,
    })
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.put("/channels/{channel_id}", dependencies=[_MANAGE])
async def update_channel(channel_id: str, body: ChannelUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    import json as _json
    sets, params = [], {"id": channel_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.config is not None:
        sets.append("config = CAST(:config AS jsonb)"); params["config"] = _json.dumps(body.config)
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    sets.append("updated_at = now()")
    result = await db.execute(
        text(f"UPDATE notification_channels SET {', '.join(sets)} WHERE id = :id "
             "RETURNING id, name, channel_type, config, is_active, updated_at"),
        params,
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    await db.commit()
    return dict(row)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_MANAGE])
async def delete_channel(channel_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("DELETE FROM notification_channels WHERE id = :id"), {"id": channel_id})
    await db.commit()


@router.post("/channels/{channel_id}/test", dependencies=[_MANAGE])
async def test_channel(channel_id: str, db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    """Send a test notification through the channel with a synthetic alert payload."""
    result = await db.execute(
        text("SELECT id, channel_type, config, is_active FROM notification_channels WHERE id = :id"),
        {"id": channel_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")
    if not row["is_active"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Channel is inactive")

    from app.notifications.providers.email import send_email
    from app.notifications.providers.sms import send_sms
    from app.notifications.providers.webhook import send_webhook

    test_alert = {
        "id": str(_uuid.uuid4()),
        "tenant_id": str(token.tenant_id),
        "camera_id": "test-camera",
        "module_type": "test",
        "severity": "low",
        "alert_code": "test.notification",
        "title": "Test notification",
        "message": "This is a test notification from Seventh AI Vision.",
        "status": "open",
        "created_at": "now",
    }
    provider = {"email": send_email, "sms": send_sms, "webhook": send_webhook}.get(row["channel_type"])
    if provider is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown channel_type: {row['channel_type']}")
    try:
        await provider(dict(row["config"]), test_alert)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Delivery failed: {exc}") from exc
    return {"status": "sent"}


# ──────────────────────────────────────────────────────────
# Rules
# ──────────────────────────────────────────────────────────

@router.get("/rules", dependencies=[_MANAGE])
async def list_rules(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(text(
        "SELECT id, channel_id, min_severity, module_types, alert_codes, "
        "trigger_events, site_ids, is_active, created_at FROM notification_rules ORDER BY created_at DESC"
    ))
    return [dict(r._mapping) for r in result]


@router.post("/rules", status_code=status.HTTP_201_CREATED, dependencies=[_MANAGE])
async def create_rule(body: RuleCreate, db: AsyncSession = Depends(get_db_with_tenant),
                       token: TokenPayload = Depends(get_token_payload)):
    import json as _json
    # Verify channel belongs to this tenant (RLS will block if not, but give a clear 404)
    ch = await db.execute(
        text("SELECT id FROM notification_channels WHERE id = :id"), {"id": body.channel_id}
    )
    if not ch.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found")

    new_id = str(_uuid.uuid4())
    result = await db.execute(text(
        """
        INSERT INTO notification_rules
            (id, tenant_id, channel_id, min_severity, module_types,
             alert_codes, trigger_events, site_ids, is_active)
        VALUES
            (:id, :tenant_id, :channel_id, :min_severity, CAST(:module_types AS jsonb),
             CAST(:alert_codes AS jsonb), CAST(:trigger_events AS jsonb),
             CAST(:site_ids AS jsonb), :is_active)
        RETURNING id, channel_id, min_severity, module_types, alert_codes, trigger_events, site_ids, is_active, created_at
        """
    ), {
        "id": new_id,
        "tenant_id": str(token.tenant_id),
        "channel_id": body.channel_id,
        "min_severity": body.min_severity,
        "module_types": _json.dumps(body.module_types),
        "alert_codes": _json.dumps(body.alert_codes),
        "trigger_events": _json.dumps(body.trigger_events),
        "site_ids": _json.dumps(body.site_ids),
        "is_active": body.is_active,
    })
    row = dict(result.mappings().first())
    await db.commit()
    return row


@router.put("/rules/{rule_id}", dependencies=[_MANAGE])
async def update_rule(rule_id: str, body: RuleUpdate, db: AsyncSession = Depends(get_db_with_tenant)):
    import json as _json
    sets, params = [], {"id": rule_id}
    if body.min_severity is not None:
        if body.min_severity not in VALID_SEVERITIES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid min_severity")
        sets.append("min_severity = :min_severity"); params["min_severity"] = body.min_severity
    if body.module_types is not None:
        sets.append("module_types = CAST(:module_types AS jsonb)"); params["module_types"] = _json.dumps(body.module_types)
    if body.alert_codes is not None:
        sets.append("alert_codes = CAST(:alert_codes AS jsonb)"); params["alert_codes"] = _json.dumps(body.alert_codes)
    if body.trigger_events is not None:
        sets.append("trigger_events = CAST(:trigger_events AS jsonb)"); params["trigger_events"] = _json.dumps(body.trigger_events)
    if body.site_ids is not None:
        sets.append("site_ids = CAST(:site_ids AS jsonb)"); params["site_ids"] = _json.dumps(body.site_ids)
    if body.is_active is not None:
        sets.append("is_active = :is_active"); params["is_active"] = body.is_active
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No fields to update")
    result = await db.execute(
        text(f"UPDATE notification_rules SET {', '.join(sets)} WHERE id = :id "
             "RETURNING id, channel_id, min_severity, module_types, alert_codes, trigger_events, site_ids, is_active"),
        params,
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    await db.commit()
    return dict(row)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_MANAGE])
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    await db.execute(text("DELETE FROM notification_rules WHERE id = :id"), {"id": rule_id})
    await db.commit()


# ──────────────────────────────────────────────────────────
# Logs (read-only)
# ──────────────────────────────────────────────────────────

@router.get("/logs", dependencies=[_MANAGE])
async def list_logs(
    db: AsyncSession = Depends(get_db_with_tenant),
    channel_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 100,
):
    query = (
        "SELECT id, alert_id, event_type, channel_id, channel_type, "
        "status, error_detail, sent_at, created_at FROM notification_logs"
    )
    params: dict = {"limit": min(limit, 500)}
    conditions = []
    if channel_id:
        conditions.append("channel_id = :channel_id"); params["channel_id"] = channel_id
    if status_filter:
        conditions.append("status = :status_filter"); params["status_filter"] = status_filter
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT :limit"
    result = await db.execute(text(query), params)
    return [dict(r._mapping) for r in result]
