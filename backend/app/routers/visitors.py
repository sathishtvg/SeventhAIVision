"""Visitor management — pre-registration, arrival/departure logging, QR check-in."""

import asyncio
import base64
import io
import logging
import secrets
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/visitors", tags=["visitors"])


def _plate_evidence_lateral(direction: str) -> str:
    """Plate-read proof for a visitor's vehicle entry (or exit).

    WHY
        An LPR read auto-creates the visitor row and starts the parking clock,
        then an operator is asked to confirm the vehicle's identity against a
        bare text string. A misread character means the wrong vehicle is billed
        or a blocklisted plate is waved through as a typo. The crop is the only
        image that settles it — at frame resolution the plate is a smudge.

    The time bound is what keeps this cheap: `evidence` is RANGE-partitioned on
    captured_at, so without it Postgres probes every monthly partition. Anchored
    on the vehicle's own timestamp with an hour of slack either side, and
    falling back to created_at so a row whose entry time was never stamped
    degrades to "still finds it" rather than "silently returns nothing".
    """
    anchor = f"COALESCE(v.vehicle_{direction}_at, v.created_at)"
    return f"""
        LEFT JOIN LATERAL (
            SELECT
              (array_agg(e.id) FILTER (WHERE e.capture_kind = 'plate_crop'))[1]
                  AS {direction}_plate_evidence_id,
              (array_agg(e.id) FILTER (WHERE e.capture_kind = 'frame'))[1]
                  AS {direction}_frame_evidence_id
            FROM evidence e
            WHERE e.detection_id = v.{direction}_lpr_detection_id
              AND e.captured_at BETWEEN {anchor} - INTERVAL '1 hour'
                                    AND {anchor} + INTERVAL '1 hour'
        ) ev_{direction} ON TRUE
    """


_PLATE_EVIDENCE_JOINS = _plate_evidence_lateral("entry") + _plate_evidence_lateral("exit")
_PLATE_EVIDENCE_COLS = (
    "v.entry_lpr_detection_id, v.exit_lpr_detection_id, "
    "v.vehicle_entry_at, v.vehicle_exit_at, "
    "ev_entry.entry_plate_evidence_id, ev_entry.entry_frame_evidence_id, "
    "ev_exit.exit_plate_evidence_id, ev_exit.exit_frame_evidence_id"
)


def _to_dt(s: str | None):
    if s is None:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class VisitorCreate(BaseModel):
    full_name: str
    id_number: str | None = None
    company: str | None = None
    host_user_id: str | None = None
    host_name: str | None = None
    purpose: str | None = None
    vehicle_plate: str | None = None
    site_id: str | None = None
    expected_from: str | None = None
    expected_until: str | None = None
    visitor_email: str | None = None


class QRScanCheckin(BaseModel):
    qr_token: str
    badge_number: str | None = None
    notes: str | None = None


class VisitorLogCreate(BaseModel):
    badge_number: str | None = None
    notes: str | None = None


class VisitorCheckin(BaseModel):
    visitor_id: str | None = None
    event_type: str = "arrival"  # arrival | departure | denied
    badge_number: str | None = None
    notes: str | None = None
    # For unregistered walk-ins:
    full_name: str | None = None
    company: str | None = None
    host_name: str | None = None
    site_id: str | None = None


