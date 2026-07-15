"""PDF report generation using ReportLab.

Supported report types:
- site_summary:  Daily/weekly summary of alerts, incidents, and patrol sessions for a site
- dob_report:    Daily Occurrence Book printout for a date range
- incident_report: Single incident full detail report with evidence list
"""

import io
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, Spacer, Table, TableStyle, SimpleDocTemplate, HRFlowable,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

BRAND_PURPLE = (108 / 255, 99 / 255, 255 / 255)
BRAND_TEAL = (0 / 255, 217 / 255, 192 / 255)


def _check_reportlab():
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "reportlab is not installed — rebuild the container with reportlab>=4.2",
        )


def _make_styles():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Title"],
        fontSize=20,
        textColor=colors.HexColor("#6C63FF"),
        spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        "BrandH2",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#00D9C0"),
        spaceBefore=10,
        spaceAfter=4,
    )
    return styles, title_style, h2_style


def _severity_color(severity: str) -> str:
    return {
        "critical": "#FF4560",
        "high": "#FF7F50",
        "medium": "#FFA500",
        "low": "#00E396",
        "info": "#6C63FF",
    }.get(severity, "#888888")


# ── Site Summary Report ───────────────────────────────────────────────────────

@router.get("/site-summary")
async def generate_site_summary(
    site_id: str,
    date_from: str,
    date_until: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_reportlab()
    df = date.fromisoformat(date_from[:10])
    du = date.fromisoformat(date_until[:10])
    # Gather data
    site_row = (await db.execute(text("SELECT name, address FROM sites WHERE id = :id"), {"id": site_id})).first()
    if site_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found")

    alerts = (
        await db.execute(
            text(
                """
                SELECT a.severity, a.module_type, a.title, a.status, a.created_at
                FROM alerts a
                JOIN cameras c ON c.id = a.camera_id
                WHERE c.site_id = CAST(:sid AS uuid)
                  AND a.created_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
                ORDER BY a.created_at DESC LIMIT 200
                """
            ),
            {"sid": site_id, "df": df, "du": du},
        )
    ).fetchall()

    incidents = (
        await db.execute(
            text(
                """
                SELECT i.severity, i.title, i.status, i.created_at, i.resolved_at
                FROM incidents i
                JOIN cameras c ON c.id = i.camera_id
                WHERE c.site_id = CAST(:sid AS uuid)
                  AND i.created_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
                ORDER BY i.created_at DESC LIMIT 100
                """
            ),
            {"sid": site_id, "df": df, "du": du},
        )
    ).fetchall()

    # Build PDF
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style, h2_style = _make_styles()
    normal = styles["Normal"]
    elements = []

    elements.append(Paragraph("7th AI Vision", title_style))
    elements.append(Paragraph(f"Site Summary Report — {site_row.name}", styles["Heading1"]))
    if site_row.address:
        elements.append(Paragraph(site_row.address, normal))
    elements.append(Paragraph(f"Period: {date_from} to {date_until}", normal))
    elements.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        normal,
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")))

    # KPI summary
    elements.append(Paragraph("Summary", h2_style))
    kpi_data = [
        ["Metric", "Count"],
        ["Total Alerts", str(len(alerts))],
        ["Total Incidents", str(len(incidents))],
        ["Critical Alerts", str(sum(1 for a in alerts if a.severity == "critical"))],
        ["Open Incidents", str(sum(1 for i in incidents if i.status == "open"))],
        ["Resolved Incidents", str(sum(1 for i in incidents if i.status == "resolved"))],
    ]
    kpi_table = Table(kpi_data, colWidths=[120 * mm, 40 * mm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C63FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 8 * mm))

    # Alerts table
    if alerts:
        elements.append(Paragraph(f"Alerts ({len(alerts)})", h2_style))
        alert_data = [["Time", "Module", "Severity", "Title", "Status"]]
        for a in alerts[:50]:  # cap at 50 rows for PDF size
            alert_data.append([
                a.created_at.strftime("%m-%d %H:%M") if hasattr(a.created_at, "strftime") else str(a.created_at)[:16],
                a.module_type or "",
                a.severity.upper(),
                (a.title or "")[:50],
                a.status,
            ])
        at = Table(alert_data, colWidths=[25 * mm, 25 * mm, 20 * mm, 80 * mm, 20 * mm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        elements.append(at)

    # Incidents table
    if incidents:
        elements.append(Paragraph(f"Incidents ({len(incidents)})", h2_style))
        inc_data = [["Time", "Severity", "Status", "Title"]]
        for i in incidents[:50]:
            inc_data.append([
                i.created_at.strftime("%m-%d %H:%M") if hasattr(i.created_at, "strftime") else str(i.created_at)[:16],
                i.severity.upper(),
                i.status,
                (i.title or "")[:70],
            ])
        it = Table(inc_data, colWidths=[25 * mm, 20 * mm, 20 * mm, 105 * mm])
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]))
        elements.append(it)

    doc.build(elements)
    buf.seek(0)
    filename = f"site_summary_{site_row.name.replace(' ', '_')}_{date_from[:10]}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── DOB Report ────────────────────────────────────────────────────────────────

