"""Multi-screen Live Wall profiles — a named, launchable set of N saved
wall_layouts screens (e.g. a 3-monitor control room). Extends the existing
wall_layouts table (Gap 84) rather than a parallel schema: each screen in a
profile is just a wall_layouts row with profile_id/screen_index set. A
standalone (non-profile) layout is unaffected — profile_id stays NULL.

Same visibility/ownership rules as wall_layouts.py: a user sees their own
profiles plus any tenant profile marked is_shared; only the owner or an
admin (roles 1-2) can modify/delete. Permission gate is camera:read, same
reasoning as wall_layouts — this is personal display configuration for
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
from app.routers.wall_layouts import WallCellIn, _ADMIN_ROLES, _MAX_CELLS

router = APIRouter(prefix="/api/v1/wall-profiles", tags=["wall-profiles"])

_MAX_SCREENS = 8


class ScreenIn(BaseModel):
    grid_size: int = Field(default=4, ge=1, le=16)
    cells: list[WallCellIn] = Field(default_factory=list, max_length=_MAX_CELLS)
    analytics_modules: list[str] = Field(default_factory=list)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_shared: bool = False
    screens: list[ScreenIn] = Field(min_length=1, max_length=_MAX_SCREENS)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_shared: bool | None = None
    screens: list[ScreenIn] | None = Field(default=None, max_length=_MAX_SCREENS)


_PROFILE_SELECT = """
    SELECT wp.id, wp.user_id, wp.name, wp.screen_count, wp.is_shared,
           wp.created_at, wp.updated_at,
           (wp.user_id = CAST(:uid AS uuid)) AS is_mine,
           u.full_name AS owner_name,
           COALESCE(
             (SELECT jsonb_agg(jsonb_build_object(
                 'id', wl.id, 'screen_index', wl.screen_index, 'name', wl.name,
                 'grid_size', wl.grid_size, 'cells', wl.cells,
                 'analytics_modules', wl.analytics_modules
               ) ORDER BY wl.screen_index)
              FROM wall_layouts wl WHERE wl.profile_id = wp.id),
             '[]'::jsonb
           ) AS screens
    FROM wall_profiles wp
    JOIN users u ON u.id = wp.user_id
