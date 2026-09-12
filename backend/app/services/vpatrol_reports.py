"""The patrol report: what was inspected, and the picture that proves it.

THE IMAGE COMES FROM DISK, NEVER FROM THE CAMERA. Section 23 is explicit and the
reason is the whole point of the feature: a report generated a week later that
fetched a fresh frame would show a gate that is closed *now* beside an answer
saying it was open *then*. The stored snapshot is the evidence; the live camera
is a different fact about a different moment.

EVERY VALUE COMES FROM THE SESSION TABLES. Camera names, question text and
options were frozen at session creation, so a report rendered today and the same
report rendered next year say the same thing even if the schedule was rewritten
in between.

A MISSING IMAGE FILE SAYS SO, LOUDLY. Quietly omitting it would make a patrol
where the snapshot was lost look identical to one where the camera was never
reached — and a report that hides a gap in its own evidence is worse than one
that admits it.

reportlab is imported lazily and guarded, matching routers/payroll.py and
routers/reports.py: a container built without it should fail with a sentence
somebody can act on rather than an ImportError at startup.
"""
from __future__ import annotations

import io
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

MAX_IMAGE_WIDTH_MM = 120
MAX_IMAGE_HEIGHT_MM = 80


def _check_reportlab():
    try:
        import reportlab  # noqa: F401
    except ImportError:  # pragma: no cover - environment guard
        raise RuntimeError(
            "reportlab is not installed — rebuild the container with reportlab>=4.2"
        )


async def load_session_for_report(db: AsyncSession, session_id: str) -> dict:
    """Everything the report needs, read only from session tables."""
    session = (await db.execute(text("""
        SELECT s.id, s.patrol_number, s.schedule_name, s.scheduled_for,
               s.started_at, s.completed_at, s.status, s.camera_count,
               s.completed_camera_count, s.notes,
               si.name AS site_name,
               u.full_name AS officer_name
          FROM virtual_patrol_sessions s
          JOIN sites si ON si.id = s.site_id
          LEFT JOIN users u ON u.id = s.officer_user_id
         WHERE s.id = CAST(:id AS uuid)
    """), {"id": session_id})).mappings().first()
    if session is None:
        raise ValueError("Patrol session not found")

    cameras = (await db.execute(text("""
        SELECT id, sequence_no, camera_name, camera_code, status,
               snapshot_path, snapshot_taken_at, snapshot_error, officer_notes,
               camera_metadata->>'location' AS location
          FROM virtual_patrol_session_cameras
         WHERE session_id = CAST(:id AS uuid)
         ORDER BY sequence_no
    """), {"id": session_id})).mappings().all()

    answers = (await db.execute(text("""
        SELECT q.session_camera_id, q.sequence_no, q.question_text,
               q.question_type, a.answer_text, a.answer_json,
               a.is_exception, a.exception_reason, a.incident_id,
               a.answered_at,
               u.full_name AS answered_by
          FROM virtual_patrol_session_questions q
          JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
          LEFT JOIN virtual_patrol_session_answers a ON a.session_question_id = q.id
          LEFT JOIN users u ON u.id = a.answered_by_user_id
         WHERE c.session_id = CAST(:id AS uuid)
         ORDER BY q.session_camera_id, q.sequence_no
    """), {"id": session_id})).mappings().all()

    grouped: dict[str, list] = {}
    for a in answers:
        grouped.setdefault(str(a["session_camera_id"]), []).append(a)

    return {"session": dict(session),
            "cameras": [dict(c) for c in cameras],
            "answers": grouped}


def _fmt(value, fallback: str = "—") -> str:
    if value is None:
        return fallback
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _answer_display(row) -> str:
    if row["answer_json"] is not None:
        payload = row["answer_json"]
        if isinstance(payload, list):
            return ", ".join(str(p) for p in payload)
        return str(payload)
    if row["answer_text"]:
        return row["answer_text"]
    return "Not answered"