@router.get("/dob")
async def generate_dob_report(
    date_from: str,
    date_until: str,
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_reportlab()
    params: dict = {
        "df": date.fromisoformat(date_from[:10]),
        "du": date.fromisoformat(date_until[:10]),
    }
    site_filter = ""
    if site_id:
        site_filter = "AND e.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id

    entries = (
        await db.execute(
            text(
                f"""
                SELECT e.entry_type, e.body, e.severity, e.occurred_at,
                       u.full_name AS author_name, s.name AS site_name
                FROM occurrence_book_entries e
                LEFT JOIN users u ON u.id = e.author_user_id
                LEFT JOIN sites s ON s.id = e.site_id
                WHERE e.occurred_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
                {site_filter}
                ORDER BY e.occurred_at ASC
                """
            ),
            params,
        )
    ).fetchall()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style, h2_style = _make_styles()
    elements = []

    elements.append(Paragraph("7th AI Vision", title_style))
    elements.append(Paragraph("Daily Occurrence Book", styles["Heading1"]))
    elements.append(Paragraph(f"Period: {date_from} to {date_until}", styles["Normal"]))
    elements.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")))
    elements.append(Spacer(1, 4 * mm))

    if not entries:
        elements.append(Paragraph("No entries found for this period.", styles["Normal"]))
    else:
        data = [["Time", "Type", "Site", "Author", "Entry"]]
        for e in entries:
            data.append([
                e.occurred_at.strftime("%Y-%m-%d %H:%M") if hasattr(e.occurred_at, "strftime") else str(e.occurred_at)[:16],
                (e.entry_type or "").replace("_", " ").title(),
                (e.site_name or "—")[:20],
                (e.author_name or "—")[:20],
                Paragraph((e.body or "")[:200], styles["Normal"]),
            ])
        tbl = Table(data, colWidths=[30 * mm, 22 * mm, 22 * mm, 22 * mm, 74 * mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
        ]))
        elements.append(tbl)

    doc.build(elements)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="daily_occurrence_book.pdf"'},
    )


# ── Incident Detail Report ────────────────────────────────────────────────────

