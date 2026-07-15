"""Audit log write helper with HMAC-SHA256 tamper detection.

Every new row's hash covers:
  "{id}|{tenant_id}|{user_id}|{action}|{resource_type}|{resource_id}|{ip_address}|{detail_json}|{created_at_iso}|{prev_hash}"

`prev_hash` is the hash of the immediately preceding row for this tenant
(ordered by created_at DESC, id DESC), creating a forward chain. A break
anywhere in the chain means a row was modified or deleted.

Rows written before migration 0017 have NULL row_hash/prev_hash and are
skipped (not failed) during chain verification — backward compatible.
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


# Stable sentinel for the genesis prev_hash (the very first row per tenant).
_GENESIS = "0" * 64


def _canonical(
    row_id: uuid.UUID,
    tenant_id: str,
    user_id: str | None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    ip_address: str | None,
    detail: dict | str | None,
    created_at: datetime,
    prev_hash: str,
) -> str:
    detail_str = json.dumps(detail, separators=(",", ":"), sort_keys=True) if isinstance(detail, dict) else (detail or "null")
    return "|".join([
        str(row_id),
        tenant_id,
        str(user_id) if user_id else "null",
        action,
        resource_type or "null",
        str(resource_id) if resource_id else "null",
        ip_address or "null",
        detail_str,
        created_at.astimezone(timezone.utc).isoformat(),
        prev_hash,
    ])


def compute_row_hash(
    row_id: uuid.UUID,
    tenant_id: str,
    user_id: str | None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    ip_address: str | None,
    detail: dict | str | None,
    created_at: datetime,
    prev_hash: str,
) -> str:
    msg = _canonical(
        row_id, tenant_id, user_id, action,
        resource_type, resource_id, ip_address, detail,
        created_at, prev_hash,
    ).encode()
    key = settings.AUDIT_HMAC_KEY.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


async def write_audit_log(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str | None = None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    detail: dict | str | None = None,
) -> uuid.UUID:
    """Insert one audit log row with a chained HMAC hash."""
    row_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Fetch the latest row_hash for this tenant in one query.
    # Uses the SECURITY DEFINER pattern is NOT needed here because
    # app.current_tenant is already set by get_db_with_tenant.
    prev_result = await db.execute(
        text("""
            SELECT row_hash FROM audit_logs
            WHERE tenant_id = :tid AND row_hash IS NOT NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """),
        {"tid": tenant_id},
    )
    prev_row = prev_result.first()
    prev_hash = prev_row.row_hash if prev_row else _GENESIS

    row_hash = compute_row_hash(
        row_id, tenant_id, user_id, action,
        resource_type, resource_id, ip_address, detail, now, prev_hash,
    )

    detail_param = json.dumps(detail, separators=(",", ":"), sort_keys=True) if isinstance(detail, dict) else detail

    await db.execute(
        text("""
            INSERT INTO audit_logs
                (id, tenant_id, user_id, action, resource_type, resource_id,
                 ip_address, detail, row_hash, prev_hash, created_at)
            VALUES
                (:id, :tid, :uid, :action, :resource_type, :resource_id,
                 :ip_address, CAST(:detail AS jsonb), :row_hash, :prev_hash, :created_at)
        """),
        {
            "id": row_id,
            "tid": tenant_id,
            "uid": user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "detail": detail_param,
            "row_hash": row_hash,
            "prev_hash": prev_hash,
            "created_at": now,
        },
    )
    return row_id
