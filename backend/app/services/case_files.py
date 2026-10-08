"""Security cases: what a case may hold, who may do what to it, and its linked records as the reader may see them.

  may()           what somebody may do to a case, given its status and who they are. Pure.
  linked()        a case's links, each as the reader may see it — or that they may not
  record()        one record that is about to be linked, as the linker may see it
  next_number()   the next case number of the organisation
  report_pdf()    a case as a document

A CASE HOLDS ITS OWN RECORD AND REFERS TO THE REST. An incident, an
investigation and an evidence package are linked by reference and read under
their own permissions and the reader's sites. A link to a record the reader may
not read says that it is one, and what kind, and nothing else.

CLOSING TAKES TWO PEOPLE. Whoever asked for a case to be closed may not approve
it. `may()` says so, the route refuses it, and the database refuses it again.

A PERSON WORKS ON A CASE THEY ARE ON. Holding `case:work` lets somebody open a
case and work on one they lead or investigate; working on any case is for
whoever manages cases.

BEING NAMED IN A CASE IS NOT AN ACCUSATION, and nothing here names anybody: a
person or a vehicle is added by a person, with how it is connected.

Reads only, apart from the PDF it builds in memory.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.sites import is_site_allowed

STATUSES = ("OPEN", "AWAITING_APPROVAL", "CLOSED")
STATUS_LABEL = {"OPEN": "Open", "AWAITING_APPROVAL": "Waiting for approval to close", "CLOSED": "Closed"}
CATEGORIES = ("THEFT", "TRESPASS", "DAMAGE", "SAFETY", "ACCESS", "OTHER")
CATEGORY_LABEL = {"THEFT": "Theft or loss", "TRESPASS": "Trespass", "DAMAGE": "Damage", "SAFETY": "Safety",
                  "ACCESS": "Access", "OTHER": "Other"}
PRIORITIES = ("LOW", "NORMAL", "HIGH")
TASK_STATES = ("OPEN", "DONE", "DROPPED")
LINK_KINDS = ("INCIDENT", "INVESTIGATION", "EVIDENCE_PACKAGE")
LINK_LABEL = {"INCIDENT": "Incident", "INVESTIGATION": "Investigation", "EVIDENCE_PACKAGE": "Evidence package"}
#: The permission each kind of linked record is read under. It is the one its own screen asks for.
LINK_NEEDS = {"INCIDENT": "incident:read", "INVESTIGATION": "investigation:read",
              "EVIDENCE_PACKAGE": "evidence:package:read"}
PARTY_KINDS = ("PERSON", "VEHICLE")
CONNECTIONS = ("REPORTED_IT", "WITNESS", "AFFECTED", "NAMED", "OTHER")
CONNECTION_LABEL = {"REPORTED_IT": "Reported it", "WITNESS": "Saw or heard it", "AFFECTED": "Was affected by it",
                    "NAMED": "Is named in it", "OTHER": "Other"}
ENTRY_KINDS = ("OPENED", "NOTE", "LEAD_SET", "INVESTIGATOR_ADDED", "INVESTIGATOR_REMOVED", "CLOSE_REQUESTED",
               "CLOSE_APPROVED", "CLOSE_DECLINED", "REOPENED")
ENTRY_WORDS = {"OPENED": "Opened the case", "NOTE": "Wrote a note", "LEAD_SET": "Set who leads it",
               "INVESTIGATOR_ADDED": "Put an investigator on it", "INVESTIGATOR_REMOVED": "Took an investigator off it",
               "CLOSE_REQUESTED": "Asked for it to be closed", "CLOSE_APPROVED": "Approved its closing",
               "CLOSE_DECLINED": "Declined its closing", "REOPENED": "Reopened it"}
PARTY_NOTE = ("Being named in a case is not an accusation. A person or a vehicle is recorded with how it is connected, "
              "by whoever added it.")
TWO_PEOPLE = "Closing a case takes two people: whoever asked for it to be closed does not approve it."
NOT_YOURS = "You are not on this case. It is worked on by its lead and its investigators, or by whoever manages cases."

_RECORDS = {
    "INCIDENT": """
        SELECT i.id, i.title AS label, i.severity || ', ' || i.status AS detail, i.created_at AS at, c.site_id
          FROM incidents i LEFT JOIN cameras c ON c.id = i.camera_id WHERE i.id = ANY(CAST(:ids AS uuid[]))""",
    "INVESTIGATION": """
        SELECT n.id, n.investigation_number || ' — ' || n.title AS label, lower(n.status) AS detail, n.opened_at AS at,
               n.site_id
          FROM investigations n WHERE n.id = ANY(CAST(:ids AS uuid[]))""",
    "EVIDENCE_PACKAGE": """
        SELECT p.id, p.package_number || ' — ' || p.title AS label, lower(p.status) AS detail, p.created_at AS at,
               p.site_id
          FROM evidence_packages p WHERE p.id = ANY(CAST(:ids AS uuid[]))""",
}


# ─── Pure ────────────────────────────────────────────────────────────────────

def on_case(case: Mapping, investigators: Sequence[Any], me: Any) -> bool:
    """Whether somebody is on a case: its lead, or one of its investigators."""
    me = str(me)
    return str(case.get("lead_user_id")) == me or me in {str(u) for u in investigators}


def may(case: Mapping, me: Any, held: frozenset[str] | set[str], is_on: bool) -> dict:
    """What somebody may do to a case. Working on it is for whoever is on it
    or manages cases, while it is open; approving its closing is for whoever
    manages cases and did not ask for it."""
    manages, status = "case:manage" in held, case["status"]
    works = status == "OPEN" and (manages or ("case:work" in held and is_on))
    waiting = status == "AWAITING_APPROVAL" and manages
    return {"work": works, "assign": status == "OPEN" and manages, "request_close": works,
            "approve_close": waiting and str(case.get("close_requested_by_user_id")) != str(me),
            "decline_close": waiting, "reopen": status == "CLOSED" and manages}


def unfinished(tasks: Sequence[Mapping]) -> int:
    """How many of a case's tasks are neither done nor dropped."""
    return sum(1 for t in tasks if t["state"] == "OPEN")


