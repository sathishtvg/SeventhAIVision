"""Patrol routes, checkpoints, sessions, and guard SOS panic button."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

from app.services.sos import raise_guard_sos

router = APIRouter(prefix="/api/v1/patrols", tags=["guard-ops"])


# ── Patrol Routes ─────────────────────────────────────────────────────────────

class RouteCreate(BaseModel):
    site_id: str
    name: str
    description: str | None = None


class CheckpointCreate(BaseModel):
    sequence: int
    name: str
    latitude: float | None = None
    longitude: float | None = None
    nfc_tag_id: str | None = None
    qr_code: str | None = None


@router.get("/routes", dependencies=[Depends(require_permission("patrol:read"))])
async def list_routes(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
):
    params: dict = {}
    where = ""
    if site_id:
        where = "WHERE pr.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    result = await db.execute(
        text(
            f"""
            SELECT pr.id, pr.site_id, pr.name, pr.description, pr.is_active,
                   s.name AS site_name,
                   COUNT(pc.id) AS checkpoint_count
            FROM patrol_routes pr
            LEFT JOIN sites s ON s.id = pr.site_id
            LEFT JOIN patrol_checkpoints pc ON pc.route_id = pr.id
            {where}
            GROUP BY pr.id, s.name
            ORDER BY pr.name
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@router.post("/routes", dependencies=[Depends(require_permission("patrol:manage"))])
async def create_route(body: RouteCreate, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text(
            """
            INSERT INTO patrol_routes (tenant_id, site_id, name, description)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site_id AS uuid), :name, :description)
            RETURNING id, site_id, name, description
            """
        ),
        {"site_id": body.site_id, "name": body.name, "description": body.description},
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/routes/{route_id}", dependencies=[Depends(require_permission("patrol:read"))])
async def get_route(route_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    route_row = (
        await db.execute(
            text("SELECT * FROM patrol_routes WHERE id = :id"),
            {"id": route_id},
        )
    ).first()
    if route_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Route not found")

    checkpoints = (
        await db.execute(
            text(
                "SELECT * FROM patrol_checkpoints WHERE route_id = :rid ORDER BY sequence"
            ),
            {"rid": route_id},
        )
    ).fetchall()

    return {
        **dict(route_row._mapping),
        "checkpoints": [dict(c._mapping) for c in checkpoints],
    }


@router.post("/routes/{route_id}/checkpoints", dependencies=[Depends(require_permission("patrol:manage"))])
async def add_checkpoint(
    route_id: str,
    body: CheckpointCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    result = await db.execute(
        text(
            """
            INSERT INTO patrol_checkpoints
                (tenant_id, route_id, sequence, name, latitude, longitude, nfc_tag_id, qr_code)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:route_id AS uuid), :sequence, :name, :latitude, :longitude, :nfc_tag_id, :qr_code
            )
            RETURNING id, route_id, sequence, name
            """
        ),
        {
            "route_id": route_id,
            "sequence": body.sequence,
            "name": body.name,
            "latitude": body.latitude,
            "longitude": body.longitude,
            "nfc_tag_id": body.nfc_tag_id,
            "qr_code": body.qr_code,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


# ── Patrol Sessions ───────────────────────────────────────────────────────────

@router.post("/routes/{route_id}/sessions", dependencies=[Depends(require_permission("patrol:manage"))])
async def start_session(
    route_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    shift_id: str | None = None,
):
    result = await db.execute(
        text(
            """
            INSERT INTO patrol_sessions (tenant_id, route_id, guard_user_id, shift_id, status, started_at)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:route_id AS uuid), CAST(:guard_id AS uuid),
                CAST(:shift_id AS uuid), 'in_progress', now()
            )
            RETURNING id, route_id, guard_user_id, status, started_at
            """
        ),
        {
            "route_id": route_id,
            "guard_id": token.user_id,
            "shift_id": shift_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.post("/sessions/{session_id}/complete", dependencies=[Depends(require_permission("patrol:manage"))])
async def complete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    result = await db.execute(
        text(
            """
            UPDATE patrol_sessions
            SET status = 'completed', completed_at = now(),
                scanned_checkpoints = (
                    SELECT COUNT(*) FROM checkpoint_scans WHERE session_id = :sid
                )
            WHERE id = :sid AND status = 'in_progress'
            RETURNING id, status, completed_at, scanned_checkpoints
            """
        ),
        {"sid": session_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or not in progress")
    return dict(row._mapping)


class CheckpointScanBody(BaseModel):
    checkpoint_id: str
    scan_method: str = "manual"  # manual | nfc | qr
    scanned_code: str | None = None  # the raw QR string or NFC tag ID read by the device
    latitude: float | None = None
    longitude: float | None = None
    # Offline sync (Gap 88): the ORIGINAL scan time, replayed later by the
    # mobile outbox. None = scanned online right now.
    scanned_at: str | None = None


@router.post("/sessions/{session_id}/scan", dependencies=[Depends(require_permission("patrol:scan"))])
async def scan_checkpoint(
    session_id: str,
    body: CheckpointScanBody,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    session_row = (
        await db.execute(
            text("SELECT id, route_id FROM patrol_sessions WHERE id = :id AND status = 'in_progress'"),
            {"id": session_id},
        )
    ).first()
    if session_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found or not active")

    # Verify QR code or NFC tag matches the checkpoint's stored value
    verified = False
    if body.scan_method in ("qr", "nfc") and body.scanned_code:
        cp_row = (await db.execute(
            text("SELECT qr_code, nfc_tag_id FROM patrol_checkpoints WHERE id = :id"),
            {"id": body.checkpoint_id},
        )).first()
        if cp_row:
            expected = cp_row.qr_code if body.scan_method == "qr" else cp_row.nfc_tag_id
            verified = expected is not None and expected == body.scanned_code
    elif body.scan_method == "manual":
        verified = True  # manual override — supervisor trusts it

    from app.core.client_time import parse_client_timestamp

    client_scanned_at = parse_client_timestamp(body.scanned_at, "scanned_at")

    result = await db.execute(
        text(
            """
            INSERT INTO checkpoint_scans
                (tenant_id, session_id, checkpoint_id, scan_method, latitude, longitude,
                 scanned_at, guard_user_id, verified)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                :session_id, :checkpoint_id,
                :scan_method, :latitude, :longitude, COALESCE(:scanned_at, now()),
                :guard_id, :verified
            )
            RETURNING id, checkpoint_id, scan_method, scanned_at, verified
            """
        ),
        {
            "session_id": session_id,
            "checkpoint_id": body.checkpoint_id,
            "scan_method": body.scan_method,
            "latitude": body.latitude,
            "longitude": body.longitude,
            "scanned_at": client_scanned_at,
            "guard_id": token.user_id,
            "verified": verified,
        },
    )
    row = result.first()
    await db.commit()
    return {**dict(row._mapping), "verified": verified}


@router.get("/sessions/{session_id}", dependencies=[Depends(require_permission("patrol:read"))])
async def get_session(session_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    session_row = (
        await db.execute(
            text(
                """
                SELECT ps.*, u.full_name AS guard_name, pr.name AS route_name
                FROM patrol_sessions ps
                LEFT JOIN users u ON u.id = ps.guard_user_id
                LEFT JOIN patrol_routes pr ON pr.id = ps.route_id
                WHERE ps.id = :id
                """
            ),
            {"id": session_id},
        )
    ).first()
    if session_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    scans = (
        await db.execute(
            text(
                """
                SELECT cs.*, pc.name AS checkpoint_name, pc.sequence
                FROM checkpoint_scans cs
                LEFT JOIN patrol_checkpoints pc ON pc.id = cs.checkpoint_id
                WHERE cs.session_id = :id ORDER BY cs.scanned_at
                """
            ),
            {"id": session_id},
        )
    ).fetchall()

    return {
        **dict(session_row._mapping),
        "scans": [dict(s._mapping) for s in scans],
    }


# ── Guard SOS Panic Button ────────────────────────────────────────────────────

class SOSBody(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    message: str | None = None


@router.post("/sos", dependencies=[Depends(require_permission("guard:sos"))])
async def guard_sos(
    body: SOSBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    """Guard panic/SOS button.

    Same work as POST /shifts/sos — they share services/sos.py so the two
    cannot drift apart again. This one used to raise an incident and nothing
    else: no occurrence book entry, and no live push, so a guard could press
    panic and nobody watching a screen would be told.
    """
    result = await raise_guard_sos(
        db,
        getattr(request.app.state, "redis", None),
        tenant_id=token.tenant_id,
        user_id=token.user_id,
        latitude=body.latitude,
        longitude=body.longitude,
        description=body.message,
    )

    # Response shape unchanged for the web client, plus entry_id — which is now
    # the id that always exists. incident_id can still be None (no camera in
    # the tenant), but that no longer means nothing was recorded.
    return {
        "sos_acknowledged": True,
        "incident_id": result["incident_id"],
        "entry_id": result["entry_id"],
        "guard_name": result["guard_name"],
    }