@router.get("/incident/{incident_id}")
async def generate_incident_report(
    incident_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
):
    _check_reportlab()
    inc = (
        await db.execute(
            text(
                """
                SELECT i.*, c.name AS camera_name, u.full_name AS assigned_user_name,
                       g.full_name AS guard_name, s.name AS site_name
                FROM incidents i
                LEFT JOIN cameras c ON c.id = i.camera_id
                LEFT JOIN users u ON u.id = i.assigned_to_user_id
                LEFT JOIN users g ON g.id = i.dispatched_guard_id
                LEFT JOIN sites s ON s.id = (SELECT site_id FROM cameras WHERE id = i.camera_id)
                WHERE i.id = :id
                """
            ),
            {"id": incident_id},
        )
    ).first()
    if inc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")

    notes = (
        await db.execute(
            text(
                """
                SELECT n.note, n.created_at, u.full_name AS author
                FROM incident_notes n
                LEFT JOIN users u ON u.id = n.author_user_id
                WHERE n.incident_id = :id ORDER BY n.created_at ASC
                """
            ),
            {"id": incident_id},
        )
    ).fetchall()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=15 * mm, bottomMargin=15 * mm)
    styles, title_style, h2_style = _make_styles()
    elements = []

    elements.append(Paragraph("7th AI Vision", title_style))
    elements.append(Paragraph(f"Incident Report — {inc.title}", styles["Heading1"]))
    elements.append(Paragraph(
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#6C63FF")))

    elements.append(Paragraph("Incident Details", h2_style))
    details = [
        ["Field", "Value"],
        ["ID", str(inc.id)],
        ["Severity", inc.severity.upper()],
        ["Status", inc.status],
        ["Camera", inc.camera_name or "—"],
        ["Site", inc.site_name or "—"],
        ["Created", str(inc.created_at)[:19] if inc.created_at else "—"],
        ["Assigned To", inc.assigned_user_name or "—"],
        ["Guard Dispatched", inc.guard_name or "—"],
        ["Guard Arrived", str(inc.guard_arrived_at)[:19] if inc.guard_arrived_at else "—"],
        ["Resolved", str(inc.resolved_at)[:19] if inc.resolved_at else "—"],
        ["SLA Deadline", str(inc.sla_deadline_at)[:19] if inc.sla_deadline_at else "—"],
        ["SLA Breached", "YES" if inc.sla_breached else "No"],
    ]
    dtbl = Table(details, colWidths=[50 * mm, 120 * mm])
    dtbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6C63FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5FF")]),
    ]))
    elements.append(dtbl)

    if inc.description:
        elements.append(Paragraph("Description", h2_style))
        elements.append(Paragraph(inc.description, styles["Normal"]))

    if notes:
        elements.append(Paragraph(f"Timeline Notes ({len(notes)})", h2_style))
        ntbl_data = [["Time", "Author", "Note"]]
        for n in notes:
            ntbl_data.append([
                str(n.created_at)[:16] if n.created_at else "—",
                n.author or "—",
                Paragraph(n.note or "", styles["Normal"]),
            ])
        ntbl = Table(ntbl_data, colWidths=[30 * mm, 35 * mm, 105 * mm])
        ntbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(ntbl)

    doc.build(elements)
    buf.seek(0)
    filename = f"incident_{incident_id[:8]}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Scheduler helpers (no FastAPI Depends — callable from scheduler_main.py) ──

async def build_site_summary_bytes(db: AsyncSession, site_id: str | None,
                                   date_from: str, date_until: str) -> bytes:
    """Return raw PDF bytes for a site summary. Used by the scheduler job."""
    if not REPORTLAB_AVAILABLE:
        return b"%PDF placeholder - reportlab not installed"

    site_row = None
    if site_id:
        site_row = (await db.execute(
            text("SELECT name, address FROM sites WHERE id = :id"), {"id": site_id}
        )).first()

    params: dict = {"df": date_from, "du": date_until}
    site_filter = ""
    if site_id:
        site_filter = "AND c.site_id = CAST(:sid AS uuid)"
        params["sid"] = site_id

    alerts_rows = (await db.execute(text(f"""
        SELECT a.severity, a.module_type, a.title, a.status, a.created_at
        FROM alerts a JOIN cameras c ON c.id = a.camera_id
        WHERE a.created_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
        {site_filter}
        ORDER BY a.created_at DESC LIMIT 200
    """), params)).fetchall()

    incidents_rows = (await db.execute(text(f"""
        SELECT i.severity, i.title, i.status, i.created_at
        FROM incidents i JOIN cameras c ON c.id = i.camera_id
        WHERE i.created_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
        {site_filter}
        ORDER BY i.created_at DESC LIMIT 100
    """), params)).fetchall()

    from reportlab.lib import colors as _colors
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.lib.units import mm as _mm
    from reportlab.platypus import (
        Paragraph as _P, Spacer as _S, Table as _T, TableStyle as _TS,
        SimpleDocTemplate as _Doc, HRFlowable as _HR,
    )
    styles, title_style, h2_style = _make_styles()
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=_A4, leftMargin=15 * _mm, rightMargin=15 * _mm,
               topMargin=15 * _mm, bottomMargin=15 * _mm)
    elements = []
    elements.append(_P("7th AI Vision", title_style))
    elements.append(_P(f"Site Summary — {site_row.name if site_row else 'All Sites'}", styles["Heading1"]))
    elements.append(_P(f"Period: {date_from} to {date_until}", styles["Normal"]))
    elements.append(_P(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]))
    elements.append(_HR(width="100%", thickness=1, color=_colors.HexColor("#6C63FF")))
    elements.append(_S(1, 4 * _mm))
    kpi_data = [
        ["Metric", "Value"],
        ["Alerts", str(len(alerts_rows))],
        ["Incidents", str(len(incidents_rows))],
        ["Critical Alerts", str(sum(1 for a in alerts_rows if a.severity == "critical"))],
    ]
    elements.append(_T(kpi_data, colWidths=[100 * _mm, 70 * _mm]))
    doc.build(elements)
    buf.seek(0)
    return buf.read()