# ─── Reading ─────────────────────────────────────────────────────────────────

async def next_number(db: AsyncSession) -> str:
    """The organisation's next case number. One at a time."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('case:' || current_setting('app.current_tenant')))"))
    n = (await db.execute(text(
        "SELECT COALESCE(max(CAST(substring(case_number FROM 6) AS integer)), 0) + 1 FROM case_files "
        "WHERE case_number ~ '^CASE-[0-9]+$'"))).scalar()
    return f"CASE-{n:04d}"


async def records(db: AsyncSession, kind: str, ids: Sequence[Any], held, allowed) -> dict[str, dict]:
    """The records of one kind the reader may see, by id."""
    if not ids or LINK_NEEDS[kind] not in held:
        return {}
    rows = await db.execute(text(_RECORDS[kind]), {"ids": [str(i) for i in ids]})
    out = {}
    for r in rows.mappings():
        if r["site_id"] is None and allowed is not None:
            continue
        if r["site_id"] is not None and not is_site_allowed(allowed, r["site_id"]):
            continue
        out[str(r["id"])] = dict(r)
    return out


async def record(db: AsyncSession, kind: str, ref_id: Any, held, allowed) -> dict | None:
    """One record as the caller may see it — or None when it is not there, or not theirs to see."""
    return (await records(db, kind, [ref_id], held, allowed)).get(str(ref_id))


async def linked(db: AsyncSession, links: Sequence[Mapping], held, allowed) -> list[dict]:
    """A case's links as the reader may see each: `SHOWN` with what it is,
    `NOT_PERMITTED` when its permission is not held, `NOT_AVAILABLE` when it is
    at a site the reader is not shown or is no longer there."""
    seen: dict[str, dict[str, dict]] = {}
    for kind in LINK_KINDS:
        seen[kind] = await records(db, kind, [x["ref_id"] for x in links if x["kind"] == kind], held, allowed)
    out = []
    for x in links:
        found = seen[x["kind"]].get(str(x["ref_id"]))
        state = "SHOWN" if found else ("NOT_PERMITTED" if LINK_NEEDS[x["kind"]] not in held else "NOT_AVAILABLE")
        # Which record it is, is said only to somebody who may read that record.
        out.append({**x, "ref_id": x["ref_id"] if found else None,
                    "kind_label": LINK_LABEL[x["kind"]], "state": state, "needs": LINK_NEEDS[x["kind"]],
                    "label": found["label"] if found else None, "detail": found["detail"] if found else None,
                    "at": found["at"] if found else None})
    return out


# ─── The report ──────────────────────────────────────────────────────────────

def _when(moment: datetime | None, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%d %b %Y %H:%M") if moment else "—"


def report_pdf(case: Mapping, zone: str, made_by: str, made_at: datetime) -> bytes:
    """A case as a document: what it is about, who is on it, its tasks, what
    is linked to it, who is named in it, and its history in order. A linked
    record the reader may not read is in it as that, and nothing more."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    tz = ZoneInfo(zone)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)
    head = ParagraphStyle("head", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)

    def p(words: Any, style=body) -> Paragraph:
        return Paragraph(escape(str(words if words not in (None, "") else "—")).replace("\n", "<br/>"), style)

    def table(rows: list[list], widths: list[float]) -> Table:
        t = Table([[p(c) for c in row] for row in rows], colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9ecf2")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5cad6")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    story = [Paragraph(escape(f"{case['case_number']} — {case['title']}"), styles["Title"]),
             p(f"{STATUS_LABEL[case['status']]} · {CATEGORY_LABEL[case['category']]} · priority {case['priority'].lower()}"
               f" · {case['site']['name'] if case.get('site') else 'no one site'}", small),
             p(f"Made by {made_by}, {_when(made_at, tz)} ({zone}). Times are as in {zone}.", small),
             Paragraph("What it is about", head), p(case["summary"])]
    if case.get("outcome"):
        story += [Paragraph("What was found", head), p(case["outcome"])]
    story += [Paragraph("Who is on it", head),
              p(f"Lead: {case['lead_name'] or 'nobody'}. Investigators: "
                f"{', '.join(i['name'] or 'somebody no longer on the system' for i in case['investigators']) or 'none'}.")]
    story += [Paragraph("Tasks", head)]
    story.append(table([["Task", "Given to", "State", "What was done, or why it was dropped"]] + [
        [t["title"], t["assigned_to_name"], t["state"].title(), t["done_note"] or t["dropped_reason"]]
        for t in case["tasks"]], [62, 34, 22, 62]) if case["tasks"] else p("None."))
    story += [Paragraph("Linked records", head)]
    story.append(table([["Kind", "Record", "Linked"]] + [
        [x["kind_label"], x["label"] if x["state"] == "SHOWN" else "A record this reader may not read",
         _when(x["linked_at"], tz)] for x in case["links"]], [38, 104, 38]) if case["links"] else p("None."))
    story += [Paragraph("People and vehicles named in it", head), p(PARTY_NOTE, small)]
    story.append(table([["", "Name or plate", "How it is connected", "Note"]] + [
        [x["kind"].title(), x["label"], CONNECTION_LABEL[x["connection"]], x["note"]] for x in case["parties"]],
        [20, 52, 42, 66]) if case["parties"] else p("None."))
    story += [Paragraph("History", head)]
    story.append(table([["When", "Who", "What", ""]] + [
        [_when(e["occurred_at"], tz), e["actor_name"], ENTRY_WORDS[e["kind"]], e["body"]] for e in case["entries"]],
        [32, 36, 42, 70]))
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=15 * mm, title=f"{case['case_number']} — {case['title']}").build(story + [Spacer(1, 6)])
    return out.getvalue()
