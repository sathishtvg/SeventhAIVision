"""Webhook subscription management — CRUD + test delivery + delivery log.

Prefix: /api/v1/webhooks
Permissions:
  webhook:manage — create / update / delete subscriptions
  webhook:read   — list subscriptions and view delivery logs
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.webhook_dispatcher import (
    SUPPORTED_EVENTS,
    _attempt_delivery,
    _create_delivery_log,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class WebhookSubscriptionCreate(BaseModel):
    name: str
    url: str
    event_types: list[str] = ["alert_created", "incident_created"]
    filters: dict[str, Any] = {}
    is_active: bool = True
    max_retries: int = 3
    timeout_seconds: int = 10

    @field_validator("event_types")
    @classmethod
    def validate_event_types(cls, v: list[str]) -> list[str]:
        invalid = set(v) - SUPPORTED_EVENTS
        if invalid:
            raise ValueError(f"Unsupported event types: {invalid}. Supported: {SUPPORTED_EVENTS}")
        if not v:
            raise ValueError("event_types must not be empty")
        return v

    @field_validator("max_retries")
    @classmethod
    def validate_retries(cls, v: int) -> int:
        if not 0 <= v <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if not 1 <= v <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        return v


class WebhookSubscriptionUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    event_types: list[str] | None = None
    filters: dict[str, Any] | None = None
    is_active: bool | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("/", dependencies=[Depends(require_permission("webhook:read"))])
async def list_subscriptions(
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    where = "WHERE tenant_id = current_setting('app.current_tenant', true)::uuid"
    if is_active is not None:
        where += f" AND is_active = {str(is_active).upper()}"
    result = await db.execute(
        text(f"""
            SELECT id, name, url, event_types, filters, is_active,
                   max_retries, timeout_seconds, created_at, updated_at
            FROM webhook_subscriptions
            {where}
            ORDER BY created_at DESC
        """)
    )
    rows = []
    for r in result:
        row = dict(r._mapping)
        row["id"] = str(row["id"])
        row["created_at"] = row["created_at"].isoformat()
        row["updated_at"] = row["updated_at"].isoformat()
        rows.append(row)
    return rows


@router.post("/", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("webhook:manage"))])
async def create_subscription(
    body: WebhookSubscriptionCreate,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    signing_secret = secrets.token_hex(32)
    result = await db.execute(
        text("""
            INSERT INTO webhook_subscriptions
              (tenant_id, name, url, secret, event_types, filters,
               is_active, max_retries, timeout_seconds)
            VALUES
              (CAST(:tid AS uuid), :name, :url, :secret,
               CAST(:etypes AS jsonb), CAST(:filters AS jsonb),
               :active, :max_r, :timeout)
            RETURNING id, created_at
        """),
        {
            "tid": str(token.tenant_id),
            "name": body.name,
            "url": str(body.url),
            "secret": signing_secret,
            "etypes": json.dumps(body.event_types),
            "filters": json.dumps(body.filters),
            "active": body.is_active,
            "max_r": body.max_retries,
            "timeout": body.timeout_seconds,
        },
    )
    row = result.first()
    await db.commit()
    return {
        "id": str(row.id),
        "name": body.name,
        "url": str(body.url),
        "signing_secret": signing_secret,
        "event_types": body.event_types,
        "filters": body.filters,
        "is_active": body.is_active,
        "max_retries": body.max_retries,
        "timeout_seconds": body.timeout_seconds,
        "created_at": row.created_at.isoformat(),
        "note": "Store the signing_secret securely — it will not be shown again.",
    }


@router.get("/{sub_id}", dependencies=[Depends(require_permission("webhook:read"))])
async def get_subscription(
    sub_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text("""
            SELECT id, name, url, event_types, filters, is_active,
                   max_retries, timeout_seconds, created_at, updated_at
            FROM webhook_subscriptions
            WHERE id = CAST(:sid AS uuid)
        """),
        {"sid": str(sub_id)},
    )
    row = result.first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook subscription not found")
    r = dict(row._mapping)
    r["id"] = str(r["id"])
    r["created_at"] = r["created_at"].isoformat()
    r["updated_at"] = r["updated_at"].isoformat()
    return r


@router.put("/{sub_id}", dependencies=[Depends(require_permission("webhook:manage"))])
async def update_subscription(
    sub_id: UUID,
    body: WebhookSubscriptionUpdate,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    check = await db.execute(
        text("SELECT id FROM webhook_subscriptions WHERE id = CAST(:sid AS uuid)"),
        {"sid": str(sub_id)},
    )
    if not check.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook subscription not found")

    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.url is not None:
        updates["url"] = str(body.url)
    if body.event_types is not None:
        invalid = set(body.event_types) - SUPPORTED_EVENTS
        if invalid:
            raise HTTPException(422, f"Unsupported event types: {invalid}")
        updates["event_types"] = json.dumps(body.event_types)
    if body.filters is not None:
        updates["filters"] = json.dumps(body.filters)
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    if body.max_retries is not None:
        updates["max_retries"] = body.max_retries
    if body.timeout_seconds is not None:
        updates["timeout_seconds"] = body.timeout_seconds

    if not updates:
        raise HTTPException(422, "No fields to update")

    set_clauses = []
    params: dict = {"sid": str(sub_id)}
    for k, v in updates.items():
        set_clauses.append(f"{k} = :{k}")
        params[k] = v
    set_clauses.append("updated_at = now()")

    await db.execute(
        text(f"UPDATE webhook_subscriptions SET {', '.join(set_clauses)} WHERE id = CAST(:sid AS uuid)"),
        params,
    )
    await db.commit()
    # Re-set transaction-local GUC after commit (cleared when transaction ends)
    await db.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(token.tenant_id)})
    return await get_subscription(sub_id, db)


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permission("webhook:manage"))])
async def delete_subscription(
    sub_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text("DELETE FROM webhook_subscriptions WHERE id = CAST(:sid AS uuid) RETURNING id"),
        {"sid": str(sub_id)},
    )
    if not result.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook subscription not found")
    await db.commit()


# ── Test delivery ─────────────────────────────────────────────────────────────

@router.post("/{sub_id}/test",
             dependencies=[Depends(require_permission("webhook:manage"))])
async def test_delivery(
    sub_id: UUID,
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Fire a synthetic test event at the subscription's URL."""
    result = await db.execute(
        text("""
            SELECT id, url, secret, max_retries, timeout_seconds
            FROM webhook_subscriptions
            WHERE id = CAST(:sid AS uuid)
        """),
        {"sid": str(sub_id)},
    )
    sub = result.first()
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook subscription not found")

    test_payload = {
        "test": True,
        "message": "This is a test delivery from Seventh AI Vision",
        "triggered_by_user_id": str(token.user_id),
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
    log_id = await _create_delivery_log(
        db, str(token.tenant_id), sub.id, "test_delivery", None, test_payload
    )
    # commit clears the transaction-scoped app.current_tenant GUC (SET LOCAL
    # semantics); restore it so the RLS-scoped read-back of webhook_delivery_log
    # below still sees this tenant instead of failing on an empty GUC.
    await db.commit()
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(token.tenant_id)},
    )
    await _attempt_delivery(
        log_id=log_id,
        sub_id=sub.id,
        tenant_id=str(token.tenant_id),
        url=sub.url,
        secret=sub.secret,
        payload=test_payload,
        event_type="test_delivery",
        event_id=None,
        max_retries=0,  # no retry for test
        timeout=sub.timeout_seconds,
    )

    # Read back the delivery result
    log_result = await db.execute(
        text("SELECT status, response_status_code, response_body FROM webhook_delivery_log WHERE id = CAST(:lid AS uuid)"),
        {"lid": str(log_id)},
    )
    log_row = log_result.first()
    return {
        "delivery_id": str(log_id),
        "status": log_row.status if log_row else "unknown",
        "response_status_code": log_row.response_status_code if log_row else None,
        "response_body": log_row.response_body if log_row else None,
    }


