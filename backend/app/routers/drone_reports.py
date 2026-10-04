"""Drone patrol reports: a flight's PDF and workbook, a period's summary, who is
emailed which, and what became of each email.

READING AND EXPORTING ARE TWO PERMISSIONS, as in the rest of the platform.
`drone:report:read` opens a report and downloads its PDF. `drone:report:export`
takes the data out as a spreadsheet and decides who is emailed — sending reports
to an address outside the organisation is exporting them.

RENDERED ON REQUEST, from the flight's own record. The copy stored when the
flight ended is the proof that a report existed then (its checksum is listed
here); the download is always built fresh, so an officer's later action or a
late upload from the site is in it.

NOT LICENCE-GATED TO READ OR TO STOP. History stays readable after a licence
lapses, and a recipient can always be paused or removed. Only adding one needs
the module.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import paginate
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.drone_module import require_drone_module
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed
from app.dependencies.tenant import get_db_with_tenant
from app.services import drone_report_delivery as delivery
from app.services import drone_reports as reports
from app.services.drone_access import assert_site_visible, audit, scope_sql, site_or_404, unique_violation

router = APIRouter(prefix="/api/v1", tags=["drone-reports"])

_READ = Depends(require_permission("drone:report:read"))
_EXPORT = Depends(require_permission("drone:report:export"))
_LICENSED = Depends(require_drone_module)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
Frequency = Literal["IMMEDIATE", "DAILY", "WEEKLY", "MONTHLY"]
#: The longest period one summary may cover.
MAX_SUMMARY_DAYS = 92
MAX_RECIPIENTS = 200


def _download(payload: bytes, media_type: str, filename: str) -> StreamingResponse:
    return StreamingResponse(io.BytesIO(payload), media_type=media_type,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def _session_or_404(db: AsyncSession, session_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(
        "SELECT id, site_id, session_number FROM drone_patrol_sessions WHERE id = CAST(:id AS uuid)"),
        {"id": str(session_id)})).mappings().first()
    if row is None:
        raise HTTPException(404, "Patrol session not found")
    assert_site_visible(allowed, row["site_id"], "Patrol session")
    return dict(row)


# ═════════════════════════════════════════════════════════════════════════════
# One flight
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/drone-patrols/{session_id}/report", dependencies=[_READ])
async def session_report(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """The report as data — exactly what the PDF and the workbook are rendered
    from — and the copies stored when the flight ended, with their checksums."""
    await _session_or_404(db, session_id, allowed)
    data = await reports.load_report(db, session_id)
    stored = (await db.execute(text("""
        SELECT report_format, file_bytes, checksum_sha256, generated_at
          FROM drone_reports WHERE session_id = CAST(:id AS uuid) ORDER BY report_format
    """), {"id": str(session_id)})).mappings().all()
    # Storage paths are the system's business; a checksum is the reader's.
    for e in data["events"]:
        e["snapshot"] = {k: v for k, v in e["snapshot"].items() if k != "path"}
    return {**data, "stored": [dict(r) for r in stored]}


@router.get("/drone-patrols/{session_id}/report/pdf", dependencies=[_READ])
async def session_report_pdf(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                             allowed: list[str] | None = Depends(get_allowed_site_ids)):
    session = await _session_or_404(db, session_id, allowed)
    pdf = await reports.build_pdf(db, session_id)
    return _download(pdf, "application/pdf", f"drone-patrol-{session['session_number']}.pdf")


@router.get("/drone-patrols/{session_id}/report/excel", dependencies=[_EXPORT])
async def session_report_excel(session_id: uuid.UUID, db: AsyncSession = Depends(get_db_with_tenant),
                               allowed: list[str] | None = Depends(get_allowed_site_ids)):
    session = await _session_or_404(db, session_id, allowed)
    xlsx = await reports.build_xlsx(db, session_id)
    return _download(xlsx, XLSX, f"drone-patrol-{session['session_number']}.xlsx")


# ═════════════════════════════════════════════════════════════════════════════
# A period
# ═════════════════════════════════════════════════════════════════════════════

async def _period(db: AsyncSession, allowed, start: date, end: date, site_id: uuid.UUID | None,
                  mission_id: uuid.UUID | None) -> tuple[dict, str]:
    if end < start:
        raise HTTPException(422, "The period ends before it starts.")
    if (end - start).days + 1 > MAX_SUMMARY_DAYS:
        raise HTTPException(422, f"A summary covers at most {MAX_SUMMARY_DAYS} days.")
    label = "all sites" if allowed is None else "your sites"
    if mission_id is not None:
        mission = (await db.execute(text(
            "SELECT name, site_id FROM drone_missions WHERE id = CAST(:id AS uuid)"),
            {"id": str(mission_id)})).mappings().first()
        if mission is None:
            raise HTTPException(404, "Mission not found")
        assert_site_visible(allowed, mission["site_id"], "Mission")
        label = f"mission {mission['name']}"
    elif site_id is not None:
        label = (await site_or_404(db, site_id, allowed))["name"]
    data = await reports.load_period(db, start=start, end=end, site_id=site_id, mission_id=mission_id,
                                     allowed_site_ids=allowed)
    return data, label


@router.get("/drone-reports/summary", dependencies=[_READ])
async def period_summary(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
    site_id: uuid.UUID | None = Query(None), mission_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Flights and events in a period — whole days, in the organisation's zone —
    for the organisation, a site or a mission. The same numbers the daily,
    weekly and monthly emails carry."""
    data, label = await _period(db, allowed, start, end, site_id, mission_id)
    return {"scope": label, "from": data["start"], "to": data["end"], "timezone": data["timezone"],
            "totals": data["totals"], "flights": data["flights"]}