@router.get("", dependencies=[Depends(require_permission("visitor:read"))])
async def list_visitors(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    status: str | None = None,
    is_active: bool = True,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = ["v.is_active = :is_active"]
    params: dict = {"is_active": is_active}
    if site_id:
        where_clauses.append("v.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if status:
        where_clauses.append("v.status = :status")
        params["status"] = status
    where = "WHERE " + " AND ".join(where_clauses)
    data_sql = f"""
        SELECT v.id, v.full_name, v.company, v.host_name, v.purpose,
               v.vehicle_plate, v.expected_from, v.expected_until, v.is_active,
               v.status, v.visitor_email, v.qr_token,
               u.full_name AS host_user_name, s.name AS site_name,
               {_PLATE_EVIDENCE_COLS}
        FROM visitors v
        LEFT JOIN users u ON u.id = v.host_user_id
        LEFT JOIN sites s ON s.id = v.site_id
        {_PLATE_EVIDENCE_JOINS}
        {where}
        ORDER BY v.expected_from DESC NULLS LAST LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM visitors v {where}"
    return await paginate(db, data_sql, count_sql, params, limit, offset)


@router.post("", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission("visitor:manage"))])
async def create_visitor(
    body: VisitorCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    qr_token = str(_uuid.uuid4())
    result = await db.execute(
        text(
            """
            INSERT INTO visitors (tenant_id, full_name, id_number, company,
                host_user_id, host_name, purpose, vehicle_plate, site_id,
                expected_from, expected_until, visitor_email,
                qr_token, created_by_user_id)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                :full_name, :id_number, :company,
                CAST(:host_user_id AS uuid), :host_name, :purpose, :vehicle_plate,
                CAST(:site_id AS uuid),
                CAST(:expected_from AS timestamptz), CAST(:expected_until AS timestamptz),
                :visitor_email, :qr_token,
                CAST(:created_by AS uuid)
            )
            RETURNING id, full_name, company, expected_from, expected_until, qr_token
            """
        ),
        {
            "full_name": body.full_name,
            "id_number": body.id_number,
            "company": body.company,
            "host_user_id": body.host_user_id,
            "host_name": body.host_name,
            "purpose": body.purpose,
            "vehicle_plate": body.vehicle_plate,
            "site_id": body.site_id,
            "expected_from": _to_dt(body.expected_from),
            "expected_until": _to_dt(body.expected_until),
            "visitor_email": body.visitor_email,
            "qr_token": qr_token,
            "created_by": token.user_id,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.post("/checkin", dependencies=[Depends(require_permission("visitor:checkin"))])
async def checkin_visitor(
    body: VisitorCheckin,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    is_unregistered = body.visitor_id is None

    if is_unregistered and not body.full_name:
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "full_name required for unregistered visitors")

    result = await db.execute(
        text(
            """
            INSERT INTO visitor_logs
                (tenant_id, visitor_id, site_id, guard_user_id, event_type,
                 badge_number, notes, is_unregistered)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:visitor_id AS uuid), CAST(:site_id AS uuid), CAST(:guard_id AS uuid),
                :event_type, :badge_number, :notes, :is_unregistered
            )
            RETURNING id, visitor_id, event_type, occurred_at, badge_number
            """
        ),
        {
            "visitor_id": body.visitor_id,
            "site_id": body.site_id,
            "guard_id": token.user_id,
            "event_type": body.event_type,
            "badge_number": body.badge_number,
            "notes": body.notes,
            "is_unregistered": is_unregistered,
        },
    )
    row = result.first()
    await db.commit()
    return dict(row._mapping)


@router.get("/logs", dependencies=[Depends(require_permission("visitor:read"))])
async def list_visitor_logs(
    db: AsyncSession = Depends(get_db_with_tenant),
    site_id: str | None = None,
    visitor_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where_clauses = []
    params: dict = {"limit": min(limit, 200), "offset": max(offset, 0)}
    if site_id:
        where_clauses.append("vl.site_id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    if visitor_id:
        where_clauses.append("vl.visitor_id = CAST(:visitor_id AS uuid)")
        params["visitor_id"] = visitor_id
    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    result = await db.execute(
        text(
            f"""
            SELECT vl.*, v.full_name AS visitor_name, s.name AS site_name,
                   u.full_name AS guard_name
            FROM visitor_logs vl
            LEFT JOIN visitors v ON v.id = vl.visitor_id
            LEFT JOIN sites s ON s.id = vl.site_id
            LEFT JOIN users u ON u.id = vl.guard_user_id
            {where}
            ORDER BY vl.occurred_at DESC LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result]


