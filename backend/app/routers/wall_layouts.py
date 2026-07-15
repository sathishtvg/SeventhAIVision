"""Saved LiveWall layouts (Gap 84) — named camera-wall configurations that
follow the operator between machines.

Visibility: a user sees their own layouts plus any tenant layout marked
is_shared. Modification/deletion: owner only, or roles 1-2 (admin override).
Permission gate is camera:read — layouts are personal display config for
anyone allowed to view cameras.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/wall-layouts", tags=["wall-layouts"])

_ADMIN_ROLES = {1, 2}
_MAX_CELLS = 16


class WallCellIn(BaseModel):
    camera_id: str
    stream_id: str
    camera_name: str = ""
    site_name: str | None = None


class LayoutCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    grid_size: int = Field(default=4, ge=1, le=16)
    cells: list[WallCellIn] = Field(default_factory=list, max_length=_MAX_CELLS)
    is_shared: bool = False


class LayoutUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    grid_size: int | None = Field(default=None, ge=1, le=16)
    cells: list[WallCellIn] | None = Field(default=None, max_length=_MAX_CELLS)
    is_shared: bool | None = None


_ROW_COLS = "id, user_id, name, grid_size, cells, is_shared, created_at, updated_at"


@router.get("", dependencies=[Depends(require_permission("camera:read"))])
async def list_layouts(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """My layouts + tenant-shared layouts, mine first."""
    result = await db.execute(
        text("""
            SELECT wl.id, wl.user_id, wl.name, wl.grid_size, wl.cells,
                   wl.is_shared, wl.created_at, wl.updated_at,
                   (wl.user_id = CAST(:uid AS uuid)) AS is_mine,
                   u.full_name AS owner_name
            FROM wall_layouts wl
            JOIN users u ON u.id = wl.user_id
            WHERE wl.user_id = CAST(:uid AS uuid) OR wl.is_shared = TRUE
            ORDER BY (wl.user_id = CAST(:uid AS uuid)) DESC, wl.name
        """),
        {"uid": token.user_id},
    )
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("camera:read"))])
async def create_layout(
    body: LayoutCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    try:
        result = await db.execute(
            text(f"""
                INSERT INTO wall_layouts (tenant_id, user_id, name, grid_size, cells, is_shared)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:uid AS uuid),
                        :name, :grid, CAST(:cells AS jsonb), :shared)
                RETURNING {_ROW_COLS}
            """),
            {
                "uid": token.user_id,
                "name": body.name,
                "grid": body.grid_size,
                "cells": json.dumps([c.model_dump() for c in body.cells]),
                "shared": body.is_shared,
            },
        )
        row = result.mappings().first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"You already have a layout named '{body.name}'") from exc
        raise
    return dict(row)


async def _fetch_editable(db: AsyncSession, layout_id: str, token: TokenPayload):
    """Load a layout row; 404 unless requester is the owner or an admin."""
    result = await db.execute(
        text(f"SELECT {_ROW_COLS} FROM wall_layouts WHERE id = CAST(:id AS uuid)"),
        {"id": layout_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Layout not found")
    if str(row["user_id"]) != token.user_id and token.role_id not in _ADMIN_ROLES:
        # Non-owners can't tell whether it exists
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Layout not found")
    return row


@router.put("/{layout_id}", dependencies=[Depends(require_permission("camera:read"))])
async def update_layout(
    layout_id: str,
    body: LayoutUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _fetch_editable(db, layout_id, token)

    sets, params = [], {"id": layout_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.grid_size is not None:
        sets.append("grid_size = :grid"); params["grid"] = body.grid_size
    if body.cells is not None:
        sets.append("cells = CAST(:cells AS jsonb)")
        params["cells"] = json.dumps([c.model_dump() for c in body.cells])
    if body.is_shared is not None:
        sets.append("is_shared = :shared"); params["shared"] = body.is_shared
    if not sets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No fields to update")

    sets.append("updated_at = now()")
    try:
        result = await db.execute(
            text(f"UPDATE wall_layouts SET {', '.join(sets)} "
                 f"WHERE id = CAST(:id AS uuid) RETURNING {_ROW_COLS}"),
            params,
        )
        row = result.mappings().first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Layout name already in use") from exc
        raise
    return dict(row)


@router.delete("/{layout_id}", dependencies=[Depends(require_permission("camera:read"))])
async def delete_layout(
    layout_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _fetch_editable(db, layout_id, token)
    await db.execute(
        text("DELETE FROM wall_layouts WHERE id = CAST(:id AS uuid)"),
        {"id": layout_id},
    )
    await db.commit()
    return {"id": layout_id, "deleted": True}
