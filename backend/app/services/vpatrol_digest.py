"""The digest: many patrols in one email, for people who did not ask for each.

SEPARATE FROM vpatrol_reports ON PURPOSE. That module answers "what happened on
this patrol, and what does the picture show" — one session, snapshots embedded,
a document an agency hands to a client. This one answers "what happened across
this week" — no images, one row per patrol, a covering summary. Same feature
family, different question, and bundling them would mean every change to one
risked the other.

NOT A CONCATENATION OF PER-PATROL PDFs. A month of daily patrols is thirty
documents and a hundred megabytes of snapshots: no mail server will accept it
and nobody will open it. The digest carries the numbers and the findings, and
says plainly that the individual reports are still in the application.

THE WINDOW IS COMPARED IN THE SCHEDULE'S OWN TIMEZONE. Comparing UTC timestamps
against local dates puts eight hours of every Singapore day in the wrong bucket
— precisely the error a summary is trusted not to make.
"""
from __future__ import annotations

import io

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.vpatrol_reports import _check_openpyxl


async def load_digest(db: AsyncSession, *, schedule_id: str, start, end,
                      tz_name: str | None = None) -> dict:
    """Every patrol for one schedule inside a closed window, with its findings."""
    tz = tz_name or "Asia/Singapore"

    schedule = (await db.execute(text("""
        SELECT s.id, s.name, s.timezone, si.name AS site_name
          FROM virtual_patrol_schedules s
          JOIN sites si ON si.id = s.site_id
         WHERE s.id = CAST(:id AS uuid)
    """), {"id": schedule_id})).mappings().first()
    if schedule is None:
        raise ValueError("Patrol schedule not found")

    patrols = (await db.execute(text("""
        SELECT s.id, s.patrol_number, s.scheduled_for, s.started_at, s.completed_at,
               s.status, s.camera_count, s.completed_camera_count,
               u.full_name AS officer_name,
               (SELECT count(*) FROM virtual_patrol_session_answers a
                  JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
                  JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
                 WHERE c.session_id = s.id AND a.is_exception) AS exception_count
          FROM virtual_patrol_sessions s
          LEFT JOIN users u ON u.id = s.officer_user_id
         WHERE s.schedule_id = CAST(:id AS uuid)
           AND (s.scheduled_for AT TIME ZONE :tz)::date BETWEEN :a AND :b
         ORDER BY s.scheduled_for
    """), {"id": schedule_id, "tz": tz, "a": start, "b": end})).mappings().all()

    exceptions = (await db.execute(text("""
        SELECT s.patrol_number, c.camera_name, q.question_text,
               a.answer_text, a.answered_at, a.incident_id
          FROM virtual_patrol_session_answers a
          JOIN virtual_patrol_session_questions q ON q.id = a.session_question_id
          JOIN virtual_patrol_session_cameras c ON c.id = q.session_camera_id
          JOIN virtual_patrol_sessions s ON s.id = c.session_id
         WHERE s.schedule_id = CAST(:id AS uuid)
           AND a.is_exception
           AND (s.scheduled_for AT TIME ZONE :tz)::date BETWEEN :a AND :b
         ORDER BY a.answered_at
    """), {"id": schedule_id, "tz": tz, "a": start, "b": end})).mappings().all()

    return {"schedule": dict(schedule), "start": start, "end": end,
            "patrols": [dict(p) for p in patrols],
            "exceptions": [dict(e) for e in exceptions]}


def summary_text(data: dict) -> str:
    """The covering body, as plain text.

    Ordered so the numbers that matter survive being read as a notification
    preview on a phone: how many patrols, how many were actually completed, how
    many findings. A summary whose headline is below a table is one nobody
    reads.
    """
    s = data["schedule"]
    patrols, exc = data["patrols"], data["exceptions"]
    done = sum(1 for p in patrols if p["status"] == "COMPLETED")
    partial = sum(1 for p in patrols if p["status"] == "PARTIALLY_COMPLETED")
    missed = sum(1 for p in patrols if p["status"] == "MISSED")

    lines = [
        f"{s['name']} - {s['site_name']}",
        f"{data['start']:%d %b %Y} to {data['end']:%d %b %Y}",
        "",
        f"Patrols scheduled:   {len(patrols)}",
        f"  completed:           {done}",
        f"  partially completed: {partial}",
        f"  missed:              {missed}",
        f"Findings raised:     {len(exc)}",
        "",
    ]

    if exc:
        lines += ["Findings", "--------"]
        for e in exc:
            when = e["answered_at"]
            stamp = when.strftime("%d %b %H:%M") if when else "-"
            raised = " (incident raised)" if e.get("incident_id") else ""
            lines.append(
                f"  {stamp}  {e['camera_name']}: {e['question_text']} "
                f"-> {e['answer_text'] or '-'}{raised}")
        lines.append("")

    if missed:
        lines += ["A missed patrol is one nobody started before its grace period "
                  "expired. The attached workbook lists which.", ""]

    lines.append("The attached workbook has a row per patrol. Individual reports, "
                 "including the snapshot captured at each camera, remain available "
                 "in the application.")
    return "\n".join(lines)


def render_xlsx(data: dict) -> bytes:
    """Two sheets: a row per patrol, and the findings. No images."""
    _check_openpyxl()
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    head = Font(bold=True)
    s = data["schedule"]

    ws = wb.active
    ws.title = "Patrols"
    ws.append([f"{s['name']} - {s['site_name']}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"{data['start']:%d %b %Y} to {data['end']:%d %b %Y}"])
    ws.append([])

    cols = ["Patrol", "Scheduled", "Officer", "Status", "Cameras", "Findings"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        ws.cell(row=4, column=c).font = head
    for p in data["patrols"]:
        ws.append([
            p["patrol_number"],
            p["scheduled_for"].strftime("%Y-%m-%d %H:%M") if p["scheduled_for"] else "-",
            p["officer_name"] or "Unassigned",
            p["status"],
            f"{p['completed_camera_count'] or 0}/{p['camera_count'] or 0}",
            p["exception_count"] or 0,
        ])
    for col, width in zip("ABCDEF", (26, 20, 22, 22, 10, 10)):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Findings")
    cols2 = ["Patrol", "Camera", "Question", "Answer", "When", "Incident"]
    ws2.append(cols2)
    for c in range(1, len(cols2) + 1):
        ws2.cell(row=1, column=c).font = head
    for e in data["exceptions"]:
        ws2.append([
            e["patrol_number"], e["camera_name"], e["question_text"],
            e["answer_text"] or "-",
            e["answered_at"].strftime("%Y-%m-%d %H:%M") if e["answered_at"] else "-",
            "Yes" if e.get("incident_id") else "No",
        ])
    for col, width in zip("ABCDEF", (26, 22, 46, 18, 20, 10)):
        ws2.column_dimensions[col].width = width
    ws2.column_dimensions["C"].alignment = Alignment(wrap_text=True)

    if not data["exceptions"]:
        # An empty sheet reads as a broken export. And "no findings" is the
        # result an agency most wants to be able to show a client, so it is
        # stated rather than left as blank space.
        ws2.append([])
        ws2.append(["No findings were raised in this period."])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def build(db: AsyncSession, *, schedule_id: str, start, end,
                tz_name: str | None = None) -> tuple[bytes, str]:
    """The workbook and its covering text, for the sender."""
    data = await load_digest(db, schedule_id=schedule_id, start=start, end=end,
                             tz_name=tz_name)
    return render_xlsx(data), summary_text(data)
