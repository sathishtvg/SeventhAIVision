"""Incident dispatch, SLA configuration, and evidence chain of custody."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])
sla_router = APIRouter(prefix="/api/v1/sla", tags=["dispatch"])
custody_router = APIRouter(prefix="/api/v1/custody", tags=["evidence"])


# ── Dispatch ──────────────────────────────────────────────────────────────────

class DispatchBody(BaseModel):
    guard_user_id: str
    dispatch_notes: str | None = None


@router.post("/incidents/{incident_id}", dependencies=[Depends(require_permission("incident:dispatch"))])
async def dispatch_guard(
    incident_id: str,
    body: DispatchBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Assign and dispatch a guard to an incident. Calculates SLA deadline from config."""
    incident_row = (
        await db.execute(
            text("SELECT id, severity FROM incidents WHERE id = :id"),
            {"id": incident_id},
        )
    ).first()
    if incident_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    sla_row = (
        await db.execute(
            text(
                """
                SELECT dispatch_within_seconds, resolve_within_seconds
                FROM sla_configs
                WHERE tenant_id = current_setting('app.current_tenant')::uuid
                  AND severity = :severity
                """
            ),
            {"severity": incident_row.severity},
        )
    ).first()
    sla_deadline_expr = "now()" if sla_row is None else f"now() + INTERVAL '{sla_row.dispatch_within_seconds} seconds'"

    result = await db.execute(
        text(
            f"""
            UPDATE incidents
            SET dispatched_guard_id = CAST(:guard_id AS uuid),
                dispatched_at = now(),
                dispatch_notes = :notes,
                sla_deadline_at = {sla_deadline_expr},
                status = CASE WHEN status = 'open' THEN 'in_progress' ELSE status END
            WHERE id = :id
            RETURNING id, dispatched_guard_id, dispatched_at, sla_deadline_at, status
            """
        ),
        {"guard_id": body.guard_user_id, "notes": body.dispatch_notes, "id": incident_id},
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.post("/incidents/{incident_id}/arrived", dependencies=[Depends(require_permission("incident:dispatch"))])
async def guard_arrived(
    incident_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            """
            UPDATE incidents SET guard_arrived_at = now()
            WHERE id = :id AND dispatched_guard_id IS NOT NULL
            RETURNING id, guard_arrived_at
            """
        ),
        {"id": incident_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found or no guard dispatched")
    return dict(row._mapping)


# ── SLA Config ────────────────────────────────────────────────────────────────

class SLAConfigBody(BaseModel):
    ack_within_seconds: int
    dispatch_within_seconds: int
    resolve_within_seconds: int
    escalation_user_id: str | None = None


@sla_router.get("/configs", dependencies=[Depends(require_permission("sla:manage"))])
async def list_sla_configs(db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            """
            SELECT sc.*, u.full_name AS escalation_user_name
            FROM sla_configs sc
            LEFT JOIN users u ON u.id = sc.escalation_user_id
            ORDER BY CASE sc.severity
                WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3
                WHEN 'low' THEN 4 ELSE 5 END
            """
        )
    )
    return [dict(row._mapping) for row in result]


@sla_router.put("/configs/{severity}", dependencies=[Depends(require_permission("sla:manage"))])
async def upsert_sla_config(
    severity: str,
    body: SLAConfigBody,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if severity not in ("critical", "high", "medium", "low", "info"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid severity")

    result = await db.execute(
        text(
            """
            INSERT INTO sla_configs (tenant_id, severity, ack_within_seconds,
                dispatch_within_seconds, resolve_within_seconds, escalation_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                :severity, :ack, :dispatch, :resolve, CAST(:escalation_user_id AS uuid)
            )
            ON CONFLICT (tenant_id, severity) DO UPDATE SET
                ack_within_seconds = EXCLUDED.ack_within_seconds,
                dispatch_within_seconds = EXCLUDED.dispatch_within_seconds,
                resolve_within_seconds = EXCLUDED.resolve_within_seconds,
                escalation_user_id = EXCLUDED.escalation_user_id,
                updated_at = now()
            RETURNING *
            """
        ),
        {
            "severity": severity,
            "ack": body.ack_within_seconds,
            "dispatch": body.dispatch_within_seconds,
            "resolve": body.resolve_within_seconds,
            "escalation_user_id": body.escalation_user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ── Evidence Chain of Custody ─────────────────────────────────────────────────

@custody_router.get("/{evidence_id}", dependencies=[Depends(require_permission("evidence:custody:read"))])
async def get_custody_log(evidence_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    rows = (
        await db.execute(
            text(
                """
                SELECT eal.*, u.full_name AS user_name, u.email AS user_email
                FROM evidence_access_log eal
                LEFT JOIN users u ON u.id = eal.user_id
                WHERE eal.evidence_id = :eid
                ORDER BY eal.accessed_at ASC
                """
            ),
            {"eid": evidence_id},
        )
    ).fetchall()
    return [dict(row._mapping) for row in rows]


@custody_router.post("/{evidence_id}", dependencies=[Depends(require_permission("evidence:read"))])
async def log_evidence_access(
    evidence_id: str,
    request: Request,
    action: str = "view",
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if action not in ("view", "download", "export", "custody_transfer"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid action")

    result = await db.execute(
        text(
            """
            INSERT INTO evidence_access_log
                (tenant_id, evidence_id, user_id, action, ip_address, user_agent)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:eid AS uuid), CAST(:uid AS uuid), :action,
                :ip, :ua
            )
            RETURNING id, accessed_at
            """
        ),
        {
            "eid": evidence_id,
            "uid": token.user_id,
            "action": action,
            "ip": request.client.host if request.client else None,
            "ua": request.headers.get("user-agent"),
        },
    )
    row = result.first()
    await db.commit()
    return {"logged": True, "id": str(row.id), "accessed_at": str(row.accessed_at)}