async def build_dob_bytes(db: AsyncSession, site_id: str | None,
                          date_from: str, date_until: str) -> bytes:
    """Return raw PDF bytes for a DOB report. Used by the scheduler job."""
    if not REPORTLAB_AVAILABLE:
        return b"%PDF placeholder - reportlab not installed"

    params: dict = {"df": date_from, "du": date_until}
    site_filter = ""
    if site_id:
        site_filter = "AND e.site_id = CAST(:sid AS uuid)"
        params["sid"] = site_id

    entries = (await db.execute(text(f"""
        SELECT e.entry_type, e.body, e.severity, e.occurred_at,
               u.full_name AS author_name, s.name AS site_name
        FROM occurrence_book_entries e
        LEFT JOIN users u ON u.id = e.author_user_id
        LEFT JOIN sites s ON s.id = e.site_id
        WHERE e.occurred_at BETWEEN CAST(:df AS timestamptz) AND CAST(:du AS timestamptz)
        {site_filter}
        ORDER BY e.occurred_at ASC
    """), params)).fetchall()

    from reportlab.lib import colors as _colors
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.lib.units import mm as _mm
    from reportlab.platypus import (
        Paragraph as _P, Spacer as _S, Table as _T, TableStyle as _TS,
        SimpleDocTemplate as _Doc, HRFlowable as _HR,
    )
    styles, title_style, h2_style = _make_styles()
    buf = io.BytesIO()
    doc = _Doc(buf, pagesize=_A4, leftMargin=15 * _mm, rightMargin=15 * _mm,
               topMargin=15 * _mm, bottomMargin=15 * _mm)
    elements = []
    elements.append(_P("7th AI Vision", title_style))
    elements.append(_P("Daily Occurrence Book", styles["Heading1"]))
    elements.append(_P(f"Period: {date_from} to {date_until}", styles["Normal"]))
    elements.append(_P(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]))
    elements.append(_HR(width="100%", thickness=1, color=_colors.HexColor("#6C63FF")))
    elements.append(_S(1, 4 * _mm))
    if entries:
        data = [["Time", "Type", "Author", "Entry"]]
        for e in entries[:200]:
            data.append([
                e.occurred_at.strftime("%m-%d %H:%M") if hasattr(e.occurred_at, "strftime") else str(e.occurred_at)[:16],
                (e.entry_type or "").replace("_", " ").title(),
                (e.author_name or "—")[:20],
                _P((e.body or "")[:200], styles["Normal"]),
            ])
        tbl = _T(data, colWidths=[28 * _mm, 28 * _mm, 28 * _mm, 86 * _mm])
        tbl.setStyle(_TS([
            ("BACKGROUND", (0, 0), (-1, 0), _colors.HexColor("#1A0828")),
            ("TEXTCOLOR", (0, 0), (-1, 0), _colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, _colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(tbl)
    else:
        elements.append(_P("No entries found.", styles["Normal"]))
    doc.build(elements)
    buf.seek(0)
    return buf.read()
