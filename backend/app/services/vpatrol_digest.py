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

TIMES ARE PRINTED IN THAT SAME ZONE, AND THE DIGEST SAYS WHICH. The database
hands back UTC, so a finding answered at 22:00 Singapore printed straight from
it reads 14:00. The email and its workbook print every time on the schedule's
clock, with the same Asia/Singapore fallback the scheduler and the patrol
report use (vpatrol_reports does the same for one patrol).
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.vpatrol_reports import _check_openpyxl
from app.services.vpatrol_scheduler import _zone


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
            "exceptions": [dict(e) for e in exceptions],
            "timezone": _zone(tz).key}


def _digest_zone(data: dict) -> ZoneInfo:
    """The zone the digest prints in. Data built without one (older callers,
    tests) gets the scheduler's default rather than UTC."""
    return _zone(data.get("timezone"))


def _local(value: datetime, tz: ZoneInfo) -> datetime:
    """A TIMESTAMPTZ on the schedule's clock. A naive value can only come from a
    hand-built dict; it is read as the UTC the database keeps."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def summary_text(data: dict) -> str:
    """The covering body, as plain text.

    Ordered so the numbers that matter survive being read as a notification
    preview on a phone: how many patrols, how many were actually completed, how
    many findings. A summary whose headline is below a table is one nobody
    reads.
    """
    s = data["schedule"]
    tz = _digest_zone(data)
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
        # Said once, above the times, so "22:00" is never left to be read as
        # whichever clock the reader happens to be on.
        lines += ["Findings", "--------", f"All times are {tz.key}.", ""]
        for e in exc:
            when = e["answered_at"]
            stamp = _local(when, tz).strftime("%d %b %H:%M") if when else "-"
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
    tz = _digest_zone(data)
    # The zone is in each time column's heading, as in the patrol report's
    # workbook: Findings is often the only sheet opened, and a bare "22:00"
    # there does not say whose 22:00 it is.
    in_zone = f"({tz.key})"

    ws = wb.active
    ws.title = "Patrols"
    ws.append([f"{s['name']} - {s['site_name']}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"{data['start']:%d %b %Y} to {data['end']:%d %b %Y}"])
    ws.append([])

    cols = ["Patrol", f"Scheduled {in_zone}", "Officer", "Status", "Cameras",
            "Findings"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        ws.cell(row=4, column=c).font = head
    for p in data["patrols"]:
        ws.append([
            p["patrol_number"],
            (_local(p["scheduled_for"], tz).strftime("%Y-%m-%d %H:%M")
             if p["scheduled_for"] else "-"),
            p["officer_name"] or "Unassigned",
            p["status"],
            f"{p['completed_camera_count'] or 0}/{p['camera_count'] or 0}",
            p["exception_count"] or 0,
        ])
    for col, width in zip("ABCDEF", (26, 28, 22, 22, 10, 10)):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Findings")
    cols2 = ["Patrol", "Camera", "Question", "Answer", f"When {in_zone}",
             "Incident"]
    ws2.append(cols2)
    for c in range(1, len(cols2) + 1):
        ws2.cell(row=1, column=c).font = head
    for e in data["exceptions"]:
        ws2.append([
            e["patrol_number"], e["camera_name"], e["question_text"],
            e["answer_text"] or "-",
            (_local(e["answered_at"], tz).strftime("%Y-%m-%d %H:%M")
             if e["answered_at"] else "-"),
            "Yes" if e.get("incident_id") else "No",
        ])
    for col, width in zip("ABCDEF", (26, 22, 46, 18, 26, 10)):
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