@router.get("/drone-reports/summary/excel", dependencies=[_EXPORT])
async def period_summary_excel(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
    site_id: uuid.UUID | None = Query(None), mission_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    data, label = await _period(db, allowed, start, end, site_id, mission_id)
    xlsx = reports.render_period_xlsx(data, scope_label=label)
    return _download(xlsx, XLSX, f"drone-patrol-summary-{start:%Y%m%d}-{end:%Y%m%d}.xlsx")


# ═════════════════════════════════════════════════════════════════════════════
# Recipients
# ═════════════════════════════════════════════════════════════════════════════

_RECIPIENT_SELECT = """
    SELECT r.id, r.site_id, r.mission_id, r.email, r.frequency, r.is_active, r.created_at, r.updated_at,
           s.name AS site_name, m.name AS mission_name, COALESCE(r.site_id, m.site_id) AS scope_site_id,
           u.full_name AS created_by_name
      FROM drone_report_recipients r
      LEFT JOIN sites s          ON s.id = r.site_id
      LEFT JOIN drone_missions m ON m.id = r.mission_id
      LEFT JOIN users u          ON u.id = r.created_by_user_id
"""


def _shape(r: dict) -> dict:
    scope = "mission" if r["mission_id"] else "site" if r["site_id"] else "organisation"
    return {**{k: v for k, v in r.items() if k != "scope_site_id"}, "scope": scope}


async def _recipient_or_404(db: AsyncSession, recipient_id: uuid.UUID, allowed) -> dict:
    row = (await db.execute(text(_RECIPIENT_SELECT + " WHERE r.id = CAST(:id AS uuid)"),
                            {"id": str(recipient_id)})).mappings().first()
    # An organisation-wide recipient has no site, so someone restricted to
    # certain sites can neither see nor change it.
    if row is None or not is_site_allowed(allowed, row["scope_site_id"]):
        raise HTTPException(404, "Report recipient not found")
    return dict(row)


class RecipientCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    frequency: Frequency = "IMMEDIATE"
    site_id: uuid.UUID | None = None
    mission_id: uuid.UUID | None = None


class RecipientUpdate(BaseModel):
    frequency: Frequency | None = None
    is_active: bool | None = None


@router.get("/drone-report-recipients", dependencies=[_READ])
async def list_recipients(db: AsyncSession = Depends(get_db_with_tenant),
                          allowed: list[str] | None = Depends(get_allowed_site_ids)):
    params: dict = {}
    scope = scope_sql(allowed, "COALESCE(r.site_id, m.site_id)", params)
    rows = (await db.execute(text(
        _RECIPIENT_SELECT + f" WHERE TRUE {scope} "
        " ORDER BY s.name NULLS FIRST, m.name NULLS FIRST, r.email, r.frequency"), params)).mappings().all()
    return [_shape(dict(r)) for r in rows]


@router.post("/drone-report-recipients", status_code=201, dependencies=[_EXPORT, _LICENSED])
async def create_recipient(body: RecipientCreate, request: Request,
                           db: AsyncSession = Depends(get_db_with_tenant),
                           token: TokenPayload = Depends(get_token_payload),
                           allowed: list[str] | None = Depends(get_allowed_site_ids)):
    email = delivery.normalise_email(body.email)
    if email is None:
        raise HTTPException(422, "That is not an email address.")
    if body.site_id is not None and body.mission_id is not None:
        raise HTTPException(422, "Choose a site or a mission, not both: a mission already belongs to a site.")
    if body.mission_id is not None:
        mission = (await db.execute(text("SELECT site_id FROM drone_missions WHERE id = CAST(:id AS uuid)"),
                                    {"id": str(body.mission_id)})).mappings().first()
        if mission is None or not is_site_allowed(allowed, mission["site_id"]):
            raise HTTPException(404, "Mission not found")
    elif body.site_id is not None:
        await site_or_404(db, body.site_id, allowed)
    elif allowed is not None:
        # Every site's reports, for someone who may see only some sites.
        raise HTTPException(422, "Choose one of your sites or missions for this recipient.")
    count = (await db.execute(text("SELECT count(*) FROM drone_report_recipients"))).scalar()
    if count >= MAX_RECIPIENTS:
        raise HTTPException(409, f"This organisation already has {MAX_RECIPIENTS} report recipients.")
    try:
        rid = (await db.execute(text("""
            INSERT INTO drone_report_recipients (tenant_id, site_id, mission_id, email, frequency, created_by_user_id)
            VALUES (current_setting('app.current_tenant')::uuid, CAST(:site AS uuid), CAST(:mission AS uuid),
                    :email, :f, CAST(:by AS uuid))
            RETURNING id
        """), {"site": str(body.site_id) if body.site_id else None,
               "mission": str(body.mission_id) if body.mission_id else None,
               "email": email, "f": body.frequency, "by": token.user_id})).scalar()
    except IntegrityError as exc:
        if unique_violation(exc, "uq_drr_recipient"):
            raise HTTPException(409, "That address already receives this report at this frequency.") from exc
        raise
    await audit(db, request, token, "drone.report_recipient.create", "drone_report_recipient", rid,
                {"email": email, "frequency": body.frequency,
                 "scope": delivery.scope_key(body.site_id, body.mission_id)})
    result = _shape(await _recipient_or_404(db, rid, allowed))
    await db.commit()
    return result


@router.put("/drone-report-recipients/{recipient_id}", dependencies=[_EXPORT])
async def update_recipient(recipient_id: uuid.UUID, body: RecipientUpdate, request: Request,
                           db: AsyncSession = Depends(get_db_with_tenant),
                           token: TokenPayload = Depends(get_token_payload),
                           allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Change how often, or pause and resume. The address and the scope are what
    a recipient *is*; to change those, remove it and add another."""
    await _recipient_or_404(db, recipient_id, allowed)
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        raise HTTPException(422, "No fields to update")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    try:
        await db.execute(text(f"UPDATE drone_report_recipients SET {sets}, updated_at = now() "
                              " WHERE id = CAST(:id AS uuid)"), {**changes, "id": str(recipient_id)})
    except IntegrityError as exc:
        if unique_violation(exc, "uq_drr_recipient"):
            raise HTTPException(409, "That address already receives this report at this frequency.") from exc
        raise
    await audit(db, request, token, "drone.report_recipient.update", "drone_report_recipient", recipient_id,
                {"changed": sorted(changes)})
    result = _shape(await _recipient_or_404(db, recipient_id, allowed))
    await db.commit()
    return result


@router.delete("/drone-report-recipients/{recipient_id}", status_code=204, dependencies=[_EXPORT])
async def delete_recipient(recipient_id: uuid.UUID, request: Request,
                           db: AsyncSession = Depends(get_db_with_tenant),
                           token: TokenPayload = Depends(get_token_payload),
                           allowed: list[str] | None = Depends(get_allowed_site_ids)):
    row = await _recipient_or_404(db, recipient_id, allowed)
    await db.execute(text("DELETE FROM drone_report_recipients WHERE id = CAST(:id AS uuid)"),
                     {"id": str(recipient_id)})
    await audit(db, request, token, "drone.report_recipient.delete", "drone_report_recipient", recipient_id,
                {"email": row["email"], "frequency": row["frequency"]})
    await db.commit()
    return Response(status_code=204)


# ═════════════════════════════════════════════════════════════════════════════
# Deliveries: what became of each email
# ═════════════════════════════════════════════════════════════════════════════

_DELIVERY_SELECT = """
    SELECT q.id, q.session_id, q.site_id, q.mission_id, q.scope_label, q.frequency, q.period_start,
           q.period_end, q.recipients, q.subject, q.status, q.attempts, q.last_error, q.scheduled_at,
           q.sent_at, q.created_at, ps.session_number, s.name AS site_name
      FROM drone_report_email_queue q
      LEFT JOIN drone_patrol_sessions ps ON ps.id = q.session_id
      LEFT JOIN sites s                  ON s.id = q.site_id
"""


@router.get("/drone-report-deliveries", dependencies=[_READ])
async def list_deliveries(
    status_filter: Literal["PENDING", "PROCESSING", "SENT", "FAILED"] | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Every report email queued, newest first: sent, waiting, or failed with
    the mail server's reason. `attempts_left` says whether it will be tried
    again by itself."""
    params: dict = {"status": status_filter}
    scope = scope_sql(allowed, "q.site_id", params)
    where = f" WHERE (CAST(:status AS text) IS NULL OR q.status = CAST(:status AS text)) {scope}"
    page = await paginate(
        db, _DELIVERY_SELECT + where + " ORDER BY q.created_at DESC LIMIT :limit OFFSET :offset",
        "SELECT count(*) FROM drone_report_email_queue q" + where, params, limit, offset)
    for item in page["items"]:
        item["recipients"] = [e for e in (item["recipients"] or "").split(",") if e]
        item["attempts_left"] = max(0, delivery.MAX_ATTEMPTS - item["attempts"])
    return page


@router.post("/drone-report-deliveries/{delivery_id}/retry", dependencies=[_EXPORT])
async def retry_delivery(delivery_id: uuid.UUID, request: Request,
                         db: AsyncSession = Depends(get_db_with_tenant),
                         token: TokenPayload = Depends(get_token_payload),
                         allowed: list[str] | None = Depends(get_allowed_site_ids)):
    """Put a failed email back in the queue with its attempts reset — for after
    the mail server has been fixed. Only a failed one: an email already sent or
    being sent is not sent again from here."""
    row = (await db.execute(text(_DELIVERY_SELECT + " WHERE q.id = CAST(:id AS uuid)"),
                            {"id": str(delivery_id)})).mappings().first()
    if row is None or (allowed is not None and not is_site_allowed(allowed, row["site_id"])):
        raise HTTPException(404, "Report delivery not found")
    done = (await db.execute(text("""
        UPDATE drone_report_email_queue
           SET status = 'PENDING', attempts = 0, scheduled_at = :now, last_error = NULL
         WHERE id = CAST(:id AS uuid) AND status = 'FAILED'
        RETURNING id
    """), {"id": str(delivery_id), "now": datetime.now(timezone.utc)})).first()
    if done is None:
        raise HTTPException(409, f"Only a failed email can be retried; this one is {row['status'].lower()}.")
    await audit(db, request, token, "drone.report_delivery.retry", "drone_report_delivery", delivery_id, None)
    await db.commit()
    return {"id": str(delivery_id), "status": "PENDING"}