"""


async def _insert_screen(
    db: AsyncSession, token: TokenPayload, profile_id: str, profile_name: str,
    idx: int, screen: ScreenIn,
) -> None:
    await db.execute(
        text("""
            INSERT INTO wall_layouts
                (tenant_id, user_id, name, grid_size, cells, is_shared,
                 profile_id, screen_index, analytics_modules)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:uid AS uuid),
                    :name, :grid, CAST(:cells AS jsonb), FALSE,
                    CAST(:profile_id AS uuid), :idx, CAST(:analytics AS jsonb))
        """),
        {
            "uid": token.user_id,
            "name": f"{profile_name} — Screen {idx + 1}",
            "grid": screen.grid_size,
            "cells": json.dumps([c.model_dump() for c in screen.cells]),
            "profile_id": profile_id,
            "idx": idx,
            "analytics": json.dumps(screen.analytics_modules),
        },
    )


async def _reset_tenant_context(db: AsyncSession, token: TokenPayload) -> None:
    """Re-applies the RLS tenant GUC after a mid-request db.commit().

    get_db_with_tenant sets app.current_tenant with is_local=true (SET
    LOCAL semantics) once, at the start of the request's first
    transaction. Committing ends that transaction, so any further query in
    the same request runs with the GUC reverted — every RLS-protected
    table (wall_profiles, wall_layouts, users) then filters to zero rows,
    and a WHERE/policy comparison against the reverted value can raise
    "invalid input syntax for type uuid" instead of just returning empty.
    Endpoints that commit and then issue a follow-up query must call this
    first.
    """
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": token.tenant_id},
    )


async def _fetch_profile(db: AsyncSession, profile_id: str, token: TokenPayload):
    result = await db.execute(
        text(f"{_PROFILE_SELECT} WHERE wp.id = CAST(:id AS uuid)"),
        {"id": profile_id, "uid": token.user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return dict(row)


async def _fetch_editable_profile(db: AsyncSession, profile_id: str, token: TokenPayload):
    """Load a profile row; 404 unless requester is the owner or an admin."""
    result = await db.execute(
        text("SELECT id, user_id, name, screen_count FROM wall_profiles WHERE id = CAST(:id AS uuid)"),
        {"id": profile_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    if str(row["user_id"]) != token.user_id and token.role_id not in _ADMIN_ROLES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return row


@router.get("", dependencies=[Depends(require_permission("camera:read"))])
async def list_profiles(
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """My profiles + tenant-shared profiles, mine first, each with its
    screens nested (ordered by screen_index)."""
    result = await db.execute(
        text(f"""
            {_PROFILE_SELECT}
            WHERE wp.user_id = CAST(:uid AS uuid) OR wp.is_shared = TRUE
            ORDER BY (wp.user_id = CAST(:uid AS uuid)) DESC, wp.name
        """),
        {"uid": token.user_id},
    )
    return [dict(r._mapping) for r in result]


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("camera:read"))])
async def create_profile(
    body: ProfileCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    try:
        result = await db.execute(
            text("""
                INSERT INTO wall_profiles (tenant_id, user_id, name, screen_count, is_shared)
                VALUES (current_setting('app.current_tenant')::uuid, CAST(:uid AS uuid),
                        :name, :count, :shared)
                RETURNING id
            """),
            {"uid": token.user_id, "name": body.name, "count": len(body.screens), "shared": body.is_shared},
        )
        profile_id = str(result.scalar_one())

        for idx, screen in enumerate(body.screens):
            await _insert_screen(db, token, profile_id, body.name, idx, screen)

        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"You already have a profile named '{body.name}'") from exc
        raise

    await _reset_tenant_context(db, token)
    return await _fetch_profile(db, profile_id, token)


@router.put("/{profile_id}", dependencies=[Depends(require_permission("camera:read"))])
async def update_profile(
    profile_id: str,
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    existing = await _fetch_editable_profile(db, profile_id, token)
    new_name = body.name if body.name is not None else existing["name"]

    try:
        sets, params = [], {"id": profile_id}
        if body.name is not None:
            sets.append("name = :name"); params["name"] = body.name
        if body.is_shared is not None:
            sets.append("is_shared = :shared"); params["shared"] = body.is_shared
        if body.screens is not None:
            sets.append("screen_count = :count"); params["count"] = len(body.screens)
        if sets:
            sets.append("updated_at = now()")
            await db.execute(
                text(f"UPDATE wall_profiles SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"),
                params,
            )

        if body.screens is not None:
            # Replace-on-edit: simplest correct approach given the small N —
            # same precedent as roster.py's draft-shift replace-on-edit.
            await db.execute(
                text("DELETE FROM wall_layouts WHERE profile_id = CAST(:id AS uuid)"),
                {"id": profile_id},
            )
            for idx, screen in enumerate(body.screens):
                await _insert_screen(db, token, profile_id, new_name, idx, screen)

        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Profile name already in use") from exc
        raise

    await _reset_tenant_context(db, token)
    return await _fetch_profile(db, profile_id, token)


@router.post("/{profile_id}/screens", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("camera:read"))])
async def add_screen(
    profile_id: str,
    body: ScreenIn,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Append one more screen to an existing profile — the "need more
    screens later" path — without disturbing already-configured screens."""
    existing = await _fetch_editable_profile(db, profile_id, token)
    if existing["screen_count"] >= _MAX_SCREENS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"A profile can have at most {_MAX_SCREENS} screens")

    await _insert_screen(db, token, profile_id, existing["name"], existing["screen_count"], body)
    await db.execute(
        text("UPDATE wall_profiles SET screen_count = screen_count + 1, updated_at = now() "
             "WHERE id = CAST(:id AS uuid)"),
        {"id": profile_id},
    )
    await db.commit()
    await _reset_tenant_context(db, token)
    return await _fetch_profile(db, profile_id, token)


@router.delete("/{profile_id}", dependencies=[Depends(require_permission("camera:read"))])
async def delete_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    await _fetch_editable_profile(db, profile_id, token)
    # Child wall_layouts rows cascade via profile_id's ON DELETE CASCADE.
    await db.execute(
        text("DELETE FROM wall_profiles WHERE id = CAST(:id AS uuid)"),
        {"id": profile_id},
    )
    await db.commit()
    return {"id": profile_id, "deleted": True}
