"""CSV export endpoints for alerts, incidents, detections, and audit logs.

All endpoints return text/csv with Content-Disposition: attachment so the browser
triggers a file download. Filters are passed as optional query params; each endpoint
caps output at MAX_ROWS to prevent accidental multi-GB exports."""

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/export", tags=["export"])
MAX_ROWS = 10_000


def _make_csv(rows: list[dict], fieldnames: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else str(row[k])) for k in fieldnames})
    return output.getvalue()


def _csv_response(filename: str, content: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _s(v) -> Optional[str]:
    """Return v only when it is an actual string (not a FastAPI Query sentinel)."""
    return v if isinstance(v, str) else None


# ── Alerts ────────────────────────────────────────────────


@router.get("/alerts", dependencies=[Depends(require_permission("alert:read"))])
async def export_alerts(
    db: AsyncSession = Depends(get_db_with_tenant),
    date_from: Optional[str] = Query(None, description="ISO date e.g. 2026-01-01"),
    date_to: Optional[str] = Query(None, description="ISO date e.g. 2026-12-31"),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    module_type: Optional[str] = Query(None),
):
    date_from, date_to, severity, status, module_type = _s(date_from), _s(date_to), _s(severity), _s(status), _s(module_type)
    conds, params = [], {}
    if date_from:
        conds.append("a.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("a.created_at <= CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if severity:
        conds.append("a.severity = :severity")
        params["severity"] = severity
    if status:
        conds.append("a.status = :status")
        params["status"] = status
    if module_type:
        conds.append("a.module_type = :module_type")
        params["module_type"] = module_type

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    result = await db.execute(
        text(
            f"SELECT a.id, c.name AS camera_name, a.module_type, a.severity, "
            f"a.alert_code, a.title, a.message, a.status, a.acknowledged_at, a.created_at "
            f"FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id "
            f"{where} ORDER BY a.created_at DESC LIMIT {MAX_ROWS}"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    fields = [
        "id", "camera_name", "module_type", "severity", "alert_code",
        "title", "message", "status", "acknowledged_at", "created_at",
    ]
    return _csv_response(f"alerts_{_now_tag()}.csv", _make_csv(rows, fields))


# ── Incidents ─────────────────────────────────────────────


@router.get("/incidents", dependencies=[Depends(require_permission("incident:read"))])
async def export_incidents(
    db: AsyncSession = Depends(get_db_with_tenant),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    date_from, date_to, severity, status = _s(date_from), _s(date_to), _s(severity), _s(status)
    conds, params = [], {}
    if date_from:
        conds.append("i.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("i.created_at <= CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if severity:
        conds.append("i.severity = :severity")
        params["severity"] = severity
    if status:
        conds.append("i.status = :status")
        params["status"] = status

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    result = await db.execute(
        text(
            f"SELECT i.id, c.name AS camera_name, i.title, i.description, i.severity, "
            f"i.status, i.alert_code, i.is_auto_created, i.resolved_at, i.created_at, i.updated_at "
            f"FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id "
            f"{where} ORDER BY i.created_at DESC LIMIT {MAX_ROWS}"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    fields = [
        "id", "camera_name", "title", "description", "severity", "status",
        "alert_code", "is_auto_created", "resolved_at", "created_at", "updated_at",
    ]
    return _csv_response(f"incidents_{_now_tag()}.csv", _make_csv(rows, fields))


# ── Detections ────────────────────────────────────────────


@router.get("/detections", dependencies=[Depends(require_permission("detection:read"))])
async def export_detections(
    db: AsyncSession = Depends(get_db_with_tenant),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    module_type: Optional[str] = Query(None),
    camera_id: Optional[str] = Query(None),
):
    date_from, date_to, module_type, camera_id = _s(date_from), _s(date_to), _s(module_type), _s(camera_id)
    conds, params = [], {}
    if date_from:
        conds.append("d.detected_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("d.detected_at <= CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if module_type:
        conds.append("d.module_type = :module_type")
        params["module_type"] = module_type
    if camera_id:
        conds.append("d.camera_id = CAST(:camera_id AS uuid)")
        params["camera_id"] = camera_id

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    result = await db.execute(
        text(
            f"SELECT d.id, c.name AS camera_name, d.module_type, d.confidence, d.detected_at "
            f"FROM detections d LEFT JOIN cameras c ON c.id = d.camera_id "
            f"{where} ORDER BY d.detected_at DESC LIMIT {MAX_ROWS}"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    fields = ["id", "camera_name", "module_type", "confidence", "detected_at"]
    return _csv_response(f"detections_{_now_tag()}.csv", _make_csv(rows, fields))


# ── Audit Logs ────────────────────────────────────────────


@router.get("/audit-logs", dependencies=[Depends(require_permission("audit:read"))])
async def export_audit_logs(
    db: AsyncSession = Depends(get_db_with_tenant),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
):
    date_from, date_to, action, resource_type = _s(date_from), _s(date_to), _s(action), _s(resource_type)
    conds, params = [], {}
    if date_from:
        conds.append("al.created_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("al.created_at <= CAST(:date_to AS date) + interval '1 day'")
        params["date_to"] = date_to
    if action:
        conds.append("al.action ILIKE :action")
        params["action"] = f"%{action}%"
    if resource_type:
        conds.append("al.resource_type = :resource_type")
        params["resource_type"] = resource_type

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    result = await db.execute(
        text(
            f"SELECT al.id, u.email AS user_email, al.action, al.resource_type, "
            f"al.resource_id, al.ip_address, al.created_at "
            f"FROM audit_logs al LEFT JOIN users u ON u.id = al.user_id "
            f"{where} ORDER BY al.created_at DESC LIMIT {MAX_ROWS}"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    fields = ["id", "user_email", "action", "resource_type", "resource_id", "ip_address", "created_at"]
    return _csv_response(f"audit_logs_{_now_tag()}.csv", _make_csv(rows, fields))
