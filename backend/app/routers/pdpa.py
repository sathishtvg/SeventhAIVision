"""Privacy masking zones, PDPA consent management, and data subject requests (DSAR)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant, get_raw_db

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])
pdpa_router = APIRouter(prefix="/api/v1/pdpa", tags=["pdpa"])


# ── Privacy Masking Zones ─────────────────────────────────────────────────────

class PrivacyZoneCreate(BaseModel):
    camera_id: str
    name: str = "Privacy Zone"
    polygon: list[dict]  # [{"x": 0.1, "y": 0.2}, ...]
    fill_color: str = "#000000"
    is_active: bool = True


@router.get("/zones", dependencies=[Depends(require_permission("privacy:manage"))])
async def list_privacy_zones(
    db: AsyncSession = Depends(get_db_with_tenant),
    camera_id: str | None = None,
):
    params: dict = {}
    where = ""
    if camera_id:
        where = "WHERE pz.camera_id = CAST(:camera_id AS uuid)"
        params["camera_id"] = camera_id
    result = await db.execute(
        text(
            f"""
            SELECT pz.*, c.name AS camera_name
            FROM privacy_zones pz
            LEFT JOIN cameras c ON c.id = pz.camera_id
            {where}
            ORDER BY pz.created_at DESC
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@router.post("/zones", dependencies=[Depends(require_permission("privacy:manage"))])
async def create_privacy_zone(
    body: PrivacyZoneCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    import json
    result = await db.execute(
        text(
            """
            INSERT INTO privacy_zones
                (tenant_id, camera_id, name, polygon, fill_color, is_active, created_by_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:camera_id AS uuid), :name, CAST(:polygon AS jsonb), :fill_color, :is_active,
                CAST(:created_by AS uuid)
            )
            RETURNING id, camera_id, name, polygon, fill_color, is_active
            """
        ),
        {
            "camera_id": body.camera_id,
            "name": body.name,
            "polygon": json.dumps(body.polygon),
            "fill_color": body.fill_color,
            "is_active": body.is_active,
            "created_by": token.user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.delete("/zones/{zone_id}", dependencies=[Depends(require_permission("privacy:manage"))])
async def delete_privacy_zone(zone_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("DELETE FROM privacy_zones WHERE id = :id RETURNING id"),
        {"id": zone_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Privacy zone not found")
    return {"deleted": True, "id": str(row.id)}


@router.get("/zones/camera/{camera_id}")
async def get_camera_privacy_zones(camera_id: str, db: AsyncSession = Depends(get_raw_db)):
    """Public read — called by AI workers to get active masking zones before processing.

    Uses a SECURITY DEFINER function (migration 0041) that bypasses RLS so that AI
    workers can retrieve masking zones by camera_id without holding a tenant JWT.
    """
    result = await db.execute(
        text("SELECT id, polygon, fill_color FROM get_camera_privacy_zones_public(CAST(:cid AS uuid))"),
        {"cid": camera_id},
    )
    return [dict(row._mapping) for row in result]


# ── PDPA Consents ─────────────────────────────────────────────────────────────

class ConsentCreate(BaseModel):
    data_subject_name: str | None = None
    data_subject_id: str | None = None
    consent_type: str = "face_recognition"
    consented: bool = True
    consent_method: str = "physical_form"
    site_id: str | None = None
    valid_until: str | None = None
    notes: str | None = None


@pdpa_router.get("/consents", dependencies=[Depends(require_permission("pdpa:read"))])
async def list_consents(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    consent_type: str | None = None,
    consented: bool | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if site_id:
        where_clauses.append("pc.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if consent_type:
        where_clauses.append("pc.consent_type = :consent_type")
        params["consent_type"] = consent_type
    if consented is not None:
        where_clauses.append("pc.consented = :consented")
        params["consented"] = consented
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    result = await db.execute(
        text(
            f"""
            SELECT pc.*, u.full_name AS collected_by_name, s.name AS site_name
            FROM pdpa_consents pc
            LEFT JOIN users u ON u.id = pc.collected_by_user_id
            LEFT JOIN sites s ON s.id = pc.site_id
            {where}
            ORDER BY pc.created_at DESC LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@pdpa_router.post("/consents", dependencies=[Depends(require_permission("pdpa:admin"))])
async def record_consent(
    body: ConsentCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            """
            INSERT INTO pdpa_consents
                (tenant_id, site_id, data_subject_name, data_subject_id,
                 consent_type, consented, consent_method, valid_until, notes,
                 collected_by_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:site_id AS uuid), :data_subject_name, :data_subject_id,
                :consent_type, :consented, :consent_method,
                CAST(:valid_until AS timestamptz), :notes, CAST(:collected_by AS uuid)
            )
            RETURNING id, consent_type, consented, valid_from, valid_until
            """
        ),
        {
            "site_id": body.site_id,
            "data_subject_name": body.data_subject_name,
            "data_subject_id": body.data_subject_id,
            "consent_type": body.consent_type,
            "consented": body.consented,
            "consent_method": body.consent_method,
            "valid_until": body.valid_until,
            "notes": body.notes,
            "collected_by": token.user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ── Data Subject Requests (DSAR) ──────────────────────────────────────────────

class DSARCreate(BaseModel):
    request_type: str = "access"  # access | erasure | correction | portability | objection
    data_subject_name: str
    data_subject_id: str | None = None
    data_subject_email: str | None = None
    description: str | None = None


class DSARUpdate(BaseModel):
    status: str  # in_review | fulfilled | rejected | partial
    fulfillment_notes: str | None = None
    records_erased: int | None = None


@pdpa_router.get("/dsar", dependencies=[Depends(require_permission("pdpa:read"))])
async def list_dsars(
    db: AsyncSession = Depends(get_db_with_tenant),
    dsar_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if dsar_status:
        where_clauses.append("dsr.status = :dsar_status")
        params["dsar_status"] = dsar_status
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    result = await db.execute(
        text(
            f"""
            SELECT dsr.*, u.full_name AS fulfilled_by_name,
                   (dsr.deadline_at < now() AND dsr.status NOT IN ('fulfilled', 'rejected')) AS is_overdue
            FROM data_subject_requests dsr
            LEFT JOIN users u ON u.id = dsr.fulfilled_by_user_id
            {where}
            ORDER BY dsr.deadline_at ASC LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@pdpa_router.post("/dsar", dependencies=[Depends(require_permission("pdpa:admin"))])
async def create_dsar(
    body: DSARCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    if body.request_type not in ("access", "erasure", "correction", "portability", "objection"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid request_type")

    result = await db.execute(
        text(
            """
            INSERT INTO data_subject_requests
                (tenant_id, request_type, data_subject_name, data_subject_id,
                 data_subject_email, description)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                :request_type, :data_subject_name, :data_subject_id,
                :data_subject_email, :description
            )
            RETURNING id, request_type, status, deadline_at
            """
        ),
        {
            "request_type": body.request_type,
            "data_subject_name": body.data_subject_name,
            "data_subject_id": body.data_subject_id,
            "data_subject_email": body.data_subject_email,
            "description": body.description,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@pdpa_router.put("/dsar/{dsar_id}", dependencies=[Depends(require_permission("pdpa:admin"))])
async def update_dsar(
    dsar_id: str,
    body: DSARUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    valid_statuses = ("in_review", "fulfilled", "rejected", "partial")
    if body.status not in valid_statuses:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Status must be one of: {valid_statuses}")

    result = await db.execute(
        text(
            """
            UPDATE data_subject_requests SET
                status = :status,
                fulfilled_at = CASE WHEN :status_check IN ('fulfilled', 'partial') THEN now() ELSE fulfilled_at END,
                fulfilled_by_user_id = CAST(:uid AS uuid),
                fulfillment_notes = :notes,
                records_erased = COALESCE(:records_erased, records_erased),
                updated_at = now()
            WHERE id = :id
            RETURNING id, status, fulfilled_at
            """
        ),
        {
            "id": dsar_id,
            "status": body.status,
            "status_check": body.status,
            "uid": token.user_id,
            "notes": body.fulfillment_notes,
            "records_erased": body.records_erased,
        },
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DSAR not found")
    return dict(row._mapping)
