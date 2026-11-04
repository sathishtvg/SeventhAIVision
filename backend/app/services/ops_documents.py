"""What an emailed briefing or report says: its text, and its PDF.

Phase 4 of closing the feature reference's known limits (4 November 2026; services/ops_delivery.py sends them).

A REPORT'S PDF HOLDS WHAT ITS CSV HOLDS, read the same way (services/ops_reports.py `cell`, `headings`): a narrow
report as a table, a wide one - the operations board has forty columns - as one block for each row, each field with
its heading. A PDF stops at PDF_ROWS rows and says so; the CSV beside it holds every row, as downloaded.

A BRIEFING IS GIVEN AS PUBLISHED: its sections less those the reviewer left out, the reviewer's note as theirs, and
who published it, when. Nothing is added to it or left out of it here.

The PDFs use the fonts every PDF of the platform uses. A name in a script those fonts do not hold may not print in
the PDF; the email's text and the CSV hold it as it was written.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Mapping, Sequence
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from app.services import ops_reports

#: The most rows a report's PDF holds.
PDF_ROWS = 500
#: A report with more columns than this is given as a block for each row.
TABLE_COLUMNS = 8
AS_AT = {"DRAFTING": " (as it stood when drafted)", "WEEKS": " (over the last 4 weeks)"}
FONT_NOTE = "A name in a script these fonts do not hold may not print here; the CSV holds it as written."


def day_words(moment: datetime, zone: str) -> str:
    return moment.astimezone(ZoneInfo(zone)).strftime("%a %d %b %Y")


def period_words(frequency: str, start: datetime | None, end: datetime | None, zone: str) -> str:
    """What a report's period is called: "Tue 03 Nov 2026", "the week to Sun 02 Nov 2026", "October 2026"."""
    if start is None or end is None:
        return "as it is now"
    tz = ZoneInfo(zone)
    if frequency == "DAILY":
        return start.astimezone(tz).strftime("%a %d %b %Y")
    if frequency == "WEEKLY":
        return f"the week to {(end.astimezone(tz) - timedelta(days=1)).strftime('%a %d %b %Y')}"
    return start.astimezone(tz).strftime("%B %Y")


# ─── A report ────────────────────────────────────────────────────────────────

def report_text(title: str, scope: str, period: str, rows: int, cut: bool, zone: str) -> str:
    lines = [title, f"{scope}, {period}.", "",
             f"{rows:,} row{'' if rows == 1 else 's'}." + (f" The report was cut at {ops_reports.MAX_ROWS:,} rows."
                                                           if cut else "")]
    if rows > PDF_ROWS:
        lines.append(f"The PDF holds the first {PDF_ROWS:,}; the CSV holds every row.")
    lines += [f"Times are where the organisation is ({zone}).", "",
              "You receive this because your organisation's schedule names you. It holds what you may read on screen."]
    return "\n".join(lines)


def report_pdf(report: ops_reports.Report, rows: Sequence[Mapping], *, scope: str, period: str, zone: str,
               made_at: datetime, cut: bool) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    tz = ZoneInfo(zone)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8.5, leading=11)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5, textColor=colors.grey)
    head = ParagraphStyle("head", parent=body, fontName="Helvetica-Bold")
    wide = len(report.columns) > TABLE_COLUMNS
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4) if not wide else A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=report.title)
    names = ops_reports.headings(report, zone)
    shown = rows[:PDF_ROWS]
    story = [Paragraph(escape(report.title), styles["Title"]),
             Paragraph(escape(f"{scope}, {period}. Made {made_at.astimezone(tz).strftime('%d %b %Y %H:%M')} "
                              f"({zone})."), body),
             Paragraph(escape(report.holds), small), Spacer(1, 6)]
    counted = f"{len(rows):,} row{'' if len(rows) == 1 else 's'}"
    if cut:
        counted += f"; the report was cut at {ops_reports.MAX_ROWS:,}"
    if len(rows) > PDF_ROWS:
        counted += f"; this PDF holds the first {PDF_ROWS:,} and the CSV holds every row"
    story += [Paragraph(escape(counted + "."), body), Spacer(1, 6)]
    if not shown:
        story.append(Paragraph("Nothing to report for this period.", body))
    elif not wide:
        data = [[Paragraph(escape(n), head) for n in names]]
        data += [[Paragraph(escape(ops_reports.cell(r.get(k), tz)), body) for k, _ in report.columns] for r in shown]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
    else:
        for n, r in enumerate(shown, start=1):
            fields = [(name, ops_reports.cell(r.get(k), tz)) for (k, _), name in zip(report.columns, names)]
            filled = [f"<b>{escape(name)}</b>: {escape(value)}" for name, value in fields if value not in ("", None)]
            story.append(KeepTogether([Paragraph(f"<b>{n}.</b> " + " · ".join(filled), body), Spacer(1, 4)]))
    story += [Spacer(1, 8), Paragraph(escape(FONT_NOTE), small)]
    doc.build(story)
    return out.getvalue()