def _snapshot_flowable(camera: dict, styles):
    """The stored frame, or an explicit statement of why there isn't one."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph

    if camera["snapshot_path"]:
        path = Path(settings.EVIDENCE_ROOT) / camera["snapshot_path"]
        if path.exists():
            try:
                img = Image(str(path))
                # Fit the box, preserving aspect ratio. A stretched frame is a
                # misleading frame.
                ratio = img.imageHeight / float(img.imageWidth or 1)
                width = MAX_IMAGE_WIDTH_MM * mm
                height = width * ratio
                if height > MAX_IMAGE_HEIGHT_MM * mm:
                    height = MAX_IMAGE_HEIGHT_MM * mm
                    width = height / (ratio or 1)
                img.drawWidth, img.drawHeight = width, height
                return img
            except Exception:  # pragma: no cover - corrupt file on disk
                return Paragraph(
                    "<b>Snapshot unreadable.</b> The stored image exists but could "
                    "not be rendered.", styles["BodyText"])
        return Paragraph(
            "<b>Snapshot missing from storage.</b> The patrol recorded a capture "
            "but the file is no longer present.", styles["BodyText"])

    reason = camera["snapshot_error"] or "No snapshot was captured."
    return Paragraph(f"<b>No snapshot.</b> {reason}", styles["BodyText"])


def render_pdf(data: dict) -> bytes:
    """Build the PDF from already-loaded session data."""
    _check_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    session = data["session"]
    styles = getSampleStyleSheet()
    title = ParagraphStyle("vpTitle", parent=styles["Title"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("vpSub", parent=styles["Normal"], fontSize=9,
                         textColor=colors.HexColor("#555555"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"Virtual Patrol {session['patrol_number']}")

    story = [
        Paragraph("SEVENTH AI VISION", title),
        Paragraph("Virtual Patrol Report", styles["Heading2"]),
        Paragraph(session["patrol_number"], sub),
        Spacer(1, 6),
        HRFlowable(width="100%", color=colors.HexColor("#999999")),
        Spacer(1, 8),
    ]

    facts = [
        ["Site", session["site_name"], "Patrol", session["schedule_name"]],
        ["Scheduled", _fmt(session["scheduled_for"]),
         "Duty Officer", session["officer_name"] or "Unassigned"],
        ["Started", _fmt(session["started_at"], "Not started"),
         "Completed", _fmt(session["completed_at"], "Not completed")],
        ["Status", session["status"].replace("_", " ").title(),
         "Cameras", f"{session['completed_camera_count']} of {session['camera_count']}"],
    ]
    table = Table(facts, colWidths=[28 * mm, 55 * mm, 28 * mm, 55 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#555555")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 10)]

    for camera in data["cameras"]:
        heading = f"{camera['sequence_no']}. {camera['camera_name']}"
        if camera["location"]:
            heading += f" — {camera['location']}"
        story += [
            HRFlowable(width="100%", color=colors.HexColor("#DDDDDD")),
            Spacer(1, 4),
            Paragraph(heading, styles["Heading3"]),
            Paragraph(
                f"Status: {camera['status'].replace('_', ' ').title()} &nbsp;•&nbsp; "
                f"Snapshot: {_fmt(camera['snapshot_taken_at'], 'none')}", sub),
            Spacer(1, 4),
            _snapshot_flowable(camera, styles),
            Spacer(1, 6),
        ]

        rows = [["#", "Question", "Answer", "Answered by"]]
        exception_rows = []
        for idx, a in enumerate(data["answers"].get(str(camera["id"]), []), start=1):
            rows.append([
                str(a["sequence_no"]),
                Paragraph(a["question_text"], styles["BodyText"]),
                Paragraph(_answer_display(a), styles["BodyText"]),
                a["answered_by"] or "—",
            ])
            if a["is_exception"]:
                exception_rows.append(idx)

        if len(rows) > 1:
            qt = Table(rows, colWidths=[8 * mm, 78 * mm, 50 * mm, 30 * mm], repeatRows=1)
            style = [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            # Exceptions are tinted rather than merely noted. A reviewer scanning
            # twelve cameras should not have to read every row to find the one
            # that went wrong.
            for r in exception_rows:
                style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FDE7E7")))
            qt.setStyle(TableStyle(style))
            story += [qt]
        else:
            story += [Paragraph("No questions were configured for this camera.", sub)]

        if camera["officer_notes"]:
            story += [Spacer(1, 4),
                      Paragraph(f"<b>Officer notes:</b> {camera['officer_notes']}",
                                styles["BodyText"])]
        story += [Spacer(1, 10)]

    if session["notes"]:
        story += [HRFlowable(width="100%", color=colors.HexColor("#DDDDDD")),
                  Spacer(1, 4),
                  Paragraph(f"<b>Patrol notes:</b> {session['notes']}", styles["BodyText"])]

    doc.build(story)
    return buf.getvalue()


async def build_patrol_pdf(db: AsyncSession, session_id: str) -> bytes:
    return render_pdf(await load_session_for_report(db, session_id))


# ── Excel ────────────────────────────────────────────────────────────────────

def _check_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:  # pragma: no cover - environment guard
        raise RuntimeError(
            "openpyxl is not installed - rebuild the container with openpyxl>=3.1"
        )


def render_xlsx(data: dict) -> bytes:
    """Four sheets, because they answer four different questions.

    Summary is what happened. Camera Inspection is what was looked at.
    Questions is the full record. Exceptions is the only sheet most people open,
    so it exists separately rather than as a filter somebody has to remember to
    apply -- a findings list buried inside two hundred rows of "YES" is a
    findings list nobody reads.

    Unlike the PDF this carries no images. A spreadsheet is for sorting and
    totalling; the evidence lives in the PDF, and the snapshot timestamp here
    points at it.
    """
    _check_openpyxl()
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    session = data["session"]
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="EEEEEE")
    exception_fill = PatternFill("solid", fgColor="FDE7E7")

    wb = Workbook()

    def sheet(title: str, headers: list[str], first: bool = False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(headers)
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        ws.freeze_panes = "A2"
        return ws

    def autosize(ws, widths: list[int]):
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # ── Summary ──
    done = session["completed_camera_count"] or 0
    total = session["camera_count"] or 0
    ws = sheet("Summary", ["Field", "Value"], first=True)
    for label, value in (
        ("Site", session["site_name"]),
        ("Patrol", session["schedule_name"]),
        ("Patrol number", session["patrol_number"]),
        ("Scheduled", _fmt(session["scheduled_for"])),
        ("Duty officer", session["officer_name"] or "Unassigned"),
        ("Started", _fmt(session["started_at"], "Not started")),
        ("Completed", _fmt(session["completed_at"], "Not completed")),
        ("Status", session["status"].replace("_", " ").title()),
        ("Cameras inspected", f"{done} of {total}"),
        # Stored as text, not a float: 66.67% invites somebody to average two
        # patrols' percentages, which is not a number that means anything.
        ("Completion", f"{round(done / total * 100) if total else 0}%"),
    ):
        ws.append([label, value])
    autosize(ws, [22, 46])

    # ── Camera Inspection ──
    ws = sheet("Camera Inspection",
               ["#", "Camera", "Location", "Status", "Snapshot taken",
                "Officer notes"])
    for cam in data["cameras"]:
        ws.append([
            cam["sequence_no"], cam["camera_name"], cam["location"] or "",
            cam["status"].replace("_", " ").title(),
            _fmt(cam["snapshot_taken_at"], "none"),
            cam["officer_notes"] or "",
        ])
    autosize(ws, [5, 26, 20, 20, 20, 50])

    # ── Questions & Answers ──
    ws = sheet("Questions & Answers",
               ["Camera", "#", "Question", "Answer", "Answered by",
                "Answered at", "Exception"])
    for cam in data["cameras"]:
        for a in data["answers"].get(str(cam["id"]), []):
            ws.append([
                cam["camera_name"], a["sequence_no"], a["question_text"],
                _answer_display(a), a["answered_by"] or "",
                _fmt(a.get("answered_at"), ""),
                "Yes" if a["is_exception"] else "",
            ])
            if a["is_exception"]:
                for cell in ws[ws.max_row]:
                    cell.fill = exception_fill
    autosize(ws, [24, 5, 52, 20, 20, 18, 12])

    # ── Exceptions ──
    ws = sheet("Exceptions",
               ["Camera", "Question", "Answer", "Reason", "Incident", "Answered at"])
    found = 0
    for cam in data["cameras"]:
        for a in data["answers"].get(str(cam["id"]), []):
            if not a["is_exception"]:
                continue
            found += 1
            ws.append([
                cam["camera_name"], a["question_text"], _answer_display(a),
                a["exception_reason"] or "",
                str(a["incident_id"]) if a["incident_id"] else "No incident raised",
                _fmt(a.get("answered_at"), ""),
            ])
    if not found:
        # An empty sheet reads as "the export is broken". This reads as the
        # good news it actually is.
        ws.append(["No exceptions were raised on this patrol.", "", "", "", "", ""])
        ws["A2"].alignment = Alignment(horizontal="left")
    autosize(ws, [24, 52, 20, 40, 38, 18])

    import io as _io
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def build_patrol_xlsx(db: AsyncSession, session_id: str) -> bytes:
    return render_xlsx(await load_session_for_report(db, session_id))
