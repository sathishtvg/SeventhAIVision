"""The organisation's case escalation timings, and a case's escalations.

Phase 3 of closing the feature reference's known limits (4 November 2026; services/case_escalation.py).

  GET /api/v1/case-escalations/settings          the timings, and whether the caller may change them   case:read
  PUT /api/v1/case-escalations/settings          change them                                           settings:write
  GET /api/v1/case-escalations/cases/{case_id}   a case's escalations, newest first                    case:read

The timings are changed by a signed-in person who may change the organisation's settings, not an API key and not a
support session, and the change is audited with what they were and what they became. Changing them sends nothing:
the next pass judges by them.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids
from app.dependencies.tenant import get_db_with_tenant
from app.routers.cases import _a_person, _case
from app.routers.operations_board import held_by
from app.services import case_escalation as escalation
from app.services import intel_audit

router = APIRouter(prefix="/api/v1/case-escalations", tags=["cases"])
_READ = [Depends(require_permission("case:read"))]
_SET = [Depends(require_permission("settings:write"))]
FIELDS = tuple(escalation.LIMITS)


def _shown(config: dict, may_set: bool) -> dict:
    return {**{k: config[k] for k in FIELDS}, "since": config["since"], "updated_at": config["updated_at"],
            "updated_by_name": config["updated_by_name"], "defaults": escalation.DEFAULTS,
            "limits": {k: {"least": a, "most": b} for k, (a, b) in escalation.LIMITS.items()},
            "quiet_second_days": escalation.QUIET_SECOND_DAYS, "may_set": may_set}


@router.get("/settings", dependencies=_READ)
async def read_settings(db: AsyncSession = Depends(get_db_with_tenant),
                        token: TokenPayload = Depends(get_token_payload)):
    """The organisation's timings: the defaults until it changes them."""
    held = await held_by(db, token.role_id, ("settings:write",))
    may = "settings:write" in held and not token.via_api_key and not token.support_session_id
    return _shown(await escalation.settings(db), may)


class TimingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quiet_high_days: int = Field(..., ge=1, le=90)
    quiet_normal_days: int = Field(..., ge=1, le=90)
    quiet_low_days: int = Field(..., ge=1, le=90)
    overdue_hours: int = Field(..., ge=1, le=168)
    close_wait_days: int = Field(..., ge=1, le=30)


@router.put("/settings", dependencies=_SET)
async def set_settings(body: TimingsBody, request: Request, db: AsyncSession = Depends(get_db_with_tenant),
                       token: TokenPayload = Depends(get_token_payload)):
    """Change the timings. Nothing is sent by this: the next pass judges by them."""
    _a_person(token)
    before = await escalation.settings(db, make=True)
    values = body.model_dump()
    await db.execute(text(f"""
        UPDATE case_escalation_settings
           SET {', '.join(f'{k} = :{k}' for k in FIELDS)}, updated_by_user_id = CAST(:who AS uuid), updated_at = now()
         WHERE tenant_id = current_setting('app.current_tenant')::uuid
    """), {**values, "who": token.user_id})
    await intel_audit.record(db, request, token, "case.escalation.settings", "case_escalation_settings", None,
                             detail={"from": {k: before[k] for k in FIELDS}, "to": values})
    after = await escalation.settings(db)
    await db.commit()
    return _shown(after, True)


@router.get("/cases/{case_id:uuid}", dependencies=_READ)
async def case_escalations(case_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                           allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """A case's escalations, newest first: what fell due, when it was sent, and how many people it was for."""
    await _case(db, case_id, allowed)
    rows = await escalation.for_case(db, case_id)
    if not rows:
        return {"escalations": [], "note": "Nothing about this case has escalated."}
    return {"escalations": rows, "note": None}