# ─── A briefing ──────────────────────────────────────────────────────────────

def briefing_title(site_name: str | None, briefing_date, revised: bool) -> str:
    when = briefing_date.strftime("%a %d %b %Y")
    return f"Daily briefing: {site_name or 'Every site'}, {when}" + (" (revised)" if revised else "")


def briefing_text(briefing: Mapping, sections: Sequence[Mapping], zone: str) -> str:
    """A published briefing as plain text: readable on a phone."""
    tz = ZoneInfo(zone)
    lines = [briefing_title(briefing["site_name"], briefing["briefing_date"], briefing["revision"] > 1)]
    if briefing["revision"] > 1:
        lines.append(f"Revision {briefing['revision']}: it replaces the one published before for this day.")
    for s in sections:
        lines += ["", s["title"].upper()]
        lines += [f"- {line['text']}{AS_AT.get(line.get('as_at'), '')}" for line in s.get("lines", [])]
        if s.get("note"):
            lines.append(f"  {s['note']}")
    if briefing.get("note"):
        lines += ["", f"Note from {briefing.get('published_by_name') or 'the reviewer'}:", briefing["note"]]
    published = briefing.get("published_at")
    lines += ["", f"Published by {briefing.get('published_by_name') or 'a member of staff'}"
                  + (f", {published.astimezone(tz).strftime('%d %b %Y %H:%M')} ({zone})." if published else ".")]
    return "\n".join(lines)


def briefing_pdf(briefing: Mapping, sections: Sequence[Mapping], zone: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13.5)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11, textColor=colors.grey)
    out = io.BytesIO()
    title = briefing_title(briefing["site_name"], briefing["briefing_date"], briefing["revision"] > 1)
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=title)
    story = [Paragraph(escape(title), styles["Title"])]
    if briefing["revision"] > 1:
        story.append(Paragraph(escape(f"Revision {briefing['revision']}: it replaces the one published before for "
                                      "this day."), small))
    for s in sections:
        story += [Spacer(1, 6), Paragraph(escape(s["title"]), styles["Heading3"])]
        story += [Paragraph("• " + escape(line["text"] + AS_AT.get(line.get("as_at"), "")), body)
                  for line in s.get("lines", [])]
        if s.get("note"):
            story.append(Paragraph(escape(s["note"]), small))
    if briefing.get("note"):
        story += [Spacer(1, 8), Paragraph(escape(f"Note from {briefing.get('published_by_name') or 'the reviewer'}"),
                                          styles["Heading4"]), Paragraph(escape(briefing["note"]), body)]
    tz = ZoneInfo(zone)
    published = briefing.get("published_at")
    story += [Spacer(1, 10), Paragraph(escape(
        f"Published by {briefing.get('published_by_name') or 'a member of staff'}"
        + (f", {published.astimezone(tz).strftime('%d %b %Y %H:%M')} ({zone})." if published else ".")), small)]
    doc.build(story)
    return out.getvalue()
