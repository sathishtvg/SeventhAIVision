"""Digital Occurrence Book (DOB) — Singapore legally mandated append-only log.

Under the Private Security Industry Act (PSIA) / PLRD, licensed security agencies
must maintain a Daily Occurrence Book. This router provides a digital version:
append-only entries with immutable timestamps, no editing or deletion.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/dob", tags=["guard-ops"])

VALID_ENTRY_TYPES = {
    "general", "incident", "patrol_start", "patrol_end", "visitor_arrival",
    "visitor_departure", "guard_relief", "equipment_check", "maintenance",
    "alarm_activation", "fire_drill", "sos", "handover",
}


class DOBEntryCreate(BaseModel):
    entry_type: str = "general"
    body: str
    severity: str | None = None
    site_id: str | None = None
    shift_id: str | None = None
    related_alert_id: str | None = None
    related_incident_id: str | None = None
    # Offline sync (Gap 88): the ORIGINAL occurrence time, replayed later by
    # the mobile outbox. None = written online right now.
    occurred_at: str | None = None


@router.post("", dependencies=[Depends(require_permission("dob:write"))])
async def create_dob_entry(
    body: DOBEntryCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    if body.entry_type not in VALID_ENTRY_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid entry_type. Must be one of: {', '.join(sorted(VALID_ENTRY_TYPES))}",
        )

    from app.core.client_time import parse_client_timestamp

    client_occurred_at = parse_client_timestamp(body.occurred_at, "occurred_at")

    result = await db.execute(
        text(
            """
            INSERT INTO occurrence_book_entries
                (tenant_id, author_user_id, entry_type, body, severity,
                 site_id, shift_id, related_alert_id, related_incident_id, occurred_at)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:author_id AS uuid), :entry_type, :body, :severity,
                CAST(:site_id AS uuid), CAST(:shift_id AS uuid),
                CAST(:related_alert_id AS uuid), CAST(:related_incident_id AS uuid),
                COALESCE(:occurred_at, now())
            )
            RETURNING id, entry_type, body, severity, occurred_at,
                      site_id, shift_id, related_alert_id, related_incident_id
            """
        ),
        {
            "author_id": token.user_id,
            "entry_type": body.entry_type,
            "body": body.body,
            "severity": body.severity,
            "site_id": body.site_id,
            "shift_id": body.shift_id,
            "related_alert_id": body.related_alert_id,
            "related_incident_id": body.related_incident_id,
            "occurred_at": client_occurred_at,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("", dependencies=[Depends(require_permission("dob:read"))])
async def list_dob_entries(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    shift_id: str | None = None,
    entry_type: str | None = None,
    date_from: str | None = None,
    date_until: str | None = None,
    limit: int = 100,
    offset: int = 0,
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    where_clauses = []
    params: dict = {"limit": min(limit, 500), "offset": max(offset, 0)}
    scope = site_scope_clause(allowed_sites, "e.site_id", params)
    if scope:
        where_clauses.append(scope)
    if site_id:
        where_clauses.append("e.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if shift_id:
        where_clauses.append("e.shift_id = CAST(:shift_id AS uuid)")
        params["shift_id"] = shift_id
    if entry_type:
        where_clauses.append("e.entry_type = :entry_type")
        params["entry_type"] = entry_type
    if date_from:
        where_clauses.append("e.occurred_at >= CAST(:date_from AS timestamptz)")
        params["date_from"] = date_from
    if date_until:
        where_clauses.append("e.occurred_at <= CAST(:date_until AS timestamptz)")
        params["date_until"] = date_until
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    query = f"""
        SELECT e.id, e.entry_type, e.body, e.severity, e.occurred_at,
               e.site_id, e.shift_id, e.related_alert_id, e.related_incident_id,
               u.full_name AS author_name, u.email AS author_email,
               s.name AS site_name
        FROM occurrence_book_entries e
        LEFT JOIN users u ON u.id = e.author_user_id
        LEFT JOIN sites s ON s.id = e.site_id
        {where}
        ORDER BY e.occurred_at DESC LIMIT :limit OFFSET :offset
    """
    result = await db.execute(text(query), params)
    return [dict(row._mapping) for row in result]


@router.get("/{entry_id}", dependencies=[Depends(require_permission("dob:read"))])
async def get_dob_entry(
    entry_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed_sites: list[str] | None = Depends(get_allowed_site_ids),
):
    result = await db.execute(
        text(
            """
            SELECT e.*, u.full_name AS author_name, s.name AS site_name
            FROM occurrence_book_entries e
            LEFT JOIN users u ON u.id = e.author_user_id
            LEFT JOIN sites s ON s.id = e.site_id
            WHERE e.id = :id
            """
        ),
        {"id": entry_id},
    )
    row = result.first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DOB entry not found")
    d = dict(row._mapping)
    if not is_site_allowed(allowed_sites, d.get("site_id")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DOB entry not found")
    return d
