import json

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant
from app.services.audit import compute_row_hash

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", dependencies=[Depends(require_permission("audit:read"))])
async def list_audit_logs(
    db: AsyncSession = Depends(get_db_with_tenant),
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {}
    if action:
        where_clauses.append("action = :action")
        params["action"] = action
    if resource_type:
        where_clauses.append("resource_type = :resource_type")
        params["resource_type"] = resource_type
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    data_sql = f"""
        SELECT id, user_id, action, resource_type, resource_id,
               ip_address, detail, created_at,
               CASE WHEN row_hash IS NOT NULL THEN true ELSE false END AS has_hash
        FROM audit_logs
        {where}
        ORDER BY created_at DESC LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM audit_logs {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.get("/verify", dependencies=[Depends(require_permission("audit:verify"))])
async def verify_chain(
    db: AsyncSession = Depends(get_db_with_tenant),
    limit: int = 500,
):
    """Re-compute HMAC hashes for the N most-recent signed rows and report any
    chain breaks.

    Rows with NULL row_hash (written before migration 0017) are skipped.
    A tampered_count > 0 means at least one row was modified or a deletion
    broke the chain.

    Returns:
        verified:        True if every checked row's hash matches exactly
        total_checked:   Number of rows with row_hash IS NOT NULL that were checked
        tampered_count:  Number of rows where computed hash != stored hash
        tampered_ids:    UUIDs of the offending rows (up to 50 returned)
        chain_broken:    True if any prev_hash link is inconsistent
    """
    rows = (await db.execute(
        text("""
            SELECT id, tenant_id, user_id, action, resource_type, resource_id,
                   ip_address, detail, created_at, row_hash, prev_hash
            FROM audit_logs
            WHERE row_hash IS NOT NULL
            ORDER BY created_at ASC, id ASC
            LIMIT :limit
        """),
        {"limit": min(limit, 2000)},
    )).fetchall()

    tampered_ids: list[str] = []
    chain_broken = False
    expected_prev: str | None = None  # None = haven't started the chain yet

    for row in rows:
        detail_raw = row.detail
        if detail_raw is not None and not isinstance(detail_raw, (dict, str)):
            detail_raw = json.loads(str(detail_raw))

        computed = compute_row_hash(
            row_id=row.id,
            tenant_id=str(row.tenant_id),
            user_id=str(row.user_id) if row.user_id else None,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=str(row.resource_id) if row.resource_id else None,
            ip_address=row.ip_address,
            detail=detail_raw,
            created_at=row.created_at,
            prev_hash=row.prev_hash,
        )

        if computed != row.row_hash:
            tampered_ids.append(str(row.id))

        # Check chain continuity: this row's prev_hash should equal
        # the immediately preceding row's row_hash.
        if expected_prev is not None and row.prev_hash != expected_prev:
            chain_broken = True

        expected_prev = row.row_hash

    total_checked = len(rows)
    tampered_count = len(tampered_ids)

    return {
        "verified": tampered_count == 0 and not chain_broken,
        "total_checked": total_checked,
        "tampered_count": tampered_count,
        "tampered_ids": tampered_ids[:50],
        "chain_broken": chain_broken,
    }
