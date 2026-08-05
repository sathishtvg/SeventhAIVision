"""Hardware protocol catalogue for the admin UI.

One read-only endpoint. It exists so the device-configuration form is
generated from the backend's declaration of what each protocol needs, rather
than the frontend carrying a hand-maintained copy that drifts the first time a
protocol gains a field.

Same convention as GET /recording-policies/options and
GET /alert-rules/catalogue.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies.permissions import require_permission
from app.services.device_protocols import ConfigError, describe_family
from shared.device_protocols import FAMILIES

router = APIRouter(prefix="/api/v1/device-protocols", tags=["device-protocols"])


@router.get("", dependencies=[Depends(require_permission("device_config:read"))])
async def list_families():
    """Every family and every protocol in one call — the payload is small and
    an admin screen typically needs more than one family."""
    return {"families": [describe_family(f) for f in FAMILIES]}


@router.get("/{family}", dependencies=[Depends(require_permission("device_config:read"))])
async def get_family(family: str):
    try:
        return describe_family(family)
    except ConfigError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
