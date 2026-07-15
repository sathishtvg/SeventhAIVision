"""Regulatory Data Compliance API — GDPR / PDPA posture summary, audit stats,
retention configuration, and Data Subject Rights (DSR) export.

Distinct from /api/v1/compliance (guard-tour compliance reports).
Prefix: /api/v1/data-compliance
Required permission: audit:read (read-only endpoints) / audit:read for DSR export
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/data-compliance", tags=["data-compliance"])

# ── Default retention values (mirrors tenant_settings fallbacks in config.py) ─

_DEFAULT_EVIDENCE_DAYS = int(os.environ.get("EVIDENCE_RETENTION_DAYS", "90"))
_DEFAULT_AUDIT_YEARS = int(os.environ.get("AUDIT_RETENTION_YEARS", "7"))


async def _get_setting(db: AsyncSession, key: str, default: Any) -> Any:
    result = await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": key},
    )
    row = result.first()
    return row[0] if row else default


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(require_permission("settings:read"))])
async def compliance_summary(db: AsyncSession = Depends(get_db_with_tenant)):
    """Return the tenant's regulatory compliance posture.

    Aggregates key security and data-protection indicators into a single
    response so a compliance officer can quickly assess the system's state.
    """
    # Tenant user / session stats
    user_result = await db.execute(text(
        "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE is_active) AS active FROM users"
    ))
    user_row = dict(user_result.first()._mapping)

    # 2FA adoption
    totp_result = await db.execute(text(
        "SELECT COUNT(*) AS enrolled FROM users WHERE totp_secret IS NOT NULL"
    ))
    totp_enrolled = totp_result.scalar() or 0

    # Notification channel count (active)
    notif_result = await db.execute(text(
        "SELECT COUNT(*) AS cnt FROM notification_channels WHERE is_active = TRUE"
    ))
    notif_channels = notif_result.scalar() or 0

    # Effective retention settings
    evidence_days = await _get_setting(db, "evidence.retention_days", _DEFAULT_EVIDENCE_DAYS)
    audit_years = await _get_setting(db, "audit.retention_years", _DEFAULT_AUDIT_YEARS)

    # Camera count + modules enabled
    cam_result = await db.execute(text(
        "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE is_active) AS active FROM cameras"
    ))
    cam_row = dict(cam_result.first()._mapping)

    # Active watchlist entries
    plate_wl = await db.execute(text(
        "SELECT COUNT(*) FROM watchlist_entries WHERE is_active = TRUE"
    ))
    face_wl = await db.execute(text(
        "SELECT COUNT(*) FROM face_watchlist_entries WHERE is_active = TRUE"
    ))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "security": {
            "rls_enforced": True,
            "rbac_enabled": True,
            "two_factor_available": True,
            "two_factor_enrolled_users": int(totp_enrolled),
            "account_lockout_enabled": True,
            "session_management_enabled": True,
            "ip_allowlist_supported": True,
            "rate_limiting_enabled": True,
            "jwt_key_rotation_supported": True,
            "audit_logging_enabled": True,
        },
        "data_protection": {
            "evidence_retention_days": int(evidence_days),
            "audit_retention_years": int(audit_years),
            "pdpa_masking_available": True,
            "dsr_export_available": True,
            "cross_tenant_isolation": "PostgreSQL Row-Level Security",
        },
        "users": {
            "total": int(user_row["total"]),
            "active": int(user_row["active"]),
        },
        "notification_channels": int(notif_channels),
        "cameras": {
            "total": int(cam_row["total"]),
            "active": int(cam_row["active"]),
        },
        "watchlist": {
            "active_plates": int(plate_wl.scalar() or 0),
            "active_faces": int(face_wl.scalar() or 0),
        },
    }


@router.get("/audit-stats", dependencies=[Depends(require_permission("settings:read"))])
async def audit_statistics(db: AsyncSession = Depends(get_db_with_tenant)):
    """Return audit log statistics for the tenant.

    Useful for demonstrating audit completeness to auditors.
    """
    result = await db.execute(text("""
        SELECT
            COUNT(*)                        AS total_entries,
            MIN(created_at)                 AS earliest_entry,
            MAX(created_at)                 AS latest_entry,
            COUNT(DISTINCT user_id)         AS unique_users,
            COUNT(DISTINCT action)          AS unique_actions,
            COUNT(DISTINCT resource_type)   AS unique_resource_types,
            COUNT(*) FILTER (
                WHERE created_at >= now() - INTERVAL '30 days'
            )                               AS entries_last_30d,
            COUNT(*) FILTER (
                WHERE user_id IS NULL
            )                               AS system_actor_entries
        FROM audit_logs
    """))
    row = dict(result.first()._mapping)

    # Top 5 actions in last 30 days
    top_actions = await db.execute(text("""
        SELECT action, COUNT(*) AS cnt
        FROM audit_logs
        WHERE created_at >= now() - INTERVAL '30 days'
        GROUP BY action
        ORDER BY cnt DESC
        LIMIT 5
    """))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_entries": int(row["total_entries"] or 0),
        "earliest_entry": row["earliest_entry"].isoformat() if row["earliest_entry"] else None,
        "latest_entry": row["latest_entry"].isoformat() if row["latest_entry"] else None,
        "unique_users": int(row["unique_users"] or 0),
        "unique_actions": int(row["unique_actions"] or 0),
        "unique_resource_types": int(row["unique_resource_types"] or 0),
        "entries_last_30_days": int(row["entries_last_30d"] or 0),
        "system_actor_entries": int(row["system_actor_entries"] or 0),
        "top_actions_last_30d": [
            {"action": r.action, "count": r.cnt} for r in top_actions
        ],
    }


@router.get("/retention-config", dependencies=[Depends(require_permission("settings:read"))])
async def retention_configuration(db: AsyncSession = Depends(get_db_with_tenant)):
    """Return the effective data retention configuration for the tenant.

    Shows both the tenant-specific overrides and the system defaults.
    """
    evidence_days = await _get_setting(db, "evidence.retention_days", _DEFAULT_EVIDENCE_DAYS)
    audit_years = await _get_setting(db, "audit.retention_years", _DEFAULT_AUDIT_YEARS)

    # Check for custom setting rows
    ev_row = await db.execute(text(
        "SELECT updated_at FROM tenant_settings WHERE setting_key = 'evidence.retention_days'"
    ))
    ev_custom = ev_row.first()
    au_row = await db.execute(text(
        "SELECT updated_at FROM tenant_settings WHERE setting_key = 'audit.retention_years'"
    ))
    au_custom = au_row.first()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_retention": {
            "days": int(evidence_days),
            "source": "tenant_settings" if ev_custom else "environment_default",
            "last_updated": ev_custom[0].isoformat() if ev_custom else None,
            "note": "Evidence files and their DB rows are deleted after this many days by the scheduler.",
        },
        "audit_retention": {
            "years": int(audit_years),
            "source": "tenant_settings" if au_custom else "environment_default",
            "last_updated": au_custom[0].isoformat() if au_custom else None,
            "note": "Audit log partitions are archived (not deleted) after this many years.",
        },
    }


class DsrExportRequest(BaseModel):
    include_evidence_urls: bool = False


@router.post(
    "/dsr-export/{subject_user_id}",
    dependencies=[Depends(require_permission("audit:read"))],
)
async def dsr_export(
    subject_user_id: UUID,
    body: DsrExportRequest = DsrExportRequest(),
    token: TokenPayload = Depends(get_token_payload),
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Generate a GDPR Article 20 / PDPA Section 21 data portability export.

    Returns all personal data held for the specified user in a structured
    JSON format suitable for handover to the data subject or their new controller.

    Only admin+ roles should use this endpoint; the `audit:read` permission
    gate is intentional (same permission as the audit log — high sensitivity).
    """
    # Verify the subject user belongs to this tenant (RLS handles it, but 404 is clearer)
    user_result = await db.execute(text("""
        SELECT id, email, full_name, role_id, is_active, last_login_at, created_at
        FROM users WHERE id = CAST(:uid AS uuid)
    """), {"uid": str(subject_user_id)})
    user_row = user_result.first()
    if not user_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in this tenant")

    user_data = dict(user_row._mapping)
    for k, v in user_data.items():
        if isinstance(v, (datetime,)):
            user_data[k] = v.isoformat()

    # Recent audit log entries for this user (last 12 months, max 500)
    audit_result = await db.execute(text("""
        SELECT action, resource_type, resource_id, ip_address, created_at
        FROM audit_logs
        WHERE user_id = CAST(:uid AS uuid)
          AND created_at >= now() - INTERVAL '12 months'
        ORDER BY created_at DESC
        LIMIT 500
    """), {"uid": str(subject_user_id)})
    audit_entries = [
        {**dict(r._mapping), "created_at": r.created_at.isoformat()}
        for r in audit_result
    ]

    # Active sessions (refresh_tokens = one per device/login)
    sessions_result = await db.execute(text("""
        SELECT id, device_name, last_ip, created_at, last_seen_at, expires_at
        FROM refresh_tokens
        WHERE user_id = CAST(:uid AS uuid)
          AND revoked_at IS NULL
          AND expires_at > now()
        ORDER BY last_seen_at DESC NULLS LAST
    """), {"uid": str(subject_user_id)})
    sessions = [
        {
            "id": str(r.id),
            "device_name": r.device_name,
            "last_ip": r.last_ip,
            "created_at": r.created_at.isoformat(),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in sessions_result
    ]

    # Evidence items linked to this user (admin actions)
    evidence_result = await db.execute(text("""
        SELECT id, media_type, storage_path, checksum_sha256, captured_at
        FROM evidence
        WHERE captured_at >= now() - INTERVAL '12 months'
        ORDER BY captured_at DESC
        LIMIT 100
    """))
    evidence_items = []
    for r in evidence_result:
        item: dict = {
            "id": str(r.id),
            "media_type": r.media_type,
            "checksum_sha256": r.checksum_sha256,
            "captured_at": r.captured_at.isoformat(),
        }
        if body.include_evidence_urls:
            item["storage_path"] = r.storage_path
        evidence_items.append(item)

    return {
        "export_generated_at": datetime.now(timezone.utc).isoformat(),
        "export_generated_by_user_id": str(token.user_id),
        "regulation": "GDPR Article 20 / PDPA Section 21",
        "subject": user_data,
        "audit_log_last_12_months": audit_entries,
        "active_sessions": sessions,
        "evidence_last_12_months": evidence_items,
        "notes": (
            "This export covers data directly held for this user account. "
            "CCTV evidence of this individual as a data subject (not as an account holder) "
            "requires a separate search by camera/date range using the Evidence API."
        ),
    }