@router.delete("/{visitor_id}", dependencies=[Depends(require_permission("visitor:manage"))])
async def deactivate_visitor(visitor_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    result = await db.execute(
        text("UPDATE visitors SET is_active = FALSE WHERE id = :id RETURNING id"),
        {"id": visitor_id},
    )
    row = result.first()
    await db.commit()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visitor not found")
    return {"id": row.id, "is_active": False}


# ── QR code image ─────────────────────────────────────────────────────────────

@router.get("/{visitor_id}/qr.png", dependencies=[Depends(require_permission("visitor:read"))])
async def get_visitor_qr(visitor_id: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(
        text("SELECT qr_token, full_name FROM visitors WHERE id = CAST(:id AS uuid)"),
        {"id": visitor_id}
    )).first()
    if row is None:
        raise HTTPException(404, "Visitor not found")

    import qrcode
    qr = qrcode.QRCode(version=2, box_size=8, border=4)
    qr.add_data(row.qr_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


# ── Look up visitor by QR token ───────────────────────────────────────────────

@router.get("/by-qr/{qr_token}", dependencies=[Depends(require_permission("visitor:read"))])
async def lookup_by_qr(qr_token: str, db: AsyncSession = Depends(get_db_with_tenant)):
    row = (await db.execute(text(f"""
        SELECT v.id, v.full_name, v.company, v.host_name, v.purpose,
               v.vehicle_plate, v.expected_from, v.expected_until,
               v.visitor_email, v.status, v.qr_token,
               s.name AS site_name,
               {_PLATE_EVIDENCE_COLS}
        FROM visitors v
        LEFT JOIN sites s ON s.id = v.site_id
        {_PLATE_EVIDENCE_JOINS}
        WHERE v.qr_token = :token AND v.is_active = TRUE
    """), {"token": qr_token})).first()
    if row is None:
        raise HTTPException(404, "QR code not found or visitor inactive")
    return dict(row._mapping)


# ── QR scan check-in ──────────────────────────────────────────────────────────

@router.post("/qr-scan", dependencies=[Depends(require_permission("visitor:checkin"))])
async def qr_scan_checkin(
    body: QRScanCheckin,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    visitor = (await db.execute(text("""
        SELECT id, full_name, status, expected_from, expected_until, site_id
        FROM visitors
        WHERE qr_token = :token AND is_active = TRUE
    """), {"token": body.qr_token})).first()

    if visitor is None:
        raise HTTPException(404, "QR code invalid or visitor not found")
    if visitor.status == "arrived":
        # Already checked in — allow departure scan instead
        log = await db.execute(text("""
            INSERT INTO visitor_logs
                (tenant_id, visitor_id, site_id, guard_user_id, event_type,
                 badge_number, notes, checkin_method, qr_token_used)
            VALUES (
                current_setting('app.current_tenant')::uuid,
                CAST(:vid AS uuid), CAST(:sid AS uuid), CAST(:gid AS uuid),
                'departure', :badge, :notes, 'qr_scan', :qr
            )
            RETURNING id, event_type, occurred_at
        """), {
            "vid": str(visitor.id), "sid": str(visitor.site_id) if visitor.site_id else None,
            "gid": token.user_id, "badge": body.badge_number,
            "notes": body.notes, "qr": body.qr_token,
        })
        await db.execute(
            text("UPDATE visitors SET status = 'departed', departed_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": str(visitor.id)}
        )
        await db.commit()
        return {"event_type": "departure", "visitor": dict(visitor._mapping), **dict(log.first()._mapping)}

    # Normal arrival check-in
    log = await db.execute(text("""
        INSERT INTO visitor_logs
            (tenant_id, visitor_id, site_id, guard_user_id, event_type,
             badge_number, notes, checkin_method, qr_token_used)
        VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:vid AS uuid), CAST(:sid AS uuid), CAST(:gid AS uuid),
            'arrival', :badge, :notes, 'qr_scan', :qr
        )
        RETURNING id, event_type, occurred_at
    """), {
        "vid": str(visitor.id), "sid": str(visitor.site_id) if visitor.site_id else None,
        "gid": token.user_id, "badge": body.badge_number,
        "notes": body.notes, "qr": body.qr_token,
    })
    await db.execute(
        text("UPDATE visitors SET status = 'arrived' WHERE id = CAST(:id AS uuid)"),
        {"id": str(visitor.id)}
    )
    await db.commit()
    return {"event_type": "arrival", "visitor": dict(visitor._mapping), **dict(log.first()._mapping)}


# ── Upcoming visitors ─────────────────────────────────────────────────────────

@router.get("/upcoming", dependencies=[Depends(require_permission("visitor:read"))])
async def upcoming_visitors(
    db: AsyncSession = Depends(get_db_with_tenant),
    hours: int = 24,
    site_id: str | None = None,
):
    params: dict = {"hours": min(hours, 72)}
    where = ["v.is_active = TRUE",
             "v.expected_from BETWEEN now() - INTERVAL '1 hour' AND now() + make_interval(hours => :hours)",
             "v.status NOT IN ('departed', 'cancelled')"]
    if site_id:
        where.append("v.site_id = CAST(:sid AS uuid)"); params["sid"] = site_id
    rows = await db.execute(text(f"""
        SELECT v.id, v.full_name, v.company, v.host_name, v.purpose,
               v.vehicle_plate, v.expected_from, v.expected_until,
               v.visitor_email, v.status, v.qr_token,
               s.name AS site_name, u.full_name AS host_user_name,
               {_PLATE_EVIDENCE_COLS}
        FROM visitors v
        LEFT JOIN sites s ON s.id = v.site_id
        LEFT JOIN users u ON u.id = v.host_user_id
        {_PLATE_EVIDENCE_JOINS}
        WHERE {' AND '.join(where)}
        ORDER BY v.expected_from ASC
    """), params)
    return [dict(r._mapping) for r in rows]


# ── Send QR by email ──────────────────────────────────────────────────────────

@router.post("/{visitor_id}/send-qr", dependencies=[Depends(require_permission("visitor:manage"))])
async def send_qr_email(
    visitor_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    v = (await db.execute(text("""
        SELECT id, full_name, visitor_email, qr_token,
               host_name, expected_from, expected_until
        FROM visitors WHERE id = CAST(:id AS uuid) AND is_active = TRUE
    """), {"id": visitor_id})).first()

    if v is None:
        raise HTTPException(404, "Visitor not found")
    if not v.visitor_email:
        raise HTTPException(422, "Visitor has no email address on file")

    # Generate QR PNG inline
    import qrcode
    qr = qrcode.QRCode(version=2, box_size=8, border=4)
    qr.add_data(v.qr_token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    # Send email asynchronously
    asyncio.create_task(_send_qr_email_async(v, qr_b64))

    # Mark email sent
    await db.execute(
        text("UPDATE visitors SET qr_email_sent_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": visitor_id}
    )
    await db.commit()
    return {"ok": True, "email": v.visitor_email}


@router.post("/{visitor_id}/checkin", dependencies=[Depends(require_permission("visitor:checkin"))])
async def checkin_visitor_by_id(
    visitor_id: str,
    body: VisitorLogCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    v = (await db.execute(text("""
        SELECT id, site_id FROM visitors WHERE id = CAST(:id AS uuid) AND is_active = TRUE
    """), {"id": visitor_id})).first()
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visitor not found")
    log_row = (await db.execute(text("""
        INSERT INTO visitor_logs
            (tenant_id, visitor_id, site_id, guard_user_id, event_type,
             badge_number, notes, checkin_method)
        VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:vid AS uuid), CAST(:sid AS uuid), CAST(:gid AS uuid),
            'arrival', :badge, :notes, 'manual'
        )
        RETURNING id, event_type, occurred_at
    """), {
        "vid": str(v.id), "sid": str(v.site_id) if v.site_id else None,
        "gid": token.user_id, "badge": body.badge_number, "notes": body.notes,
    })).first()
    await db.execute(
        text("UPDATE visitors SET status = 'arrived' WHERE id = CAST(:id AS uuid)"),
        {"id": visitor_id}
    )
    await db.commit()
    return dict(log_row._mapping)


@router.post("/{visitor_id}/checkout", dependencies=[Depends(require_permission("visitor:checkin"))])
async def checkout_visitor_by_id(
    visitor_id: str,
    body: VisitorLogCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    v = (await db.execute(text("""
        SELECT id, site_id FROM visitors WHERE id = CAST(:id AS uuid) AND is_active = TRUE
    """), {"id": visitor_id})).first()
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visitor not found")
    log_row = (await db.execute(text("""
        INSERT INTO visitor_logs
            (tenant_id, visitor_id, site_id, guard_user_id, event_type, notes, checkin_method)
        VALUES (
            current_setting('app.current_tenant')::uuid,
            CAST(:vid AS uuid), CAST(:sid AS uuid), CAST(:gid AS uuid),
            'departure', :notes, 'manual'
        )
        RETURNING id, event_type, occurred_at
    """), {
        "vid": str(v.id), "sid": str(v.site_id) if v.site_id else None,
        "gid": token.user_id, "notes": body.notes,
    })).first()
    await db.execute(
        text("UPDATE visitors SET status = 'departed', departed_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": visitor_id}
    )
    await db.commit()
    return dict(log_row._mapping)


async def _send_qr_email_async(v, qr_b64: str) -> None:
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.image import MIMEImage
        import base64 as _b64

        expected = ""
        if v.expected_from:
            expected = f"\nExpected arrival: {v.expected_from}"

        text_body = (
            f"Dear {v.full_name},\n\n"
            f"Your visit has been pre-registered.\n"
            f"Host: {v.host_name or 'N/A'}{expected}\n\n"
            "Please present the attached QR code at the security desk upon arrival.\n\n"
            "The security guard will scan your QR code to complete check-in.\n\n"
            "Regards,\nSecurity Team"
        )

        msg = MIMEMultipart("related")
        msg["Subject"] = "Your Visitor QR Code — Seventh AI Vision"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = v.visitor_email

        body = MIMEText(text_body, "plain", "utf-8")
        msg.attach(body)

        qr_img = MIMEImage(_b64.b64decode(qr_b64), name="visitor_qr.png")
        qr_img.add_header("Content-ID", "<visitor_qr>")
        msg.attach(qr_img)

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASSWORD or None,
            use_tls=(settings.SMTP_PORT == 465),
            start_tls=(settings.SMTP_PORT == 587),
        )
        logger.info("QR email sent to visitor %s at %s", str(v.id), v.visitor_email)
    except Exception as exc:
        logger.warning("QR email failed for visitor %s: %s", str(v.id), exc)
