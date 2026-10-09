"""Privacy masking zones, PDPA consent management, and data subject requests (DSAR)."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, site_scope_clause
from app.dependencies.tenant import get_db_with_tenant
from app.services import intel_audit, privacy_mask

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])
pdpa_router = APIRouter(prefix="/api/v1/pdpa", tags=["pdpa"])

logger = logging.getLogger(__name__)


# ── Privacy Masking Zones ─────────────────────────────────────────────────────

# A zone is applied (services/privacy_mask.py): what is under it is painted out
# of the camera's live view, of what the AI is given, of recordings and of the
# images a patrol keeps, within about ten seconds of its being drawn. So drawing
# one and deleting one are each a person's act, on a camera they may see, and
# each is one line in the audit log.

class PrivacyZoneCreate(BaseModel):
    camera_id: uuid.UUID
    name: str = "Privacy Zone"
    polygon: list[dict]  # [{"x": 0.1, "y": 0.2}, ...], each a share of the picture
    fill_color: str = "#000000"
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _a_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("A zone's name is 1 to 100 characters.")
        return value

    @field_validator("polygon")
    @classmethod
    def _a_polygon(cls, value: list[dict]) -> list[dict]:
        privacy_mask.points_of(value)
        return value

    @field_validator("fill_color")
    @classmethod
    def _a_colour(cls, value: str) -> str:
        if not privacy_mask.is_colour(value):
            raise ValueError("A zone's colour is written #RRGGBB.")
        return value


def _a_person(token: TokenPayload) -> None:
    """What a camera shows and records is changed by somebody, by name."""
    if token.via_api_key:
        raise HTTPException(403, "This is done by a person who is signed in, not by an API key.")
    if token.support_session_id:
        raise HTTPException(403, "This is done by the organisation's own staff, not from a support session.")


@router.get("/zones", dependencies=[Depends(require_permission("privacy:manage"))])
async def list_privacy_zones(
    db: AsyncSession = Depends(get_db_with_tenant),
    camera_id: uuid.UUID | None = None,
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The zones of the cameras the caller may see, the newest first."""
    params: dict = {}
    where = []
    if camera_id:
        where.append("pz.camera_id = CAST(:camera_id AS uuid)")
        params["camera_id"] = str(camera_id)
    scope = site_scope_clause(allowed, "c.site_id", params)
    if scope:
        where.append(scope)
    result = await db.execute(
        text(
            f"""
            SELECT pz.*, c.name AS camera_name, u.full_name AS created_by_name
            FROM privacy_zones pz
            JOIN cameras c ON c.id = pz.camera_id
            LEFT JOIN users u ON u.id = pz.created_by_user_id
            {('WHERE ' + ' AND '.join(where)) if where else ''}
            ORDER BY pz.created_at DESC
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@router.get("/masked-cameras", dependencies=[Depends(require_permission("camera:read"))])
async def masked_cameras(
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Which of the cameras the caller may see have a privacy zone now. A live
    wall asks once, and shows those through the masked view: they have no HLS
    one (routers/streams.py). It says that a camera is masked, not where."""
    params: dict = {}
    scope = site_scope_clause(allowed, "c.site_id", params)
    rows = (await db.execute(text(f"""
        SELECT DISTINCT pz.camera_id FROM privacy_zones pz JOIN cameras c ON c.id = pz.camera_id
         WHERE pz.is_active = TRUE {('AND ' + scope) if scope else ''}
    """), params)).all()
    return {"camera_ids": sorted(str(r[0]) for r in rows), "refresh_seconds": privacy_mask.REFRESH_SECONDS}


@router.post("/zones", dependencies=[Depends(require_permission("privacy:manage"))])
async def create_privacy_zone(
    body: PrivacyZoneCreate,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Draw a zone on a camera. From about ten seconds later what is under it
    is painted out of everything the platform shows, analyses and records of
    that camera. What was recorded before is not changed."""
    _a_person(token)
    camera_id = str(body.camera_id)
    params: dict = {"c": camera_id}
    scope = site_scope_clause(allowed, "c.site_id", params)
    camera = (await db.execute(text(f"""
        SELECT c.id, c.name, c.site_id, EXISTS (SELECT 1 FROM drones d WHERE d.camera_id = c.id) AS on_a_drone
          FROM cameras c WHERE c.id = CAST(:c AS uuid) {('AND ' + scope) if scope else ''}
    """), params)).mappings().first()
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    if camera["on_a_drone"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A drone's camera cannot have a privacy zone: a zone is fixed to the picture, and a drone's moves.")
    # One at a time for a camera, so that two people drawing at once cannot pass the limit together.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:c))"), {"c": "privacy_zones:" + camera_id})
    drawn = (await db.execute(text("SELECT count(*) FROM privacy_zones WHERE camera_id = CAST(:c AS uuid)"),
                              {"c": camera_id})).scalar()
    if drawn >= privacy_mask.MOST_ZONES:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A camera has at most {privacy_mask.MOST_ZONES} privacy zones. Delete one first.")
    row = (await db.execute(
        text(
            """
            INSERT INTO privacy_zones
                (tenant_id, camera_id, name, polygon, fill_color, is_active, created_by_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:camera_id AS uuid), :name, CAST(:polygon AS jsonb), :fill_color, :is_active,
                CAST(:created_by AS uuid)
            )
            RETURNING id, camera_id, name, polygon, fill_color, is_active, created_at
            """
        ),
        {
            "camera_id": camera_id,
            "name": body.name,
            "polygon": json.dumps(body.polygon),
            "fill_color": body.fill_color,
            "is_active": body.is_active,
            "created_by": token.user_id,
        },
    )).first()
    await intel_audit.record(db, request, token, "privacy.zone.create", "privacy_zone", row.id, site_id=camera["site_id"],
                             detail={"camera_id": camera_id, "camera_name": camera["name"], "name": body.name,
                                     "points": len(body.polygon), "is_active": body.is_active})
    # Its HLS sessions are stopped at once in this process; another process
    # refuses within ten seconds (routers/streams.py). Read before the commit:
    # the tenant is the transaction's, and nothing is read after it.
    streams = [str(r[0]) for r in (await db.execute(
        text("SELECT id FROM streams WHERE camera_id = CAST(:c AS uuid)"), {"c": camera_id})).all()]
    out = {**dict(row._mapping), "camera_name": camera["name"], "applies_within_seconds": privacy_mask.REFRESH_SECONDS}
    await db.commit()
    privacy_mask.forget(camera_id)
    if body.is_active:
        from app.services.hls_stream import stop_stream
        for stream_id in streams:
            await stop_stream(stream_id)
    return out


@router.delete("/zones/{zone_id}", dependencies=[Depends(require_permission("privacy:manage"))])
async def delete_privacy_zone(
    zone_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Delete a zone. From about ten seconds later the camera is shown,
    analysed and recorded without it. What was recorded with it stays masked."""
    _a_person(token)
    params: dict = {"id": str(zone_id)}
    scope = site_scope_clause(allowed, "c.site_id", params)
    zone = (await db.execute(text(f"""
        SELECT pz.id, pz.camera_id, pz.name, c.name AS camera_name, c.site_id
          FROM privacy_zones pz JOIN cameras c ON c.id = pz.camera_id
         WHERE pz.id = CAST(:id AS uuid) {('AND ' + scope) if scope else ''}
    """), params)).mappings().first()
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Privacy zone not found")
    await db.execute(text("DELETE FROM privacy_zones WHERE id = CAST(:id AS uuid)"), {"id": str(zone_id)})
    await intel_audit.record(db, request, token, "privacy.zone.delete", "privacy_zone", zone_id, site_id=zone["site_id"],
                             detail={"camera_id": str(zone["camera_id"]), "camera_name": zone["camera_name"],
                                     "name": zone["name"]})
    camera_id = str(zone["camera_id"])
    await db.commit()
    privacy_mask.forget(camera_id)
    return {"deleted": True, "id": str(zone_id)}


@router.get("/zones/camera/{camera_id}", dependencies=[Depends(require_permission("camera:read"))])
async def get_camera_privacy_zones(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant)):
    """The active masking zones of one camera, for whoever may read the camera.

    Until 2026-10-09 this was open to anybody who knew a camera's id, so that
    an AI worker could read masks without a credential. Where a camera's masks
    are drawn says what the organisation chose not to look at, and is the
    organisation's own. It is now read with a credential like everything else
    of a camera: a signed-in person who may read cameras, or the organisation's
    API key - which is what a worker outside the platform presents. It is read
    under row level security, so another organisation's camera has no masks
    here: the answer is an empty list, as it is for a camera that does not
    exist.
    """
    result = await db.execute(
        text("SELECT pz.id, pz.polygon, pz.fill_color FROM privacy_zones pz "
             "WHERE pz.camera_id = CAST(:cid AS uuid) AND pz.is_active = TRUE"),
        {"cid": str(camera_id)},
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


class ErasureExecuteRequest(BaseModel):
    face_watchlist_entry_ids: list[str] = []
    plate_watchlist_entry_ids: list[str] = []
    visitor_ids: list[str] = []
    evidence_ids: list[str] = []


@pdpa_router.post("/dsar/{dsar_id}/execute-erasure", dependencies=[Depends(require_permission("pdpa:admin"))])
async def execute_dsar_erasure(
    dsar_id: str,
    body: ErasureExecuteRequest,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Actually deletes/redacts the personal data an erasure DSAR names,
    replacing the previous workflow of an admin typing a records_erased
    count by hand with no code path that erased anything. Historical
    detection rows are redacted in place (biometric embedding cleared,
    plate number/watchlist link removed) rather than deleted outright —
    mirrors this codebase's audit_logs immutability convention: the fact
    a detection happened stays, the data identifying who it was doesn't."""
    dsar_row = (await db.execute(
        text("SELECT id, request_type FROM data_subject_requests WHERE id = CAST(:id AS uuid)"),
        {"id": dsar_id},
    )).first()
    if dsar_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DSAR not found")
    if dsar_row.request_type != "erasure":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This DSAR is not an erasure request")

    counts = {"face_watchlist_entries": 0, "plate_watchlist_entries": 0, "visitors": 0, "evidence": 0}

    for entry_id in body.face_watchlist_entry_ids:
        await db.execute(
            text(
                "UPDATE face_events SET matched_watchlist_id = NULL, "
                "embedding_v = NULL, watchlist_match = NULL "
                "WHERE matched_watchlist_id = CAST(:id AS uuid)"
            ),
            {"id": entry_id},
        )
        result = await db.execute(
            text("DELETE FROM face_watchlist_entries WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": entry_id},
        )
        if result.first() is not None:
            counts["face_watchlist_entries"] += 1

    for entry_id in body.plate_watchlist_entry_ids:
        plate_row = (await db.execute(
            text("SELECT plate_number FROM watchlist_entries WHERE id = CAST(:id AS uuid)"),
            {"id": entry_id},
        )).first()
        if plate_row is None:
            continue
        await db.execute(
            text("UPDATE lpr_events SET plate_number = '[ERASED]' WHERE plate_number = :plate"),
            {"plate": plate_row.plate_number},
        )
        result = await db.execute(
            text("DELETE FROM watchlist_entries WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": entry_id},
        )
        if result.first() is not None:
            counts["plate_watchlist_entries"] += 1

    for visitor_id in body.visitor_ids:
        # visitor_logs.visitor_id is ON DELETE SET NULL and never stores the
        # visitor's name/id_number directly, so deleting the visitor row alone
        # is sufficient — the log entries survive as anonymous attendance records.
        result = await db.execute(
            text("DELETE FROM visitors WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": visitor_id},
        )
        if result.first() is not None:
            counts["visitors"] += 1

    for evidence_id in body.evidence_ids:
        ev_row = (await db.execute(
            text("SELECT storage_path FROM evidence WHERE id = CAST(:id AS uuid)"),
            {"id": evidence_id},
        )).first()
        if ev_row is None:
            continue
        # File deleted before the row — an orphaned row (404s if ever served) is
        # safer than an orphaned file nothing references, same ordering the
        # scheduler's retention purge already uses.
        if settings.STORAGE_BACKEND == "s3":
            try:
                from app.core.object_store import delete_object as _s3_delete
                await asyncio.to_thread(_s3_delete, ev_row.storage_path)
            except Exception:
                logger.warning("S3 delete failed for evidence %s during DSAR erasure", evidence_id)
        else:
            file_path = Path(settings.EVIDENCE_ROOT) / ev_row.storage_path
            if file_path.exists():
                file_path.unlink()
        result = await db.execute(
            text("DELETE FROM evidence WHERE id = CAST(:id AS uuid) RETURNING id"),
            {"id": evidence_id},
        )
        if result.first() is not None:
            counts["evidence"] += 1

    total = sum(counts.values())

    # Proof-of-erasure audit trail — records that an erasure happened and its
    # shape, never the erased PII itself (that would defeat the point).
    await db.execute(
        text(
            "INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, detail) "
            "VALUES (current_setting('app.current_tenant')::uuid, CAST(:uid AS uuid), "
            "'dsar_erasure_executed', 'data_subject_requests', CAST(:dsar_id AS uuid), CAST(:detail AS jsonb))"
        ),
        {"uid": token.user_id, "dsar_id": dsar_id, "detail": json.dumps(counts)},
    )

    updated = (await db.execute(
        text(
            "UPDATE data_subject_requests SET records_erased = COALESCE(records_erased, 0) + :total, "
            "updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING id, records_erased"
        ),
        {"id": dsar_id, "total": total},
    )).first()

    await db.commit()
    return {
        "dsar_id": dsar_id,
        "erased_counts": counts,
        "total_erased_this_call": total,
        "records_erased": updated.records_erased,
    }
