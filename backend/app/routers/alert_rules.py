"""Tenant-configurable AI alert rules (Module 14).

Reads always return the whole merged matrix — see services/alert_rules.py for
why. Writes are per (module, trigger) upserts, and DELETE means "go back to the
shipped default", which is distinct from setting is_enabled=false ("never alert
on this").

Permission split matches recording_policy (migration 0080): supervisors read,
admins/managers write. Severity decides who gets paged in the middle of the
night; retuning that is an admin decision with an audit trail.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.alert_rules import (
    RULE_COLUMNS,
    get_effective_rules,
    is_known_trigger,
    validate_rule,
)
from shared.alert_rules import MODULE_TRIGGERS, SEVERITIES

router = APIRouter(prefix="/api/v1/alert-rules", tags=["alert-rules"])


class RuleUpsert(BaseModel):
    severity: str
    create_incident: bool = False
    incident_severity: str | None = None
    is_enabled: bool = True


@router.get("", dependencies=[Depends(require_permission("alert_rule:read"))])
async def list_effective_rules(db: AsyncSession = Depends(get_db_with_tenant)):
    return await get_effective_rules(db)


@router.get("/catalogue", dependencies=[Depends(require_permission("alert_rule:read"))])
async def get_catalogue():
    """Which modules and triggers exist, and the valid severities.

    Served rather than hardcoded in the frontend so a new AI module's triggers
    appear in the editor without a second edit in TypeScript.
    """
    return {
        "modules": {m: list(t) for m, t in MODULE_TRIGGERS.items()},
        "severities": list(SEVERITIES),
    }


@router.put(
    "/{module_type}/{trigger_key}",
    dependencies=[Depends(require_permission("alert_rule:manage"))],
)
async def upsert_rule(
    module_type: str,
    trigger_key: str,
    body: RuleUpsert,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if not is_known_trigger(module_type, trigger_key):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"unknown rule '{module_type}/{trigger_key}' — see GET /alert-rules/catalogue",
        )
    try:
        validate_rule(body.severity, body.create_incident, body.incident_severity)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = (
        await db.execute(
            text(f"""
                INSERT INTO alert_rules (
                    tenant_id, module_type, trigger_key, severity,
                    create_incident, incident_severity, is_enabled, updated_by_user_id
                ) VALUES (
                    CAST(:tid AS uuid), :module_type, :trigger_key, :severity,
                    :create_incident, :incident_severity, :is_enabled, CAST(:uid AS uuid)
                )
                ON CONFLICT (tenant_id, module_type, trigger_key) DO UPDATE SET
                    severity           = EXCLUDED.severity,
                    create_incident    = EXCLUDED.create_incident,
                    incident_severity  = EXCLUDED.incident_severity,
                    is_enabled         = EXCLUDED.is_enabled,
                    updated_by_user_id = EXCLUDED.updated_by_user_id,
                    updated_at         = now()
                RETURNING {RULE_COLUMNS}
            """),
            {
                "tid": token.tenant_id,
                "uid": token.user_id,
                "module_type": module_type,
                "trigger_key": trigger_key,
                **body.model_dump(),
            },
        )
    ).first()
    await db.commit()
    return dict(row._mapping)


@router.delete(
    "/{module_type}/{trigger_key}",
    dependencies=[Depends(require_permission("alert_rule:manage"))],
)
async def reset_rule(
    module_type: str, trigger_key: str, db: AsyncSession = Depends(get_db_with_tenant)
):
    """Drop the override so the shipped default applies again."""
    result = await db.execute(
        text("DELETE FROM alert_rules WHERE module_type = :m AND trigger_key = :t"),
        {"m": module_type, "t": trigger_key},
    )
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no override set for this rule"
        )
    await db.commit()
    return {"reset": True, "module_type": module_type, "trigger_key": trigger_key}