# ── Delivery log ──────────────────────────────────────────────────────────────

@router.get("/{sub_id}/deliveries",
            dependencies=[Depends(require_permission("webhook:read"))])
async def list_deliveries(
    sub_id: UUID,
    status_filter: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    check = await db.execute(
        text("SELECT id FROM webhook_subscriptions WHERE id = CAST(:sid AS uuid)"),
        {"sid": str(sub_id)},
    )
    if not check.first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook subscription not found")

    where = "WHERE subscription_id = CAST(:sid AS uuid)"
    params: dict = {"sid": str(sub_id), "limit": min(limit, 200)}
    if status_filter:
        where += " AND status = :status"
        params["status"] = status_filter

    result = await db.execute(
        text(f"""
            SELECT id, event_type, event_id, status, attempt_count,
                   response_status_code, response_body,
                   next_retry_at, delivered_at, created_at
            FROM webhook_delivery_log
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        params,
    )
    rows = []
    for r in result:
        row = dict(r._mapping)
        row["id"] = str(row["id"])
        row["event_id"] = str(row["event_id"]) if row["event_id"] else None
        for ts_col in ("next_retry_at", "delivered_at", "created_at"):
            if row[ts_col]:
                row[ts_col] = row[ts_col].isoformat()
        rows.append(row)
    return rows


# ── Global delivery stats ─────────────────────────────────────────────────────

@router.get("/stats/overview",
            dependencies=[Depends(require_permission("webhook:read"))])
async def delivery_stats(db: AsyncSession = Depends(get_db_with_tenant)):
    """Summary stats across all subscriptions for the tenant."""
    result = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE wdl.status = 'delivered') AS delivered,
                COUNT(*) FILTER (WHERE wdl.status = 'failed')    AS failed,
                COUNT(*) FILTER (WHERE wdl.status = 'retrying')  AS retrying,
                COUNT(*) FILTER (WHERE wdl.status = 'pending')   AS pending,
                COUNT(*) FILTER (WHERE wdl.created_at >= now() - INTERVAL '24 hours') AS last_24h,
                COUNT(*) AS total
            FROM webhook_delivery_log wdl
        """)
    )
    row = dict(result.first()._mapping)
    sub_result = await db.execute(
        text("SELECT COUNT(*) FILTER (WHERE is_active) AS active, COUNT(*) AS total FROM webhook_subscriptions")
    )
    sub_row = dict(sub_result.first()._mapping)
    return {
        "subscriptions": {"active": int(sub_row["active"]), "total": int(sub_row["total"])},
        "deliveries": {k: int(v or 0) for k, v in row.items()},
    }
