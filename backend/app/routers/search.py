"""Unified cross-module search.

Single endpoint that queries multiple resource types via a PostgreSQL UNION
and returns a sorted, paginated list of matching items.

Permission gate: alert:read (minimum to use the platform).
The caller controls which modules to search via the `modules` query param;
Row-Level Security on every table enforces tenant isolation regardless.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/search", tags=["search"])

# ── Searchable sub-queries ────────────────────────────────────────────────────
# Every branch returns the same 7 columns so they can be UNIONed cleanly:
#   module TEXT, id TEXT, title TEXT, summary TEXT,
#   severity TEXT, status TEXT, created_at TIMESTAMPTZ

_MODULE_QUERIES: dict[str, str] = {
    "alerts": """
        SELECT 'alert'::text            AS module,
               id::text                AS id,
               title                   AS title,
               COALESCE(LEFT(message, 200), '')   AS summary,
               severity                AS severity,
               status                  AS status,
               created_at              AS created_at
        FROM alerts
        WHERE title ILIKE :q OR message ILIKE :q OR alert_code ILIKE :q
    """,
    "incidents": """
        SELECT 'incident'::text         AS module,
               id::text                AS id,
               title                   AS title,
               COALESCE(LEFT(description, 200), '') AS summary,
               severity                AS severity,
               status                  AS status,
               created_at              AS created_at
        FROM incidents
        WHERE title ILIKE :q OR description ILIKE :q
    """,
    "cameras": """
        SELECT 'camera'::text           AS module,
               id::text                AS id,
               name                    AS title,
               COALESCE(location, '')  AS summary,
               NULL::text              AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM cameras
        WHERE name ILIKE :q OR location ILIKE :q
    """,
    "sites": """
        SELECT 'site'::text             AS module,
               id::text                AS id,
               name                    AS title,
               COALESCE(address, '')   AS summary,
               NULL::text              AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM sites
        WHERE name ILIKE :q OR address ILIKE :q
    """,
    "watchlist": """
        SELECT 'watchlist_plate'::text  AS module,
               id::text                AS id,
               plate_number            AS title,
               COALESCE(reason, '')    AS summary,
               NULL::text              AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM watchlist_entries
        WHERE plate_number ILIKE :q OR reason ILIKE :q
    """,
    "face_watchlist": """
        SELECT 'watchlist_face'::text   AS module,
               id::text                AS id,
               person_name             AS title,
               list_type               AS summary,
               NULL::text              AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM face_watchlist_entries
        WHERE person_name ILIKE :q
    """,
    "zones": """
        SELECT 'zone'::text             AS module,
               id::text                AS id,
               name                    AS title,
               ''                      AS summary,
               severity                AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM restricted_zones
        WHERE name ILIKE :q
    """,
    "users": """
        SELECT 'user'::text             AS module,
               id::text                AS id,
               COALESCE(full_name, email) AS title,
               email                   AS summary,
               NULL::text              AS severity,
               CASE WHEN is_active THEN 'active' ELSE 'inactive' END AS status,
               created_at              AS created_at
        FROM users
        WHERE full_name ILIKE :q OR email ILIKE :q
    """,
}

# Modules included when the caller omits the `modules` param.
# `users` is excluded by default — callers with user:read can opt in explicitly.
_DEFAULT_MODULES = [
    "alerts", "incidents", "cameras", "sites",
    "watchlist", "face_watchlist", "zones",
]

_VALID_MODULES = frozenset(_MODULE_QUERIES.keys())


def _build_union(modules: list[str]) -> tuple[str, str]:
    """Return (data_sql, count_sql) for the given module list."""
    sub_queries = [_MODULE_QUERIES[m] for m in modules]
    union_body = "\nUNION ALL\n".join(f"({q})" for q in sub_queries)
    data_sql = (
        f"SELECT module, id, title, summary, severity, status, created_at\n"
        f"FROM (\n{union_body}\n) _union\n"
        f"ORDER BY created_at DESC\n"
        f"LIMIT :limit OFFSET :offset"
    )
    count_sql = (
        f"SELECT COUNT(*) FROM (\n{union_body}\n) _union"
    )
    return data_sql, count_sql


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_permission("alert:read"))])
async def unified_search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200, description="Search term"),
    modules: list[str] = Query(
        default=_DEFAULT_MODULES,
        description="Which resource types to include",
    ),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
) -> dict[str, Any]:
    # Detect explicitly empty modules list (?modules= with empty string value)
    if "modules" in request.query_params:
        raw = request.query_params.getlist("modules")
        filtered = [m for m in raw if m.strip()]
        if not filtered:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "modules must not be empty",
            )
        modules = filtered

    if not modules:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "modules must not be empty",
        )

    # Validate requested module names against the whitelist
    invalid = [m for m in modules if m not in _VALID_MODULES]
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown module(s): {invalid}. Valid: {sorted(_VALID_MODULES)}",
        )

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for m in modules:
        if m not in seen:
            seen.add(m)
            deduped.append(m)

    data_sql, count_sql = _build_union(deduped)
    pattern = f"%{q}%"
    params: dict[str, Any] = {"q": pattern}

    total_result = await db.execute(text(count_sql), params)
    total: int = total_result.scalar_one()

    params.update({"limit": limit, "offset": offset})
    rows_result = await db.execute(text(data_sql), params)
    items = [dict(r._mapping) for r in rows_result]

    return {
        "query": q,
        "modules": deduped,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
    }
